"""Вход на сайт через российский OAuth: Яндекс ID (Authorization Code + PKCE).

Кнопка ведёт на наш /api/account/yandex/start, оттуда — на страницу входа
Яндекса, а Яндекс возвращает на /api/account/yandex/callback с кодом. Код
сервер меняет на токен и по нему один раз спрашивает id пользователя и имя.
Токен нигде не хранится. Почту и телефон не запрашиваем: только доступ
login:info. https://yandex.ru/dev/id/doc/ru/
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp

from .telegram_auth import InitDataError

PENDING_TTL_S = 600
MAX_PENDING = 2000
HTTP_TIMEOUT_S = 15


@dataclass(frozen=True, slots=True)
class Provider:
    name: str  # ключ в адресах и в таблице oauth_identities
    title: str
    auth_url: str
    token_url: str
    info_url: str
    scope: str


YANDEX = Provider(
    name="yandex", title="Яндекс ID",
    auth_url="https://oauth.yandex.ru/authorize",
    token_url="https://oauth.yandex.ru/token",
    info_url="https://login.yandex.ru/info?format=json",
    scope="login:info",
)


@dataclass(slots=True)
class Identity:
    """Кто вошёл: постоянный id у провайдера и имя для профиля."""

    subject: str
    name: str = ""


@dataclass(slots=True)
class Pending:
    verifier: str
    redirect_uri: str
    created: float
    # Не вход, а привязка к этому аккаунту из настроек.
    link_to: int | None = None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class OAuthLogins:
    """Начатые входы одного провайдера: state → PKCE-ключ и адрес возврата (в памяти)."""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self._items: dict[str, Pending] = {}

    def start(self, client_id: str, redirect_uri: str, *, link_to: int | None = None) -> tuple[str, str]:
        """Новый вход: (state, адрес страницы входа провайдера)."""
        now = time.monotonic()
        self._items = {k: v for k, v in self._items.items() if now - v.created < PENDING_TTL_S}
        if len(self._items) >= MAX_PENDING:
            self._items.pop(next(iter(self._items)))
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        self._items[state] = Pending(verifier, redirect_uri, now, link_to)
        query = {
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "scope": self.provider.scope, "state": state,
            "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
        }
        return state, f"{self.provider.auth_url}?{urlencode(query)}"

    def take(self, state: str) -> Pending | None:
        """Одноразово: второй возврат с тем же state не пройдёт."""
        item = self._items.pop(state, None)
        if item is None or time.monotonic() - item.created > PENDING_TTL_S:
            return None
        return item

    async def finish(self, item: Pending, code: str, client_id: str, client_secret: str) -> Identity:
        """Обменять код на токен и узнать, кто вошёл."""
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.provider.token_url, data={
                "grant_type": "authorization_code", "code": code, "code_verifier": item.verifier,
                "client_id": client_id, "client_secret": client_secret,
            }) as response:
                body = await response.json(content_type=None)
                if response.status != 200 or not isinstance(body, dict) or not body.get("access_token"):
                    raise InitDataError(f"{self.provider.title} не выдал токен: {response.status} {str(body)[:200]}")
            async with session.get(self.provider.info_url,
                                   headers={"Authorization": f"OAuth {body['access_token']}"}) as response:
                info = await response.json(content_type=None)
                if response.status != 200 or not isinstance(info, dict):
                    raise InitDataError(f"{self.provider.title} не отдал профиль: {response.status}")
        return yandex_identity(info, client_id)


def yandex_identity(info: dict, client_id: str) -> Identity:
    """Ответ login.yandex.ru/info → Identity. Токен должен быть выдан нашему приложению."""
    if str(info.get("client_id") or "") != client_id:
        raise InitDataError("Токен Яндекса выдан другому приложению")
    subject = str(info.get("id") or "").strip()
    if not subject.isdigit():
        raise InitDataError("В ответе Яндекса нет id пользователя")
    name = info.get("display_name") or info.get("real_name") or " ".join(
        part for part in (info.get("first_name"), info.get("last_name")) if part) or info.get("login") or ""
    return Identity(subject=subject, name=str(name)[:64])
