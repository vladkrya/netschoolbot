"""Страница статуса «Сетевого города» — открыта всем, без входа.

Пути без `/api/`: иначе запрос ушёл бы в проверку входа (api.auth_middleware).
"""

from __future__ import annotations

import asyncio
import json

import aiohttp_jinja2
from aiohttp import web

from .. import __version__
from .api import CONTEXT

routes = web.RouteTableDef()


def _service(request: web.Request):
    return request.app[CONTEXT].status


@routes.get("/status")
async def status_page(request: web.Request) -> web.Response:
    from .server import STATIC_VERSION, _base

    # embed=1 — экран «Статус серверов» в приложении: без заголовка (он у
    # экрана свой) и в теме приложения.
    theme = request.query.get("theme", "")
    response = aiohttp_jinja2.render_template(
        "status.html", request,
        {"base": _base(request), "version": STATIC_VERSION, "app_version": __version__,
         "embed": request.query.get("embed") == "1",
         "theme": theme if theme in ("light", "dark") else ""},
    )
    response.headers["Cache-Control"] = "no-cache"
    return response


@routes.get("/status/")
async def status_slash(request: web.Request) -> web.Response:
    from .server import _base

    raise web.HTTPFound(f"{_base(request)}/status")


@routes.get("/status/data")
async def status_data(request: web.Request) -> web.Response:
    data = await _service(request).summary(request.query.get("range", "24h"))
    return web.json_response(data, headers={"Cache-Control": "no-cache"})


@routes.post("/status/report")
async def status_report(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "Не удалось прочитать жалобу"}, status=400)
    # Поле-ловушка: человек его не видит и не заполняет.
    if body.get("website"):
        return web.json_response({"ok": True})
    ip = request.headers.get("X-Real-IP") or request.remote or "?"
    refused = await _service(request).report(
        str(body.get("host") or ""), str(body.get("kind") or ""), str(body.get("comment") or ""), ip)
    if refused:
        return web.json_response({"ok": False, "error": refused}, status=400)
    return web.json_response({"ok": True})


@routes.get("/status/ws")
async def status_socket(request: web.Request) -> web.WebSocketResponse:
    """Только публичный сигнал об изменении; снимок клиент читает по HTTP."""
    service = _service(request)
    socket = web.WebSocketResponse(heartbeat=25, max_msg_size=1024)
    await socket.prepare(request)
    queue = service.subscribe()

    async def publish() -> None:
        while True:
            revision = await queue.get()
            if revision is None:
                return
            await socket.send_json({"t": "status", "revision": revision})

    async def receive() -> None:
        async for _message in socket:
            pass

    tasks = [asyncio.create_task(publish()), asyncio.create_task(receive())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (ConnectionError, RuntimeError):
        pass
    finally:
        service.unsubscribe(queue)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await socket.close()
    return socket


# ───────────────────────── из приложения (со входом) ─────────────────────────


async def _school_url(request: web.Request) -> tuple[int, str]:
    """Кому сообщать и о каком сервере: о школе открытого дневника — своего
    или того, что приглашённый смотрит."""
    from . import viewing
    from .api import context, current_user

    app, user = context(request), current_user(request)
    owner = await viewing.target(app, user)
    return user.telegram_id, (owner or user).school.url


@routes.get("/api/sgo/server")
async def sgo_server(request: web.Request) -> web.Response:
    """Что с сервером «Сетевого города» школы — приложение спрашивает,
    когда дневник грузится долго или школа не ответила."""
    from .api import context, json_error

    telegram_id, url = await _school_url(request)
    if not url:
        return json_error("Школа не выбрана")
    data = await context(request).sgo_alerts.info(telegram_id, url)
    return web.json_response({"ok": True, "data": data})


@routes.post("/api/sgo/notify")
async def sgo_notify(request: web.Request) -> web.Response:
    """Подписка: «сообщить, когда заработает» (up) или «об обновлениях» (updates)."""
    from ..status.alerts import KINDS
    from .api import context, json_error

    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict) or body.get("kind") not in KINDS:
        return json_error("Некорректный запрос")
    telegram_id, url = await _school_url(request)
    if not url:
        return json_error("Школа не выбрана")
    data = await context(request).sgo_alerts.subscribe(telegram_id, url, body["kind"], bool(body.get("on", True)))
    return web.json_response({"ok": True, "data": data})
