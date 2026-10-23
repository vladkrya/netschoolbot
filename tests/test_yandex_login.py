"""Вход через Яндекс ID: start → callback, новый и прежний аккаунт, привязка из настроек."""

from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app.web import account as account_web
from app.web.oauth_login import YANDEX, Identity, OAuthLogins, yandex_identity
from app.web.server import create_app
from app.web.telegram_auth import InitDataError
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура

CLIENT_ID = "ya-client"
BASE = "/mini"


@pytest.fixture
async def client(app_context, monkeypatch):
    app_context.settings = replace(app_context.settings, web=replace(
        app_context.settings.web, yandex_client_id=CLIENT_ID, yandex_client_secret="secret",
        yandex_hosts=("127.0.0.1",)))
    monkeypatch.setattr(account_web, "YANDEX_LOGINS", OAuthLogins(YANDEX))
    monkeypatch.setattr(account_web, "SITE_LOGINS", account_web.Limiter(50, 900))
    monkeypatch.setattr(account_web, "REGISTRATIONS", account_web.Limiter(50, 3600))
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    yield client
    await client.close()


def query_of(response) -> dict[str, str]:
    return {key: value[0] for key, value in parse_qs(urlsplit(response.headers["Location"]).query).items()}


async def sign_in(client, monkeypatch, subject="1130000012345678", name="Иван Петров", email=""):
    """Полный вход через Яндекс: вернуть ответ callback."""
    response = await client.get(f"{BASE}/api/account/yandex/start", allow_redirects=False)
    state = query_of(response)["state"]

    async def finish(item, code, client_id, secret):
        assert (code, client_id, secret) == ("the-code", CLIENT_ID, "secret")
        return Identity(subject=subject, name=name, email=email)

    monkeypatch.setattr(account_web.YANDEX_LOGINS, "finish", finish)
    return await client.get(f"{BASE}/api/account/yandex/callback",
                            params={"code": "the-code", "state": state}, allow_redirects=False)


class TestIdentity:
    def test_email_is_normalized(self):
        identity = yandex_identity({"id": "123", "client_id": CLIENT_ID,
                                    "default_email": " Ivan@Yandex.ru "}, CLIENT_ID)
        assert identity.email == "ivan@yandex.ru"

    @pytest.mark.parametrize("email", [None, [], "broken", "bad email@yandex.ru"])
    def test_bad_email_does_not_break_login(self, email):
        assert yandex_identity({"id": "123", "client_id": CLIENT_ID,
                                "default_email": email}, CLIENT_ID).email == ""

    def test_info_to_identity(self):
        identity = yandex_identity({"id": "123", "client_id": CLIENT_ID, "login": "ivan",
                                    "display_name": "Иван"}, CLIENT_ID)
        assert (identity.subject, identity.name) == ("123", "Иван")

    def test_name_falls_back_to_login(self):
        assert yandex_identity({"id": "1", "client_id": CLIENT_ID, "login": "ivan"}, CLIENT_ID).name == "ivan"

    @pytest.mark.parametrize("info", [
        {"id": "123", "client_id": "other"}, {"client_id": CLIENT_ID}, {"id": "abc", "client_id": CLIENT_ID},
    ])
    def test_bad_info_rejected(self, info):
        with pytest.raises(InitDataError):
            yandex_identity(info, CLIENT_ID)


