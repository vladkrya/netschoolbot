"""Заметки ученика, второй родитель, выгрузка оценок и настройки напоминаний.

Те же возможности, что в боте (/family, /export, /settings), — для приложения.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict, replace
from typing import Any

from aiohttp import web
from aiogram.types import BufferedInputFile

from ..accounts import has_telegram
from ..domain.models import clamp_reminder_hour
from ..exporting import SCOPES, export_grades
from ..netschool.errors import NetSchoolError
from .api import BOT, _content_disposition, context, current_user, json_error

logger = logging.getLogger("netschoolbot.web")

routes = web.RouteTableDef()

# Заметки дальше этого окна в обе стороны не запрашиваются.
NOTES_MAX_RANGE = dt.timedelta(days=400)


async def _body(request: web.Request) -> dict[str, Any] | None:
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@routes.post("/api/settings/reminder")
async def reminder_settings(request: web.Request) -> web.Response:
    """Час напоминания о несделанных заданиях (и о контрольных)."""
    app, user = context(request), current_user(request)
    body = await _body(request)
    try:
        hour = clamp_reminder_hour(int((body or {}).get("hour")))
    except (TypeError, ValueError):
        return json_error("Некорректное время")
    updated = await app.users.save(replace(user, notifications=replace(user.notifications, reminder_hour=hour)))
    return web.json_response({"ok": True, "data": asdict(updated.notifications)})


@routes.post("/api/settings/marks-filter")
async def marks_filter_settings(request: web.Request) -> web.Response:
    """О каких новых оценках сообщать: пустой список — обо всех."""
    app, user = context(request), current_user(request)
    body = await _body(request)
    values = (body or {}).get("values")
    if not isinstance(values, list) or not all(v in (2, 3, 4, 5) for v in values):
        return json_error("Некорректный набор оценок")
    chosen = tuple(sorted(set(values)))
    if set(chosen) >= {2, 3, 4, 5}:
        chosen = ()
    updated = await app.users.save(replace(user, notifications=replace(user.notifications, marks_filter=chosen)))
    return web.json_response({"ok": True, "data": asdict(updated.notifications)})


@routes.get("/api/notes")
async def list_notes(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    today = user.today()
    start = _date(request.query.get("from", "")) or today - dt.timedelta(days=7)
    end = _date(request.query.get("to", "")) or today + dt.timedelta(days=60)
    if end < start or end - start > NOTES_MAX_RANGE:
        return json_error("Некорректный период")
    items = await app.notes.between(user.telegram_id, start, end)
    return web.json_response({"ok": True, "data": {"items": items}}, headers={"Cache-Control": "no-store"})


@routes.post("/api/notes")
async def add_note(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    body = await _body(request) or {}
    day = _date(body.get("date", ""))
    text = str(body.get("text") or "")
    subject = str(body.get("subject") or "")
    if day is None or not text.strip():
        return json_error("Нужны дата и текст")
    note = await app.notes.add(user.telegram_id, day, text, subject)
    if note is None:
        return json_error("Пустая заметка")
    return web.json_response({"ok": True, "data": note})


@routes.post("/api/notes/{note_id:\\d+}")
async def update_note(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    body = await _body(request) or {}
    note_id = int(request.match_info["note_id"])
    if body.get("delete"):
        ok = await app.notes.delete(user.telegram_id, note_id)
    else:
        done = body.get("done")
        text = body.get("text")
        ok = await app.notes.update(
            user.telegram_id, note_id,
            done=bool(done) if done is not None else None,
            text=str(text) if text is not None else None,
        )
    if not ok:
        return json_error("Заметка не найдена", status=404)
    return web.json_response({"ok": True})


async def _bot_username(request: web.Request) -> str:
    bot = request.config_dict.get(BOT)
    if bot is None:
        return ""
    try:
        return (await bot.me()).username or ""
    except Exception:  # noqa: BLE001
        return ""


@routes.get("/api/family")
async def family_info(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    following = []
    for owner_id in await app.family.following(user.telegram_id):
        owner = await app.users.get(owner_id)
        following.append({"id": owner_id, "name": owner.label if owner else f"ID {owner_id}"})
    return web.json_response({"ok": True, "data": {
        "followers": [{"id": f["id"], "name": f["name"]} for f in await app.family.followers(user.telegram_id)],
        "following": following,
    }}, headers={"Cache-Control": "no-store"})


@routes.post("/api/family/invite")
async def family_invite(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    if not user.school.configured:
        return json_error("Сначала войдите в «Сетевой город»")
    username = await _bot_username(request)
    if not username:
        return json_error("Бот сейчас недоступен — попробуйте позже", status=503)
    code = await app.family.issue_invite(user.telegram_id)
    return web.json_response({"ok": True, "data": {"url": f"https://t.me/{username}?start=f_{code}"}})


@routes.post("/api/family/remove")
async def family_remove(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    body = await _body(request) or {}
    try:
        other = int(body.get("id"))
    except (TypeError, ValueError):
        return json_error("Некорректный запрос")
    if body.get("leave"):
        await app.family.remove(other, user.telegram_id)
    else:
        await app.family.remove(user.telegram_id, other)
    return await family_info(request)


@routes.get("/api/export.xlsx")
async def export_download(request: web.Request) -> web.Response:
    """Оценки файлом — для браузера и установленного приложения."""
    app, user = context(request), current_user(request)
    scope = request.query.get("scope", "period")
    if scope not in SCOPES:
        return json_error("Некорректный период")
    data, name = await export_grades(app, user, scope)
    return web.Response(body=data, headers={
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": _content_disposition(name).replace("inline;", "attachment;", 1),
        "Cache-Control": "no-store",
    })


@routes.post("/api/export/send")
async def export_send(request: web.Request) -> web.Response:
    """Оценки файлом в чат с ботом: внутри Telegram скачать файл из окна нельзя."""
    app, user = context(request), current_user(request)
    body = await _body(request) or {}
    scope = body.get("scope", "period")
    if scope not in SCOPES:
        return json_error("Некорректный период")
    bot = request.config_dict.get(BOT)
    if bot is None or not has_telegram(user.telegram_id):
        return json_error("Telegram не привязан — скачайте файл", reason="no_telegram")
    try:
        data, name = await export_grades(app, user, scope)
    except NetSchoolError:
        raise
    await bot.send_document(
        user.telegram_id, BufferedInputFile(data, filename=name),
        caption="📊 Оценки: лист «Итоги» — средние по предметам, «Оценки» — все работы.",
    )
    return web.json_response({"ok": True})
