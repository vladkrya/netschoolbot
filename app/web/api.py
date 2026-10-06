"""HTTP API мини-приложения.

Раньше это был файл на 2953 строки, в котором каждый обработчик сам ходил в
«Сетевой город» через `asyncio.run()` — то есть на каждый запрос создавался
новый event loop, а соединения не переиспользовались. Теперь обработчики
асинхронные и работают через общий сервис дневника.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import mimetypes
import re
from urllib.parse import quote
from dataclasses import asdict, replace
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

from ..context import AppContext
from ..domain import formatting
from ..domain.calendar import build_ics
from ..domain.models import TIMEZONES, ApiMode, User, normalize
from ..notifications.outbox import mark_from_dict
from ..domain.records import DiaryDay, clean_content
from ..netschool.errors import NetSchoolError, Reason
from ..domain.periods import (
    PERIOD_KEYS,
    SCHEME_BOUNDS,
    PeriodScheme,
    Quarter,
    period_of,
    periods,
    periods_from_terms,
    quarter_of,
    term_key,
)
from .telegram_auth import InitDataError, verify_init_data

logger = logging.getLogger("netschoolbot.web")

routes = web.RouteTableDef()

# Типизированные ключи вместо строк: aiohttp так избегает конфликтов имён
# между приложением и подключёнными к нему библиотеками.
CONTEXT = web.AppKey("context", AppContext)
BOT = web.AppKey("bot", object)
# AppKey предназначен для Application; Request хранит строковые ключи.
USER = "netschool_user"

# Не задерживаем показ сохранённых данных ожиданием школьного сервера.
CACHE_RESPONSE_WAIT = 0.2
# Обновление жестом — человек явно просит свежие данные и видит индикатор,
# поэтому ждём школьный сервер всерьёз. Меньше таймаута запроса в браузере.
REFRESH_RESPONSE_WAIT = 60


def _cache_owner(user: User) -> tuple:
    return user.school, user.credentials, user.selected_student_id, user.effective_api_mode


def _refresh_cached_section(app, user, section, producer, cacheable):
    key = user.telegram_id, section, _cache_owner(user)
    if task := app.cache_refreshes.get(key):
        return task

    async def refresh():
        payload = await producer(app, user)
        current = await app.users.get(user.telegram_id)
        # Переключение ребёнка или выход во время запроса не должны вернуть
        # в очищенный кэш данные прежнего аккаунта.
        if current is not None and _cache_owner(current) == _cache_owner(user) and cacheable(payload):
            await app.cache.put(user.telegram_id, section, payload)
        return payload

    task = asyncio.create_task(refresh(), name=f"cache:{section}")
    app.cache_refreshes[key] = task

    def done(completed):
        if app.cache_refreshes.get(key) is completed:
            del app.cache_refreshes[key]
        if not completed.cancelled() and (error := completed.exception()):
            logger.info("Обновление кэша %s не завершилось: %s", section, type(error).__name__)

    task.add_done_callback(done)
    return task


def context(request: web.Request) -> AppContext:
    return request.app[CONTEXT]


def current_user(request: web.Request) -> User:
    """Пользователь запроса. Проставляется middleware авторизации."""
    return request[USER]


def json_error(message: str, *, status: int = 400, reason: str = "") -> web.Response:
    return web.json_response(
        {"ok": False, "error": message, "reason": reason}, status=status
    )


@web.middleware
async def error_middleware(request: web.Request, handler):
    """Единая обработка ошибок API.

    Раньше каждый обработчик ловил исключения сам, по-своему, и в браузер
    улетал то JSON, то HTML-страница ошибки Flask, то текст исключения.
    """
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except NetSchoolError as exc:
        status = 401 if exc.reason.needs_relogin else 503
        return json_error(exc.user_message, status=status, reason=exc.reason.value)
    except Exception:
        logger.exception("Ошибка в %s %s", request.method, request.path)
        return json_error("Внутренняя ошибка. Она записана, я разберусь.", status=500)


async def resolve_identity(request: web.Request) -> tuple[int | None, str]:
    """Кто делает запрос. Возвращает (telegram_id, способ).

    Два пути входа, и порядок между ними важен:

    1. `initData` от Telegram — подписана токеном бота, подделать её нельзя.
       Проверяется первой: если приложение открыто внутри Telegram, это
       самое надёжное удостоверение, и никакой токен из ссылки его не
       перебивает.
    2. Постоянный токен из ссылки `/app` — для установленного на домашний
       экран приложения, которое открывается вне Telegram и initData не
       получает.
    """
    app = context(request)

    init_data = (
        request.headers.get("X-Telegram-Init-Data") or request.query.get("tgWebAppData") or ""
    ).strip()
    if init_data:
        try:
            tg_user = verify_init_data(init_data, app.settings.telegram.bot_token)
        except InitDataError as exc:
            logger.info("initData отклонена: %s", exc)
        else:
            return tg_user.id, "telegram"

    token = (
        request.headers.get("X-Netschool-Token")
        or request.query.get("token")
        or request.cookies.get("netschool_token")
        or ""
    ).strip()
    if token:
        return await app.miniapp.resolve_token(token), "token"

    return None, "none"


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Опознание пользователя для запросов к API."""
    # /captcha-proxy/api/v1/… — API капчи, а не наше: его зовёт виджет без входа.
    if "/api/" not in request.path or "/captcha-proxy/" in request.path:
        return await handler(request)

    telegram_id, method = await resolve_identity(request)
    if telegram_id is None:
        return json_error(
            "Не удалось вас опознать. Откройте приложение кнопкой в боте "
            "или запросите новую ссылку: /app",
            status=401,
            reason="auth",
        )

    user = await context(request).users.get(telegram_id)
    if user is None:
        return json_error(
            "Вы ещё не входили в «Сетевой город». Откройте бота и выполните /login",
            status=401,
            reason="login",
        )

    request[USER] = user
    response = await handler(request)

    # Токен из ссылки переносим в cookie, чтобы он не тянулся в каждом URL
    # и не утекал в историю браузера и в заголовок Referer.
    if method == "token" and request.query.get("token"):
        response.set_cookie(
            "netschool_token",
            request.query["token"],
            max_age=365 * 24 * 3600,
            httponly=True,
            samesite="Lax",
            secure=request.url.scheme == "https",
        )
    return response


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
                "marks": [_mark_to_json(m) for m in lesson.marks],
                "homework": [_homework_to_json(h, observed) for h in lesson.homework],
            }
            for lesson in day.lessons
        ],
        "marks": [_mark_to_json(record) for record in day.marks],
        "homework": [_homework_to_json(item, observed) for item in day.homework],
    }


