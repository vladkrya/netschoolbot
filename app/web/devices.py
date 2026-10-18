"""Экран «Устройства»: где открыт дневник и как завершить сеанс.

Основной сеанс — мини-приложение в Telegram: там вход подтверждает сам
Telegram, завершить его отсюда нельзя (выход из Telegram — в Telegram).
Остальное — токены (miniapp_tokens):

* устройства (device=1) — вход через бота, по почте или по ссылке установки,
  у каждого свой токен; завершить можно каждое по отдельности;
* «ссылки из бота» (device=0) — общий токен старых ссылок и иконок с токеном
  в адресе; их не различить между собой, поэтому завершаются разом;
* подписка на календарь (scope='calendar') — открывает только расписание.

Сами токены наружу не отдаются: у строки только короткий отпечаток.
"""

from __future__ import annotations

import base64
import calendar
import time

from aiogram.types import BufferedInputFile
from aiohttp import web

from ..accounts import has_telegram
from ..accounts.site_login import describe_browser, device_model, system_version
from ..db.repositories import token_fingerprint as fingerprint
from .api import BOT, context, current_user, json_error

routes = web.RouteTableDef()

LINKS, CALENDAR = "links", "calendar"
# Активность пишем не на каждый запрос: раз в 10 минут на токен.
TOUCH_EVERY = 600
_touched: dict[str, float] = {}


def masked_ip(request: web.Request) -> str:
    """Примерный адрес — «95.24.*.*»: узнать чужое устройство хватает, а
    точный адрес не хранится."""
    ip = request.headers.get("X-Real-IP") or request.remote or ""
    if ":" in ip:
        return ":".join(ip.split(":")[:2]) + ":…"
    parts = ip.split(".")
    return f"{parts[0]}.{parts[1]}.*.*" if len(parts) == 4 else ""


async def touch(request: web.Request, token: str) -> None:
    ua = request.headers.get("User-Agent", "")
    label, model = describe_browser(ua), device_model(ua)
    if request.headers.get('X-Netschool-Client') == 'pwa':
        label = 'Веб-приложение · ' + label
    version = system_version(ua)
    key = f"{token}|{label}|{model}"
    now = time.monotonic()
    if now - _touched.get(key, -TOUCH_EVERY) < TOUCH_EVERY:
        return
    _touched[key] = now
    if len(_touched) > 50_000:
        _touched.clear()
    app = context(request)
    owner = await app.miniapp.touch_token(
        token, label, model=" · ".join(part for part in (model, version) if part), ip=masked_ip(request))
    # «Активность» и название устройства — сразу на открытых «Устройствах».
    if owner is not None:
        await app.hub.publish_user(owner, {"t": "sync", "topic": "devices"})


def request_token(request: web.Request) -> str:
    return (
        request.headers.get("X-Netschool-Token")
        or request.query.get("token")
        or request.cookies.get("netschool_token")
        or ""
    ).strip()


