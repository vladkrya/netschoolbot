"""Оценки и итоги: текущий год, архив, аналитика по предмету."""

from __future__ import annotations

import datetime as dt
import logging

from aiohttp import web

from ...context import AppContext
from ...domain import formatting
from ...domain.models import ApiMode, User, normalize
from ...netschool.errors import NetSchoolError, Reason
from ...domain.periods import (
    PERIOD_KEYS,
    PeriodScheme,
    Quarter,
    class_average_period,
    period_of,
    periods,
    periods_from_terms,
    term_key,
)
from .base import _cached, context, current_user, json_error, routes
from .diary import _mark_to_json, _teachers

logger = logging.getLogger(__name__)


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
        now = user.now()
        today = now.date()
        year, source = await _user_periods(app, user, today)
        now_quarter = next((q for q in year if q.covers(today)), None)
        if now_quarter is None:
            # Каникулы между периодами или лето — последний начавшийся.
            started = [q for q in year if q.start <= today]
            now_quarter = started[-1] if started else year[0]
        # На каникулах экран уже показывает следующий период: без его недель
        # полоса уроков получала только край последней недели («0/1»).
        fetched_end = now_quarter.end
        upcoming = next((q for q in year if q.start > today), None)
        if upcoming and now_quarter.lessons_end and today > now_quarter.lessons_end:
            fetched_end = upcoming.end
        days = await app.diary.fetch_period(user, year[0].start, fetched_end, today=today)

        by_subject: dict[str, list] = {}
        lessons: dict[str, dict[str, dict[str, int]]] = {}
        teachers: dict[str, dict[str, int]] = {}
        # Отметки посещаемости: {предмет: {четверть: {код: сколько}}}.
        absences: dict[str, dict[str, dict[str, int]]] = {}
        attendance: dict[str, list] = {}
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
                if _lesson_over(day.day, lesson, now):
                    counts["done"] += 1
                code = (lesson.absence or "").strip()
                if code:
                    attendance.setdefault(lesson.subject, []).append(_attendance_to_json(lesson, day.day, key))
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

        complete = {q.key for q in year if q.end <= fetched_end
                    and (q.start <= today or _reached_end(q, days))}
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
                    # Период, загруженный не целиком, дал бы неполное число уроков.
                    "lessons": {key: counts for key, counts in lessons.get(subject, {}).items()
                                if key in complete},
                    "absences": absences.get(subject, {}),
                    "attendance": attendance.get(subject, []),
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


@routes.get("/api/marks/subject")
async def archived_subject_marks(request: web.Request) -> web.Response:
    """Оценки архивного предмета: только выбранный период, по запросу экрана."""
    subject = (request.query.get("subject") or "").strip()[:120]
    key = request.query.get("quarter") or ""
    try:
        year_id = int(request.query.get("year", ""))
        if year_id <= 0 or not subject or key not in PERIOD_KEYS:
            raise ValueError
    except ValueError:
        return json_error("Не указан предмет, учебный год или период")

    async def produce(app: AppContext, user: User):
        start, end, days = await _archived_period(app, user, year_id, key)
        items = []
        topics = {}
        teachers = {}
        absences = {}
        attendance = []
        total = 0
        for day in days:
            # Понедельный дневник включает соседние дни за границами четверти.
            if not start <= day.day <= end:
                continue
            excluded = {record for lesson in day.lessons if lesson.is_elective for record in lesson.marks}
            items.extend(record for record in day.marks
                         if normalize(record.subject) == normalize(subject) and record not in excluded)
            for lesson in day.lessons:
                if lesson.is_elective or normalize(lesson.subject) != normalize(subject):
                    continue
                total += 1
                for record in lesson.marks:
                    topics[record] = lesson.topic
                if lesson.teacher:
                    teachers[lesson.teacher] = teachers.get(lesson.teacher, 0) + 1
                if code := (lesson.absence or "").strip():
                    absences[code] = absences.get(code, 0) + 1
                    attendance.append(_attendance_to_json(lesson, day.day, key))
        return {"subject": {
            "subject": subject,
            "marks": [{**_mark_to_json(record), "quarter": key, "lesson_topic": topics.get(record, "")}
                      for record in sorted(items, key=lambda record: record.date)
                      if start <= record.date <= end],
            "lessons": {key: {"total": total, "done": total}},
            "absences": {key: absences},
            "attendance": attendance,
            "teacher": max(teachers, key=teachers.get) if teachers else "",
        }}

    return await _cached(request, f"marks:subject:{year_id}:{key}:{normalize(subject)}", produce)


