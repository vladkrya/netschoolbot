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
from typing import Any

from aiohttp import web

from ..accounts import has_telegram
from ..accounts.passwords import check_password, hash_password, verify_password
from ..domain.models import Credentials, LoginType, School
from ..netschool import login as login_flow
from ..netschool.errors import NetSchoolError, Reason, wrap
from . import viewing
from .api import BOT, context, current_user, json_error

routes = web.RouteTableDef()
logger = logging.getLogger("netschoolbot.web")

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")
COOKIE = "netschool_token"
OTP_TIMEOUT_S = 300
MAX_SCHOOLS = 30

# Без входа доступны только эти адреса.
PUBLIC_PATHS = ("/api/account/register", "/api/account/login", "/api/account/reset/request",
                "/api/account/reset/confirm", "/api/account/config", "/api/family/invite-info")


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


def _ip(request: web.Request) -> str:
    return request.headers.get("X-Real-IP") or request.remote or "?"


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def _signed_in(request: web.Request, telegram_id: int) -> web.Response:
    """Ответ с постоянным токеном в cookie — страница перезагрузится уже с ним."""
    token = await context(request).miniapp.issue_token(telegram_id)
    response = web.json_response({"ok": True, "data": {"token": token}})
    response.set_cookie(COOKIE, token, max_age=365 * 24 * 3600, httponly=True, samesite="Lax",
                        secure=request.url.scheme == "https")
    return response


# ─────────────────────────────── Без входа ───────────────────────────────


@routes.get("/api/account/config")
async def account_config(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "data": {"mail": context(request).mailer.configured}})


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
    return await _signed_in(request, telegram_id)


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
    return await _signed_in(request, account["telegram_id"])


@routes.post("/api/account/reset/request")
async def reset_request(request: web.Request) -> web.Response:
    """Код для сброса пароля — письмом, а если почта не настроена, в Telegram.
    Ответ одинаковый, есть ли такая почта: по нему не узнать, кто зарегистрирован."""
    email = str((await _body(request)).get("email") or "").strip().lower()
    app = context(request)
    account = await app.accounts.by_email(email) if EMAIL.match(email) else None
    if account and not CODE_REQUESTS.blocked(email):
        CODE_REQUESTS.hit(email)
        code = await app.accounts.issue_code(email, "reset")
        text = f"Код для сброса пароля: {code}\nОн действует 15 минут. Если вы не запрашивали сброс — ничего не делайте."
        if app.mailer.configured:
            await _mail(request, email, "Сброс пароля — Дневник", text)
        elif has_telegram(account["telegram_id"]) and (bot := request.config_dict.get(BOT)):
            try:
                await bot.send_message(account["telegram_id"], "🔑 " + text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Код сброса в Telegram не ушёл: %s", exc)
    channel = "mail" if app.mailer.configured else "telegram"
    return web.json_response({"ok": True, "data": {"channel": channel}})


@routes.post("/api/account/reset/confirm")
async def reset_confirm(request: web.Request) -> web.Response:
    body = await _body(request)
    email = str(body.get("email") or "").strip().lower()
    code = str(body.get("code") or "")
    password = str(body.get("password") or "")
    if problem := check_password(password):
        return json_error(problem)
    app = context(request)
    if not await app.accounts.check_code(email, "reset", code):
        return json_error("Неверный или устаревший код")
    account = await app.accounts.by_email(email)
    if not account:
        return json_error("Неверный или устаревший код")
    await app.accounts.set_password(account["telegram_id"], hash_password(password))
    # Код пришёл на почту — значит, почта настоящая.
    await app.accounts.mark_verified(account["telegram_id"])
    # Пароль сбрасывают, когда к аккаунту могли получить доступ: старые
    # ссылки и входы на других устройствах перестают работать.
    await app.miniapp.revoke_tokens(account["telegram_id"])
    LOGIN_FAILURES.reset(email)
    return await _signed_in(request, account["telegram_id"])


async def _mail(request: web.Request, email: str, subject: str, text: str) -> bool:
    try:
        await context(request).mailer.send(email, subject, text)
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
    await _mail(request, email, "Подтверждение почты — Дневник",
                f"Код подтверждения: {code}\nОн действует 15 минут.")


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
        "verified": bool(account and account["email_verified_at"]),
        "telegram": has_telegram(user.telegram_id),
        # Только «не вошёл» (нет школы). enabled=False — это и выключенные
        # уведомления в боте: таким дневник открывать как обычно.
        # Родному с просмотром свой дневник не нужен — он смотрит дневник ученика.
        "needs_sgo": not user.school.configured and owner is None,
        "school": user.school.name,
        "mail": app.mailer.configured,
        "login": attempt.status_json() if isinstance(attempt, WebLogin) else None,
    }})


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
    bot = request.config_dict.get(BOT)
    username = ""
    if bot is not None:
        try:
            username = (await bot.get_me()).username or ""
        except Exception:  # noqa: BLE001
            username = ""
    return web.json_response({"ok": True, "data": {
        "code": code, "url": f"https://t.me/{username}?start=link_{code}" if username else ""}})


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
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE)
    return response


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
                payload = await login_flow.qr_login(app, candidate.school.url, candidate.school.name, show_qr)
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
        attempt.status, attempt.message = "error", exc.user_message
        attempt.retry = attempt.method == "qr"
    except Exception as exc:  # noqa: BLE001 — попытка должна закончиться понятной ошибкой
        logger.warning("Вход с сайта не удался: %s", type(exc).__name__)
        attempt.status, attempt.message = "error", wrap(exc, context="вход").user_message


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
