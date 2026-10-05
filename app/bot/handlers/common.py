"""Общее для обработчиков: доступ к контексту и разбор ошибок."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from ...context import AppContext
from ...domain import formatting
from ...domain.models import User
from ...netschool.errors import NetSchoolError

logger = logging.getLogger("netschoolbot.bot")


class ContextMiddleware(BaseMiddleware):
    """Кладёт контекст приложения и пользователя в данные обработчика.

    Раньше обработчики тянули состояние из глобальных переменных модуля
    `runtime`, поэтому зависели от порядка импортов и не тестировались.
    """

    def __init__(self, context: AppContext) -> None:
        self._context = context

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["app"] = self._context
        tg_user = data.get("event_from_user")
        if tg_user is not None:
            data["user"] = await self._context.users.get_or_create(
                tg_user.id, display_name=tg_user.full_name or ""
            )
        return await handler(event, data)


def _message_of(target: Message | CallbackQuery) -> Message | None:
    """Сообщение, в которое нужно отвечать.

    У CallbackQuery ответ уходит в сообщение с кнопкой; у Message — в него
    самого. Проверяется наличие поля, а не класс: обработчики принимают оба
    типа, и привязка к конкретному классу мешала бы их тестировать.
    """
    return getattr(target, "message", target)


async def reply(target: Message | CallbackQuery, text: str, **kwargs: Any) -> None:
    """Ответить, разбив длинный текст на части."""
    message = _message_of(target)
    if message is None:
        return
    parts = formatting.split_message(text)
    for index, part in enumerate(parts):
        # Клавиатура прикрепляется только к последней части, иначе она
        # продублируется в каждом куске длинного ответа.
        extra = kwargs if index == len(parts) - 1 else {}
        await message.answer(part, parse_mode="HTML", **extra)


async def report_error(target: Message | CallbackQuery, error: Exception) -> None:
    """Показать человеку внятное сообщение вместо traceback."""
    if isinstance(error, NetSchoolError):
        await reply(target, f"⚠️ {formatting.esc(error.user_message)}")
        return
    logger.exception("Необработанная ошибка в обработчике")
    await reply(target, "⚠️ Что-то пошло не так. Ошибка записана, я разберусь.")


def require_school(user: User) -> str | None:
    """Текст-подсказка, если пользователь ещё не выбрал школу."""
    if user.school.configured:
        return None
    return (
        "Вы не вошли в «Сетевой город».\n\n"
        "Войти — /login"
    )
