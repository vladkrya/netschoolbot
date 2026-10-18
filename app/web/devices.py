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

import calendar
import hashlib
import time

from aiohttp import web

from ..accounts.site_login import describe_browser, device_model, system_version
from .api import context, current_user, json_error

routes = web.RouteTableDef()

LINKS, CALENDAR = "links", "calendar"
# Активность пишем не на каждый запрос: раз в 10 минут на токен.
TOUCH_EVERY = 600
_touched: dict[str, float] = {}


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


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
    await context(request).miniapp.touch_token(
        token, label, model=" · ".join(part for part in (model, version) if part), ip=masked_ip(request))


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


@routes.post("/api/devices/revoke-others")
async def revoke_others(request: web.Request) -> web.Response:
    """Завершить все сеансы, кроме этого. Telegram и подписка на календарь
    (она открывает только расписание) остаются."""
    app, user = context(request), current_user(request)
    current = "" if request.get("verified_telegram") else request_token(request)
    await app.miniapp.revoke_where(user.telegram_id, "scope = '' AND token != ?", (current,))
    await app.miniapp.drop_install_codes(user.telegram_id)
    return await devices(request)


@routes.post("/api/devices/new-link")
async def new_device_link(request: web.Request) -> web.Response:
    """«Добавить устройство» — то же, что /app_install в боте: ссылка на сутки,
    которая даст новому устройству свой токен, и QR-код для камеры телефона."""
    from .account import _render_qr

    app, user = context(request), current_user(request)
    code = await app.miniapp.issue_enter_code(user.telegram_id)
    url = f"{app.settings.web.miniapp_url}/?enter={code}"
    return web.json_response({"ok": True, "data": {"url": url, "qr": _render_qr(url)}})
