"""Запасной бот со ссылкой на основной: python -m app.bot.redirect."""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import replace

from aiogram import Dispatcher, F
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Message,
)

from ..settings import Settings, SettingsError, load_settings
from .app import create_bot

MAIN_BOT_URL = "https://t.me/netschoolbot"
REDIRECT_TEXT = (
    "Это запасной бот «Сетевого города».\n\n"
    "СГОшка, оценки и уведомления доступны в основном боте @netschoolbot.\n"
    "Нажмите кнопку ниже, чтобы перейти к нему."
)


async def redirect_message(message: Message) -> None:
    await message.answer(
        REDIRECT_TEXT,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть основной бот", url=MAIN_BOT_URL),
        ]]),
    )


def create_redirect_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.message.register(redirect_message, F.chat.type == "private")
    return dispatcher


async def run(settings: Settings) -> None:
    # Отдельная сессия и dispatcher; основной токен и база здесь не используются.
    bot = create_bot(replace(settings, telegram=replace(
        settings.telegram, bot_token=settings.telegram.redirect_bot_token,
    )))
    dispatcher = create_redirect_dispatcher()
    try:
        me = await bot.get_me()
        if not me.username or me.username.lower() != "netschool13_bot":
            raise SettingsError("NETSCHOOL_REDIRECT_BOT_TOKEN должен принадлежать @Netschool13_bot")
        await bot.set_my_commands([BotCommand(command="start", description="Открыть основной бот")])
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        await bot.delete_webhook(drop_pending_updates=True)
        logging.getLogger("netschoolbot.redirect").info("Запасной бот @%s запущен", me.username)
        await dispatcher.start_polling(bot, allowed_updates=["message"], close_bot_session=False)
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings(require_bot_token=False)
        if not settings.telegram.redirect_bot_token:
            raise SettingsError("NETSCHOOL_REDIRECT_BOT_TOKEN не задан")
        if settings.telegram.redirect_bot_token == settings.telegram.bot_token:
            raise SettingsError("Токены основного и запасного ботов должны отличаться")
        asyncio.run(run(settings))
    except SettingsError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
