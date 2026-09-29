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

    async def test_gosuslugi_user_stays_on_web_path(self, service, holder):
        from app.domain.models import Credentials, LoginType

        user = mobile_user(credentials=Credentials(login_type=LoginType.ESIA))
        assert await service.fetch_recent_events(user) is None

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
