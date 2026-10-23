"""Экран «Админ-настройки» (web/admin.py): только владелец, вход через Telegram, статистика."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.bot.handlers.admin import widget_mode
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user

ADMIN = 1  # admin_id в фикстуре app


@pytest.fixture
async def client(app_context):
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    yield client
    await client.close()


async def test_only_owner(client, app_context, users):
    await users.save(make_user(ADMIN))
    await users.save(make_user(5))
    admin, other = [await app_context.miniapp.issue_token(uid) for uid in (ADMIN, 5)]

    for path in ("/mini/api/admin", "/mini/api/admin/stats"):
        assert (await client.get(path, params={"token": other})).status == 403
    response = await client.post("/mini/api/admin/widget", json={"mode": "off"}, params={"token": other})
    assert response.status == 403 and await widget_mode(app_context) != "off"

    profile = lambda token: client.get("/mini/api/profile", params={"token": token})  # noqa: E731
    assert (await (await profile(admin)).json())["data"]["admin"] is True
    assert (await (await profile(other)).json())["data"]["admin"] is False

    data = (await (await client.get("/mini/api/admin", params={"token": admin})).json())["data"]
    # Чаты отключены в коде — тумблера в админке нет.
    assert data["chats"] is None and data["share_sgo"] == {"on": False}
    assert data["widget"]["mode"] in ("main", "reserve")

    response = await client.post("/mini/api/admin/widget", json={"mode": "off"}, params={"token": admin})
    assert response.status == 200 and (await response.json())["data"]["widget"]["mode"] == "off"
    assert await widget_mode(app_context) == "off"
    response = await client.post("/mini/api/admin/widget", json={"mode": "main"}, params={"token": admin})
    assert (await response.json())["data"]["widget"]["mode"] == "main"
    assert (await client.post("/mini/api/admin/widget", json={"mode": "x"}, params={"token": admin})).status == 400

    stats = (await (await client.get("/mini/api/admin/stats", params={"token": admin})).json())["data"]["html"]
    assert "Статистика" in stats and "Пользователи: 2" in stats


async def test_chats_disabled_in_code(client, app_context, users):
    from app.db.repositories import AppFlagRepository

    await users.save(make_user(ADMIN))
    await users.save(make_user(5))
    admin, other = [await app_context.miniapp.issue_token(uid) for uid in (ADMIN, 5)]
    # Даже включённый когда-то тумблер не возвращает чаты.
    await AppFlagRepository(app_context.db).set("chats_on", True)
    for token in (admin, other):
        profile = (await (await client.get("/mini/api/profile", params={"token": token})).json())["data"]
        assert profile["chats"] is False
        response = await client.get("/mini/api/chat/rooms", params={"token": token})
        assert response.status == 403
    response = await client.post("/mini/api/settings/chats", json={"on": True}, params={"token": admin})
    assert response.status == 410
