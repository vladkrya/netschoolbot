"""Настройки → «Устройства»: список сеансов и их завершение."""

from tests.test_web import BASE, app_context, client, token  # noqa: F401 — фикстуры


async def items(client, auth):
    response = await client.get(f"{BASE}/api/devices", headers={"X-Netschool-Token": auth, "User-Agent": "Mozilla/5.0 (iPhone) Safari"})
    assert response.status == 200, await response.text()
    return (await response.json())["data"]["items"]


async def post(client, auth, path, body=None):
    response = await client.post(f"{BASE}{path}", headers={"X-Netschool-Token": auth}, json=body or {})
    return response.status, await response.json()


async def test_lists_sessions_without_exposing_tokens(client, token, app_context):
    phone = await app_context.miniapp.issue_token(1, device=True)
    laptop = await app_context.miniapp.issue_token(1, device=True)
    calendar = await app_context.miniapp.issue_calendar_token(1)
    listed = await items(client, phone)
    kinds = [item["kind"] for item in listed]
    assert kinds == ["telegram", "device", "device", "links", "calendar"]
    assert listed[0]["main"] and not listed[0]["current"]
    current = [item for item in listed if item["current"]]
    assert len(current) == 1 and current[0]["kind"] == "device"
    # Первое обращение запомнило устройство и время.
    assert current[0]["label"] == "Safari на iPhone" and current[0]["last_used"]
    text = str(listed)
    assert all(secret not in text for secret in (token, phone, laptop, calendar))


async def test_end_one_device_and_others(client, token, app_context):
    phone = await app_context.miniapp.issue_token(1, device=True)
    laptop = await app_context.miniapp.issue_token(1, device=True)
    calendar = await app_context.miniapp.issue_calendar_token(1)
    target = next(item for item in await items(client, phone) if item["kind"] == "device" and not item["current"])
    status, body = await post(client, phone, "/api/devices/revoke", {"id": target["id"]})
    assert status == 200 and await app_context.miniapp.resolve_token(laptop) is None
    assert await app_context.miniapp.resolve_token(phone) == 1
    # Telegram не завершается; повторно — «уже завершён».
    assert (await post(client, phone, "/api/devices/revoke", {"id": "telegram"}))[0] == 400
    assert (await post(client, phone, "/api/devices/revoke", {"id": target["id"]}))[0] == 404
    # «Все другие»: ссылки из бота уходят, это устройство и календарь остаются.
    status, body = await post(client, phone, "/api/devices/revoke-others")
    assert status == 200
    assert await app_context.miniapp.resolve_token(token) is None
    assert await app_context.miniapp.resolve_token(phone) == 1
    assert await app_context.miniapp.resolve_calendar_token(calendar) == 1
    assert [item["kind"] for item in body["data"]["items"]] == ["telegram", "device", "calendar"]


async def test_end_links_and_calendar(client, token, app_context):
    phone = await app_context.miniapp.issue_token(1, device=True)
    calendar = await app_context.miniapp.issue_calendar_token(1)
    assert (await post(client, phone, "/api/devices/revoke", {"id": "links"}))[0] == 200
    assert await app_context.miniapp.resolve_token(token) is None
    assert (await post(client, phone, "/api/devices/revoke", {"id": "calendar"}))[0] == 200
    assert await app_context.miniapp.resolve_calendar_token(calendar) is None


async def test_link_clients_are_listed(client, token, app_context):
    from app.web import devices as devices_web
    devices_web._touched.clear()
    phone = await app_context.miniapp.issue_token(1, device=True)
    android = "Mozilla/5.0 (Linux; Android 14; SM-S918B Build/UP1A) AppleWebKit Chrome/120 Mobile Safari/537.36"
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit Version/27.2 Mobile/15E148 Safari/604.1"
    # Два разных устройства открывают дневник по одной общей ссылке.
    for ua, ip in ((android, "95.24.10.20"), (iphone, "178.66.1.2")):
        response = await client.get(f"{BASE}/api/prefs", headers={"X-Netschool-Token": token, "User-Agent": ua, "X-Real-IP": ip})
        assert response.status == 200
    links = next(item for item in await items(client, phone) if item["kind"] == "links")
    clients = {entry["label"]: entry for entry in links["clients"]}
    assert set(clients) == {"Chrome на Android", "Safari на iPhone"}
    assert clients["Chrome на Android"]["details"] == "SM-S918B · Android 14"
    assert clients["Safari на iPhone"]["details"] == "iOS 18.7"
    # Точный адрес не хранится.
    assert clients["Chrome на Android"]["ip"] == "95.24.*.*"
    assert "10.20" not in str(links)
    # Завершили ссылки — список пуст.
    await post(client, phone, "/api/devices/revoke", {"id": "links"})
    assert await app_context.miniapp.link_clients(1) == []


IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit Version/27.2 Mobile/15E148 Safari/604.1"


async def open_link(client, url, ua=IPHONE):
    """Открыть ссылку «с чистого устройства»: без cookie. Токен страницы или ""."""
    import re
    client.session.cookie_jar.clear()
    page = await (await client.get(f"{BASE}/?{url.split('?', 1)[1]}", headers={"User-Agent": ua})).text()
    return re.search(r'token: "([^"]*)"', page).group(1)


