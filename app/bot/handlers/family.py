"""Родные: доступ по приглашению, без пароля от школы.

Ученик выбирает, что откроет приглашение: копии уведомлений, просмотр
дневника в приложении (только чтение, app/web/viewing.py) или и то и другое.
Ссылка ведёт на сайт — там можно войти через Telegram или по почте — или
прямо в бота. Ссылка одноразовая и живёт сутки.
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
RIGHTS = {"both": (True, True), "notify": (True, False), "view": (False, True)}


def rights_text(notify: bool, view: bool) -> str:
    if notify and view:
        return "уведомления и дневник"
    return "уведомления" if notify else "просмотр дневника"


async def _family_text(app: AppContext, user: User) -> tuple[str, InlineKeyboardMarkup]:
    followers = await app.family.followers(user.telegram_id)
    following = []
    for owner_id in await app.family.following(user.telegram_id):
        owner = await app.users.get(owner_id)
        following.append((owner_id, owner.label if owner else f"ID {owner_id}"))
    lines = [
        "👪 <b>Родные</b>",
        "",
        "Пригласите маму, папу, бабушку — вы выбираете, что им будет доступно: "
        "уведомления об оценках и заданиях, просмотр дневника или и то и другое. "
        "Пароль от «Сетевого города» им не нужен, менять что-либо они не смогут.",
    ]
    if followers:
        lines += ["", "<b>Приглашённые:</b>"]
        lines += [f"• {formatting.esc(f['name'] or 'ID ' + str(f['id']))} — {rights_text(f['notify'], f['view'])}"
                  for f in followers]
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
    await callback.answer()
    if hint := require_school(user):
        await reply(callback, hint)
        return
    if callback.message:
        await callback.message.edit_text(
            "👪 <b>Что будет доступно приглашённому?</b>\n\n"
            "Просмотр — дневник в приложении: оценки, расписание, задания. Только чтение.",
            parse_mode="HTML", reply_markup=keyboards.family_rights(),
        )


@router.callback_query(F.data.startswith("family:invite:"))
async def family_invite_link(callback: CallbackQuery, user: User, app: AppContext) -> None:
    rights = RIGHTS.get(callback.data.rsplit(":", 1)[1])
    if rights is None:
        await callback.answer("Не понял")
        return
    if hint := require_school(user):
        await callback.answer()
        await reply(callback, hint)
        return
    code = await app.family.issue_invite(user.telegram_id, notify=rights[0], view=rights[1])
    username = (await callback.bot.me()).username or ""
    await callback.answer()
    site = f"{app.settings.web.miniapp_url}/?invite={code}"
    await reply(
        callback,
        "🔗 <b>Приглашение: " + rights_text(*rights) + "</b>\n\n"
        "Перешлите эту ссылку тому, кого приглашаете. По ней можно войти через Telegram "
        "или зарегистрироваться по почте. Ссылка одноразовая и действует сутки:\n"
        f"{site}\n\n"
        + (f"Если у него есть Telegram, можно сразу в бота:\nhttps://t.me/{username}?start={INVITE_PREFIX}{code}\n\n"
           if username else "")
        + "Изменить доступ или убрать — /family.",
    )


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(INVITE_PREFIX)))
async def start_follow(message: Message, command: CommandObject, user: User, app: AppContext) -> None:
    code = (command.args or "")[len(INVITE_PREFIX):]
    invite = await app.family.invite(code)
    owner_id = invite["owner_id"] if invite else None
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
        f"👪 Вас приглашает ученик <b>{formatting.esc(label)}</b>{school}. Будет доступно: "
        f"{rights_text(invite['notify'], invite['view'])}.\n\nПодтвердить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Получать", callback_data=f"family:accept:{code}"),
            InlineKeyboardButton(text="Отмена", callback_data="confirm:cancel"),
        ]]),
    )


@router.callback_query(F.data.startswith("family:accept:"))
async def family_accept(callback: CallbackQuery, user: User, app: AppContext) -> None:
    code = callback.data.split(":", 2)[2]
    name = callback.from_user.full_name if callback.from_user else ""
    invite = await app.family.invite(code)
    owner_id = await app.family.accept(code, user.telegram_id, name)
    if owner_id is None or invite is None:
        await callback.answer("Приглашение недействительно", show_alert=True)
        return
    if invite["view"]:
        await app.family.set_viewing(user.telegram_id, owner_id)
    await callback.answer("Готово")
    owner = await app.users.get(owner_id)
    label = formatting.esc(owner.label if owner else "")
    lines = [f"✅ Готово: ученик <b>{label}</b> — {rights_text(invite['notify'], invite['view'])}."]
    if invite["notify"]:
        lines.append("Уведомления будут приходить сюда.")
    if invite["view"]:
        lines.append("СГОшка откроется в приложении: /app.")
    lines.append("Отказаться — /family.")
    if callback.message:
        await callback.message.edit_text("\n".join(lines), parse_mode="HTML")
    try:
        await callback.bot.send_message(
            owner_id, f"👪 {formatting.esc(name or 'Приглашённый')} принял приглашение: "
                      f"{rights_text(invite['notify'], invite['view'])}. Управлять — /family.", parse_mode="HTML",
        )
    except Exception as exc:  # noqa: BLE001 — сообщение владельцу необязательно
        logger.info("Владельцу %s не сообщили о приглашённом: %s", owner_id, exc)


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
