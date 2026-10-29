"""Экран «Администрирование»: всё, что может только владелец (TG_ADMIN_ID).

Тумблеры «Решения всему классу» и «Мой Сетевой город другим
аккаунтам» сохраняются своими запросами (solver.py, account.py);
здесь — общее состояние, вход на сайт через Telegram (как /tgwidget)
и статистика (как /adminstats). Вкладка «Иконки» — app_icon.py.
"""

from __future__ import annotations

import datetime as dt

from aiohttp import web

from ..bot.handlers.admin import WIDGET_MODES, set_widget_mode, stats_report, widget_mode
from ..netschool import ownership
from . import android_updates, app_icon, solver
from ..db.repositories import AppFlagRepository
from .api import context, current_user, json_error
from .api.base import issue_attachment_ticket

routes = web.RouteTableDef()


def _owner(request: web.Request) -> bool:
    return solver.is_owner(context(request), current_user(request))


async def _state(request: web.Request) -> dict:
    app = context(request)
    release = android_updates.release_manifest(android_updates.directory(request))
    return {
        "android_updates": {"on": await android_updates.updates_enabled(app), "available": release is not None,
                            "version": release["versionName"] if release else "", "size": release["size"] if release else 0,
                            "versionCode": release["versionCode"] if release else 0,
                            "blockedVersionCodes": await android_updates.blocked_versions(app),
                            "versions": android_updates.published_versions(android_updates.directory(request))},
        "solver_class": {"on": await solver.class_open(app)} if app.settings.assistant.enabled else None,
        "share_sgo": {"on": await ownership.sharing_allowed(app)},
        "widget": {
            "mode": await widget_mode(app),
            "domain": app.settings.web.login_widget_domain,
            "reserve": bool(app.settings.web.login_widget_bot_token),
        },
        "icons_pending": len(await app_icon.AppIconRepository(app.db).listing("pending")),
    }


def android_apk_info(app) -> dict | None:
    """Размер и время сборки APK для строки в настройках владельца; None — файла нет."""
    root = app.settings.data_dir / "android-releases"
    release = android_updates.release_manifest(root)
    path = root / release["file"] if release else app.settings.android_apk
    if not path.is_file():
        return None
    stat = path.stat()
    updated = dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc)
    return {"size": stat.st_size, "updated": updated.isoformat(timespec="seconds").replace("+00:00", "Z")}


@routes.get("/api/admin/android.apk")
async def admin_android_apk(request: web.Request) -> web.StreamResponse:
    """Актуальный Android-клиент — только владельцу.

    Ссылка открывается во внешнем браузере, поэтому доступ — по билету на
    вложения (api/base.py), как у файлов заданий."""
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    root = android_updates.directory(request)
    release = android_updates.release_manifest(root)
    path = root / release["file"] if release else context(request).settings.android_apk
    if not path.is_file():
        return json_error("APK ещё не загружен на сервер", status=404)
    return web.FileResponse(path, headers={
        "Content-Type": "application/vnd.android.package-archive",
        "Content-Disposition": f'attachment; filename="{path.name}"',
        "Cache-Control": "no-store",
    })


@routes.get("/api/admin")
async def admin_state(request: web.Request) -> web.Response:
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    return web.json_response({"ok": True, "data": await _state(request)})


@routes.post("/api/admin/widget")
async def admin_widget(request: web.Request) -> web.Response:
    """Вход на сайт и привязка Telegram: основной бот, резервный или выключено."""
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    try:
        body = await request.json()
    except ValueError:
        body = None
    mode = body.get("mode") if isinstance(body, dict) else None
    if mode not in WIDGET_MODES:
        return json_error("Некорректный запрос")
    await set_widget_mode(context(request), mode)
    return web.json_response({"ok": True, "data": await _state(request)})


@routes.get("/api/admin/stats")
async def admin_stats(request: web.Request) -> web.Response:
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    return web.json_response({"ok": True, "data": {"html": await stats_report(context(request))}})


@routes.post("/api/admin/android-updates")
async def android_updates_toggle(request: web.Request) -> web.Response:
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("on"), bool):
        return json_error("Некорректный запрос")
    async with request.app[android_updates.POLICY_LOCK]:
        if not body["on"] and await android_updates.blocked_versions(context(request)):
            return json_error("Сначала разрешите отключённые версии APK: пользователям нужен доступ к обновлению")
        await AppFlagRepository(context(request).db).set("android_updates_disabled", not body["on"])
    return web.json_response({"ok": True, "data": await _state(request)})


@routes.post("/api/admin/android-version")
async def android_version_toggle(request: web.Request) -> web.Response:
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    try:
        body = await request.json()
    except ValueError:
        body = None
    code = body.get("versionCode") if isinstance(body, dict) else None
    up_to = body.get("upTo") if isinstance(body, dict) else None
    blocked = body.get("blocked") if isinstance(body, dict) else None
    if type(blocked) is not bool or (up_to is None and (type(code) is not int or not 0 < code <= 2_100_000_000)) \
            or (up_to is not None and (type(up_to) is not int or not 0 < up_to <= 2_100_000_000)):
        return json_error("Укажите версию и состояние blocked")
    app = context(request)
    async with request.app[android_updates.POLICY_LOCK]:
        release = android_updates.release_manifest(android_updates.directory(request))
        if up_to is not None:
            # «Эта версия и ниже»: все выпущенные сборки до выбранной включительно.
            codes = [item["versionCode"] for item in android_updates.published_versions(android_updates.directory(request))
                     if item["versionCode"] <= up_to]
        else:
            codes = [code]
        if blocked:
            existing = await android_updates.blocked_versions(app)
            if len(set(existing) | set(codes)) > 1000:
                return json_error("Слишком много отключённых версий")
            if not await android_updates.updates_enabled(app) or release is None:
                return json_error("Сначала опубликуйте более новую сборку и включите обновления APK")
            if up_to is not None:
                # Актуальную сборку не отключаем: обновляться пользователям некуда.
                codes = [item for item in codes if item < release["versionCode"]]
            elif release["versionCode"] <= code:
                return json_error("Сначала опубликуйте более новую сборку и включите обновления APK")
        for item in codes:
            await AppFlagRepository(app.db).set(android_updates.BLOCK_PREFIX + str(item), blocked)
    return web.json_response({"ok": True, "data": await _state(request)})


@routes.post("/api/admin/android-link")
async def android_link(request: web.Request) -> web.Response:
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    if android_updates.release_manifest(android_updates.directory(request)) is None:
        return json_error("Сборка ещё не опубликована", status=404)
    ticket = issue_attachment_ticket(context(request), current_user(request).telegram_id)
    url = context(request).settings.web.miniapp_url.rstrip("/") + "/api/admin/android.apk?ticket=" + ticket
    return web.json_response({"ok": True, "data": {"url": url}})