async def test_new_device_link_opens_once(client, token, app_context):
    phone = await app_context.miniapp.issue_token(1, device=True)
    status, body = await post(client, phone, "/api/devices/new-link")
    assert status == 200
    data = body["data"]
    url, code = data["url"], data["code"]
    assert "?device=" in url and "install=1" in url and token not in url and phone not in url
    assert data["qr"].startswith("data:image/png;base64,")
    assert 3500 < data["expires"] - __import__("time").time() <= 3600
    assert (await post(client, phone, "/api/devices/new-link/status", {"code": code}))[1]["data"]["state"] == "waiting"
    # Превью в мессенджере ссылку не гасит.
    assert await open_link(client, url, "TelegramBot (like TwitterBot)") == ""
    # Первое открытие — новый токен устройства, второе — уже ничего.
    device = await open_link(client, url)
    assert device not in ("", token, phone) and await app_context.miniapp.is_device_token(device)
    assert await open_link(client, url) == ""
    status = (await post(client, phone, "/api/devices/new-link/status", {"code": code}))[1]["data"]
    assert status["state"] == "used" and status["label"] == "Safari на iPhone"
    assert status["id"] in [item["id"] for item in await items(client, phone)]
    # Открытую ссылку уже не отменить и QR по ней не прислать.
    assert (await post(client, phone, "/api/devices/new-link/send-qr", {"code": code}))[0] == 400


async def test_new_device_link_cancel_and_expiry(client, token, app_context):
    phone = await app_context.miniapp.issue_token(1, device=True)
    code = (await post(client, phone, "/api/devices/new-link"))[1]["data"]["code"]
    assert (await post(client, phone, "/api/devices/new-link/cancel", {"code": code}))[0] == 200
    assert (await post(client, phone, "/api/devices/new-link/status", {"code": code}))[1]["data"]["state"] == "gone"
    assert await app_context.miniapp.use_device_code(code) is None

    url = (await post(client, phone, "/api/devices/new-link"))[1]["data"]["url"]
    code = url.split("device=")[1].split("&")[0]
    await app_context.db.execute("UPDATE install_codes SET expires_at = 1 WHERE code = ?", (code,))
    assert (await post(client, phone, "/api/devices/new-link/status", {"code": code}))[1]["data"]["state"] == "expired"
    assert await open_link(client, url) == ""
    # Чужую ссылку не узнать и не отменить.
    other = (await app_context.miniapp.issue_device_code(2))[0]
    assert (await post(client, phone, "/api/devices/new-link/status", {"code": other}))[1]["data"]["state"] == "gone"
    await post(client, phone, "/api/devices/new-link/cancel", {"code": other})
    assert (await app_context.miniapp.device_code(2, other))["used_at"] is None


async def test_pwa_and_safari_are_distinct_clients_of_legacy_link(client, token, app_context):
    from app.web import devices as devices_web
    devices_web._touched.clear()
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit Version/27.2 Mobile/15E148 Safari/604.1"
    for mode in ("web", "pwa"):
        response = await client.get(f"{BASE}/api/prefs", headers={
            "X-Netschool-Token": token, "User-Agent": ua, "X-Netschool-Client": mode})
        assert response.status == 200
    clients = await app_context.miniapp.link_clients(1)
    assert {row["client"] for row in clients} == {"Safari на iPhone", "Веб-приложение · Safari на iPhone"}


async def test_new_telegram_button_does_not_create_legacy_links(client, app_context, users):
    from app.bot.handlers.menu import miniapp_url
    from tests.test_repositories import make_user
    user = await users.save(make_user(91))
    assert await miniapp_url(app_context, user) == app_context.settings.web.miniapp_url + "/"
    token = await app_context.miniapp.issue_token(91, device=True)
    listed = await items(client, token)
    assert not any(row["kind"] == "links" for row in listed)


async def test_device_pwa_label_is_updated_from_first_browser_visit(client, app_context, token):
    from app.web import devices as devices_web
    devices_web._touched.clear()
    device = await app_context.miniapp.issue_token(1, device=True)
    await items(client, device)
    response = await client.get(f"{BASE}/api/devices", headers={
        "X-Netschool-Token": device, "User-Agent": "Mozilla/5.0 (iPhone) Safari", "X-Netschool-Client": "pwa"})
    rows = (await response.json())["data"]["items"]
    assert next(row for row in rows if row["current"])["label"].startswith("Веб-приложение")


async def test_new_device_qr_to_chat(app_context, users):
    from aiohttp.test_utils import TestClient, TestServer

    from app.web.server import create_app
    from tests.test_web import make_user

    await users.save(make_user(1))
    sent = []

    class Bot:
        async def send_photo(self, chat_id, photo, caption=""):
            sent.append((chat_id, photo.data[:8], caption))

    client = TestClient(TestServer(create_app(app_context, bot=Bot())))
    await client.start_server()
    try:
        phone = await app_context.miniapp.issue_token(1, device=True)
        code = (await post(client, phone, "/api/devices/new-link"))[1]["data"]["code"]
        assert (await post(client, phone, "/api/devices/new-link/send-qr", {"code": code}))[0] == 200
    finally:
        await client.close()
    assert sent and sent[0][0] == 1 and sent[0][1] == b"\x89PNG\r\n\x1a\n" and "одно открытие" in sent[0][2]


