"""Вход без Telegram: почта и пароль, сброс, привязка Telegram, вход в СГО с сайта."""

import asyncio

import datetime as dt
from dataclasses import replace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.accounts import WEB_ID_BASE, has_telegram
from app.accounts.service import link_telegram
from app.web import account as account_web
from app.web.server import create_app
from tests.test_telegram_auth import make_widget_data
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user

BASE = "/mini"
WIDGET_TOKEN = "777:widget-test-token"


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

    async def test_login_again_replaces_this_browser_token(self, client, app_context):
        _, body, _ = await register(client)
        old = body["data"]["token"]
        _, data, _ = await post(client, "/api/account/login", {"email": "kid@example.com", "password": "длинный-пароль"})
        assert data["data"]["token"] != old
        assert await app_context.miniapp.resolve_token(old) is None
        uid = await app_context.miniapp.resolve_token(data["data"]["token"])
        assert len([row for row in await app_context.miniapp.sessions(uid) if row["device"]]) == 1

    async def test_private_api_still_needs_login(self, client):
        response = await client.get(f"{BASE}/api/account")
        assert response.status == 401


class TestEmailVerification:
    async def test_new_account_must_verify_before_sgo_login(self, client, app_context):
        app_context.mailer.outbox = []
        _, body, _ = await register(client)
        token = body["data"]["token"]
        info = (await (await client.get(f"{BASE}/api/account", params={"token": token})).json())["data"]
        assert info["needs_verify"] and not info["verified"]
        status, data, _ = await post(client, "/api/sgo/login", {
            "url": "https://sgo.example.ru", "school": "Школа №1", "method": "password",
            "login": "kid", "password": "секрет"}, token)
        assert status == 403 and data["reason"] == "verify"
        code = app_context.mailer.outbox[0].get_body(("plain",)).get_content().split("Код подтверждения:")[1].split()[0]
        status, data, _ = await post(client, "/api/account/verify", {"code": code}, token)
        assert status == 200 and data["data"]["verified"] and not data["data"]["needs_verify"]

    async def test_no_mail_no_gate(self, client):
        _, body, _ = await register(client)
        info = (await (await client.get(f"{BASE}/api/account", params={"token": body["data"]["token"]})).json())["data"]
        assert info["needs_verify"] is False


