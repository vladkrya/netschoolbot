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
from . import app_icon, solver
from .api import context, current_user, json_error

routes = web.RouteTableDef()


def _owner(request: web.Request) -> bool:
    return solver.is_owner(context(request), current_user(request))


async def _state(request: web.Request) -> dict:
    app = context(request)
    return {
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
    path = app.settings.android_apk
    if not path.is_file():
        return None
    stat = path.stat()
    updated = dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc)
    return {"size": stat.st_size, "updated": updated.isoformat(timespec="seconds").replace("+00:00", "Z")}


@routes.get("/api/admin/android.apk")
async def admin_android_apk(request: web.Request) -> web.StreamResponse:
    """Android-клиент (неофициальный, debug-сборка) — только владельцу.

    Ссылка открывается во внешнем браузере, поэтому доступ — по билету на
    вложения (api/base.py), как у файлов заданий."""
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    path = context(request).settings.android_apk
    if not path.is_file():
        return json_error("APK ещё не загружен на сервер", status=404)
    return web.FileResponse(path, headers={
        "Content-Type": "application/vnd.android.package-archive",
        "Content-Disposition": 'attachment; filename="dnevnik.apk"',
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
