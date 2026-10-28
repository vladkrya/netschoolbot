"""WebSocket приложения: новые оценки, синхронизация устройств, завершение сеанса.

Браузер не умеет ставить заголовки на WebSocket, поэтому сокет открывается
по короткому билету: его выдаёт обычный запрос, уже прошедший вход и PIN.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from aiohttp import WSMsgType, web

from ..live import Listener, LiveHub
from .api import _ticket_key, context, current_user

routes = web.RouteTableDef()
HUB = web.AppKey("live_hub", LiveHub)
WS_TICKET_TTL = 60


def _ws_signature(app: Any, body: str) -> str:
    return hmac.new(_ticket_key(app), f"live-ws|{body}".encode(), hashlib.sha256).hexdigest()[:32]


def issue_ws_ticket(app: Any, telegram_id: int, now: float | None = None, device: str = "") -> str:
    """device — отпечаток токена устройства: завершат сеанс — сокет закроется."""
    body = f"{telegram_id}.{int((now if now is not None else time.time()) + WS_TICKET_TTL)}"
    if device:
        body += f".{device}"
    return f"{body}.{_ws_signature(app, body)}"


def ws_ticket_device(ticket: str) -> str:
    """Отпечаток устройства из билета, уже проверенного verify_ws_ticket."""
    parts = ticket.split(".")
    return parts[2] if len(parts) == 4 else ""


def verify_ws_ticket(app: Any, ticket: str, now: float | None = None) -> int | None:
    try:
        *head, signature = ticket.split(".")
        telegram_id, expires, *device = head
        if len(device) > 1:
            return None
        body = ".".join([str(int(telegram_id)), str(int(expires)), *device])
    except ValueError:
        return None
    if not hmac.compare_digest(_ws_signature(app, body), signature):
        return None
    if int(expires) < (now if now is not None else time.time()):
        return None
    return int(telegram_id)


# Прежние пути /api/chat/ws* держим для установленных приложений на Android.
@routes.post("/api/live/ws-ticket")
@routes.post("/api/chat/ws-ticket")
async def ws_ticket(request: web.Request) -> web.Response:
    from .devices import fingerprint, request_token
    token = "" if request.get("verified_telegram") else request_token(request)
    return web.json_response({"ok": True, "data": {"ticket": issue_ws_ticket(
        context(request), current_user(request).telegram_id, device=fingerprint(token) if token else "")}})


@routes.get("/api/live/ws")
@routes.get("/api/chat/ws")
async def live_ws(request: web.Request) -> web.StreamResponse:
    uid = current_user(request).telegram_id
    socket = web.WebSocketResponse(heartbeat=25)
    await socket.prepare(request)
    listener = Listener(uid, socket=socket, tab=request.query.get("tab", "")[:40],
                        device=ws_ticket_device(request.query.get("ticket", "")))
    hub = request.config_dict[HUB]
    hub.add(listener)
    try:
        await socket.send_json({"t": "hello"})
        async for frame in socket:
            if frame.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(frame.data)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("t") == "ping":
                await socket.send_json({"t": "pong"})
    finally:
        hub.remove(listener)
    return socket
