"""Вход на сайт через Telegram по OpenID Connect (Authorization Code + PKCE).

Скрипт Telegram на страницу не ставим — telegram.org в России бывает
недоступен. Кнопка ведёт на наш /tg-oidc/start, оттуда — на страницу входа
Telegram, а Telegram возвращает на /tg-oidc/callback с кодом. Код сервер
меняет на id_token (JWT) и проверяет его подпись по ключам Telegram.
https://core.telegram.org/bots/telegram-login
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from .telegram_auth import InitDataError, TelegramUser

ISSUER = "https://oauth.telegram.org"
AUTH_URL = f"{ISSUER}/auth"
TOKEN_URL = f"{ISSUER}/token"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
SCOPE = "openid profile"
PENDING_TTL_S = 600
MAX_PENDING = 2000
JWKS_TTL_S = 3600
HTTP_TIMEOUT_S = 15
CLOCK_SKEW_S = 60


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(slots=True)
class Pending:
    verifier: str
    nonce: str
    redirect_uri: str
    created: float


class OidcLogins:
    """Начатые входы: state → PKCE-ключ, nonce и адрес возврата (в памяти)."""

    def __init__(self) -> None:
        self._items: dict[str, Pending] = {}
        self._jwks: dict[str, dict] = {}
        self._jwks_at = 0.0

    def start(self, client_id: str, redirect_uri: str) -> tuple[str, str]:
        """Новый вход: (state, адрес страницы входа Telegram)."""
        now = time.monotonic()
        self._items = {k: v for k, v in self._items.items() if now - v.created < PENDING_TTL_S}
        if len(self._items) >= MAX_PENDING:
            self._items.pop(next(iter(self._items)))
        state, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        self._items[state] = Pending(verifier, nonce, redirect_uri, now)
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        query = {
            "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": SCOPE, "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256",
        }
        return state, f"{AUTH_URL}?{urlencode(query)}"

    def take(self, state: str) -> Pending | None:
        """Одноразово: второй возврат с тем же state не пройдёт."""
        item = self._items.pop(state, None)
        if item is None or time.monotonic() - item.created > PENDING_TTL_S:
            return None
        return item

    async def finish(self, item: Pending, code: str, client_id: str, client_secret: str) -> TelegramUser:
        """Обменять код на id_token и проверить его."""
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(TOKEN_URL, data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": item.redirect_uri,
                "client_id": client_id, "code_verifier": item.verifier,
            }, headers={"Authorization": "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()},
            ) as response:
                body = await response.json(content_type=None)
                if response.status != 200 or not isinstance(body, dict) or not body.get("id_token"):
                    raise InitDataError(f"Telegram не выдал токен: {response.status} {str(body)[:200]}")
            claims = await self._verify(session, body["id_token"], client_id)
        if claims.get("nonce") != item.nonce:
            raise InitDataError("nonce не совпал")
        return user_from_claims(claims)

    async def _verify(self, session: aiohttp.ClientSession, token: str, client_id: str) -> dict:
        header = _jwt_header(token)
        key = await self._key(session, str(header.get("kid") or ""))
        return verify_id_token(token, key, client_id)

    async def _key(self, session: aiohttp.ClientSession, kid: str) -> dict:
        fresh = time.monotonic() - self._jwks_at < JWKS_TTL_S
        if kid not in self._jwks or not fresh:
            async with session.get(JWKS_URL) as response:
                keys = (await response.json(content_type=None)).get("keys", [])
            self._jwks = {str(key.get("kid") or ""): key for key in keys if isinstance(key, dict)}
            self._jwks_at = time.monotonic()
        if kid not in self._jwks:
            raise InitDataError("Ключ подписи Telegram не найден")
        return self._jwks[kid]


def _jwt_header(token: str) -> dict:
    try:
        return json.loads(_unb64url(token.split(".")[0]))
    except (ValueError, IndexError) as exc:
        raise InitDataError("id_token не разбирается") from exc


def verify_id_token(token: str, jwk: dict, client_id: str, *, now: float | None = None) -> dict:
    """Проверить подпись (RS256 или ES256) и поля id_token, вернуть claims."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_unb64url(header_b64))
        claims = json.loads(_unb64url(payload_b64))
        signature = _unb64url(signature_b64)
    except ValueError as exc:
        raise InitDataError("id_token не разбирается") from exc
    signed = f"{header_b64}.{payload_b64}".encode()
    alg = header.get("alg")
    try:
        if alg == "RS256" and jwk.get("kty") == "RSA":
            public = rsa.RSAPublicNumbers(
                int.from_bytes(_unb64url(jwk["e"]), "big"), int.from_bytes(_unb64url(jwk["n"]), "big")
            ).public_key()
            public.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        elif alg == "ES256" and jwk.get("kty") == "EC" and jwk.get("crv") == "P-256" and len(signature) == 64:
            public = ec.EllipticCurvePublicNumbers(
                int.from_bytes(_unb64url(jwk["x"]), "big"), int.from_bytes(_unb64url(jwk["y"]), "big"), ec.SECP256R1()
            ).public_key()
            der = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
            public.verify(der, signed, ec.ECDSA(hashes.SHA256()))
        else:
            raise InitDataError(f"Неподдерживаемая подпись id_token: {alg}")
    except InvalidSignature as exc:
        raise InitDataError("Подпись id_token не совпала") from exc
    except (KeyError, ValueError) as exc:
        raise InitDataError("Ключ подписи Telegram не читается") from exc

    now = time.time() if now is None else now
    if claims.get("iss") != ISSUER:
        raise InitDataError("id_token выдан не Telegram")
    audience = claims.get("aud")
    if str(client_id) not in ([str(a) for a in audience] if isinstance(audience, list) else [str(audience)]):
        raise InitDataError("id_token выдан другому боту")
    try:
        if float(claims["exp"]) < now - CLOCK_SKEW_S:
            raise InitDataError("id_token устарел")
    except (KeyError, TypeError, ValueError) as exc:
        raise InitDataError("В id_token нет срока действия") from exc
    return claims


def user_from_claims(claims: dict) -> TelegramUser:
    try:
        user_id = int(claims["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InitDataError("В id_token нет пользователя Telegram") from exc
    return TelegramUser(
        id=user_id,
        first_name=str(claims.get("given_name") or claims.get("name") or ""),
        last_name=str(claims.get("family_name") or ""),
        username=str(claims.get("preferred_username") or ""),
    )
