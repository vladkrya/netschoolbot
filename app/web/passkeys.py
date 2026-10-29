"""Ключи входа (passkeys): вход на сайт по Face ID, Touch ID или PIN телефона.

Ключ создают на экране «Устройства», уже войдя. Он хранится в связке ключей
(iCloud, Google, Windows) и сам переезжает на другие устройства человека.
На экране входа браузер предлагает выбрать ключ — сервер узнаёт аккаунт по
его credential_id, почта не нужна.

Это не ключи блокировки (lock.py, app_passkeys): те только снимают PIN на
уже вошедшем устройстве. У ключей входа свой user.id («login-…»), иначе
связка ключей заменила бы ими ключ блокировки с тем же id.
"""

from __future__ import annotations

import json
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import web
from webauthn import (base64url_to_bytes, generate_authentication_options, generate_registration_options,
                      verify_authentication_response, verify_registration_response)
from webauthn.helpers import bytes_to_base64url, options_to_json
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

from ..accounts.site_login import describe_browser
from .api import context, current_user, json_error
from .lock import site

routes = web.RouteTableDef()

CHALLENGE_TTL = 5 * 60
MAX_CHALLENGES = 5000
LIMIT = 10  # ключей на аккаунт
# Запросы ключа живут минуты — в памяти, как вход через бота.
_challenges: dict[str, tuple[str, int | None, bytes, str, str, float]] = {}


def ready(request: web.Request) -> bool:
    """Ключи работают только на https-адресе из настроек (lock.site)."""
    settings = context(request).settings.web
    return any(urlsplit(url).scheme == "https" for url in settings.all_urls)


def _remember(kind: str, telegram_id: int | None, challenge: bytes, origin: str, rp: str) -> str:
    now = time.time()
    for key in [key for key, item in _challenges.items() if item[5] < now]:
        del _challenges[key]
    if len(_challenges) >= MAX_CHALLENGES:
        _challenges.pop(next(iter(_challenges)))
    key = secrets.token_urlsafe(24)
    _challenges[key] = (kind, telegram_id, challenge, origin, rp, now + CHALLENGE_TTL)
    return key


def _take(key: str, kind: str, telegram_id: int | None, origin: str, rp: str) -> bytes | None:
    item = _challenges.pop(str(key or ""), None)
    if item is None or item[0] != kind or item[1] != telegram_id or item[5] < time.time():
        return None
    return item[2] if (item[3], item[4]) == (origin, rp) else None


async def _body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _options(key: str, options) -> web.Response:
    return web.json_response({"ok": True, "data": {"id": key, "options": json.loads(options_to_json(options))}})


# ─────────────────────────────── Вход ───────────────────────────────


@routes.post("/api/account/passkey/options")
async def login_options(request: web.Request) -> web.Response:
    """Без списка ключей: браузер сам покажет ключи этого сайта."""
    from .account import IP_FAILURES, _ip

    origin, rp = site(request)
    if IP_FAILURES.blocked(_ip(request)):
        return json_error("Слишком много попыток входа — подождите 15 минут", status=429)
    options = generate_authentication_options(rp_id=rp, user_verification=UserVerificationRequirement.REQUIRED)
    return _options(_remember("login", None, options.challenge, origin, rp), options)


@routes.post("/api/account/passkey/login")
async def login(request: web.Request) -> web.Response:
    from .account import IP_FAILURES, _ip, _signed_in

    origin, rp = site(request)
    ip = _ip(request)
    if IP_FAILURES.blocked(ip):
        return json_error("Слишком много попыток входа — подождите 15 минут", status=429)
    body = await _body(request)
    challenge = _take(body.get("id"), "login", None, origin, rp)
    if challenge is None:
        return json_error("Запрос ключа истёк — попробуйте ещё раз", status=403)
    db = context(request).db
    credential = body.get("credential")
    try:
        credential_id = base64url_to_bytes(credential["id"])
        async with db.transaction() as conn:
            cursor = await conn.execute(
                "SELECT telegram_id, public_key, sign_count FROM login_passkeys WHERE credential_id = ? AND rp_id = ?",
                (credential_id, rp))
            key = await cursor.fetchone()
            if key is None:
                raise LookupError
            result = verify_authentication_response(
                credential=credential, expected_challenge=challenge, expected_rp_id=rp, expected_origin=origin,
                credential_public_key=key["public_key"], credential_current_sign_count=key["sign_count"],
                require_user_verification=True)
            await conn.execute("UPDATE login_passkeys SET sign_count = ?, last_used = ? WHERE credential_id = ?",
                               (result.new_sign_count, time.time(), credential_id))
    except LookupError:
        IP_FAILURES.hit(ip)
        return json_error("Этот ключ входа удалён. Войдите другим способом и создайте новый в «Устройствах».",
                          status=403, reason="passkey-unknown")
    except (WebAuthnException, ValueError, TypeError, KeyError):
        IP_FAILURES.hit(ip)
        return json_error("Ключ входа не подтверждён — попробуйте ещё раз", status=403)
    return await _signed_in(request, int(key["telegram_id"]), "passkey")


