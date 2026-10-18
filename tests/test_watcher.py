"""Тесты цикла проверки оценок.

Проверяется поведение, которого нельзя было проверить в старом коде: там эта
логика жила внутри метода на 568 строк вместе с HTTP и Telegram.
"""

import asyncio
import datetime as dt

import pytest

from app.domain.models import Credentials, LoginType, MarkKind, QuietHours
from app.domain.records import HomeworkRecord, MarkRecord, RecentEvents, TrackedMark
from app.netschool.errors import NetSchoolError, Reason
from app.notifications.diff import MISSING_STREAK_FOR_DELETE
from app.notifications.watcher import (
    FULL_SYNC_INTERVAL, MAX_BACKOFF, UserWatcher, WatcherRegistry,
)
from tests.test_repositories import make_user


def record(index=0, value="5", date=dt.date(2026, 3, 2), subject=None):
    """Оценка по своему предмету.

    Предмет зависит от индекса намеренно: `loose_identity` не учитывает
    порядковый номер работы, поэтому записи, различающиеся только им,
    неотличимы при подтверждении удаления — как и в реальности.
    """
    return MarkRecord(
        subject=subject or f"Предмет {index}",
        date=date,
        assignment_type="Контрольная",
        content="Дроби",
        mark=value,
        lesson_number=index + 1,
        assignment_index=index,
    )


class FakeNotifier:
    def __init__(self):
        self.mark_events = []
        self.homework = []
        self.absences = []
        self.token_warnings = []
        self.errors = []
        self.texts = []

    async def send_mark_events(self, user, events):
        self.mark_events.extend(events)

    async def send_homework(self, user, items):
        self.homework.extend(items)

    async def send_absences(self, user, items):
        self.absences.extend(items)

    async def send_token_expiring(self, user, expires_at):
        self.token_warnings.append(expires_at)

    async def send_error(self, user, error):
        self.errors.append(error)

    async def send_text(self, user, text, *, push=None, kind="", family=True):
        self.texts.append(text)
        self.family = family


class FakeDiary:
    """Сервис дневника без сети."""

    def __init__(self):
        self.marks: list[MarkRecord] = []
        self.homework: list = []
        self.present_in_week: set[str] = set()
        self.error: Exception | None = None
        self.week_error: Exception | None = None
        self.week_queries = 0
        self.logged_out = False
        self.message = None
        self.events = None
        self.events_error: Exception | None = None
        self.recent_event_calls = 0
        self.totals = None
        self.token_check = None
        self.token_error: Exception | None = None
        self.token_checks = 0
        # Пропуски: (день, предмет, номер урока, код отметки).
        self.absent: list = []
        self.stats = None
        self.stats_calls: list = []
        self.diary_calls = 0

    async def fetch_marks(self, user, **kwargs):
        if self.error:
            raise self.error
        return list(self.marks)

    async def fetch_diary(self, user, **kwargs):
        """Дни дневника, собранные из заданных оценок и заданий."""
        from app.domain.records import DiaryDay, Lesson

        self.diary_calls += 1
        if self.error:
            raise self.error
        days: dict = {}
        for item in self.marks:
            days.setdefault(item.date, DiaryDay(day=item.date)).marks.append(item)
        for item in self.homework:
            days.setdefault(item.due_date, DiaryDay(day=item.due_date)).homework.append(item)
        for when, subject, number, code in self.absent:
            days.setdefault(when, DiaryDay(day=when)).lessons.append(
                Lesson(number=number, subject=subject, absence=code)
            )
        return [days[key] for key in sorted(days)]

    async def fetch_recent_events(self, user, **kwargs):
        """По умолчанию ленты событий нет — как в веб-режиме."""
        self.recent_event_calls += 1
        if self.events_error:
            raise self.events_error
        return self.events

    async def check_mobile_token(self, user, **kwargs):
        self.token_checks += 1
        if self.token_error:
            raise self.token_error
        return self.token_check

    async def fetch_school_years(self, user):
        return None

    async def fetch_totals(self, user):
        return self.totals

    async def fetch_subject_stats(self, user, subject, quarter):
        self.stats_calls.append((subject, quarter))
        return self.stats

    async def fetch_period(self, user, start, end, **kwargs):
        return await self.fetch_diary(user)

    async def fetch_terms(self, user):
        return getattr(self, "terms", None)

    async def fetch_teachers(self, user, wanted):
        return {subject: "Учитель" for subject in wanted}

    async def fetch_announcements(self, user, limit=30):
        if self.error:
            raise self.error
        return list(getattr(self, "announcements", []))

    async def fetch_mail(self, user, limit=20):
        if self.error:
            raise self.error
        return []

    async def fetch_chat_identity(self, user):
        if self.error:
            raise self.error
        identities = getattr(self, "chat_identities", {})
        return identities.get(user.telegram_id, {
            "is_student": True, "user_id": 100 + user.telegram_id,
            "name": "Соловьёв Владислав", "class_name": "9б",
        })

    async def fetch_assignment_comment(self, user, assignment_id):
        if self.error:
            raise self.error
        return getattr(self, "comments", {}).get(assignment_id, "")

    async def fetch_mail_message(self, user, message_id):
        if self.error:
            raise self.error
        return self.message or {}

    async def download_attachment(self, user, attachment_id):
        if self.error:
            raise self.error
        return b""

    async def fetch_homework(self, user, **kwargs):
        if self.error:
            raise self.error
        return list(self.homework)

    async def logout(self, user):
        self.logged_out = True

    async def sync_students(self, user):
        """По умолчанию список детей не меняется."""
        return user

    async def marks_present_in_week(self, user, week_day):
        self.week_queries += 1
        if self.week_error:
            raise self.week_error
        return set(self.present_in_week)


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def diary():
    return FakeDiary()


