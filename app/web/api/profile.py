"""Профиль, ученик, уведомления, пояс и push — то, что показывают настройки."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, replace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

from ...context import AppContext
from ...domain import formatting
from ...domain.models import TIMEZONES, User
from ...netschool.errors import NetSchoolError
from .base import _utc_iso, context, current_user, halloween_season, json_error, routes


async def _keep_attachments_info(app: AppContext, user: User) -> dict:
    """Тумблер «Сохранять файлы заданий»: есть ли он и включён ли."""
    if app.attachments is None or not app.attachments.enabled:
        return {"available": False}
    return {"available": True, "on": await app.keep_attachments_wanted(user)}


@routes.post("/api/settings/keep-attachments")
async def keep_attachments_setting(request: web.Request) -> web.Response:
    """Свой тумблер «Сохранять файлы заданий»: у всех включён, выключить можно."""
    from ...attachments.keeper import PREF

    app, user = context(request), current_user(request)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return json_error("Некорректный запрос")
    if "on" in body:
        await app.miniapp.save_prefs(user.telegram_id, {PREF: bool(body["on"])})
    return web.json_response({"ok": True, "data": await _keep_attachments_info(app, user)})


async def _sgo_updates(app: AppContext, telegram_id: int, url: str) -> bool:
    from ...db.repositories import SgoStatusRepository
    from ...status.service import host_of

    if not url:
        return False
    return "updates" in await SgoStatusRepository(app.db).alert_kinds(telegram_id, host_of(url))


@routes.get("/api/profile")
async def profile(request: web.Request) -> web.Response:
    from .. import solver, viewing

    app, user = context(request), current_user(request)
    status = await app.state.watch_status(user.telegram_id)
    viewer = request.get(viewing.VIEWER)
    return web.json_response(
        {
            "ok": True,
            "data": {
                # Просмотр дневника родным: интерфейс прячет всё, что меняет данные.
                "readonly": viewer is not None and viewer.telegram_id != user.telegram_id,
                "views": await viewing.summary(
                    app, viewer or user, user if viewer is not None and viewer.telegram_id != user.telegram_id else None),
                "name": user.label,
                "school": user.school.name,
                "students": [
                    {"id": s.id, "name": s.name, "current": s.id == user.selected_student_id}
                    for s in user.available_students
                ],
                "notifications": asdict(user.notifications),
                # «Об обновлениях «Сетевого города»» — подписка на сервер школы (status/alerts.py).
                "sgo_updates": await _sgo_updates(app, (viewer or user).telegram_id, user.school.url),
                # «Сохранять файлы заданий» (бета, attachments/keeper.py).
                "keep_attachments": await _keep_attachments_info(app, viewer or user),
                # Тумблер пропусков показывается только там, где есть отметки посещаемости.
                "api_mode": user.effective_api_mode.value,
                "interval_minutes": user.check_interval // 60,
                "quiet_hours": user.quiet_hours.as_text(),
                "enabled": user.enabled,
                "assistant": app.settings.assistant.enabled,
                # Кнопка «Решение» под заданием (web/solver.py).
                "solver": await solver.solver_access(app, viewer or user),
                # Тумблер «Решения заданий всему классу» — только у владельца.
                "solver_class": {"on": await solver.class_open(app)}
                if app.settings.assistant.enabled and solver.is_owner(app, viewer or user) else None,
                # Хеллоуинское оформление — у всех в праздничный сезон;
                # выключается тумблером в настройках.
                "halloween": halloween_season(user.today()),
                # Без капчи отчёт об ошибке не отправить — пункт меню прячется.
                "bug_report": bool(app.settings.web.captcha_url),
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
    "schedule", "absences", "telegram", "homework_reminder", "test_reminder", "grade_risk",
    "monthly_summary", "all_children", "weekly_review", "vacation_quiet",
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


CLASS_FRESH = dt.timedelta(days=1)


@routes.get("/api/student-class")
async def student_class(request: web.Request) -> web.Response:
    """Класс ученика для настроек. Меняется раз в год — храним сутки, а без
    связи со школой отдаём прошлый ответ или пустую строку."""
    app, user = context(request), current_user(request)
    section = f"class:{user.selected_student_id}"
    cached = await app.cache.get(user.telegram_id, section)
    now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if cached is not None and now_utc - cached[1] < CLASS_FRESH:
        name = cached[0]
    else:
        try:
            name = await app.diary.fetch_class_name(user)
            await app.cache.put(user.telegram_id, section, name)
        except NetSchoolError:
            name = cached[0] if cached is not None else ""
    return web.json_response({"ok": True, "data": {"name": name, "label": formatting.class_label(name)}})


@routes.get("/api/pwa-link")
async def pwa_link(request: web.Request) -> web.Response:
    """Ссылка «Открыть в Safari» из инструкции установки: та же, что
    «Добавить устройство», — 15 минут, одно открытие, свой токен устройства."""
    from ..devices import _device_url

    app, user = context(request), current_user(request)
    code, _ = await app.miniapp.issue_device_code(user.telegram_id)
    return web.json_response({"ok": True, "data": {"url": _device_url(app, code)}})


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
