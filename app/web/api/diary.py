"""Дневник, календарь и всё, что приходит из «Сетевого города» вокруг уроков."""

from __future__ import annotations

import datetime as dt
import json

from aiohttp import web

from ...context import AppContext
from ...domain import formatting
from ...domain.calendar import build_ics
from ...domain.models import ApiMode, User
from ...notifications.outbox import mark_from_dict
from ...domain.records import DiaryDay, clean_content
from ...netschool.errors import NetSchoolError, Reason
from ...domain.periods import quarter_of
from .base import _cached, _refresh_cached_section, context, current_user, json_error, logger, routes


def _mark_to_json(record) -> dict:
    return {
        "subject": record.subject,
        "title": record.title,
        # Содержание работы без типа: «Тема» на экране оценки.
        "topic": clean_content(record.content),
        "type": record.assignment_type,
        "date": record.date.isoformat(),
        "mark": record.mark,
        "weight": record.weight,
        "numeric": record.numeric_mark,
        "published_at": record.published_at.isoformat() if record.published_at else None,
        "marked_at": record.marked_at.isoformat() if record.marked_at else None,
        "lesson_number": record.lesson_number,
        "comment": record.comment,
        # Четверть считается на сервере: правила одни и те же для бота и
        # приложения, дублировать их в JavaScript незачем.
        "quarter": quarter_of(record.date),
    }


def _homework_to_json(item, observed: dict[str, str] | None = None) -> dict:
    return {
        "subject": item.subject,
        "date": item.due_date.isoformat(),
        "type": item.assignment_type,
        "text": item.text,
        "attachments": [{"id": a.id, "name": a.name} for a in item.attachments],
        "observed_at": (observed or {}).get(item.identity, ""),
        "published_at": item.published_at.isoformat() if item.published_at else "",
        "weight": item.weight,
        "topic": item.topic,
        "key": item.key,
        "id": item.assignment_id,
        "comment": item.comment,
        "auto_done": item.nothing_to_do,
        "classwork": item.classwork,
    }


async def _teachers(app: AppContext, user: User, days: list[DiaryDay]) -> dict[str, str]:
    """Учителя по предметам, с долговременным кэшем.

    Дневник учителя не отдаёт — только карточка задания, по запросу на
    предмет. Учителя меняются редко, поэтому однажды найденный хранится,
    а спрашиваются только новые предметы.

    В мобильном режиме учитель уже лежит в самом уроке (`classmeetings`), и
    ходить за карточками заданий не нужно.
    """
    if user.effective_api_mode is ApiMode.MOBILE:
        return {}
    cached = await app.cache.get(user.telegram_id, "teachers")
    known: dict[str, str] = dict(cached[0]) if cached else {}
    wanted: dict[str, int] = {}
    for day in days:
        for lesson in day.lessons:
            if lesson.subject not in known and lesson.assignment_ids:
                wanted.setdefault(lesson.subject, lesson.assignment_ids[0])
    if wanted:
        try:
            found = await app.diary.fetch_teachers(user, wanted)
        except NetSchoolError as exc:
            logger.info("Учителя не получены для %s: %s", user.telegram_id, exc.reason)
            found = {}
        if found:
            known.update(found)
            await app.cache.put(user.telegram_id, "teachers", known)
    return known


def _day_to_json(
    day: DiaryDay, teachers: dict[str, str] | None = None,
    observed: dict[str, str] | None = None,
) -> dict:
    return {
        "date": day.day.isoformat(),
        "label": formatting.date_label(day.day),
        "weekday": day.day.weekday(),
        "lessons": [
            {
                "number": lesson.number,
                "subject": lesson.subject,
                "time": lesson.time_range,
                "start": lesson.start,
                "end": lesson.end,
                "room": lesson.room,
                "teacher": lesson.teacher or (teachers or {}).get(lesson.subject, ""),
                # Тема урока: карточка «Тема урока» на экране урока.
                "topic": getattr(lesson, "topic", ""),
                "absence": getattr(lesson, "absence", ""),
                "is_elective": lesson.is_elective,
                "distance_meeting_id": getattr(lesson, "distance_meeting_id", None),
                "marks": [_mark_to_json(m) for m in lesson.marks],
                "homework": [_homework_to_json(h, observed) for h in lesson.homework],
            }
            for lesson in day.lessons
        ],
        "marks": [_mark_to_json(record) for record in day.marks],
        "homework": [_homework_to_json(item, observed) for item in day.homework],
    }


