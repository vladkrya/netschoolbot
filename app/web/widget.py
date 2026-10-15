"""Виджет на экране iPhone через Scriptable.

Live Activity без своего приложения в App Store не сделать, а виджет
Scriptable умеет живой обратный отсчёт до времени — этого хватает для
«До конца урока 12:34». Скрипт на телефоне ходит сюда за расписанием на
ближайшие дни и сам решает, что сейчас: урок, перемена или ничего, — так
виджет обновляется и без сети.

Ключ виджета отдельный от ссылки на приложение: он лежит в тексте скрипта
на телефоне и открывает только расписание, а не дневник целиком.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

from aiohttp import web

from ..db.repositories import WidgetKeyRepository
from ..domain.records import DiaryDay
from ..netschool.errors import NetSchoolError
from .api import context, current_user, json_error

routes = web.RouteTableDef()

# Сколько дней с уроками отдавать: сегодня и следующие учебные — чтобы
# вечером виджет показал завтрашний первый урок.
DAYS_AHEAD = 7
MAX_DAYS = 3
# Виджеты на нескольких экранах обновляются часто; школу спрашиваем не чаще.
FRESH = dt.timedelta(minutes=20)
SCRIPT = Path(__file__).parent / "static" / "widget" / "scriptable.js"
NO_STORE = {"Cache-Control": "no-store"}


def _moment(day: dt.date, clock: str, tz: dt.tzinfo) -> int | None:
    try:
        moment = dt.datetime.strptime(f"{day.isoformat()} {clock.strip()}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return int(moment.replace(tzinfo=tz).timestamp())


def widget_days(days: list[DiaryDay], tz: dt.tzinfo, today: dt.date) -> list[dict]:
    """Уроки сегодня и в ближайшие учебные дни, время — секунды Unix."""
    result = []
    for day in sorted(days, key=lambda item: item.day):
        if day.day < today:
            continue
        lessons = []
        for lesson in day.lessons:
            start, end = _moment(day.day, lesson.start, tz), _moment(day.day, lesson.end, tz)
            if start is None or end is None or end <= start:
                continue
            lessons.append({
                "number": lesson.number, "subject": lesson.subject, "room": lesson.room,
                "start": start, "end": end, "start_text": lesson.start.strip(), "end_text": lesson.end.strip(),
            })
        if lessons:
            lessons.sort(key=lambda item: item["start"])
            result.append({"date": day.day.isoformat(), "lessons": lessons})
        if len(result) >= MAX_DAYS:
            break
    return result


async def _schedule(app, user) -> tuple[list[dict], bool]:
    """Расписание из кэша, если свежее; при недоступной школе — прежнее."""
    today = user.today()
    cached = await app.cache.get(user.telegram_id, "widget")
    now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    usable = cached is not None and cached[0].get("today") == today.isoformat()
    if usable and now_utc - cached[1] < FRESH:
        return cached[0]["days"], False
    try:
        days = await app.diary.fetch_period(user, today, today + dt.timedelta(days=DAYS_AHEAD), today=today)
    except NetSchoolError:
        if cached is None:
            raise
        # Вчерашний ответ тоже годится: в нём уже есть сегодняшние уроки.
        return [day for day in cached[0]["days"] if day["date"] >= today.isoformat()], True
    payload = widget_days(days, user.tz, today)
    await app.cache.put(user.telegram_id, "widget", {"today": today.isoformat(), "days": payload})
    return payload, False


@routes.get("/widget/schedule")
async def schedule(request: web.Request) -> web.Response:
    """Расписание для виджета по ключу (заголовок X-Widget-Key или ?key=)."""
    app = context(request)
    key = (request.headers.get("X-Widget-Key") or request.query.get("key") or "").strip()
    telegram_id = await WidgetKeyRepository(app.db).resolve(key)
    user = await app.users.get(telegram_id) if telegram_id is not None else None
    if not user or not user.school.configured or not user.credentials.usable:
        # Задержка неверного ключа, как у CalDAV.
        await asyncio.sleep(1)
        return json_error("Ключ виджета не подходит — создайте новый в приложении", status=401, reason="auth")
    try:
        days, stale = await _schedule(app, user)
    except NetSchoolError as exc:
        return json_error(exc.user_message, status=503, reason=exc.reason.value)
    return web.json_response({"ok": True, "data": {
        "name": user.label, "timezone": user.timezone, "app_url": app.settings.web.miniapp_url,
        "generated_at": int(dt.datetime.now(dt.timezone.utc).timestamp()), "stale": stale, "days": days,
    }}, headers=NO_STORE)


@routes.get("/api/widget-key")
async def key_status(request: web.Request) -> web.Response:
    row = await WidgetKeyRepository(context(request).db).get(current_user(request).telegram_id)
    return web.json_response({"ok": True, "data": {
        "connected": bool(row), "used_at": row["used_at"] if row else None,
    }}, headers=NO_STORE)


@routes.post("/api/widget-key")
async def create_key(request: web.Request) -> web.Response:
    """Новый ключ и готовый скрипт с ним. Ключ показывается только здесь."""
    app, user = context(request), current_user(request)
    if not user.school.configured or not user.credentials.usable:
        return json_error("Сначала войдите в школу через /login", status=401, reason="login")
    key = await WidgetKeyRepository(app.db).create(user.telegram_id)
    script = (SCRIPT.read_text(encoding="utf-8")
              .replace("__SERVER__", app.settings.web.miniapp_url.rstrip("/"))
              .replace("__KEY__", key))
    return web.json_response({"ok": True, "data": {"connected": True, "script": script}}, headers=NO_STORE)


@routes.delete("/api/widget-key")
async def revoke_key(request: web.Request) -> web.Response:
    await WidgetKeyRepository(context(request).db).revoke(current_user(request).telegram_id)
    return web.json_response({"ok": True}, headers=NO_STORE)