class TestPasswords:
    async def test_reset_by_mail_link_revokes_old_tokens(self, client, app_context):
        app_context.mailer.outbox = []
        _, body, _ = await register(client)
        old_token = body["data"]["token"]
        assert "Код подтверждения" in app_context.mailer.outbox[0].get_body(("plain",)).get_content()
        status, data, _ = await post(client, "/api/account/reset/request", {"email": "kid@example.com"})
        assert status == 200 and data["data"]["channel"] == "mail"
        letter = app_context.mailer.outbox[-1]
        text = letter.get_body(("plain",)).get_content()
        link = next(word for word in text.split() if "#reset=" in word)
        assert link.startswith(app_context.settings.web.miniapp_url + "/#reset=")
        assert "Задать новый пароль" in letter.get_body(("html",)).get_content()
        secret = link.split("#reset=")[1]
        check = await client.get(f"{BASE}/api/account/reset/check", params={"token": secret})
        assert (await check.json())["data"]["email"] == "kid@example.com"
        assert (await client.get(f"{BASE}/api/account/reset/check", params={"token": "x" * 43})).status == 400
        assert (await post(client, "/api/account/reset/confirm",
                           {"token": "x" * 43, "password": "новый-пароль-1"}))[0] == 400
        # Слабый пароль не гасит ссылку.
        assert (await post(client, "/api/account/reset/confirm", {"token": secret, "password": "1"}))[0] == 400
        status, data, _ = await post(client, "/api/account/reset/confirm",
                                     {"token": secret, "password": "новый-пароль-1"})
        assert status == 200
        assert await app_context.miniapp.resolve_token(old_token) is None
        assert (await post(client, "/api/account/login", {"email": "kid@example.com", "password": "новый-пароль-1"}))[0] == 200
        # Ссылка одноразовая.
        assert (await post(client, "/api/account/reset/confirm",
                           {"token": secret, "password": "ещё-пароль-2"}))[0] == 400

    async def test_reset_link_expires_and_new_one_replaces_old(self, client, app_context):
        await register(client)
        first = await app_context.accounts.issue_reset_link("kid@example.com")
        second = await app_context.accounts.issue_reset_link("kid@example.com")
        assert await app_context.accounts.reset_link_email(first) is None
        assert await app_context.accounts.reset_link_email(second) == "kid@example.com"
        await app_context.db.execute("UPDATE account_codes SET expires_at = 0")
        assert (await post(client, "/api/account/reset/confirm",
                           {"token": second, "password": "новый-пароль-1"}))[0] == 400

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
        app_context.mailer.outbox = []
        answer = await link_telegram(app_context, code, 777)
        assert answer.startswith("✅")
        # Владельцу почты — письмо о привязке, в двух видах.
        (letter,) = app_context.mailer.outbox
        assert letter["To"] == "kid@example.com" and "Telegram привязан" in letter["Subject"]
        assert letter.get_body(("html",)) is not None
        assert await users.get(web_id) is None
        assert (await app_context.accounts.by_email("kid@example.com"))["telegram_id"] == 777
        assert await app_context.miniapp.resolve_token(token) == 777
        assert (await app_context.miniapp.prefs(777)).get("theme") == "dark"
        assert "устарел" in await link_telegram(app_context, code, 777)

    async def test_telegram_with_own_diary_keeps_diary_when_linked(self, client, app_context, users):
        _, body, _ = await register(client)
        _, data, _ = await post(client, "/api/account/telegram-link", {}, body["data"]["token"])
        await users.save(make_user(778))
        assert (await link_telegram(app_context, data["data"]["code"], 778)).startswith("✅")
        assert (await users.get(778)).school.configured
        assert (await app_context.accounts.by_email("kid@example.com"))["telegram_id"] == 778

    async def test_telegram_after_logout_keeps_own_data(self, client, app_context, users):
        """После /logout школы нет, но заметки остались — перенос стёр бы их."""
        _, body, _ = await register(client)
        _, data, _ = await post(client, "/api/account/telegram-link", {}, body["data"]["token"])
        await users.get_or_create(779)
        await app_context.notes.add(779, dt.date(2026, 10, 1), "Принести циркуль")
        assert (await link_telegram(app_context, data["data"]["code"], 779)).startswith("✅")
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


def test_smtp_ssl_checks_certificate_against_tls_name(monkeypatch):
    # Через туннель подключаемся к 127.0.0.1, а сертификат сверяем с mail.ikrya.ru.
    import smtplib

    from app.accounts.mailer import _SMTPSSL

    seen = {}

    def fake_get_socket(self, host, port, timeout):
        seen.update(connect=host, name=self._host)
        raise OSError("стоп")

    monkeypatch.setattr(smtplib.SMTP_SSL, "_get_socket", fake_get_socket)
    with pytest.raises(OSError):
        _SMTPSSL("127.0.0.1", 2465, tls_name="mail.ikrya.ru", timeout=1)
    assert seen == {"connect": "127.0.0.1", "name": "mail.ikrya.ru"}


class FakeBot:
    async def get_me(self):
        from types import SimpleNamespace
        return SimpleNamespace(username="netschoolbot")


@pytest.fixture
async def bot_client(app_context, monkeypatch) -> TestClient:
    monkeypatch.setattr(account_web, "_bot_username", "")
    client = TestClient(TestServer(create_app(app_context, FakeBot())))
    await client.start_server()
    yield client
    await client.close()


class TestSiteLoginThroughBot:
    async def test_login_needs_confirmation_in_bot(self, bot_client, app_context, users):
        await users.save(make_user(telegram_id=4242))
        status, body, _ = await post(bot_client, "/api/account/tg-login", {})
        assert status == 200
        code = body["data"]["code"]
        assert body["data"]["url"] == f"https://t.me/netschoolbot?start=web_{code}"

        async def poll():
            response = await bot_client.get(f"{BASE}/api/account/tg-login", params={"code": code})
            return await response.json(), response

        # Пока в боте не нажали «Войти» — ждём, токена нет.
        data, _ = await poll()
        assert data["data"] == {"status": "pending"}
        assert app_context.site_logins.decide(code, 4242, approve=True) is not None
        # Решённый запрос второй раз не решается — ни тем же, ни другим человеком.
        assert app_context.site_logins.decide(code, 5555, approve=True) is None
        data, response = await poll()
        token = data["data"]["token"]
        assert await app_context.miniapp.resolve_token(token) == 4242
        assert response.cookies["netschool_token"].value == token
        # Код одноразовый.
        assert (await poll())[0]["data"] == {"status": "expired"}

    async def test_declined_login_gives_nothing(self, bot_client, app_context):
        _, body, _ = await post(bot_client, "/api/account/tg-login", {})
        code = body["data"]["code"]
        app_context.site_logins.decide(code, 4242, approve=False)
        response = await bot_client.get(f"{BASE}/api/account/tg-login", params={"code": code})
        assert (await response.json())["data"] == {"status": "declined"}

    async def test_unknown_code_is_expired(self, bot_client):
        response = await bot_client.get(f"{BASE}/api/account/tg-login", params={"code": "nope"})
        assert (await response.json())["data"] == {"status": "expired"}