@pytest.fixture
def watcher(users, marks, diary, notifier):
    return UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)


class TestFirstRun:
    async def test_first_run_is_silent(self, users, watcher, diary, notifier):
        await users.save(make_user())
        diary.marks = [record(i) for i in range(20)]
        await watcher.run_once()
        assert notifier.mark_events == []

    async def test_first_run_records_state(self, users, marks, watcher, diary):
        await users.save(make_user())
        diary.marks = [record(i) for i in range(3)]
        await watcher.run_once()
        assert len(await marks.load_marks(1)) == 3

    async def test_second_run_reports_only_new(self, users, watcher, diary, notifier):
        await users.save(make_user())
        diary.marks = [record(0)]
        await watcher.run_once()
        diary.marks = [record(0), record(1)]
        await watcher.run_once()
        assert len(notifier.mark_events) == 1
        assert notifier.mark_events[0].kind is MarkKind.NEW


    async def test_open_app_hears_new_marks_even_with_notifications_off(self, users, marks, diary, notifier):
        """Сигнал приложению по сокету — о любом новом, но не о первой сверке."""
        from app.domain.models import NotificationPrefs

        signals = []

        async def live(user_id, event):
            signals.append((user_id, event))

        await users.save(make_user(notifications=NotificationPrefs(grades=False)))
        watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier, live=live)
        diary.marks = [record(0)]
        await watcher.run_once()
        assert signals == []
        await watcher.run_once()
        assert signals == []  # ничего не поменялось
        diary.marks = [record(0), record(1)]
        await watcher.run_once()
        assert signals == [(1, {"t": "diary", "kinds": ["marks"]})]
        assert notifier.mark_events == []

    async def test_seen_homework_goes_to_attachment_keeper(self, users, marks, diary, notifier):
        """Задания из полной сверки — хранилищу вложений (attachments/keeper.py)."""
        seen = []
        await users.save(make_user())
        watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier,
                              homework_seen=lambda user, items: seen.append((user.telegram_id, list(items))))
        diary.marks = [record(0)]
        await watcher.run_once()
        assert seen and seen[0][0] == 1


class TestChanges:
    async def test_changed_mark(self, users, watcher, diary, notifier):
        await users.save(make_user())
        diary.marks = [record(0, "3")]
        await watcher.run_once()
        diary.marks = [record(0, "5")]
        await watcher.run_once()
        event = notifier.mark_events[0]
        assert event.kind is MarkKind.CHANGED
        assert (event.old_mark, event.new_mark) == ("3", "5")