async def test_rename_session(client, token, app_context):
    phone = await app_context.miniapp.issue_token(1, device=True)
    target = next(item for item in await items(client, phone) if item["current"])
    assert target["name"] == ""
    status, body = await post(client, phone, "/api/devices/rename", {"id": target["id"], "name": "  Планшет   на кухне  "})
    assert status == 200
    assert next(item for item in body["data"]["items"] if item["current"])["name"] == "Планшет на кухне"
    # Общие ссылки и Telegram не подписываются; завершённый — «уже завершён».
    assert (await post(client, phone, "/api/devices/rename", {"id": "links", "name": "x"}))[0] == 404
    await app_context.miniapp.revoke_device_token(phone)
    other = await app_context.miniapp.issue_token(1, device=True)
    assert (await post(client, other, "/api/devices/rename", {"id": target["id"], "name": "x"}))[0] == 404


async def test_home_screen_icon_gets_own_token(client, token, app_context):
    safari = await app_context.miniapp.issue_token(1, device=True)
    status, body = await post(client, safari, "/api/devices/own")
    own = body["data"]["token"]
    assert status == 200 and own not in ("", safari)
    # Выход в Safari иконку не трогает.
    await app_context.miniapp.revoke_device_token(safari)
    assert await app_context.miniapp.resolve_token(own) == 1
    # Общий токен старых ссылок не размножается.
    assert (await post(client, token, "/api/devices/own"))[1]["data"]["token"] == ""


class FakeSocket:
    def __init__(self):
        self.sent, self.closed = [], False

    async def send_json(self, event):
        self.sent.append(event)

    async def close(self):
        self.closed = True


def listen(app_context, uid, *, tab="", device=""):
    from app.chat.hub import Listener

    socket = FakeSocket()
    app_context.hub.add(Listener(uid, set(), socket=socket, tab=tab, device=device))
    return socket


async def test_changes_reach_other_tabs_not_the_sender(client, token, app_context):
    here, there, stranger = listen(app_context, 1, tab="a"), listen(app_context, 1, tab="b"), listen(app_context, 2)
    response = await client.post(f"{BASE}/api/homework/done", headers={"X-Netschool-Token": token, "X-Netschool-Tab": "a"},
                                 json={"key": "0123456789abcdef", "done": True})
    assert response.status == 200
    homework = {"t": "sync", "topic": "homework"}
    assert homework not in here.sent and stranger.sent == []
    assert homework in there.sent
    # Ошибка ничего не меняет — и сигнала нет.
    await client.post(f"{BASE}/api/homework/done", headers={"X-Netschool-Token": token}, json={"key": "bad"})
    assert there.sent.count(homework) == 1


async def test_revoked_device_signs_out_at_once(client, token, app_context):
    from app.db.repositories import token_fingerprint

    phone = await app_context.miniapp.issue_token(1, device=True)
    laptop = await app_context.miniapp.issue_token(1, device=True)
    telegram = listen(app_context, 1)
    on_phone = listen(app_context, 1, device=token_fingerprint(phone))
    on_laptop = listen(app_context, 1, device=token_fingerprint(laptop))
    target = next(item for item in await items(client, phone) if item["kind"] == "device" and not item["current"])
    assert (await post(client, phone, "/api/devices/revoke", {"id": target["id"]}))[0] == 200
    assert on_laptop.closed and {"t": "signed-out"} in on_laptop.sent
    assert not on_phone.closed and not telegram.closed
    assert {"t": "sync", "topic": "devices"} in on_phone.sent


def test_sync_topics():
    from app.web.sync import topic_for

    assert topic_for("/app/api/homework/done") == "homework"
    assert topic_for("/app/api/homework/5/answer/text") == "answers"
    assert topic_for("/app/api/notes/12") == "notes"
    assert topic_for("/app/api/devices/new-link/status") == ""
    assert topic_for("/app/api/devices/revoke") == "devices"
    assert topic_for("/app/api/student-class") == ""
    assert topic_for("/app/api/visit") == ""


def test_ws_ticket_carries_device():
    from types import SimpleNamespace

    from app.web.chat import issue_ws_ticket, verify_ws_ticket, ws_ticket_device

    app = SimpleNamespace(settings=SimpleNamespace(telegram=SimpleNamespace(bot_token="123:abc")))
    ticket = issue_ws_ticket(app, 7, now=100, device="00ff00ff00ff00ff")
    assert verify_ws_ticket(app, ticket, now=120) == 7 and ws_ticket_device(ticket) == "00ff00ff00ff00ff"
    forged = ticket.replace("00ff00ff00ff00ff", "11ff00ff00ff00ff")
    assert verify_ws_ticket(app, forged, now=120) is None
    plain = issue_ws_ticket(app, 7, now=100)
    assert verify_ws_ticket(app, plain, now=120) == 7 and ws_ticket_device(plain) == ""