class TestSiteLoginThroughWidget:
    @pytest.fixture
    def widget_on(self, app_context):
        app_context.settings = replace(
            app_context.settings,
            telegram=replace(app_context.settings.telegram, bot_token=WIDGET_TOKEN),
            web=replace(app_context.settings.web, login_widget_domain="127.0.0.1"),
        )

    async def test_config_gives_bot_id_only_on_widget_domain(self, client, app_context, widget_on):
        config = await (await client.get(f"{BASE}/api/account/config")).json()
        assert config["data"]["widget"] == 777
        app_context.settings = replace(
            app_context.settings, web=replace(app_context.settings.web, login_widget_domain="ns.example.ru"))
        config = await (await client.get(f"{BASE}/api/account/config")).json()
        assert config["data"]["widget"] == 0

    async def test_signed_data_logs_in_and_creates_user(self, client, app_context, widget_on):
        status, body, response = await post(client, "/api/account/tg-widget", make_widget_data(WIDGET_TOKEN, id="4243"))
        assert status == 200
        token = body["data"]["token"]
        assert await app_context.miniapp.resolve_token(token) == 4243
        assert response.cookies["netschool_token"].value == token
        assert (await app_context.users.get(4243)).display_name == "Иван"

    async def test_widget_can_live_on_another_bot(self, client, app_context, widget_on):
        other = "888:reserve-bot-token"
        app_context.settings = replace(
            app_context.settings, web=replace(app_context.settings.web, login_widget_bot_token=other))
        config = await (await client.get(f"{BASE}/api/account/config")).json()
        assert config["data"]["widget"] == 888
        # Подпись — от того бота, к которому привязан домен, а не от основного.
        assert (await post(client, "/api/account/tg-widget", make_widget_data(WIDGET_TOKEN)))[0] == 401
        status, body, _ = await post(client, "/api/account/tg-widget", make_widget_data(other, id="4244"))
        assert status == 200 and await app_context.miniapp.resolve_token(body["data"]["token"]) == 4244

    async def test_admin_switches_widget_bot(self, client, app_context, widget_on):
        from types import SimpleNamespace
        from aiogram.filters import CommandObject
        from app.bot.handlers.admin import tg_widget
        from tests.test_bot_handlers import FakeMessage

        app_context.settings = replace(
            app_context.settings, web=replace(app_context.settings.web, login_widget_bot_token="888:reserve"))

        async def admin(args, user=1):
            message = FakeMessage(f"/tgwidget {args}")
            message.from_user = SimpleNamespace(id=user)
            await tg_widget(message, CommandObject(command="tgwidget", args=args or None), app_context)
            return message.answers[0][0] if message.answers else ""

        async def widget():
            return (await (await client.get(f"{BASE}/api/account/config")).json())["data"]["widget"]

        assert await widget() == 888 and "резервный" in await admin("")
        assert await admin("main", user=2) == "" and await widget() == 888  # не админ — молчим
        assert "основной" in await admin("main") and await widget() == 777
        status, _, _ = await post(client, "/api/account/tg-widget", make_widget_data(WIDGET_TOKEN, id="4245"))
        assert status == 200
        assert "выключен" in await admin("off") and await widget() == 0
        assert (await post(client, "/api/account/tg-widget", make_widget_data(WIDGET_TOKEN)))[0] == 401
        assert "резервный" in await admin("reserve") and await widget() == 888

    async def test_forged_data_rejected(self, client, app_context, widget_on):
        data = {**make_widget_data(WIDGET_TOKEN), "id": "1"}
        status, body, response = await post(client, "/api/account/tg-widget", data)
        assert status == 401 and "netschool_token" not in response.cookies
        assert await app_context.users.get(1) is None