class TestDeletions:
    async def test_deletion_needs_confirmation(self, users, marks, watcher, diary, notifier):
        await users.save(make_user())
        gone = record(0)
        rest = [record(i) for i in range(1, 10)]
        state = {r.identity: TrackedMark(r) for r in rest}
        state[gone.identity] = TrackedMark(gone, missing_streak=MISSING_STREAK_FOR_DELETE - 1)
        await marks.replace_marks(1, state)

        diary.marks = rest
        diary.present_in_week = {r.loose_identity for r in rest}
        await watcher.run_once()
        assert [e.kind for e in notifier.mark_events] == [MarkKind.DELETED]

    async def test_still_present_mark_is_not_deleted(
        self, users, marks, watcher, diary, notifier
    ):
        """Оценка пропала из общего ответа, но есть при точечной проверке —
        значит, ответ был неполным, а не оценка удалена."""
        await users.save(make_user())
        gone = record(0)
        rest = [record(i) for i in range(1, 10)]
        state = {r.identity: TrackedMark(r) for r in rest}
        state[gone.identity] = TrackedMark(gone, missing_streak=MISSING_STREAK_FOR_DELETE - 1)
        await marks.replace_marks(1, state)

        diary.marks = rest
        diary.present_in_week = {r.loose_identity for r in rest} | {gone.loose_identity}
        await watcher.run_once()
        assert notifier.mark_events == []
        # Счётчик сброшен: следующая пропажа начнёт отсчёт заново.
        assert (await marks.load_marks(1))[gone.identity].missing_streak == 0

    async def test_confirmation_failure_keeps_mark(self, users, marks, watcher, diary, notifier):
        await users.save(make_user())
        gone = record(0)
        rest = [record(i) for i in range(1, 10)]
        state = {r.identity: TrackedMark(r) for r in rest}
        state[gone.identity] = TrackedMark(gone, missing_streak=MISSING_STREAK_FOR_DELETE - 1)
        await marks.replace_marks(1, state)

        diary.marks = rest
        diary.week_error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "сервер лежит")
        await watcher.run_once()
        assert notifier.mark_events == []
        assert gone.identity in await marks.load_marks(1)

    async def test_one_query_per_week(self, users, marks, watcher, diary):
        """Десять пропавших оценок одной недели — один запрос, а не десять."""
        await users.save(make_user())
        gone = [record(i) for i in range(10)]
        rest = [record(i, date=dt.date(2026, 3, 9)) for i in range(100, 130)]
        state = {r.identity: TrackedMark(r) for r in rest}
        for r in gone:
            state[r.identity] = TrackedMark(r, missing_streak=MISSING_STREAK_FOR_DELETE - 1)
        await marks.replace_marks(1, state)

        diary.marks = rest
        await watcher.run_once()
        assert diary.week_queries == 1


class TestQuietHours:
    async def test_quiet_hours_suppress_sending(self, users, watcher, diary, notifier, monkeypatch):
        import app.notifications.watcher as module

        # Полночь — внутри окна 22:00–07:00.
        monkeypatch.setattr(
            module, "_local_now", lambda user: dt.datetime(2026, 3, 2, 0, 30)
        )
        await users.save(make_user(quiet_hours=QuietHours(dt.time(22), dt.time(7))))
        diary.marks = [record(0)]
        await watcher.run_once()
        diary.marks = [record(0), record(1)]
        await watcher.run_once()
        assert notifier.mark_events == []

    async def test_queued_during_quiet_hours_sent_in_the_morning(
        self, users, watcher, diary, notifier, monkeypatch
    ):
        import app.notifications.watcher as module

        now = {"value": dt.datetime(2026, 3, 2, 0, 30)}
        monkeypatch.setattr(module, "_local_now", lambda user: now["value"])
        await users.save(make_user(quiet_hours=QuietHours(dt.time(22), dt.time(7))))
        diary.marks = [record(0)]
        await watcher.run_once()
        diary.marks = [record(0), record(1)]
        await watcher.run_once()
        assert notifier.mark_events == []

        now["value"] = dt.datetime(2026, 3, 2, 7, 30)
        await watcher.run_once()
        assert len(notifier.mark_events) == 1

    async def test_state_still_advances_during_quiet_hours(
        self, users, marks, watcher, diary, monkeypatch
    ):
        import app.notifications.watcher as module

        monkeypatch.setattr(module, "_local_now", lambda user: dt.datetime(2026, 3, 2, 0, 30))
        await users.save(make_user(quiet_hours=QuietHours(dt.time(22), dt.time(7))))
        diary.marks = [record(0)]
        await watcher.run_once()
        # Оценка запомнена, поэтому после тихих часов её не пришлют как новую.
        assert len(await marks.load_marks(1)) == 1

    async def test_outside_quiet_hours_sends(self, users, watcher, diary, notifier, monkeypatch):
        import app.notifications.watcher as module

        monkeypatch.setattr(module, "_local_now", lambda user: dt.datetime(2026, 3, 2, 12, 0))
        await users.save(make_user(quiet_hours=QuietHours(dt.time(22), dt.time(7))))
        diary.marks = [record(0)]
        await watcher.run_once()
        diary.marks = [record(0), record(1)]
        await watcher.run_once()
        assert len(notifier.mark_events) == 1


class TestPreferences:
    async def test_disabled_grade_notifications(self, users, watcher, diary, notifier):
        from app.domain.models import NotificationPrefs

        await users.save(make_user(notifications=NotificationPrefs(grades=False)))
        diary.marks = [record(0)]
        await watcher.run_once()
        diary.marks = [record(0), record(1)]
        await watcher.run_once()
        assert notifier.mark_events == []

    async def test_inactive_user_is_skipped(self, users, watcher, diary, notifier):
        await users.save(make_user(enabled=False))
        diary.marks = [record(0)]
        assert await watcher.run_once() == float(MAX_BACKOFF)
        assert notifier.mark_events == []


