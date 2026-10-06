"""Доставка уведомлений в Telegram.

Реализация протокола `Notifier` из watcher.py. Вынесена отдельно, чтобы цикл
проверки оценок можно было тестировать без Telegram — в старом коде отправка
была вшита в тот же метод, что и вся логика.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import ReplyKeyboardRemove

from ..db.repositories import UserRepository
from ..domain import formatting
from ..domain.models import User
from ..domain.records import AbsenceRecord, HomeworkRecord, MarkEvent
from ..netschool.errors import NetSchoolError

logger = logging.getLogger("netschoolbot.notifier")


def _events_preview(events: list[MarkEvent]) -> str:
    """Короткая строка для всплывающего уведомления в браузере."""
    parts = [f"{e.record.subject}: {e.record.mark}" for e in events[:3]]
    if len(events) > 3:
        parts.append(f"и ещё {len(events) - 3}")
    return ", ".join(parts)

# Больше этого числа событий — присылаем одну сводку вместо потока сообщений.
DIGEST_THRESHOLD = 5
BETWEEN_MESSAGES = 2.0


class TelegramNotifier:
    def __init__(self, bot: Bot, users: UserRepository, push=None) -> None:
        self._bot = bot
        self._users = users
        # Push в браузер — дополнение к Telegram, а не замена: приложение
        # может быть не установлено, а подписка — отозвана без уведомления.
        self._push = push

    async def send_mark_events(self, user: User, events: list[MarkEvent]) -> None:
        if not events:
            return
        if len(events) > DIGEST_THRESHOLD:
            await self._notify(user, formatting.mark_events_digest(events))
        else:
            for index, event in enumerate(events):
                if index and user.notifications.telegram:
                    await asyncio.sleep(BETWEEN_MESSAGES)
                await self._notify(user, formatting.mark_event(event))
        await self._push_notify(user, "Новые оценки", _events_preview(events))

    async def send_homework(self, user: User, items: list[HomeworkRecord]) -> None:
        if not items:
            return
        await self._notify(user, formatting.homework_digest(items, tz=user.tz))
        await self._push_notify(
            user,
            "Новые задания",
            ", ".join(sorted({item.subject for item in items})[:4]),
        )

    async def send_absences(self, user: User, items: list[AbsenceRecord]) -> None:
        if not items:
            return
        await self._notify(user, formatting.absences_digest(items))
        await self._push_notify(
            user,
            "Отметки посещаемости",
            ", ".join(sorted({item.subject for item in items})[:4]),
        )

    async def send_token_expiring(self, user: User, expires_at: dt.datetime) -> None:
        await self._send(user, formatting.token_expiring(expires_at, tz=user.tz))

    async def _push_notify(self, user: User, title: str, body: str) -> None:
        """Отправить push, не давая его сбою повлиять на Telegram."""
        if self._push is None:
            return
        try:
            await self._push.send(user.telegram_id, title, body)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Push для %s не ушёл: %s", user.telegram_id, exc)

    async def send_text(self, user: User, text: str, *, push: tuple[str, str] | None = None) -> None:
        """Уведомление-текст (замены, сводки); push — заголовок и строка для браузера."""
        await self._notify(user, text)
        if push:
            await self._push_notify(user, *push)

    async def _notify(self, user: User, text: str) -> None:
        """Уведомление в Telegram, если человек его оттуда не выключил.

        Служебное — ошибки входа, истекающий токен — идёт в Telegram всегда.
        """
        if user.notifications.telegram:
            await self._send(user, text)

    async def send_error(self, user: User, error: NetSchoolError) -> None:
        await self._send(user, f"⚠️ {formatting.esc(error.user_message)}")

    async def _send(self, user: User, text: str) -> None:
        """Отправить, разбив длинный текст и пережив ограничение частоты."""
        for part in formatting.split_message(text):
            await self._send_part(user, part)

    async def _send_part(self, user: User, text: str, *, retry: bool = True) -> None:
        try:
            await self._bot.send_message(
                user.telegram_id, text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
            )
        except TelegramRetryAfter as exc:
            if not retry:
                # Ошибка наверх: уведомление останется в очереди до следующего прохода.
                logger.warning("Повторное ограничение частоты для %s", user.telegram_id)
                raise
            await asyncio.sleep(exc.retry_after + 1)
            await self._send_part(user, text, retry=False)
        except TelegramForbiddenError:
            # Пользователь заблокировал бота: продолжать проверять его оценки
            # бессмысленно. Раньше такие пользователи оставались в цикле
            # навсегда и на каждом круге давали ошибку в логах.
            logger.info("Пользователь %s заблокировал бота — отключаем", user.telegram_id)
            from dataclasses import replace

            await self._users.save(replace(user, enabled=False))