class TestWidgetLink:
    """Привязка Telegram виджетом: те же бот и переключатель /tgwidget, что у входа."""

    @pytest.fixture
    def widget_on(self, app_context):
        app_context.settings = replace(
            app_context.settings,
            telegram=replace(app_context.settings.telegram, bot_token=WIDGET_TOKEN),
            web=replace(app_context.settings.web, login_widget_domain="127.0.0.1"),
        )

    async def test_signed_data_links_telegram(self, client, app_context, widget_on):
        _, body, _ = await register(client)
        token = body["data"]["token"]
        info = (await (await client.get(f"{BASE}/api/account", params={"token": token})).json())["data"]
        assert info["tg_link"] == {"oidc": False, "widget": 777}
        assert (await post(client, "/api/account/telegram-link/widget",
                           {**make_widget_data(WIDGET_TOKEN, id="4260"), "id": "1"}, token))[0] == 400
        status, _, _ = await post(client, "/api/account/telegram-link/widget",
                                  make_widget_data(WIDGET_TOKEN, id="4260"), token)
        assert status == 200
        assert await app_context.miniapp.resolve_token(token) == 4260
        assert (await app_context.accounts.by_email("kid@example.com"))["telegram_id"] == 4260

    async def test_widget_off_hides_link_button(self, client, app_context, widget_on):
        from app.db.repositories import AppFlagRepository

        _, body, _ = await register(client)
        token = body["data"]["token"]
        await AppFlagRepository(app_context.db).set(account_web.WIDGET_OFF_FLAG, True)
        info = (await (await client.get(f"{BASE}/api/account", params={"token": token})).json())["data"]
        assert info["tg_link"] is None
        status, _, _ = await post(client, "/api/account/telegram-link/widget",
                                  make_widget_data(WIDGET_TOKEN, id="4261"), token)
        assert status == 400 and await app_context.users.get(4261) is None


class TestSignOut:
    async def test_logout_revokes_only_this_device(self, client, app_context):
        _, body, _ = await register(client)
        device = body["data"]["token"]
        uid = await app_context.miniapp.resolve_token(device)
        # Общий токен — ссылки из бота, подписка на календарь.
        shared = await app_context.miniapp.issue_token(uid)
        assert shared != device
        # Общий токен не подменяется токеном устройства.
        assert await app_context.miniapp.issue_token(uid) == shared
        status, data, response = await post(client, "/api/account/logout", {}, device)
        assert status == 200 and data["data"]["revoked"]
        assert await app_context.miniapp.resolve_token(device) is None
        assert await app_context.miniapp.resolve_token(shared) == uid
        # Выход с общим токеном его не отзывает.
        status, data, _ = await post(client, "/api/account/logout", {}, shared)
        assert not data["data"]["revoked"] and await app_context.miniapp.resolve_token(shared) == uid

    async def test_netschool_logout_keeps_app_login(self, client, app_context):
        await app_context.users.save(make_user(31))
        token = await app_context.miniapp.issue_token(31)
        status, data, _ = await post(client, "/api/account/netschool-logout", {}, token)
        assert status == 200 and data["ok"]
        user = await app_context.users.get(31)
        assert not user.school.configured and not user.credentials.login and not user.enabled
        # В приложении остался: дальше оно предложит войти в школу.
        assert await app_context.miniapp.resolve_token(token) == 31
        response = await client.get(f"{BASE}/api/account", headers={"X-Netschool-Token": token})
        assert (await response.json())["data"]["needs_sgo"]

    async def test_page_with_revoked_token_falls_back_to_cookie(self, client, app_context):
        _, body, _ = await register(client)
        device = body["data"]["token"]
        uid = await app_context.miniapp.resolve_token(device)
        await app_context.miniapp.revoke_device_token(device)
        fresh = await app_context.miniapp.issue_token(uid, device=True)
        client.session.cookie_jar.clear()
        response = await client.get(f"{BASE}/", params={"token": device}, cookies={"netschool_token": fresh})
        page = await response.text()
        assert f'"token": "{fresh}"' in page or f'token: "{fresh}"' in page
        assert device not in page