class TestErrors:
    async def test_expired_gosuslugi_session_does_not_notify(self, users, watcher, diary, notifier):
        await users.save(make_user(credentials=Credentials(login_type=LoginType.ESIA)))
        diary.error = NetSchoolError(Reason.AUTH, "Сессия Госуслуг истекла. Войдите заново")
        await watcher.run_once()
        await watcher.run_once()
        assert notifier.errors == []

    async def test_auth_error_reported_once(self, users, watcher, diary, notifier):
        await users.save(make_user())
        diary.error = NetSchoolError(Reason.AUTH, "войдите заново")
        await watcher.run_once()
        await watcher.run_once()
        # Сообщение одно, а не каждые пять минут.
        assert len(notifier.errors) == 1

    async def test_auth_error_backs_off_hard(self, users, watcher, diary):
        await users.save(make_user())
        diary.error = NetSchoolError(Reason.AUTH, "войдите заново")
        assert await watcher.run_once() == float(MAX_BACKOFF)

    async def test_server_error_is_not_reported_to_user(self, users, watcher, diary, notifier):
        await users.save(make_user())
        diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "сервер лежит")
        await watcher.run_once()
        # Временная недоступность школы — не повод писать человеку.
        assert notifier.errors == []

    async def test_backoff_grows_and_is_capped(self, users, watcher, diary):
        await users.save(make_user(check_interval=600))
        diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "лежит")
        delays = [await watcher.run_once() for _ in range(8)]
        assert delays[0] == 600
        assert delays == sorted(delays)
        assert max(delays) <= MAX_BACKOFF

    async def test_recovery_resets_backoff(self, users, watcher, diary):
        await users.save(make_user(check_interval=300))
        diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "лежит")
        await watcher.run_once()
        diary.error = None
        assert await watcher.run_once() == 300.0

    async def test_unexpected_error_does_not_kill_the_loop(self, users, watcher, diary):
        await users.save(make_user())
        diary.error = ValueError("опечатка")
        delay = await watcher.run_once()
        assert delay > 0
        diary.error = None
        assert await watcher.run_once() == 300.0

    async def test_failed_delivery_is_retried_once(self, users, marks, diary, notifier):
        """Сбой Telegram откладывает уведомление, а не теряет и не дублирует его."""

        class Failing(FakeNotifier):
            async def send_mark_events(self, user, events):
                raise RuntimeError("Telegram недоступен")

        await users.save(make_user())
        watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=Failing())
        diary.marks = [record(0)]
        await watcher.run_once()
        diary.marks = [record(0), record(1)]
        await watcher.run_once()  # отправка падает, событие остаётся в очереди
        assert len(await marks.pending_outbox(1)) == 1

        good = FakeNotifier()
        watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=good)
        await watcher.run_once()
        await watcher.run_once()
        assert [e.record.subject for e in good.mark_events] == ["Предмет 1"]
        assert await marks.pending_outbox(1) == []

    async def test_status_records_success_and_error(self, users, marks, watcher, diary):
        await users.save(make_user())
        diary.marks = [record(0)]
        await watcher.run_once()
        status = await marks.watch_status(1)
        assert status.last_ok_at is not None and status.next_check_at is not None

        diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "Сервер школы недоступен")
        await watcher.run_once()
        status = await marks.watch_status(1)
        assert status.last_error_at is not None
        assert status.last_error


class TestOutbox:
    async def test_stale_items_are_dropped(self, users, marks, watcher, diary, notifier):
        from app.notifications.outbox import OutboxKind, encode

        await users.save(make_user())
        await marks.save_check(
            1, tracked={}, homework=set(), observed_new_homework=set(), absences=None,
            outbox=[(OutboxKind.MARKS.value, encode(OutboxKind.MARKS, []))],
            history=[], baseline_done=False,
        )
        await marks._db.execute("UPDATE outbox SET created_at = '2000-01-01 00:00:00'")
        diary.marks = [record(0)]
        await watcher.run_once()
        assert await marks.pending_outbox(1) == []
        assert notifier.mark_events == []

    async def test_roundtrip_homework_and_absences(self):
        from app.domain.records import AbsenceRecord, Attachment
        from app.notifications.outbox import OutboxKind, decode, encode

        hw = HomeworkRecord(
            subject="Алгебра", due_date=dt.date(2026, 3, 3), assignment_type="ДЗ",
            text="№1", attachments=(Attachment(7, "a.pdf"),),
            published_at=dt.datetime(2026, 3, 2, 15, 0), topic="Дроби",
        )
        absence = AbsenceRecord("Физика", dt.date(2026, 3, 2), 3, "Б")
        assert decode(OutboxKind.HOMEWORK, encode(OutboxKind.HOMEWORK, [hw])) == [hw]
        assert decode(OutboxKind.ABSENCES, encode(OutboxKind.ABSENCES, [absence])) == [absence]