async def _archived_period(app: AppContext, user: User, year_id: int, key: str):
    """Границы периода архивного года и дневник за него."""
    years = await app.diary.fetch_school_years(user) or []
    selected = next((year for year in years if year["id"] == year_id and not year["current"]), None)
    if selected is None:
        raise NetSchoolError(Reason.UNKNOWN, "Этот архивный год недоступен для выбранного ученика")
    terms = await app.diary.fetch_terms(user, school_year_id=year_id) or []
    term = next((item for item in terms if term_key(item.get("name")) == key), None)
    try:
        start = dt.date.fromisoformat(str((term or {}).get("startDate") or "")[:10])
        end = dt.date.fromisoformat(str((term or {}).get("endDate") or "")[:10])
        if end < start or (end - start).days > 366:
            raise ValueError
    except ValueError:
        raise NetSchoolError(Reason.SERVER_UNAVAILABLE, "Школа не вернула даты выбранного периода")
    days = await app.diary.fetch_period(user, start, end, today=user.today())
    return start, end, days


@routes.get("/api/marks/period")
async def archived_period_marks(request: web.Request) -> web.Response:
    """Оценки всех предметов архивного года за один период.

    Архивные итоги приходят без оценок (их много и они дорогие), и карточки
    предметов в «Оценках» показывали «Оценок пока нет». Экран просит этот
    период целиком, когда его выбирают.
    """
    key = request.query.get("quarter") or ""
    try:
        year_id = int(request.query.get("year", ""))
        if year_id <= 0 or key not in PERIOD_KEYS:
            raise ValueError
    except ValueError:
        return json_error("Не указан учебный год или период")

    async def produce(app: AppContext, user: User):
        start, end, days = await _archived_period(app, user, year_id, key)
        marks = []
        for day in days:
            if not start <= day.day <= end:
                continue
            excluded = {record for lesson in day.lessons if lesson.is_elective for record in lesson.marks}
            topics = {record: lesson.topic for lesson in day.lessons for record in lesson.marks}
            marks.extend({**_mark_to_json(record), "quarter": key, "lesson_topic": topics.get(record, "")}
                         for record in day.marks
                         if record not in excluded and start <= record.date <= end)
        marks.sort(key=lambda mark: mark["date"])
        return {"quarter": key, "marks": marks}

    return await _cached(request, f"marks:period:{year_id}:{key}", produce)


@routes.get("/api/marks/lessons")
async def period_lessons(request: web.Request) -> web.Response:
    """Сколько уроков в ещё не начавшемся периоде, по предметам.

    «Итоги» загружают дневник только до конца текущего периода — следующий
    стоил бы ещё десятка недельных запросов при каждом обновлении. Экран
    предмета просит его отдельно, когда открывают будущую четверть.
    """
    key = request.query.get("quarter") or ""
    if key not in PERIOD_KEYS:
        return json_error("Не указан период")

    async def produce(app: AppContext, user: User):
        now = user.now()
        today = now.date()
        year, _ = await _user_periods(app, user, today)
        period = next((q for q in year if q.key == key), None)
        if period is None:
            raise NetSchoolError(Reason.UNKNOWN, "Такого периода в этом учебном году нет")
        days = await app.diary.fetch_period(user, period.start, period.end, today=today)
        if not _reached_end(period, days):
            return {"quarter": key, "lessons": None}
        lessons: dict[str, dict[str, int]] = {}
        for day in days:
            if not period.covers(day.day):
                continue
            for lesson in day.lessons:
                if lesson.is_elective:
                    continue
                counts = lessons.setdefault(lesson.subject, {"total": 0, "done": 0})
                counts["total"] += 1
                if _lesson_over(day.day, lesson, now):
                    counts["done"] += 1
        return {"quarter": key, "lessons": lessons}

    return await _cached(request, f"marks:lessons:{key}", produce,
                         cacheable=lambda payload: payload["lessons"] is not None)


