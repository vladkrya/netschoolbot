"""Привязка Telegram к аккаунту с сайта: /link КОД или ссылка из приложения."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from ...accounts.service import link_telegram
from ...context import AppContext
from .common import reply

router = Router(name="account")


@router.message(CommandStart(deep_link=True, magic=F.args.startswith("link_")))
async def start_link(message: Message, command: CommandObject, app: AppContext) -> None:
    await reply(message, await link_telegram(app, (command.args or "")[5:], message.from_user.id))


@router.message(Command("link"))
async def link(message: Message, command: CommandObject, app: AppContext) -> None:
    code = (command.args or "").strip()
    if not code:
        await reply(message, "Пришлите код из приложения: <code>/link КОД</code>\n"
                             "Код — в Настройках, раздел «Вход по почте».")
        return
    await reply(message, await link_telegram(app, code, message.from_user.id))
