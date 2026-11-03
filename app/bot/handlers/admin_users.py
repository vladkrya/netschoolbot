"""Поиск и управление пользователями — только TG_ADMIN_ID, в личном чате."""
from __future__ import annotations

import html
import logging
from dataclasses import replace

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ...accounts import has_telegram
from ...context import AppContext
from .admin import _is_admin, lookup_account, refresh_identity
from .common import reply

router = Router(name="admin_users")
logger = logging.getLogger("netschoolbot.admin_users")
NOTIFICATIONS = {"grades": "Оценки", "homework": "Д/З", "mail": "Почта", "telegram": "Telegram"}
INTERVALS = (180, 300, 600, 1800)


async def user_card(app: AppContext, uid: int, *, identity=None):
    row = await lookup_account(app, f"tg:{uid}")
    user = await app.users.get(uid)
    if row is None or user is None:
        return "Пользователь не найден.", None
    name, username = identity or (row["display_name"], row["telegram_username"])
    from ...access import controls
    access = await controls(app, uid)
    status = await app.state.watch_status(uid)
    lines = [f"👤 <b>Аккаунт {row['account_no']}</b>", html.escape(name or user.label)]
    if username:
        lines.append(f"@{html.escape(username)}")
    lines.append(f"{'Telegram ID' if has_telegram(uid) else 'ID аккаунта'}: <code>{uid}</code>")
    if row["email"]:
        lines.append(f"Почта: {html.escape(row['email'])}")
    lines += ["", f"Доступ: {'заблокирован' if access['blocked'] else 'разрешён'}", f"Школа: {html.escape(user.school.name or 'не выбрана')}",
              f"Ученик: {html.escape(user.student_name or 'не выбран')}",
              f"API: {html.escape(user.effective_api_mode.title)}",
              f"Проверка: {'включена' if user.enabled else 'на паузе'} · каждые {user.check_interval // 60} мин",
              f"Фоновая задача: {'работает' if uid in app.watchers.running else 'остановлена'}"]
    if status.last_ok_at:
        lines.append(f"Последняя успешная: {status.last_ok_at.isoformat(timespec='minutes')}")
    if status.last_error:
        lines.append(f"Ошибка: {html.escape(status.last_error[:350])}")
    prefix = f"adminuser:{uid}:"
    def button(label, action):
        return InlineKeyboardButton(text=label, callback_data=prefix + action)
    rows = [[button("⏸ Приостановить" if user.enabled else "▶ Возобновить", "pause" if user.enabled else "resume"),
             button("↻ Перезапустить", "restart")]]
    rows.append([button(f"{'✓ ' if user.check_interval == n else ''}{n // 60} мин", f"interval-{n}") for n in INTERVALS])
    for field, label in NOTIFICATIONS.items():
        on = getattr(user.notifications, field)
        rows.append([button(f"{'✓' if on else '—'} {label}: {'вкл.' if on else 'выкл.'}", f"{field}-{'off' if on else 'on'}")])
    rows += [[button("🔐 Доступы", "rights"), button("🎨 Оформление", "appearance")],
             [button("🛡 Безопасность", "security")], [button("Обновить карточку", "refresh")]]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("user", "userid"))
async def admin_user(message: Message, command: CommandObject, bot: Bot, app: AppContext) -> None:
    if not _is_admin(message, app):
        return
    raw = (command.args or "").strip()
    try:
        row = await lookup_account(app, raw)
    except ValueError:
        await reply(message, "<b>Управление пользователем</b>\n"
                    "<code>/user 123456789</code> — Telegram ID\n"
                    "<code>/userid @username</code> — по нику\n"
                    "<code>/user #10006</code> — номер аккаунта\n"
                    "Карточка: проверка, уведомления, доступы, оформление и безопасность.")
        return
    if row is None:
        await reply(message, "Пользователь не найден. По @username ищу среди пользователей приложения; "
                    "ник сохраняется при обращении к боту или входе через Telegram.")
        return
    identity = await refresh_identity(app, bot, row)
    if raw.startswith("@") and identity[1].lower() != raw[1:].lower():
        await reply(message, "Username изменился. Укажите новый ник или Telegram ID.")
        return
    text, keyboard = await user_card(app, row["telegram_id"], identity=identity)
    await reply(message, text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("adminuser:"))