async def test_attach_existing_email_merges_only_after_password_proof(client, app_context, users):
    _, registration, _ = await register(client)
    web_token = registration["data"]["token"]
    web_id = await app_context.miniapp.resolve_token(web_token)
    await users.save(make_user(42))
    token = await app_context.miniapp.issue_token(42, device=True)
    day = dt.date(2026, 10, 18)
    await app_context.notes.add(web_id, day, "С сайта")
    await app_context.notes.add(42, day, "Из Telegram")
    wrong = {"email": "kid@example.com", "password": "неверный-пароль"}
    assert (await post(client, "/api/account/attach", wrong, token))[0] == 403
    assert await users.get(web_id) is not None
    status, data, _ = await post(client, "/api/account/attach",
                                {**wrong, "password": "длинный-пароль"}, token)
    assert status == 200 and data["data"]["email"] == "kid@example.com"
    assert await users.get(web_id) is None
    assert await app_context.miniapp.resolve_token(web_token) == 42
    assert await app_context.miniapp.resolve_token(token) == 42
    assert (await users.get(42)).school.configured
    notes = await app_context.notes.between(42, day, day)
    assert len(notes) == 2
    assert not await app_context.db.fetch_all("PRAGMA foreign_key_check")


async def test_link_two_existing_same_diaries_preserves_sessions_and_notes(client, app_context, users):
    _, body, _ = await register(client)
    token = body["data"]["token"]
    web_id = await app_context.miniapp.resolve_token(token)
    await users.save(make_user(web_id))
    await users.save(make_user(88))
    day = dt.date(2026, 10, 18)
    await app_context.notes.add(web_id, day, "С сайта")
    await app_context.notes.add(88, day, "Из Telegram")
    # Conflicting keys must retain the Telegram state.
    await post(client, "/api/prefs", {"theme": "light"}, token)
    tg_token = await app_context.miniapp.issue_token(88, device=True)
    await post(client, "/api/prefs", {"theme": "dark"}, tg_token)
    _, data, _ = await post(client, "/api/account/telegram-link", {}, token)
    assert (await link_telegram(app_context, data["data"]["code"], 88)).startswith("✅")
    assert await app_context.miniapp.resolve_token(token) == 88
    assert await app_context.miniapp.resolve_token(tg_token) == 88
    assert len(await app_context.notes.between(88, day, day)) == 2
    assert (await app_context.miniapp.prefs(88))["theme"] == "dark"
    assert not await app_context.db.fetch_all("PRAGMA foreign_key_check")


async def test_link_different_diaries_does_not_delete_either(client, app_context, users):
    from dataclasses import replace
    _, body, _ = await register(client)
    token = body["data"]["token"]
    web_id = await app_context.miniapp.resolve_token(token)
    await users.save(make_user(web_id))
    await users.save(replace(make_user(89), selected_student_id=999))
    _, data, _ = await post(client, "/api/account/telegram-link", {}, token)
    assert "разные дневники" in await link_telegram(app_context, data["data"]["code"], 89)
    assert await users.get(web_id) is not None and await users.get(89) is not None
    assert await app_context.miniapp.resolve_token(token) == web_id


async def test_delete_email_account_requires_password_and_revokes_all_sessions(client, app_context, users):
    _, body, _ = await register(client)
    token = body["data"]["token"]
    uid = await app_context.miniapp.resolve_token(token)
    another = await app_context.miniapp.issue_token(uid, device=True)
    await app_context.notes.add(uid, dt.date(2026, 10, 18), "Личная заметка")
    assert (await post(client, "/api/account/delete", {"password": "ошибка"}, token))[0] == 403
    assert await users.get(uid) is not None
    status, data, response = await post(client, "/api/account/delete", {"password": "длинный-пароль"}, token)
    assert status == 200 and data["ok"]
    assert response.cookies["netschool_token"]["max-age"] == "0"
    assert await users.get(uid) is None
    assert await app_context.accounts.by_email("kid@example.com") is None
    assert await app_context.miniapp.resolve_token(token) is None
    assert await app_context.miniapp.resolve_token(another) is None
    assert not await app_context.db.fetch_all("SELECT * FROM user_notes WHERE telegram_id = ?", (uid,))


