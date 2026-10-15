"""Виджет на iPhone: ключ только на расписание и само расписание."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.domain.records import DiaryDay, Lesson
from app.netschool.errors import NetSchoolError, Reason
from app.web import widget
from tests.test_web import BASE, client, token  # noqa: F401 — фикстуры
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура

MSK = ZoneInfo("Europe/Moscow")


def day(date, *lessons):
    return DiaryDay(day=date, lessons=list(lessons))


class TestWidgetDays:
    def test_times_become_unix_seconds_in_user_zone(self):
        today = dt.date(2026, 10, 15)
        days = widget.widget_days([day(today, Lesson(number=6, subject="Английский", start="13:40", end="14:20", room="7"))], MSK, today)
        lesson = days[0]["lessons"][0]
        assert lesson["start"] == int(dt.datetime(2026, 10, 15, 13, 40, tzinfo=MSK).timestamp())
        assert lesson["end"] - lesson["start"] == 40 * 60
        assert (lesson["number"], lesson["subject"], lesson["room"], lesson["start_text"]) == (6, "Английский", "7", "13:40")

    def test_skips_past_days_untimed_lessons_and_empty_days(self):
        today = dt.date(2026, 10, 15)
        days = widget.widget_days([
            day(today - dt.timedelta(days=1), Lesson(number=1, subject="Вчера", start="08:00", end="08:40")),
            day(today, Lesson(number=1, subject="Без времени"), Lesson(number=2, subject="Алгебра", start="09:00", end="09:40")),
            day(today + dt.timedelta(days=1), Lesson(number=1, subject="Сломано", start="10:00", end="09:00")),
            day(today + dt.timedelta(days=2), Lesson(number=1, subject="Физика", start="08:00", end="08:40")),
        ], MSK, today)
        assert [d["date"] for d in days] == ["2026-10-15", "2026-10-17"]
        assert [lesson["subject"] for lesson in days[0]["lessons"]] == ["Алгебра"]

    def test_lessons_are_sorted_and_days_limited(self):
        today = dt.date(2026, 10, 15)
        days = widget.widget_days([
            day(today + dt.timedelta(days=offset),
                Lesson(number=2, subject="Второй", start="09:00", end="09:40"),
                Lesson(number=1, subject="Первый", start="08:00", end="08:40"))
            for offset in range(6)
        ], MSK, today)
        assert len(days) == widget.MAX_DAYS
        assert [lesson["subject"] for lesson in days[0]["lessons"]] == ["Первый", "Второй"]


@pytest.fixture
def lessons(app_context):
    calls = []

    async def fetch_period(user, start, end, *, today=None):
        calls.append((start, end))
        return [day(today, Lesson(number=1, subject="Алгебра", start="08:00", end="08:40", room="12"))]

    app_context.diary.fetch_period = fetch_period
    return calls


async def create_key(client, token):
    response = await client.post(f"{BASE}/api/widget-key", headers={"X-Netschool-Token": token})
    assert response.status == 200
    script = (await response.json())["data"]["script"]
    key = script.split('const KEY = "', 1)[1].split('"', 1)[0]
    return script, key


class TestWidgetEndpoint:
    async def test_script_carries_key_and_server(self, client, token, app_context):
        script, key = await create_key(client, token)
        assert "__KEY__" not in script and "__SERVER__" not in script
        assert f'const SERVER = "{app_context.settings.web.miniapp_url.rstrip("/")}"' in script
        assert len(key) > 30

    async def test_schedule_by_key(self, client, token, lessons):
        _, key = await create_key(client, token)
        response = await client.get(f"{BASE}/widget/schedule", headers={"X-Widget-Key": key})
        assert response.status == 200 and response.headers["Cache-Control"] == "no-store"
        data = (await response.json())["data"]
        assert data["days"][0]["lessons"][0]["subject"] == "Алгебра"
        assert data["stale"] is False
        status = (await (await client.get(f"{BASE}/api/widget-key", headers={"X-Netschool-Token": token})).json())["data"]
        assert status["connected"] and status["used_at"]

    async def test_repeated_requests_use_cache(self, client, token, lessons):
        _, key = await create_key(client, token)
        for _ in range(3):
            assert (await client.get(f"{BASE}/widget/schedule", params={"key": key})).status == 200
        assert len(lessons) == 1

    async def test_school_down_serves_last_schedule(self, client, token, lessons, app_context, monkeypatch):
        _, key = await create_key(client, token)
        await client.get(f"{BASE}/widget/schedule", params={"key": key})
        monkeypatch.setattr(widget, "FRESH", dt.timedelta(0))

        async def down(*args, **kwargs):
            raise NetSchoolError(Reason.SERVER_UNAVAILABLE, "Школа недоступна")

        app_context.diary.fetch_period = down
        data = (await (await client.get(f"{BASE}/widget/schedule", params={"key": key})).json())["data"]
        assert data["stale"] is True and data["days"][0]["lessons"][0]["subject"] == "Алгебра"

    async def test_wrong_and_revoked_keys_are_rejected(self, client, token, lessons, monkeypatch):
        async def no_wait(_):
            return None

        monkeypatch.setattr(widget.asyncio, "sleep", no_wait)
        _, old = await create_key(client, token)
        _, key = await create_key(client, token)
        assert (await client.get(f"{BASE}/widget/schedule", params={"key": old})).status == 401
        assert (await client.get(f"{BASE}/widget/schedule", params={"key": "nope"})).status == 401
        assert (await client.get(f"{BASE}/widget/schedule")).status == 401
        await client.delete(f"{BASE}/api/widget-key", headers={"X-Netschool-Token": token})
        assert (await client.get(f"{BASE}/widget/schedule", params={"key": key})).status == 401

    async def test_key_does_not_open_the_diary(self, client, token):
        _, key = await create_key(client, token)
        assert (await client.get(f"{BASE}/api/profile", params={"token": key})).status == 401
        assert (await client.get(f"{BASE}/api/profile", headers={"X-Widget-Key": key})).status == 401