async def manage_user(callback: CallbackQuery, app: AppContext) -> None:
    if not _is_admin(callback, app):
        await callback.answer("Только для администратора", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[1].isascii() or not parts[1].isdigit() or not 0 < int(parts[1]) <= 2**63 - 1:
        await callback.answer("Некорректная команда", show_alert=True)
        return
    uid, action = int(parts[1]), parts[2]
    user = await app.users.get(uid)
    if user is None:
        await callback.answer("Пользователь уже удалён", show_alert=True)
        return
    if await advanced_action(callback, app, uid, action):
        return
    from ...access import blocked
    if action in ("resume", "restart") and await blocked(app, uid):
        await callback.answer("Сначала разблокируйте доступ", show_alert=True)
        return
    # Только заданные значения, а не имена произвольных полей пользователя.
    if action in ("resume", "restart") and not user.ready_to_check and not replace(user, enabled=True).ready_to_check:
        await callback.answer("У пользователя нет входа в дневник или выбранного ученика", show_alert=True)
        return
    if action == "restart" and not user.enabled:
        await callback.answer("Проверка на паузе. Сначала возобновите её.", show_alert=True)
        return
    valid = action in ("resume", "pause", "restart", "refresh")
    if action.startswith("interval-"):
        raw = action.removeprefix("interval-")
        valid = raw.isdigit() and int(raw) in INTERVALS
        if valid:
            user = await app.users.save(replace(user, check_interval=int(raw)))
    elif action in ("resume", "pause"):
        user = await app.users.save(replace(user, enabled=action == "resume"))
    elif "-" in action:
        field, value = action.rsplit("-", 1)
        valid = field in NOTIFICATIONS and value in ("on", "off")
        if valid:
            user = await app.users.save(replace(user, notifications=replace(user.notifications, **{field: value == "on"})))
    if not valid:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    if action in ("resume", "pause", "restart") or action.startswith("interval-"):
        if user.ready_to_check:
            await app.watchers.start(uid)
        else:
            await app.watchers.stop(uid)
    if action != "refresh":
        logger.info("Администратор %s: %s для пользователя %s", callback.from_user.id, action, uid)
    await callback.answer("Обновлено" if action == "refresh" else "Готово")
    if callback.message:
        text, keyboard = await user_card(app, uid)
        if callback.message.text != text or callback.message.reply_markup != keyboard:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)


# Подтверждения одноразовые, привязаны к владельцу, пользователю и действию.
# Перезапуск процесса отменяет неисполненные подтверждения.
CONFIRMATIONS = {}


