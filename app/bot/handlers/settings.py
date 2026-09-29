"""Настройки: уведомления, интервал, тихие часы, выбор ребёнка, выход."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import replace

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ...context import AppContext
from ...domain import formatting
from ...domain.models import (
    MAX_CHECK_INTERVAL,
    ApiMode,
    MIN_CHECK_INTERVAL,
    NotificationPrefs,
    QuietHours,
    User,
    clamp_interval,
)
from .. import keyboards
from .common import reply, report_error, require_school

logger = logging.getLogger("netschoolbot.bot")
router = Router(name="settings")


class SettingsForm(StatesGroup):
    quiet_hours = State()


# Какое поле переключает каждая кнопка. Словарь вместо цепочки if-ов:
# добавить настройку — значит добавить строку, а не ветку.
TOGGLES = {
    "changes": "changes",
    "deletes": "deletes",
    "homework": "homework",
    "mail": "mail",
    "weekly": "weekly_summary",
    "absences": "absences",
}


@router.message(Command("settings"))
@router.message(F.text == "⚙️ Настройки")
async def open_settings(message: Message, user: User) -> None:
    await message.answer(
        "⚙️ <b>Настройки</b>",
        parse_mode="HTML",
        reply_markup=keyboards.settings_menu(user),
    )


@router.callback_query(F.data == "settings:open")
async def reopen_settings(callback: CallbackQuery, user: User) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "⚙️ <b>Настройки</b>",
            parse_mode="HTML",
            reply_markup=keyboards.settings_menu(user),
        )


@router.callback_query(F.data.startswith("toggle:"))
async def toggle(callback: CallbackQuery, user: User, app: AppContext) -> None:
    action = callback.data.split(":", 1)[1]

    if action == "enabled":
        updated = await app.users.save(replace(user, enabled=not user.enabled))
        # Проверку нужно не только пометить в базе, но и реально запустить
        # или остановить: раньше эти два действия жили в разных местах и
        # расходились — флаг стоял, а задача не работала.
        if updated.ready_to_check:
            await app.watchers.start(updated.telegram_id)
        else:
            await app.watchers.stop(updated.telegram_id)
        await callback.answer("Уведомления включены" if updated.enabled else "Уведомления выключены")
    elif action in TOGGLES:
        field = TOGGLES[action]
        prefs = user.notifications
        updated = await app.users.save(
            replace(user, notifications=replace(prefs, **{field: not getattr(prefs, field)}))
        )
        await callback.answer("Готово")
    else:
        await callback.answer("Не знаю такой настройки")
        return

    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=keyboards.settings_menu(updated))


@router.callback_query(F.data == "settings:interval")
async def choose_interval(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "⏱ Как часто проверять оценки?",
            reply_markup=keyboards.interval_choices(),
        )


@router.callback_query(F.data.startswith("interval:"))
async def set_interval(callback: CallbackQuery, user: User, app: AppContext) -> None:
    try:
        seconds = clamp_interval(int(callback.data.split(":", 1)[1]))
    except ValueError:
        await callback.answer("Не понял интервал")
        return
    updated = await app.users.save(replace(user, check_interval=seconds))
    if updated.ready_to_check:
        # Перезапуск нужен, чтобы новый интервал заработал сразу, а не после
        # окончания текущей паузы.
        await app.watchers.start(updated.telegram_id)
    await callback.answer(f"Интервал: {seconds // 60} мин")
    if callback.message:
        await callback.message.edit_text(
            "⚙️ <b>Настройки</b>",
            parse_mode="HTML",
            reply_markup=keyboards.settings_menu(updated),
        )


@router.message(Command("interval"))
async def interval_command(message: Message, user: User, app: AppContext) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await reply(
            message,
            f"Укажите интервал: <code>/interval 10м</code>\n"
            f"Допустимо от {MIN_CHECK_INTERVAL // 60} до {MAX_CHECK_INTERVAL // 3600} ч.",
        )
        return
    seconds = parse_interval(parts[1])
    if seconds is None:
        await reply(message, "Не понял интервал. Примеры: <code>10м</code>, <code>1ч</code>, <code>600</code>")
        return
    updated = await app.users.save(replace(user, check_interval=clamp_interval(seconds)))
    if updated.ready_to_check:
        await app.watchers.start(updated.telegram_id)
    await reply(message, f"⏱ Интервал проверки: {updated.check_interval // 60} мин")


def parse_interval(raw: str) -> int | None:
    """Разобрать «10м», «1ч», «600». Русские буквы поддерживаются наравне."""
    value = raw.strip().lower().replace("м", "m").replace("ч", "h").replace("с", "s")
    if not value:
        return None
    multipliers = {"h": 3600, "m": 60, "s": 1}
    suffix = value[-1]
    try:
        if suffix in multipliers:
            return int(value[:-1]) * multipliers[suffix]
        return int(value)
    except ValueError:
        return None


@router.callback_query(F.data == "settings:quiet")
async def ask_quiet_hours(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(SettingsForm.quiet_hours)
    await reply(
        callback,
        "🌙 Пришлите промежуток, в который не беспокоить, например "
        "<code>22:00-07:00</code>.\n\nЧтобы выключить — напишите <code>выкл</code>.",
    )


@router.message(SettingsForm.quiet_hours)
async def set_quiet_hours(message: Message, user: User, app: AppContext, state: FSMContext) -> None:
    raw = (message.text or "").strip().lower()
    await state.clear()

    if raw in {"выкл", "off", "нет", "-"}:
        updated = await app.users.save(replace(user, quiet_hours=QuietHours()))
        await reply(message, "🌙 Тихие часы выключены.")
        return

    window = parse_quiet_hours(raw)
    if window is None:
        await reply(message, "Не понял. Нужен формат <code>22:00-07:00</code>.")
        return
    updated = await app.users.save(replace(user, quiet_hours=window))
    await reply(message, f"🌙 Тихие часы: {updated.quiet_hours.as_text()}")


def parse_quiet_hours(raw: str) -> QuietHours | None:
    """Разобрать «22:00-07:00». Промежуток через полночь допустим."""
    for separator in ("-", "–", "—", " до "):
        if separator in raw:
            left, _, right = raw.partition(separator)
            start, end = _parse_time(left), _parse_time(right)
            if start and end:
                return QuietHours(start=start, end=end)
            return None
    return None


def _parse_time(raw: str) -> dt.time | None:
    text = raw.strip()
    for fmt in ("%H:%M", "%H.%M", "%H"):
        try:
            return dt.datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


@router.callback_query(F.data == "settings:child")
async def choose_child(callback: CallbackQuery, user: User) -> None:
    await callback.answer()
    if len(user.available_students) < 2:
        await reply(callback, "В аккаунте только один ученик.")
        return
    if callback.message:
        await callback.message.edit_text(
            "👶 Чей дневник показывать?", reply_markup=keyboards.children(user)
        )


@router.callback_query(F.data.startswith("child:"))
async def set_child(callback: CallbackQuery, user: User, app: AppContext) -> None:
    try:
        student_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Не понял, какой ученик")
        return

    student = next((s for s in user.available_students if s.id == student_id), None)
    if student is None:
        await callback.answer("Такого ученика нет в списке")
        return

    updated = await app.users.save(
        replace(user, selected_student_id=student.id, student_name=student.name)
    )
    # Оценки другого ребёнка — другой журнал. Без сброса состояния
    # пользователь получил бы весь его журнал как «новые оценки».
    await app.state.forget_all(updated.telegram_id)
    await app.state.mark_baseline_pending(updated.telegram_id, True)
    await app.cache.clear(updated.telegram_id)
    await app.pool.invalidate(updated.telegram_id)
    if updated.ready_to_check:
        await app.watchers.start(updated.telegram_id)

    await callback.answer(f"Выбран: {student.name}")
    if callback.message:
        await callback.message.edit_text(
            f"👶 Показываю дневник: <b>{formatting.esc(student.name)}</b>",
            parse_mode="HTML",
            reply_markup=keyboards.settings_menu(updated),
        )


@router.message(Command("child"))
async def child_command(message: Message, user: User) -> None:
    if len(user.available_students) < 2:
        await reply(message, "В аккаунте только один ученик.")
        return
    await message.answer("👶 Чей дневник показывать?", reply_markup=keyboards.children(user))


@router.callback_query(F.data == "settings:logout")
async def ask_logout(callback: CallbackQuery) -> None:
    await callback.answer()
    await reply(
        callback,
        "🚪 Выйти из «Сетевого города»? Настройки уведомлений сохранятся, "
        "но школу и вход придётся выбрать заново.",
        reply_markup=keyboards.confirm("logout", yes="Выйти"),
    )


@router.callback_query(F.data == "confirm:logout")
@router.message(Command("logout"))
async def logout(event: Message | CallbackQuery, user: User, app: AppContext) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer()
    await app.watchers.stop(user.telegram_id)
    # Каждый шаг перечислен явно: раньше часть чистки пряталась внутри
    # чужих методов, и понять, что именно остаётся после выхода, было нельзя.
    await app.pool.invalidate(user.telegram_id, forget_saved=True)
    await app.state.forget_all(user.telegram_id)
    await app.cache.clear(user.telegram_id)
    await app.miniapp.revoke_tokens(user.telegram_id)
    await app.users.save(
        replace(
            user,
            enabled=False,
            school=replace(user.school, url="", name=""),
            credentials=replace(user.credentials, login="", password=""),
            student_name="",
            selected_student_id=None,
            available_students=(),
        )
    )
    await reply(event, "🚪 Вы вышли. Ссылка на приложение отозвана. Войти заново: /login")


@router.callback_query(F.data == "confirm:cancel")
async def cancel(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")
    if callback.message:
        await callback.message.delete()


API_HELP = (
    "🔌 <b>Откуда бот берёт данные</b>\n\n"
    "Сейчас: <b>{current}</b>.\n\n"
    "🌐 <b>Веб-API</b> — как сайт «Сетевого города». Режим по умолчанию, "
    "умеет всё, включая отправку писем.\n\n"
    "📱 <b>Мобильное API</b> — как приложение NetSchool. Вход держится 60 дней "
    "без переподключений, у домашних заданий видно время публикации, у уроков — "
    "учитель и тема. Письма отправляются тоже."
)


def _api_text(user: User, mobile_available: bool) -> str:
    text = API_HELP.format(current=user.effective_api_mode.title)
    if not mobile_available:
        text += "\n\n<i>Мобильное API на этом сервере бота не настроено.</i>"
    return text


@router.message(Command("changeapi"))
async def change_api(message: Message, user: User, app: AppContext) -> None:
    if hint := require_school(user):
        await reply(message, hint)
        return
    await message.answer(
        _api_text(user, app.diary.mobile_available),
        parse_mode="HTML",
        reply_markup=keyboards.api_modes(user, app.diary.mobile_available),
    )


@router.callback_query(F.data == "settings:api")
async def open_api(callback: CallbackQuery, user: User, app: AppContext) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            _api_text(user, app.diary.mobile_available),
            parse_mode="HTML",
            reply_markup=keyboards.api_modes(user, app.diary.mobile_available),
        )


@router.callback_query(F.data.startswith("api:"))
async def set_api(callback: CallbackQuery, user: User, app: AppContext) -> None:
    mode = ApiMode.parse(callback.data.split(":", 1)[1])
    if mode is user.effective_api_mode and mode is user.api_mode:
        await callback.answer("Уже выбрано")
        return
    if not user.school.configured:
        await callback.answer("Сначала войдите: /login", show_alert=True)
        return
    if mode is ApiMode.MOBILE and not app.diary.mobile_available:
        await callback.answer("Мобильное API не настроено", show_alert=True)
        return

    await callback.answer("Переключаю…")
    try:
        updated = await app.diary.switch_api(user, mode)
    except Exception as exc:
        await report_error(callback, exc)
        return

    # Разные API могут по-разному собирать одну и ту же работу (порядок,
    # подписи). Без тихой пересборки состояния первая проверка разослала бы
    # «новые» и «удалённые» оценки, которых на деле нет.
    await app.state.mark_baseline_pending(updated.telegram_id, True)
    await app.cache.clear(updated.telegram_id)
    if updated.ready_to_check:
        await app.watchers.start(updated.telegram_id)

    note = ""
    if mode is ApiMode.WEB and updated.credentials.login_type.is_gosuslugi:
        note = "\n\nДля веб-API нужно заново войти через Госуслуги: /login"
    if callback.message:
        await callback.message.edit_text(
            f"✅ Теперь данные идут через <b>{updated.api_mode.title}</b>.{note}",
            parse_mode="HTML",
            reply_markup=keyboards.settings_menu(updated),
        )
