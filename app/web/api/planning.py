"""Обратный отсчёт, поиск за год, сводка по детям и расписание картинкой в чат."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import replace

from aiohttp import web

from ...context import AppContext
from ...domain import formatting
from ...domain.models import User
from ...domain.periods import academic_year_start, current_period
from ...domain.records import DUTY_MARK
from ...domain.vacations import (
    DIARY_MARGIN_DAYS, Vacation, exam_for, next_vacation, on_vacation, refine_by_diary, vacations,
)
from ...netschool.errors import NetSchoolError
from .base import BOT, _cached, context, current_user, json_error, logger, routes
from .diary import _homework_to_json
from .marks import _user_periods
from .profile import class_name

# Каникулы и экзамены меняются раз в год, а класс — раз в год тем более.
COUNTDOWN_FRESH_SECONDS = 6 * 3600
# Примерные каникулы ближе этого уточняются по дневнику: расписание на
# ближайшие недели школа обычно уже заполнила.
REFINE_AHEAD_DAYS = 21


async def _refined(app: AppContext, user: User, today: dt.date, items: list[Vacation]) -> list[Vacation]:
    """Ближайшие примерные каникулы — по дневнику (domain/vacations.py)."""
    near = [item for item in items if not item.exact and item.end >= today
            and (item.start - today).days <= REFINE_AHEAD_DAYS]
    if not near:
        return items
    margin = dt.timedelta(days=DIARY_MARGIN_DAYS)
    try:
        days = await app.diary.fetch_period(
            user, min(item.start for item in near) - margin, max(item.end for item in near) + margin, today=today)
    except NetSchoolError:
        return items
    lessons = {day.day: [lesson.is_elective for lesson in day.lessons] for day in days}
    return [refine_by_diary(item, lessons) if item in near else item for item in items]


@routes.get("/api/countdown")
async def countdown(request: web.Request) -> web.Response:
    """До каникул и, у 9-х и 11-х классов, до ОГЭ или ЕГЭ — карточка на «Дне».

    Даты, а не число дней: число дней считает приложение, чтобы кэш на
    полдня не отставал на сутки после полуночи.
    """

    async def produce(app: AppContext, user: User):
        today = user.today()
        year, source = await _user_periods(app, user, today)
        items = await _refined(app, user, today, vacations(today, year, source))
        now, upcoming = on_vacation(today, items), next_vacation(today, items)
        exam = exam_for(await class_name(app, user), today)
        shown = now or upcoming
        return {
            "vacation": {"label": now.label, "start": now.start.isoformat(), "end": now.end.isoformat(), "now": True}
            if now else
            {"label": upcoming.label, "start": upcoming.start.isoformat(), "end": upcoming.end.isoformat(), "now": False}
            if upcoming else None,
            # Каникулы по общепринятым датам — приложение пишет «примерно».
            "vacation_exact": bool(shown and shown.exact),
            "exam": {"name": exam[0], "start": exam[1].isoformat(), "confirmed": exam[2]} if exam else None,
        }

    # Раздел кэша с номером: ответ хранится 6 часов, и после смены расчёта
    # люди полдня видели бы старый — меняя расчёт, поднимайте номер.
    return await _cached(request, "countdown-v2", produce, fresh_seconds=COUNTDOWN_FRESH_SECONDS)


# Поиск по году: задания перечитываются раз в 6 часов, почта — раз в полчаса.
SEARCH_HOMEWORK_FRESH = dt.timedelta(hours=6)
SEARCH_MAIL_FRESH = dt.timedelta(minutes=30)
# Мобильное API больше сотни писем за раз может не отдать.
SEARCH_MAIL_LIMIT = 100
SEARCH_RESULTS = 50
SEARCH_MIN_QUERY = 2
# Сколько ждать сбора индекса в запросе; дальше приложение спросит ещё раз.
SEARCH_WAIT_SECONDS = 20

# Сбор индекса идёт один на человека и раздел: поиск шлёт запрос на каждую
# паузу в наборе, и раньше каждый заново читал дневник с 1 сентября — первый
# поиск не дожидался ни одного ответа.
_building: dict[tuple[int, str], asyncio.Task] = {}


def _fold(text: str) -> str:
    return (text or "").casefold().replace("ё", "е")


class IndexPending(Exception):
    """Индекс ещё собирается — ответ будет в следующем запросе."""


def _build(app: AppContext, user: User, section: str, producer) -> asyncio.Task:
    key = (user.telegram_id, section)
    task = _building.get(key)
    if task is None or task.done():
        async def run():
            try:
                payload = await producer()
                await app.cache.put(user.telegram_id, section, payload)
                return payload
            finally:
                _building.pop(key, None)

        task = _building[key] = asyncio.create_task(run())
        # Ошибку забирает тот, кто ждёт; без ожидающих — не шумим в логе.
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
    return task


async def _index(app: AppContext, user: User, section: str, fresh: dt.timedelta, producer):
    """Свой кэш индекса поиска: свежий — сразу, устаревший — пока собирается
    новый, а без кэша ждём сбор не дольше SEARCH_WAIT_SECONDS (IndexPending)."""
    cached = await app.cache.get(user.telegram_id, section)
    now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if cached is not None and now_utc - cached[1] < fresh:
        return cached[0]
    task = _build(app, user, section, producer)
    if cached is not None:
        # Искать по прежнему, пока в фоне собирается свежий.
        return cached[0]
    done, _ = await asyncio.wait({task}, timeout=SEARCH_WAIT_SECONDS)
    if not done:
        raise IndexPending
    return task.result()


@routes.get("/api/search")
async def search(request: web.Request) -> web.Response:
    """Поиск по домашним заданиям и почте за весь учебный год."""
    query = _fold((request.query.get("q") or "").strip())[:100]
    if len(query) < SEARCH_MIN_QUERY:
        return json_error("Введите хотя бы две буквы")
    app, user = context(request), current_user(request)
    today = user.today()
    year_start = dt.date(academic_year_start(today), 9, 1)

    async def homework():
        days = await app.diary.fetch_period(user, year_start, today + dt.timedelta(days=14), today=today)
        observed = await app.state.observed_homework(user.telegram_id)
        items = sorted((item for day in days for item in day.homework), key=lambda h: (h.due_date, h.subject))
        return [{**_homework_to_json(item, observed), "label": formatting.date_label(item.due_date)} for item in items]

    async def mail():
        messages = await app.diary.fetch_mail(user, limit=SEARCH_MAIL_LIMIT)
        return [m for m in messages if (m.get("sent") or "")[:10] >= year_start.isoformat()]

    words = query.split()
    found_homework, found_mail, failed, pending = [], [], [], []
    # Оба индекса собираются одновременно, а не по очереди.
    indexes = await asyncio.gather(
        _index(app, user, "search:homework", SEARCH_HOMEWORK_FRESH, homework),
        _index(app, user, "search:mail", SEARCH_MAIL_FRESH, mail),
        return_exceptions=True,
    )
    for name, result in zip(("homework", "mail"), indexes):
        if isinstance(result, IndexPending):
            pending.append(name)
        elif isinstance(result, NetSchoolError):
            logger.info("Поиск: %s для %s не загрузилось: %s", name, user.telegram_id, result.reason)
            failed.append(name)
        elif isinstance(result, BaseException):
            logger.exception("Поиск: %s для %s", name, user.telegram_id, exc_info=result)
            failed.append(name)
    homework_index = indexes[0] if isinstance(indexes[0], list) else []
    mail_index = indexes[1] if isinstance(indexes[1], list) else []
    for item in reversed(homework_index):
        text = _fold(" ".join((item["subject"], item["text"] or "", item.get("topic") or "", item.get("type") or "")))
        if all(word in text for word in words):
            found_homework.append(item)
            if len(found_homework) >= SEARCH_RESULTS:
                break
    # Родным с просмотром поиск недоступен (viewing.py): там была бы и почта.
    for message in mail_index:
        text = _fold(f"{message['subject']} {message['author']}")
        if all(word in text for word in words):
            found_mail.append(message)
            if len(found_mail) >= SEARCH_RESULTS:
                break
    if len(failed) == 2:
        return json_error("«Сетевой город» сейчас не отвечает — поиск не удался", status=503, reason="server")
    return web.json_response({"ok": True, "data": {
        "homework": found_homework, "mail": found_mail, "failed": failed, "pending": pending,
        "since": year_start.isoformat(),
    }})


async def _child_summary(app: AppContext, user: User, done: set[str]) -> dict:
    """Карточка одного ребёнка: средний за период, свежие оценки, долги, задания."""
    today = user.today()
    year, _ = await _user_periods(app, user, today)
    period = current_period(year, today)
    days = await app.diary.fetch_period(user, period.start, today + dt.timedelta(days=7), today=today)
    marks = [r for d in days for r in d.marks if period.covers(r.date) and r.date <= today]
    recent = sorted(marks, key=lambda r: (r.date, r.lesson_number or 0), reverse=True)[:6]
    by_subject: dict[str, list] = {}
    for record in marks:
        by_subject.setdefault(record.subject, []).append(record)
    low = []
    for subject, items in sorted(by_subject.items()):
        average = formatting.weighted_average(items)
        if average is not None and average < 3.5:
            low.append({"subject": subject, "average": round(average, 2)})
    upcoming = [h for d in days for h in d.homework if today <= h.due_date <= today + dt.timedelta(days=1)]
    return {
        "period": period.label,
        "average": formatting.weighted_average(marks),
        "count": len(marks),
        "recent": [{"subject": r.subject, "mark": r.mark, "date": r.date.isoformat(), "weight": r.weight} for r in recent],
        "debts": [{"subject": r.subject, "date": r.date.isoformat(), "title": r.title or r.assignment_type}
                  for r in marks if (r.mark or "").strip() == DUTY_MARK],
        "low": low,
        "homework": {
            "total": sum(1 for h in upcoming if not h.nothing_to_do and not h.classwork),
            "left": sum(1 for h in upcoming if not h.nothing_to_do and not h.classwork and h.key not in done),
        },
    }


@routes.get("/api/children")
async def children(request: web.Request) -> web.Response:
    """Сводка по всем детям аккаунта на одном экране."""
    if len(current_user(request).available_students) < 2:
        return web.json_response({"ok": True, "data": {"children": []}, "stale": False})

    async def produce(app: AppContext, user: User):
        done = await app.state.done_homework(user.telegram_id)
        result = []
        for student in user.available_students:
            child = replace(user, selected_student_id=student.id, student_name=student.name)
            entry = {"id": student.id, "name": student.name, "current": student.id == user.selected_student_id}
            try:
                entry.update(await _child_summary(app, child, done))
            except NetSchoolError as exc:
                logger.info("Сводка по ребёнку %s не собрана: %s", user.telegram_id, exc.reason)
                entry["error"] = exc.user_message
            result.append(entry)
        return {"children": result}

    return await _cached(request, "children", produce,
                         cacheable=lambda payload: not any("error" in c for c in payload["children"]))


SHARE_MAX_BYTES = 5 * 1024 * 1024
_PNG = b"\x89PNG\r\n\x1a\n"


@routes.post("/api/share/schedule")
async def share_schedule(request: web.Request) -> web.Response:
    """Расписание недели картинкой — в чат с ботом, чтобы переслать дальше.

    Картинку рисует приложение (canvas): там уже есть расписание со своими
    правками, а серверу пришлось бы повторять раскладку и шрифты."""
    from aiogram.types import BufferedInputFile

    from ...accounts import has_telegram

    user = current_user(request)
    bot = request.config_dict.get(BOT)
    if bot is None or not has_telegram(user.telegram_id):
        return json_error("Telegram не привязан — сохраните картинку", reason="no_telegram")
    data = await request.content.read(SHARE_MAX_BYTES + 1)
    if len(data) > SHARE_MAX_BYTES or not data.startswith(_PNG):
        return json_error("Некорректная картинка")
    caption = (request.query.get("caption") or "Расписание на неделю").strip()[:200]
    await bot.send_photo(user.telegram_id, BufferedInputFile(data, filename="raspisanie.png"), caption=f"🗓 {caption}")
    return web.json_response({"ok": True})
