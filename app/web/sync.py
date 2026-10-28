"""Синхронизация открытых приложений: изменение на одном устройстве сразу
видно на остальных.

После успешного изменения (отметка ДЗ, свои задачи, оформление, настройки,
устройства…) сервер шлёт по сокету приложения сигнал {"t": "sync", "topic": …}
всем вкладкам этого человека, кроме той, что изменила (X-Netschool-Tab).
Данные клиент забирает обычным запросом — сигнал только
говорит, что именно перечитать.

Завершённые сеансы закрываются сразу: сокет того устройства получает
{"t": "signed-out"}, и приложение там выходит, не дожидаясь запроса.
"""

from __future__ import annotations

from aiohttp import web

from .api import USER, context
from .viewing import VIEWER, _api_path

# Что изменилось — по адресу запроса. Первое совпадение по началу пути.
TOPICS: tuple[tuple[str, str], ...] = (
    ("/api/homework/done", "homework"),
    ("/api/homework/", "answers"),
    ("/api/notes", "notes"),
    ("/api/prefs", "prefs"),
    ("/api/settings/", "settings"),
    ("/api/caldav-", "settings"),
    ("/api/lock/settings", "settings"),
    ("/api/family/", "family"),
    ("/api/mail/", "mail"),
    ("/api/devices/new-link/status", ""),
    ("/api/devices/new-link/send-qr", ""),
    ("/api/devices/", "devices"),
    ("/api/account/logout", "devices"),
    ("/api/student", "account"),
    ("/api/account/netschool-logout", "account"),
)
# После этих изменений могли пропасть сеансы — их сокеты закрываем.
REVOKING = frozenset(("devices",))
CHANGES = frozenset(("POST", "PUT", "PATCH", "DELETE"))


def topic_for(path: str) -> str:
    path = _api_path(path)
    for prefix, topic in TOPICS:
        if path == prefix or path.startswith(prefix.rstrip("/") + "/") or (
                prefix.endswith("-") and path.startswith(prefix)):
            return topic
    return ""


async def publish(app, telegram_id: int, topic: str, *, skip_tab: str = "") -> None:
    await app.hub.publish_user(telegram_id, {"t": "sync", "topic": topic}, skip_tab=skip_tab)
    if topic in REVOKING:
        await app.hub.sign_out_devices(telegram_id, await app.miniapp.live_fingerprints(telegram_id))


@web.middleware
async def sync_middleware(request: web.Request, handler):
    response = await handler(request)
    if request.method not in CHANGES or response.status != 200 or USER not in request:
        return response
    if topic := topic_for(request.path):
        user = request.get(VIEWER) or request[USER]
        await publish(context(request), user.telegram_id, topic,
                      skip_tab=request.headers.get("X-Netschool-Tab", "")[:40])
    return response
