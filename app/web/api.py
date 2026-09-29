"""HTTP API мини-приложения.

Раньше это был файл на 2953 строки, в котором каждый обработчик сам ходил в
«Сетевой город» через `asyncio.run()` — то есть на каждый запрос создавался
новый event loop, а соединения не переиспользовались. Теперь обработчики
асинхронные и работают через общий сервис дневника.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict, replace

from aiohttp import web

from ..context import AppContext
from ..domain import formatting
from ..domain.models import ApiMode, User, normalize
from ..domain.records import DiaryDay, clean_content
from ..netschool.errors import NetSchoolError, Reason
from ..domain.periods import QUARTER_KEYS, quarter_of, quarters
from ..netschool.service import msk_today
from .telegram_auth import InitDataError, verify_init_data

logger = logging.getLogger("netschoolbot.web")

routes = web.RouteTableDef()

# Типизированные ключи вместо строк: aiohttp так избегает конфликтов имён
# между приложением и подключёнными к нему библиотеками.
CONTEXT = web.AppKey("context", AppContext)
BOT = web.AppKey("bot", object)
USER = web.RequestKey("netschool_user", User)


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
    if "/api/" not in request.path:
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
                "marks": [_mark_to_json(m) for m in lesson.marks],
                "homework": [_homework_to_json(h, observed) for h in lesson.homework],
            }
            for lesson in day.lessons
        ],
        "marks": [_mark_to_json(record) for record in day.marks],
        "homework": [_homework_to_json(item, observed) for item in day.homework],
    }


async def _cached(request: web.Request, section: str, producer, *, cacheable=lambda payload: True):
    """Отдать свежий кэш или сходить за данными и обновить его.

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
            return web.json_response({"ok": True, "data": payload, "stale": False})

    try:
        payload = await producer(app, user)
    except NetSchoolError as exc:
        if cached is None:
            raise
        logger.info("Отдаю устаревший кэш %s для %s: %s", section, user.telegram_id, exc.reason)
        return web.json_response(
            {"ok": True, "data": cached[0], "stale": True, "reason": exc.reason.value}
        )

    if cacheable(payload):
        await app.cache.put(user.telegram_id, section, payload)
    return web.json_response({"ok": True, "data": payload, "stale": False})


@routes.get("/api/profile")
async def profile(request: web.Request) -> web.Response:
    user = current_user(request)
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
        today = msk_today() + dt.timedelta(weeks=offset)
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

    async def produce(app: AppContext, user: User):
        today = msk_today()
        year = quarters(today)
        now_quarter = next((q for q in year if q.covers(today)), year[0])
        days = await app.diary.fetch_period(user, year[0].start, now_quarter.end, today=today)

        by_subject: dict[str, list] = {}
        lessons: dict[str, dict[str, dict[str, int]]] = {}
        teachers: dict[str, dict[str, int]] = {}
        # Отметки посещаемости: {предмет: {четверть: {код: сколько}}}.
        absences: dict[str, dict[str, dict[str, int]]] = {}
        for day in days:
            key = quarter_of(day.day)
            for record in day.marks:
                by_subject.setdefault(record.subject, []).append(record)
            for lesson in day.lessons:
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
        totals_by_name = {normalize(name): (name, item) for name, item in totals.items()}
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
                        _mark_to_json(r) for r in sorted(items, key=lambda r: r.date)
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
            "subjects": subjects,
            "average": formatting.weighted_average(records),
            "current_quarter": now_quarter.key,
            # Экран по этому признаку решает, спрашивать ли у сервера
            # аналитику по предмету (средний балл класса).
            "mobile": user.effective_api_mode is ApiMode.MOBILE,
            "has_totals": bool(totals),
            "quarters": [
                {"key": q.key, "label": q.label, "start": q.start.isoformat(), "end": q.end.isoformat()}
                for q in year
            ],
            "year": f"{year[0].start.year}/{year[0].start.year + 1}",
        }

    return await _cached(request, "marks:year", produce)


@routes.get("/api/subject")
async def subject_stats(request: web.Request) -> web.Response:
    """Аналитика по предмету за четверть: средний балл класса и распределение оценок.

    Данные есть только в мобильном режиме. Иначе, а также когда сервер не
    вернул аналитику, ответ — `{"available": false}`: экран просто не
    показывает эту строку.
    """
    subject = (request.query.get("subject") or "").strip()[:120]
    quarter = (request.query.get("quarter") or "").strip()
    if not subject or quarter not in QUARTER_KEYS:
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


@routes.get("/api/homework")
async def homework(request: web.Request) -> web.Response:
    async def produce(app: AppContext, user: User):
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

    return await _cached(request, "homework", produce)


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


@routes.post("/api/mail/send")
async def mail_send(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    try:
        body = await request.json()
    except ValueError:
        return json_error("Некорректный запрос")
    to = [str(item) for item in (body.get("to") or []) if str(item).strip()]
    subject = str(body.get("subject") or "").strip()
    text = str(body.get("text") or "").strip()
    if not to:
        return json_error("Выберите получателя")
    if not subject:
        return json_error("Укажите тему письма")
    if len(subject) > 200 or len(text) > 10000:
        return json_error("Письмо слишком длинное")
    await app.diary.send_mail(user, to, subject, text)
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
    return web.Response(
        body=payload,
        headers={
            # Имя файла не подставляем из данных школы: оно приходит извне и
            # в заголовке требует отдельного экранирования.
            "Content-Disposition": f'attachment; filename="attachment-{attachment_id}"'
        },
    )


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
    "grades", "changes", "deletes", "homework", "mail", "weekly_summary", "absences",
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