async def test_link_never_replaces_an_existing_email_binding(client, app_context, users):
    from app.accounts.passwords import hash_password
    _, body, _ = await register(client)
    web_token = body["data"]["token"]
    web_id = await app_context.miniapp.resolve_token(web_token)
    await users.save(make_user(92))
    await app_context.accounts.attach(92, "telegram@example.com", hash_password("отдельный-пароль"))
    _, data, _ = await post(client, "/api/account/telegram-link", {}, web_token)
    assert "другая почта" in await link_telegram(app_context, data["data"]["code"], 92)
    assert await users.get(web_id) is not None
    assert (await app_context.accounts.by_email("telegram@example.com"))["telegram_id"] == 92
    assert (await app_context.accounts.by_email("kid@example.com"))["telegram_id"] == web_id


async def test_delete_also_removes_reset_codes_and_legacy_client_metadata(client, app_context):
    _, body, _ = await register(client)
    token = body["data"]["token"]
    uid = await app_context.miniapp.resolve_token(token)
    link = await app_context.accounts.issue_reset_link("kid@example.com")
    legacy = await app_context.miniapp.issue_token(uid)
    await app_context.miniapp.touch_token(legacy, "Safari")
    assert (await post(client, "/api/account/delete", {"password": "длинный-пароль"}, token))[0] == 200
    assert not await app_context.accounts.reset_link_email(link)
    assert not await app_context.db.fetch_all("SELECT * FROM token_clients WHERE telegram_id = ?", (uid,))


async def test_query_token_logout_keeps_cookie_deleted(client, app_context):
    _, body, _ = await register(client)
    token = body["data"]["token"]
    status, _, response = await post(client, "/api/account/logout", {}, token)
    assert status == 200
    assert response.cookies["netschool_token"]["max-age"] == "0"


async def test_merge_retains_custom_lessons_from_both_accounts(client, app_context, users):
    import json
    _, body, _ = await register(client)
    web_token = body["data"]["token"]
    web_id = await app_context.miniapp.resolve_token(web_token)
    await users.save(make_user(93))
    for uid, edit_id in ((web_id, "web-lesson"), (93, "tg-lesson")):
        payload = json.dumps({"lessonEdits": [{"id": edit_id, "kind": "add", "subject": "Кружок",
                                              "weekday": 1, "start": "16:00", "end": "17:00"}]})
        await app_context.db.execute("INSERT INTO web_prefs (telegram_id, payload) VALUES (?, ?)", (uid, payload))
    _, data, _ = await post(client, "/api/account/telegram-link", {}, web_token)
    assert (await link_telegram(app_context, data["data"]["code"], 93)).startswith("✅")
    prefs = await app_context.miniapp.prefs(93)
    assert {edit["id"] for edit in prefs["lessonEdits"]} == {"web-lesson", "tg-lesson"}


async def test_linking_keeps_earliest_date_and_smaller_account_number(app_context, users):
    await users.save(make_user(5))  # бот завёл запись первым
    await app_context.db.execute("UPDATE users SET created_at = '2026-01-01 00:00:00' WHERE telegram_id = 5")
    telegram_no = await app_context.accounts.number(5)
    from app.web.account import hash_password
    web_id = await app_context.accounts.create_web_user("late@example.com", hash_password("длинный-пароль"))
    assert await app_context.accounts.number(web_id) > telegram_no
    await app_context.accounts.move_user(web_id, 5)
    row = await app_context.db.fetch_one("SELECT created_at, account_no FROM users WHERE telegram_id = 5")
    assert row["created_at"] == "2026-01-01 00:00:00" and row["account_no"] == telegram_no


async def test_owner_gets_first_number_once(app_context, users):
    for telegram_id in (3, 4, 1):
        await users.save(make_user(telegram_id))
    first, owner = await app_context.accounts.number(3), await app_context.accounts.number(1)
    await app_context.db.execute("UPDATE app_metadata SET value = '10003' WHERE key = 'account_no_last'")
    await app_context.db.execute("UPDATE users SET account_no = account_no - ? + 10001", (first,))
    assert await app_context.accounts.number(3) == 10001
    owner = await app_context.accounts.number(1)
    assert await app_context.accounts.give_owner_first_number(1) is True
    assert await app_context.accounts.number(1) == 10001
    assert await app_context.accounts.number(3) == owner
    # Один раз: дальше номера не трогаем.
    assert await app_context.accounts.give_owner_first_number(4) is False
    assert await app_context.accounts.number(4) != 10001