class TestFlow:
    async def test_email_is_suggested_without_taking_existing_account(self, client, app_context, monkeypatch):
        owner_id = await app_context.accounts.create_web_user("ivan@yandex.ru", "existing-hash")
        response = await sign_in(client, monkeypatch, email="ivan@yandex.ru")
        token = response.cookies["netschool_token"].value
        user_id = await app_context.miniapp.resolve_token(token)
        assert user_id != owner_id
        info = (await (await client.get(f"{BASE}/api/account", params={"token": token})).json())["data"]
        assert info["suggested_email"] == "ivan@yandex.ru" and info["email"] is None
        assert await app_context.accounts.by_user(user_id) is None
        assert (await app_context.accounts.by_email("ivan@yandex.ru"))["telegram_id"] == owner_id
        await sign_in(client, monkeypatch, email="new@yandex.ru")
        assert await app_context.accounts.oauth_email(user_id) == "new@yandex.ru"
        await app_context.accounts.unlink_oauth(user_id, "yandex")
        assert await app_context.accounts.oauth_email(user_id) == ""

    async def test_start_redirects_with_pkce(self, client):
        config = await (await client.get(f"{BASE}/api/account/config")).json()
        assert config["data"]["yandex"] is True
        response = await client.get(f"{BASE}/api/account/yandex/start", allow_redirects=False)
        assert response.status == 302
        url, query = urlsplit(response.headers["Location"]), query_of(response)
        assert url.netloc == "oauth.yandex.ru" and query["client_id"] == CLIENT_ID
        assert query["code_challenge_method"] == "S256" and query["scope"] == "login:info login:email"
        assert query["redirect_uri"].endswith(f"{BASE}/api/account/yandex/callback")
        assert response.cookies["yandex_state"].value == query["state"]

    async def test_new_user_then_same_user(self, client, app_context, monkeypatch):
        response = await sign_in(client, monkeypatch)
        assert response.status == 302 and response.headers["Location"] == f"{BASE}/?refresh=1"
        user_id = await app_context.miniapp.resolve_token(response.cookies["netschool_token"].value)
        assert user_id >= 8_000_000_000_000_000
        assert (await app_context.users.get(user_id)).display_name == "Иван Петров"
        # Второй вход — тот же аккаунт, а не новый.
        again = await sign_in(client, monkeypatch)
        assert await app_context.miniapp.resolve_token(again.cookies["netschool_token"].value) == user_id
        info = (await (await client.get(f"{BASE}/api/account",
                                        params={"token": again.cookies["netschool_token"].value})).json())["data"]
        assert info["yandex"] == {"enabled": True, "linked": True} and info["email"] is None

    async def test_state_is_one_time_and_bound_to_browser(self, client, monkeypatch):
        response = await sign_in(client, monkeypatch)
        assert "netschool_token" in response.cookies
        state, _ = account_web.YANDEX_LOGINS.start(CLIENT_ID, "http://127.0.0.1/cb")
        client.session.cookie_jar.clear()
        response = await client.get(f"{BASE}/api/account/yandex/callback",
                                    params={"code": "c", "state": state}, allow_redirects=False)
        assert response.headers["Location"].endswith("#yaLoginError=expired")
        assert "netschool_token" not in response.cookies

    async def test_cancel_and_failure(self, client, monkeypatch):
        response = await client.get(f"{BASE}/api/account/yandex/callback",
                                    params={"error": "access_denied", "state": "x"}, allow_redirects=False)
        assert response.headers["Location"].endswith("#yaLoginError=cancel")
        response = await client.get(f"{BASE}/api/account/yandex/start", allow_redirects=False)
        state = query_of(response)["state"]

        async def finish(*args):
            raise InitDataError("Токен Яндекса выдан другому приложению")

        monkeypatch.setattr(account_web.YANDEX_LOGINS, "finish", finish)
        response = await client.get(f"{BASE}/api/account/yandex/callback",
                                    params={"code": "c", "state": state}, allow_redirects=False)
        assert response.headers["Location"].endswith("#yaLoginError=failed")
        assert "netschool_token" not in response.cookies

    async def test_off_without_keys_or_on_other_domain(self, client, app_context):
        app_context.settings = replace(app_context.settings, web=replace(
            app_context.settings.web, yandex_hosts=("ns.example.ru",)))
        config = await (await client.get(f"{BASE}/api/account/config")).json()
        assert config["data"]["yandex"] is False
        response = await client.get(f"{BASE}/api/account/yandex/start", allow_redirects=False)
        assert response.headers["Location"].endswith("#yaLoginError=off")
        app_context.settings = replace(app_context.settings, web=replace(
            app_context.settings.web, yandex_hosts=("127.0.0.1",), yandex_client_secret=""))
        assert (await (await client.get(f"{BASE}/api/account/config")).json())["data"]["yandex"] is False


