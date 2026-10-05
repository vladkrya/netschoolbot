"""Тесты обработчиков бота.

Обработчики вызываются напрямую с поддельными объектами aiogram: контекст
приложения приходит параметром, а не берётся из глобальных переменных, как
в старой версии — благодаря этому их вообще можно вызвать в тесте.
"""

import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.bot.handlers import menu, settings as settings_handlers
from app.bot.handlers.settings import parse_interval, parse_quiet_hours
from app.bot.notifier import TelegramNotifier
from app.context import AppContext
from app.domain.models import MarkKind, NotificationPrefs, QuietHours
from app.domain.records import MarkEvent, MarkRecord
from app.netschool.errors import NetSchoolError, Reason
from tests.test_repositories import make_user
from tests.test_watcher import FakeDiary, FakeNotifier, record


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.answers: list[tuple[str, dict]] = []
        self.deleted = False

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return FakeMessage(text)

    async def answer_photo(self, photo, **kwargs):
        self.answers.append(("<фото>", kwargs))
        return FakeMessage("<фото>")

    async def edit_text(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_reply_markup(self, **kwargs):
        self.answers.append(("<клавиатура>", kwargs))

    async def delete(self):
        self.deleted = True

    @property
    def texts(self) -> str:
        return "\n".join(text for text, _ in self.answers)


class FakeCallback:
    def __init__(self, data="", message=None):
        self.data = data
        self.message = message or FakeMessage()
        self.answered: list[str] = []

    async def answer(self, text="", **kwargs):
        self.answered.append(text)


@pytest.fixture
async def app(db, users, marks, sessions, miniapp, cache):
    from app.netschool.session import SessionPool
    from app.notifications.watcher import WatcherRegistry
    from app.settings import (
        NetSchoolSettings, PushSettings, Settings, TelegramSettings, WebSettings,
    )
    from pathlib import Path

    diary = FakeDiary()
    notifier = FakeNotifier()
    watchers = WatcherRegistry(users=users, state=marks, diary=diary, notifier=notifier)
    settings = Settings(
        data_dir=Path("/tmp"),
        db_path=Path("/tmp/x.sqlite3"),
        telegram=TelegramSettings(bot_token="t", admin_id=1),
        web=WebSettings(
            enabled=True, host="127.0.0.1", port=8283,
            public_url="https://example.ru", miniapp_path="/mini",
            token_ttl=900, login_code_ttl=600,
            cache_fresh_seconds=3600,
        ),
        push=PushSettings(public_key="", private_key="", subject=""),
        netschool=NetSchoolSettings(
            default_check_interval=300, session_ttl=1800, http_timeout=20,
            blocked_host_ttl=600, qr_login_ttl=60, fallback_proxy="",
        ),
        debug=False,
    )
    context = AppContext(
        settings=settings, db=db, users=users, state=marks, sessions=sessions,
        miniapp=miniapp, cache=cache,
        pool=SessionPool(settings.netschool, sessions),
        diary=diary, watchers=watchers,
    )
    yield context
    await watchers.stop_all()


class TestMenu:
    async def test_greeting_before_login(self, app, users):
        user = await users.get_or_create(1)
        message = FakeMessage()
        await menu.show_menu(message, user, app)
        assert "login" in message.texts

    async def test_menu_has_no_app_button_before_login(self, app, users):
        # Кнопка мини-приложения без выбранной школы вела бы в пустоту.
        user = await users.get_or_create(1)
        message = FakeMessage()
        await menu.show_menu(message, user, app)
        assert len(message.answers) == 1
        assert message.answers[-1][1]["reply_markup"].remove_keyboard is True

    async def test_menu_has_app_button_after_login(self, app, users):
        user = await users.save(make_user())
        message = FakeMessage()
        await menu.show_menu(message, user, app)
        assert message.answers[0][1]["reply_markup"].remove_keyboard is True
        rows = message.answers[-1][1]["reply_markup"].inline_keyboard
        assert rows[0][0].web_app is not None
        assert rows[0][0].style == "primary"
        assert "token=" in rows[0][0].web_app.url

    async def test_homework_requires_school(self, app, users):
        user = await users.get_or_create(1)
        message = FakeMessage()
        await menu.homework(message, user, app)
        assert "/login" in message.texts

    async def test_homework_lists_items(self, app, users):
        from app.domain.records import HomeworkRecord

        user = await users.save(make_user())
        today = user.today()
        app.diary.homework = [
            HomeworkRecord("Алгебра", d, "ДЗ", "Упр. 1")
            for d in menu.next_school_days(today)
        ]
        message = FakeMessage()
        await menu.homework(message, user, app)
        assert "Алгебра" in message.texts

    async def test_netschool_error_is_shown_readably(self, app, users):
        user = await users.save(make_user())
        app.diary.error = NetSchoolError(Reason.AUTH, "Сессия истекла. Войдите: /login")
        message = FakeMessage()
        await menu.homework(message, user, app)
        assert "Сессия истекла" in message.texts

    async def test_grades_offers_subjects(self, app, users):
        user = await users.save(make_user())
        app.diary.marks = [record(0, subject="Алгебра"), record(1, subject="Химия")]
        message = FakeMessage()
        await menu.grades(message, user, app)
        assert "Выберите предмет" in message.texts
        cached, _ = await app.cache.get(1, "subjects")
        assert cached == ["Алгебра", "Химия"]

    async def test_subject_marks(self, app, users):
        user = await users.save(make_user())
        app.diary.marks = [record(0, subject="Алгебра"), record(1, subject="Химия")]
        await menu.grades(FakeMessage(), user, app)
        callback = FakeCallback("subject:0")
        await menu.subject_marks(callback, user, app)
        assert "Алгебра" in callback.message.texts

    async def test_stale_subject_index_is_handled(self, app, users):
        user = await users.save(make_user())
        await app.cache.put(1, "subjects", ["Алгебра"])
        callback = FakeCallback("subject:99")
        await menu.subject_marks(callback, user, app)
        assert "заново" in callback.message.texts

    async def test_statistics(self, app, users):
        user = await users.save(make_user(student_name="Иванов И."))
        app.diary.marks = [record(0), record(1)]
        message = FakeMessage()
        await menu.statistics(message, user, app)
        assert "Статистика" in message.texts

    async def test_app_opens_as_telegram_mini_app(self, app, users):
        """Кнопка должна открывать приложение внутри Telegram, а не в браузере."""
        user = await users.save(make_user())
        message = FakeMessage()
        await menu.open_miniapp(message, user, app)
        button = message.answers[-1][1]["reply_markup"].inline_keyboard[0][0]
        assert button.web_app is not None
        assert button.url is None
        assert button.web_app.url.startswith("https://example.ru/mini/?token=")

    async def test_install_link_is_a_plain_url(self, app, users):
        """Установить приложение на домашний экран можно только из браузера,
        поэтому здесь нужна обычная ссылка, а не кнопка Telegram."""
        user = await users.save(make_user())
        message = FakeMessage()
        await menu.install_miniapp(message, user, app)
        button = message.answers[-1][1]["reply_markup"].inline_keyboard[0][0]
        assert button.web_app is None
        assert button.url.startswith("https://example.ru/mini/?token=")

    async def test_app_requires_school(self, app, users):
        user = await users.get_or_create(1)
        message = FakeMessage()
        await menu.open_miniapp(message, user, app)
        assert "/login" in message.texts

    async def test_app_reset_changes_token(self, app, users):
        user = await users.save(make_user())
        first = await app.miniapp.issue_token(1)
        await menu.miniapp_reset(FakeMessage(), user, app)
        assert await app.miniapp.resolve_token(first) is None

    async def test_status_mentions_pending_baseline(self, app, users, marks):
        user = await users.save(make_user())
        await marks.mark_baseline_pending(1, True)
        message = FakeMessage()
        await menu.status(message, user, app)
        assert "молча" in message.texts


class TestNextSchoolDays:
    def test_friday_rolls_into_monday(self):
        friday = dt.date(2026, 3, 6)
        days = menu.next_school_days(friday)
        assert days == [dt.date(2026, 3, 6), dt.date(2026, 3, 9), dt.date(2026, 3, 10)]

    def test_saturday_starts_on_monday(self):
        days = menu.next_school_days(dt.date(2026, 3, 7))
        assert days[0] == dt.date(2026, 3, 9)

    def test_always_three_weekdays(self):
        for offset in range(14):
            days = menu.next_school_days(dt.date(2026, 3, 2) + dt.timedelta(days=offset))
            assert len(days) == 3
            assert all(d.weekday() < 5 for d in days)


class TestSettingsToggles:
    async def test_toggle_starts_watcher(self, app, users):
        user = await users.save(make_user(enabled=False))
        await settings_handlers.toggle(FakeCallback("toggle:enabled"), user, app)
        # Флаг и реально запущенная задача больше не расходятся.
        assert (await users.get(1)).enabled is True
        assert app.watchers.running == {1}

    async def test_toggle_off_stops_watcher(self, app, users):
        user = await users.save(make_user(enabled=True))
        await app.watchers.start(1)
        await settings_handlers.toggle(FakeCallback("toggle:enabled"), user, app)
        assert app.watchers.running == set()

    async def test_toggle_notification_flag(self, app, users):
        user = await users.save(make_user(notifications=NotificationPrefs(changes=True)))
        await settings_handlers.toggle(FakeCallback("toggle:changes"), user, app)
        assert (await users.get(1)).notifications.changes is False

    async def test_unknown_toggle_is_reported(self, app, users):
        user = await users.save(make_user())
        callback = FakeCallback("toggle:чепуха")
        await settings_handlers.toggle(callback, user, app)
        assert callback.answered == ["Не знаю такой настройки"]

    async def test_interval_change_restarts_watcher(self, app, users):
        user = await users.save(make_user(enabled=True, check_interval=300))
        await app.watchers.start(1)
        await settings_handlers.set_interval(FakeCallback("interval:600"), user, app)
        assert (await users.get(1)).check_interval == 600
        assert app.watchers.running == {1}


class TestChildSwitching:
    async def test_switch_resets_tracking(self, app, users, marks):
        from app.domain.models import Student
        from app.domain.records import TrackedMark

        user = await users.save(
            make_user(
                available_students=(Student(10, "А"), Student(11, "Б")),
                selected_student_id=10,
            )
        )
        r = record(0)
        await marks.replace_marks(1, {r.identity: TrackedMark(r)})

        await settings_handlers.set_child(FakeCallback("child:11"), user, app)

        updated = await users.get(1)
        assert updated.selected_student_id == 11
        assert updated.student_name == "Б"
        # Иначе журнал второго ребёнка приехал бы как «новые оценки».
        assert await marks.load_marks(1) == {}
        assert await marks.is_baseline_pending(1) is True

    async def test_unknown_child_is_rejected(self, app, users):
        from app.domain.models import Student

        user = await users.save(make_user(available_students=(Student(10, "А"),)))
        callback = FakeCallback("child:999")
        await settings_handlers.set_child(callback, user, app)
        assert callback.answered == ["Такого ученика нет в списке"]


class TestLogout:
    async def test_logout_clears_everything(self, app, users, marks, sessions):
        user = await users.save(make_user())
        await sessions.save(1, "{}")
        token = await app.miniapp.issue_token(1)
        await app.watchers.start(1)

        await settings_handlers.logout(FakeMessage(), user, app)

        updated = await users.get(1)
        assert updated.enabled is False
        assert updated.school.url == ""
        assert await sessions.load(1) is None
        assert await app.miniapp.resolve_token(token) is None
        assert app.watchers.running == set()


class TestParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [("10м", 600), ("10m", 600), ("1ч", 3600), ("600", 600), ("30s", 30), ("чушь", None), ("", None)],
    )
    def test_interval(self, raw, expected):
        assert parse_interval(raw) == expected

    @pytest.mark.parametrize(
        "raw,start,end",
        [
            ("22:00-07:00", dt.time(22), dt.time(7)),
            ("22:00 – 07:00", dt.time(22), dt.time(7)),
            ("9-18", dt.time(9), dt.time(18)),
        ],
    )
    def test_quiet_hours(self, raw, start, end):
        window = parse_quiet_hours(raw)
        assert window is not None
        assert (window.start, window.end) == (start, end)

    @pytest.mark.parametrize("raw", ["чушь", "22:00", "25:00-30:00", ""])
    def test_bad_quiet_hours(self, raw):
        assert parse_quiet_hours(raw) is None


