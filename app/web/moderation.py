"""API управления общим расписанием и правами модераторов."""
from __future__ import annotations

import datetime as dt
import json
import time
from collections import defaultdict

from aiohttp import web

from .. import access
from .. import moderation as mod
from .api import context, current_user, json_error

routes = web.RouteTableDef()


def response(data):
    return web.json_response({"ok": True, "data": data}, headers={"Cache-Control": "no-store"})


async def body(request):
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="Нужен Content-Type application/json")
    try:
        value = await request.json()
    except (ValueError, UnicodeError):
        value = None
    if not isinstance(value, dict):
        raise web.HTTPBadRequest(text="Ожидается объект JSON")
    return value


@routes.get("/api/moderation")
async def dashboard(request):
    app, user = context(request), current_user(request)
    owner = access.owner(app, user.telegram_id)
    if owner and request.query.get("school_id"):
        try:
            school_id = int(request.query["school_id"])
            if not 0 < school_id < 2**63:
                raise ValueError
            scope = mod.Scope(request.query["installation"], school_id, mod.school_year(user.today()))
        except (KeyError, ValueError):
            return json_error("Проверьте школу")
    else:
        scope = await mod.diary_scope(app, user)
    schools = []
    if owner:
        schools = [dict(row) for row in await app.db.fetch_all(
            "SELECT installation, school_id, title, enabled FROM moderation_schools ORDER BY title",
        )]
    if not scope or not await mod.enabled(app, scope):
        return response({"owner": owner, "schools": schools, "available": owner})
    school_scope = mod.Scope(*scope.school, scope.year)
    school_rights = [key for key in mod.PERMISSIONS if await mod.allowed(app, user.telegram_id, school_scope, key)]
    class_rights = [key for key in mod.CLASS_PERMISSIONS if await mod.allowed(app, user.telegram_id, scope, key)]
    if not school_rights and not class_rights:
        return response({"owner": owner, "schools": schools, "available": owner})
    classes = [dict(row) for row in await app.db.fetch_all(
        """SELECT c.class_key, c.class_label, s.shift FROM moderation_classes c
           LEFT JOIN moderation_shifts s USING (installation, school_id, school_year, class_key)
           WHERE c.installation=? AND c.school_id=? AND c.school_year=?
           ORDER BY c.class_label""",
        (*scope.school, scope.year),
    ) if "read" in school_rights or (row["class_key"] == scope.class_key and "read" in class_rights)]
    rows = await app.db.fetch_all(
        """SELECT class_key, class_label, date, lesson_key, payload, seen_at FROM moderation_lessons
           WHERE installation=? AND school_id=? AND school_year=? AND date>=? AND date<=?
           ORDER BY class_label, date, lesson_key""",
        (*scope.school, scope.year, user.today().isoformat(), (user.today() + dt.timedelta(days=21)).isoformat()),
    )
    lessons = [{**dict(row), **json.loads(row["payload"])} for row in rows
               if "read" in school_rights or (row["class_key"] == scope.class_key and "read" in class_rights)]
    for lesson in lessons:
        del lesson["payload"]
    await _add_preview(app, scope, lessons)
    roles = []
    if owner:
        roles = [dict(row) for row in await app.db.fetch_all(
            """SELECT r.user_id, u.account_no, r.class_key, r.permissions
               FROM moderation_roles r JOIN users u ON u.telegram_id=r.user_id
               WHERE r.installation=? AND r.school_id=? AND r.school_year=?""",
            (*scope.school, scope.year),
        )]
        for role in roles:
            role["user_id"] = str(role["user_id"])
            role["permissions"] = json.loads(role["permissions"])
    history = []
    if school_rights or class_rights:
        rows = await app.db.fetch_all(
            """SELECT id, date, end_date, class_key, kind, payload, revoked_at
               FROM moderation_changes WHERE installation=? AND school_id=? AND school_year=?
               ORDER BY id DESC LIMIT 100""",
            (*scope.school, scope.year),
        )
        for row in rows:
            rights = school_rights if not row["class_key"] else (
                class_rights if row["class_key"] == scope.class_key else school_rights
            )
            right = {"bells": "bells", "lesson": "lessons", "announce": "announce"}[row["kind"]]
            if right not in rights and "reports" not in rights:
                continue
            item = {**dict(row), "payload": json.loads(row["payload"]), "can_revoke": right in rights}
            if "reports" in rights:
                counts = await app.db.fetch_all(
                    "SELECT reason, count(*) AS count FROM moderation_reports WHERE change_id=? GROUP BY reason",
                    (row["id"],),
                )
                item["reports"] = [dict(count) for count in counts]
            history.append(item)
    return response({"available": True, "owner": owner, "schools": schools, "school_rights": school_rights,
                     "class_rights": class_rights, "scope": {"installation": scope.installation,
                     "school_id": scope.school_id, "class_key": scope.class_key, "class_label": scope.class_label},
                     "permissions": mod.PERMISSIONS, "roles": roles, "classes": classes, "lessons": lessons, "history": history,
                     "today": user.today().isoformat()})


