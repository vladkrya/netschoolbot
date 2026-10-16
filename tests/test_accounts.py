"""Вход без Telegram: почта и пароль, сброс, привязка Telegram, вход в СГО с сайта."""

import asyncio

import datetime as dt

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.accounts import WEB_ID_BASE, has_telegram
from app.accounts.service import link_telegram
from app.web import account as account_web
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user

BASE = "/mini"


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    for name in ("LOGIN_FAILURES", "IP_FAILURES", "REGISTRATIONS", "CODE_REQUESTS"):
        limiter = getattr(account_web, name)
        monkeypatch.setattr(account_web, name, account_web.Limiter(limiter.limit, limiter.window))


@pytest.fixture
async def client(app_context) -> TestClient:
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    yield client
    await client.close()


async def post(client, path, body, token=None):
    params = {"token": token} if token else {}
    response = await client.post(f"{BASE}{path}", json=body, params=params)
    return response.status, await response.json(), response


async def register(client, email="kid@example.com", password="длинный-пароль"):
    status, body, response = await post(client, "/api/account/register", {"email": email, "password": password})
    return status, body, response


class TestRegisterAndLogin:
    async def test_register_gives_token_for_web_user(self, client, app_context):
        status, body, response = await register(client)
        assert status == 200 and "netschool_token" in response.cookies
        token = body["data"]["token"]
        uid = await app_context.miniapp.resolve_token(token)
        assert uid > WEB_ID_BASE and not has_telegram(uid)
        info = await (await client.get(f"{BASE}/api/account", params={"token": token})).json()
        assert info["data"]["email"] == "kid@example.com"
        assert info["data"]["needs_sgo"] and not info["data"]["telegram"]
        # Второй с той же почтой — нельзя; плохой пароль — нельзя.
        assert (await register(client, email="KID@example.com"))[0] == 400
        assert (await register(client, email="other@example.com", password="123"))[0] == 400
        assert (await register(client, email="not-an-email"))[0] == 400

    async def test_login_and_rate_limit(self, client, app_context):
        _, body, _ = await register(client)
        uid = await app_context.miniapp.resolve_token(body["data"]["token"])
        status, data, _ = await post(client, "/api/account/login", {"email": "Kid@Example.com", "password": "длинный-пароль"})
        assert status == 200 and await app_context.miniapp.resolve_token(data["data"]["token"]) == uid
        assert (await post(client, "/api/account/login", {"email": "kid@example.com", "password": "нет"}))[0] == 401
        assert (await post(client, "/api/account/login", {"email": "nobody@example.com", "password": "нет"}))[0] == 401
        for _ in range(8):
            await post(client, "/api/account/login", {"email": "kid@example.com", "password": "нет"})
        status, _, _ = await post(client, "/api/account/login", {"email": "kid@example.com", "password": "длинный-пароль"})
        assert status == 429

    async def test_private_api_still_needs_login(self, client):
        response = await client.get(f"{BASE}/api/account")
        assert response.status == 401


class TestPasswords:
    async def test_reset_by_mail_revokes_old_tokens(self, client, app_context):
        app_context.mailer.outbox = []
        _, body, _ = await register(client)
        old_token = body["data"]["token"]
        assert "Код подтверждения" in app_context.mailer.outbox[0].get_content()
        status, data, _ = await post(client, "/api/account/reset/request", {"email": "kid@example.com"})
        assert status == 200 and data["data"]["channel"] == "mail"
        code = app_context.mailer.outbox[-1].get_content().split("сброса пароля: ")[1][:6]
        assert (await post(client, "/api/account/reset/confirm",
                           {"email": "kid@example.com", "code": "000000", "password": "новый-пароль-1"}))[0] == 400
        status, data, _ = await post(client, "/api/account/reset/confirm",
                                     {"email": "kid@example.com", "code": code, "password": "новый-пароль-1"})
        assert status == 200
        assert await app_context.miniapp.resolve_token(old_token) is None
        assert (await post(client, "/api/account/login", {"email": "kid@example.com", "password": "новый-пароль-1"}))[0] == 200
        # Код одноразовый.
        assert (await post(client, "/api/account/reset/confirm",
                           {"email": "kid@example.com", "code": code, "password": "ещё-пароль-2"}))[0] == 400

    async def test_unknown_email_reset_looks_the_same(self, client, app_context):
        app_context.mailer.outbox = []
        status, data, _ = await post(client, "/api/account/reset/request", {"email": "ghost@example.com"})
        assert status == 200 and app_context.mailer.outbox == []

    async def test_telegram_user_attaches_email(self, client, app_context, users):
        await users.save(make_user(5))
        token = await app_context.miniapp.issue_token(5)
        status, data, _ = await post(client, "/api/account/attach", {"email": "me@example.com", "password": "пароль-для-сайта"}, token)
        assert status == 200 and data["data"]["telegram"] and data["data"]["email"] == "me@example.com"
        status, data, _ = await post(client, "/api/account/login", {"email": "me@example.com", "password": "пароль-для-сайта"})
        assert await app_context.miniapp.resolve_token(data["data"]["token"]) == 5
        # Сменить почту — только с текущим паролем.
        assert (await post(client, "/api/account/attach", {"email": "x@example.com", "password": "пароль-для-сайта"}, token))[0] == 403
        assert (await post(client, "/api/account/password", {"current": "пароль-для-сайта", "password": "новый-пароль-9"}, token))[0] == 200


