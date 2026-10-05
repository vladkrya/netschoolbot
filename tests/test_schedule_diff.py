"""Изменения расписания: что считается заменой, а что — сбоем ответа."""

import datetime as dt

from app.domain.models import NotificationPrefs
from app.domain.records import DiaryDay, Lesson
from app.notifications.schedule_diff import diff_day, diff_schedule, watched_days
from app.notifications.watcher import UserWatcher
from tests.test_repositories import make_user
from tests.test_watcher import FakeDiary, FakeNotifier

DAY = dt.date(2026, 10, 7)


def lessons(*items):
    return [(n, s, r) for n, s, r in items]


def test_replacement_cancel_and_room():
    old = lessons((1, "Алгебра", "21"), (2, "Физика", "30"), (3, "История", "12"))
    new = lessons((1, "Алгебра", "25"), (2, "Химия", "30"))
    kinds = [(c.number, c.kind) for c in diff_day(DAY, old, new)]
    assert kinds == [(1, "room"), (2, "replaced"), (3, "cancelled")]


def test_empty_day_is_not_a_cancellation():
    assert diff_day(DAY, lessons((1, "Алгебра", "")), []) == []


def test_many_changes_collapse():
    old = lessons(*[(i, f"П{i}", "") for i in range(1, 7)])
    new = lessons(*[(i, f"Н{i}", "") for i in range(1, 7)])
    assert [c.kind for c in diff_day(DAY, old, new)] == ["reshuffled"]


def test_new_days_are_silent_and_window_is_a_week():
    today = dt.date(2026, 10, 5)
    days = [DiaryDay(day=today + dt.timedelta(days=i), lessons=[Lesson(1, "Алгебра")]) for i in range(10)]
    snap = watched_days(days, today)
    assert max(snap) == today + dt.timedelta(days=7)
    assert diff_schedule({}, snap) == []


async def test_watcher_notifies_about_replacement(users, marks, monkeypatch):
    import app.notifications.watcher as module

    monkeypatch.setattr(module, "_local_now", lambda user: dt.datetime(2026, 10, 6, 12, 0))
    diary, notifier = FakeDiary(), FakeNotifier()
    schedule = {"lessons": [Lesson(1, "Алгебра", start="08:00"), Lesson(2, "Физика")]}

    async def fetch_diary(user, **kwargs):
        return [DiaryDay(day=DAY, lessons=list(schedule["lessons"]))]

    diary.fetch_diary = fetch_diary
    await users.save(make_user(notifications=NotificationPrefs(schedule=True)))
    watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)
    await watcher.run_once()  # первый проход — молча
    await watcher.run_once()
    assert notifier.texts == []

    schedule["lessons"] = [Lesson(1, "Алгебра", start="08:00"), Lesson(2, "Химия")]
    watcher._last_full_sync = None  # следующий проход сверяет дневник целиком
    await watcher.run_once()
    assert len(notifier.texts) == 1
    assert "Физика → <b>Химия</b>" in notifier.texts[0]
