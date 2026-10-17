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


async def test_new_device_link(client, token, app_context):
    phone = await app_context.miniapp.issue_token(1, device=True)
    status, body = await post(client, phone, "/api/devices/new-link")
    assert status == 200
    url = body["data"]["url"]
    assert "?enter=" in url and token not in url and phone not in url
    assert await app_context.miniapp.use_enter_code(url.split("enter=")[1]) == 1
    assert body["data"]["qr"].startswith("data:image/png;base64,")