@routes.get("/api/diary")
async def diary(request: web.Request) -> web.Response:
    """Дневник вокруг запрошенной недели.

    Отдаём не одну неделю, а окно из нескольких: переключение недель в
    приложении должно быть мгновенным, а не ждать похода в школьный сервер
    на каждое нажатие стрелки.
    """
    try:
        offset = int(request.query.get("week", "0"))
    except ValueError:
        offset = 0
    # Ограничение, чтобы случайный огромный параметр не заставил бота
    # перебирать сотни недель.
    offset = max(-30, min(30, offset))

    compact = request.query.get('window') == 'current'

    async def produce(app: AppContext, user: User):
        today = user.today() + dt.timedelta(weeks=offset)
        days = await app.diary.fetch_diary(user, weeks_back=0 if compact else 1, weeks_forward=0 if compact else 1, today=today)
        # Обновление вручную — тоже успешная проверка: время в настройках не отстаёт.
        await app.state.mark_watch_ok(user.telegram_id)
        if compact and user.effective_api_mode is not ApiMode.MOBILE:
            cached_teachers = await app.cache.get(user.telegram_id, "teachers")
            teachers = dict(cached_teachers[0]) if cached_teachers else {}
            wanted = {lesson.subject: lesson.assignment_ids[0]
                      for day in days for lesson in day.lessons
                      if lesson.subject not in teachers and lesson.assignment_ids}
            if wanted:
                async def refresh_teachers(ctx, owner):
                    found = await ctx.diary.fetch_teachers(owner, wanted)
                    return {**teachers, **found}
                _refresh_cached_section(app, user, "teachers", refresh_teachers, bool)
        else:
            teachers = await _teachers(app, user, days)
        observed = await app.state.observed_homework(user.telegram_id)
        return {"days": [_day_to_json(day, teachers, observed) for day in days], "week": offset}

    # Каждое окно кэшируется отдельно, иначе соседние недели вытесняли бы
    # друг друга и стрелки снова упирались бы в сеть.
    section = f"diary:{offset}"
    return await _cached(request, f"{section}:current" if compact else section, produce,
                         fallback_section=section if compact else None)


CALENDAR_FRESH = dt.timedelta(hours=1)


@routes.get("/api/calendar.ics")
async def calendar_feed(request: web.Request) -> web.Response:
    """Подписка на расписание в календаре телефона (по личной ссылке с токеном).

    Календари переспрашивают сами, поэтому ответ хранится час, а при
    недоступной школе отдаётся последний удачный.
    """
    app, user = context(request), current_user(request)
    cached = await app.cache.get(user.telegram_id, "calendar")
    now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if cached is not None and now_utc - cached[1] < CALENDAR_FRESH:
        text = cached[0]
    else:
        try:
            days = await app.diary.fetch_diary(user, weeks_back=1, weeks_forward=3, today=user.today())
            text = build_ics(days, user.tz, name=f"Уроки · {user.label}")
            await app.cache.put(user.telegram_id, "calendar", text)
        except NetSchoolError:
            if cached is None:
                raise
            text = cached[0]
    return web.Response(
        text=text,
        content_type="text/calendar",
        charset="utf-8",
        headers={"Content-Disposition": 'inline; filename="raspisanie.ics"'},
    )


@routes.get("/api/calendar-link")
async def calendar_link(request: web.Request) -> web.Response:
    """Адрес подписки: webcal:// открывает календарь телефона сразу."""
    app, user = context(request), current_user(request)
    token = await app.miniapp.issue_calendar_token(user.telegram_id)
    url = f"{app.settings.web.miniapp_url}/api/calendar.ics?token={token}"
    return web.json_response(
        {"ok": True, "data": {"url": url, "webcal": "webcal://" + url.split("://", 1)[1]}}
    )