# ─────────────────────────── Свои ключи ───────────────────────────


async def _list(request: web.Request) -> web.Response:
    rows = await context(request).db.fetch_all(
        """SELECT credential_id, rp_id, name, created_at, last_used FROM login_passkeys
           WHERE telegram_id = ? ORDER BY created_at""", (current_user(request).telegram_id,))
    return web.json_response({"ok": True, "data": {"items": [{
        "id": bytes_to_base64url(row["credential_id"]), "name": row["name"], "site": row["rp_id"],
        "created": row["created_at"], "last_used": row["last_used"],
    } for row in rows]}})


async def _changed(request: web.Request) -> None:
    from .sync import publish
    await publish(context(request), current_user(request).telegram_id, "devices")


@routes.get("/api/devices/passkeys")
async def passkeys(request: web.Request) -> web.Response:
    return await _list(request)


@routes.post("/api/devices/passkeys/options")
async def register_options(request: web.Request) -> web.Response:
    origin, rp = site(request)
    app, uid = context(request), current_user(request).telegram_id
    keys = await app.db.fetch_all("SELECT credential_id FROM login_passkeys WHERE telegram_id = ? AND rp_id = ?",
                                  (uid, rp))
    if len(keys) >= LIMIT:
        return json_error(f"Ключей входа уже {LIMIT} — удалите ненужные")
    number = await app.accounts.number(uid)
    label = f"СГОшка · ID {number}" if number else "СГОшка"
    options = generate_registration_options(
        rp_id=rp, rp_name="Школьный дневник",
        user_id=f"login-{number}".encode() if number else secrets.token_bytes(16),
        user_name=label, user_display_name=label,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=row["credential_id"]) for row in keys],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED))
    return _options(_remember("register", uid, options.challenge, origin, rp), options)


@routes.post("/api/devices/passkeys/register")
async def register(request: web.Request) -> web.Response:
    origin, rp = site(request)
    uid = current_user(request).telegram_id
    body = await _body(request)
    challenge = _take(body.get("id"), "register", uid, origin, rp)
    if challenge is None:
        return json_error("Запрос ключа истёк — попробуйте ещё раз", status=403)
    try:
        result = verify_registration_response(
            credential=body.get("credential"), expected_challenge=challenge, expected_rp_id=rp,
            expected_origin=origin, require_user_verification=True)
    except (WebAuthnException, ValueError, TypeError, KeyError):
        return json_error("Ключ входа не создан — попробуйте ещё раз", status=403)
    name = describe_browser(request.headers.get("User-Agent", ""))
    await context(request).db.execute(
        """INSERT INTO login_passkeys (credential_id, telegram_id, public_key, sign_count, rp_id, name, created_at)
           VALUES (?,?,?,?,?,?,?) ON CONFLICT (credential_id) DO NOTHING""",
        (result.credential_id, uid, result.credential_public_key, result.sign_count, rp, name, time.time()))
    await _changed(request)
    return await _list(request)


@routes.post("/api/devices/passkeys/delete")
async def delete(request: web.Request) -> web.Response:
    """Удалить ключ на сервере: в связке ключей он останется, но войти им
    уже нельзя — экран входа так и скажет."""
    body = await _body(request)
    try:
        credential_id = base64url_to_bytes(str(body.get("id") or ""))
    except ValueError:
        credential_id = b""
    async with context(request).db.transaction() as conn:
        cursor = await conn.execute("DELETE FROM login_passkeys WHERE credential_id = ? AND telegram_id = ?",
                                    (credential_id, current_user(request).telegram_id))
        deleted = cursor.rowcount
    if not deleted:
        return json_error("Ключ уже удалён", status=404)
    await _changed(request)
    return await _list(request)