class TestHistory:
    async def test_history_keeps_events_with_notifications_off(
        self, users, marks, watcher, diary, notifier
    ):
        from app.domain.models import NotificationPrefs

        await users.save(make_user(notifications=NotificationPrefs(grades=False, changes=False)))
        diary.marks = [record(0)]
        await watcher.run_once()
        diary.marks = [record(0, value="4"), record(1)]
        await watcher.run_once()
        assert notifier.mark_events == []
        kinds = sorted(row["kind"] for row in await marks.mark_history(1))
        assert kinds == ["changed", "new"]


class TestRegistry:
    async def test_start_and_stop(self, users, marks, diary, notifier):
        await users.save(make_user())
        registry = WatcherRegistry(users=users, state=marks, diary=diary, notifier=notifier)
        await registry.start(1)
        assert registry.running == {1}
        await registry.stop(1)
        assert registry.running == set()

    async def test_restart_does_not_duplicate(self, users, marks, diary, notifier):
        await users.save(make_user())
        registry = WatcherRegistry(users=users, state=marks, diary=diary, notifier=notifier)
        await registry.start(1)
        await registry.start(1)
        # Именно этого не гарантировал словарь задач в старом runtime.
        assert len(registry.running) == 1
        await registry.stop_all()

    async def test_start_all_covers_active_users(self, users, marks, diary, notifier):
        await users.save(make_user(1))
        await users.save(make_user(2))
        await users.save(make_user(3, enabled=False))
        registry = WatcherRegistry(users=users, state=marks, diary=diary, notifier=notifier)
        await registry.start_all()
        assert registry.running == {1, 2}
        await registry.stop_all()

    async def test_stop_all_is_idempotent(self, users, marks, diary, notifier):
        registry = WatcherRegistry(users=users, state=marks, diary=diary, notifier=notifier)
        await registry.stop_all()
        await registry.stop_all()
        assert registry.running == set()


class TestStudentNameRefresh:
    """Починка уже сохранённых заглушек вместо имени.

    Записи, созданные до исправления разбора student/diary/init, хранят
    «Ученик 583039». Сами они не обновятся: вход эти люди больше не проходят.
    """

    async def test_placeholder_is_refreshed(self, users, marks, diary, notifier):
        from app.domain.models import Student
        from app.notifications.watcher import UserWatcher

        await users.save(make_user(student_name="Ученик 583039"))

        async def sync(user):
            from dataclasses import replace

            return await users.save(
                replace(
                    user,
                    student_name="Иванов Иван",
                    available_students=(Student(583039, "Иванов Иван"),),
                )
            )

        diary.sync_students = sync
        watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)
        await watcher.run_once()
        assert (await users.get(1)).student_name == "Иванов Иван"

    async def test_real_name_is_left_alone(self, users, marks, diary, notifier):
        from app.notifications.watcher import UserWatcher

        await users.save(make_user(student_name="Петров Пётр"))
        called = False

        async def sync(user):
            nonlocal called
            called = True
            return user

        diary.sync_students = sync
        watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)
        await watcher.run_once()
        # Лишний запрос к школьному серверу на каждом круге не нужен.
        assert called is False

    async def test_failed_refresh_does_not_break_the_check(self, users, marks, diary, notifier):
        from app.notifications.watcher import UserWatcher

        await users.save(make_user(student_name=""))

        async def sync(user):
            raise NetSchoolError(Reason.SERVER_UNAVAILABLE, "школа лежит")

        diary.sync_students = sync
        diary.marks = [record(0)]
        watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)
        await watcher.run_once()
        # Проверка оценок всё равно прошла и состояние записано.
        assert len(await marks.load_marks(1)) == 1


