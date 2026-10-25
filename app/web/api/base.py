"""Общее для всех обработчиков API: ключи приложения, авторизация, ошибки и кэш."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import logging

from aiohttp import web

from ...context import AppContext
from ...domain.models import User
from ...netschool.errors import NetSchoolError
from ..telegram_auth import InitDataError, verify_init_data


logger = logging.getLogger("netschoolbot.web")

routes = web.RouteTableDef()

# Типизированные ключи вместо строк: aiohttp так избегает конфликтов имён
# между приложением и подключёнными к нему библиотеками.
CONTEXT = web.AppKey("context", AppContext)
BOT = web.AppKey("bot", object)
# AppKey предназначен для Application; Request хранит строковые ключи.
USER = "netschool_user"

# Не задерживаем показ сохранённых данных ожиданием школьного сервера.
CACHE_RESPONSE_WAIT = 0.2
# Обновление жестом — человек явно просит свежие данные и видит индикатор,
# поэтому ждём школьный сервер всерьёз. Меньше таймаута запроса в браузере.
REFRESH_RESPONSE_WAIT = 60


def _cache_owner(user: User) -> tuple:
    return user.school, user.credentials, user.selected_student_id, user.effective_api_mode


def _refresh_cached_section(app, user, section, producer, cacheable):
    key = user.telegram_id, section, _cache_owner(user)
    if task := app.cache_refreshes.get(key):
        return task

    async def refresh():
        try:
            payload = await producer(app, user)
        except NetSchoolError as exc:
            if exc.reason.breaks_diary:
                await _count_check(app, user, False)
            raise
        await _count_check(app, user, True)
        current = await app.users.get(user.telegram_id)
        # Переключение ребёнка или выход во время запроса не должны вернуть
        # в очищенный кэш данные прежнего аккаунта.
        if current is not None and _cache_owner(current) == _cache_owner(user) and cacheable(payload):
            await app.cache.put(user.telegram_id, section, payload)
        return payload

    task = asyncio.create_task(refresh(), name=f"cache:{section}")
    app.cache_refreshes[key] = task

    def done(completed):
        if app.cache_refreshes.get(key) is completed:
            del app.cache_refreshes[key]
        if not completed.cancelled() and (error := completed.exception()):
            logger.info("Обновление кэша %s не завершилось: %s", section, type(error).__name__)

    task.add_done_callback(done)
    return task


async def _count_check(app, user, ok: bool) -> None:
    """Загрузка из приложения — тоже проверка дневника для страницы статуса:
    фоновые проверки не видят, что вход сломан у всех, пока жива их сессия."""
    if app.status is None or not user.school.url:
        return
    try:
        await app.status.record_account_check(user.school.url, ok, who=user.telegram_id)
    except Exception as exc:  # noqa: BLE001 — статус не должен ломать загрузку
        logger.warning("Не удалось учесть загрузку в статусе: %s", type(exc).__name__)


def context(request: web.Request) -> AppContext:
    return request.app[CONTEXT]


def current_user(request: web.Request) -> User:
    """Пользователь запроса. Проставляется middleware авторизации."""
    return request[USER]


def json_error(message: str, *, status: int = 400, reason: str = "") -> web.Response:
    return web.json_response(
        {"ok": False, "error": message, "reason": reason}, status=status
    )


@web.middleware
async def error_middleware(request: web.Request, handler):
    """Единая обработка ошибок API.

    Раньше каждый обработчик ловил исключения сам, по-своему, и в браузер
    улетал то JSON, то HTML-страница ошибки Flask, то текст исключения.
    """
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except NetSchoolError as exc:
        status = 401 if exc.reason.needs_relogin else 503
        return json_error(exc.user_message, status=status, reason=exc.reason.value)
    except Exception:
        logger.exception("Ошибка в %s %s", request.method, request.path)
        return json_error("Не удалось выполнить запрос из-за ошибки приложения. Попробуйте позже.", status=500)


async def resolve_identity(request: web.Request) -> tuple[int | None, str]:
    """Кто делает запрос. Возвращает (telegram_id, способ).

    Два пути входа, и порядок между ними важен:

    1. `initData` от Telegram — подписана токеном бота, подделать её нельзя.
       Проверяется первой: если приложение открыто внутри Telegram, это
       самое надёжное удостоверение, и никакой токен из ссылки его не
       перебивает.
    2. Постоянный токен из ссылки `/app` — для установленного на домашний
       экран приложения, которое открывается вне Telegram и initData не
       получает.
    """
    app = context(request)

    init_data = (
        request.headers.get("X-Telegram-Init-Data") or request.query.get("tgWebAppData") or ""
    ).strip()
    if init_data:
        try:
            tg_user = verify_init_data(init_data, app.settings.telegram.bot_token)
        except InitDataError as exc:
            logger.info("initData отклонена: %s", exc)
        else:
            return tg_user.id, "telegram"

    token = (
        request.headers.get("X-Netschool-Token")
        or request.query.get("token")
        or request.cookies.get("netschool_token")
        or ""
    ).strip()
    if token:
        telegram_id = await app.miniapp.resolve_token(token)
        if telegram_id is not None:
            from ..devices import touch
            await touch(request, token)
        return telegram_id, "token"

    return None, "none"


# Билет на вложения: вложение открывается во внешнем браузере, куда не
# передать ни заголовок разблокировки, ни initData. Постоянный токен в адресе
# остался бы в истории браузера, поэтому в ссылке — подписанный билет только
# на вложения, на те же 12 часов, что и разблокировка.
ATTACHMENT_TICKET_TTL = 12 * 3600


def _ticket_key(app: AppContext) -> bytes:
    return hmac.new(b"attachment-ticket", app.settings.telegram.bot_token.encode(), hashlib.sha256).digest()


def issue_attachment_ticket(app: AppContext, telegram_id: int, now: float | None = None) -> str:
    expires = int((now if now is not None else dt.datetime.now().timestamp()) + ATTACHMENT_TICKET_TTL)
    body = f"{telegram_id}.{expires}"
    signature = hmac.new(_ticket_key(app), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{signature}"


def verify_attachment_ticket(app: AppContext, ticket: str, now: float | None = None) -> int | None:
    try:
        telegram_id, expires, signature = ticket.split(".")
        body = f"{int(telegram_id)}.{int(expires)}"
    except ValueError:
        return None
    expected = hmac.new(_ticket_key(app), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, signature):
        return None
    if int(expires) < (now if now is not None else dt.datetime.now().timestamp()):
        return None
    return int(telegram_id)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Опознание пользователя для запросов к API."""
    # /captcha-proxy/api/v1/… — API капчи, а не наше: его зовёт виджет без входа.
    if "/api/" not in request.path or "/captcha-proxy/" in request.path:
        return await handler(request)

    # Вложение по билету: билет выдан уже опознанному и разблокированному
    # приложению, поэтому ни входа, ни PIN здесь не спрашиваем.
    ticket = request.query.get("ticket", "")
    if ticket and ("/api/attachment/" in request.path or "/api/export/" in request.path
                   or request.path.endswith("/api/admin/android.apk")):
        telegram_id = verify_attachment_ticket(context(request), ticket)
        user = await context(request).users.get(telegram_id) if telegram_id else None
        if user is None:
            return json_error("Ссылка на вложение устарела — откройте его в приложении ещё раз", status=401, reason="auth")
        request[USER] = user
        from .. import viewing
        if (refused := await viewing.apply(request, context(request), USER)) is not None:
            return refused
        return await handler(request)

    # Регистрация, вход по почте и сброс пароля — до всякого входа.
    from ..account import is_public
    if is_public(request.path):
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    # Короткий билет выдан запросом, прошедшим обычную авторизацию и PIN.
    if request.path.rstrip('/').endswith('/api/chat/ws'):
        from ..chat import verify_ws_ticket
        telegram_id = verify_ws_ticket(context(request), ticket)
        user = await context(request).users.get(telegram_id) if telegram_id else None
        if user is None:
            return json_error("Билет чата устарел", status=401, reason="auth")
        request[USER] = user
        return await handler(request)

    # Токен подписки на календарь — только для calendar.ics и без PIN: он
    # открывает одно расписание, а календарь PIN ввести не может.
    if request.path.endswith("/api/calendar.ics"):
        calendar_id = await context(request).miniapp.resolve_calendar_token(request.query.get("token", ""))
        if calendar_id is not None and (user := await context(request).users.get(calendar_id)) is not None:
            request[USER] = user
            from ..devices import touch
            await touch(request, request.query["token"])
            return await handler(request)

    telegram_id, method = await resolve_identity(request)
    if telegram_id is None:
        return json_error(
            "Не удалось вас опознать. Откройте приложение кнопкой в боте "
            "или запросите новую ссылку: /app",
            status=401,
            reason="auth",
        )

    user = await context(request).users.get(telegram_id)
    if user is None:
        return json_error(
            "Вы ещё не входили в «Сетевой город». Откройте бота и выполните /login",
            status=401,
            reason="login",
        )

    request[USER] = user
    request['verified_telegram'] = method == 'telegram'
    from ..lock import guard
    blocked = await guard(request)
    if blocked is not None:
        return blocked
    # Родной с правом просмотра: дневник ученика — только чтение (viewing.py).
    # После guard: PIN спрашивается у того, кто вошёл, а не у ученика.
    from .. import viewing
    if (refused := await viewing.apply(request, context(request), USER)) is not None:
        return refused
    response = await handler(request)

    # Токен из ссылки переносим в cookie, чтобы он не тянулся в каждом URL
    # и не утекал в историю браузера и в заголовок Referer.
    if method == "token" and request.query.get("token") and "netschool_token" not in response.cookies:
        response.set_cookie(
            "netschool_token",
            request.query["token"],
            max_age=365 * 24 * 3600,
            httponly=True,
            samesite="Lax",
            secure=request.url.scheme == "https",
        )
    return response