@routes.get("/api/announcements")
async def announcements(request: web.Request) -> web.Response:
    async def produce(app: AppContext, user: User):
        return {"items": await app.diary.fetch_announcements(user)}

    return await _cached(request, "announcements", produce)


# Звонки меняются раз в год, а в мобильном режиме за ними нужен разовый
# веб-вход — держим их сутки.
BELLS_FRESH_SECONDS = 24 * 3600


@routes.get("/api/bells")
async def bells(request: web.Request) -> web.Response:
    """Расписание звонков школы. `items: null` — звонков нет (вход через
    Госуслуги в мобильном режиме, сбой): приложение соберёт их из уроков."""

    async def produce(app: AppContext, user: User):
        return {"items": await app.diary.fetch_bells(user)}

    return await _cached(
        request, "bells", produce,
        cacheable=lambda payload: bool(payload.get("items")), fresh_seconds=BELLS_FRESH_SECONDS,
    )


@routes.get("/api/distance/{meeting_id}/join")
async def distance_join(request: web.Request) -> web.Response:
    """Ссылка на дистанционный урок. Не кэшируется: ссылка разовая."""
    try:
        meeting_id = int(request.match_info["meeting_id"])
    except ValueError:
        return json_error("Некорректный урок")
    app, user = context(request), current_user(request)
    url = await app.diary.fetch_distance_link(user, meeting_id)
    if not url:
        return json_error("Школьный сервер не дал ссылку на урок", status=503)
    return web.json_response({"ok": True, "data": {"url": url}})


@routes.get("/api/events")
async def events(request: web.Request) -> web.Response:
    """Олимпиады и конкурсы ученика — только мобильный режим."""
    if current_user(request).effective_api_mode is not ApiMode.MOBILE:
        return web.json_response({"ok": True, "data": {"available": False, "items": []}, "stale": False})

    async def produce(app: AppContext, user: User):
        return {"available": True, "items": await app.diary.fetch_events(user)}

    return await _cached(request, "events", produce)


@routes.get("/api/references/types")
async def reference_types(request: web.Request) -> web.Response:
    """Справки, которые можно заказать. Ученику сервер отказывает — `available: false`."""
    app, user = context(request), current_user(request)
    if user.effective_api_mode is not ApiMode.MOBILE:
        return web.json_response({"ok": True, "data": {"available": False, "items": []}})
    try:
        items = await app.diary.fetch_reference_types(user)
    except NetSchoolError as exc:
        if exc.reason is not Reason.UNKNOWN:
            raise
        return web.json_response({"ok": True, "data": {"available": False, "items": [], "message": exc.user_message}})
    return web.json_response({"ok": True, "data": {"available": True, "items": items}})


@routes.post("/api/references")
async def order_reference(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    try:
        body = await request.json()
        type_id = int(body.get("type_id"))
    except (ValueError, TypeError):
        return json_error("Выберите справку")
    known = {item["id"] for item in await app.diary.fetch_reference_types(user)}
    if type_id not in known:
        return json_error("Такой справки нет")
    await app.diary.order_reference(user, type_id)
    return web.json_response({"ok": True})


@routes.get("/api/history")
async def mark_history(request: web.Request) -> web.Response:
    """Что происходило с оценками: новые, исправленные, удалённые."""
    app, user = context(request), current_user(request)
    try:
        before = int(request.query["before"]) if "before" in request.query else None
    except ValueError:
        before = None
    rows = await app.state.mark_history(user.telegram_id, limit=50, before_id=before)
    items = []
    for row in rows:
        try:
            record = mark_from_dict(json.loads(row["payload"]))
        except (ValueError, KeyError, TypeError):
            continue
        items.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "subject": record.subject,
                "date": record.date.isoformat(),
                "title": record.title,
                # Номер урока: по нажатию на запись открывается сам урок.
                "lesson": record.lesson_number,
                "mark": record.mark,
                "old": row["old_mark"],
                "new": row["new_mark"],
                "seen": row["created_at"].replace(" ", "T") + "Z",
            }
        )
    return web.json_response({"ok": True, "data": {"items": items}})