class TestQuietHoursWindow:
    def test_window_across_midnight(self):
        window = QuietHours(dt.time(22), dt.time(7))
        assert window.covers(dt.time(23)) is True
        assert window.covers(dt.time(3)) is True
        assert window.covers(dt.time(12)) is False

    def test_normal_window(self):
        window = QuietHours(dt.time(9), dt.time(18))
        assert window.covers(dt.time(12)) is True
        assert window.covers(dt.time(20)) is False

    def test_boundaries(self):
        window = QuietHours(dt.time(22), dt.time(7))
        assert window.covers(dt.time(22)) is True
        assert window.covers(dt.time(7)) is False

    def test_disabled(self):
        assert QuietHours().covers(dt.time(3)) is False


class TestTelegramNotifier:
    class Bot:
        def __init__(self, error=None):
            self.sent: list[tuple[int, str]] = []
            self.error = error

        async def send_message(self, chat_id, text, **kwargs):
            if self.error:
                error, self.error = self.error, None
                raise error
            self.sent.append((chat_id, text))

    async def test_few_events_sent_separately(self, users, monkeypatch):
        import app.bot.notifier as module

        monkeypatch.setattr(module.asyncio, "sleep", _no_sleep)
        bot = self.Bot()
        user = await users.save(make_user())
        notifier = TelegramNotifier(bot, users)
        events = [MarkEvent(MarkKind.NEW, record(i)) for i in range(3)]
        await notifier.send_mark_events(user, events)
        assert len(bot.sent) == 3

    async def test_many_events_become_digest(self, users):
        bot = self.Bot()
        user = await users.save(make_user())
        notifier = TelegramNotifier(bot, users)
        events = [MarkEvent(MarkKind.NEW, record(i)) for i in range(10)]
        await notifier.send_mark_events(user, events)
        assert len(bot.sent) == 1
        assert "Изменения в оценках" in bot.sent[0][1]

    async def test_blocked_user_is_disabled(self, users):
        from aiogram.exceptions import TelegramForbiddenError

        bot = self.Bot(error=TelegramForbiddenError(method=None, message="bot was blocked"))
        user = await users.save(make_user(enabled=True))
        notifier = TelegramNotifier(bot, users)
        await notifier.send_mark_events(user, [MarkEvent(MarkKind.NEW, record())])
        # Иначе такой пользователь навсегда остаётся в цикле и на каждом
        # круге даёт ошибку в логах.
        assert (await users.get(1)).enabled is False

    async def test_rate_limit_is_retried_once(self, users, monkeypatch):
        import app.bot.notifier as module
        from aiogram.exceptions import TelegramRetryAfter

        monkeypatch.setattr(module.asyncio, "sleep", _no_sleep)
        bot = self.Bot(
            error=TelegramRetryAfter(method=None, message="flood", retry_after=1)
        )
        user = await users.save(make_user())
        notifier = TelegramNotifier(bot, users)
        await notifier.send_mark_events(user, [MarkEvent(MarkKind.NEW, record())])
        assert len(bot.sent) == 1


