"""Возможности мобильного API: лента событий, аналитика, итоги, учитель без
лишних запросов, посещаемость и срок токена.

Клиент подменяется заглушкой `MobileClient`: живых запросов к серверам школы
и `ir-tech.ru` здесь нет.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from app.db.repositories import SessionRepository, UserRepository
from app.domain.models import ApiMode
from app.netschool.errors import NetSchoolError, Reason
from app.netschool.service import DiaryService
from app.netschool.session import SessionPool
from tests.test_api_mode import FakeMobile, MOBILE_SETTINGS  # noqa: F401
from tests.test_session_pool import SETTINGS, FakeClient, make_user, reset_clients  # noqa: F401
from tests.test_web import client, app_context  # noqa: F401 — фикстуры


class MobileClient(FakeMobile):
    """Мобильный клиент с заранее заданными ответами сервера."""

    def __init__(self, url, proxy=None, secret=""):
        super().__init__(url, proxy, secret)
        self.events_payload: dict = {"events": []}
        self.events_error: Exception | None = None
        self.calls: list[str] = []
        self.totals_rows: list = []
        self.subjects: list = []
        self.terms_list: list = []
        self.performance: dict = {}
        self.performance_error: Exception | None = None
        self.token = None
        self.refresh_result = None
        self.refresh_error: Exception | None = None
        self.refreshes = 0
        self.sent: list = []

    async def mail_send(self, subject, text, to, **kwargs):
        self.sent.append((subject, text, list(to), kwargs))

    async def refresh(self):
        self.refreshes += 1
        if self.refresh_error:
            raise self.refresh_error
        self.token = self.refresh_result
        return self.refresh_result

    async def totals(self, school_year_id=None, student_id=None):
        self.calls.append("totals")
        return self.totals_rows

    async def subjects_raw(self, school_year_id=None, student_id=None):
        self.calls.append("subjects")
        return self.subjects

    async def terms(self, school_year_id=None, student_id=None):
        self.calls.append("terms")
        return self.terms_list

    async def subject_performance(self, subject_id, term_id, student_id=None):
        self.calls.append(f"performance:{subject_id}:{term_id}")
        if self.performance_error:
            raise self.performance_error
        return self.performance

    async def period_events(self, subject_group_ids=None, *, period_days=14, **kwargs):
        self.calls.append(f"period_events:{period_days}")
        if self.events_error:
            raise self.events_error
        return self.events_payload


@pytest.fixture
def holder():
    return {}


@pytest.fixture
def service(db, holder):
    def mobile_factory(url, proxy, secret):
        client = MobileClient(url, proxy, secret)
        # Ошибка задаётся через holder, чтобы её видел и клиент, созданный
        # заново после сброса сессии.
        client.events_error = holder.get("events_error")
        client.performance_error = holder.get("performance_error")
        holder["mobile"] = client
        return client

    def web_factory(url, proxy=None):
        client = FakeClient(url, proxy)
        holder["web"] = client
        return client

    pool = SessionPool(
        MOBILE_SETTINGS, SessionRepository(db), client_factory=web_factory, mobile_factory=mobile_factory
    )
    return DiaryService(pool, UserRepository(db))


def mobile_user(**kwargs):
    return make_user(api_mode=ApiMode.MOBILE, **kwargs)


RESULT_EVENT = {
    "type": "ResultInfo",
    "result": {
        "subjectId": 5, "subjectName": "Алгебра", "date": "2026-09-28T00:00:00",
        "assignmentId": 501, "result": 5, "assignmentTypeAbbr": "К",
        "resultDate": "2026-09-29T10:15:00+03:00",
    },
}
HOMEWORK_EVENT = {
    "type": "HomeworkInfo",
    "homework": {
        "assignmentId": 502, "subjectId": 5, "subjectName": "Алгебра",
        "date": "2026-09-30T00:00:00", "assignmentDate": "2026-09-29T09:34:00",
    },
}


class TestRecentEvents:
    async def test_mobile_mode_reads_period_events(self, service, holder, users):
        user = await users.save(mobile_user())
        # Клиент создаётся при первом обращении; заранее его не достать.
        first = await service.fetch_recent_events(user)
        holder["mobile"].events_payload = {"events": [RESULT_EVENT, HOMEWORK_EVENT]}

        events = await service.fetch_recent_events(user)

        assert first is not None and first.keys == frozenset()
        assert (events.marks, events.homework) == (1, 1)
        assert len(events.keys) == 2
        # Запас по дням: не сутки, как «Сегодня» в приложении, а две недели.
        assert holder["mobile"].calls == ["period_events:14", "period_events:14"]

    async def test_corrected_mark_is_a_new_event(self, service, holder, users):
        user = await users.save(mobile_user())
        await service.fetch_recent_events(user)
        holder["mobile"].events_payload = {"events": [RESULT_EVENT]}
        before = await service.fetch_recent_events(user)
        corrected = {**RESULT_EVENT, "result": {**RESULT_EVENT["result"], "result": 4}}
        holder["mobile"].events_payload = {"events": [corrected]}
        after = await service.fetch_recent_events(user)
        assert not before.keys & after.keys

    async def test_web_mode_has_no_events(self, service, holder):
        assert await service.fetch_recent_events(make_user()) is None
        assert "mobile" not in holder

    async def test_gosuslugi_user_in_mobile_mode_reads_the_feed(self, service, holder, users, sessions):
        from app.domain.models import Credentials, LoginType

        user = await users.save(mobile_user(credentials=Credentials(login_type=LoginType.ESIA)))
        # Пароля нет — клиент поднимается из сохранённого мобильного токена.
        await sessions.save(1, '{"kind": "mobile"}')
        await service.fetch_recent_events(user)
        holder["mobile"].events_payload = {"events": [RESULT_EVENT]}
        assert (await service.fetch_recent_events(user)).marks == 1

    async def test_feed_failure_falls_back(self, service, holder, users):
        user = await users.save(mobile_user())
        await service.fetch_recent_events(user)
        holder["events_error"] = holder["mobile"].events_error = NetSchoolError(Reason.UNKNOWN, "сбой ленты")
        assert await service.fetch_recent_events(user) is None

    async def test_relogin_error_is_not_swallowed(self, service, holder, users):
        user = await users.save(mobile_user())
        await service.fetch_recent_events(user)
        holder["events_error"] = holder["mobile"].events_error = NetSchoolError(Reason.AUTH, "токен отозван")
        with pytest.raises(NetSchoolError):
            await service.fetch_recent_events(user)

    async def test_garbage_payload_gives_empty_events(self, service, holder, users):
        user = await users.save(mobile_user())
        await service.fetch_recent_events(user)
        holder["mobile"].events_payload = {"events": [None, {"type": "Other"}, {"type": "ResultInfo"}]}
        events = await service.fetch_recent_events(user)
        assert events.keys == frozenset() and events.marks == 0


class TestTeacherInLesson:
    """Мобильный режим: учитель, кабинет и тема приходят вместе с уроком."""

    @staticmethod
    def _mobile_lesson():
        from types import SimpleNamespace

        return SimpleNamespace(
            day=dt.date(2026, 9, 28),
            lessons=[
                SimpleNamespace(
                    subject="Алгебра", number=2, start=dt.time(9, 20), end=dt.time(10, 0),
                    room="14", teacher="Петрова А. И.", topic="Дроби", absence="Б",
                    assignments=[SimpleNamespace(id=42, kind="ДЗ", content="Стр. 3", mark=None, weight=1,
                                                 comment="", deadline=None, attachments=[])],
                )
            ],
        )

    def test_mapping_keeps_teacher_room_topic_and_absence(self):
        from app.netschool.mapping import diary_days

        (day,) = diary_days([self._mobile_lesson()], today=dt.date(2026, 9, 28))
        lesson = day.lessons[0]
        assert (lesson.teacher, lesson.room, lesson.topic, lesson.absence) == (
            "Петрова А. И.", "14", "Дроби", "Б",
        )

    async def test_mobile_mode_does_not_ask_for_teachers(self, client, app_context, users):
        from tests.test_web import BASE

        user = await users.save(mobile_user())
        token = await app_context.miniapp.issue_token(user.telegram_id)
        asked = []

        async def fetch_teachers(user, wanted):
            asked.append(wanted)
            return {subject: "Из карточки" for subject in wanted}

        async def fetch_diary(user, **kwargs):
            from app.netschool.mapping import diary_days

            return diary_days([self._mobile_lesson()], today=dt.date(2026, 9, 28))

        app_context.diary.fetch_teachers = fetch_teachers
        app_context.diary.fetch_diary = fetch_diary
        response = await client.get(f"{BASE}/api/diary", params={"token": token})

        lesson = (await response.json())["data"]["days"][0]["lessons"][0]
        assert asked == []
        assert (lesson["teacher"], lesson["room"], lesson["topic"], lesson["absence"]) == (
            "Петрова А. И.", "14", "Дроби", "Б",
        )
        # Кэш учителей тоже не заводим: он нужен только веб-режиму.
        assert await app_context.cache.get(user.telegram_id, "teachers") is None

    async def test_web_mode_still_asks_for_teachers(self, client, app_context, users):
        from tests.test_web import BASE

        user = await users.save(make_user())
        token = await app_context.miniapp.issue_token(user.telegram_id)
        asked = []

        async def fetch_teachers(user, wanted):
            asked.append(wanted)
            return {subject: "Из карточки" for subject in wanted}

        async def fetch_diary(user, **kwargs):
            from app.domain.records import DiaryDay, Lesson

            return [DiaryDay(day=dt.date(2026, 9, 28),
                             lessons=[Lesson(number=1, subject="Алгебра", assignment_ids=(42,))])]

        app_context.diary.fetch_teachers = fetch_teachers
        app_context.diary.fetch_diary = fetch_diary
        response = await client.get(f"{BASE}/api/diary", params={"token": token})

        assert asked == [{"Алгебра": 42}]
        assert (await response.json())["data"]["days"][0]["lessons"][0]["teacher"] == "Из карточки"


SUBJECTS = [
    {"id": 10, "name": "Алгебра", "order": 1},
    {"id": 11, "name": "История", "order": 2},
    {"id": 12, "name": "Физкультура", "order": 3},
]
TERMS = [
    {"id": 71, "name": "1 четверть", "current": False},
    {"id": 72, "name": "2 четверть", "current": True},
    {"id": 73, "name": "1 полугодие", "current": False},
]
TOTALS = [
    {
        "subjectId": 10,
        "termTotals": [
            {"term": {"id": 71, "name": "1 четверть"}, "markCode": "4", "avgMark": 4.33},
            {"term": {"id": 72, "name": "2 четверть"}, "markCode": None, "avgMark": 4.5},
            {"term": {"id": 73, "name": "1 полугодие"}, "markCode": "4", "avgMark": 4.4},
        ],
        "yearTotals": [
            {"period": {"id": 1, "periodName": "Годовая", "periodType": "Year"}, "markCode": "4"},
            {"period": {"id": 2, "periodName": "Итоговая", "periodType": "Total"}, "markCode": None},
        ],
    },
    {
        "subjectId": 11,
        "termTotals": [{"term": {"id": 71, "name": "1 четверть"}, "markCode": "зач", "avgMark": None}],
        "yearTotals": [],
    },
    # Ни одной оценки — строку в таблице занимать нечем.
    {"subjectId": 12, "termTotals": [{"term": {"id": 71, "name": "1 четверть"}, "markCode": None, "avgMark": None}],
     "yearTotals": []},
    # Предмета нет в списке subjects — назвать его нечем.
    {"subjectId": 99, "termTotals": [{"term": {"id": 71, "name": "1 четверть"}, "markCode": "5"}], "yearTotals": []},
]
PERFORMANCE = {
    "averageMark": 4.4, "classAverageMark": 3.87, "maxMark": 5,
    "classmeetingsStats": {"passed": 20, "scheduled": 34},
    "markStats": [{"mark": 5, "count": 3, "fraction": 0.6}, {"mark": 4, "count": 2, "fraction": 0.4}],
    "results": [], "attendance": [],
}


class TestTotals:
    def test_terms_map_to_table_columns(self):
        from app.netschool.mapping import quarter_key_of_term

        assert [quarter_key_of_term(n) for n in ("1 четверть", "4 Четверть", "1 полугодие", "", None, "5 четверть")] == [
            "q1", "q4", None, None, None, None,
        ]

    def test_table_is_built_from_totals_response(self):
        from app.netschool.mapping import totals_from_mobile

        table = totals_from_mobile(TOTALS, {s["id"]: s["name"] for s in SUBJECTS})

        assert table["Алгебра"] == {
            # Четвертные с оценкой и с одним лишь средним; полугодие в таблицу не идёт.
            "terms": {"q1": {"mark": "4", "average": 4.33}, "q2": {"mark": "", "average": 4.5}},
            "year": "4",
            "final": "",
        }
        assert table["История"]["terms"] == {"q1": {"mark": "зач", "average": None}}
        assert set(table) == {"Алгебра", "История"}

    def test_status_codes_are_not_marks(self):
        from app.netschool.mapping import totals_from_mobile

        # Так /totals отвечает в начале года: статус вместо оценки. В таблице
        # «Итогов» стояли «PointSystem» и «NotExamined».
        rows = [{"subjectId": 10, "termTotals": [
            {"term": {"name": "1 четверть"}, "markCode": "PointSystem", "avgMark": 5.0},
            {"term": {"name": "2 четверть"}, "markCode": "NotExamined", "avgMark": None},
        ], "yearTotals": [{"period": {"periodType": "Year"}, "markCode": "NotExamined"}]}]
        assert totals_from_mobile(rows, {10: "Алгебра"}) == {
            "Алгебра": {"terms": {"q1": {"mark": "", "average": 5.0}}, "year": "", "final": ""}
        }

    def test_year_and_final_are_told_apart(self):
        from app.netschool.mapping import totals_from_mobile

        rows = [{"subjectId": 10, "termTotals": [], "yearTotals": [
            {"period": {"periodType": "Year"}, "markCode": "4"},
            {"period": {"periodType": "Total"}, "markCode": 5},
        ]}]
        assert totals_from_mobile(rows, {10: "Алгебра"}) == {
            "Алгебра": {"terms": {}, "year": "4", "final": "5"}
        }

    async def test_service_reads_totals_in_one_go(self, service, holder, users):
        user = await users.save(mobile_user())
        await service.fetch_totals(user)  # создаёт клиент
        holder["mobile"].subjects = SUBJECTS
        holder["mobile"].totals_rows = TOTALS
        holder["mobile"].calls.clear()

        table = await service.fetch_totals(user)

        assert table["Алгебра"]["year"] == "4"
        assert holder["mobile"].calls == ["totals", "subjects"]

    async def test_web_mode_has_no_totals(self, service, holder):
        assert await service.fetch_totals(make_user()) is None
        assert "mobile" not in holder

    async def test_marks_screen_carries_totals(self, client, app_context, users):
        from tests.test_web import BASE
        from tests.test_watcher import record

        user = await users.save(mobile_user())
        token = await app_context.miniapp.issue_token(user.telegram_id)
        app_context.diary.marks = [record(0, "5", subject="Алгебра")]
        app_context.diary.totals = {
            "Алгебра": {"terms": {"q1": {"mark": "4", "average": 4.33}}, "year": "4", "final": ""},
            "История": {"terms": {"q1": {"mark": "5", "average": None}}, "year": "", "final": ""},
        }

        data = (await (await client.get(f"{BASE}/api/marks", params={"token": token})).json())["data"]

        by_name = {s["subject"]: s for s in data["subjects"]}
        assert by_name["Алгебра"]["totals"]["year"] == "4"
        # Предмет, по которому оценок в дневнике нет, но итоги выставлены, всё равно в таблице.
        assert by_name["История"]["totals"]["terms"]["q1"]["mark"] == "5"
        assert (data["mobile"], data["has_totals"]) == (True, True)

    async def test_marks_screen_without_totals_is_unchanged(self, client, app_context, users):
        from tests.test_web import BASE
        from tests.test_watcher import record

        user = await users.save(make_user())
        token = await app_context.miniapp.issue_token(user.telegram_id)
        app_context.diary.marks = [record(0, "5", subject="Алгебра")]

        data = (await (await client.get(f"{BASE}/api/marks", params={"token": token})).json())["data"]

        assert [s["subject"] for s in data["subjects"]] == ["Алгебра"]
        assert data["subjects"][0]["totals"] is None
        assert (data["mobile"], data["has_totals"]) == (False, False)


class TestSubjectStats:
    async def test_service_resolves_subject_and_term(self, service, holder, users):
        user = await users.save(mobile_user())
        await service.fetch_subject_stats(user, "Алгебра", "q2")
        holder["mobile"].subjects, holder["mobile"].terms_list = SUBJECTS, TERMS
        holder["mobile"].performance = PERFORMANCE

        stats = await service.fetch_subject_stats(user, " алгебра ", "q2")

        assert holder["mobile"].calls[-1] == "performance:10:72"
        assert (stats["average"], stats["class_average"]) == (4.4, 3.87)
        assert stats["lessons"] == {"passed": 20, "scheduled": 34}
        assert stats["mark_stats"][0] == {"mark": "5", "count": 3, "fraction": 0.6}

    async def test_unknown_subject_or_quarter_gives_none(self, service, holder, users):
        user = await users.save(mobile_user())
        await service.fetch_subject_stats(user, "Алгебра", "q2")
        holder["mobile"].subjects, holder["mobile"].terms_list = SUBJECTS, TERMS
        holder["mobile"].performance = PERFORMANCE

        assert await service.fetch_subject_stats(user, "Черчение", "q2") is None
        assert await service.fetch_subject_stats(user, "Алгебра", "q4") is None

    async def test_server_error_gives_none(self, service, holder, users):
        user = await users.save(mobile_user())
        await service.fetch_subject_stats(user, "Алгебра", "q2")
        holder["mobile"].subjects, holder["mobile"].terms_list = SUBJECTS, TERMS
        holder["mobile"].performance_error = holder["performance_error"] = NetSchoolError(
            Reason.UNKNOWN, "сбой"
        )
        assert await service.fetch_subject_stats(user, "Алгебра", "q2") is None

    def test_empty_class_average_is_kept_as_none(self):
        from app.netschool.mapping import subject_stats_from

        stats = subject_stats_from({"averageMark": 5, "classAverageMark": None})
        assert stats["class_average"] is None and stats["mark_stats"] == []
        assert subject_stats_from(None) == {}

    async def test_endpoint_returns_class_average(self, client, app_context, users):
        from tests.test_web import BASE

        user = await users.save(mobile_user())
        token = await app_context.miniapp.issue_token(user.telegram_id)
        app_context.diary.stats = {"average": 4.4, "class_average": 3.87, "mark_stats": [], "lessons": {}}

        response = await client.get(
            f"{BASE}/api/subject", params={"token": token, "subject": "Алгебра", "quarter": "q2"}
        )

        data = (await response.json())["data"]
        assert data["available"] is True and data["class_average"] == 3.87
        assert app_context.diary.stats_calls == [("Алгебра", "q2")]

    async def test_endpoint_survives_missing_data_and_retries_later(self, client, app_context, users):
        from tests.test_web import BASE

        user = await users.save(mobile_user())
        token = await app_context.miniapp.issue_token(user.telegram_id)
        params = {"token": token, "subject": "Алгебра", "quarter": "q2"}

        response = await client.get(f"{BASE}/api/subject", params=params)
        assert response.status == 200
        assert (await response.json())["data"] == {"available": False}

        # Отсутствие данных не запоминается: при следующем открытии спросим снова.
        app_context.diary.stats = {"class_average": 3.5}
        data = (await (await client.get(f"{BASE}/api/subject", params=params)).json())["data"]
        assert data["available"] is True and data["class_average"] == 3.5
        assert len(app_context.diary.stats_calls) == 2

    async def test_web_mode_never_asks_the_school(self, client, app_context, users):
        from tests.test_web import BASE

        user = await users.save(make_user())
        token = await app_context.miniapp.issue_token(user.telegram_id)

        response = await client.get(
            f"{BASE}/api/subject", params={"token": token, "subject": "Алгебра", "quarter": "q1"}
        )

        assert (await response.json())["data"] == {"available": False}
        assert app_context.diary.stats_calls == []

    async def test_endpoint_rejects_bad_request(self, client, users, app_context):
        from tests.test_web import BASE

        user = await users.save(mobile_user())
        token = await app_context.miniapp.issue_token(user.telegram_id)
        for params in ({"subject": "Алгебра"}, {"quarter": "q1"}, {"subject": "Алгебра", "quarter": "q9"}):
            response = await client.get(f"{BASE}/api/subject", params={"token": token, **params})
            assert response.status == 400


class TestAbsences:
    """Отметки посещаемости: сводка, уведомление один раз, только мобильный режим."""

    TODAY = dt.date(2026, 9, 29)

    @pytest.fixture(autouse=True)
    def _today(self, monkeypatch):
        # Уведомляем только о свежих уроках, поэтому «сегодня» фиксируем.
        monkeypatch.setattr("app.notifications.watcher.msk_today", lambda: self.TODAY)

    @staticmethod
    def _user(**kwargs):
        from app.domain.models import NotificationPrefs

        kwargs.setdefault("notifications", NotificationPrefs(absences=True))
        return mobile_user(**kwargs)

    @staticmethod
    def _watcher(users, marks, diary, notifier):
        from app.notifications.watcher import UserWatcher

        return UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)

    def test_absences_are_read_from_lessons(self):
        from app.domain.records import DiaryDay, Lesson
        from app.netschool.mapping import absences_from_days

        days = [DiaryDay(day=dt.date(2026, 9, 28), lessons=[
            Lesson(number=1, subject="Алгебра", absence="Б"),
            Lesson(number=2, subject="История", absence=""),
            Lesson(number=3, subject="Физкультура", absence=" ОП "),
        ])]

        records = absences_from_days(days)

        assert [(r.subject, r.lesson_number, r.code) for r in records] == [
            ("Алгебра", 1, "Б"), ("Физкультура", 3, "ОП"),
        ]
        assert [r.label for r in records] == ["Пропуск по болезни", "Опоздал"]
        # Ключ различает уроки и отметки: иначе два пропуска за день слились бы.
        assert len({r.identity for r in records}) == 2

    def test_unknown_code_is_shown_as_is(self):
        from app.domain.records import absence_label

        assert absence_label("XYZ") == "XYZ"

    def test_digest_text(self):
        from app.domain import formatting
        from app.domain.records import AbsenceRecord

        text = formatting.absences_digest([
            AbsenceRecord("Алгебра", dt.date(2026, 9, 28), 2, "Б"),
            AbsenceRecord("История", dt.date(2026, 9, 27), None, "Н"),
        ])
        assert "Новые отметки посещаемости</b> (2)" in text
        assert "27.09 — История: Пропуск" in text
        assert "28.09 — Алгебра, 2 урок: Пропуск по болезни" in text

    async def test_new_absence_is_notified_exactly_once(self, users, marks):
        from tests.test_watcher import FakeDiary, FakeNotifier, record

        diary, notifier = FakeDiary(), FakeNotifier()
        await users.save(self._user())
        watcher = self._watcher(users, marks, diary, notifier)
        diary.marks = [record(0)]
        diary.events = None  # ленты событий нет — дневник читается на каждом проходе

        await watcher.run_once()  # первая проверка молчит и запоминает
        assert notifier.absences == []

        diary.absent = [(dt.date(2026, 9, 28), "Алгебра", 2, "Б")]
        await watcher.run_once()
        assert [(a.subject, a.code) for a in notifier.absences] == [("Алгебра", "Б")]

        await watcher.run_once()
        await watcher.run_once()
        assert len(notifier.absences) == 1

        diary.absent.append((dt.date(2026, 9, 29), "История", 3, "Н"))
        await watcher.run_once()
        assert [a.subject for a in notifier.absences] == ["Алгебра", "История"]

    async def test_switch_off_records_but_stays_silent(self, users, marks):
        from app.domain.models import NotificationPrefs
        from tests.test_watcher import FakeDiary, FakeNotifier, record

        diary, notifier = FakeDiary(), FakeNotifier()
        user = await users.save(self._user(notifications=NotificationPrefs(absences=False)))
        watcher = self._watcher(users, marks, diary, notifier)
        diary.marks = [record(0)]
        await watcher.run_once()

        diary.absent = [(dt.date(2026, 9, 28), "Алгебра", 2, "Б")]
        await watcher.run_once()
        assert notifier.absences == []

        # Включили позже — отметка уже виденная, старое за новое не выдаём.
        await users.save(replace(user, notifications=NotificationPrefs(absences=True)))
        await watcher.run_once()
        assert notifier.absences == []

    async def test_old_absences_are_not_announced(self, users, marks):
        from tests.test_watcher import FakeDiary, FakeNotifier, record

        diary, notifier = FakeDiary(), FakeNotifier()
        await users.save(self._user())
        watcher = self._watcher(users, marks, diary, notifier)
        diary.marks = [record(0)]
        await watcher.run_once()

        diary.absent = [(dt.date(2026, 8, 30), "Алгебра", 2, "Б")]
        await watcher.run_once()
        assert notifier.absences == []

    async def test_web_mode_ignores_absences(self, users, marks):
        from app.domain.models import NotificationPrefs
        from tests.test_watcher import FakeDiary, FakeNotifier, record

        diary, notifier = FakeDiary(), FakeNotifier()
        await users.save(make_user(notifications=NotificationPrefs(absences=True)))
        watcher = self._watcher(users, marks, diary, notifier)
        diary.marks = [record(0)]
        await watcher.run_once()
        diary.absent = [(dt.date(2026, 9, 28), "Алгебра", 2, "Б")]
        await watcher.run_once()

        assert notifier.absences == []
        assert await marks.load_absences(1) == set()

    async def test_flag_is_saved_and_off_by_default(self, users):
        from app.domain.models import NotificationPrefs

        assert not (await users.save(make_user())).notifications.absences
        saved = await users.save(make_user(notifications=NotificationPrefs(absences=True)))
        assert (await users.get(saved.telegram_id)).notifications.absences

    async def test_state_is_forgotten_with_the_rest(self, users, marks):
        await users.save(make_user())
        await marks.replace_absences(1, {"a", "b"})
        await marks.replace_absences(1, {"b", "c"})
        assert await marks.load_absences(1) == {"a", "b", "c"}
        await marks.forget_all(1)
        assert await marks.load_absences(1) == set()

    async def test_migration_adds_flag_to_existing_users(self, tmp_path):
        import aiosqlite

        from app.db.migrations import MIGRATIONS, migrate

        async with aiosqlite.connect(tmp_path / "old.sqlite3") as db:
            for version, script in enumerate(MIGRATIONS[:4]):
                await db.executescript(script)
            await db.execute("PRAGMA user_version = 4")
            await db.execute("INSERT INTO users (telegram_id) VALUES (7)")
            await db.commit()

            assert await migrate(db) == len(MIGRATIONS)

            cursor = await db.execute("SELECT notify_absences FROM users WHERE telegram_id = 7")
            assert (await cursor.fetchone())[0] == 0
            await db.execute("INSERT INTO tracked_absences (telegram_id, identity) VALUES (7, 'x')")

    def test_settings_toggle_only_for_mobile(self):
        from app.bot import keyboards

        def buttons(user):
            return [b.callback_data for row in keyboards.settings_menu(user).inline_keyboard for b in row]

        assert "toggle:absences" in buttons(mobile_user())
        assert "toggle:absences" not in buttons(make_user())

    async def test_marks_screen_summarizes_absences_by_quarter(self, client, app_context, users):
        from app.domain.records import DiaryDay, Lesson
        from tests.test_web import BASE

        user = await users.save(mobile_user())
        token = await app_context.miniapp.issue_token(user.telegram_id)

        async def fetch_period(user, start, end, **kwargs):
            lessons = lambda *codes: [Lesson(number=i + 1, subject="Алгебра", absence=c) for i, c in enumerate(codes)]
            return [
                DiaryDay(day=dt.date(2026, 9, 7), lessons=lessons("Б", "", "Б")),
                DiaryDay(day=dt.date(2026, 11, 9), lessons=lessons("Н")),
            ]

        app_context.diary.fetch_period = fetch_period
        data = (await (await client.get(f"{BASE}/api/marks", params={"token": token})).json())["data"]

        algebra = data["subjects"][0]
        assert algebra["absences"] == {"q1": {"Б": 2}, "q2": {"Н": 1}}

    async def test_profile_tells_the_screen_which_api_is_used(self, client, app_context, users):
        from tests.test_web import BASE

        mobile = await users.save(mobile_user())
        token = await app_context.miniapp.issue_token(mobile.telegram_id)
        data = (await (await client.get(f"{BASE}/api/profile", params={"token": token})).json())["data"]
        assert data["api_mode"] == "mobile" and data["notifications"]["absences"] is False

        await users.save(make_user())
        data = (await (await client.get(f"{BASE}/api/profile", params={"token": token})).json())["data"]
        assert data["api_mode"] == "web"

    async def test_absences_toggle_is_saved_from_miniapp(self, client, app_context, users):
        from tests.test_web import BASE

        user = await users.save(mobile_user())
        token = await app_context.miniapp.issue_token(user.telegram_id)
        response = await client.post(
            f"{BASE}/api/settings/notifications", params={"token": token},
            json={"key": "absences", "value": True},
        )
        assert (await response.json())["data"]["absences"] is True
        assert (await users.get(user.telegram_id)).notifications.absences


def token_expiring_in(days: float):
    from netschoolpy.mobile import MobileToken

    return MobileToken(
        access_token="T", refresh_token="R",
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days),
    )


class TestTokenExpiry:
    async def test_healthy_token_is_left_alone(self, service, holder, users):
        from app.netschool.service import TokenState

        user = await users.save(mobile_user())
        await service.check_mobile_token(user)
        holder["mobile"].token = token_expiring_in(40)

        check = await service.check_mobile_token(user)

        assert check.state is TokenState.OK
        assert holder["mobile"].refreshes == 0

    async def test_expiring_token_is_renewed(self, service, holder, users):
        from app.netschool.service import TokenState

        user = await users.save(mobile_user())
        await service.check_mobile_token(user)
        holder["mobile"].token = token_expiring_in(2)
        holder["mobile"].refresh_result = token_expiring_in(60)

        check = await service.check_mobile_token(user)

        assert check.state is TokenState.RENEWED
        assert holder["mobile"].refreshes == 1
        assert check.expires_at > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=50)
        # Дальше срок в порядке — повторного продления нет.
        assert (await service.check_mobile_token(user)).state is TokenState.OK
        assert holder["mobile"].refreshes == 1

    async def test_failed_renewal_means_expiring(self, service, holder, users):
        from app.netschool.service import TokenState

        user = await users.save(mobile_user())
        await service.check_mobile_token(user)
        expiring = token_expiring_in(2)
        holder["mobile"].token = expiring
        holder["mobile"].refresh_error = RuntimeError("identity.ir-tech.ru отказал")

        check = await service.check_mobile_token(user)

        assert (check.state, check.expires_at) == (TokenState.EXPIRING, expiring.expires_at)

    async def test_renewal_that_does_not_extend_is_not_renewal(self, service, holder, users):
        from app.netschool.service import TokenState

        user = await users.save(mobile_user())
        await service.check_mobile_token(user)
        holder["mobile"].token = token_expiring_in(2)
        holder["mobile"].refresh_result = token_expiring_in(0)  # expires_in не пришёл

        assert (await service.check_mobile_token(user)).state is TokenState.EXPIRING

    async def test_web_mode_and_tokenless_client_have_nothing_to_check(self, service, holder, users):
        assert await service.check_mobile_token(make_user()) is None
        user = await users.save(mobile_user())
        assert await service.check_mobile_token(user) is None  # у заглушки токена нет

    def test_warning_text(self):
        from app.domain import formatting

        text = formatting.token_expiring(dt.datetime(2026, 10, 12, 21, 30, tzinfo=dt.timezone.utc))
        assert "до 13.10.2026" in text  # по Москве уже следующий день
        assert "/login" in text and "/changeapi" in text


class TestTokenWarning:
    @pytest.fixture
    def clock(self):
        class Clock:
            now = 1000.0

            def __call__(self):
                return self.now

        return Clock()

    @pytest.fixture
    def make_watcher(self, users, marks, clock):
        from tests.test_watcher import FakeDiary, FakeNotifier
        from app.notifications.watcher import UserWatcher

        diary, notifier = FakeDiary(), FakeNotifier()

        def build():
            return UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier, clock=clock)

        return build, diary, notifier

    @staticmethod
    def expiring():
        from app.netschool.service import TokenCheck, TokenState

        return TokenCheck(TokenState.EXPIRING, dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2))

    async def test_warning_is_sent_once(self, users, make_watcher, clock):
        from app.notifications.watcher import TOKEN_CHECK_INTERVAL

        build, diary, notifier = make_watcher
        await users.save(mobile_user())
        diary.token_check = self.expiring()
        watcher = build()

        await watcher.run_once()
        assert len(notifier.token_warnings) == 1

        for _ in range(3):
            clock.now += TOKEN_CHECK_INTERVAL
            await watcher.run_once()
        assert diary.token_checks == 4 and len(notifier.token_warnings) == 1

    async def test_warning_survives_restart(self, users, make_watcher, clock):
        build, diary, notifier = make_watcher
        await users.save(mobile_user())
        diary.token_check = self.expiring()

        await build().run_once()
        await build().run_once()  # новый watcher — как после перезапуска бота

        assert len(notifier.token_warnings) == 1

    async def test_token_is_checked_a_few_times_a_day_not_every_pass(self, users, make_watcher, clock):
        build, diary, notifier = make_watcher
        await users.save(mobile_user())
        diary.token_check = self.expiring()
        watcher = build()

        for _ in range(5):
            clock.now += 300
            await watcher.run_once()

        assert diary.token_checks == 1

    async def test_renewed_token_sends_no_warning(self, users, make_watcher):
        from app.netschool.service import TokenCheck, TokenState

        build, diary, notifier = make_watcher
        await users.save(mobile_user())
        diary.token_check = TokenCheck(TokenState.RENEWED, dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=60))

        await build().run_once()

        assert notifier.token_warnings == []

    async def test_new_token_rearms_the_warning(self, users, marks, make_watcher, clock):
        from app.netschool.service import TokenCheck, TokenState
        from app.notifications.watcher import TOKEN_CHECK_INTERVAL

        build, diary, notifier = make_watcher
        await users.save(mobile_user())
        watcher = build()
        diary.token_check = self.expiring()
        await watcher.run_once()
        assert await marks.token_warned_at(1) is not None

        # Вошли заново: токен свежий, отметка снимается…
        clock.now += TOKEN_CHECK_INTERVAL
        diary.token_check = TokenCheck(TokenState.OK, dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=60))
        await watcher.run_once()
        assert await marks.token_warned_at(1) is None

        # …и через 57 дней предупреждение снова придёт.
        clock.now += TOKEN_CHECK_INTERVAL
        diary.token_check = self.expiring()
        await watcher.run_once()
        assert len(notifier.token_warnings) == 2

    async def test_quiet_hours_postpone_the_warning(self, users, make_watcher, clock, monkeypatch):
        from app.domain.models import QuietHours
        from app.notifications.watcher import TOKEN_CHECK_INTERVAL

        build, diary, notifier = make_watcher
        await users.save(mobile_user(quiet_hours=QuietHours(dt.time(0, 0), dt.time(23, 59))))
        diary.token_check = self.expiring()
        watcher = build()

        monkeypatch.setattr(watcher, "_is_quiet", lambda user: True)
        await watcher.run_once()
        assert notifier.token_warnings == []

        monkeypatch.setattr(watcher, "_is_quiet", lambda user: False)
        clock.now += TOKEN_CHECK_INTERVAL
        await watcher.run_once()
        assert len(notifier.token_warnings) == 1

    async def test_check_failure_does_not_break_the_pass(self, users, make_watcher):
        from tests.test_watcher import record

        build, diary, notifier = make_watcher
        await users.save(mobile_user())
        diary.marks = [record(0)]
        diary.token_error = RuntimeError("что-то пошло не так")

        await build().run_once()

        assert notifier.errors == [] and notifier.token_warnings == []

    async def test_web_mode_is_not_checked(self, users, make_watcher):
        build, diary, notifier = make_watcher
        await users.save(make_user())
        diary.token_check = self.expiring()

        await build().run_once()

        assert diary.token_checks == 0 and notifier.token_warnings == []

    async def test_migration_adds_warning_column(self, tmp_path):
        import aiosqlite

        from app.db.migrations import MIGRATIONS, migrate

        async with aiosqlite.connect(tmp_path / "old.sqlite3") as db:
            for script in MIGRATIONS[:5]:
                await db.executescript(script)
            await db.execute("PRAGMA user_version = 5")
            await db.execute("INSERT INTO users (telegram_id) VALUES (7)")
            await db.commit()
            await migrate(db)
            cursor = await db.execute("SELECT token_warned_at FROM users WHERE telegram_id = 7")
            assert (await cursor.fetchone())[0] is None


class TestMailAttachments:
    @staticmethod
    def _form(files, to=("aWQ=",), subject="Тема", text="Текст"):
        """Тело multipart, как его шлют браузеры: имя файла — сырой UTF-8.

        Клиент aiohttp сам URL-кодирует имена файлов, поэтому для проверки
        сервера тело собирается вручную. Возвращает (тело, заголовки).
        """
        boundary = "----testboundary"
        chunks: list[bytes] = []

        def field(name, value):
            chunks.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            )

        for item in to:
            field("to", item)
        field("subject", subject)
        field("text", text)
        for name, data in files:
            chunks.append(
                (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                 "Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n"
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks), {"Content-Type": f"multipart/form-data; boundary={boundary}"}

    @pytest.fixture
    async def sent(self, app_context, users):
        user = await users.save(mobile_user())
        calls = []

        async def send_mail(user, to, subject, text, attachments=None):
            calls.append((to, subject, text, attachments))

        app_context.diary.send_mail = send_mail
        return calls, await app_context.miniapp.issue_token(user.telegram_id)

    async def test_files_reach_the_service(self, client, sent):
        from tests.test_web import BASE

        calls, token = sent
        body, headers = self._form([("../x/evil.txt", b"abc"), ("Фото.png", b"\x89PNG")], to=("a", "b"))
        response = await client.post(f"{BASE}/api/mail/send", params={"token": token}, data=body, headers=headers)

        assert response.status == 200
        # Путь из имени отброшен, содержимое и порядок сохранены.
        assert calls == [(["a", "b"], "Тема", "Текст", [("evil.txt", b"abc"), ("Фото.png", b"\x89PNG")])]

    async def test_json_without_files_still_works(self, client, sent):
        from tests.test_web import BASE

        calls, token = sent
        response = await client.post(
            f"{BASE}/api/mail/send", params={"token": token},
            json={"to": ["a"], "subject": "Тема", "text": "Текст"},
        )
        assert response.status == 200 and calls == [(["a"], "Тема", "Текст", None)]

    async def test_limits_are_enforced(self, client, sent, monkeypatch):
        from app.web import api
        from tests.test_web import BASE

        calls, token = sent

        def post(files):
            body, headers = self._form(files)
            return client.post(f"{BASE}/api/mail/send", params={"token": token}, data=body, headers=headers)

        monkeypatch.setattr(api, "MAIL_MAX_FILES", 2)
        assert (await post([("a", b"1"), ("b", b"1"), ("c", b"1")])).status == 400

        monkeypatch.setattr(api, "MAIL_MAX_FILE_BYTES", 10)
        response = await post([("big", b"x" * 11)])
        assert response.status == 400 and "больше" in (await response.json())["error"]

        monkeypatch.setattr(api, "MAIL_MAX_FILE_BYTES", 10 * 1024 * 1024)
        monkeypatch.setattr(api, "MAIL_MAX_TOTAL_BYTES", 15)
        assert (await post([("a", b"x" * 10), ("b", b"x" * 10)])).status == 400

        assert (await post([("empty", b"")])).status == 400
        assert calls == []

    async def test_recipient_and_subject_are_still_required(self, client, sent):
        from tests.test_web import BASE

        calls, token = sent
        body, headers = self._form([("a", b"1")], to=())
        response = await client.post(f"{BASE}/api/mail/send", params={"token": token}, data=body, headers=headers)
        assert response.status == 400 and calls == []

    async def test_service_passes_files_to_mobile_client(self, service, holder, users):
        user = await users.save(mobile_user())
        await service.send_mail(user, ["a"], "Тема", "Текст")
        await service.send_mail(user, ["a"], "Тема", "Текст", [("a.txt", b"1")])

        sent = holder["mobile"].sent
        assert sent[0][3] == {} and sent[1][3] == {"attachments": [("a.txt", b"1")]}

    async def test_web_mode_refuses_files_and_sends_nothing(self, service, holder, users):
        user = await users.save(make_user())
        with pytest.raises(NetSchoolError, match="/changeapi"):
            await service.send_mail(user, ["a"], "Тема", "Текст", [("a.txt", b"1")])
        assert "web" not in holder  # клиента даже не поднимали

    async def test_profile_tells_whether_files_can_be_attached(self, client, app_context, users):
        from tests.test_web import BASE

        user = await users.save(mobile_user())
        token = await app_context.miniapp.issue_token(user.telegram_id)
        data = (await (await client.get(f"{BASE}/api/profile", params={"token": token})).json())["data"]
        assert data["api_mode"] == "mobile"
