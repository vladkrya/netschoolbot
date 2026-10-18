"""Страница статуса «Сетевого города» — открыта всем, без входа.

Пути без `/api/`: иначе запрос ушёл бы в проверку входа (api.auth_middleware).
"""

from __future__ import annotations

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

    response = aiohttp_jinja2.render_template(
        "status.html", request,
        {"base": _base(request), "version": STATIC_VERSION, "app_version": __version__},
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
