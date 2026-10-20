"""Вход через Telegram по OpenID Connect: проверка id_token и путь start → callback."""

import base64
import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app.web import account as account_web
from app.web.server import create_app
from app.web.telegram_auth import InitDataError, TelegramUser
from app.web.telegram_oidc import OidcLogins, user_from_claims, verify_id_token
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура

CLIENT_ID = "7779472554"
BASE = "/mini"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def claims(**changes):
    now = int(time.time())
    return {"iss": "https://oauth.telegram.org", "aud": CLIENT_ID, "sub": "x", "iat": now, "exp": now + 3600,
            "id": 42, "name": "Иван Петров", "given_name": "Иван", "family_name": "Петров",
            "preferred_username": "ivan", "nonce": "n", **changes}


def rsa_token(key, payload, kid="k1"):
    head = b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
    body = b64(json.dumps(payload).encode())
    signature = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{b64(signature)}"


def rsa_jwk(key, kid="k1"):
    numbers = key.public_key().public_numbers()
    to_bytes = lambda n: n.to_bytes((n.bit_length() + 7) // 8, "big")  # noqa: E731
    return {"kty": "RSA", "kid": kid, "n": b64(to_bytes(numbers.n)), "e": b64(to_bytes(numbers.e))}


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class TestIdToken:
    def test_valid_rs256(self, rsa_key):
        data = verify_id_token(rsa_token(rsa_key, claims()), rsa_jwk(rsa_key), CLIENT_ID)
        user = user_from_claims(data)
        assert (user.id, user.full_name, user.username) == (42, "Иван Петров", "ivan")

    def test_valid_es256(self):
        key = ec.generate_private_key(ec.SECP256R1())
        head = b64(json.dumps({"alg": "ES256", "kid": "e"}).encode())
        body = b64(json.dumps(claims()).encode())
        r, s = decode_dss_signature(key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
        numbers = key.public_key().public_numbers()
        jwk = {"kty": "EC", "crv": "P-256", "x": b64(numbers.x.to_bytes(32, "big")), "y": b64(numbers.y.to_bytes(32, "big"))}
        token = f"{head}.{body}.{b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        assert verify_id_token(token, jwk, CLIENT_ID)["id"] == 42

    @pytest.mark.parametrize("change", [
        {"aud": "123"}, {"iss": "https://evil.example"}, {"exp": int(time.time()) - 3600},
    ])
    def test_wrong_claims_rejected(self, rsa_key, change):
        with pytest.raises(InitDataError):
            verify_id_token(rsa_token(rsa_key, claims(**change)), rsa_jwk(rsa_key), CLIENT_ID)

    def test_tampered_payload_rejected(self, rsa_key):
        head, _, signature = rsa_token(rsa_key, claims()).split(".")
        forged = b64(json.dumps(claims(id=1)).encode())
        with pytest.raises(InitDataError):
            verify_id_token(f"{head}.{forged}.{signature}", rsa_jwk(rsa_key), CLIENT_ID)

    def test_other_key_rejected(self, rsa_key):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(InitDataError):
            verify_id_token(rsa_token(other, claims()), rsa_jwk(rsa_key), CLIENT_ID)

    def test_alg_none_rejected(self, rsa_key):
        head = b64(json.dumps({"alg": "none"}).encode())
        with pytest.raises(InitDataError):
            verify_id_token(f"{head}.{b64(json.dumps(claims()).encode())}.", rsa_jwk(rsa_key), CLIENT_ID)


@pytest.fixture
async def client(app_context, monkeypatch):
    app_context.settings = replace(app_context.settings, web=replace(
        app_context.settings.web, tg_oidc_client_id=CLIENT_ID, tg_oidc_client_secret="secret",
        tg_oidc_hosts=("127.0.0.1",)))
    monkeypatch.setattr(account_web, "OIDC", OidcLogins())
    monkeypatch.setattr(account_web, "SITE_LOGINS", account_web.Limiter(10, 900))
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    yield client
    await client.close()


class TestFlow:
    async def test_start_then_callback_logs_in(self, client, app_context, monkeypatch):
        config = await (await client.get(f"{BASE}/api/account/config")).json()
        assert config["data"]["oidc"] is True

        response = await client.get(f"{BASE}/api/account/tg-oidc/start", allow_redirects=False)
        assert response.status == 302
        url = urlsplit(response.headers["Location"])
        query = {key: value[0] for key, value in parse_qs(url.query).items()}
        assert url.netloc == "oauth.telegram.org" and query["client_id"] == CLIENT_ID
        assert query["code_challenge_method"] == "S256" and query["scope"] == "openid profile"
        assert query["redirect_uri"].endswith(f"{BASE}/api/account/tg-oidc/callback")
        state = query["state"]

        async def finish(item, code, client_id, secret):
            assert (code, client_id, secret) == ("the-code", CLIENT_ID, "secret")
            assert item.redirect_uri == query["redirect_uri"]
            return TelegramUser(id=4246, first_name="Вася")

        monkeypatch.setattr(account_web.OIDC, "finish", finish)
        response = await client.get(f"{BASE}/api/account/tg-oidc/callback",
                                    params={"code": "the-code", "state": state}, allow_redirects=False)
        assert response.status == 302 and response.headers["Location"] == f"{BASE}/?refresh=1"
        token = response.cookies["netschool_token"].value
        assert await app_context.miniapp.resolve_token(token) == 4246
        # state одноразовый.
        response = await client.get(f"{BASE}/api/account/tg-oidc/callback",
                                    params={"code": "the-code", "state": state}, allow_redirects=False)
        assert response.headers["Location"].endswith("#tgLoginError=expired")

    async def test_state_from_another_browser_rejected(self, client, app_context, monkeypatch):
        state, _ = account_web.OIDC.start(CLIENT_ID, "http://127.0.0.1/cb")

        async def finish(*args):
            raise AssertionError("без своего cookie код менять нельзя")

        monkeypatch.setattr(account_web.OIDC, "finish", finish)
        response = await client.get(f"{BASE}/api/account/tg-oidc/callback",
                                    params={"code": "c", "state": state}, allow_redirects=False)
        assert response.headers["Location"].endswith("#tgLoginError=expired")
        assert "netschool_token" not in response.cookies

    async def test_cancel_and_failure(self, client, monkeypatch):
        response = await client.get(f"{BASE}/api/account/tg-oidc/callback",
                                    params={"error": "access_denied", "state": "x"}, allow_redirects=False)
        assert response.headers["Location"].endswith("#tgLoginError=cancel")

        response = await client.get(f"{BASE}/api/account/tg-oidc/start", allow_redirects=False)
        state = parse_qs(urlsplit(response.headers["Location"]).query)["state"][0]

        async def finish(*args):
            raise InitDataError("Подпись id_token не совпала")

        monkeypatch.setattr(account_web.OIDC, "finish", finish)
        response = await client.get(f"{BASE}/api/account/tg-oidc/callback",
                                    params={"code": "c", "state": state}, allow_redirects=False)
        assert response.headers["Location"].endswith("#tgLoginError=failed")
        assert "netschool_token" not in response.cookies

    async def test_off_on_other_domains(self, client, app_context):
        app_context.settings = replace(app_context.settings, web=replace(
            app_context.settings.web, tg_oidc_hosts=("ns.example.ru",)))
        config = await (await client.get(f"{BASE}/api/account/config")).json()
        assert config["data"]["oidc"] is False
        response = await client.get(f"{BASE}/api/account/tg-oidc/start", allow_redirects=False)
        assert response.headers["Location"].endswith("#tgLoginError=off")


async def test_finish_exchanges_code_and_checks_nonce(rsa_key, monkeypatch):
    from aiohttp import web
    from app.web import telegram_oidc

    seen = {}

    async def token(request):
        form = await request.post()
        nonce = seen["nonce"]
        seen.update(form, auth=request.headers.get("Authorization"))
        return web.json_response({"id_token": rsa_token(rsa_key, claims(nonce=nonce))})

    async def jwks(request):
        return web.json_response({"keys": [rsa_jwk(rsa_key)]})

    fake = web.Application()
    fake.router.add_post("/token", token)
    fake.router.add_get("/jwks", jwks)
    server = TestServer(fake)
    await server.start_server()
    try:
        monkeypatch.setattr(telegram_oidc, "TOKEN_URL", str(server.make_url("/token")))
        monkeypatch.setattr(telegram_oidc, "JWKS_URL", str(server.make_url("/jwks")))
        logins = OidcLogins()
        state, url = logins.start(CLIENT_ID, "https://ns.example.ru/cb")
        item = logins.take(state)
        seen["nonce"] = item.nonce
        user = await logins.finish(item, "the-code", CLIENT_ID, "secret")
        assert user.id == 42
        assert seen["code"] == "the-code" and seen["code_verifier"] == item.verifier
        assert seen["redirect_uri"] == "https://ns.example.ru/cb"
        assert seen["auth"] == "Basic " + base64.b64encode(f"{CLIENT_ID}:secret".encode()).decode()
        # Чужой nonce — токен от другого входа.
        seen["nonce"] = "other"
        with pytest.raises(InitDataError):
            await logins.finish(item, "the-code", CLIENT_ID, "secret")
    finally:
        await server.close()