async def _add_preview(app, scope, lessons):
    by_class = defaultdict(lambda: defaultdict(list))
    for lesson in lessons:
        by_class[lesson["class_key"]][lesson["date"]].append(lesson)
    for class_key, days in by_class.items():
        cohort = mod.Scope(*scope.school, scope.year, class_key)
        changes = await mod.changes_for(app, cohort, min(days), max(days))
        if not changes:
            continue
        for date, originals in days.items():
            day = {"date": date, "lessons": [{
                "lesson_key": lesson["lesson_key"], **{key: lesson[key]
                    for key in ("number", "subject", "start", "end", "room")}
            } for lesson in originals]}
            mod.apply_day(day, changes)
            if not day.get("moderation_notices"):
                continue
            effective = {lesson.get("lesson_key"): lesson for lesson in day["lessons"]}
            for lesson in originals:
                lesson["effective"] = effective[lesson["lesson_key"]]


@routes.post("/api/moderation/schools")
async def schools(request):
    app, user = context(request), current_user(request)
    if not access.owner(app, user.telegram_id):
        return json_error("Доступно владельцу", status=403)
    data = await body(request)
    host, title, school_id = data.get("installation"), data.get("title"), data.get("school_id")
    if (not isinstance(host, str) or not host.strip() or len(host) > 250 or "://" in host
        or not isinstance(title, str) or not title.strip() or len(title) > 150
        or type(school_id) is not int or not 0 < school_id < 2**63
        or type(data.get("enabled", True)) is not bool):
        return json_error("Проверьте адрес, название и ID школы")
    school = host.strip().lower(), school_id
    is_enabled = data.get("enabled", True)
    async with app.db.transaction() as db:
        await db.execute(
            """INSERT INTO moderation_schools VALUES (?, ?, ?, ?)
               ON CONFLICT(installation, school_id) DO UPDATE SET title=excluded.title, enabled=excluded.enabled""",
            (*school, title.strip(), int(is_enabled)),
        )
        if not is_enabled:
            await db.execute("DELETE FROM moderation_lessons WHERE installation=? AND school_id=?", school)
            await db.execute("DELETE FROM moderation_classes WHERE installation=? AND school_id=?", school)
    return response(True)


@routes.post("/api/moderation/schools/resolve")
async def resolve_school(request):
    app, user = context(request), current_user(request)
    if not access.owner(app, user.telegram_id):
        return json_error("Доступно владельцу", status=403)
    account_no = (await body(request)).get("account_no")
    if type(account_no) is not int or not 0 < account_no < 2**63:
        return json_error("Введите номер аккаунта")
    row = await app.db.fetch_one("SELECT telegram_id FROM users WHERE account_no=?", (account_no,))
    target = await app.users.get(row["telegram_id"]) if row else None
    scope = await mod.scope_for(app, target, require_enabled=False) if target else None
    if not scope:
        return json_error("Не удалось подтвердить школу по сессии СГО", status=404)
    return response({"installation": scope.installation, "school_id": scope.school_id, "title": target.school.name})


@routes.post("/api/moderation/quick-grant")
async def quick_grant(request):
    app, user = context(request), current_user(request)
    if not access.owner(app, user.telegram_id):
        return json_error("Доступно владельцу", status=403)
    account_no = (await body(request)).get("account_no")
    if type(account_no) is not int or not 0 < account_no < 2**63:
        return json_error("Введите номер аккаунта СГОшки")
    row = await app.db.fetch_one("SELECT telegram_id FROM users WHERE account_no=?", (account_no,))
    target = await app.users.get(row["telegram_id"]) if row else None
    if not target:
        return json_error("Аккаунт не найден", status=404)
    scope = await mod.scope_for(app, target, require_enabled=False)
    if not scope:
        return json_error("Школа не подтверждена. Попроси пользователя обновить дневник в СГОшке.")
    key = (target.telegram_id, *scope.school, scope.year, "")
    async with app.db.transaction() as db:
        await db.execute(
            """INSERT INTO moderation_schools VALUES (?, ?, ?, 1)
               ON CONFLICT(installation, school_id) DO UPDATE SET enabled=1""",
            (*scope.school, target.school.name),
        )
        existing = await app.db.fetch_one(
            """SELECT permissions FROM moderation_roles
               WHERE user_id=? AND installation=? AND school_id=? AND school_year=? AND class_key=?""", key,
        )
        if not existing:
            await db.execute(
                "INSERT INTO moderation_roles VALUES (?, ?, ?, ?, ?, ?)",
                (*key, json.dumps(["announce", "bells", "read", "reports"])),
            )
    await mod.remember_class(app, scope)
    return response({"installation": scope.installation, "school_id": scope.school_id, "title": target.school.name})


