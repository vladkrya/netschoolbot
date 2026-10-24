"""Вход по QR-коду с экрана входа и по ключу входа (passkey)."""

from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.web import account as account_web, passkeys
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user

BASE = "/mini"
ORIGIN = {"Origin": "https://example.ru"}


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    for name in ("IP_FAILURES", "SITE_LOGINS"):
        limiter = getattr(account_web, name)
        monkeypatch.setattr(account_web, name, account_web.Limiter(limiter.limit, limiter.window))
    monkeypatch.setattr(passkeys, "_challenges", {})


@pytest.fixture
async def client(app_context) -> TestClient:
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    yield client
    await client.close()


async def signed_in(app_context, telegram_id=501):
    await app_context.users.save(make_user(telegram_id))
    return await app_context.miniapp.issue_token(telegram_id, device=True, method="email")


async def call(client, path, body=None, token=None, headers=None):
    params = {"token": token} if token else {}
    response = await client.post(f"{BASE}{path}", json=body or {}, params=params, headers=headers or {})
    return response.status, await response.json()


class TestQrLogin:
    async def test_scan_and_approve_signs_in_new_device(self, client, app_context):
        token = await signed_in(app_context)
        status, started = await call(client, "/api/account/qr-login", headers={"User-Agent": "Safari Mac OS X"})
        assert status == 200 and f"/mini/?qr={started['data']['code']}" in started["data"]["url"]
        code = started["data"]["code"]
        pending = await (await client.get(f"{BASE}/api/account/tg-login", params={"code": code})).json()
        assert pending["data"]["status"] == "pending"

        status, info = await call(client, "/api/devices/qr-login/info", {"code": code}, token)
        assert status == 200 and info["data"]["browser"] == "Safari на Mac"
        status, _ = await call(client, "/api/devices/qr-login/approve", {"code": code, "approve": True}, token)
        assert status == 200

        client.session.cookie_jar.clear()
        done = await (await client.get(f"{BASE}/api/account/tg-login", params={"code": code})).json()
        new_token = done["data"]["token"]
        assert new_token != token and await app_context.miniapp.resolve_token(new_token) == 501
        assert await app_context.miniapp.token_method(new_token) == "qr"
        # Второй раз тот же код ничего не даёт.
        assert (await call(client, "/api/devices/qr-login/approve", {"code": code, "approve": True}, token))[0] == 404

    async def test_declined_and_unknown(self, client, app_context):
        token = await signed_in(app_context)
        code = (await call(client, "/api/account/qr-login"))[1]["data"]["code"]
        await call(client, "/api/devices/qr-login/approve", {"code": code, "approve": False}, token)
        done = await (await client.get(f"{BASE}/api/account/tg-login", params={"code": code})).json()
        assert done["data"]["status"] == "declined"
        assert (await call(client, "/api/devices/qr-login/info", {"code": "nope"}, token))[0] == 404

    async def test_approve_needs_sign_in(self, client):
        code = (await call(client, "/api/account/qr-login"))[1]["data"]["code"]
        client.session.cookie_jar.clear()
        assert (await call(client, "/api/devices/qr-login/approve", {"code": code, "approve": True}))[0] == 401


def fake_credential(credential_id=b"key-1"):
    return {"id": passkeys.bytes_to_base64url(credential_id), "rawId": "", "type": "public-key", "response": {}}


class TestPasskeys:
    async def register(self, client, token, monkeypatch, credential_id=b"key-1"):
        status, options = await call(client, "/api/devices/passkeys/options", {}, token, ORIGIN)
        assert status == 200
        assert options["data"]["options"]["authenticatorSelection"]["residentKey"] == "required"
        assert options["data"]["options"]["user"]["id"] != ""
        monkeypatch.setattr(passkeys, "verify_registration_response", lambda **kwargs: SimpleNamespace(
            credential_id=credential_id, credential_public_key=b"pk", sign_count=0))
        return await call(client, "/api/devices/passkeys/register",
                          {"id": options["data"]["id"], "credential": fake_credential(credential_id)}, token,
                          {**ORIGIN, "User-Agent": "Safari Mac OS X"})

    async def test_create_and_sign_in(self, client, app_context, monkeypatch):
        token = await signed_in(app_context)
        status, listed = await self.register(client, token, monkeypatch)
        assert status == 200 and listed["data"]["items"][0]["name"] == "Safari на Mac"

        client.session.cookie_jar.clear()
        status, options = await call(client, "/api/account/passkey/options", {}, None, ORIGIN)
        assert status == 200 and not options["data"]["options"].get("allowCredentials")
        seen = {}

        def verify(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(new_sign_count=1)

        monkeypatch.setattr(passkeys, "verify_authentication_response", verify)
        status, body = await call(client, "/api/account/passkey/login",
                                  {"id": options["data"]["id"], "credential": fake_credential()}, None, ORIGIN)
        assert status == 200 and seen["credential_public_key"] == b"pk"
        assert await app_context.miniapp.resolve_token(body["data"]["token"]) == 501
        assert await app_context.miniapp.token_method(body["data"]["token"]) == "passkey"
        # Запрос ключа — на один раз.
        status, _ = await call(client, "/api/account/passkey/login",
                               {"id": options["data"]["id"], "credential": fake_credential()}, None, ORIGIN)
        assert status == 403

    async def test_deleted_key_says_so(self, client, app_context, monkeypatch):
        token = await signed_in(app_context)
        await self.register(client, token, monkeypatch)
        key_id = passkeys.bytes_to_base64url(b"key-1")
        status, listed = await call(client, "/api/devices/passkeys/delete", {"id": key_id}, token, ORIGIN)
        assert status == 200 and listed["data"]["items"] == []
        assert (await call(client, "/api/devices/passkeys/delete", {"id": key_id}, token, ORIGIN))[0] == 404
        options = (await call(client, "/api/account/passkey/options", {}, None, ORIGIN))[1]
        status, body = await call(client, "/api/account/passkey/login",
                                  {"id": options["data"]["id"], "credential": fake_credential()}, None, ORIGIN)
        assert status == 403 and body["reason"] == "passkey-unknown"

    async def test_other_users_key_is_not_deletable(self, client, app_context, monkeypatch):
        token = await signed_in(app_context)
        await self.register(client, token, monkeypatch)
        other = await signed_in(app_context, 502)
        key_id = passkeys.bytes_to_base64url(b"key-1")
        assert (await call(client, "/api/devices/passkeys/delete", {"id": key_id}, other, ORIGIN))[0] == 404

    async def test_foreign_origin_refused(self, client):
        status = (await client.post(f"{BASE}/api/account/passkey/options", json={},
                                    headers={"Origin": "https://evil.example"})).status
        assert status == 403

    async def test_config_reports_passkeys(self, client):
        config = await (await client.get(f"{BASE}/api/account/config")).json()
        assert config["data"]["passkey"] is True
