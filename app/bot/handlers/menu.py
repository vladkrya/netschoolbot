"""Меню, дневник, расписание, оценки и статистика."""

from __future__ import annotations

import datetime as dt
import json
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import BufferedInputFile, CallbackQuery, Message, ReplyKeyboardRemove

from ...context import AppContext
from ...exporting import SCOPES, export_grades
from ...domain import formatting
from ...domain.models import MarkKind, User, normalize
from ...notifications.outbox import mark_from_dict
from ...netschool.errors import NetSchoolError
from ...web import viewing
from .. import keyboards
from .common import reply, report_error, require_school

logger = logging.getLogger("netschoolbot.bot")
router = Router(name="menu")

GREETING = (
    "👋 <b>Бот «Сетевого города»</b>\n\n"
    "Присылаю новые оценки, их изменения и удаления, показываю домашние "
    "задания, расписание и статистику.\n\n"
    "Вы не вошли в «Сетевой город».\n\n"
    "Войти — /login"
)


def next_school_days(start: dt.date, count: int = 3) -> list[dt.date]:
    """Ближайшие учебные дни. С пятницы — сразу на понедельник."""
    days: list[dt.date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


async def miniapp_url(app: AppContext, user: User) -> str:
    """Адрес мини-приложения для этого пользователя.

    Токен в адресе нужен не для входа внутри Telegram — там личность
    подтверждает подписанная initData, — а чтобы приложение работало и
    после установки на домашний экран, где initData уже нет.
    """
    if not user.school.configured:
        return ""
    token = await app.miniapp.issue_token(user.telegram_id)
    return f"{app.settings.web.miniapp_url}/?token={token}"


def mirror_url(app: AppContext, url: str) -> str:
    """Та же ссылка на резервном адресе, если он настроен."""
    web = app.settings.web
    if not url or not web.mirror_url:
        return ""
    return web.mirror_miniapp_url + url[len(web.miniapp_url):]


@router.message(CommandStart())
@router.message(Command("menu"))
async def show_menu(message: Message, user: User, app: AppContext) -> None:
    text = GREETING if not user.school.configured else "📱 Дневник открывается кнопкой внизу чата. Команды доступны через /."
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    if user.school.configured:
        await message.answer("Открыть мини-приложение", reply_markup=keyboards.miniapp(await miniapp_url(app, user)))


@router.message(Command("dz"))
@router.message(F.text == "📚 Домашка")
async def homework(message: Message, user: User, app: AppContext) -> None:
    if hint := require_school(user):
        await reply(message, hint)
        return
    try:
        today = user.today()
        wanted = set(next_school_days(today))
        items = await app.diary.fetch_homework(user, weeks_back=0, weeks_forward=1, today=today)
    except NetSchoolError as exc:
        await report_error(message, exc)
        return
    relevant = sorted(
        (item for item in items if item.due_date in wanted),
        key=lambda h: (h.due_date, h.lesson_number or 0, h.subject),
    )
    done = await app.state.done_homework(user.telegram_id)
    text = formatting.homework_list(relevant, "📚 <b>Домашние задания</b>", tz=user.tz, done=done)
    parts = formatting.split_message(text)
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        await message.answer(
            part,
            parse_mode="HTML",
            reply_markup=keyboards.homework_done(relevant, done) if last else None,
        )


@router.callback_query(F.data.startswith("hwdone:"))
async def toggle_homework_done(callback: CallbackQuery, user: User, app: AppContext) -> None:
    """Отметить задание выполненным прямо из /dz.

    Список не перезапрашивается у школы: клавиатура уже несёт подписи,
    достаточно перевернуть галочку у нажатой кнопки.
    """
    key = callback.data.split(":", 1)[1]
    done = key not in await app.state.done_homework(user.telegram_id)
    await app.state.set_homework_done(user.telegram_id, key, done)
    await callback.answer("Выполнено ✅" if done else "Снята отметка")
    markup = callback.message.reply_markup if callback.message else None
    if markup is None:
        return
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data == callback.data:
                button.text = ("✅" if done else "⬜") + button.text[1:]
    await callback.message.edit_reply_markup(reply_markup=markup)


@router.message(Command("rasp"))
@router.message(F.text == "🗓 Расписание")
async def schedule(message: Message, user: User, app: AppContext) -> None:
    if hint := require_school(user):
        await reply(message, hint)
        return
    try:
        today = user.today()
        wanted = set(next_school_days(today))
        days = await app.diary.fetch_diary(user, weeks_back=0, weeks_forward=1, today=today)
    except NetSchoolError as exc:
        await report_error(message, exc)
        return

    relevant = [day for day in days if day.day in wanted]
    await reply(message, formatting.schedule(relevant, "🗓 <b>Расписание</b>"))


@router.message(Command("grades"))
@router.message(F.text == "📊 Оценки")
async def grades(message: Message, user: User, app: AppContext) -> None:
    if hint := require_school(user):
        await reply(message, hint)
        return
    try:
        records = await app.diary.fetch_marks(user)
    except NetSchoolError as exc:
        await report_error(message, exc)
        return
    if not records:
        await reply(message, "📊 Оценок за период нет.")
        return

    subjects = sorted({record.subject for record in records})
    await app.cache.put(user.telegram_id, "subjects", subjects)
    await reply(
        message,
        "📊 Выберите предмет:",
        reply_markup=keyboards.subjects(subjects),
    )


@router.callback_query(F.data.startswith("subject:"))
async def subject_marks(callback: CallbackQuery, user: User, app: AppContext) -> None:
    await callback.answer()
    cached = await app.cache.get(user.telegram_id, "subjects")
    if cached is None:
        await reply(callback, "Список предметов устарел — откройте /grades заново.")
        return
    subjects, _ = cached
    try:
        index = int(callback.data.split(":", 1)[1])
        subject = subjects[index]
    except (ValueError, IndexError):
        await reply(callback, "Не понял, какой предмет. Откройте /grades заново.")
        return

    try:
        records = await app.diary.fetch_marks(user)
    except NetSchoolError as exc:
        await report_error(callback, exc)
        return
    wanted = normalize(subject)
    selected = [r for r in records if normalize(r.subject) == wanted]
    await reply(callback, formatting.marks_by_subject(subject, selected))


@router.message(Command("mystats"))
@router.message(F.text == "📈 Статистика")
async def statistics(message: Message, user: User, app: AppContext) -> None:
    if hint := require_school(user):
        await reply(message, hint)
        return
    try:
        records = await app.diary.fetch_marks(user)
    except NetSchoolError as exc:
        await report_error(message, exc)
        return
    await reply(message, formatting.statistics(user, records))


@router.message(Command("weeksummary"))
async def week_summary(message: Message, user: User, app: AppContext) -> None:
    if hint := require_school(user):
        await reply(message, hint)
        return
    today = user.today()
    start = today - dt.timedelta(days=today.weekday())
    try:
        records = await app.diary.fetch_marks(user, weeks_back=1, weeks_forward=0, today=today)
    except NetSchoolError as exc:
        await report_error(message, exc)
        return
    week = [r for r in records if start <= r.date <= today]
    await reply(message, formatting.weekly_summary(week, start, today))


async def _day_command(message: Message, user: User, app: AppContext, *, tomorrow: bool) -> None:
    if hint := require_school(user):
        await reply(message, hint)
        return
    today = user.today()
    # «Завтра» в пятницу — понедельник: в выходные уроков нет.
    day = next_school_days(today + dt.timedelta(days=1), 1)[0] if tomorrow else today
    try:
        days = await app.diary.fetch_period(user, day, day, today=today)
    except NetSchoolError as exc:
        await report_error(message, exc)
        return
    found = next((d for d in days if d.day == day), None)
    if tomorrow:
        title = "Завтра" if day == today + dt.timedelta(days=1) else "Следующий учебный день"
    else:
        title = "Сегодня"
    done = await app.state.done_homework(user.telegram_id)
    text = formatting.day_brief(found, day, title, done=done)
    notes = [n for n in await app.notes.between(user.telegram_id, day, day) if not n["done"]]
    if notes:
        text += "\n\n📝 <b>Свои задачи</b>\n" + "\n".join(
            f"⬜ {formatting.esc(n['subject'] + ': ' if n['subject'] else '')}{formatting.esc(n['text'])}" for n in notes
        )
    await reply(message, text)


@router.message(Command("today"))
async def today_command(message: Message, user: User, app: AppContext) -> None:
    await _day_command(message, user, app, tomorrow=False)


@router.message(Command("tomorrow"))
async def tomorrow_command(message: Message, user: User, app: AppContext) -> None:
    await _day_command(message, user, app, tomorrow=True)


@router.message(Command("last"))
async def last_command(message: Message, user: User, app: AppContext) -> None:
    if hint := require_school(user):
        await reply(message, hint)
        return
    try:
        records = await app.diary.fetch_marks(user, weeks_back=3, weeks_forward=0, today=user.today())
    except NetSchoolError as exc:
        await report_error(message, exc)
        return
    await reply(message, formatting.last_marks(records))


@router.message(Command("export"))
async def export_command(message: Message, user: User) -> None:
    if hint := require_school(user):
        await reply(message, hint)
        return
    await reply(message, "📤 Выгрузить оценки в Excel — за какой срок?", reply_markup=keyboards.export_periods())


@router.callback_query(F.data.startswith("export:"))
async def export_file(callback: CallbackQuery, user: User, app: AppContext) -> None:
    scope = callback.data.split(":", 1)[1]
    if scope not in SCOPES or (hint := require_school(user)):
        await callback.answer("Не получится")
        return
    await callback.answer("Готовлю файл…")
    if callback.message:
        await callback.message.edit_text("⏳ Собираю оценки — за весь год это может занять до минуты…")
    try:
        data, name = await export_grades(app, user, scope)
    except NetSchoolError as exc:
        await report_error(callback, exc)
        return
    if callback.message:
        await callback.message.answer_document(
            BufferedInputFile(data, filename=name),
            caption="📊 Оценки: лист «Итоги» — средние по предметам, «Оценки» — все работы.",
        )
        await callback.message.delete()


@router.message(Command("profile"))
@router.message(F.text == "👤 Профиль")
async def profile(message: Message, user: User) -> None:
    await reply(message, formatting.profile(user))


@router.message(Command("app"))
async def open_miniapp(message: Message, user: User, app: AppContext) -> None:
    """Открыть дневник мини-приложением Telegram."""
    if (hint := require_school(user)) and not await viewing.choices(app, user):
        # Родной с просмотром дневника входит без своего «Сетевого города».
        await reply(message, hint)
        return
    url = await miniapp_url(app, user)
    await message.answer(
        "📱 <b>Дневник</b>\n\n"
        "Открывается прямо в Telegram — оценки, домашние задания и расписание "
        "в одном месте.",
        parse_mode="HTML",
        reply_markup=keyboards.miniapp(url),
    )


@router.message(Command("app_install"))
async def install_miniapp(message: Message, user: User, app: AppContext) -> None:
    """Ссылка для установки на домашний экран.

    Отдельной командой, потому что внутри Telegram установить приложение
    нельзя — для этого страницу нужно открыть в самом браузере.
    """
    if (hint := require_school(user)) and not await viewing.choices(app, user):
        # Родной с просмотром дневника входит без своего «Сетевого города».
        await reply(message, hint)
        return
    url = await miniapp_url(app, user)
    mirror = mirror_url(app, url)
    mirror_hint = (
        "Если мобильный интернет урезан и основной адрес не открывается — "
        "установите с резервного.\n\n"
    ) if mirror else ""
    await message.answer(
        "🌐 <b>Установка на телефон</b>\n\n"
        "Откройте ссылку в браузере и выберите «Добавить на домашний экран» — "
        "дневник появится отдельной иконкой и будет работать без Telegram.\n\n"
        f"{mirror_hint}"
        "⚠️ Ссылка персональная: по ней открывается ваш дневник. "
        "Если она утекла — <code>/app_reset</code>.",
        parse_mode="HTML",
        reply_markup=keyboards.miniapp_external(url, mirror),
    )


@router.message(Command("demo"))
async def demo(message: Message, app: AppContext) -> None:
    """Демо-версия приложения на выдуманных данных — без входа в школу."""
    url = f"{app.settings.web.miniapp_url}/demo"
    direct_url = f"https://ns.ikrya.ru{app.settings.web.miniapp_path}/demo"
    await message.answer(
        "✨ <b>Демо-версия дневника</b>\n\n"
        "Выдуманный ученик и оценки, всё можно трогать: в меню ☰ есть "
        "«Настройки демо» — имя, школа, дети, пропуски, число оценок.\n\n"
        "Ссылкой можно делиться — личных данных в ней нет.",
        parse_mode="HTML",
        reply_markup=keyboards.demo(url, direct_url),
    )


@router.message(Command("app_reset"))
async def miniapp_reset(message: Message, user: User, app: AppContext) -> None:
    token = await app.miniapp.issue_token(user.telegram_id, revoke_existing=True)
    url = f"{app.settings.web.miniapp_url}/?token={token}"
    await message.answer(
        "🔄 Старая ссылка отозвана, прежние перестали работать. Новая:",
        reply_markup=keyboards.miniapp_external(url, mirror_url(app, url)),
    )


@router.message(Command("status"))
async def status(message: Message, user: User, app: AppContext) -> None:
    watching = user.telegram_id in app.watchers.running
    tracked = len(await app.state.load_marks(user.telegram_id))
    info = await app.state.watch_status(user.telegram_id)
    text = formatting.watch_status(user, info, running=watching, tracked=tracked)
    if await app.state.is_baseline_pending(user.telegram_id):
        text += "\n• Первая проверка после переноса пройдёт молча"
    await reply(message, text)


@router.message(Command("history"))
async def history(message: Message, user: User, app: AppContext) -> None:
    """Что происходило с оценками — даже если уведомление выключено или не дошло."""
    rows = await app.state.mark_history(user.telegram_id, limit=30)
    entries = []
    for row in rows:
        try:
            record = mark_from_dict(json.loads(row["payload"]))
            seen = dt.datetime.fromisoformat(row["created_at"])
        except (ValueError, KeyError, TypeError):
            continue
        entries.append((MarkKind(row["kind"]), record, row["old_mark"], row["new_mark"], seen))
    await reply(message, formatting.mark_history(entries, tz=user.tz))


@router.message(StateFilter(None))
async def unrecognized_message(message: Message, user: User) -> None:
    if hint := require_school(user):
        await reply(message, hint)
