"""Доставка уведомлений в Telegram.

Реализация протокола `Notifier` из watcher.py. Вынесена отдельно, чтобы цикл
проверки оценок можно было тестировать без Telegram — в старом коде отправка
была вшита в тот же метод, что и вся логика.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove

from ..accounts import has_telegram
from ..db.repositories import UserRepository
from ..domain import formatting
from ..domain.models import User
from ..domain.records import AbsenceRecord, HomeworkRecord, MarkEvent
from ..netschool.errors import NetSchoolError, Reason

logger = logging.getLogger("netschoolbot.notifier")


def _events_preview(events: list[MarkEvent]) -> str:
    """Короткая строка для всплывающего уведомления в браузере."""
    parts = [f"{e.record.subject}: {e.record.mark}" for e in events[:3]]
    if len(events) > 3:
        parts.append(f"и ещё {len(events) - 3}")
    return ", ".join(parts)

def _many_children(user: User) -> bool:
    """Следят за несколькими детьми — уведомление нужно подписать именем."""
    return user.notifications.all_children and len(user.available_students) > 1


# Больше этого числа событий — присылаем одну сводку вместо потока сообщений.
DIGEST_THRESHOLD = 5
BETWEEN_MESSAGES = 2.0
# Сколько помнить отправленный push: уведомление, которое не дошло до
# Telegram, остаётся в очереди и повторяется — push второй раз не нужен.
PUSH_DEDUP_SECONDS = 24 * 3600


class TelegramNotifier:
    def __init__(self, bot: Bot, users: UserRepository, push=None, family=None) -> None:
        self._bot = bot
        self._users = users
        # Родные (FamilyRepository): копии уведомлений в их Telegram и push.
        self._family = family
        # Push в браузер — дополнение к Telegram, а не замена: приложение
        # может быть не установлено, а подписка — отозвана без уведомления.
        self._push = push
        self._pushed: dict[tuple[int, str, str], float] = {}

    # Push уходит раньше Telegram: раньше сбой Telegram (а он бывает — сеть
    # до api.telegram.org рвётся) держал в очереди и push, и на телефон не
    # приходило ничего, пока не оживёт Telegram.

    async def send_mark_events(self, user: User, events: list[MarkEvent]) -> None:
        if not events:
            return
        await self._push_notify(user, "Новые оценки", _events_preview(events), kind="marks")
        if len(events) > DIGEST_THRESHOLD:
            await self._notify(user, formatting.mark_events_digest(events))
        else:
            for index, event in enumerate(events):
                if index and user.notifications.telegram:
                    await asyncio.sleep(BETWEEN_MESSAGES)
                await self._notify(user, formatting.mark_event(event))

    async def send_homework(self, user: User, items: list[HomeworkRecord]) -> None:
        if not items:
            return
        await self._push_notify(
            user,
            "Новые задания",
            ", ".join(sorted({item.subject for item in items})[:4]),
            kind="homework",
        )
        await self._notify(user, formatting.homework_digest(items, tz=user.tz))

    async def send_absences(self, user: User, items: list[AbsenceRecord]) -> None:
        if not items:
            return
        await self._push_notify(
            user,
            "Отметки посещаемости",
            ", ".join(sorted({item.subject for item in items})[:4]),
            kind="marks",
        )
        await self._notify(user, formatting.absences_digest(items))

    async def send_token_expiring(self, user: User, expires_at: dt.datetime) -> None:
        await self._send(user, formatting.token_expiring(expires_at, tz=user.tz))

    async def _push_notify(self, user: User, title: str, body: str, *, kind: str = "", family: bool = True) -> None:
        """Отправить push, не давая его сбою повлиять на Telegram."""
        if self._push is None:
            return
        now = time.monotonic()
        self._pushed = {key: at for key, at in self._pushed.items() if now - at < PUSH_DEDUP_SECONDS}
        if _many_children(user) and user.student_name:
            body = f"{user.student_name}: {body}"
        key = (user.telegram_id, title, body)
        if key in self._pushed:
            return
        try:
            if await self._push.send(user.telegram_id, title, body, kind=kind):
                self._pushed[key] = now
        except Exception as exc:  # noqa: BLE001
            logger.warning("Push для %s не ушёл: %s", user.telegram_id, exc)
        if family:
            await self._push_followers(user, title, body, kind=kind, now=now)

    async def _push_followers(self, user: User, title: str, body: str, *, kind: str, now: float) -> None:
        """Push родным: тем, кто пришёл по почте без Telegram, это единственный канал."""
        if self._family is None:
            return
        try:
            followers = await self._family.followers(user.telegram_id, notify_only=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Список родных %s не прочитан: %s", user.telegram_id, exc)
            return
        body = f"{user.label}: {body}"
        for follower in followers:
            key = (follower["id"], title, body)
            if key in self._pushed:
                continue
            try:
                if await self._push.send(follower["id"], title, body, kind=kind):
                    self._pushed[key] = now
            except Exception as exc:  # noqa: BLE001 — копия вторична
                logger.info("Push родному %s не ушёл: %s", follower["id"], exc)

    async def send_text(
        self, user: User, text: str, *, push: tuple[str, str] | None = None, kind: str = "",
        family: bool = True,
    ) -> None:
        """Уведомление-текст (замены, сводки); push — заголовок и строка для браузера.
        family=False — только самому ученику, без копии родным (свои напоминания)."""
        if push:
            await self._push_notify(user, *push, kind=kind, family=family)
        await self._notify(user, text, family=family)

    async def _notify(self, user: User, text: str, *, family: bool = True) -> None:
        """Уведомление в Telegram, если человек его оттуда не выключил.

        Служебное — ошибки входа, истекающий токен — идёт в Telegram всегда.
        """
        if _many_children(user) and user.student_name and not text.startswith("👤"):
            text = f"{formatting.student_header(user.student_name)}\n{text}"
        if user.notifications.telegram:
            await self._send(user, text)
        if family:
            await self._copy_to_followers(user, text)

    async def _copy_to_followers(self, user: User, text: str) -> None:
        """Копия родным. Их сбой не задерживает уведомление владельцу."""
        if self._family is None:
            return
        try:
            followers = await self._family.followers(user.telegram_id, notify_only=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Список родителей %s не прочитан: %s", user.telegram_id, exc)
            return
        # Без Telegram (вход по почте) копии приходят только push-ом.
        followers = [follower for follower in followers if has_telegram(follower["id"])]
        if not followers:
            return
        if not text.startswith("👤"):
            text = f"👪 <b>{formatting.esc(user.label)}</b>\n{text}"
        for follower in followers:
            try:
                for part in formatting.split_message(text):
                    await self._bot.send_message(follower["id"], part, parse_mode="HTML")
            except TelegramForbiddenError:
                logger.info("Родитель %s заблокировал бота — убираю из копий %s", follower["id"], user.telegram_id)
                await self._family.remove(user.telegram_id, follower["id"])
            except Exception as exc:  # noqa: BLE001 — копия вторична
                logger.info("Копия для %s не ушла: %s", follower["id"], exc)

    async def send_error(self, user: User, error: NetSchoolError) -> None:
        # Без человека дальше никак (пароль сменили, вход истёк, нужен код) —
        # кнопка сразу ведёт ко входу. «Продолжить так» оставляет дневник
        # с сохранёнными данными, как на сайте.
        markup = None
        if error.reason.needs_relogin or error.reason is Reason.MFA:
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔑 Войти заново", callback_data="relogin:start"),
                InlineKeyboardButton(text="Продолжить так", callback_data="relogin:keep"),
            ]])
        text = f"⚠️ {formatting.esc(error.user_message.removesuffix(' Войти заново: /login'))}"
        if error.reason is Reason.SERVER_UNAVAILABLE:
            # Watcher шлёт это, только если сервер молчит дольше часа.
            text = ("⚠️ Сервер «Сетевого города» не отвечает уже больше часа. Бот сам "
                    "подключится, когда он заработает, и пришлёт пропущенное — ничего делать не нужно.")
        if markup is None:
            await self._send(user, text)
        else:
            await self._send_part(user, text, markup=markup)

    async def _send(self, user: User, text: str) -> None:
        """Отправить, разбив длинный текст и пережив ограничение частоты."""
        for part in formatting.split_message(text):
            await self._send_part(user, part)

    async def _send_part(
        self, user: User, text: str, *, retry: bool = True, markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        if not has_telegram(user.telegram_id):
            # Вошёл по почте без Telegram: уведомления — только push в браузер.
            return
        try:
            await self._bot.send_message(
                user.telegram_id, text, parse_mode="HTML", reply_markup=markup or ReplyKeyboardRemove()
            )
        except TelegramRetryAfter as exc:
            if not retry:
                # Ошибка наверх: уведомление останется в очереди до следующего прохода.
                logger.warning("Повторное ограничение частоты для %s", user.telegram_id)
                raise
            await asyncio.sleep(exc.retry_after + 1)
            await self._send_part(user, text, retry=False, markup=markup)
        except TelegramForbiddenError:
            # Пользователь заблокировал бота: продолжать проверять его оценки
            # бессмысленно. Раньше такие пользователи оставались в цикле
            # навсегда и на каждом круге давали ошибку в логах.
            logger.info("Пользователь %s заблокировал бота — отключаем", user.telegram_id)
            from dataclasses import replace

            await self._users.save(replace(user, enabled=False))
