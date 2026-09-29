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