def _when(value) -> float | None:
    """created_at — текст SQLite в UTC, last_used — секунды."""
    if value is None or isinstance(value, (int, float)):
        return value
    try:
        return float(calendar.timegm(time.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")))
    except ValueError:
        return None


def build(rows: list[dict], current: str, in_telegram: bool, clients: list[dict] | None = None) -> list[dict]:
    items = [{"id": "telegram", "kind": "telegram", "label": "Telegram", "current": in_telegram,
              "main": True, "created": None, "last_used": None}]
    links = [row for row in rows if not row["device"] and row["scope"] == ""]
    calendar = [row for row in rows if row["scope"] == CALENDAR]
    for row in rows:
        if row["device"] and row["scope"] == "":
            items.append({
                "id": fingerprint(row["token"]), "kind": "device", "label": row["label"] or "Браузер",
                "name": row.get("name") or "",
                "current": row["token"] == current, "created": _when(row["created_at"]),
                "last_used": _when(row["last_used"]),
            })
    for kind, group, label in ((LINKS, links, "Ссылки из бота"), (CALENDAR, calendar, "Подписка на календарь")):
        if group:
            used = [_when(row["last_used"]) for row in group if row["last_used"]]
            extra = {"clients": [{
                "label": client["client"], "details": client["model"], "ip": client["ip"],
                "first_seen": client["first_seen"], "last_seen": client["last_seen"],
            } for client in clients or []]} if kind == LINKS else {}
            items.append({**extra,
                "id": kind, "kind": kind, "label": label, "count": len(group),
                "current": any(row["token"] == current for row in group),
                "created": min(filter(None, (_when(row["created_at"]) for row in group)), default=None),
                "last_used": max(used) if used else None,
            })
    return items


@routes.get("/api/devices")
async def devices(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    rows = await app.miniapp.sessions(user.telegram_id)
    clients = await app.miniapp.link_clients(user.telegram_id)
    in_telegram = bool(request.get("verified_telegram"))
    return web.json_response({"ok": True, "data": {
        "items": build(rows, "" if in_telegram else request_token(request), in_telegram, clients)}})


@routes.post("/api/devices/revoke")
async def revoke(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    target = str((body or {}).get("id") or "")
    if target == "telegram":
        return json_error("Сеанс в Telegram завершить нельзя — это основной вход")
    if target == LINKS:
        count = await app.miniapp.revoke_where(user.telegram_id, "device = 0 AND scope = ''")
    elif target == CALENDAR:
        count = await app.miniapp.revoke_where(user.telegram_id, "scope = 'calendar'")
    else:
        tokens = [row["token"] for row in await app.miniapp.sessions(user.telegram_id)
                  if row["device"] and fingerprint(row["token"]) == target]
        count = await app.miniapp.revoke_where(user.telegram_id, "token = ?", (tokens[0],)) if tokens else 0
    if not count:
        return json_error("Этот сеанс уже завершён", status=404)
    return await devices(request)


NAME_LIMIT = 40


@routes.post("/api/devices/rename")
async def rename(request: web.Request) -> web.Response:
    """Своя подпись сеанса; пустая — снова название браузера."""
    app, user = context(request), current_user(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    target = str(body.get("id") or "")
    name = " ".join(str(body.get("name") or "").split())[:NAME_LIMIT]
    tokens = [row["token"] for row in await app.miniapp.sessions(user.telegram_id)
              if row["device"] and row["scope"] == "" and fingerprint(row["token"]) == target]
    if not tokens or not await app.miniapp.set_token_name(user.telegram_id, tokens[0], name):
        return json_error("Этот сеанс уже завершён", status=404)
    return await devices(request)


@routes.post("/api/devices/revoke-others")
async def revoke_others(request: web.Request) -> web.Response:
    """Завершить все сеансы, кроме этого. Telegram и подписка на календарь
    (она открывает только расписание) остаются."""
    app, user = context(request), current_user(request)
    current = "" if request.get("verified_telegram") else request_token(request)
    await app.miniapp.revoke_where(user.telegram_id, "scope = '' AND token != ?", (current,))
    await app.miniapp.drop_install_codes(user.telegram_id)
    return await devices(request)


@routes.post("/api/devices/own")
async def own_token(request: web.Request) -> web.Response:
    """Свой токен для иконки на экране «Домой» iPhone. При установке iOS
    копирует в иконку cookie Safari — и токен у них оказывался общий: выход
    или вход в другой аккаунт в Safari выкидывал и иконку. Иконка один раз
    берёт себе новый токен, у Safari остаётся прежний."""
    app, user = context(request), current_user(request)
    current = "" if request.get("verified_telegram") else request_token(request)
    if not current or not await app.miniapp.is_device_token(current):
        return web.json_response({"ok": True, "data": {"token": ""}})
    token = await app.miniapp.issue_token(user.telegram_id, device=True)
    response = web.json_response({"ok": True, "data": {"token": token}})
    response.set_cookie("netschool_token", token, max_age=365 * 24 * 3600, httponly=True, samesite="Lax",
                        secure=request.url.scheme == "https")
    return response


def _device_url(app, code: str) -> str:
    return f"{app.settings.web.miniapp_url}/?device={code}&install=1"


@routes.post("/api/devices/new-link")
async def new_device_link(request: web.Request) -> web.Response:
    """«Добавить устройство»: ссылка на час и одно открытие, QR-код для камеры
    телефона. Метка install=1 — на телефоне сразу покажется, как поставить
    дневник на экран «Домой». /app_install в боте по-прежнему даёт ссылку на
    сутки: её открывают сначала в Telegram, потом в Safari."""
    from .account import _render_qr

    app, user = context(request), current_user(request)
    code, expires = await app.miniapp.issue_device_code(user.telegram_id)
    url = _device_url(app, code)
    return web.json_response({"ok": True, "data": {"url": url, "code": code, "expires": expires, "qr": _render_qr(url)}})


async def _code(request: web.Request) -> str:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    return str((body or {}).get("code") or "")


@routes.post("/api/devices/new-link/status")
async def new_device_status(request: web.Request) -> web.Response:
    """Экран ссылки спрашивает раз в несколько секунд: открыли ли её."""
    app, user = context(request), current_user(request)
    row = await app.miniapp.device_code(user.telegram_id, await _code(request))
    if row is None:
        return web.json_response({"ok": True, "data": {"state": "gone"}})
    if row["used_at"]:
        return web.json_response({"ok": True, "data": {
            "state": "used", "label": row["label"] or "Браузер",
            "id": fingerprint(row["token"]) if row["token"] else None}})
    state = "waiting" if row["expires_at"] > time.time() else "expired"
    return web.json_response({"ok": True, "data": {"state": state, "expires": row["expires_at"]}})


@routes.post("/api/devices/new-link/cancel")
async def new_device_cancel(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    await app.miniapp.drop_device_code(user.telegram_id, await _code(request))
    return web.json_response({"ok": True})


@routes.post("/api/devices/new-link/send-qr")
async def new_device_send_qr(request: web.Request) -> web.Response:
    """QR-код картинкой в чат с ботом: из окна Telegram файл не сохранить."""
    from .account import _render_qr

    app, user = context(request), current_user(request)
    code = await _code(request)
    row = await app.miniapp.device_code(user.telegram_id, code)
    if row is None or row["used_at"] or row["expires_at"] <= time.time():
        return json_error("Ссылка уже не действует — создайте новую")
    bot = request.config_dict.get(BOT)
    if bot is None or not has_telegram(user.telegram_id):
        return json_error("Telegram не привязан")
    qr = _render_qr(_device_url(app, code))
    if not qr:
        return json_error("QR-код сейчас не собрать — скопируйте ссылку")
    png = base64.b64decode(qr.split(",", 1)[1])
    left = max(1, round((row["expires_at"] - time.time()) / 60))
    await bot.send_photo(
        user.telegram_id, BufferedInputFile(png, filename="dnevnik-qr.png"),
        caption=f"📱 QR-код для нового устройства. Действует {left} мин и только на одно открытие — "
                "не пересылайте его чужим.",
    )
    return web.json_response({"ok": True})
