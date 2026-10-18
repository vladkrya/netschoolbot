"""Группы и каналы для сообщений о «Сетевом городе» — настраивает только админ.

/addgroup <id> в личном чате подключает группу, /groups —
список. В карточке группы: о чём писать (простои, обновления — одно или
оба) и по каким регионам. Сама рассылка — status/groups.py.
"""

from __future__ import annotations

import contextlib
import html
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ...context import AppContext
from ...db.repositories import BroadcastGroupRepository
from ...status.groups import KINDS
from .common import reply

router = Router(name="groups")

PAGE = 8
HINT = ("Чтобы подключить группу или канал: добавьте туда бота (в канал — админом), "
        "затем пришлите в личный чат боту <code>/addgroup -100…</code> с id группы.")


def _admin(from_user, app: AppContext) -> bool:
    admin = app.settings.telegram.admin_id
    return bool(admin) and from_user is not None and from_user.id == admin


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _regions(app: AppContext) -> list[tuple[str, str]]:
    """(хост, название) всех серверов со страницы статуса — по названию."""
    return [(t.host, t.region or t.host) for t in app.status.targets()]


def _region_names(app: AppContext, hosts: list[str]) -> str:
    if not hosts:
        return "все"
    names = dict(_regions(app))
    shown = [html.escape(names.get(host, host)) for host in hosts]
    return ", ".join(shown) if len(shown) <= 10 else ", ".join(shown[:10]) + f" и ещё {len(shown) - 10}"


def _title(group: dict) -> str:
    return html.escape(group["title"] or str(group["chat_id"]))


