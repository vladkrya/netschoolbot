"""Школьное расписание с отдельными правами и историей опубликованных правок."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
import time
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import access

PERMISSIONS = {
    "read": "Смотреть расписание",
    "lessons": "Править уроки",
    "bells": "Править звонки и сокращать учебный день",
    "announce": "Публиковать объявления",
    "reports": "Смотреть сообщения об ошибках",
}
CLASS_PERMISSIONS = frozenset(PERMISSIONS) - {"bells"}
REPORT_REASONS = {"time", "room", "subject", "cancelled"}
MAX_LESSONS = 20


@dataclass(frozen=True, slots=True)
class Scope:
    installation: str
    school_id: int
    year: int
    class_key: str = ""
    class_label: str = ""

    @property
    def school(self):
        return self.installation, self.school_id

    @property
    def cohort(self):
        return *self.school, self.year, self.class_key


def school_year(date: dt.date) -> int:
    return date.year if date.month >= 8 else date.year - 1


def installation(url: str) -> str:
    parsed = urlsplit(url)
    return parsed.netloc.lower() + parsed.path.rstrip("/")


def identify(user, school_id, student, date: dt.date) -> Scope | None:
    if type(school_id) is not int or school_id <= 0 or not student:
        return None
    label = " ".join(str(student.get("className") or "").split())
    if not label or len(label) > 40 or not installation(user.school.url):
        return None
    # Мобильный diary_init передаёт только название. Один ключ для
    # обоих режимов СГО, иначе один класс распадётся при смене режима.
    key = "name:" + label.casefold()
    return Scope(installation(user.school.url), school_id, school_year(date), key, label)


async def enabled(app, scope: Scope) -> bool:
    return bool(await app.db.fetch_one(
        "SELECT 1 FROM moderation_schools WHERE installation=? AND school_id=? AND enabled=1",
        scope.school,
    ))


async def allowed(app, user_id: int, scope: Scope, permission: str) -> bool:
    if not await enabled(app, scope):
        return False
    if access.owner(app, user_id):
        return True
    rows = await app.db.fetch_all(
        """SELECT permissions FROM moderation_roles
           WHERE user_id=? AND installation=? AND school_id=? AND school_year=?
           AND class_key IN ('', ?)""",
        (user_id, *scope.cohort),
    )
    return any(permission in json.loads(row["permissions"]) for row in rows)


def lesson_fields(lesson) -> dict:
    return {"number": lesson.number, "subject": lesson.subject, "start": lesson.start,
            "end": lesson.end, "room": lesson.room}


def lesson_key(fields: dict) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def remember_class(app, scope: Scope) -> None:
    if not scope.class_key:
        return
    await app.db.execute(
        """INSERT INTO moderation_classes
           SELECT ?, ?, ?, ?, ?, ?
           WHERE EXISTS (SELECT 1 FROM moderation_schools WHERE installation=? AND school_id=? AND enabled=1)
           ON CONFLICT(installation, school_id, school_year, class_key)
           DO UPDATE SET class_label=excluded.class_label, seen_at=max(seen_at, excluded.seen_at)""",
        (*scope.cohort, scope.class_label, int(time.time()), *scope.school),
    )


async def observe(app, scope: Scope, days, today: dt.date) -> None:
    if not await enabled(app, scope):
        return
    await remember_class(app, scope)
    now = int(time.time())
    rows = []
    for day in days:
        if school_year(day.day) != scope.year or not -7 <= (day.day - today).days <= 21:
            continue
        for lesson in day.lessons:
            if lesson.is_elective or not lesson.number:
                continue
            fields = lesson_fields(lesson)
            rows.append((*scope.cohort, scope.class_label, day.day.isoformat(), lesson_key(fields),
                         json.dumps(fields, ensure_ascii=False), now))
    if not rows:
        return
    async with app.db.transaction() as db:
        if not await enabled(app, scope):
            return
        cutoff = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=8)).isoformat()
        await db.execute("DELETE FROM moderation_lessons WHERE date<?", (cutoff,))
        await db.executemany(
            """INSERT INTO moderation_lessons
               (installation, school_id, school_year, class_key, class_label, date, lesson_key, payload, seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (installation, school_id, school_year, class_key, date, lesson_key)
               DO UPDATE SET seen_at=max(seen_at, excluded.seen_at), class_label=excluded.class_label""",
            rows,
        )


def clock_time(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) is not None


def validate_bells(data: dict) -> dict:
    limit = data.get("limit")
    if limit is not None and (type(limit) is not int or not 0 <= limit <= MAX_LESSONS):
        raise ValueError("Проверьте количество уроков")
    slots = data.get("slots", {})
    if not isinstance(slots, dict) or len(slots) > MAX_LESSONS:
        raise ValueError("Проверьте звонки")
    clean = {}
    for number, pair in slots.items():
        if number not in {str(i) for i in range(1, MAX_LESSONS + 1)} or (
            limit is not None and int(number) > limit
        ):
            raise ValueError("Некорректный номер урока")
        if not isinstance(pair, list) or len(pair) != 2 or not all(map(clock_time, pair)) or pair[0] >= pair[1]:
            raise ValueError("Проверьте начало и конец урока")
        clean[number] = pair
    ordered = sorted(clean, key=int)
    if any(clean[left][1] > clean[right][0] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("Интервалы звонков пересекаются")
    if not clean and limit is None:
        raise ValueError("Укажите звонки или количество уроков")
    shift = data.get("shift", 0)
    if type(shift) is not int or shift not in (0, 1, 2):
        raise ValueError("Проверьте смену")
    return {"slots": clean, "limit": limit, "shift": shift}


async def scope_for(app, user, *, require_enabled: bool = True) -> Scope | None:
    from .domain.models import ApiMode
    from .netschool.errors import NetSchoolError
    from .netschool.service import _fetch_diary_init

    if user.api_mode is ApiMode.MYSCHOOL or not user.school.configured:
        return None
    if require_enabled and not await available_for_school(app, user):
        return None

    async def resolve(client):
        school_id = getattr(client, "_school_id", None)
        selected = getattr(client, "_student_id", None)
        if not selected or (user.selected_student_id and selected != user.selected_student_id):
            return None
        raw = (await _fetch_diary_init(client)).get("students") or []
        pairs = raw.items() if isinstance(raw, dict) else enumerate(raw)
        student = next((
            item for key, item in pairs if isinstance(item, dict)
            and str(item.get("studentId") or (key if isinstance(raw, dict) else "")) == str(selected)
        ), None)
        mobile_user = getattr(client, "_user", None)
        if isinstance(mobile_user, dict) and mobile_user.get("children"):
            child = next((item for item in mobile_user["children"] if isinstance(item, dict)
                          and str(item.get("id") or item.get("studentId")) == str(selected)), {})
            organization = child.get("organization")
            school_id = (child.get("schoolId") or child.get("organizationId")
                         or (organization.get("id") if isinstance(organization, dict) else None))
        elif student and (student_school := student.get("schoolId") or student.get("organizationId")):
            school_id = student_school
        elif len(raw) > 1:
            # Нельзя публиковать сведения ребёнка в школе другого ребёнка.
            return None
        return identify(user, school_id, student, user.today())

    try:
        scope = await app.diary.with_client(user, resolve)
    except NetSchoolError:
        return None
    return scope if scope and (not require_enabled or await enabled(app, scope)) else None


async def changes_for(app, scope: Scope, start: str, end: str):
    rows = await app.db.fetch_all(
        """SELECT id, date, end_date, class_key, kind, payload FROM moderation_changes
           WHERE installation=? AND school_id=? AND school_year=? AND class_key IN ('', ?)
           AND end_date>=? AND date<=? AND revoked_at IS NULL
           ORDER BY id""",
        (*scope.cohort, start, end),
    )

    shift = await app.db.fetch_one(
        """SELECT shift FROM moderation_shifts
           WHERE installation=? AND school_id=? AND school_year=? AND class_key=?""",
        scope.cohort,
    )
    return [dict(row) for row in rows if row["kind"] != "bells" or
            json.loads(row["payload"])["shift"] in (0, shift["shift"] if shift else None)]


def dates_for(change: dict, start: dt.date, end: dt.date):
    first = max(start, dt.date.fromisoformat(change["date"]))
    last = min(end, dt.date.fromisoformat(change.get("end_date", change["date"])))
    weekdays = json.loads(change["payload"]).get("weekdays")
    for offset in range((last - first).days + 1):
        date = first + dt.timedelta(days=offset)
        if weekdays is None or date.weekday() in weekdays:
            yield date


def notice(change_id: int, text: str, payload: dict) -> dict:
    result = {"id": change_id, "text": text}
    roles = {"class": "Модератор класса в СГОшке", "school": "Модератор школы в СГОшке"}
    source = roles.get(payload.get("moderator_role"))
    if source:
        result["source"] = source
    if "summary" in payload:
        heading, separator, details = text.partition(". ")
        slot, dot, subject = heading.partition(" · ")
        if separator and dot and re.fullmatch(r"\d+-й урок", slot):
            result["title"] = f"{subject} · {slot}"
            result["detail"] = details.replace("Время изменено на ", "Новое время ", 1)
    return result


def apply_day(day: dict, changes) -> None:
    lessons = day["lessons"]
    originals = [(lesson, lesson_key({
        field: lesson.get(field) for field in ("number", "subject", "start", "end", "room")
    })) for lesson in lessons]
    notices = []
    bells = {}
    individual_times = set()
    date = dt.date.fromisoformat(day["date"])
    for change in changes:
        if not any(dates_for(change, date, date)):
            continue
        payload = json.loads(change["payload"])
        kind = change["kind"]
        if kind == "bells":
            bells[payload["shift"]] = (change["id"], payload)
            continue
        if kind == "announce":
            notices.append(notice(change["id"], payload["text"], payload))
            continue
        if fields := payload.get("add"):
            if not any(lesson.get("number") == fields["number"] for lesson in lessons):
                lessons.append({**fields, "marks": [], "homework": [], "is_elective": False, "moderation_changed": True})
                notices.append(notice(change["id"], payload["summary"], payload))
            continue
        changed = False
        for entry in payload.get("entries", [payload]):
            matching = [lesson for lesson, key in originals if key == entry["lesson_key"]]
            if len(matching) != 1:
                continue
            lesson = matching[0]
            fields = entry["fields"]
            if "subject" in fields and fields["subject"] != lesson.get("subject"):
                # Личные оценки и задания старого предмета есть в данных дня,
                # но карточка замены не должна показывать их как новый урок.
                lesson.update(teacher="", topic="", absence="", distance_meeting_id=None, marks=[], homework=[])
            lesson.update(fields)
            lesson["moderation_changed"] = True
            if "start" in fields:
                if entry.get("clock_override"):
                    individual_times.add(id(lesson))
                    day["moderation_bells"] = True
                else:
                    individual_times.discard(id(lesson))
            if entry.get("cancelled"):
                lesson["moderation_cancelled"] = True
            changed = True
        if changed:
            notices.append(notice(change["id"], payload["summary"], payload))
    if bells:
        day["moderation_bells"] = True
        ordered = [bells[shift] for shift in sorted(bells)]
        combined_slots = {}
        combined_limit = None
        for _, rule in ordered:
            combined_slots.update(rule["slots"])
            if rule["limit"] is not None:
                combined_limit = rule["limit"]
        for lesson in lessons:
            number = lesson.get("number")
            if not isinstance(number, int):
                continue
            limit = combined_limit
            if limit is not None and number > limit:
                lesson["moderation_cancelled"] = True
                lesson["moderation_changed"] = True
            if id(lesson) not in individual_times and (pair := combined_slots.get(str(number))):
                if (lesson.get("start"), lesson.get("end")) != tuple(pair):
                    lesson.update(start=pair[0], end=pair[1], time="–".join(pair), moderation_changed=True)
        for change_id, payload in ordered:
            text = "Изменилось расписание звонков"
            if limit := payload["limit"]:
                text = f"Уроки начиная с {limit + 1}-го отменены"
                if payload["slots"]:
                    text = "Звонки изменились. " + text
            elif payload["limit"] == 0:
                text = "Все уроки отменены"
            notices.append(notice(change_id, text, payload))
    if notices:
        # Старые клиенты тоже показывают отмену в поле времени и не ставят
        # отменённый урок в таймер. Исходные времена остались в кэше СГО.
        for lesson in lessons:
            if lesson.get("moderation_cancelled"):
                lesson.update(start="", end="", time="Отменён")
        day["moderation_notices"] = notices
        day["lessons"] = sorted(lessons, key=lambda lesson: (lesson.get("number") or 0, lesson.get("start") or ""))


async def available_for_school(app, user) -> bool:
    return bool(user.school.configured and await app.db.fetch_one(
        "SELECT 1 FROM moderation_schools WHERE installation=? AND enabled=1",
        (installation(user.school.url),),
    ))


async def has_dashboard(app, user) -> bool:
    if access.owner(app, user.telegram_id):
        return True
    return bool(await app.db.fetch_one(
        """SELECT 1 FROM moderation_roles r JOIN moderation_schools s
           USING (installation, school_id)
           WHERE r.user_id=? AND r.installation=? AND r.school_year=? AND s.enabled=1""",
        (user.telegram_id, installation(user.school.url), school_year(user.today())),
    ))


async def diary_scope(app, user, *, refresh: bool = False) -> Scope | None:
    """Данные привязки в личном кэше позволяют применить правки и при сбое СГО."""
    if not await available_for_school(app, user):
        return None
    section = f"moderation-scope:{user.selected_student_id}"
    cached = await app.cache.get(user.telegram_id, section)
    scope = None
    if cached:
        candidate = Scope(**cached[0])
        age = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - cached[1]
        if (candidate.installation == installation(user.school.url) and candidate.year == school_year(user.today())
            and age < dt.timedelta(days=3) and await enabled(app, candidate)):
            scope = candidate
            if not refresh or age < dt.timedelta(minutes=10):
                return scope
    try:
        async with asyncio.timeout(8 if refresh else 2):
            resolved = await scope_for(app, user)
    except TimeoutError:
        return None if refresh else scope
    if resolved:
        from dataclasses import asdict

        current = await app.users.get(user.telegram_id)
        if (current is not None and current.school == user.school and current.credentials == user.credentials
            and current.selected_student_id == user.selected_student_id and current.api_mode == user.api_mode):
            await app.cache.put(user.telegram_id, section, asdict(resolved))
        await remember_class(app, resolved)
        return resolved
    # Сохранённая привязка используется для чтения собственного дневника.
    # При новом наблюдении не записываем расписание, пока школа не подтверждена.
    return None if refresh else scope


async def record_diary(app, user, days) -> None:
    try:
        scope = await diary_scope(app, user, refresh=True)
        if scope and days:
            await observe(app, scope, days, user.today())
    except sqlite3.Error:
        logging.getLogger(__name__).exception("Не удалось сохранить общий календарь")