@routes.post("/api/moderation/roles")
async def roles(request):
    app, user = context(request), current_user(request)
    if not access.owner(app, user.telegram_id):
        return json_error("Доступно владельцу", status=403)
    data = await body(request)
    account_no, permissions = data.get("account_no"), data.get("permissions")
    class_key = data.get("class_key", "")
    if (type(account_no) is not int or not 0 < account_no < 2**63
        or not isinstance(class_key, str) or len(class_key) > 50
        or not isinstance(permissions, list) or len(permissions) > len(mod.PERMISSIONS)
        or any(not isinstance(key, str) or key not in (mod.CLASS_PERMISSIONS if class_key else mod.PERMISSIONS)
               for key in permissions)):
        return json_error("Проверьте аккаунт, класс и права")
    row = await app.db.fetch_one("SELECT telegram_id FROM users WHERE account_no=?", (account_no,))
    if not row:
        return json_error("Аккаунт не найден", status=404)
    target = await app.users.get(row["telegram_id"])
    if permissions:
        scope = await mod.scope_for(app, target) if target else None
        if not scope or (data.get("installation"), data.get("school_id")) != scope.school:
            return json_error("Школа аккаунта не подтверждена")
        if class_key and class_key != scope.class_key:
            return json_error("Этот класс не подтверждён для аккаунта")
    else:
        host, school_id = data.get("installation"), data.get("school_id")
        if not isinstance(host, str) or type(school_id) is not int or not 0 < school_id < 2**63:
            return json_error("Проверьте школу")
        scope = mod.Scope(host, school_id, mod.school_year(user.today()))
    key = (row["telegram_id"], *scope.school, scope.year, class_key)
    if permissions:
        await app.db.execute(
            """INSERT INTO moderation_roles VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, installation, school_id, school_year, class_key)
               DO UPDATE SET permissions=excluded.permissions""", (*key, json.dumps(sorted(set(permissions)))),
        )
    else:
        await app.db.execute(
            "DELETE FROM moderation_roles WHERE user_id=? AND installation=? AND school_id=? AND school_year=? AND class_key=?",
            key,
        )
    return response(True)


@routes.post("/api/moderation/shift")
async def shift(request):
    app, user = context(request), current_user(request)
    if not access.owner(app, user.telegram_id):
        return json_error("Доступно владельцу", status=403)
    data = await body(request)
    host, school_id, class_key, value = (
        data.get("installation"), data.get("school_id"), data.get("class_key"), data.get("shift")
    )
    if (not isinstance(host, str) or type(school_id) is not int or not 0 < school_id < 2**63
        or not isinstance(class_key, str) or not class_key or len(class_key) > 50
        or type(value) is not int or value not in (0, 1, 2)):
        return json_error("Проверьте класс и смену")
    scope = mod.Scope(host, school_id, mod.school_year(user.today()), class_key)
    if not await mod.enabled(app, scope):
        return json_error("Школа не подключена", status=404)
    if value:
        await app.db.execute(
            """INSERT INTO moderation_shifts VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(installation, school_id, school_year, class_key)
               DO UPDATE SET shift=excluded.shift""", (*scope.cohort, value),
        )
    else:
        await app.db.execute(
            """DELETE FROM moderation_shifts
               WHERE installation=? AND school_id=? AND school_year=? AND class_key=?""", scope.cohort,
        )
    return response(True)


async def target_scope(app, user, data, class_key, right):
    if access.owner(app, user.telegram_id) and "school_id" in data:
        school_id, host = data["school_id"], data.get("installation")
        if type(school_id) is not int or not 0 < school_id < 2**63 or not isinstance(host, str):
            return None
        scope = mod.Scope(host, school_id, mod.school_year(user.today()))
    else:
        scope = await mod.scope_for(app, user)
    if not scope:
        return None
    if class_key not in ("", scope.class_key):
        school_scope = mod.Scope(*scope.school, scope.year)
        if not await mod.allowed(app, user.telegram_id, school_scope, right):
            return None
    return mod.Scope(*scope.school, scope.year, class_key)