async def _cached(request: web.Request, section: str, producer, *, cacheable=lambda payload: True):
    """Отдать сохранённые данные сразу, обновив устаревшие в фоне.

    Если школьный сервер недоступен, отдаётся устаревший кэш с пометкой —
    приложение продолжает работать, а не показывает пустой экран.
    `cacheable` не пускает в кэш ответ, который лишь сообщает «данных нет»:
    иначе разовый сбой запомнился бы на весь срок свежести кэша.
    """
    app = context(request)
    user = current_user(request)
    cached = await app.cache.get(user.telegram_id, section)
    fresh_for = dt.timedelta(seconds=app.settings.web.cache_fresh_seconds)

    if cached is not None and not request.query.get("refresh"):
        payload, updated = cached
        # updated_at пишется SQLite-функцией datetime('now') — это UTC.
        # С локальным now() в Москве кэш всегда был «старше на 3 часа»,
        # и каждое открытие приложения шло в школьный сервер.
        now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        if now_utc - updated < fresh_for:
            # Возраст кэша нужен приложению: при входе оно показывает сохранённое
            # сразу, а заметно устаревшее тут же перепроверяет у школьного сервера.
            age = max(0, int((now_utc - updated).total_seconds()))
            return web.json_response({"ok": True, "data": payload, "stale": False, "age": age})

    task = _refresh_cached_section(app, user, section, producer, cacheable)
    if cached is not None:
        # Раньше и обновление жестом получало кэш через 0,2 с, а экран после
        # фоновой загрузки не перерисовывался — «обновление» ничего не меняло.
        wait = REFRESH_RESPONSE_WAIT if request.query.get("refresh") else CACHE_RESPONSE_WAIT
        completed, _ = await asyncio.wait({task}, timeout=wait)
        if not completed:
            return web.json_response(
                {"ok": True, "data": cached[0], "stale": True, "refreshing": True}
            )
    try:
        # Отмена HTTP-запроса не прерывает общее обновление для других вкладок.
        payload = await asyncio.shield(task)
    except NetSchoolError as exc:
        if cached is None:
            raise
        logger.info("Отдаю устаревший кэш %s для %s: %s", section, user.telegram_id, exc.reason)
        return web.json_response(
            {"ok": True, "data": cached[0], "stale": True, "reason": exc.reason.value}
        )

    return web.json_response({"ok": True, "data": payload, "stale": False})


