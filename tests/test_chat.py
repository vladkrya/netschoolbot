"""Школьные чаты: кто попадает, что видно и что нельзя писать."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.chat import rules
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user

BASE = "/mini"


@pytest.fixture(autouse=True)
def no_send_interval(monkeypatch):
    # Лимит частоты проверяется отдельно; в остальных тестах он мешает.
    monkeypatch.setattr("app.web.chat.SEND_INTERVAL_S", 0)


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

    async def test_rate_limit(self, client, student_token, monkeypatch):
        monkeypatch.setattr("app.web.chat.SEND_INTERVAL_S", 60)
        _, body = await join(client, student_token)
        room = body["data"]["rooms"][0]["id"]
        assert (await send(client, student_token, room, "раз"))[0] == 200
        assert (await send(client, student_token, room, "два"))[0] == 429

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


async def make_student(app_context, users, telegram_id, name, class_name="9б"):
    await users.save(make_user(telegram_id, student_name=name))
    identities = getattr(app_context.diary, "chat_identities", {})
    identities[telegram_id] = {"is_student": True, "user_id": 200 + telegram_id, "name": name, "class_name": class_name}
    app_context.diary.chat_identities = identities
    return await app_context.miniapp.issue_token(telegram_id)


async def post(client, path, token, body):
    response = await client.post(f"{BASE}{path}", params={"token": token}, json=body)
    return response.status, await response.json()


class TestModeration:
    async def test_reactions_toggle(self, client, student_token):
        _, body = await join(client, student_token)
        room = body["data"]["rooms"][0]["id"]
        _, sent = await send(client, student_token, room, "ура")
        mid = sent["data"]["id"]
        _, reacted = await post(client, f"/api/chat/messages/{mid}/react", student_token, {"emoji": "🔥"})
        assert reacted["data"]["reactions"] == {"🔥": 1} and reacted["data"]["my_reactions"] == ["🔥"]
        _, reacted = await post(client, f"/api/chat/messages/{mid}/react", student_token, {"emoji": "🔥"})
        assert reacted["data"]["reactions"] == {}
        status, _ = await post(client, f"/api/chat/messages/{mid}/react", student_token, {"emoji": "💩"})
        assert status == 400

    async def test_three_reports_hide(self, client, app_context, users, student_token):
        _, body = await join(client, student_token)
        room = body["data"]["rooms"][0]["id"]
        _, sent = await send(client, student_token, room, "обидное")
        mid = sent["data"]["id"]
        status, _ = await post(client, f"/api/chat/messages/{mid}/report", student_token, {})
        assert status == 400  # на своё нельзя
        tokens = [await make_student(app_context, users, 10 + i, f"Ученик{i} Имя{i}") for i in range(3)]
        for token in tokens:
            await join(client, token)
        await post(client, f"/api/chat/messages/{mid}/report", tokens[0], {})
        await post(client, f"/api/chat/messages/{mid}/report", tokens[0], {})  # повтор не считается
        _, data = await post(client, f"/api/chat/messages/{mid}/report", tokens[1], {})
        assert not data["data"]["hidden"]
        _, data = await post(client, f"/api/chat/messages/{mid}/report", tokens[2], {})
        assert data["data"]["hidden"] and data["data"]["text"] is None

    async def test_delete_own_but_not_others(self, client, app_context, users, student_token):
        _, body = await join(client, student_token)
        room = body["data"]["rooms"][0]["id"]
        other = await make_student(app_context, users, 20, "Петров Иван")
        await join(client, other)
        _, sent = await send(client, student_token, room, "опечатка")
        mid = sent["data"]["id"]
        status, _ = await post(client, f"/api/chat/messages/{mid}/moderate", other, {"action": "delete"})
        assert status == 403
        _, data = await post(client, f"/api/chat/messages/{mid}/moderate", student_token, {"action": "delete"})
        assert data["data"]["deleted"] and data["data"]["text"] is None

    async def test_school_moderator_and_mute(self, client, app_context, users, admin_token, student_token):
        _, body = await join(client, student_token)
        room = body["data"]["rooms"][0]["id"]
        mod = await make_student(app_context, users, 30, "Сидорова Аня", class_name="11а")
        _, mod_body = await join(client, mod)
        # Модератора назначает администратор — по его сообщению.
        _, sent = await send(client, mod, mod_body["data"]["rooms"][2]["id"], "я тут")
        status, _ = await post(client, f"/api/chat/messages/{sent['data']['id']}/author", student_token,
                               {"action": "moderator"})
        assert status == 403
        _, data = await post(client, f"/api/chat/messages/{sent['data']['id']}/author", admin_token,
                             {"action": "moderator"})
        assert data["data"]["moderator"]
        # Модератор видит чужой класс своей школы и мьютит автора.
        _, bad = await send(client, student_token, room, "спам")
        status, _ = await post(client, f"/api/chat/messages/{bad['data']['id']}/moderate", mod, {"action": "hide"})
        assert status == 200
        await post(client, f"/api/chat/messages/{bad['data']['id']}/author", mod, {"action": "mute", "minutes": 60})
        status, data = await send(client, student_token, room, "ещё спам")
        assert status == 403 and "мин" in data["error"]
        # В общем чате модератор школы — обычный участник.
        _, glob = await send(client, mod, "global:10-11", "привет")
        status, _ = await post(client, f"/api/chat/messages/{glob['data']['id']}/moderate", mod, {"action": "pin"})
        assert status == 403

    async def test_ban_and_reports_queue(self, client, app_context, users, admin_token, student_token):
        _, body = await join(client, student_token)
        room = body["data"]["rooms"][0]["id"]
        other = await make_student(app_context, users, 40, "Кузнецов Олег")
        await join(client, other)
        _, sent = await send(client, student_token, room, "плохое")
        await post(client, f"/api/chat/messages/{sent['data']['id']}/report", other, {})
        queue = await (await client.get(f"{BASE}/api/chat/reports", params={"token": admin_token})).json()
        assert [item["id"] for item in queue["data"]["items"]] == [sent["data"]["id"]]
        assert queue["data"]["items"][0]["reports"] == 1
        await post(client, f"/api/chat/messages/{sent['data']['id']}/author", admin_token, {"action": "ban"})
        status, data = await send(client, student_token, room, "снова")
        assert status == 403 and "заблокированы" in data["error"]
        await post(client, f"/api/chat/messages/{sent['data']['id']}/moderate", admin_token, {"action": "dismiss"})
        queue = await (await client.get(f"{BASE}/api/chat/reports", params={"token": admin_token})).json()
        assert queue["data"]["items"] == []
        status, _ = await post(client, f"/api/chat/messages/{sent['data']['id']}/moderate", admin_token,
                               {"action": "pin"})
        listing = await (await client.get(f"{BASE}/api/chat/rooms/{room}/messages",
                                          params={"token": other})).json()
        assert listing["data"]["pinned"]["id"] == sent["data"]["id"]


class TestPollsAndNotifications:
    async def test_poll_and_vote(self, client, student_token):
        _, body = await join(client, student_token)
        room = body["data"]["rooms"][0]["id"]
        status, sent = await send(client, student_token, room, "Кто идёт на экскурсию?",
                                  poll={"options": ["Иду", "Не иду", ""]})
        assert status == 200 and [o["text"] for o in sent["data"]["poll"]["options"]] == ["Иду", "Не иду"]
        mid = sent["data"]["id"]
        _, voted = await post(client, f"/api/chat/messages/{mid}/vote", student_token, {"option": 0})
        assert voted["data"]["poll"]["my"] == 0 and voted["data"]["poll"]["total"] == 1
        _, voted = await post(client, f"/api/chat/messages/{mid}/vote", student_token, {"option": 1})
        assert [o["votes"] for o in voted["data"]["poll"]["options"]] == [0, 1]
        _, voted = await post(client, f"/api/chat/messages/{mid}/vote", student_token, {"option": 1})
        assert voted["data"]["poll"]["my"] is None and voted["data"]["poll"]["total"] == 0
        status, _ = await send(client, student_token, "global:8-9", "опрос", poll={"options": ["а", "б"]})
        assert status == 400

    async def test_mentions_and_replies_notify(self, client, app_context, users, student_token):
        import asyncio

        sent_to = []

        async def capture(user, text, *, push=None, kind=""):
            sent_to.append((user.telegram_id, kind))

        app_context.watchers.notifier.send_text = capture
        _, body = await join(client, student_token)  # Пётр, 9б
        room = body["data"]["rooms"][0]["id"]
        other = await make_student(app_context, users, 50, "Орлова Маша")
        await join(client, other)
        await send(client, other, room, "всем привет")  # без упоминания — тихо
        await asyncio.sleep(0.05)
        assert sent_to == []
        await send(client, other, room, "@Пётр ты сделал алгебру?")
        await asyncio.sleep(0.05)
        assert sent_to == [(2, "chat")]
        listing = await (await client.get(f"{BASE}/api/chat/rooms/{room}/messages",
                                          params={"token": student_token})).json()
        last = listing["data"]["messages"][-1]["id"]
        await send(client, student_token, room, "да", reply_to=last)
        await asyncio.sleep(0.05)
        assert sent_to[-1] == (50, "chat")
        # Заглушил чат — уведомлений нет.
        await post(client, f"/api/chat/rooms/{room}/mute", student_token, {"muted": True})
        await send(client, other, room, "@Петр ау")
        await asyncio.sleep(0.05)
        assert sent_to[-1] == (50, "chat") and len(sent_to) == 2


async def test_migration_switches_everyone_to_new_design(tmp_path):
    """Миграция 19: сохранённый «classic» становится «new», остальное не трогаем."""
    import json

    import aiosqlite

    from app.db.migrations import MIGRATIONS, migrate

    db = await aiosqlite.connect(tmp_path / "m.sqlite3")
    for version, script in enumerate(MIGRATIONS[:18]):
        await db.executescript(script)
        await db.execute(f"PRAGMA user_version = {version + 1}")
    await db.execute("INSERT INTO users (telegram_id) VALUES (1)")
    await db.execute("INSERT INTO web_prefs (telegram_id, payload, updated_at) VALUES (1, ?, datetime('now'))",
                     (json.dumps({"design": "classic", "theme": "dark"}),))
    await db.commit()
    await migrate(db)
    row = await (await db.execute("SELECT payload FROM web_prefs WHERE telegram_id = 1")).fetchone()
    assert json.loads(row[0]) == {"design": "new", "theme": "dark"}
    await db.close()