@routes.post("/api/moderation/changes")
async def publish(request):
    app, user = context(request), current_user(request)
    data = await body(request)
    kind, class_key = data.get("kind"), data.get("class_key", "")
    if (kind not in ("bells", "lesson", "announce") or not isinstance(class_key, str)
        or len(class_key) > 50 or (kind == "bells" and class_key) or (kind == "lesson" and not class_key)):
        return json_error("Проверьте тип правки и класс")
    try:
        date = dt.date.fromisoformat(data.get("date", ""))
        end_date = dt.date.fromisoformat(data["end_date"]) if data.get("end_date") else date
    except (ValueError, TypeError):
        return json_error("Проверьте дату")
    right = {"bells": "bells", "lesson": "lessons", "announce": "announce"}[kind]
    scope = await target_scope(app, user, data, class_key, right)
    if not scope:
        return json_error("Школа или класс не подтверждены", status=403)
    today = user.today()
    if not today <= date <= today + dt.timedelta(days=21) or not date <= end_date <= date + dt.timedelta(days=92):
        return json_error("Начните в ближайшие три недели и выберите период до 92 дней")
    if mod.school_year(end_date) != scope.year:
        return json_error("Выберите даты в пределах учебного года")
    if end_date != date and kind == "announce":
        return json_error("Укажите одну дату для объявления")
    async with app.db.transaction() as db:
        if not await mod.allowed(app, user.telegram_id, scope, right):
            return json_error("Недостаточно прав", status=403)
        try:
            if kind == "bells":
                payload = mod.validate_bells(data)
                if end_date != date and data.get("school_days_only"):
                    payload["weekdays"] = [0, 1, 2, 3, 4]
            elif kind == "announce":
                message = data.get("text")
                if not isinstance(message, str) or not message.strip() or len(message) > 1000:
                    raise ValueError("Введите текст объявления до 1000 символов")
                payload = {"text": message.strip()}
            else:
                payload = await _lesson_payload(app, scope, date, data)
                if end_date != date:
                    payload["weekdays"] = [date.weekday()]
        except ValueError as exc:
            return json_error(str(exc))
        if not access.owner(app, user.telegram_id):
            school_scope = mod.Scope(*scope.school, scope.year)
            payload["moderator_role"] = "school" if await mod.allowed(app, user.telegram_id, school_scope, right) else "class"
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        key = (*scope.cohort, date.isoformat(), end_date.isoformat(), kind, encoded)
        existing = await app.db.fetch_one(
            """SELECT id FROM moderation_changes
               WHERE installation=? AND school_id=? AND school_year=? AND class_key=?
               AND date=? AND end_date=? AND kind=? AND payload=? AND revoked_at IS NULL
               ORDER BY id DESC LIMIT 1""", key,
        )
        if existing:
            return response({"id": existing["id"]})
        cursor = await db.execute(
            """INSERT INTO moderation_changes
               (installation, school_id, school_year, class_key, date, end_date, kind, payload, author_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*key, user.telegram_id, int(time.time())),
        )
        change_id = cursor.lastrowid
        await cursor.close()
    return response({"id": change_id})


async def _get_lesson(app, scope, date, key):
    if not isinstance(key, str) or len(key) != 64:
        raise ValueError("Выберите урок")
    row = await app.db.fetch_one(
        """SELECT payload FROM moderation_lessons
           WHERE installation=? AND school_id=? AND school_year=? AND class_key=? AND date=? AND lesson_key=?""",
        (*scope.cohort, date.isoformat(), key),
    )
    if row is None:
        raise ValueError("Урок отсутствует в общем расписании")
    return json.loads(row["payload"])


async def _lesson_payload(app, scope, date, data):
    if data.get("action") == "add":
        subject, room, number = data.get("subject"), data.get("room", ""), data.get("number")
        start, end = data.get("start"), data.get("end")
        if (not isinstance(subject, str) or not subject.strip() or len(subject) > 100
            or not isinstance(room, str) or len(room) > 100
            or type(number) is not int or not 1 <= number <= mod.MAX_LESSONS
            or not mod.clock_time(start) or not mod.clock_time(end) or start >= end):
            raise ValueError("Укажите номер, предмет и время")
        fields = {"number": number, "subject": subject.strip(), "room": room.strip(),
                  "start": start, "end": end, "time": f"{start}–{end}"}
        return {"add": fields, "summary": f'Добавлен {number}-й урок · {fields["subject"]}'}
    key = data.get("lesson_key")
    original = await _get_lesson(app, scope, date, key)
    swap_key = data.get("swap_key")
    if swap_key is not None:
        other = await _get_lesson(app, scope, date, swap_key)
        if original["number"] == other["number"]:
            raise ValueError("Для перестановки выберите разные номера уроков")

        def target(lesson):
            return {**{field: lesson[field] for field in ("number", "start", "end")},
                    "time": f'{lesson["start"]}–{lesson["end"]}'}

        return {"entries": [{"lesson_key": key, "fields": target(other)},
                            {"lesson_key": swap_key, "fields": target(original)}],
                "summary": f'{original["number"]}-й и {other["number"]}-й уроки поменялись местами'}
    cancelled = data.get("cancelled", False)
    if type(cancelled) is not bool:
        raise ValueError("Проверьте отмену урока")
    fields = {}
    summaries = []
    for field, title in (("subject", "Предмет"), ("room", "Кабинет")):
        value = data.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or (field == "subject" and not value.strip()) or len(value) > 100:
            raise ValueError("Проверьте название предмета или кабинета")
        if value.strip() != original[field]:
            fields[field] = value.strip()
            summaries.append(f"{title} → {value.strip() or 'не указан'}")
    number = data.get("number", original["number"])
    if type(number) is not int or not 1 <= number <= mod.MAX_LESSONS:
        raise ValueError("Проверьте номер урока")
    moved = number != original["number"]
    start, end = data.get("start"), data.get("end")
    clock_override = False
    if moved or start or end:
        if not mod.clock_time(start) or not mod.clock_time(end) or start >= end:
            raise ValueError("Укажи начало и конец урока. Конец должен быть позже начала.")
        if moved or (start, end) != (original["start"], original["end"]):
            fields.update(start=start, end=end, time=f"{start}–{end}")
            if moved:
                fields["number"] = number
                summaries.append(f'Перенос на {number}-й урок ({fields["time"]})')
            else:
                clock_override = True
                summaries.append(f'Время изменено на {fields["time"]}')
    if cancelled:
        summaries.append("Урок отменён")
    if not summaries:
        raise ValueError("Укажите, что изменилось")
    return {"lesson_key": key, "fields": fields, "cancelled": cancelled, "clock_override": clock_override,
            "summary": f'{original["number"]}-й урок · {original["subject"]}. ' + ". ".join(summaries)}


def change_id(request):
    raw = request.match_info["change_id"]
    if not raw.isascii() or not raw.isdigit() or len(raw) > 18:
        raise web.HTTPNotFound()
    return int(raw)


@routes.delete("/api/moderation/changes/{change_id}")
async def revoke(request):
    app, user = context(request), current_user(request)
    row = await app.db.fetch_one("SELECT * FROM moderation_changes WHERE id=?", (change_id(request),))
    if not row:
        return json_error("Правка не найдена", status=404)
    right = {"bells": "bells", "lesson": "lessons", "announce": "announce"}[row["kind"]]
    scope = await target_scope(app, user, dict(row), row["class_key"], right)
    if not scope or (*scope.school, scope.year) != (row["installation"], row["school_id"], row["school_year"]):
        return json_error("Недостаточно прав", status=403)
    async with app.db.transaction() as db:
        if not await mod.allowed(app, user.telegram_id, scope, right):
            return json_error("Недостаточно прав", status=403)
        await db.execute(
            "UPDATE moderation_changes SET revoked_at=?, revoked_by=? WHERE id=? AND revoked_at IS NULL",
            (int(time.time()), user.telegram_id, row["id"]),
        )
    return response(True)


@routes.post("/api/moderation/changes/{change_id}/report")
async def report(request):
    app, user = context(request), current_user(request)
    data = await body(request)
    reason = data.get("reason")
    if not isinstance(reason, str) or reason not in mod.REPORT_REASONS:
        return json_error("Выберите причину")
    scope = await mod.diary_scope(app, user)
    if not scope:
        return json_error("Объявление недоступно", status=404)
    now = user.today()
    rows = await mod.changes_for(app, scope, (now - dt.timedelta(days=7)).isoformat(),
                                 (now + dt.timedelta(days=21)).isoformat())
    if not any(row["id"] == change_id(request) for row in rows):
        return json_error("Объявление недоступно", status=404)
    await app.db.execute("INSERT OR IGNORE INTO moderation_reports VALUES (?, ?, ?)",
                         (change_id(request), user.telegram_id, reason))
    return response(True)
