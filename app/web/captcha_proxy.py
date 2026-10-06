"""KryaCaptcha через домен самого приложения: /captcha-proxy/… → NETSCHOOL_CAPTCHA_URL.

Капча живёт на отдельном сервере и домене. Когда мобильный интернет урезают до
белых списков, он не открывается, а свежий домен ещё и не виден, пока у
устройства не истёк отрицательный кэш DNS, — в обоих случаях рамка писала
«Капча не загрузилась». Через прокси браузеру нужен только адрес приложения,
до которого он и так дошёл.

Прокси узкий: только маршруты, которыми пользуется SDK виджета, и только их
методы. siteverify сюда не входит — его вызывает сервер бота напрямую.
"""

from __future__ import annotations

import logging
import re

import aiohttp
from aiohttp import web

from .api import CONTEXT

logger = logging.getLogger("netschoolbot.web")

routes = web.RouteTableDef()

PREFIX = "/captcha-proxy"
TIMEOUT = 15
MAX_BODY = 64 * 1024

_SESSION = r"[A-Za-z0-9_-]{1,100}"
ALLOWED = (
    ("GET", re.compile(r"sdk/kryacaptcha\.js")),
    ("GET", re.compile(r"css/(main|puzzles)\.css")),
    ("POST", re.compile(r"api/v1/session/new")),
    ("GET", re.compile(rf"api/v1/session/{_SESSION}/challenge")),
    ("POST", re.compile(rf"api/v1/session/{_SESSION}/verify")),
)
PASS_HEADERS = ("Content-Type", "Cache-Control")


def allowed(method: str, tail: str) -> bool:
    return any(method == m and pattern.fullmatch(tail) for m, pattern in ALLOWED)


@routes.route("*", PREFIX + "/{tail:.*}")
async def captcha_proxy(request: web.Request) -> web.Response:
    base = request.app[CONTEXT].settings.web.captcha_url
    tail = request.match_info["tail"]
    if not base or not allowed(request.method, tail):
        raise web.HTTPNotFound()

    body = None
    headers = {}
    if request.method == "POST":
        if (request.content_length or 0) > MAX_BODY:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=request.content_length)
        body = await request.read()
        if len(body) > MAX_BODY:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=len(body))
        headers["Content-Type"] = request.headers.get("Content-Type", "application/json")

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
            async with session.request(
                request.method, f"{base}/{tail}", params=request.query, data=body, headers=headers
            ) as upstream:
                payload = await upstream.read()
                status = upstream.status
                passed = {k: upstream.headers[k] for k in PASS_HEADERS if k in upstream.headers}
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.warning("Капча недоступна через прокси (%s): %s", tail, exc)
        return web.json_response({"success": False, "error": "Сервис капчи не отвечает"}, status=502)
    return web.Response(status=status, body=payload, headers=passed)