async def advanced_panel(app, uid, panel):
    from ...access import controls, FEATURES, MANAGED_PREFS
    data = await controls(app, uid)
    rows = []
    def button(text, action):
        return InlineKeyboardButton(text=text, callback_data=f"adminuser:{uid}:{action}")
    if panel == "rights":
        lines = [f"🔐 <b>Доступы пользователя {uid}</b>", "По умолчанию — общие правила приложения."]
        for key, label in FEATURES.items():
            value = data["grants"].get(key)
            state = "разрешено" if value is True else "запрещено" if value is False else "по умолчанию"
            lines.append(f"{label}: <b>{state}</b>")
            rows.append([button(f"{label}: вкл.", f"grant-{key}-allow"), button("Выкл.", f"grant-{key}-deny"), button("Авто", f"grant-{key}-default")])
        lines.append("Помощник и решения требуют настроенного ИИ-сервиса. Полномочия администратора не выдаются.")
    elif panel == "appearance":
        lines = [f"🎨 <b>Оформление пользователя {uid}</b>", "Заданную здесь настройку пользователь изменить не сможет. «Свободно» снимает ограничение."]
        labels = {"unity": "День единства", "halloween": "Хеллоуин", "theme": "Светлая / тёмная", "design": "Дизайн", "motion": "Анимации", "confetti": "Конфетти"}
        names = {"on": "Вкл.", "off": "Выкл.", "light": "Светлая", "dark": "Тёмная", "auto": "Системная", "new": "Новый", "classic": "Старый", "show": "Вкл.", "hide": "Выкл."}
        for key, values in MANAGED_PREFS.items():
            forced = data["forced_prefs"].get(key)
            lines.append(f"{labels[key]}: <b>{names.get(forced, 'свободно')}</b>")
            rows.append([button(f"{labels[key] + ': ' if value == values[0] else ''}{'✓ ' if forced == value else ''}{names[value]}", f"force-{key}-{value}") for value in values]
                        + [button("Свободно", f"force-{key}-free")])
    else:
        lines = [f"🛡 <b>Безопасность пользователя {uid}</b>",
                 f"Доступ: {'заблокирован' if data['blocked'] else 'разрешён'}",
                 "Блокировка закрывает бота, API и календарь и останавливает проверку.",
                 "Сброс входов отзывает текущие сеансы и ссылки. Удаление полностью стирает аккаунт и данные."]
        rows += [[button("Разблокировать" if data["blocked"] else "Заблокировать", "unblock" if data["blocked"] else "block")],
                 [button("Сбросить входы…", "confirm-reset")], [button("Сбросить PIN…", "confirm-pin")],
                 [button("Удалить аккаунт…", "confirm-delete")]]
    rows.append([button("← Карточка", "refresh")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def advanced_action(callback, app, uid, action):
    import secrets
    import time
    from ...access import owner, set_control

    async def show(panel):
        text, keyboard = await advanced_panel(app, uid, panel)
        await callback.answer()
        if callback.message and (callback.message.text != text or callback.message.reply_markup != keyboard):
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    if action in ("rights", "appearance", "security"):
        await show(action)
        return True
    if action in ("block", "unblock"):
        if owner(app, uid):
            await callback.answer("Нельзя заблокировать владельца", show_alert=True)
            return True
        await set_control(app, uid, block=action == "block")
        if action == "unblock":
            user = await app.users.get(uid)
            if user and user.ready_to_check:
                await app.watchers.start(uid)
        logger.info("Администратор %s: %s для %s", callback.from_user.id, action, uid)
        await show("security")
        return True
    if action.startswith(("grant-", "force-")):
        parts = action.split("-")
        if len(parts) != 3:
            return False
        kind, name, value = parts
        try:
            await set_control(app, uid, **({"grant": (name, value)} if kind == "grant" else {"force": (name, value)}))
        except ValueError:
            await callback.answer("Некорректная настройка", show_alert=True)
            return True
        logger.info("Администратор %s: %s для %s", callback.from_user.id, action, uid)
        await show("rights" if kind == "grant" else "appearance")
        return True
    if action.startswith("confirm-"):
        kind = action.removeprefix("confirm-")
        if kind not in ("reset", "pin", "delete"):
            return False
        if owner(app, uid):
            await callback.answer("Это действие недоступно для аккаунта владельца", show_alert=True)
            return True
        now = time.monotonic()
        for key, (token, expires) in list(CONFIRMATIONS.items()):
            if expires <= now:
                CONFIRMATIONS.pop(key, None)
        if len(CONFIRMATIONS) >= 100:
            CONFIRMATIONS.clear()
        token = secrets.token_hex(6)
        CONFIRMATIONS[(callback.from_user.id, uid, kind)] = (token, now + 300)
        labels = {"reset": "Отозвать все сеансы и ссылки входа", "pin": "Сбросить PIN и биометрию приложения", "delete": "Полностью удалить аккаунт и все его данные"}
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data=f"adminuser:{uid}:do-{kind}-{token}")],
            [InlineKeyboardButton(text="Отмена", callback_data=f"adminuser:{uid}:security")]])
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(f"⚠️ <b>{labels[kind]}?</b>\nID: <code>{uid}</code>\n"
                                             + ("Удаление необратимо." if kind == "delete" else "Действие выполнится после подтверждения."),
                                             parse_mode="HTML", reply_markup=keyboard)
        return True
    if action.startswith("do-"):
        parts = action.split("-")
        if len(parts) != 3:
            return False
        _, kind, token = parts
        expected = CONFIRMATIONS.pop((callback.from_user.id, uid, kind), None)
        if owner(app, uid) or not expected or expected[1] <= time.monotonic() or not secrets.compare_digest(expected[0], token):
            await callback.answer("Подтверждение устарело. Откройте действие заново.", show_alert=True)
            return True
        if kind == "delete":
            await app.delete_user_data(uid)
        elif kind == "reset":
            await app.miniapp.revoke_tokens(uid)
            await app.db.execute("DELETE FROM app_unlocks WHERE telegram_id = ?", (uid,))
            if app.caldav:
                await app.caldav.accounts.revoke(uid)
            await app.hub.disconnect(uid)
            app.web_logins.pop(uid, None)
        elif kind == "pin":
            async with app.db.transaction() as db:
                for table in ("app_locks", "app_unlocks", "app_passkeys", "app_biometrics", "app_lock_challenges"):
                    await db.execute(f"DELETE FROM {table} WHERE telegram_id = ?", (uid,))
        else:
            return False
        logger.info("Администратор %s: %s для %s", callback.from_user.id, kind, uid)
        await callback.answer("Готово")
        if callback.message:
            if kind == "delete":
                await callback.message.edit_text(f"Аккаунт {uid} удалён.")
            else:
                text, keyboard = await user_card(app, uid)
                await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        return True
    return False