def _card(app: AppContext, group: dict) -> tuple[str, InlineKeyboardMarkup]:
    chat = group["chat_id"]
    kinds = [name.lower() for kind, name in KINDS.items() if group[kind]]
    text = (f"📣 <b>{_title(group)}</b>\n"
            f"id: <code>{chat}</code>\n"
            f"Сообщает: {', '.join(kinds) if kinds else 'ничего — всё выключено'}\n"
            f"Регионы: {_region_names(app, group['regions'])}")
    rows = [[_button(("✅ " if group[kind] else "▫️ ") + name, f"grp:k:{chat}:{kind}")]
            for kind, name in KINDS.items()]
    rows.append([_button(f"🗺 Регионы: {len(group['regions']) or 'все'}", f"grp:reg:{chat}:0")])
    rows.append([_button("🧪 Пробное сообщение", f"grp:test:{chat}"), _button("🗑 Отключить", f"grp:del:{chat}")])
    rows.append([_button("← Все группы", "grp:list")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _picker(app: AppContext, group: dict, page: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = group["chat_id"]
    regions = _regions(app)
    pages = max(1, -(-len(regions) // PAGE))
    page = min(max(page, 0), pages - 1)
    chosen = set(group["regions"])
    rows = [[_button(("✅ " if host in chosen else "▫️ ") + name, f"grp:rt:{chat}:{page}:{host}")]
            for host, name in regions[page * PAGE:(page + 1) * PAGE]]
    nav = []
    if page > 0:
        nav.append(_button("◀️", f"grp:reg:{chat}:{page - 1}"))
    nav.append(_button(f"{page + 1} / {pages}", f"grp:reg:{chat}:{page}"))
    if page < pages - 1:
        nav.append(_button("▶️", f"grp:reg:{chat}:{page + 1}"))
    rows.append(nav)
    rows.append([_button(("✅ " if not chosen else "") + "Все регионы", f"grp:rall:{chat}:{page}"),
                 _button("Готово", f"grp:open:{chat}")])
    text = (f"🗺 <b>{_title(group)}</b>: о каких регионах писать\n"
            f"Выбрано: {_region_names(app, group['regions'])}\n"
            "Ничего не выбрано — пишем обо всех.")
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _list(groups: list[dict]) -> tuple[str, InlineKeyboardMarkup | None]:
    if not groups:
        return "📣 <b>Группы для сообщений о «Сетевом городе»</b>\nПока ни одной.\n\n" + HINT, None
    lines = ["📣 <b>Группы для сообщений о «Сетевом городе»</b>"]
    rows = []
    for group in groups:
        kinds = " + ".join(name.lower() for kind, name in KINDS.items() if group[kind]) or "выключено"
        regions = len(group["regions"]) or "все"
        lines.append(f"• {_title(group)} — {kinds}; регионы: {regions}")
        rows.append([_button(group["title"] or str(group["chat_id"]), f"grp:open:{group['chat_id']}")])
    lines += ["", HINT]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("groups"))
async def groups_list(message: Message, app: AppContext) -> None:
    if not _admin(message.from_user, app) or message.chat.type != "private":
        return
    text, markup = _list(await BroadcastGroupRepository(app.db).all())
    await reply(message, text, reply_markup=markup)


@router.message(Command("addgroup"))
async def add_group(message: Message, app: AppContext, command: CommandObject, bot) -> None:
    if not _admin(message.from_user, app):
        return
    admin = app.settings.telegram.admin_id
    in_group = message.chat.type != "private"
    raw = (command.args or "").strip()
    if not raw and not in_group:
        await reply(message, HINT)
        return
    try:
        chat_id = int(raw) if raw else message.chat.id
    except ValueError:
        await reply(message, "Id группы — число, например <code>-1001234567890</code>.\n\n" + HINT)
        return
    try:
        chat = await bot.get_chat(chat_id)
    except Exception as exc:  # noqa: BLE001 — нет такой группы или бота в ней
        await reply(message, f"Не вижу чат <code>{chat_id}</code>: {html.escape(str(exc))}\n\n"
                             "Проверьте id и что бот добавлен в группу.")
        return
    if chat.type == "private":
        await reply(message, "Это личный чат, а нужна группа или канал.")
        return
    repo = BroadcastGroupRepository(app.db)
    added = await repo.add(chat.id, chat.title or "", time.time())
    text, markup = _card(app, await repo.get(chat.id))
    text = ("✅ Группа подключена: простои и обновления, все регионы. Поменять — кнопками ниже.\n\n"
            if added else "Эта группа уже подключена.\n\n") + text
    if in_group:
        # Настройки — в личку админу, а не на виду у всей группы.
        await bot.send_message(admin, text, reply_markup=markup)
        await message.answer("✅ Группа подключена к сообщениям о «Сетевом городе».")
    else:
        await reply(message, text, reply_markup=markup)


@router.callback_query(F.data.startswith("grp:"))
async def group_button(callback: CallbackQuery, app: AppContext, bot) -> None:
    if not _admin(callback.from_user, app):
        await callback.answer()
        return
    parts = callback.data.split(":", 4)
    action = parts[1]
    repo = BroadcastGroupRepository(app.db)
    if action == "list":
        text, markup = _list(await repo.all())
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer()
        return
    chat_id = int(parts[2])
    group = await repo.get(chat_id)
    if group is None:
        await callback.answer("Группа уже отключена", show_alert=True)
        return
    notice = ""
    view = "card"
    page = 0
    if action == "k":
        await repo.set_kind(chat_id, parts[3], not group[parts[3]])
    elif action == "reg":
        view, page = "picker", int(parts[3])
    elif action == "rt":
        view, page = "picker", int(parts[3])
        hosts = set(group["regions"])
        hosts ^= {parts[4]}
        await repo.set_regions(chat_id, list(hosts))
    elif action == "rall":
        view, page = "picker", int(parts[3])
        await repo.set_regions(chat_id, [])
    elif action == "test":
        try:
            await bot.send_message(chat_id, "«Сетевой город»\n✅ Проверка: сюда будут приходить сообщения "
                                            "о простоях и обновлениях.")
            notice = "Отправлено"
        except Exception as exc:  # noqa: BLE001 — показать админу, почему
            notice = f"Не отправилось: {exc}"[:190]
    elif action == "del":
        await callback.message.edit_text(
            f"Отключить «{_title(group)}» от сообщений о «Сетевом городе»?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                _button("Да, отключить", f"grp:delok:{chat_id}"), _button("Нет", f"grp:open:{chat_id}"),
            ]]))
        await callback.answer()
        return
    elif action == "delok":
        await repo.remove(chat_id)
        text, markup = _list(await repo.all())
        await callback.message.edit_text(text, reply_markup=markup)
        await callback.answer("Отключено")
        return
    group = await repo.get(chat_id)
    text, markup = _picker(app, group, page) if view == "picker" else _card(app, group)
    # «message is not modified» при повторном нажатии — не ошибка.
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer(notice, show_alert=notice.startswith("Не отправилось"))


@router.my_chat_member(F.chat.type.in_({"group", "supergroup", "channel"}))
async def added_to_group(event: ChatMemberUpdated, app: AppContext, bot) -> None:
    """Бота добавили в группу — подсказать админу её id."""
    admin = app.settings.telegram.admin_id
    joined = event.new_chat_member.status in ("member", "administrator")
    was_out = event.old_chat_member.status in ("left", "kicked")
    if not admin or not (joined and was_out):
        return
    chat = event.chat
    known = await BroadcastGroupRepository(app.db).get(chat.id) is not None
    text = (f"Меня добавили в «{html.escape(chat.title or '')}», id <code>{chat.id}</code>."
            + ("" if known else f"\nПодключить к сообщениям о «Сетевом городе»: <code>/addgroup {chat.id}</code>"))
    await bot.send_message(admin, text)
