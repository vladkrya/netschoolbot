"""Экран «Администрирование»: всё, что может только владелец (TG_ADMIN_ID).

Тумблеры «Решения всему классу», «Чаты» и «Мой Сетевой город другим
аккаунтам» сохраняются своими запросами (solver.py, chat.py, account.py);
здесь — общее состояние, вход на сайт через Telegram (как /tgwidget)
и статистика (как /adminstats).
"""

from __future__ import annotations

from aiohttp import web

from ..bot.handlers.admin import WIDGET_MODES, set_widget_mode, stats_report, widget_mode
from ..netschool import ownership
from . import chat, solver
from .api import context, current_user, json_error

routes = web.RouteTableDef()


def _owner(request: web.Request) -> bool:
    return solver.is_owner(context(request), current_user(request))


async def _state(request: web.Request) -> dict:
    app = context(request)
    return {
        "solver_class": {"on": await solver.class_open(app)} if app.settings.assistant.enabled else None,
        "chats": {"on": await chat.chats_on(app)},
        "share_sgo": {"on": await ownership.sharing_allowed(app)},
        "widget": {
            "mode": await widget_mode(app),
            "domain": app.settings.web.login_widget_domain,
            "reserve": bool(app.settings.web.login_widget_bot_token),
        },
    }


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