def _utc_iso(moment: dt.datetime | None) -> str | None:
    """Время из базы (UTC без пояса) — в ISO с «Z», чтобы браузер показал местное."""
    return moment.isoformat(timespec="seconds") + "Z" if moment else None


@routes.get("/api/profile")
async def profile(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    status = await app.state.watch_status(user.telegram_id)
    return web.json_response(
        {
            "ok": True,
            "data": {
                "name": user.label,
                "school": user.school.name,
                "students": [
                    {"id": s.id, "name": s.name, "current": s.id == user.selected_student_id}
                    for s in user.available_students
                ],
                "notifications": asdict(user.notifications),
                # Тумблер пропусков показывается только там, где есть отметки посещаемости.
                "api_mode": user.effective_api_mode.value,
                "interval_minutes": user.check_interval // 60,
                "quiet_hours": user.quiet_hours.as_text(),
                "enabled": user.enabled,
                "timezone": user.timezone,
                "periods": (await app.miniapp.prefs(user.telegram_id)).get("periods")
                or {"scheme": "auto"},
                "timezones": [{"id": key, "name": city} for key, city in TIMEZONES],
                "status": {
                    "running": user.telegram_id in app.watchers.running,
                    "last_ok_at": _utc_iso(status.last_ok_at),
                    "last_error_at": _utc_iso(status.last_error_at),
                    "last_error": status.last_error,
                    "next_check_at": _utc_iso(status.next_check_at),
                    "queued": status.queued,
                },
            },
        }
    )


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

    async def produce(app: AppContext, user: User):
        today = user.today() + dt.timedelta(weeks=offset)
        days = await app.diary.fetch_diary(user, weeks_back=1, weeks_forward=1, today=today)
        teachers = await _teachers(app, user, days)
        observed = await app.state.observed_homework(user.telegram_id)
        return {"days": [_day_to_json(day, teachers, observed) for day in days], "week": offset}

    # Каждое окно кэшируется отдельно, иначе соседние недели вытесняли бы
    # друг друга и стрелки снова упирались бы в сеть.
    return await _cached(request, f"diary:{offset}", produce)


@routes.get("/api/marks")
async def marks(request: web.Request) -> web.Response:
    """Оценки за учебный год — экран «Итоги».

    Берётся весь год до конца текущей четверти, а не пять недель, как раньше:
    таблице «предмет × четверть» нужны все четверти, а полосе прогресса —
    число уроков, включая ещё не прошедшие.
    """

    selected_year_id = None
    if "year" in request.query:
        try:
            selected_year_id = int(request.query["year"])
            if selected_year_id <= 0:
                raise ValueError
        except ValueError:
            return json_error("Некорректный учебный год")

    async def produce(app: AppContext, user: User):
        available_years = await app.diary.fetch_school_years(user)
        selected = next((y for y in available_years or [] if y["id"] == selected_year_id), None)
        if selected_year_id is not None and selected is None:
            raise NetSchoolError(Reason.UNKNOWN, "Этот учебный год недоступен для выбранного ученика")
        if selected and not selected["current"]:
            return await _archived_totals(app, user, selected, available_years)
        current = next((y for y in available_years or [] if y["current"]), None)
        today = user.today()
        year, source = await _user_periods(app, user, today)
        now_quarter = next((q for q in year if q.covers(today)), None)
        if now_quarter is None:
            # Каникулы между периодами или лето — последний начавшийся.
            started = [q for q in year if q.start <= today]
            now_quarter = started[-1] if started else year[0]
        days = await app.diary.fetch_period(user, year[0].start, now_quarter.end, today=today)

        by_subject: dict[str, list] = {}
        lessons: dict[str, dict[str, dict[str, int]]] = {}
        teachers: dict[str, dict[str, int]] = {}
        # Отметки посещаемости: {предмет: {четверть: {код: сколько}}}.
        absences: dict[str, dict[str, dict[str, int]]] = {}
        # Тема урока к каждой оценке: экран оценки показывает её как «Тему».
        # Ключ — сама запись: номер урока бывает перенумерован (см. mapping).
        lesson_topics: dict = {}
        elective_subjects = {normalize(lesson.subject) for day in days for lesson in day.lessons if lesson.is_elective}
        regular_subjects = {normalize(lesson.subject) for day in days for lesson in day.lessons if not lesson.is_elective}
        excluded_subjects = elective_subjects - regular_subjects
        elective_marks = {record for day in days for lesson in day.lessons
                          if lesson.is_elective for record in lesson.marks}
        for day in days:
            key = period_of(day.day, year)
            for record in day.marks:
                if record in elective_marks or normalize(record.subject) in excluded_subjects:
                    continue
                by_subject.setdefault(record.subject, []).append(record)
            for lesson in day.lessons:
                if lesson.is_elective:
                    continue
                for record in lesson.marks if lesson.topic else ():
                    lesson_topics[record] = lesson.topic
                counts = lessons.setdefault(lesson.subject, {}).setdefault(
                    key or "", {"total": 0, "done": 0}
                )
                counts["total"] += 1
                if day.day <= today:
                    counts["done"] += 1
                code = (lesson.absence or "").strip()
                if code:
                    counts_by_code = absences.setdefault(lesson.subject, {}).setdefault(key or "", {})
                    counts_by_code[code] = counts_by_code.get(code, 0) + 1
                if lesson.teacher:
                    seen = teachers.setdefault(lesson.subject, {})
                    seen[lesson.teacher] = seen.get(lesson.teacher, 0) + 1

        records = [record for items in by_subject.values() for record in items]
        found_teachers = await _teachers(app, user, days)
        # Официальные четвертные и годовые оценки — только мобильный режим;
        # без них таблица остаётся на расчётных средних.
        totals = await app.diary.fetch_totals(user) or {}
        totals_by_name = {normalize(name): (name, item) for name, item in totals.items()
                          if normalize(name) not in excluded_subjects}
        known = {normalize(name) for name in set(by_subject) | set(lessons)}
        extra = {name for key, (name, _) in totals_by_name.items() if key not in known}

        subjects = []
        for subject in sorted(set(by_subject) | set(lessons) | extra):
            items = by_subject.get(subject, [])
            teacher_counts = teachers.get(subject, {})
            subjects.append(
                {
                    "subject": subject,
                    "average": formatting.weighted_average(items),
                    "count": len(items),
                    # Общая функция, а не свой набор полей: своя копия уже
                    # разошлась с остальными и потеряла четверть.
                    "marks": [
                        {
                            **_mark_to_json(r),
                            "quarter": period_of(r.date, year),
                            "lesson_topic": lesson_topics.get(r, ""),
                        }
                        for r in sorted(items, key=lambda r: r.date)
                    ],
                    "lessons": lessons.get(subject, {}),
                    "absences": absences.get(subject, {}),
                    "totals": totals_by_name.get(normalize(subject), (None, None))[1],
                    "teacher": (
                        max(teacher_counts, key=teacher_counts.get)
                        if teacher_counts
                        else found_teachers.get(subject, "")
                    ),
                }
            )
        return {
            "years": available_years or [],
            "year_id": current["id"] if current else None,
            "class_name": current["class_name"] if current else "",
            "archived": False,
            "subjects": subjects,
            "average": formatting.weighted_average(records),
            "current_quarter": now_quarter.key,
            # Экран по этому признаку решает, спрашивать ли у сервера
            # аналитику по предмету (средний балл класса).
            "mobile": user.effective_api_mode is ApiMode.MOBILE,
            "has_totals": bool(totals_by_name),
            "quarters": [
                {
                    "key": q.key,
                    "label": q.label,
                    "short": q.short,
                    "start": q.start.isoformat(),
                    "end": q.end.isoformat(),
                }
                for q in year
            ],
            # Откуда взяты границы: school — из «Сетевого города», custom —
            # свои даты, default — общепринятые.
            "periods_source": source,
            "year": f"{year[0].start.year}/{year[0].start.year + 1}",
        }

    section = f"marks:year:{selected_year_id}" if selected_year_id is not None else "marks:year"
    return await _cached(request, section, produce)


async def _archived_totals(app: AppContext, user: User, selected: dict, years: list) -> dict:
    """Архив берётся из итогов выбранного года, без запросов текущего дневника."""
    year_id = selected["id"]
    totals = await app.diary.fetch_totals(user, school_year_id=year_id)
    if totals is None:
        raise NetSchoolError(Reason.SERVER_UNAVAILABLE, "Не удалось получить итоги за выбранный год")
    terms = await app.diary.fetch_terms(user, school_year_id=year_id) or []
    columns = {}
    for term in terms:
        key = term_key(term.get("name"))
        if key:
            columns[key] = {"key": key, "label": str(term["name"]), "short": str(term["name"]),
                            "start": str(term.get("startDate") or "")[:10],
                            "end": str(term.get("endDate") or "")[:10]}
    for subject in totals.values():
        for key in subject["terms"]:
            if key not in columns:
                label = {"q": "четверть", "h": "полугодие", "t": "триместр"}[key[0]]
                columns[key] = {"key": key, "label": f"{key[1:]} {label}", "short": f"{key[1:]} {label}"}
    for key, column in columns.items():
        count = {"q": 4, "h": 2, "t": 3}[key[0]]
        column["short"] = f"{key[1:]}/{count}" + ({"h": " п/г", "t": " тр."}.get(key[0], ""))
    return {
        "years": years, "year_id": year_id, "year": selected["name"],
        "class_name": selected["class_name"], "archived": True, "mobile": True,
        "has_totals": bool(totals), "current_quarter": None, "average": None,
        "periods_source": "school", "quarters": sorted(columns.values(), key=lambda q: q["key"]),
        "subjects": [{"subject": name, "marks": [], "lessons": {}, "absences": {},
                      "teacher": "", "average": None, "count": 0, "totals": item}
                     for name, item in sorted(totals.items())],
    }


async def _user_periods(app: AppContext, user: User, today: dt.date) -> tuple[list[Quarter], str]:
    """Периоды для «Итогов»: свои даты, периоды школы или общепринятые."""
    setting = (await app.miniapp.prefs(user.telegram_id)).get("periods") or {}
    scheme_name = setting.get("scheme") or "auto"
    if scheme_name != "auto":
        scheme = PeriodScheme(scheme_name)
        starts = setting.get("starts")
        parsed = [tuple(int(x) for x in item.split("-")) for item in starts] if starts else None
        return periods(today, scheme, parsed), "custom" if parsed else "default"
    try:
        terms = await app.diary.fetch_terms(user)
    except NetSchoolError:
        terms = None
    found = periods_from_terms(terms or [], today)
    if found:
        return found, "school"
    return periods(today), "default"


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
    token = await app.miniapp.issue_token(user.telegram_id)
    url = f"{app.settings.web.miniapp_url}/api/calendar.ics?token={token}"
    return web.json_response(
        {"ok": True, "data": {"url": url, "webcal": "webcal://" + url.split("://", 1)[1]}}
    )


@routes.get("/api/subject")
async def subject_stats(request: web.Request) -> web.Response:
    """Аналитика по предмету за четверть: средний балл класса и распределение оценок.

    Данные есть только в мобильном режиме. Иначе, а также когда сервер не
    вернул аналитику, ответ — `{"available": false}`: экран просто не
    показывает эту строку.
    """
    subject = (request.query.get("subject") or "").strip()[:120]
    quarter = (request.query.get("quarter") or "").strip()
    if not subject or quarter not in PERIOD_KEYS:
        return json_error("Не указан предмет или четверть")
    if current_user(request).effective_api_mode is not ApiMode.MOBILE:
        return web.json_response({"ok": True, "data": {"available": False}, "stale": False})

    async def produce(app: AppContext, user: User):
        stats = await app.diary.fetch_subject_stats(user, subject, quarter)
        return {"available": True, **stats} if stats else {"available": False}

    return await _cached(
        request, f"subject:{quarter}:{normalize(subject)}", produce,
        cacheable=lambda payload: payload.get("available", False),
    )


@routes.get("/api/announcements")
async def announcements(request: web.Request) -> web.Response:
    async def produce(app: AppContext, user: User):
        return {"items": await app.diary.fetch_announcements(user)}

    return await _cached(request, "announcements", produce)


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


@routes.get("/api/mail")
async def mail(request: web.Request) -> web.Response:
    async def produce(app: AppContext, user: User):
        return {"messages": await app.diary.fetch_mail(user, limit=50)}

    return await _cached(request, "mail", produce)


@routes.get("/api/mail/recipients")
async def mail_recipients(request: web.Request) -> web.Response:
    async def produce(app: AppContext, user: User):
        return {"items": await app.diary.fetch_recipients(user)}

    return await _cached(request, "mail:recipients", produce)


# Вложения к письму. Общий предел держим меньше client_max_body_size у nginx.
MAIL_MAX_FILES = 5
MAIL_MAX_FILE_BYTES = 10 * 1024 * 1024
MAIL_MAX_TOTAL_BYTES = 15 * 1024 * 1024


class _BadMail(Exception):
    """Письмо в запросе не годится; текст показывается пользователю."""


def _safe_filename(name: str | None) -> str:
    """Имя файла без пути и управляющих символов; пустое заменяется запасным."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    return base[:150] or "file"


async def _read_mail_request(request: web.Request) -> tuple[list[str], str, str, list[tuple[str, bytes]]]:
    """Разобрать запрос отправки: JSON без файлов или multipart с файлами.

    Multipart читается потоком с проверкой размера: слишком большой файл
    отвергается, не оседая целиком в памяти.
    """
    if not request.content_type.startswith("multipart/"):
        try:
            body = await request.json()
        except ValueError as exc:
            raise _BadMail("Некорректный запрос") from exc
        to = [str(item) for item in (body.get("to") or []) if str(item).strip()]
        return to, str(body.get("subject") or "").strip(), str(body.get("text") or "").strip(), []

    to: list[str] = []
    fields: dict[str, str] = {}
    files: list[tuple[str, bytes]] = []
    total = 0
    try:
        reader = await request.multipart()
        async for part in reader:
            if part.name == "file" and part.filename is not None:
                if len(files) >= MAIL_MAX_FILES:
                    raise _BadMail(f"Не больше {MAIL_MAX_FILES} файлов")
                data = bytearray()
                while chunk := await part.read_chunk(64 * 1024):
                    data += chunk
                    total += len(chunk)
                    if len(data) > MAIL_MAX_FILE_BYTES:
                        raise _BadMail(f"Файл больше {MAIL_MAX_FILE_BYTES // 1024 // 1024} МБ")
                    if total > MAIL_MAX_TOTAL_BYTES:
                        raise _BadMail(f"Файлы больше {MAIL_MAX_TOTAL_BYTES // 1024 // 1024} МБ в сумме")
                files.append((_safe_filename(part.filename), bytes(data)))
            elif part.name == "to":
                value = (await part.text()).strip()
                if value:
                    to.append(value)
            elif part.name in ("subject", "text"):
                fields[part.name] = (await part.text()).strip()
    except (ValueError, AssertionError) as exc:
        raise _BadMail("Некорректный запрос") from exc
    return to, fields.get("subject", ""), fields.get("text", ""), files


@routes.post("/api/mail/send")
async def mail_send(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    try:
        to, subject, text, files = await _read_mail_request(request)
    except _BadMail as exc:
        return json_error(str(exc))
    if not to:
        return json_error("Выберите получателя")
    if not subject:
        return json_error("Укажите тему письма")
    if len(subject) > 200 or len(text) > 10000:
        return json_error("Письмо слишком длинное")
    if any(not data for _, data in files):
        return json_error("Файл пустой")
    await app.diary.send_mail(user, to, subject, text, files or None)
    return web.json_response({"ok": True})


@routes.get("/api/mail/{message_id}")
async def mail_message(request: web.Request) -> web.Response:
    """Письмо целиком. Не кэшируется: открывают его по одному разу."""
    try:
        message_id = int(request.match_info["message_id"])
    except ValueError:
        return json_error("Некорректный номер письма")

    app, user = context(request), current_user(request)
    message = await app.diary.fetch_mail_message(user, message_id)
    return web.json_response({"ok": True, "data": message})



@routes.get("/api/attachment/{attachment_id}")
async def attachment(request: web.Request) -> web.Response:
    try:
        attachment_id = int(request.match_info["attachment_id"])
    except ValueError:
        return json_error("Некорректный идентификатор вложения")

    app, user = context(request), current_user(request)
    payload = await app.diary.download_attachment(user, attachment_id)
    # Без имени и типа файл сохранялся как «attachment-123» без расширения,
    # и телефон не понимал, что это docx или pdf.
    name = _attachment_name(request.query.get("name", ""), attachment_id)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return web.Response(
        body=payload,
        headers={
            "Content-Type": content_type,
            "Content-Disposition": _content_disposition(name),
        },
    )


_UNSAFE_NAME = re.compile(r'[\x00-\x1f\x7f/\\"]+')


def _attachment_name(raw: str, attachment_id: int) -> str:
    """Имя файла для скачивания: из ссылки, без путей и управляющих символов."""
    name = _UNSAFE_NAME.sub("_", raw).strip(" .")[:200]
    return name or f"attachment-{attachment_id}"


def _content_disposition(name: str) -> str:
    """Заголовок с именем в UTF-8 (RFC 6266) и ASCII-запасным для старых клиентов."""
    fallback = name.encode("ascii", "replace").decode().replace("?", "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


@routes.post("/api/student")
async def switch_student(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    body = await request.json()
    try:
        student_id = int(body["id"])
    except (KeyError, TypeError, ValueError):
        return json_error("Не указан ученик")

    student = next((s for s in user.available_students if s.id == student_id), None)
    if student is None:
        return json_error("Такого ученика нет в аккаунте", status=404)

    updated = await app.users.save(
        replace(user, selected_student_id=student.id, student_name=student.name)
    )
    # Тот же сброс, что и в боте: журнал другого ребёнка иначе приедет
    # как «новые оценки».
    await app.state.forget_all(updated.telegram_id)
    await app.state.mark_baseline_pending(updated.telegram_id, True)
    await app.cache.clear(updated.telegram_id)
    await app.pool.invalidate(updated.telegram_id)
    return web.json_response({"ok": True, "data": {"name": student.name}})


NOTIFICATION_KEYS = (
    "grades", "changes", "deletes", "homework", "mail", "weekly_summary", "evening_summary",
    "schedule", "absences", "telegram",
)


@routes.post("/api/settings/notifications")
async def notification_settings(request: web.Request) -> web.Response:
    """Переключатели уведомлений в Telegram — те же, что в /settings бота."""
    app, user = context(request), current_user(request)
    try:
        body = await request.json()
    except ValueError:
        return json_error("Некорректный запрос")
    key = str(body.get("key") or "")
    if key not in NOTIFICATION_KEYS:
        return json_error("Неизвестная настройка")
    prefs = replace(user.notifications, **{key: bool(body.get("value"))})
    updated = await app.users.save(replace(user, notifications=prefs))
    return web.json_response({"ok": True, "data": asdict(updated.notifications)})


def valid_timezone(name: str) -> bool:
    if name in {key for key, _ in TIMEZONES}:
        return True
    if not name or len(name) > 64 or "/" not in name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


@routes.post("/api/settings/timezone")
async def timezone_settings(request: web.Request) -> web.Response:
    """Пояс устройства: приложение присылает его само, когда он не совпал.

    Принимается любой пояс IANA, а не только российские из списка бота.
    """
    app, user = context(request), current_user(request)
    try:
        body = await request.json()
    except ValueError:
        return json_error("Некорректный запрос")
    name = str(body.get("timezone") or "")
    if not valid_timezone(name):
        return json_error("Неизвестный часовой пояс")
    if name == user.timezone:
        return web.json_response({"ok": True, "data": {"timezone": name}})
    await app.users.save(replace(user, timezone=name))
    # «Сегодня» сдвинулось — сохранённые окна дневника посчитаны от старого.
    await app.cache.clear(user.telegram_id)
    return web.json_response({"ok": True, "data": {"timezone": name}})


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
                "mark": record.mark,
                "old": row["old_mark"],
                "new": row["new_mark"],
                "seen": row["created_at"].replace(" ", "T") + "Z",
            }
        )
    return web.json_response({"ok": True, "data": {"items": items}})


THEMES = ("auto", "light", "dark")
DESIGNS = ("classic", "new")
ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _clean_weights(value: Any) -> list[float] | None:
    """Веса калькулятора «что если»: до 12 положительных чисел не больше 100."""
    if not isinstance(value, list) or len(value) > 12:
        return None
    weights: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not 0 < item <= 100:
            return None
        number = round(float(item), 2)
        if number not in weights:
            weights.append(int(number) if number.is_integer() else number)
    return weights


def _clean_grade_thresholds(value: Any) -> dict[str, float] | None:
    """С какого среднего «5», «4», «3»: числа от 1 до 5 по убыванию."""
    if not isinstance(value, dict):
        return None
    result: dict[str, float] = {}
    for grade in ("5", "4", "3"):
        number = value.get(grade)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not 1 <= number <= 5:
            return None
        result[grade] = round(float(number), 2)
    return result if result["5"] > result["4"] > result["3"] else None


def _clean_thresholds(value: Any) -> dict[str, Any] | None:
    """Пороги оценок: общие (`all`, необязательно) и свои у предметов (`subjects`)."""
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    if value.get("all") is not None:
        if (common := _clean_grade_thresholds(value["all"])) is None:
            return None
        result["all"] = common
    subjects = value.get("subjects") or {}
    if not isinstance(subjects, dict) or len(subjects) > 60:
        return None
    result["subjects"] = {}
    for name, item in subjects.items():
        if not name or len(name) > 120 or (own := _clean_grade_thresholds(item)) is None:
            return None
        result["subjects"][name] = own
    return result


_MONTH_DAY = re.compile(r"^(\d{2})-(\d{2})$")


def _clean_periods(value: Any) -> dict[str, Any] | None:
    """Схема учебного года и, по желанию, свои даты начала периодов («MM-DD»)."""
    if not isinstance(value, dict):
        return None
    scheme = value.get("scheme")
    if scheme == "auto":
        return {"scheme": "auto"}
    try:
        bounds = SCHEME_BOUNDS[PeriodScheme(scheme)]
    except ValueError:
        return None
    starts = value.get("starts")
    if not starts:
        return {"scheme": scheme}
    if not isinstance(starts, list) or len(starts) != len(bounds):
        return None
    dates = []
    for item in starts:
        match = _MONTH_DAY.match(str(item))
        if not match:
            return None
        month, day = int(match.group(1)), int(match.group(2))
        try:
            # Високосный год: 29 февраля допустимо.
            dt.date(2028, month, day)
        except ValueError:
            return None
        dates.append((month, day))
    # Учебный год идёт с сентября: порядок проверяем по «учебному» месяцу.
    order = [((m - 9) % 12, d) for m, d in dates]
    if order != sorted(order) or len(set(order)) != len(order):
        return None
    return {"scheme": scheme, "starts": [f"{m:02d}-{d:02d}" for m, d in dates]}


@routes.get("/api/prefs")
async def get_prefs(request: web.Request) -> web.Response:
    """Тема, дизайн, цвет шапки и веса калькулятора — общие для Telegram и сайта."""
    app, user = context(request), current_user(request)
    return web.json_response({"ok": True, "data": await app.miniapp.prefs(user.telegram_id)})


@routes.post("/api/prefs")
async def save_prefs(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    try:
        body = await request.json()
    except ValueError:
        return json_error("Некорректный запрос")
    if not isinstance(body, dict):
        return json_error("Некорректный запрос")
    changes: dict[str, Any] = {}
    if "theme" in body:
        if body["theme"] not in THEMES:
            return json_error("Неизвестная тема")
        changes["theme"] = body["theme"]
    if "design" in body:
        if body["design"] not in DESIGNS:
            return json_error("Неизвестный дизайн")
        changes["design"] = body["design"]
    if "accent" in body:
        if not isinstance(body["accent"], str) or not ACCENT_RE.match(body["accent"]):
            return json_error("Некорректный цвет")
        changes["accent"] = body["accent"].lower()
    if "weights" in body:
        weights = _clean_weights(body["weights"])
        if weights is None:
            return json_error("Некорректные веса")
        changes["weights"] = weights
    if "periods" in body:
        setting = _clean_periods(body["periods"])
        if setting is None:
            return json_error("Некорректные учебные периоды")
        changes["periods"] = setting
        # Таблица «Итогов» посчитана по старым границам.
        await app.cache.clear(user.telegram_id)
    if "thresholds" in body:
        thresholds = _clean_thresholds(body["thresholds"])
        if thresholds is None:
            return json_error("Некорректные пороги оценок")
        changes["thresholds"] = thresholds
    if not changes:
        return json_error("Нечего сохранять")
    data = await app.miniapp.save_prefs(user.telegram_id, changes)
    return web.json_response({"ok": True, "data": data})


@routes.get("/api/pwa-link")
async def pwa_link(request: web.Request) -> web.Response:
    """Постоянная ссылка для установки на домашний экран (как /app_install)."""
    app, user = context(request), current_user(request)
    token = await app.miniapp.issue_token(user.telegram_id)
    return web.json_response({"ok": True, "data": {"url": f"{app.settings.web.miniapp_url}/?token={token}"}})


@routes.get("/api/push/key")
async def push_key(request: web.Request) -> web.Response:
    settings = context(request).settings.push
    if not settings.configured:
        return json_error("Push-уведомления не настроены", status=503)
    return web.json_response({"ok": True, "data": {"key": settings.public_key}})


@routes.post("/api/push/subscribe")
async def push_subscribe(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    body = await request.json()
    endpoint = str(body.get("endpoint") or "").strip()
    keys = body.get("keys") or {}
    p256dh, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")

    if not endpoint or not p256dh or not auth:
        return json_error("Неполные данные подписки")

    await app.miniapp.add_push_subscription(user.telegram_id, endpoint, p256dh, auth)
    return web.json_response({"ok": True})


@routes.post("/api/push/unsubscribe")
async def push_unsubscribe(request: web.Request) -> web.Response:
    body = await request.json()
    endpoint = str(body.get("endpoint") or "").strip()
    if endpoint:
        await context(request).miniapp.drop_push_subscription(endpoint)
    return web.json_response({"ok": True})