def _reached_end(period: Quarter, days: list) -> bool:
    """Дошёл ли дневник до последнего учебного дня периода.

    Будущие недели школа может не отдать (409 — см. _collect_weeks), и
    тогда число уроков вышло бы неполным. Загрузка идёт неделями — считаем
    по концу последней полученной недели.
    """
    if not days:
        return False
    last = max(day.day for day in days)
    return (period.lessons_end or period.end) <= last + dt.timedelta(days=6 - last.weekday())


def _attendance_to_json(lesson, day, key):
    return {"subject": lesson.subject, "date": day.isoformat(), "quarter": key,
            "mark": lesson.absence.strip(), "type": "Посещаемость", "title": "Посещаемость",
            "lesson_number": lesson.number, "lesson_topic": lesson.topic, "weight": None}


def _lesson_over(day: dt.date, lesson, now: dt.datetime) -> bool:
    """Прошёл ли урок к моменту `now` (местное время ученика).

    Сегодняшний урок считается прошедшим только после звонка с него;
    без времени в расписании — не раньше завтрашнего дня.
    """
    if day != now.date():
        return day < now.date()
    clock = lesson.end or lesson.start
    try:
        ends = dt.datetime.strptime(clock, "%H:%M").time()
    except ValueError:
        return False
    return now.time() >= ends


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
        if stats and stats.get("class_average") is not None:
            await _record_class_averages(app, user, quarter, {subject: stats["class_average"]})
        return {"available": True, **stats} if stats else {"available": False}

    return await _cached(
        request, f"subject:{quarter}:{normalize(subject)}", produce,
        cacheable=lambda payload: payload.get("available", False),
    )


def _class_average_scope(user: User, quarter: str) -> tuple[str, str]:
    """Чьи и за какой период снимки среднего класса — как в вотчере."""
    return (str(user.selected_student_id or user.student_name or ""),
            class_average_period(quarter, dt.date.today()))


async def _record_class_averages(app: AppContext, user: User, quarter: str, averages: dict[str, float]) -> None:
    """Свежие средние класса из приложения — тоже снимок для истории."""
    try:
        await app.state.record_class_averages(user.telegram_id, *_class_average_scope(user, quarter), averages)
    except Exception as exc:  # noqa: BLE001 — история вторична, ответ важнее
        logger.debug("Снимок средних класса %s не записан: %s", user.telegram_id, exc)


@routes.get("/api/subject/class-history")
async def class_average_history(request: web.Request) -> web.Response:
    """Как менялся средний балл класса по предмету за период.

    Снимки копит фоновая проверка (только мобильное API): `[{at, value}]`,
    старые сверху, `at` — UTC. Пустой список — снимков пока нет.
    """
    subject = (request.query.get("subject") or "").strip()[:120]
    quarter = (request.query.get("quarter") or "").strip()
    if not subject or quarter not in PERIOD_KEYS:
        return json_error("Не указан предмет или четверть")
    user = current_user(request)
    rows = await context(request).state.class_average_history(
        user.telegram_id, *_class_average_scope(user, quarter), subject)
    return web.json_response({"ok": True, "data": {"history": [
        {"at": f"{at}Z", "value": value} for at, value in rows
    ]}})


@routes.get("/api/class-averages")
async def class_averages(request: web.Request) -> web.Response:
    """Средний балл класса по всем предметам за четверть — для карточек «Итогов».

    Как и `/api/subject`, только мобильный режим; иначе `{"available": false}`.
    """
    quarter = (request.query.get("quarter") or "").strip()
    if quarter not in PERIOD_KEYS:
        return json_error("Не указана четверть")
    if current_user(request).effective_api_mode is not ApiMode.MOBILE:
        return web.json_response({"ok": True, "data": {"available": False}, "stale": False})

    async def produce(app: AppContext, user: User):
        averages = await app.diary.fetch_class_averages(user, quarter)
        if averages:
            await _record_class_averages(app, user, quarter, averages)
        return {"available": True, "averages": averages} if averages else {"available": False}

    return await _cached(
        request, f"class-averages:{quarter}", produce,
        cacheable=lambda payload: payload.get("available", False),
    )
