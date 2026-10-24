"""Вход на сайт без Telegram: почта и пароль, и вход в «Сетевой город» с сайта.

После входа по почте выдаётся тот же постоянный токен мини-приложения, что
и по ссылке из бота, — дальше приложение работает как обычно (PIN, push,
офлайн). Вход в «Сетевой город» идёт по шагам: страница запускает попытку и
опрашивает её состояние (QR-код, запрос кода Госуслуг, итог).
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import web

from ..accounts import has_telegram
from ..accounts.service import connect_accounts, link_account
from ..accounts.mailer import letter
from ..accounts.site_login import PREFIX as SITE_LOGIN_PREFIX, describe_browser
from ..accounts.passwords import check_password, hash_password, verify_password
from ..domain.models import Credentials, LoginType, School
from ..netschool import login as login_flow, ownership
from ..netschool.errors import NetSchoolError, Reason, login_message, wrap
from . import viewing
from .api import BOT, context, current_user, json_error
from .oauth_login import YANDEX, OAuthLogins
from .telegram_auth import InitDataError, verify_login_widget
from .telegram_oidc import OidcLogins

if TYPE_CHECKING:
    from ..context import AppContext

routes = web.RouteTableDef()
logger = logging.getLogger("netschoolbot.web")

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")
COOKIE = "netschool_token"
OTP_TIMEOUT_S = 300
MAX_SCHOOLS = 30

# Без входа доступны только эти адреса.
PUBLIC_PATHS = ("/api/account/register", "/api/account/login", "/api/account/reset/request",
                "/api/account/reset/confirm", "/api/account/reset/check", "/api/account/config", "/api/family/invite-info",
                "/api/account/tg-login", "/api/account/tg-widget",
                "/api/account/tg-oidc/start", "/api/account/tg-oidc/callback",
                "/api/account/yandex/start", "/api/account/yandex/callback")


def is_public(path: str) -> bool:
    return path.rstrip("/").endswith(PUBLIC_PATHS)


# ─────────────────────────── Ограничение попыток ───────────────────────────


class Limiter:
    """Не больше `limit` событий за `window` секунд на ключ (в памяти)."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = {}

    def blocked(self, key: str) -> bool:
        hits = self._hits.get(key)
        now = time.monotonic()
        while hits and now - hits[0] > self.window:
            hits.popleft()
        return bool(hits) and len(hits) >= self.limit

    def hit(self, key: str) -> None:
        self._hits.setdefault(key, deque()).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


LOGIN_FAILURES = Limiter(8, 15 * 60)  # на почту
IP_FAILURES = Limiter(30, 15 * 60)  # на адрес
REGISTRATIONS = Limiter(5, 3600)  # на адрес
CODE_REQUESTS = Limiter(3, 15 * 60)  # писем с кодом на почту
SITE_LOGINS = Limiter(10, 15 * 60)  # входов через бота на адрес


def _ip(request: web.Request) -> str:
    return request.headers.get("X-Real-IP") or request.remote or "?"


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def _signed_in(request: web.Request, telegram_id: int, method: str) -> web.Response:
    """Ответ с постоянным токеном в cookie — страница перезагрузится уже с ним."""
    # Свой токен на каждое устройство: «Выйти» отзывает только его. Прежний
    # токен этого браузера новый перезапишет в cookie — он больше ничей и
    # только висел бы лишним Safari в «Устройствах».
    await context(request).miniapp.revoke_device_token(
        (request.headers.get("X-Netschool-Token") or request.cookies.get(COOKIE) or "").strip())
    token = await context(request).miniapp.issue_token(telegram_id, device=True, method=method)
    # Новый сеанс — в «Устройствах» на остальных; после сброса пароля
    # отозванные устройства выходят сразу.
    from .sync import publish
    await publish(context(request), telegram_id, "devices")
    response = web.json_response({"ok": True, "data": {"token": token}})
    response.set_cookie(COOKIE, token, max_age=365 * 24 * 3600, httponly=True, samesite="Lax",
                        secure=request.url.scheme == "https")
    return response


# ─────────────────────────────── Без входа ───────────────────────────────


_bot_username = ""


async def bot_username(request: web.Request) -> str:
    """Имя бота для ссылок t.me — спрашиваем Telegram один раз."""
    global _bot_username
    bot = request.config_dict.get(BOT)
    if not _bot_username and bot is not None:
        try:
            _bot_username = (await bot.get_me()).username or ""
        except Exception:  # noqa: BLE001 — без имени просто не будет кнопки
            return ""
    return _bot_username


@routes.get("/api/account/config")
async def account_config(request: web.Request) -> web.Response:
    """Что нужно главному экрану без входа: работает ли почта и куда звать в Telegram."""
    username = await bot_username(request)
    return web.json_response({"ok": True, "data": {
        "mail": context(request).mailer.configured,
        "bot": f"https://t.me/{username}" if username else "",
        "widget": await login_widget_bot_id(request),
        "oidc": _oidc_redirect_uri(request) is not None,
        "yandex": _yandex_redirect_uri(request) is not None,
    }})


# Переключатели админа (/tgwidget в боте), хранятся в app_flags.
WIDGET_OFF_FLAG = "login_widget_off"
WIDGET_MAIN_FLAG = "login_widget_main_bot"


async def widget_bot_token(app: AppContext) -> str:
    """Токен бота, к которому в BotFather привязан домен виджета; пусто — выключен.

    Обычно это резервный бот из NETSCHOOL_TG_LOGIN_BOT_TOKEN: у бота домен
    один. Командой /tgwidget main виджет переводится на основного бота.
    """
    from ..db.repositories import AppFlagRepository
    flags = AppFlagRepository(app.db)
    if await flags.get(WIDGET_OFF_FLAG):
        return ""
    settings = app.settings
    if await flags.get(WIDGET_MAIN_FLAG):
        return settings.telegram.bot_token
    return settings.web.login_widget_bot_token or settings.telegram.bot_token


