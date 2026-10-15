"""Второй родитель: копии уведомлений по приглашению, без пароля от школы.

Владелец аккаунта присылает ссылку-приглашение; кто по ней перешёл и
подтвердил, получает в Telegram те же уведомления об оценках, заданиях и
сводки. В дневник он не входит и ничего не меняет — только читает
уведомления. Ссылка одноразовая и живёт сутки.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ...context import AppContext
from ...domain import formatting
from ...domain.models import User
from .. import keyboards
from .common import reply, require_school

logger = logging.getLogger("netschoolbot.bot")
router = Router(name="family")

INVITE_PREFIX = "f_"


async def _family_text(app: AppContext, user: User) -> tuple[str, InlineKeyboardMarkup]:
    followers = await app.family.followers(user.telegram_id)
    following = []
    for owner_id in await app.family.following(user.telegram_id):
        owner = await app.users.get(owner_id)
        following.append((owner_id, owner.label if owner else f"ID {owner_id}"))
    lines = [
        "👪 <b>Уведомления родным</b>",
        "",
        "Пригласите маму, папу, бабушку — они будут получать в Telegram те же уведомления "
        "об оценках, заданиях и сводки. Это только уведомления: дневник им не открывается, "
        "пароль от «Сетевого города» не нужен, менять они ничего не смогут.",
    ]
    if followers:
        lines += ["", "<b>Получают копии:</b>"]
        lines += [f"• {formatting.esc(f['name'] or 'ID ' + str(f['id']))}" for f in followers]
    if following:
        lines += ["", "<b>Вы получаете уведомления об:</b>"]
        lines += [f"• {formatting.esc(label)}" for _, label in following]
    return "\n".join(lines), keyboards.family(followers, following)


@router.message(Command("family"))
async def family_command(message: Message, user: User, app: AppContext) -> None:
    text, markup = await _family_text(app, user)
    await reply(message, text, reply_markup=markup)


@router.callback_query(F.data == "family:open")
async def family_open(callback: CallbackQuery, user: User, app: AppContext) -> None:
    await callback.answer()
    text, markup = await _family_text(app, user)
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data == "family:invite")
async def family_invite(callback: CallbackQuery, user: User, app: AppContext) -> None:
    if hint := require_school(user):
        await callback.answer()
        await reply(callback, hint)
        return
    code = await app.family.issue_invite(user.telegram_id)
    username = (await callback.bot.me()).username or ""
    await callback.answer()
    link = f"https://t.me/{username}?start={INVITE_PREFIX}{code}"
    await reply(
        callback,
        "🔗 <b>Приглашение получать уведомления</b>\n\n"
        "Перешлите эту ссылку тому, кого приглашаете. Она одноразовая и действует сутки:\n"
        f"{link}\n\n"
        "Убрать доступ можно в любой момент: /family.",
    )


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(INVITE_PREFIX)))
async def start_follow(message: Message, command: CommandObject, user: User, app: AppContext) -> None:
    code = (command.args or "")[len(INVITE_PREFIX):]
    owner_id = await app.family.invite_owner(code)
    if owner_id is None:
        await reply(message, "⚠️ Приглашение недействительно: истекло или уже использовано. "
                             "Попросите прислать новое.")
        return
    if owner_id == user.telegram_id:
        await reply(message, "Это ваше собственное приглашение — перешлите его тому, кого приглашаете.")
        return
    owner = await app.users.get(owner_id)
    label = owner.label if owner else "ученик"
    school = f" ({formatting.esc(owner.school.name)})" if owner and owner.school.name else ""
    await reply(
        message,
        f"👪 Вас приглашают получать уведомления об ученике <b>{formatting.esc(label)}</b>{school}: "
        "новые оценки, задания и сводки.\n\nПодтвердить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Получать", callback_data=f"family:accept:{code}"),
            InlineKeyboardButton(text="Отмена", callback_data="confirm:cancel"),
        ]]),
    )


@router.callback_query(F.data.startswith("family:accept:"))
async def family_accept(callback: CallbackQuery, user: User, app: AppContext) -> None:
    code = callback.data.split(":", 2)[2]
    name = callback.from_user.full_name if callback.from_user else ""
    owner_id = await app.family.accept(code, user.telegram_id, name)
    if owner_id is None:
        await callback.answer("Приглашение недействительно", show_alert=True)
        return
    await callback.answer("Готово")
    owner = await app.users.get(owner_id)
    if callback.message:
        await callback.message.edit_text(
            f"✅ Теперь вы получаете уведомления об ученике <b>{formatting.esc(owner.label if owner else '')}</b>.\n"
            "Отказаться — /family.",
            parse_mode="HTML",
        )
    try:
        await callback.bot.send_message(
            owner_id, f"👪 {formatting.esc(name or 'Приглашённый')} теперь получает копии уведомлений. "
                      "Управлять — /family.", parse_mode="HTML",
        )
    except Exception as exc:  # noqa: BLE001 — сообщение владельцу необязательно
        logger.info("Владельцу %s не сообщили о втором родителе: %s", owner_id, exc)


@router.callback_query(F.data.startswith("family:drop:") | F.data.startswith("family:leave:"))
async def family_remove(callback: CallbackQuery, user: User, app: AppContext) -> None:
    _, action, raw = callback.data.split(":", 2)
    try:
        other = int(raw)
    except ValueError:
        await callback.answer("Не понял")
        return
    if action == "drop":
        removed = await app.family.remove(user.telegram_id, other)
    else:
        removed = await app.family.remove(other, user.telegram_id)
    await callback.answer("Готово" if removed else "Уже убрано")
    text, markup = await _family_text(app, user)
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