async def _no_sleep(_seconds):
    return None


class TestPushDelivery:
    class Push:
        def __init__(self, error=None):
            self.sent = []
            self.error = error

        async def send(self, telegram_id, title, body, url=""):
            if self.error:
                raise self.error
            self.sent.append((telegram_id, title, body))
            return 1

    async def test_push_accompanies_telegram(self, users):
        bot = TestTelegramNotifier.Bot()
        push = self.Push()
        user = await users.save(make_user())
        notifier = TelegramNotifier(bot, users, push)
        await notifier.send_mark_events(user, [MarkEvent(MarkKind.NEW, record())])
        assert len(bot.sent) == 1
        assert len(push.sent) == 1

    async def test_push_failure_does_not_break_telegram(self, users):
        bot = TestTelegramNotifier.Bot()
        push = self.Push(error=RuntimeError("сервис push недоступен"))
        user = await users.save(make_user())
        notifier = TelegramNotifier(bot, users, push)
        await notifier.send_mark_events(user, [MarkEvent(MarkKind.NEW, record())])
        # Telegram-сообщение ушло, несмотря на сбой push.
        assert len(bot.sent) == 1

    async def test_no_push_configured_is_fine(self, users):
        bot = TestTelegramNotifier.Bot()
        user = await users.save(make_user())
        notifier = TelegramNotifier(bot, users, None)
        await notifier.send_mark_events(user, [MarkEvent(MarkKind.NEW, record())])
        assert len(bot.sent) == 1
