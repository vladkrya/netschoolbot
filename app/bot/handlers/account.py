"""Аккаунт на сайте: привязка Telegram (/link КОД) и вход на сайт через бота."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ...accounts.service import link_telegram
from ...accounts.site_login import PREFIX as SITE_LOGIN_PREFIX
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


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(SITE_LOGIN_PREFIX)))
async def start_site_login(message: Message, command: CommandObject, app: AppContext) -> None:
    """Кнопка «Войти через Telegram-бота» на сайте: спросить подтверждение."""
    code = (command.args or "")[len(SITE_LOGIN_PREFIX):]
    item = app.site_logins.get(code)
    if item is None or item.telegram_id is not None or item.declined:
        await reply(message, "Ссылка для входа устарела. Нажмите «Войти через Telegram-бота» на сайте ещё раз.")
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Войти", callback_data=f"weblogin:ok:{code}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"weblogin:no:{code}"),
    ]])
    await reply(
        message,
        f"🔐 <b>Вход на сайт дневника</b>\n\nЗапрос из: {html.escape(item.browser)}\n\n"
        "Подтвердите, только если это вы сейчас входите на сайт. "
        "Если ссылку прислал кто-то другой — нажмите «Отмена».",
        reply_markup=markup,
    )


@router.callback_query(F.data.startswith("weblogin:"))
async def site_login_decision(callback: CallbackQuery, app: AppContext) -> None:
    _, action, code = (callback.data or "").split(":", 2)
    approve = action == "ok"
    if approve:
        await app.users.get_or_create(callback.from_user.id, display_name=callback.from_user.full_name or "")
    item = app.site_logins.decide(code, callback.from_user.id, approve)
    if item is None:
        text = "Ссылка для входа устарела. Нажмите «Войти через Telegram-бота» на сайте ещё раз."
    elif approve:
        text = "✅ Готово — вернитесь на сайт, он откроет дневник сам."
    else:
        text = "Вход отменён."
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(text)
