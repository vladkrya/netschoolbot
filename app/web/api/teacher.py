"""Учительский журнал: только просмотр классов, учеников и оценок."""

from __future__ import annotations

from aiohttp import web

from ...context import AppContext
from ...domain.models import User
from .base import _cached, context, current_user, json_error, routes


def _positive(query: web.MultiDictProxy[str], name: str) -> int | None:
    try:
        value = int(query[name])
    except (KeyError, TypeError, ValueError):
        return None
    return value if value > 0 else None


@routes.get("/api/teacher/journal/filter")
async def teacher_journal_filter(request: web.Request) -> web.Response:
    """Справочник классов, предметов и периодов учительского журнала."""
    async def produce(app: AppContext, user: User):
        return await app.diary.fetch_teacher_journal_filter(user)

    return await _cached(request, "teacher:journal:filter:v2", produce)


@routes.get("/api/teacher/journal")
async def teacher_journal(request: web.Request) -> web.Response:
    """Один read-only снимок журнала выбранного класса, предмета и периода."""
    values = {name: _positive(request.query, name)
              for name in ("class_id", "subject_group_id", "term_id", "year_id")}
    if any(value is None for value in values.values()):
        return json_error("Укажите класс, предмет, период и учебный год")
    try:
        grade = int(request.query.get("grade", "-1"))
    except ValueError:
        return json_error("Некорректная параллель")
    grade = grade if grade >= -1 else -1

    async def produce(app: AppContext, user: User):
        return await app.diary.fetch_teacher_journal(
            user, class_id=values["class_id"], subject_group_id=values["subject_group_id"],
            term_id=values["term_id"], year_id=values["year_id"], grade=grade,
        )

    section = "teacher:journal:" + ":".join(str(values[name]) for name in values) + f":{grade}"
    return await _cached(request, section, produce)
