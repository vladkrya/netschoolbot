"""Веб-сервер мини-приложения.

aiohttp вместо Flask + flask-socketio + eventlet в отдельном потоке: веб
живёт в том же event loop, что и бот, и они делят один пул сессий
«Сетевого города» и одно соединение с базой.

Приложение отдаётся с корня домена либо, если задан NETSCHOOL_MINIAPP_PATH,
подприложением под этим префиксом. Это не косметика: страница строит ссылки на
статику и API от этого префикса, и когда маршруты висели в корне, браузер
получал 404 на CSS, JS и на каждый запрос данных — работал только голый
HTML-каркас.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path

import aiohttp_jinja2
import jinja2
from aiohttp import web

from .. import __version__
from ..context import AppContext
from . import account, api, assistant, bugreport, caldav, captcha_proxy, chat, devices, extras, lock, retro, status, sync
from .api import BOT, CONTEXT

logger = logging.getLogger("netschoolbot.web")

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

def _static_version() -> str:
    """Версия статики — хэш содержимого файлов.

    Раньше это было время запуска, и каждый перезапуск сервера, даже без
    изменений, давал новые адреса: телефоны заново качали всю статику, а на
    медленной сети приложение долго стояло на пустом экране.
    """
    digest = hashlib.sha1()
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(STATIC_DIR)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


STATIC_VERSION = _static_version()
# Модули приложения — в <head> как modulepreload: браузер качает их разом,
# а не цепочкой «app.js → его импорты → их импорты».
JS_MODULES = sorted(path.name for path in (STATIC_DIR / "js").glob("*.js") if path.name != "demo.js")

BOOT_MODULES = ['boot.js', 'core.js', 'icons.js', 'haptics.js', 'lock.js', 'biometrics.js', 'pinpad.js']

page_routes = web.RouteTableDef()

# Прежний адрес приложения. Ссылки на него остались в чатах, на домашних
# экранах и в подписках на push, поэтому он по-прежнему ведёт в приложение.
LEGACY_PREFIX = "/mini/netschool"


@web.middleware
async def compress_static(request: web.Request, handler):
    response = await handler(request)
    if '/static/' in request.path and request.path.endswith(('.js', '.css')):
        response.headers['Vary'] = 'Accept-Encoding'
        if response.status == 200 and 'Range' not in request.headers:
            # Прокси не сжимал JS/CSS: на мобильной сети уходили сотни килобайт.
            response.enable_compression()
    return response


# Встраивать страницы можно только себе и веб-версии Telegram: чужой сайт не
# подложит экран PIN в невидимую рамку и не перехватит нажатия.
FRAME_ANCESTORS = "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"


def _secure(request: web.Request, headers) -> None:
    headers.setdefault('X-Content-Type-Options', 'nosniff')
    headers.setdefault('Referrer-Policy', 'no-referrer')
    headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=()')
    if 'X-Frame-Options' not in headers:
        headers.setdefault('Content-Security-Policy', FRAME_ANCESTORS)
    # Личные данные и ключи входа не оседают в кэшах браузера и прокси.
    if '/api/' in request.path:
        headers.setdefault('Cache-Control', 'no-store')


@web.middleware
async def security_headers(request: web.Request, handler):
    """Защитные заголовки на всех ответах, в том числе на зеркале без nginx."""
    try:
        response = await handler(request)
    except web.HTTPException as error:
        _secure(request, error.headers)
        raise
    _secure(request, response.headers)
    return response


def create_app(context: AppContext, bot=None) -> web.Application:
    """Собрать сервер: подприложение под префиксом плюс проверка живости."""
    inner = web.Application(middlewares=[security_headers, caldav.root_discovery, compress_static, api.error_middleware, api.auth_middleware, sync.sync_middleware])
    inner[CONTEXT] = context
    inner[BOT] = bot
    inner[chat.HUB] = context.hub
    # Проверка оценок сообщает открытому приложению о новом сразу, по сокету.
    context.watchers.live = context.hub.publish_user
    async def stop_refreshes(app):
        context.status.close_listeners()
        await app[chat.HUB].close()
        await context.stop_cache_refreshes()

    inner.on_shutdown.append(stop_refreshes)

    aiohttp_jinja2.setup(
        inner,
        loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
    )

    if context.settings.caldav_enabled:
        from ..caldav.service import CaldavService
        context.caldav = CaldavService(context)
        async def calendar_lifecycle(app):
            context.caldav.start()
            yield
            await context.caldav.close()
        inner.cleanup_ctx.append(calendar_lifecycle)
    inner.add_routes(caldav.routes)
    inner.add_routes(api.routes)
    inner.add_routes(lock.routes)
    inner.add_routes(chat.routes)
    inner.add_routes(account.routes)
    inner.add_routes(devices.routes)
    inner.add_routes(bugreport.routes)
    inner.add_routes(assistant.routes)
    inner.add_routes(extras.routes)
    inner.add_routes(captcha_proxy.routes)
    inner.add_routes(status.routes)
    inner.add_routes(retro.routes)
    inner.add_routes(page_routes)
    inner.add_routes(retro.page_routes)
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
        inner.middlewares.insert(0, _legacy_prefix_middleware)
        inner.router.add_route("*", "/.well-known/caldav", caldav.discovery)
        inner.router.add_get("/health", health)
        return inner

    outer = web.Application(middlewares=[caldav.root_discovery, _make_redirect_middleware(prefix)])
    outer[CONTEXT] = context
    outer.add_subapp(prefix, inner)

    # Проверка живости остаётся в корне: ею пользуются systemd и мониторинг,
    # которым незачем знать про префикс приложения.
    outer.router.add_route("*", "/.well-known/caldav", caldav.discovery)
    outer.router.add_get("/health", health)
    return outer


async def _revalidate_static(request: web.Request, response: web.StreamResponse) -> None:
    """Кэш статики.

    По пути с версией (/static/v<хэш>/) файл не меняется никогда: новая
    версия — новый адрес. Его можно хранить сколько угодно и не спрашивать
    сервер при каждом запуске — на мобильной сети эти перепроверки двух
    десятков модулей и были заметной частью пустого экрана.

    Без версии статику браузер обязан перепроверять: без заголовка WebView
    Telegram брал старые модули из своего кэша.
    """
    if "/static/" not in request.path:
        return
    if f"/static/v{STATIC_VERSION}/" in request.path and response.status == 200:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
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


@web.middleware
async def _legacy_prefix_middleware(request: web.Request, handler):
    """Старые адреса `/mini/netschool/...` ведут на те же пути от корня.

    Service worker отдаётся на месте, а не переадресацией: браузер не
    обновляет его по редиректу, и вместе с ним отвалились бы push-подписки
    уже установленных приложений.
    """
    path = request.path
    if path != LEGACY_PREFIX and not path.startswith(LEGACY_PREFIX + "/"):
        return await handler(request)
    if path == f"{LEGACY_PREFIX}/sw.js":
        return await service_worker(request)
    target = path[len(LEGACY_PREFIX):] or "/"
    if request.query_string:
        target = f"{target}?{request.query_string}"
    # 307 сохраняет метод и тело: устаревший JS шлёт и POST-запросы к API.
    if request.method in ("GET", "HEAD"):
        raise web.HTTPFound(target)
    raise web.HTTPTemporaryRedirect(target)


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


# Превью ссылок в мессенджерах загружают страницу сами — одноразовую ссылку
# с экрана «Добавить устройство» это погасило бы раньше, чем её откроет человек.
LINK_PREVIEWS = re.compile(
    r"TelegramBot|Twitterbot|facebookexternalhit|WhatsApp|vkShare|Slackbot|Discordbot|SkypeUriPreview"
    r"|Viber|LinkedInBot|Googlebot|bingbot|YandexBot|Applebot|redditbot|curl/|python-|Go-http-client",
    re.IGNORECASE,
)


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
    # Иконка на экране «Домой», первый запуск: код из start_url меняется на
    # свой токен устройства в cookie, и иконка открывает чистый адрес.
    if (icon := request.query.get("icon")) and (telegram_id := await context.miniapp.use_icon_code(icon)):
        # #own — токен уже свой, заводить его заново незачем (boot.js, ownToken).
        response = web.HTTPFound(f"{_base(request)}/#own")
        _set_token_cookie(request, response, await context.miniapp.issue_token(telegram_id, device=True))
        raise response
    # Ссылка установки прежнего /app_install (новых не выдаём, старые живут
    # до суток): адрес с кодом не меняем — из
    # встроенного браузера Telegram его ещё откроют «в Safari», и там код
    # тоже должен сработать (сутки, каждый раз свой токен устройства).
    entered = ""
    if (enter := request.query.get("enter")) and (telegram_id := await context.miniapp.use_enter_code(enter)):
        current = await context.miniapp.resolve_token(request.cookies.get("netschool_token", ""))
        if current == telegram_id and await context.miniapp.is_device_token(request.cookies["netschool_token"]):
            entered = request.cookies["netschool_token"]  # обновили страницу — токен уже есть
        else:
            entered = await context.miniapp.issue_token(telegram_id, device=True)
    # Ссылка с экрана «Добавить устройство»: открывается один раз — токен
    # уходит в cookie, а адрес сразу чистый. Погасший код в адресе (обновили
    # страницу, добавили на экран «Домой») больше нигде не остаётся.
    if (
        (device_code := request.query.get("device"))
        and request.method == "GET"
        and not LINK_PREVIEWS.search(request.headers.get("User-Agent", ""))
    ):
        response = web.HTTPFound(f"{_base(request)}/" + ("?install=1" if request.query.get("install") == "1" else ""))
        if issued := await context.miniapp.use_device_code(device_code):
            await devices.touch(request, issued)
            _set_token_cookie(request, response, issued)
        raise response
    # Ссылка с токеном важнее cookie, но отозванный токен в ссылке (старая
    # иконка на домашнем экране после «Выйти») не мешает войти по cookie.
    token, user = "", None
    for candidate in (entered, request.query.get("token"), request.cookies.get("netschool_token")):
        candidate = (candidate or "").strip()
        if candidate and (telegram_id := await context.miniapp.resolve_token(candidate)):
            token, user = candidate, await context.users.get(telegram_id)
            break

    protected = bool(user and await lock.enabled(context.db, user.telegram_id))
    response = aiohttp_jinja2.render_template(
        "app.html",
        request,
        {
            "base": _base(request),
            "version": STATIC_VERSION,
            "app_version": __version__,
            "modules": JS_MODULES,
            "boot_modules": BOOT_MODULES,
            "token": token,
            "student": user.student_name if user and not protected else "",
            "school": user.school.name if user and not protected else "",
            "push_enabled": context.settings.push.configured,
            "assistant": context.settings.assistant.enabled,
            "attachment_hosts": context.settings.web.attachment_hosts,
        },
    )
    if not token and request.cookies.get("netschool_token"):
        response.del_cookie("netschool_token")
    if token:
        _set_token_cookie(request, response, token)
    return response


def _set_token_cookie(request: web.Request, response: web.StreamResponse, token: str) -> None:
    response.set_cookie(
        "netschool_token",
        token,
        max_age=365 * 24 * 3600,
        httponly=True,
        samesite="Lax",
        secure=request.url.scheme == "https",
    )


@page_routes.get("/attachment/{attachment_id:\\d+}")
async def attachment_page(request: web.Request) -> web.Response:
    """Страница ожидания вложения: сервер школы бывает перегружен, и прямая
    ссылка на файл минутами держала пустую вкладку. Здесь — анимация,
    прогресс, понятная ошибка и «Повторить». Файл качает сам скрипт страницы
    с тем же билетом; данных пользователя в странице нет."""
    response = aiohttp_jinja2.render_template(
        "attachment.html", request, {
            "name": request.query.get("name", "")[:200],
            "waiting": "Скачиваем файл из «Сетевого города»",
        })
    response.headers["Cache-Control"] = "no-store"
    return response


EXPORT_NAMES = {
    "grades": "Оценки", "subjects": "Сводка по предметам", "dynamics": "Динамика оценок",
    "tests": "Контрольные", "absences": "Пропуски", "homework": "Задания", "lessons": "Уроки",
    "schedule": "Расписание",
}


@page_routes.get("/export/{scope:(period|year)}")
async def export_page(request: web.Request) -> web.Response:
    """Отчёты в Excel — та же страница ожидания, что у вложений: книга за год
    собирается до минуты, а потом открывается просмотром или скачивается."""
    response = aiohttp_jinja2.render_template(
        "attachment.html", request, {
            "name": f"{EXPORT_NAMES.get(request.query.get('report', ''), 'Оценки')} за "
                    + ("год" if request.match_info["scope"] == "year" else "период"),
            "waiting": "Собираем отчёт — за год это может занять до минуты",
        })
    response.headers["Cache-Control"] = "no-store"
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
            "app_version": __version__,
            "modules": JS_MODULES,
            "boot_modules": BOOT_MODULES,
            "token": "",
            "student": "Пятёркин Отличник",
            "school": "Демо-школа",
            "push_enabled": False,
            "assistant": False,
            "demo": True,
        },
    )


@page_routes.get("/captcha-frame")
async def captcha_frame(request: web.Request) -> web.Response:
    """Рамка с KryaCaptcha для отчёта об ошибке — см. templates/captcha.html.

    Скрипт, стили и API капчи идут через /captcha-proxy на этом же домене.
    """
    settings = request.app[CONTEXT].settings.web
    if not settings.captcha_url:
        raise web.HTTPNotFound()
    response = aiohttp_jinja2.render_template(
        "captcha.html",
        request,
        {"base": _base(request), "rounds": settings.captcha_rounds},
    )
    # Встраивать рамку можно только в своё же приложение.
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Cache-Control"] = "no-store"
    return response


@page_routes.get("/manifest.webmanifest")
async def manifest(request: web.Request) -> web.Response:
    context: AppContext = request.app[CONTEXT]
    token = (request.query.get("token") or "").strip()
    base = _base(request)
    user = None
    if telegram_id := await context.miniapp.resolve_token(token):
        user = await context.users.get(telegram_id)

    start_url = f"{base}/"
    if user and await context.miniapp.is_device_token(token):
        start_url = f"{base}/?icon={await context.miniapp.icon_code(user.telegram_id, token)}"
    elif token:
        start_url = f"{base}/?token={token}"
    payload = {
        "name": user.school.name if user and user.school.name else "Сетевой город",
        "short_name": "Дневник",
        "description": "Дневник, оценки и домашние задания",
        # start_url с токеном: иначе установленное приложение открывалось бы
        # на странице «не удалось вас опознать». Вошли на этом устройстве
        # (ссылка установки, бот, почта) — вместо токена одноразовый код:
        # первый запуск иконки меняет его на свой токен, и через «Поделиться»
        # иконки уходит адрес, который уже ничего не открывает.
        "start_url": start_url,
        "scope": f"{base}/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0d1117",
        "theme_color": "#0d1117",
        "lang": "ru",
        "icons": [
            {
                "src": f"{base}/static/icon-{size}.png",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "any maskable",
            }
            for size in (192, 512)
        ],
    }
    return web.json_response(payload, content_type="application/manifest+json")


@page_routes.get("/time")
async def server_time(request: web.Request) -> web.Response:
    # Часы телефонов расходятся на десятки секунд, и таймер урока на двух
    # телефонах показывал разное. Он сверяется с этими часами. Путь вне
    # /api/: без входа и мимо service worker — копия времени не нужна.
    return web.json_response({"now": time.time()}, headers={"Cache-Control": "no-store"})


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
