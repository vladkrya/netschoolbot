"""Общая подсказка о входе должна появляться только в личном чате."""

import datetime as dt
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, PhotoSize, User

from app.bot.handlers import menu


@pytest.mark.parametrize("chat_type", ["private", "group", "supergroup"])
@pytest.mark.parametrize("payload", [
    {"text": "Привет"},
    {"photo": [PhotoSize(file_id="photo", file_unique_id="unique", width=10, height=10)]},
    {"new_chat_members": [User(id=123456, is_bot=True, first_name="Бот")]},
])
async def test_login_hint_only_in_private_chat(chat_type, payload, users):
    bot = Bot("123456:abcdef")
    message = Message(
        message_id=1, date=dt.datetime.now(dt.timezone.utc),
        chat=Chat(id=42 if chat_type == "private" else -42, type=chat_type),
        **payload,
    ).as_(bot)
    bot.session.make_request = AsyncMock(return_value=message)
    try:
        await menu.router.propagate_event(
            "message", message, bot=bot, user=await users.get_or_create(42), raw_state=None,
        )
        if chat_type == "private":
            bot.session.make_request.assert_awaited_once()
            request = bot.session.make_request.call_args.args[1]
            assert "Вы не вошли" in request.text
            assert "/login" in request.text
        else:
            bot.session.make_request.assert_not_awaited()
    finally:
        await bot.session.close()