class TestLink:
    async def register(self, client, app_context, email="kid@example.com"):
        response = await client.post(f"{BASE}/api/account/register",
                                     json={"email": email, "password": "длинный-пароль"})
        token = (await response.json())["data"]["token"]
        return token, await app_context.miniapp.resolve_token(token)

    async def link(self, client, monkeypatch, token, subject):
        response = await client.get(f"{BASE}/api/account/yandex/link", params={"token": token},
                                    allow_redirects=False)
        state = query_of(response)["state"]

        async def finish(item, code, client_id, secret):
            return Identity(subject=subject, name="Вася")

        monkeypatch.setattr(account_web.YANDEX_LOGINS, "finish", finish)
        return await client.get(f"{BASE}/api/account/yandex/callback",
                                params={"code": "c", "state": state}, allow_redirects=False)

    async def test_link_then_login_opens_same_account(self, client, app_context, monkeypatch):
        token, web_id = await self.register(client, app_context)
        response = await self.link(client, monkeypatch, token, "777")
        assert response.headers["Location"] == f"{BASE}/#yalink=ok"
        assert "netschool_token" not in response.cookies
        response = await sign_in(client, monkeypatch, subject="777")
        assert await app_context.miniapp.resolve_token(response.cookies["netschool_token"].value) == web_id

    async def test_taken_by_other_account(self, client, app_context, monkeypatch):
        await sign_in(client, monkeypatch, subject="888")
        token, web_id = await self.register(client, app_context)
        response = await self.link(client, monkeypatch, token, "888")
        assert response.headers["Location"] == f"{BASE}/#yalink=taken"
        assert await app_context.accounts.oauth_providers(web_id) == set()

    async def test_unlink_keeps_last_way_in(self, client, app_context, monkeypatch):
        response = await sign_in(client, monkeypatch, subject="999")
        only = response.cookies["netschool_token"].value
        response = await client.post(f"{BASE}/api/account/yandex/unlink", params={"token": only})
        assert response.status == 400
        token, web_id = await self.register(client, app_context, email="other@example.com")
        await self.link(client, monkeypatch, token, "555")
        response = await client.post(f"{BASE}/api/account/yandex/unlink", params={"token": token})
        assert (await response.json())["data"]["yandex"]["linked"] is False
        assert await app_context.accounts.oauth_user("yandex", "555") is None

    async def test_delete_without_password_when_only_yandex(self, client, app_context, monkeypatch):
        response = await sign_in(client, monkeypatch, subject="321")
        token = response.cookies["netschool_token"].value
        user_id = await app_context.miniapp.resolve_token(token)
        response = await client.post(f"{BASE}/api/account/delete", params={"token": token}, json={})
        assert response.status == 200
        assert await app_context.users.get(user_id) is None
        assert await app_context.accounts.oauth_user("yandex", "321") is None


class TestTelegramLinkKeepsYandex:
    async def test_yandex_moves_to_telegram(self, client, app_context, monkeypatch):
        """Аккаунт без почты, вошедший через Яндекс, привязывает Telegram — вход
        через Яндекс ведёт уже в аккаунт Telegram."""
        from app.accounts.service import link_account
        response = await sign_in(client, monkeypatch, subject="4444")
        web_id = await app_context.miniapp.resolve_token(response.cookies["netschool_token"].value)
        assert await link_account(app_context, web_id, 4260) is None
        assert await app_context.accounts.oauth_user("yandex", "4444") == 4260


async def test_finish_exchanges_code_and_reads_profile(monkeypatch):
    seen = {}

    async def token(request):
        seen.update(await request.post())
        return web.json_response({"access_token": "at", "token_type": "bearer"})

    async def info(request):
        seen["auth"] = request.headers.get("Authorization")
        return web.json_response({"id": "42", "client_id": CLIENT_ID, "login": "ivan", "display_name": "Иван"})

    fake = web.Application()
    fake.router.add_post("/token", token)
    fake.router.add_get("/info", info)
    server = TestServer(fake)
    await server.start_server()
    try:
        provider = replace(YANDEX, token_url=str(server.make_url("/token")), info_url=str(server.make_url("/info")))
        logins = OAuthLogins(provider)
        state, _ = logins.start(CLIENT_ID, "https://ns.example.ru/cb")
        item = logins.take(state)
        identity = await logins.finish(item, "the-code", CLIENT_ID, "secret")
        assert (identity.subject, identity.name) == ("42", "Иван")
        assert seen["code"] == "the-code" and seen["code_verifier"] == item.verifier
        assert seen["client_secret"] == "secret" and seen["auth"] == "OAuth at"
    finally:
        await server.close()