async def login_widget_bot_id(request: web.Request) -> int:
    """id бота для Telegram Login Widget — только на домене из BotFather.

    На любом другом домене (зеркала, локальная разработка) Telegram ответит
    «Bot domain invalid», поэтому кнопку там не показываем вовсе.
    """
    domain = context(request).settings.web.login_widget_domain
    if not domain or request.host.split(":")[0].lower() != domain:
        return 0
    bot_id, _, _ = (await widget_bot_token(context(request))).partition(":")
    return int(bot_id) if bot_id.isdigit() else 0


# ─────────────────── Вход через Telegram (OpenID Connect) ───────────────────

OIDC = OidcLogins()
OIDC_COOKIE = "tg_oidc_state"


def _oidc_redirect_uri(request: web.Request) -> str | None:
    """Адрес возврата для этого домена — если вход по OIDC здесь включён."""
    web_settings = context(request).settings.web
    host = request.host.lower()
    if not web_settings.tg_oidc_enabled or host.split(":")[0] not in web_settings.tg_oidc_hosts:
        return None
    # За nginx запрос приходит по http, а Telegram знает https-адрес.
    scheme = "http" if host.split(":")[0] in ("127.0.0.1", "localhost") else "https"
    base = web_settings.miniapp_path.rstrip("/")
    return f"{scheme}://{host}{base}/api/account/tg-oidc/callback"


def _oidc_back(request: web.Request, error: str = "") -> web.HTTPFound:
    """Назад на сайт: после входа страница откроется уже с новым токеном в cookie."""
    base = context(request).settings.web.miniapp_path.rstrip("/")
    response = web.HTTPFound(f"{base}/?refresh=1" if not error else f"{base}/#tgLoginError={error}")
    response.del_cookie(OIDC_COOKIE, path=f"{base}/api/account/tg-oidc/")
    return response


@routes.get("/api/account/tg-oidc/start")
async def tg_oidc_start(request: web.Request) -> web.Response:
    """Кнопка «Войти через Telegram»: на страницу входа Telegram с PKCE."""
    redirect_uri = _oidc_redirect_uri(request)
    if redirect_uri is None:
        raise _oidc_back(request, "off")
    if SITE_LOGINS.blocked(_ip(request)):
        raise _oidc_back(request, "limit")
    SITE_LOGINS.hit(_ip(request))
    state, url = OIDC.start(context(request).settings.web.tg_oidc_client_id, redirect_uri)
    response = web.HTTPFound(url)
    # state привязан к этому браузеру: чужую ссылку возврата сюда не подсунуть.
    base = context(request).settings.web.miniapp_path.rstrip("/")
    response.set_cookie(OIDC_COOKIE, state, max_age=600, httponly=True, samesite="Lax",
                        secure=redirect_uri.startswith("https:"), path=f"{base}/api/account/tg-oidc/")
    raise response


@routes.get("/api/account/tg-oidc/link")
async def tg_oidc_link(request: web.Request) -> web.Response:
    """«Привязать Telegram» в настройках входа: тот же вход через Telegram,
    но в конце Telegram привязывается к открытому аккаунту с сайта."""
    app, user = context(request), current_user(request)
    redirect_uri = _oidc_redirect_uri(request)
    if has_telegram(user.telegram_id):
        raise _link_back(request, "ok")
    if redirect_uri is None:
        raise _link_back(request, "off")
    if SITE_LOGINS.blocked(_ip(request)):
        raise _link_back(request, "limit")
    SITE_LOGINS.hit(_ip(request))
    state, url = OIDC.start(app.settings.web.tg_oidc_client_id, redirect_uri, link_to=user.telegram_id)
    response = web.HTTPFound(url)
    base = app.settings.web.miniapp_path.rstrip("/")
    response.set_cookie(OIDC_COOKIE, state, max_age=600, httponly=True, samesite="Lax",
                        secure=redirect_uri.startswith("https:"), path=f"{base}/api/account/tg-oidc/")
    raise response