class TestTelegramLink:
    async def test_web_user_moves_to_telegram(self, client, app_context, users):
        _, body, _ = await register(client)
        token = body["data"]["token"]
        web_id = await app_context.miniapp.resolve_token(token)
        await post(client, "/api/prefs", {"theme": "dark"}, token)
        _, data, _ = await post(client, "/api/account/telegram-link", {}, token)
        code = data["data"]["code"]
        # Бот уже завёл пустую запись, когда человек написал /link.
        await users.get_or_create(777)
        answer = await link_telegram(app_context, code, 777)
        assert answer.startswith("✅")
        assert await users.get(web_id) is None
        assert (await app_context.accounts.by_email("kid@example.com"))["telegram_id"] == 777
        assert await app_context.miniapp.resolve_token(token) == 777
        assert (await app_context.miniapp.prefs(777)).get("theme") == "dark"
        assert "устарел" in await link_telegram(app_context, code, 777)

    async def test_telegram_with_own_diary_is_not_merged(self, client, app_context, users):
        _, body, _ = await register(client)
        _, data, _ = await post(client, "/api/account/telegram-link", {}, body["data"]["token"])
        await users.save(make_user(778))
        assert "уже вошёл" in await link_telegram(app_context, data["data"]["code"], 778)

    async def test_telegram_after_logout_keeps_own_data(self, client, app_context, users):
        """После /logout школы нет, но заметки остались — перенос стёр бы их."""
        _, body, _ = await register(client)
        _, data, _ = await post(client, "/api/account/telegram-link", {}, body["data"]["token"])
        await users.get_or_create(779)
        await app_context.notes.add(779, dt.date(2026, 10, 1), "Принести циркуль")
        assert "свои данные" in await link_telegram(app_context, data["data"]["code"], 779)
        assert len(await app_context.notes.between(779, dt.date(2026, 9, 1), dt.date(2026, 11, 1))) == 1

    async def test_bot_sends_nothing_to_web_users(self, app_context):
        from app.bot.notifier import TelegramNotifier

        sent = []

        class Bot:
            async def send_message(self, chat_id, text, **kwargs):
                sent.append(chat_id)

        notifier = TelegramNotifier(Bot(), app_context.users, None)
        await notifier.send_text(make_user(WEB_ID_BASE + 1), "привет")
        await notifier.send_text(make_user(9), "привет")
        assert sent == [9]


class TestSgoLoginFromSite:
    async def test_gosuslugi_code_is_asked_on_the_page(self, client, app_context, monkeypatch):
        _, body, _ = await register(client)
        token = body["data"]["token"]
        finished = []

        async def fake_password_login(app, user, otp):
            code = await otp("SMS", {"phone": "+7 *** 12", "code_length": 6})
            assert code == "123456"
            return '{"session": 1}'

        async def fake_finish(app, user, payload):
            finished.append((user.school.name, user.credentials.login_type.value, payload))
            return user, ""

        monkeypatch.setattr("app.netschool.login.password_login", fake_password_login)
        monkeypatch.setattr("app.netschool.login.finish_login", fake_finish)
        status, data, _ = await post(client, "/api/sgo/login", {
            "url": "https://sgo.example.ru", "school": "Школа №1", "method": "esia",
            "login": "+79990000000", "password": "секрет"}, token)
        assert status == 200
        for _ in range(20):
            state = (await (await client.get(f"{BASE}/api/sgo/login", params={"token": token})).json())["data"]
            if state["status"] == "otp":
                break
            await asyncio.sleep(0.01)
        assert state["otp"]["phone"] == "+7 *** 12"
        assert (await post(client, "/api/sgo/login/otp", {"code": "12-34-56"}, token))[0] == 200
        for _ in range(20):
            state = (await (await client.get(f"{BASE}/api/sgo/login", params={"token": token})).json())["data"]
            if state["status"] == "done":
                break
            await asyncio.sleep(0.01)
        assert state["status"] == "done"
        assert finished == [("Школа №1", "esia", '{"session": 1}')]

    async def test_bad_requests(self, client):
        _, body, _ = await register(client)
        token = body["data"]["token"]
        assert (await post(client, "/api/sgo/login", {"url": "http://insecure", "school": "x", "method": "password"}, token))[0] == 400
        assert (await post(client, "/api/sgo/login", {"url": "https://a.ru", "school": "x", "method": "password"}, token))[0] == 400
        assert (await post(client, "/api/sgo/login/otp", {"code": "1234"}, token))[0] == 400


async def test_forgotten_pin_is_reset_with_account_password(client, app_context):
    _, body, _ = await register(client)
    token = body["data"]["token"]
    status, _, _ = await post(client, "/api/lock/pin", {"pin": "4826"}, token)
    assert status == 200
    response = await client.get(f"{BASE}/api/account", params={"token": token})
    assert response.status == 423  # заблокировано PIN
    assert (await post(client, "/api/lock/password-reset", {"password": "не тот"}, token))[0] == 403
    assert (await post(client, "/api/lock/password-reset", {"password": "длинный-пароль"}, token))[0] == 200
    response = await client.get(f"{BASE}/api/account", params={"token": token})
    assert response.status == 200


async def test_paused_notifications_do_not_ask_to_log_in_again(client, app_context, users):
    from dataclasses import replace
    await users.save(replace(make_user(6), enabled=False))
    token = await app_context.miniapp.issue_token(6)
    info = await (await client.get(f"{BASE}/api/account", params={"token": token})).json()
    assert info["data"]["needs_sgo"] is False


async def test_lock_status_offers_password_reset_only_with_account(client, app_context, users):
    await users.save(make_user(7))
    token = await app_context.miniapp.issue_token(7)
    status = await (await client.get(f"{BASE}/api/lock/status", params={"token": token})).json()
    assert status["data"]["password_reset"] is False
    _, body, _ = await register(client)
    status = await (await client.get(f"{BASE}/api/lock/status", params={"token": body["data"]["token"]})).json()
    assert status["data"]["password_reset"] is True