async def _cached(request: web.Request, section: str, producer, *, cacheable=lambda payload: True, fallback_section=None,
                  fresh_seconds: int | None = None):
    """Отдать сохранённые данные сразу, обновив устаревшие в фоне.

    Если школьный сервер недоступен, отдаётся устаревший кэш с пометкой —
    приложение продолжает работать, а не показывает пустой экран.
    `cacheable` не пускает в кэш ответ, который лишь сообщает «данных нет»:
    иначе разовый сбой запомнился бы на весь срок свежести кэша.
    """
    app = context(request)
    user = current_user(request)
    cached = await app.cache.get(user.telegram_id, section)
    if cached is None and fallback_section:
        cached = await app.cache.get(user.telegram_id, fallback_section)
    fresh_for = dt.timedelta(seconds=fresh_seconds or app.settings.web.cache_fresh_seconds)

    if cached is not None and not request.query.get("refresh"):
        payload, updated = cached
        # updated_at пишется SQLite-функцией datetime('now') — это UTC.
        # С локальным now() в Москве кэш всегда был «старше на 3 часа»,
        # и каждое открытие приложения шло в школьный сервер.
        now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        if now_utc - updated < fresh_for:
            # Возраст кэша нужен приложению: при входе оно показывает сохранённое
            # сразу, а заметно устаревшее тут же перепроверяет у школьного сервера.
            age = max(0, int((now_utc - updated).total_seconds()))
            return web.json_response({"ok": True, "data": payload, "stale": False, "age": age})

    task = _refresh_cached_section(app, user, section, producer, cacheable)
    if cached is not None:
        # Раньше и обновление жестом получало кэш через 0,2 с, а экран после
        # фоновой загрузки не перерисовывался — «обновление» ничего не меняло.
        wait = REFRESH_RESPONSE_WAIT if request.query.get("refresh") else CACHE_RESPONSE_WAIT
        completed, _ = await asyncio.wait({task}, timeout=wait)
        if not completed:
            return web.json_response(
                {"ok": True, "data": cached[0], "stale": True, "refreshing": True}
            )
    try:
        # Отмена HTTP-запроса не прерывает общее обновление для других вкладок.
        payload = await asyncio.shield(task)
    except NetSchoolError as exc:
        if cached is None:
            raise
        logger.info("Отдаю устаревший кэш %s для %s: %s", section, user.telegram_id, exc.reason)
        return web.json_response(
            {"ok": True, "data": cached[0], "stale": True, "reason": exc.reason.value}
        )

    return web.json_response({"ok": True, "data": payload, "stale": False})


def halloween_season(today: dt.date) -> bool:
    """Хеллоуинское оформление включено у всех: с 18 октября по 8 ноября
    включительно (до 23:59 по времени человека)."""
    return (10, 18) <= (today.month, today.day) <= (11, 8)


def _utc_iso(moment: dt.datetime | None) -> str | None:
    """Время из базы (UTC без пояса) — в ISO с «Z», чтобы браузер показал местное."""
    return moment.isoformat(timespec="seconds") + "Z" if moment else None