@routes.get("/api/account/tg-oidc/callback")
async def tg_oidc_callback(request: web.Request) -> web.Response:
    """Telegram вернул код: меняем его на id_token, проверяем и входим
    (или привязываем Telegram, если так начинали)."""
    state = request.query.get("state", "")
    item = OIDC.take(state) if state and request.cookies.get(OIDC_COOKIE) == state else None
    back = _link_back if item is not None and item.link_to is not None else _oidc_back
    if request.query.get("error") or not request.query.get("code"):
        OIDC.take(state)
        raise back(request, "cancel")
    if item is None:
        raise _oidc_back(request, "expired")
    web_settings = context(request).settings.web
    try:
        tg_user = await OIDC.finish(item, request.query["code"], web_settings.tg_oidc_client_id,
                                    web_settings.tg_oidc_client_secret)
    except (InitDataError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Вход через Telegram (OIDC) не удался: %s", exc)
        raise back(request, "failed")
    if item.link_to is not None:
        if problem := await link_account(context(request), item.link_to, tg_user.id):
            _remember_link_problem(item.link_to, problem)
            raise _link_back(request, "problem")
        raise _link_back(request, "ok")
    await context(request).users.get_or_create(tg_user.id, display_name=tg_user.full_name)
    signed = await _signed_in(request, tg_user.id, "telegram")
    response = _oidc_back(request)
    for name, morsel in signed.cookies.items():
        response.cookies[name] = morsel
    logger.info("Вход на сайт через Telegram (OIDC): %s", tg_user.id)
    raise response


def _link_back(request: web.Request, result: str) -> web.HTTPFound:
    """Назад в настройки после привязки. Токен в cookie переехал вместе с
    аккаунтом, поэтому заново входить не нужно."""
    base = context(request).settings.web.miniapp_path.rstrip("/")
    response = web.HTTPFound(f"{base}/?refresh=1#tglink={result}")
    response.del_cookie(OIDC_COOKIE, path=f"{base}/api/account/tg-oidc/")
    return response


# ─────────────────────────── Вход через Яндекс ID ───────────────────────────

YANDEX_LOGINS = OAuthLogins(YANDEX)
YANDEX_COOKIE = "yandex_state"
YANDEX_PREFIX = "/api/account/yandex/"


def _yandex_redirect_uri(request: web.Request) -> str | None:
    """Адрес возврата для этого домена — если вход через Яндекс здесь включён."""
    web_settings = context(request).settings.web
    host = request.host.lower()
    if not web_settings.yandex_enabled or host.split(":")[0] not in web_settings.yandex_hosts:
        return None
    # За nginx запрос приходит по http, а в кабинете Яндекса — https-адрес.
    scheme = "http" if host.split(":")[0] in ("127.0.0.1", "localhost") else "https"
    return f"{scheme}://{host}{web_settings.miniapp_path.rstrip('/')}{YANDEX_PREFIX}callback"


def _yandex_redirect(request: web.Request, redirect_uri: str, *, link_to: int | None = None) -> web.HTTPFound:
    state, url = YANDEX_LOGINS.start(context(request).settings.web.yandex_client_id, redirect_uri, link_to=link_to)
    response = web.HTTPFound(url)
    # state привязан к этому браузеру: чужую ссылку возврата сюда не подсунуть.
    base = context(request).settings.web.miniapp_path.rstrip("/")
    response.set_cookie(YANDEX_COOKIE, state, max_age=600, httponly=True, samesite="Lax",
                        secure=redirect_uri.startswith("https:"), path=f"{base}{YANDEX_PREFIX}")
    return response


def _yandex_back(request: web.Request, error: str = "", *, link: bool = False) -> web.HTTPFound:
    """Назад на сайт: после входа — с новым токеном в cookie, после привязки — в настройки."""
    base = context(request).settings.web.miniapp_path.rstrip("/")
    if link:
        url = f"{base}/#yalink={error or 'ok'}"
    else:
        url = f"{base}/#yaLoginError={error}" if error else f"{base}/?refresh=1"
    response = web.HTTPFound(url)
    response.del_cookie(YANDEX_COOKIE, path=f"{base}{YANDEX_PREFIX}")
    return response


@routes.get("/api/account/yandex/start")
async def yandex_start(request: web.Request) -> web.Response:
    """Кнопка «Войти через Яндекс ID»: на страницу входа Яндекса с PKCE."""
    redirect_uri = _yandex_redirect_uri(request)
    if redirect_uri is None:
        raise _yandex_back(request, "off")
    if SITE_LOGINS.blocked(_ip(request)):
        raise _yandex_back(request, "limit")
    SITE_LOGINS.hit(_ip(request))
    raise _yandex_redirect(request, redirect_uri)


@routes.get("/api/account/yandex/link")
async def yandex_link(request: web.Request) -> web.Response:
    """«Привязать Яндекс ID» в настройках входа: тот же вход, но в конце
    Яндекс ID привязывается к открытому аккаунту."""
    user = current_user(request)
    redirect_uri = _yandex_redirect_uri(request)
    if redirect_uri is None:
        raise _yandex_back(request, "off", link=True)
    if SITE_LOGINS.blocked(_ip(request)):
        raise _yandex_back(request, "limit", link=True)
    SITE_LOGINS.hit(_ip(request))
    raise _yandex_redirect(request, redirect_uri, link_to=user.telegram_id)


@routes.get("/api/account/yandex/callback")
async def yandex_callback(request: web.Request) -> web.Response:
    """Яндекс вернул код: узнаём, кто вошёл, и входим (или привязываем)."""
    app = context(request)
    state = request.query.get("state", "")
    item = YANDEX_LOGINS.take(state) if state and request.cookies.get(YANDEX_COOKIE) == state else None
    link = item is not None and item.link_to is not None
    if request.query.get("error") or not request.query.get("code"):
        YANDEX_LOGINS.take(state)
        raise _yandex_back(request, "cancel", link=link)
    if item is None:
        raise _yandex_back(request, "expired")
    web_settings = app.settings.web
    try:
        identity = await YANDEX_LOGINS.finish(item, request.query["code"], web_settings.yandex_client_id,
                                              web_settings.yandex_client_secret)
    except (InitDataError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Вход через Яндекс ID не удался: %s", exc)
        raise _yandex_back(request, "failed", link=link)
    if item.link_to is not None:
        if await app.users.get(item.link_to) is None:
            raise _yandex_back(request, "failed", link=True)
        if not await app.accounts.link_oauth(item.link_to, YANDEX.name, identity.subject):
            raise _yandex_back(request, "taken", link=True)
        await app.accounts.update_oauth_email(item.link_to, YANDEX.name, identity.subject, identity.email)
        logger.info("К аккаунту %s привязан Яндекс ID", item.link_to)
        raise _yandex_back(request, link=True)
    telegram_id = await app.accounts.oauth_user(YANDEX.name, identity.subject)
    if telegram_id is None:
        telegram_id = await app.accounts.create_oauth_user(YANDEX.name, identity.subject, identity.name)
        logger.info("Новый аккаунт через Яндекс ID: %s", telegram_id)
    await app.accounts.update_oauth_email(telegram_id, YANDEX.name, identity.subject, identity.email)
    signed = await _signed_in(request, telegram_id, "yandex")
    response = _yandex_back(request)
    for name, morsel in signed.cookies.items():
        response.cookies[name] = morsel
    logger.info("Вход на сайт через Яндекс ID: %s", telegram_id)
    raise response


@routes.post("/api/account/yandex/unlink")
async def yandex_unlink(request: web.Request) -> web.Response:
    """Отвязать Яндекс ID — только если останется другой способ входа."""
    app, user = context(request), current_user(request)
    if not has_telegram(user.telegram_id) and not await app.accounts.by_user(user.telegram_id):
        return json_error("Это единственный способ входа. Сначала задайте почту и пароль или привяжите Telegram")
    await app.accounts.unlink_oauth(user.telegram_id, YANDEX.name)
    return await account_info(request)


# Почему привязка не вышла: текст из connect_accounts ждёт, пока страница
# его спросит, — в адрес кладём только код, чтобы чужая ссылка не могла
# показать человеку произвольный текст.
LINK_PROBLEMS: dict[int, str] = {}
MAX_LINK_PROBLEMS = 1000


def _remember_link_problem(telegram_id: int, problem: str) -> None:
    LINK_PROBLEMS.pop(telegram_id, None)
    if len(LINK_PROBLEMS) >= MAX_LINK_PROBLEMS:
        LINK_PROBLEMS.pop(next(iter(LINK_PROBLEMS)))
    LINK_PROBLEMS[telegram_id] = problem


async def _telegram_link_methods(request: web.Request) -> dict | None:
    """Чем привязать Telegram на этом домене — тем же, чем на нём входят."""
    oidc, widget = _oidc_redirect_uri(request) is not None, await login_widget_bot_id(request)
    return {"oidc": oidc, "widget": widget} if oidc or widget else None


# Подписанный ответ виджета остаётся в истории браузера (в адресе после #),
# поэтому годен он недолго: Telegram ставит время в момент возврата на сайт.
WIDGET_MAX_AGE_S = 600


@routes.post("/api/account/tg-widget")
async def tg_widget_login(request: web.Request) -> web.Response:
    """Вход через Telegram Login Widget: Telegram вернул на сайт подписанные данные."""
    ip = _ip(request)
    if IP_FAILURES.blocked(ip):
        return json_error("Слишком много попыток входа — подождите 15 минут", status=429)
    try:
        tg_user = verify_login_widget(await _body(request), await widget_bot_token(context(request)),
                                      max_age=WIDGET_MAX_AGE_S)
    except InitDataError as exc:
        IP_FAILURES.hit(ip)
        logger.info("Вход через виджет отклонён: %s", exc)
        return json_error("Telegram не подтвердил вход — попробуйте ещё раз", status=401, reason="auth")
    await context(request).users.get_or_create(tg_user.id, display_name=tg_user.full_name)
    logger.info("Вход на сайт через виджет Telegram: %s", tg_user.id)
    return await _signed_in(request, tg_user.id, "telegram-widget")


@routes.post("/api/account/tg-login")
async def tg_login_start(request: web.Request) -> web.Response:
    """Начать вход через бота: ссылка t.me с одноразовым кодом."""
    ip = _ip(request)
    username = await bot_username(request)
    if not username:
        return json_error("Бот сейчас недоступен — войдите по почте", status=503)
    if SITE_LOGINS.blocked(ip):
        return json_error("Слишком много попыток входа — подождите 15 минут", status=429)
    code = context(request).site_logins.start(ip, describe_browser(request.headers.get("User-Agent", "")))
    if code is None:
        return json_error("Сервер занят — попробуйте через минуту", status=503)
    SITE_LOGINS.hit(ip)
    return web.json_response({"ok": True, "data": {
        "code": code, "url": f"https://t.me/{username}?start={SITE_LOGIN_PREFIX}{code}"}})


@routes.get("/api/account/tg-login")
async def tg_login_status(request: web.Request) -> web.Response:
    """Ждём подтверждения в боте: pending → ok (с токеном в cookie), declined или expired."""
    logins = context(request).site_logins
    code = request.query.get("code", "")
    item = logins.get(code)
    if item is None:
        return web.json_response({"ok": True, "data": {"status": "expired"}})
    if item.declined:
        return web.json_response({"ok": True, "data": {"status": "declined"}})
    if item.telegram_id is None:
        return web.json_response({"ok": True, "data": {"status": "pending"}})
    item = logins.take(code)
    if item is None:
        return web.json_response({"ok": True, "data": {"status": "expired"}})
    logger.info("Вход на сайт через бота: %s", item.telegram_id)
    return await _signed_in(request, item.telegram_id, "bot")


@routes.post("/api/account/register")
async def register(request: web.Request) -> web.Response:
    ip = _ip(request)
    if REGISTRATIONS.blocked(ip):
        return json_error("Слишком много регистраций с этого адреса — попробуйте позже", status=429)
    body = await _body(request)
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if not EMAIL.match(email):
        return json_error("Проверьте адрес почты")
    if problem := check_password(password):
        return json_error(problem)
    app = context(request)
    telegram_id = await app.accounts.create_web_user(email, hash_password(password))
    if telegram_id is None:
        return json_error("Эта почта уже зарегистрирована — войдите или восстановите пароль")
    REGISTRATIONS.hit(ip)
    await _send_verification(request, email)
    logger.info("Новый аккаунт без Telegram: %s", telegram_id)
    return await _signed_in(request, telegram_id, "register")


@routes.post("/api/account/login")
async def login(request: web.Request) -> web.Response:
    ip = _ip(request)
    body = await _body(request)
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if LOGIN_FAILURES.blocked(email) or IP_FAILURES.blocked(ip):
        return json_error("Слишком много неудачных попыток — подождите 15 минут", status=429)
    account = await context(request).accounts.by_email(email) if email else None
    if not account or not verify_password(password, account["password_hash"]):
        LOGIN_FAILURES.hit(email)
        IP_FAILURES.hit(ip)
        return json_error("Неверная почта или пароль", status=401, reason="account")
    LOGIN_FAILURES.reset(email)
    return await _signed_in(request, account["telegram_id"], "email")


@routes.post("/api/account/reset/request")
async def reset_request(request: web.Request) -> web.Response:
    """Ссылка для сброса пароля — письмом, а если почта не настроена, в Telegram.
    Ответ одинаковый, есть ли такая почта: по нему не узнать, кто зарегистрирован."""
    email = str((await _body(request)).get("email") or "").strip().lower()
    app = context(request)
    account = await app.accounts.by_email(email) if EMAIL.match(email) else None
    if account and not CODE_REQUESTS.blocked(email):
        CODE_REQUESTS.hit(email)
        token = await app.accounts.issue_reset_link(email)
        # Секрет — во фрагменте после #: он не уходит на сервер в адресе
        # запроса и не оседает в журналах прокси.
        # Просили с зеркала — ссылка туда же: основной домен мог не открыться.
        site = app.settings.web
        mirror = site.mirror_url and request.headers.get("Origin") == site.mirror_url.rstrip("/")
        link = f"{(site.mirror_miniapp_url if mirror else site.miniapp_url).rstrip('/')}/#reset={token}"
        note = "Ссылка действует 30 минут и сработает один раз. Если вы не запрашивали сброс — ничего не делайте."
        if app.mailer.configured:
            await _mail(request, email, "Сброс пароля — Дневник", *letter(
                "Сброс пароля", "Чтобы задать новый пароль, откройте ссылку:", link=link,
                button="Задать новый пароль", note=note, site=app.mailer.site))
        elif has_telegram(account["telegram_id"]) and (bot := request.config_dict.get(BOT)):
            try:
                await bot.send_message(account["telegram_id"], f"🔑 Сброс пароля: {link}\n{note}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ссылка сброса в Telegram не ушла: %s", exc)
    channel = "mail" if app.mailer.configured else "telegram"
    return web.json_response({"ok": True, "data": {"channel": channel}})


@routes.get("/api/account/reset/check")
async def reset_check(request: web.Request) -> web.Response:
    """Жива ли ссылка: страница сразу скажет, что она устарела, а не после ввода пароля."""
    email = await context(request).accounts.reset_link_email(request.query.get("token", ""))
    if not email:
        return json_error("Ссылка устарела или уже использована — запросите новую", reason="reset_link")
    return web.json_response({"ok": True, "data": {"email": email}})


@routes.post("/api/account/reset/confirm")
async def reset_confirm(request: web.Request) -> web.Response:
    body = await _body(request)
    token = str(body.get("token") or "")
    password = str(body.get("password") or "")
    if problem := check_password(password):
        return json_error(problem)
    app = context(request)
    email = await app.accounts.reset_link_email(token, take=True)
    account = await app.accounts.by_email(email) if email else None
    if not account:
        return json_error("Ссылка устарела или уже использована — запросите новую", reason="reset_link")
    await app.accounts.set_password(account["telegram_id"], hash_password(password))
    # Ссылка пришла на почту — значит, почта настоящая.
    await app.accounts.mark_verified(account["telegram_id"])
    # Пароль сбрасывают, когда к аккаунту могли получить доступ: старые
    # ссылки и входы на других устройствах перестают работать.
    await app.miniapp.revoke_tokens(account["telegram_id"])
    LOGIN_FAILURES.reset(email)
    return await _signed_in(request, account["telegram_id"], "reset")


async def _mail(request: web.Request, email: str, subject: str, text: str, html_body: str | None = None) -> bool:
    try:
        await context(request).mailer.send(email, subject, text, html_body=html_body)
        return True
    except Exception as exc:  # noqa: BLE001 — недоставленное письмо не ломает вход
        logger.warning("Письмо не ушло: %s", exc)
        return False


async def _send_verification(request: web.Request, email: str) -> None:
    app = context(request)
    if not app.mailer.configured or CODE_REQUESTS.blocked(email):
        return
    CODE_REQUESTS.hit(email)
    code = await app.accounts.issue_code(email, "verify")
    await _mail(request, email, "Подтверждение почты — Дневник", *letter(
        "Подтверждение почты", "Код подтверждения:", code=code,
        note="Введите его в приложении: Настройки → Вход по почте. Код действует 15 минут.", site=app.mailer.site))


async def _needs_verify(app: AppContext, telegram_id: int, account=None) -> bool:
    """Аккаунт только с почтой, и она не подтверждена: дальше кода из письма не пускаем.

    Без настроенной почты подтвердить нечем — тогда не требуем.
    """
    if not app.mailer.configured or has_telegram(telegram_id):
        return False
    account = account or await app.accounts.by_user(telegram_id)
    return bool(account and not account["email_verified_at"])


# ─────────────────────────────── После входа ───────────────────────────────


@routes.get("/api/account")
async def account_info(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    account = await app.accounts.by_user(user.telegram_id)
    attempt = app.web_logins.get(user.telegram_id)
    owner = await viewing.target(app, user)
    return web.json_response({"ok": True, "data": {
        # Открыт дневник ученика, пригласившего на просмотр: только чтение.
        "viewing": {"id": owner.telegram_id, "name": owner.label} if owner else None,
        "email": account["email"] if account else None,
        "suggested_email": await app.accounts.oauth_email(user.telegram_id) if not account else None,
        "number": await app.accounts.number(user.telegram_id),
        "verified": bool(account and account["email_verified_at"]),
        "needs_verify": await _needs_verify(app, user.telegram_id, account),
        "telegram": has_telegram(user.telegram_id),
        # Только «не вошёл» (нет школы). enabled=False — это и выключенные
        # уведомления в боте: таким дневник открывать как обычно.
        # Родному с просмотром свой дневник не нужен — он смотрит дневник ученика.
        "needs_sgo": not user.school.configured and owner is None,
        "school": user.school.name,
        "mail": app.mailer.configured,
        "login": attempt.status_json() if isinstance(attempt, WebLogin) else None,
        # Только у админа: пускать ли другие аккаунты в его Сетевой город.
        "share_sgo": await ownership.sharing_allowed(app) if _is_admin(app, user.telegram_id) else None,
        "tg_link": await _telegram_link_methods(request) if not has_telegram(user.telegram_id) else None,
        # Вход через Яندекс ID: можно ли привязать на этом домене и привязан ли.
        "yandex": {"enabled": _yandex_redirect_uri(request) is not None,
                   "linked": YANDEX.name in await app.accounts.oauth_providers(user.telegram_id)},
    }})


def _is_admin(app: AppContext, telegram_id: int) -> bool:
    return bool(app.settings.telegram.admin_id) and telegram_id == app.settings.telegram.admin_id


@routes.post("/api/account/share-sgo")
async def share_sgo(request: web.Request) -> web.Response:
    """Тумблер админа: другие аккаунты могут подключать его Сетевой город."""
    app, user = context(request), current_user(request)
    if not _is_admin(app, user.telegram_id):
        return json_error("Недоступно", status=403)
    on = bool((await _body(request)).get("on"))
    await ownership.set_sharing(app, on)
    logger.info("Админ %s другим аккаунтам свой Сетевой город", "разрешил" if on else "запретил")
    return await account_info(request)


@routes.post("/api/account/attach")
async def attach(request: web.Request) -> web.Response:
    """Почта и пароль для входа на сайт без Telegram — тем, кто пришёл из бота.
    Сменить почту можно, только подтвердив текущий пароль."""
    app, user = context(request), current_user(request)
    body = await _body(request)
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if not EMAIL.match(email):
        return json_error("Проверьте адрес почты")
    if problem := check_password(password):
        return json_error(problem)
    account = await app.accounts.by_user(user.telegram_id)
    if account and not verify_password(str(body.get("current") or ""), account["password_hash"]):
        return json_error("Неверный текущий пароль", status=403, reason="account")
    owner = await app.accounts.by_email(email)
    if owner and owner['telegram_id'] != user.telegram_id:
        key = f"attach:{user.telegram_id}"
        if LOGIN_FAILURES.blocked(key):
            return json_error("Слишком много попыток — подождите 15 минут", status=429)
        if not has_telegram(user.telegram_id) or has_telegram(owner['telegram_id']):
            return json_error("Эта почта уже используется другим аккаунтом")
        if not verify_password(password, owner['password_hash']):
            LOGIN_FAILURES.hit(key)
            return json_error("Почта уже зарегистрирована. Введите её текущий пароль для привязки", status=403)
        if problem := await connect_accounts(app, owner['telegram_id'], user.telegram_id):
            return json_error(problem)
        LOGIN_FAILURES.reset(key)
        return await account_info(request)
    if not await app.accounts.attach(user.telegram_id, email, hash_password(password)):
        return json_error("Эта почта уже используется другим аккаунтом")
    if not account or account["email"] != email:
        await _send_verification(request, email)
    return await account_info(request)


@routes.post("/api/account/password")
async def change_password(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    body = await _body(request)
    account = await app.accounts.by_user(user.telegram_id)
    if not account:
        return json_error("Сначала задайте почту и пароль")
    if not verify_password(str(body.get("current") or ""), account["password_hash"]):
        return json_error("Неверный текущий пароль", status=403, reason="account")
    password = str(body.get("password") or "")
    if problem := check_password(password):
        return json_error(problem)
    await app.accounts.set_password(user.telegram_id, hash_password(password))
    return web.json_response({"ok": True})


@routes.post("/api/account/verify/send")
async def verify_send(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    account = await app.accounts.by_user(user.telegram_id)
    if not account or not app.mailer.configured:
        return json_error("Отправка писем пока не настроена")
    if CODE_REQUESTS.blocked(account["email"]):
        return json_error("Письмо уже отправлено — проверьте почту или подождите 15 минут", status=429)
    await _send_verification(request, account["email"])
    return web.json_response({"ok": True})


@routes.post("/api/account/verify")
async def verify(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    account = await app.accounts.by_user(user.telegram_id)
    code = str((await _body(request)).get("code") or "")
    if not account or not await app.accounts.check_code(account["email"], "verify", code):
        return json_error("Неверный или устаревший код")
    await app.accounts.mark_verified(user.telegram_id)
    return await account_info(request)


@routes.post("/api/account/telegram-link")
async def telegram_link(request: web.Request) -> web.Response:
    """Код для привязки Telegram: его отправляют боту командой /link."""
    app, user = context(request), current_user(request)
    if has_telegram(user.telegram_id):
        return json_error("Telegram уже привязан")
    code = await app.accounts.issue_link_code(user.telegram_id)
    username = await bot_username(request)
    return web.json_response({"ok": True, "data": {
        "code": code, "url": f"https://t.me/{username}?start=link_{code}" if username else ""}})


@routes.post("/api/account/telegram-link/widget")
async def telegram_link_widget(request: web.Request) -> web.Response:
    """Привязка через Telegram Login Widget: страница вернулась с подписанными
    данными Telegram — привязываем этот Telegram к открытому аккаунту."""
    app, user = context(request), current_user(request)
    if has_telegram(user.telegram_id):
        return json_error("Telegram уже привязан")
    ip = _ip(request)
    if IP_FAILURES.blocked(ip):
        return json_error("Слишком много попыток — подождите 15 минут", status=429)
    try:
        tg_user = verify_login_widget(await _body(request), await widget_bot_token(app), max_age=WIDGET_MAX_AGE_S)
    except InitDataError as exc:
        IP_FAILURES.hit(ip)
        logger.info("Привязка через виджет отклонена: %s", exc)
        return json_error("Telegram не подтвердил вход — попробуйте ещё раз", status=400)
    if problem := await link_account(app, user.telegram_id, tg_user.id):
        return json_error(problem)
    return web.json_response({"ok": True})


@routes.post("/api/account/telegram-link/result")
async def telegram_link_result(request: web.Request) -> web.Response:
    """Почему не вышла привязка через Telegram (один раз)."""
    problem = LINK_PROBLEMS.pop(current_user(request).telegram_id, "")
    return web.json_response({"ok": True, "data": {"problem": problem}})


@routes.post("/api/lock/password-reset")
async def lock_password_reset(request: web.Request) -> web.Response:
    """Забыли PIN, а Telegram нет: сбросить защиту входа паролем от аккаунта
    (как /resetpin в боте подтверждает владельца через Telegram)."""
    app, user = context(request), current_user(request)
    key = f"lock:{user.telegram_id}"
    if LOGIN_FAILURES.blocked(key):
        return json_error("Слишком много попыток — подождите 15 минут", status=429)
    account = await app.accounts.by_user(user.telegram_id)
    password = str((await _body(request)).get("password") or "")
    if not account or not verify_password(password, account["password_hash"]):
        LOGIN_FAILURES.hit(key)
        return json_error("Неверный пароль", status=403, reason="account")
    async with app.db.transaction() as db:
        for table in ("app_locks", "app_unlocks", "app_passkeys", "app_biometrics", "app_lock_challenges"):
            await db.execute(f"DELETE FROM {table} WHERE telegram_id = ?", (user.telegram_id,))
    LOGIN_FAILURES.reset(key)
    return web.json_response({"ok": True})


@routes.post("/api/account/logout")
async def logout(request: web.Request) -> web.Response:
    """Выйти на этом устройстве. Токен входа с этого устройства отзывается;
    общий токен (ссылки из бота, подписка на календарь) остаётся — его
    страница просто забывает (см. boot.js, ns-signed-out)."""
    token = (request.headers.get("X-Netschool-Token") or request.cookies.get(COOKIE) or "").strip()
    revoked = await context(request).miniapp.revoke_device_token(token)
    response = web.json_response({"ok": True, "data": {"revoked": revoked}})
    response.del_cookie(COOKIE)
    return response


@routes.post("/api/account/delete")
async def delete_account(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    account = await app.accounts.by_user(user.telegram_id)
    key = f"delete:{user.telegram_id}"
    if LOGIN_FAILURES.blocked(key):
        return json_error("Слишком много попыток — подождите 15 минут", status=429)
    body = await _body(request)
    # Без почты и Telegram (вошёл через Яндекс ID) пароля нет — хватает входа.
    oauth_only = not account and not has_telegram(user.telegram_id)
    if not oauth_only and (
            not account or not verify_password(str(body.get("password") or ""), account["password_hash"])):
        LOGIN_FAILURES.hit(key)
        return json_error("Неверный пароль аккаунта", status=403, reason="account")
    await app.delete_user_data(user.telegram_id)
    LOGIN_FAILURES.reset(key)
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE)
    return response


@routes.post("/api/account/netschool-logout")
async def netschool_logout(request: web.Request) -> web.Response:
    """Выйти из «Сетевого города», оставшись в приложении: то же, что /logout
    в боте. Дальше приложение предлагает войти в школу заново."""
    await context(request).sign_out_netschool(current_user(request))
    return web.json_response({"ok": True})


# ─────────────────────── Вход в «Сетевой город» с сайта ───────────────────────


@dataclass
class WebLogin:
    """Одна попытка входа: её состояние страница опрашивает."""

    id: str
    method: str
    status: str = "running"  # running | qr | otp | done | error
    message: str = ""
    qr: str = ""
    qr_until: float = 0.0
    otp: dict[str, Any] = field(default_factory=dict)
    otp_future: asyncio.Future | None = None
    task: asyncio.Task | None = None
    retry: bool = False

    def status_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "method": self.method, "status": self.status, "message": self.message,
            "qr": self.qr if self.status == "qr" else "", "qr_left": max(0, int(self.qr_until - time.time())),
            "otp": self.otp if self.status == "otp" else None, "retry": self.retry,
        }


@routes.get("/api/sgo/regions")
async def sgo_regions(request: web.Request) -> web.Response:
    from netschoolpy import REGIONS

    return web.json_response({"ok": True, "data": {"items": sorted(REGIONS)}})


def _region_url(body_or_query: Any) -> str:
    from netschoolpy import REGIONS

    region = str(body_or_query.get("region") or "")
    url = str(body_or_query.get("url") or "").strip().rstrip("/")
    if region:
        return REGIONS.get(region, "")
    return url if url.startswith("https://") else ""


@routes.get("/api/sgo/schools")
async def sgo_schools(request: web.Request) -> web.Response:
    url = _region_url(request.query)
    query = request.query.get("q", "").strip()
    if not url:
        return json_error("Выберите регион или укажите адрес сервера (https://…)")
    if not query:
        return json_error("Напишите название или номер школы")
    app = context(request)
    try:
        from netschoolpy import search_schools

        schools = await search_schools(url, query, timeout=app.settings.netschool.http_timeout,
                                       proxy=app.settings.netschool.proxy_for(url))
    except Exception as exc:
        from ..netschool.patches import http as http_patch

        http_patch.reset_blocked_hosts()
        raise wrap(exc, context="поиск школ") from exc
    names = [school.short_name or school.name for school in schools[:MAX_SCHOOLS]]
    return web.json_response({"ok": True, "data": {"url": url, "items": names, "total": len(schools)}})


def _render_qr(content: str) -> str:
    try:
        import qrcode
    except ImportError:
        return ""
    buffer = io.BytesIO()
    qrcode.make(content).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


@routes.post("/api/sgo/login")
async def sgo_login(request: web.Request) -> web.Response:
    """Начать вход: пароль «Сетевого города», логин Госуслуг или QR Госуслуг."""
    app, user = context(request), current_user(request)
    body = await _body(request)
    url, school = _region_url(body), str(body.get("school") or "").strip()
    method = str(body.get("method") or "")
    if await _needs_verify(app, user.telegram_id):
        return json_error("Сначала подтвердите почту кодом из письма", status=403, reason="verify")
    if not url or not school:
        return json_error("Выберите регион и школу")
    if method not in {"password", "esia", "qr"}:
        return json_error("Неизвестный способ входа")
    current = app.web_logins.get(user.telegram_id)
    if isinstance(current, WebLogin) and current.task and not current.task.done():
        current.task.cancel()
    attempt = WebLogin(id=secrets.token_hex(6), method=method)
    app.web_logins[user.telegram_id] = attempt
    login_type = {"password": LoginType.PASSWORD, "esia": LoginType.ESIA, "qr": LoginType.ESIA_QR}[method]
    candidate = replace(
        user, school=School(url=url, name=school),
        credentials=Credentials(login_type=login_type, login=str(body.get("login") or "").strip(),
                                password=str(body.get("password") or ""),
                                remember=bool(body.get("remember")) and method != "qr"))
    if method != "qr" and (not candidate.credentials.login or not candidate.credentials.password):
        app.web_logins.pop(user.telegram_id, None)
        return json_error("Введите логин и пароль")
    attempt.task = asyncio.create_task(_run_login(app, candidate, attempt), name=f"web-login-{user.telegram_id}")
    return web.json_response({"ok": True, "data": attempt.status_json()})


async def _run_login(app: Any, candidate: Any, attempt: WebLogin) -> None:
    async def show_qr(content: str) -> None:
        attempt.qr = _render_qr(content) or content
        attempt.qr_until = time.time() + app.settings.netschool.qr_login_ttl
        attempt.status = "qr"

    async def ask_otp(mfa_type: str, info: dict) -> str:
        labels = {"SMS": "Код из SMS", "MAX": "Код из приложения «МАКС»", "TOTP": "Код из приложения-аутентификатора"}
        attempt.otp = {"label": labels.get(mfa_type, "Код подтверждения"), "phone": info.get("phone") or "",
                       "length": info.get("code_length", 6)}
        attempt.otp_future = asyncio.get_running_loop().create_future()
        attempt.status = "otp"
        try:
            return await asyncio.wait_for(asyncio.shield(attempt.otp_future), timeout=OTP_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            raise NetSchoolError(Reason.MFA, "Время ввода кода истекло — начните вход заново") from exc
        finally:
            attempt.status = "running"

    try:
        async with app.login_attempt(candidate.telegram_id):
            if attempt.method == "qr":
                payload = await login_flow.qr_login(app, candidate.school.url, candidate.school.name, show_qr,
                                                   telegram_id=candidate.telegram_id)
            else:
                payload = await login_flow.password_login(app, candidate, ask_otp)
            attempt.status = "running"
            saved, note = await login_flow.finish_login(app, candidate, payload)
        attempt.message = "Вход выполнен" + (f": {saved.student_name}" if saved.student_name else "")
        attempt.status = "done"
    except asyncio.CancelledError:
        attempt.status, attempt.message = "error", "Вход отменён"
        raise
    except asyncio.TimeoutError:
        attempt.status, attempt.message, attempt.retry = "error", "QR-код истёк — получите новый", True
    except NetSchoolError as exc:
        attempt.status, attempt.message = "error", login_message(exc)
        attempt.retry = attempt.method == "qr" and not isinstance(exc, ownership.AccountTaken)
    except Exception as exc:  # noqa: BLE001 — попытка должна закончиться понятной ошибкой
        logger.warning("Вход с сайта не удался: %s", type(exc).__name__)
        attempt.status, attempt.message = "error", login_message(wrap(exc, context="вход"))


@routes.get("/api/sgo/login")
async def sgo_login_status(request: web.Request) -> web.Response:
    attempt = context(request).web_logins.get(current_user(request).telegram_id)
    if not isinstance(attempt, WebLogin):
        return web.json_response({"ok": True, "data": None})
    return web.json_response({"ok": True, "data": attempt.status_json()})


@routes.post("/api/sgo/login/otp")
async def sgo_login_otp(request: web.Request) -> web.Response:
    attempt = context(request).web_logins.get(current_user(request).telegram_id)
    code = re.sub(r"\D", "", str((await _body(request)).get("code") or ""))
    if not isinstance(attempt, WebLogin) or attempt.status != "otp" or attempt.otp_future is None:
        return json_error("Код сейчас не нужен")
    if not 4 <= len(code) <= 8:
        return json_error("Введите код из цифр")
    if not attempt.otp_future.done():
        attempt.otp_future.set_result(code)
    return web.json_response({"ok": True, "data": attempt.status_json()})


@routes.post("/api/sgo/login/cancel")
async def sgo_login_cancel(request: web.Request) -> web.Response:
    attempt = context(request).web_logins.pop(current_user(request).telegram_id, None)
    if isinstance(attempt, WebLogin) and attempt.task and not attempt.task.done():
        attempt.task.cancel()
    return web.json_response({"ok": True})
