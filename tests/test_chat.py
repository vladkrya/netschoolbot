"""Школьные чаты: кто попадает, что видно и что нельзя писать."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.chat import rules
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user

BASE = "/mini"


@pytest.fixture
async def client(app_context) -> TestClient:
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    yield client
    await client.close()


@pytest.fixture
async def admin_token(app_context, users):
    # В тестовых настройках TG_ADMIN_ID = 1.
    await users.save(make_user(1, student_name="Соловьёв Владислав"))
    return await app_context.miniapp.issue_token(1)


@pytest.fixture
async def student_token(app_context, users):
    await users.save(make_user(2, student_name="Иванов Пётр"))
    app_context.diary.chat_identities = {2: {
        "is_student": True, "user_id": 202, "name": "Иванов Пётр", "class_name": "9б"}}
    return await app_context.miniapp.issue_token(2)


async def join(client, token):
    response = await client.post(f"{BASE}/api/chat/join", params={"token": token}, json={"consent": True})
    return response.status, await response.json()


async def send(client, token, room, text, **extra):
    response = await client.post(f"{BASE}/api/chat/rooms/{room}/messages", params={"token": token},
                                 json={"text": text, **extra})
    return response.status, await response.json()


class TestRules:
    def test_grades_and_bands(self):
        assert rules.grade_of("9б") == 9 and rules.grade_of("10-А") == 10 and rules.grade_of("Б") is None
        assert [rules.band_of(g) for g in (3, 5, 9, 11)] == ["1-4", "5-7", "8-9", "10-11"]

    def test_only_first_name(self):
        assert rules.first_name("Соловьёв Владислав Игоревич") == "Владислав"

    def test_filters(self):
        assert rules.check_text("блин, контрольная жесть", global_room=True) == ""
        assert "наркотики" in rules.check_text("где взять закладку", global_room=False)
        assert "доверия" in rules.check_text("хочу покончить с собой", global_room=False)
        assert rules.check_text("пиши в t.me/someone", global_room=True)
        assert rules.check_text("мой номер +7 999 123 45 67", global_room=True)
        assert rules.check_text("пиши @someone_cool", global_room=True)
        # В своём классе ссылки на учебные сайты можно.
        assert rules.check_text("задание на resh.edu.ru", global_room=False) == ""
        assert rules.check_text("@Владислав привет", global_room=True) == ""


class TestJoin:
    async def test_consent_required(self, client, student_token):
        response = await client.post(f"{BASE}/api/chat/join", params={"token": student_token}, json={})
        assert response.status == 400

    async def test_parent_is_rejected(self, client, app_context, student_token):
        app_context.diary.chat_identities = {2: {
            "is_student": False, "user_id": 5, "name": "Иванова Анна", "class_name": "9б"}}
        status, body = await join(client, student_token)
        assert status == 403 and "учеников" in body["error"]

    async def test_rooms_after_join(self, client, student_token):
        status, body = await join(client, student_token)
        assert status == 200 and body["data"]["joined"]
        rooms = body["data"]["rooms"]
        assert [room["kind"] for room in rooms] == ["class", "grade", "school", "global"]
        assert rooms[3]["id"] == "global:8-9"
        assert body["data"]["profile"]["first_name"] == "Пётр"


class TestMessages:
    async def test_send_and_read(self, client, student_token):
        _, body = await join(client, student_token)
        room = body["data"]["rooms"][0]["id"]
        status, sent = await send(client, student_token, room, "привет, 9б")
        assert status == 200
        assert (sent["data"]["author"], sent["data"]["grade"], sent["data"]["admin"]) == ("Пётр", 9, False)
        listing = await (await client.get(f"{BASE}/api/chat/rooms/{room}/messages",
                                          params={"token": student_token})).json()
        assert [m["text"] for m in listing["data"]["messages"]] == ["привет, 9б"]

    async def test_global_room_blocks_contacts(self, client, student_token):
        await join(client, student_token)
        status, body = await send(client, student_token, "global:8-9", "пиши мне t.me/kek")
        assert status == 400 and "ссылки" in body["error"]

    async def test_other_school_room_is_closed(self, client, student_token):
        await join(client, student_token)
        status, _ = await send(client, student_token, "class:deadbeef0000:9б", "эй")
        assert status == 404
        status, _ = await send(client, student_token, "global:1-4", "эй")
        assert status == 404

    async def test_admin_sees_everything_and_signs_as_admin(self, client, admin_token, student_token):
        _, body = await join(client, student_token)
        class_room = body["data"]["rooms"][0]["id"]
        await send(client, student_token, class_room, "вопрос")
        overview = await (await client.get(f"{BASE}/api/chat", params={"token": admin_token})).json()
        ids = [room["id"] for room in overview["data"]["rooms"]]
        assert overview["data"]["admin"] and class_room in ids and "global:1-4" in ids
        status, sent = await send(client, admin_token, class_room, "ответ")
        assert status == 200 and sent["data"]["author"] == "admin" and sent["data"]["grade"] is None
        # От своего профиля — после вступления и переключения.
        await join(client, admin_token)
        await client.post(f"{BASE}/api/chat/identity", params={"token": admin_token}, json={"as_admin": False})
        _, sent = await send(client, admin_token, class_room, "а теперь я")
        assert (sent["data"]["author"], sent["data"]["grade"]) == ("Владислав", 9)

    async def test_unread_and_polling(self, client, admin_token, student_token):
        _, body = await join(client, student_token)
        room = body["data"]["rooms"][0]["id"]
        first = await (await client.get(f"{BASE}/api/chat/rooms/{room}/messages",
                                         params={"token": student_token})).json()
        await send(client, admin_token, room, "раз")
        overview = await (await client.get(f"{BASE}/api/chat", params={"token": student_token})).json()
        assert overview["data"]["rooms"][0]["unread"] == 1
        polled = await (await client.get(f"{BASE}/api/chat/rooms/{room}/messages", params={
            "token": student_token, "after": 0, "since": first["data"]["now"]})).json()
        last_id = polled["data"]["messages"][-1]["id"]
        await client.post(f"{BASE}/api/chat/rooms/{room}/read", params={"token": student_token},
                          json={"last_id": last_id})
        overview = await (await client.get(f"{BASE}/api/chat", params={"token": student_token})).json()
        assert overview["data"]["rooms"][0]["unread"] == 0