class TestRecentEvents:
    """Мобильный режим: лента событий решает, нужен ли полный дневник."""

    @pytest.fixture
    def clock(self):
        class Clock:
            now = 1000.0

            def __call__(self):
                return self.now

        return Clock()

    @pytest.fixture
    def mobile_watcher(self, users, marks, diary, notifier, clock):
        return UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier, clock=clock)

    async def test_quiet_feed_skips_full_diary(self, users, mobile_watcher, diary):
        await users.save(make_user())
        diary.marks = [record(0)]
        diary.events = RecentEvents(keys=frozenset({"r:1"}), marks=1)

        await mobile_watcher.run_once()
        assert diary.diary_calls == 1  # первая проверка — полным путём

        for _ in range(3):
            await mobile_watcher.run_once()
        assert diary.diary_calls == 1
        assert diary.recent_event_calls == 4

    async def test_new_mark_in_feed_becomes_notification(
        self, users, mobile_watcher, diary, notifier
    ):
        await users.save(make_user())
        diary.marks = [record(0)]
        diary.events = RecentEvents(keys=frozenset({"r:1"}), marks=1)
        await mobile_watcher.run_once()

        diary.marks = [record(0), record(1, "5", subject="Химия")]
        diary.events = RecentEvents(keys=frozenset({"r:1", "r:2"}), marks=2)
        await mobile_watcher.run_once()

        assert diary.diary_calls == 2
        assert [(e.kind, e.record.subject) for e in notifier.mark_events] == [
            (MarkKind.NEW, "Химия")
        ]

    async def test_new_homework_in_feed_becomes_notification(
        self, users, mobile_watcher, diary, notifier
    ):
        await users.save(make_user())
        diary.marks = [record(0)]  # у пользователя уже есть история проверок
        diary.events = RecentEvents(keys=frozenset({"h:1"}), homework=1)
        await mobile_watcher.run_once()

        item = HomeworkRecord("Алгебра", dt.date(2026, 3, 3), "ДЗ", "Упр. 1", lesson_number=2)
        diary.homework = [item]
        diary.events = RecentEvents(keys=frozenset({"h:1", "h:2"}), homework=2)
        await mobile_watcher.run_once()

        assert notifier.homework == [item]

    async def test_full_sync_repeats_hourly(self, users, mobile_watcher, diary, clock):
        # Лента не видит удалений и правок давних оценок — их ловит сверка.
        await users.save(make_user())
        diary.events = RecentEvents(keys=frozenset({"r:1"}), marks=1)
        await mobile_watcher.run_once()
        await mobile_watcher.run_once()
        assert diary.diary_calls == 1

        clock.now += FULL_SYNC_INTERVAL
        await mobile_watcher.run_once()
        assert diary.diary_calls == 2

    async def test_failed_full_sync_is_retried(self, users, mobile_watcher, diary, notifier):
        await users.save(make_user())
        diary.marks = [record(0)]
        diary.events = RecentEvents(keys=frozenset({"r:1"}), marks=1)
        await mobile_watcher.run_once()

        diary.marks = [record(0), record(1)]
        diary.events = RecentEvents(keys=frozenset({"r:1", "r:2"}), marks=2)
        diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "недоступно")
        await mobile_watcher.run_once()
        assert notifier.mark_events == []

        # Событие не считается обработанным, пока дневник не сверён.
        diary.error = None
        await mobile_watcher.run_once()
        assert len(notifier.mark_events) == 1

    async def test_web_mode_walks_full_diary_every_time(self, users, watcher, diary):
        await users.save(make_user())
        diary.marks = [record(0)]
        for _ in range(3):
            await watcher.run_once()
        assert diary.diary_calls == 3


class TestOutageRecovery:
    async def test_disappearing_records_do_not_notify_again_after_restart(
        self, users, marks, diary, notifier
    ):
        from app.domain.models import ApiMode, NotificationPrefs
        from app.netschool.service import msk_today

        user = make_user(api_mode=ApiMode.MOBILE, notifications=NotificationPrefs(absences=True))
        await users.save(user)
        today = msk_today()
        old = HomeworkRecord("Алгебра", today, "Домашняя работа", "№278")
        diary.marks = [record(date=today)]
        diary.homework = [old]
        diary.absent = [(today, "Алгебра", 1, "Б")]

        def new_watcher():
            return UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)

        watcher = new_watcher()
        await watcher.run_once()  # Исходная молчаливая сверка.
        diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "503")
        await watcher.run_once()
        assert await marks.load_homework(1) == {old.identity}
        diary.error = None
        diary.homework = []
        diary.absent = []
        await watcher.run_once()  # Сервер временно вернул пустые разделы.

        fresh = HomeworkRecord("Алгебра", today, "Домашняя работа", "№279")
        diary.homework = [old, fresh]
        diary.absent = [(today, "Алгебра", 1, "Б"), (today, "Физика", 2, "Б")]
        await new_watcher().run_once()  # История переживает перезапуск проверки.
        assert notifier.homework == [fresh]
        assert len(notifier.absences) == 1
        assert notifier.absences[0].subject == "Физика"
        await new_watcher().run_once()
        assert notifier.homework == [fresh]
        assert len(notifier.absences) == 1


