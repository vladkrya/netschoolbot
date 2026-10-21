"""Домашние задания: список, отметки «выполнено», ответы ученика."""

from __future__ import annotations

import datetime as dt
import re

from aiohttp import web

from ...context import AppContext
from ...domain import formatting
from ...domain.models import ApiMode, User
from .base import _cached, context, current_user, json_error, routes
from .diary import _homework_to_json
from .mail import _safe_filename


ANSWER_MAX_TEXT = 10000
ANSWER_MAX_FILE_BYTES = 20 * 1024 * 1024


def _assignment_id(request: web.Request) -> int | None:
    try:
        return int(request.match_info["assignment_id"])
    except ValueError:
        return None


@routes.get("/api/homework/{assignment_id}/answer")
async def homework_answer(request: web.Request) -> web.Response:
    """Ответ ученика на задание. Не кэшируется: его только что могли изменить."""
    assignment_id = _assignment_id(request)
    if assignment_id is None:
        return json_error("Некорректное задание")
    app, user = context(request), current_user(request)
    if user.effective_api_mode is not ApiMode.MOBILE:
        return web.json_response({"ok": True, "data": {"available": False}})
    answer = await app.diary.fetch_answer(user, assignment_id)
    return web.json_response({"ok": True, "data": {"available": True, **answer}})


@routes.post("/api/homework/{assignment_id}/answer/text")
async def homework_answer_text(request: web.Request) -> web.Response:
    assignment_id = _assignment_id(request)
    if assignment_id is None:
        return json_error("Некорректное задание")
    try:
        text = str((await request.json()).get("text") or "").strip()
    except (ValueError, AttributeError):
        return json_error("Некорректный запрос")
    if len(text) > ANSWER_MAX_TEXT:
        return json_error("Ответ слишком длинный")
    app, user = context(request), current_user(request)
    await app.diary.set_answer_text(user, assignment_id, text)
    return web.json_response({"ok": True, "data": await app.diary.fetch_answer(user, assignment_id)})


@routes.post("/api/homework/{assignment_id}/answer/files")
async def homework_answer_file(request: web.Request) -> web.Response:
    """Приложить файл к ответу: multipart с полем `file` (один файл за запрос)."""
    assignment_id = _assignment_id(request)
    if assignment_id is None or not request.content_type.startswith("multipart/"):
        return json_error("Некорректный запрос")
    name, data, description = "", bytearray(), ""
    try:
        async for part in await request.multipart():
            if part.name == "file" and part.filename is not None and not name:
                name = _safe_filename(part.filename)
                while chunk := await part.read_chunk(64 * 1024):
                    data += chunk
                    if len(data) > ANSWER_MAX_FILE_BYTES:
                        return json_error(f"Файл больше {ANSWER_MAX_FILE_BYTES // 1024 // 1024} МБ")
            elif part.name == "description":
                description = (await part.text()).strip()[:255]
    except (ValueError, AssertionError):
        return json_error("Некорректный запрос")
    if not name or not data:
        return json_error("Файл пустой")
    app, user = context(request), current_user(request)
    await app.diary.upload_answer_file(user, assignment_id, name, bytes(data), description)
    return web.json_response({"ok": True, "data": await app.diary.fetch_answer(user, assignment_id)})


@routes.delete("/api/homework/{assignment_id}/answer/files/{attachment_id}")
async def homework_answer_file_delete(request: web.Request) -> web.Response:
    assignment_id = _assignment_id(request)
    try:
        attachment_id = int(request.match_info["attachment_id"])
    except ValueError:
        attachment_id = None
    if assignment_id is None or attachment_id is None:
        return json_error("Некорректный запрос")
    app, user = context(request), current_user(request)
    await app.diary.delete_answer_file(user, assignment_id, attachment_id)
    return web.json_response({"ok": True, "data": await app.diary.fetch_answer(user, assignment_id)})


_HOMEWORK_KEY = re.compile(r"^[0-9a-f]{16}$")


@routes.get("/api/homework/done")
async def homework_done(request: web.Request) -> web.Response:
    """Отметки «выполнено» — отдельно от кэша заданий: меняются чаще."""
    app, user = context(request), current_user(request)
    keys = sorted(await app.state.done_homework(user.telegram_id))
    return web.json_response({"ok": True, "data": {"keys": keys}})


@routes.post("/api/homework/done")
async def set_homework_done(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    try:
        body = await request.json()
    except ValueError:
        return json_error("Некорректный запрос")
    key = str(body.get("key") or "")
    if not _HOMEWORK_KEY.match(key):
        return json_error("Некорректное задание")
    await app.state.set_homework_done(user.telegram_id, key, bool(body.get("done")))
    return web.json_response({"ok": True, "data": {"key": key, "done": bool(body.get("done"))}})


@routes.get("/api/homework/{assignment_id}/comment")
async def homework_comment(request: web.Request) -> web.Response:
    """Комментарий учителя к заданию. Не кэшируется: учитель может его
    дописать, а открывают задание по одному."""
    try:
        assignment_id = int(request.match_info["assignment_id"])
    except ValueError:
        return json_error("Некорректное задание")
    app, user = context(request), current_user(request)
    comment = await app.diary.fetch_assignment_comment(user, assignment_id)
    return web.json_response({"ok": True, "data": {"comment": comment}})


@routes.get("/api/homework")
async def homework(request: web.Request) -> web.Response:
    # Прошедшие задания — отдельным окном: обычно нужны «сейчас и вперёд».
    past = request.query.get("past") == "1"

    async def produce(app: AppContext, user: User):
        if past:
            today = user.today()
            items = await app.diary.fetch_period(
                user, today - dt.timedelta(weeks=4), today - dt.timedelta(days=1), today=today
            )
            items = [record for day in items for record in day.homework]
        else:
            items = await app.diary.fetch_homework(user, weeks_back=0, weeks_forward=2)
        observed = await app.state.observed_homework(user.telegram_id)
        return {
            # Та же форма, что у заданий внутри урока: экран задания один,
            # и раньше отсюда не доходили тип, вес, тема и время публикации.
            "items": [
                {**_homework_to_json(item, observed), "label": formatting.date_label(item.due_date)}
                for item in sorted(items, key=lambda h: (h.due_date, h.subject))
            ]
        }

    return await _cached(request, "homework:past" if past else "homework", produce)
