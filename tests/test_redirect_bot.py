from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, PhotoSize, Update

from app.bot.redirect import MAIN_BOT_URL, create_redirect_dispatcher, main


@pytest.mark.parametrize("payload", [
    {"text": "/start"},
    {"text": "/grades"},
    {"text": "Привет"},
    {"photo": [PhotoSize(file_id="photo", file_unique_id="unique", width=10, height=10)]},
])
async def test_private_messages_link_to_main_bot(payload):
    bot = Bot("123456:abcdef")
    dispatcher = create_redirect_dispatcher()
    message = Message(
        message_id=1, date=datetime.now(timezone.utc),
        chat=Chat(id=42, type="private"), **payload,
    )
    bot.session.make_request = AsyncMock(return_value=message)
    try:
        await dispatcher.feed_update(bot, Update(update_id=1, message=message))
        request = bot.session.make_request.call_args.args[1]
        assert isinstance(request, SendMessage)
        assert request.chat_id == 42
        assert "@netschoolbot" in request.text
        assert request.reply_markup.inline_keyboard[0][0].url == MAIN_BOT_URL
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


async def test_group_messages_are_ignored():
    bot = Bot("123456:abcdef")
    dispatcher = create_redirect_dispatcher()
    bot.session.make_request = AsyncMock()
    try:
        await dispatcher.feed_update(bot, Update(update_id=1, message=Message(
            message_id=1, date=datetime.now(timezone.utc),
            chat=Chat(id=-42, type="group", title="Класс"), text="Привет",
        )))
        bot.session.make_request.assert_not_called()
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


def test_missing_redirect_token_does_not_start_polling(monkeypatch, capsys):
    monkeypatch.delenv("NETSCHOOL_REDIRECT_BOT_TOKEN", raising=False)
    assert main() == 2
    assert "NETSCHOOL_REDIRECT_BOT_TOKEN не задан" in capsys.readouterr().err


def test_primary_token_cannot_be_used_for_redirect(monkeypatch, capsys):
    monkeypatch.setenv("NETSCHOOL_BOT_TOKEN", "123456:abcdef")
    monkeypatch.setenv("NETSCHOOL_REDIRECT_BOT_TOKEN", "123456:abcdef")
    assert main() == 2
    assert "должны отличаться" in capsys.readouterr().err