class TestApiSwitchDoesNotFakeDeletions:
    async def test_stale_keys_after_switch_never_become_deleted_marks(
        self, users, marks, watcher, diary, notifier
    ):
        """Старые ключи от прежнего API исчезают при молчаливой пересборке.

        Раньше они копили пропажи, и после нескольких быстрых проходов
        (например, при перезапусках бота) приходило «Оценка удалена» по
        оценкам, которые на месте.
        """
        await users.save(make_user())
        stale = [record(i, subject=f"Старый {i}") for i in range(6)]
        await marks.replace_marks(1, {r.identity: TrackedMark(r, missing_streak=2) for r in stale})
        await marks.mark_baseline_pending(1, True)
        diary.marks = [record(i, subject=f"Новый {i}") for i in range(6)]

        for _ in range(MISSING_STREAK_FOR_DELETE + 2):
            await watcher.run_once()

        assert notifier.mark_events == []
        assert set((await marks.load_marks(1))) == {r.identity for r in diary.marks}


class TestDigests:
    @staticmethod
    def _at(monkeypatch, moment):
        import app.notifications.watcher as module

        monkeypatch.setattr(module, "_local_now", lambda user: moment)

    async def test_evening_summary_sent_once(self, users, watcher, diary, notifier, monkeypatch):
        from app.domain.models import NotificationPrefs

        # Воскресенье, 20:00 — сводка на понедельник 2 марта.
        self._at(monkeypatch, dt.datetime(2026, 3, 1, 20, 0))
        await users.save(make_user(notifications=NotificationPrefs(evening_summary=True)))
        diary.homework = [HomeworkRecord("Алгебра", dt.date(2026, 3, 2), "ДЗ", "№ 5")]
        await watcher.run_once()
        await watcher.run_once()
        assert len(notifier.texts) == 1
        assert "Алгебра" in notifier.texts[0] and "№ 5" in notifier.texts[0]

    async def test_no_evening_summary_before_saturday(self, users, watcher, diary, notifier, monkeypatch):
        from app.domain.models import NotificationPrefs

        # Пятница вечер: завтра суббота — рано.
        self._at(monkeypatch, dt.datetime(2026, 3, 6, 20, 0))
        await users.save(make_user(notifications=NotificationPrefs(evening_summary=True)))
        diary.homework = [HomeworkRecord("Алгебра", dt.date(2026, 3, 9), "ДЗ", "№ 5")]
        await watcher.run_once()
        assert notifier.texts == []

    async def test_weekly_summary_on_monday(self, users, watcher, diary, notifier, monkeypatch):
        from app.domain.models import NotificationPrefs

        self._at(monkeypatch, dt.datetime(2026, 3, 9, 9, 0))
        await users.save(make_user(notifications=NotificationPrefs(weekly_summary=True)))
        diary.marks = [record(0, date=dt.date(2026, 3, 4))]
        await watcher.run_once()
        await watcher.run_once()
        assert len(notifier.texts) == 1
        assert "Сводка за неделю" in notifier.texts[0]

    async def test_disabled_by_default(self, users, watcher, diary, notifier, monkeypatch):
        self._at(monkeypatch, dt.datetime(2026, 3, 9, 20, 0))
        await users.save(make_user())
        await watcher.run_once()
        assert notifier.texts == []


class TestTimezone:
    def test_unknown_zone_falls_back_to_moscow(self):
        user = make_user(timezone="Mars/Olympus")
        assert str(user.tz) == "Europe/Moscow"

    async def test_timezone_persists(self, users):
        await users.save(make_user(timezone="Asia/Novosibirsk"))
        assert (await users.get(1)).timezone == "Asia/Novosibirsk"


class TestStartOfYear:
    async def test_homework_is_notified_before_first_mark(self, users, watcher, diary, notifier):
        """1 сентября оценок ещё нет — это не повод молчать о заданиях."""
        await users.save(make_user())
        diary.homework = [HomeworkRecord("Алгебра", dt.date(2026, 9, 2), "ДЗ", "№ 1")]
        await watcher.run_once()
        diary.homework.append(HomeworkRecord("Физика", dt.date(2026, 9, 3), "ДЗ", "§ 1"))
        await watcher.run_once()
        assert [h.subject for h in notifier.homework] == ["Физика"]

