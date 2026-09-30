"""Веб-сервер мини-приложения.

aiohttp вместо Flask + flask-socketio + eventlet в отдельном потоке: веб
живёт в том же event loop, что и бот, и они делят один пул сессий
«Сетевого города» и одно соединение с базой.

Всё приложение монтируется подприложением под своим префиксом (по
умолчанию `/mini/netschool`). Это не косметика: страница строит ссылки на
статику и API от этого префикса, и когда маршруты висели в корне, браузер
получал 404 на CSS, JS и на каждый запрос данных — работал только голый
HTML-каркас.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import aiohttp_jinja2
import jinja2
from aiohttp import web

from ..context import AppContext
from . import api
from .api import BOT, CONTEXT

logger = logging.getLogger("netschoolbot.web")

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Версия для обхода кэша браузера при обновлении статики.
STATIC_VERSION = str(int(time.time()))

page_routes = web.RouteTableDef()


def create_app(context: AppContext, bot=None) -> web.Application:
    """Собрать сервер: подприложение под префиксом плюс проверка живости."""
    inner = web.Application(middlewares=[api.error_middleware, api.auth_middleware])
    inner[CONTEXT] = context
    inner[BOT] = bot
    async def stop_refreshes(app):
        await context.stop_cache_refreshes()

    inner.on_cleanup.append(stop_refreshes)

    aiohttp_jinja2.setup(
        inner,
        loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
    )

    inner.add_routes(api.routes)
    inner.add_routes(page_routes)
    # Те же файлы под путём с версией. Модули JS импортируют друг друга
    # относительными путями без ?v=, а Cloudflare навязывает браузеру кэш
    # на 4 часа поверх наших заголовков — после деплоя экраны оставались
    # старыми. С версией в пути каждый деплой даёт всем модулям новые адреса.
    inner.router.add_static(f"/static/v{STATIC_VERSION}", STATIC_DIR, name="static_versioned")
    # Порядок важен: иначе /static перехватит и путь с версией.
    inner.router.add_static("/static", STATIC_DIR, name="static")
    inner.on_response_prepare.append(_revalidate_static)

    prefix = context.settings.web.miniapp_path.rstrip("/")
    if not prefix:
        # Приложение отдаётся с корня домена — вложенность не нужна.
        inner.router.add_get("/health", health)
        return inner

    outer = web.Application(middlewares=[_make_redirect_middleware(prefix)])
    outer[CONTEXT] = context
    outer.add_subapp(prefix, inner)

    # Проверка живости остаётся в корне: ею пользуются systemd и мониторинг,
    # которым незачем знать про префикс приложения.
    outer.router.add_get("/health", health)
    return outer


async def _revalidate_static(request: web.Request, response: web.StreamResponse) -> None:
    """Статику браузер обязан перепроверять.

    Версия в адресе есть только у app.js, а остальные модули он импортирует
    без неё. Без заголовка WebView Telegram брал их из своего кэша, и после
    обновления экраны оставались старыми. Перепроверка стоит ответа 304.
    """
    if "/static/" in request.path:
        response.headers["Cache-Control"] = "no-cache"


def _make_redirect_middleware(prefix: str):
    """Переадресация на приложение с корня и с префикса без слэша.

    Сделано middleware, а не маршрутом: `add_subapp` забирает себе весь
    путь, начинающийся с префикса, поэтому обычный маршрут на `/mini`
    до обработчика уже не доходит и отдаёт 404. А открывают ссылку люди
    именно так — без завершающего слэша.
    """

    @web.middleware
    async def redirect(request: web.Request, handler):
        if request.path in ("/", prefix):
            target = f"{prefix}/"
            # Токен и данные Telegram живут в query: потерять их при
            # переадресации означало бы «не удалось вас опознать».
            if request.query_string:
                target = f"{target}?{request.query_string}"
            raise web.HTTPFound(target)
        return await handler(request)

    return redirect


async def health(request: web.Request) -> web.Response:
    context: AppContext = request.app[CONTEXT]
    return web.json_response(
        {
            "ok": True,
            "watchers": len(context.watchers.running),
            "sessions": len(context.pool._entries),
        }
    )


def _base(request: web.Request) -> str:
    """Префикс, под которым смонтировано приложение.

    Берётся из настроек, а не из request.path: ссылки в шаблоне должны
    указывать на префикс независимо от того, каким маршрутом пришёл запрос.
    """
    return request.app[CONTEXT].settings.web.miniapp_path.rstrip("/")


@page_routes.get("/")
async def index(request: web.Request) -> web.Response:
    """Страница приложения.

    Открывается двумя путями: кнопкой Telegram Mini App (тогда личность
    подтверждается подписанной initData уже в браузере) и по постоянной
    ссылке с токеном (для установленного на домашний экран приложения).
    Здесь достаточно отдать каркас — данные страница запрашивает сама,
    и там же происходит настоящая проверка доступа.
    """
    context: AppContext = request.app[CONTEXT]
    token = (request.query.get("token") or request.cookies.get("netschool_token") or "").strip()

    user = None
    if telegram_id := await context.miniapp.resolve_token(token):
        user = await context.users.get(telegram_id)

    response = aiohttp_jinja2.render_template(
        "app.html",
        request,
        {
            "base": _base(request),
            "version": STATIC_VERSION,
            "token": token,
            "student": user.student_name if user else "",
            "school": user.school.name if user else "",
            "push_enabled": context.settings.push.configured,
        },
    )
    if token:
        response.set_cookie(
            "netschool_token",
            token,
            max_age=365 * 24 * 3600,
            httponly=True,
            samesite="Lax",
            secure=request.url.scheme == "https",
        )
    return response


@page_routes.get("/demo")
async def demo(request: web.Request) -> web.Response:
    """Демо на выдуманных данных: страница та же, данные генерирует браузер."""
    return aiohttp_jinja2.render_template(
        "app.html",
        request,
        {
            "base": _base(request),
            "version": STATIC_VERSION,
            "token": "",
            "student": "Пятёркин Отличник",
            "school": "Демо-школа",
            "push_enabled": False,
            "demo": True,
        },
    )


@page_routes.get("/manifest.webmanifest")
async def manifest(request: web.Request) -> web.Response:
    context: AppContext = request.app[CONTEXT]
    token = (request.query.get("token") or "").strip()
    base = _base(request)
    user = None
    if telegram_id := await context.miniapp.resolve_token(token):
        user = await context.users.get(telegram_id)

    payload = {
        "name": user.school.name if user and user.school.name else "Сетевой город",
        "short_name": "Дневник",
        "description": "Дневник, оценки и домашние задания",
        # start_url с токеном: иначе установленное приложение открывалось бы
        # на странице «не удалось вас опознать».
        "start_url": f"{base}/?token={token}" if token else f"{base}/",
        "scope": f"{base}/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0d1117",
        "theme_color": "#0d1117",
        "lang": "ru",
        "icons": [
            {
                "src": f"{base}/static/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }
        ],
    }
    return web.json_response(payload, content_type="application/manifest+json")


@page_routes.get("/sw.js")
async def service_worker(request: web.Request) -> web.FileResponse:
    # Service worker обязан отдаваться с корня своей области видимости,
    # поэтому у него отдельный маршрут, а не путь внутри /static.
    return web.FileResponse(
        STATIC_DIR / "sw.js",
        headers={
            "Content-Type": "application/javascript; charset=utf-8",
            "Service-Worker-Allowed": _base(request) + "/",
        },
    )


async def start_web(context: AppContext, bot=None) -> None:
    """Запустить сервер и держать его до отмены задачи."""
    settings = context.settings.web
    runner = web.AppRunner(create_app(context, bot), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, settings.host, settings.port)
    await site.start()
    logger.info(
        "Мини-приложение слушает %s:%s (%s)", settings.host, settings.port, settings.miniapp_url
    )
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