class TestMailNotifications:
    async def test_baseline_new_mail_and_no_duplicates(self, users, marks, watcher, diary, notifier):
        user = make_user()
        from dataclasses import replace
        user = replace(user, notifications=replace(user.notifications, mail=True))
        await users.save(user)
        inbox = [{'id': 1, 'subject': 'Старое', 'author': 'Учитель', 'read': False}]
        async def mail(user, limit=20):
            return list(inbox)
        diary.fetch_mail = mail
        await watcher.run_once()
        assert not notifier.texts
        inbox.append({'id': 2, 'subject': '<Новое>', 'author': 'Учитель', 'read': True})
        # Mail is checked even when mobile event feed avoids full diary sync.
        diary.events = RecentEvents(keys=frozenset())
        await watcher.run_once()
        await watcher.run_once()
        assert len(notifier.texts) == 1
        assert '&lt;Новое&gt;' in notifier.texts[0]
        assert await marks.seen_mail_ids(1) == {1, 2}

    async def test_disabled_mail_is_still_seen(self, users, marks, watcher, diary, notifier):
        await users.save(make_user())
        inbox = []
        async def mail(user, limit=20): return list(inbox)
        diary.fetch_mail = mail
        await watcher.run_once()
        inbox.append({'id': 2, 'subject': 'Новое'})
        await watcher.run_once()
        assert await marks.seen_mail_ids(1) == {2}
        assert not notifier.texts

    async def test_quiet_hours_queue_and_retry(self, users, marks, watcher, diary, notifier):
        from dataclasses import replace
        user = make_user()
        user = replace(user, notifications=replace(user.notifications, mail=True),
                       quiet_hours=QuietHours(start=dt.time(0), end=dt.time(0)))
        await users.save(user)
        inbox = []
        async def mail(user, limit=20): return list(inbox)
        diary.fetch_mail = mail
        await watcher.run_once()
        inbox.append({'id': 3, 'subject': 'Письмо'})
        await watcher.run_once()
        assert not notifier.texts
        assert len(await marks.pending_outbox(1)) == 1
        await users.save(replace(user, quiet_hours=QuietHours()))
        await watcher.run_once()
        assert len(notifier.texts) == 1
        assert not await marks.pending_outbox(1)

    async def test_delivery_failure_survives_watcher_restart(self, users, marks, watcher, diary, notifier):
        from dataclasses import replace
        user = make_user()
        await users.save(replace(user, notifications=replace(user.notifications, mail=True)))
        inbox = []
        async def mail(user, limit=20): return list(inbox)
        diary.fetch_mail = mail
        await watcher.run_once()
        inbox.append({'id': 7, 'subject': 'Новое'})
        original = notifier.send_text
        async def fail(*args, **kwargs): raise RuntimeError('Telegram unavailable')
        notifier.send_text = fail
        await watcher.run_once()
        assert len(await marks.pending_outbox(1)) == 1
        notifier.send_text = original
        restarted = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)
        await restarted.run_once()
        assert len(notifier.texts) == 1
        assert not await marks.pending_outbox(1)

    async def test_mail_checked_when_diary_fails_and_mail_failure_does_not_seed(self, users, marks, watcher, diary, notifier):
        from dataclasses import replace
        user = make_user()
        await users.save(replace(user, notifications=replace(user.notifications, mail=True)))
        async def failed_mail(user, limit=20): raise RuntimeError('school offline')
        diary.fetch_mail = failed_mail
        await watcher.run_once()
        inbox = [{'id': 1, 'subject': 'Старое'}]
        async def mail(user, limit=20): return list(inbox)
        diary.fetch_mail = mail
        await watcher.run_once()
        assert not notifier.texts
        diary.error = RuntimeError('diary offline')
        inbox.append({'id': 2, 'subject': 'Новое'})
        await watcher.run_once()
        assert len(notifier.texts) == 1


async def test_auth_notice_survives_restart_and_alternating_failures(users, marks, watcher, diary, notifier):
    await users.save(make_user())
    diary.error = NetSchoolError(Reason.AUTH, 'Войдите: /login')
    await watcher.run_once()
    diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, 'Технические работы')
    await watcher.run_once()
    restarted = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)
    diary.error = NetSchoolError(Reason.AUTH, 'Войдите: /login')
    await restarted.run_once()
    assert len(notifier.errors) == 1
    diary.error = None
    await restarted.run_once()
    diary.error = NetSchoolError(Reason.AUTH, 'Новое истечение сессии')
    await restarted.run_once()
    assert len(notifier.errors) == 2

async def test_failed_error_notice_can_be_retried(users, marks, watcher, diary, notifier):
    await users.save(make_user())
    original = notifier.send_error
    async def fail(*args):
        raise RuntimeError('Telegram unavailable')
    notifier.send_error = fail
    diary.error = NetSchoolError(Reason.AUTH, 'Войдите: /login')
    with pytest.raises(RuntimeError):
        await watcher.run_once()
    notifier.send_error = original
    await watcher.run_once()
    assert len(notifier.errors) == 1
