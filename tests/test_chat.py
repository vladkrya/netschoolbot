"""Школьные чаты: кто попадает, что видно и что нельзя писать."""

import hashlib

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


async def join(client, token, nick=None):
    nick = nick or "Student_" + hashlib.sha1(token.encode()).hexdigest()[:8]
    response = await client.post(f"{BASE}/api/chat/join", params={"token": token}, json={"consent": True, "nick": nick})
    return response.status, await response.json()


async def send(client, token, room, text, **extra):
    response = await client.post(f"{BASE}/api/chat/rooms/{room}/messages", params={"token": token},
                                 json={"text": text, **extra})
    return response.status, await response.json()


class TestRules:
    def test_grades_and_bands(self):
        assert rules.grade_of("9б") == 9 and rules.grade_of("10-А") == 10 and rules.grade_of("Б") is None
        assert [rules.band_of(g) for g in (3, 5, 6, 9, 11)] == [None, None, "6-11", "6-11", "6-11"]

    def test_short_school_names(self):
        assert rules.short_school("МБУ «Школа № 93»") == "школа №93"
        assert rules.short_school('МБОУ "Лицей №6" г. Рубцовска') == "лицей №6"
        assert rules.short_school('МБОУ "СОШ 10 Кадетский корпус юных спасателей"') == "школа №10"
        assert rules.short_school('МАОУ "Сотниковская СОШ"') == "Сотниковская СОШ"
        assert rules.grade_title(5, "") == "5-е классы"

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
        assert [room["kind"] for room in rooms] == ["grade", "school", "global", "support"]
        assert [room["title"] for room in rooms[:2]] == ["9-е классы · школа №1", "Школа №1"]
        assert rooms[2]["id"] == "global:6-11" and rooms[2]["title"] == "Общий чат"
        assert body["data"]["profile"]["nick"].startswith("Student_")
        assert "first_name" not in body["data"]["profile"]


class TestMessages:
    async def test_send_and_read(self, client, student_token):
        _, body = await join(client, student_token)
        room = body["data"]["rooms"][0]["id"]
        status, sent = await send(client, student_token, room, "привет, 9б")
        assert status == 200
        assert (sent["data"]["author"], sent["data"]["grade"], sent["data"]["admin"]) == (body["data"]["profile"]["nick"], 9, False)
        listing = await (await client.get(f"{BASE}/api/chat/rooms/{room}/messages",
                                          params={"token": student_token})).json()
        assert [m["text"] for m in listing["data"]["messages"] if m["kind"] != "system"] == ["привет, 9б"]

    async def test_rate_limit(self, client, student_token, monkeypatch):
        monkeypatch.setattr("app.web.chat.SEND_INTERVAL_S", 60)
        _, body = await join(client, student_token)
        room = body["data"]["rooms"][0]["id"]
        assert (await send(client, student_token, room, "раз"))[0] == 200
        assert (await send(client, student_token, room, "два"))[0] == 429

    async def test_global_room_blocks_contacts(self, client, student_token):
        await join(client, student_token)
        status, body = await send(client, student_token, "global:6-11", "пиши мне t.me/kek")
        assert status == 400 and "ссылки" in body["error"]

    async def test_other_school_room_is_closed(self, client, student_token):
        await join(client, student_token)
        status, _ = await send(client, student_token, "grade:deadbeef0000:9", "эй")
        assert status == 404
        status, _ = await send(client, student_token, "global:1-4", "эй")
        assert status == 404

    async def test_no_global_chat_below_sixth_grade(self, client, app_context, users):
        token = await make_student(app_context, users, 60, "Малышев Коля", class_name="5а")
        _, body = await join(client, token)
        assert [room["kind"] for room in body["data"]["rooms"]] == ["grade", "school", "support"]
        status, _ = await send(client, token, "global:6-11", "привет")
        assert status == 404

    async def test_admin_sees_everything_and_signs_as_admin(self, client, admin_token, student_token):
        _, body = await join(client, student_token)
        class_room = body["data"]["rooms"][0]["id"]
        await send(client, student_token, class_room, "вопрос")
        overview = await (await client.get(f"{BASE}/api/chat", params={"token": admin_token})).json()
        ids = [room["id"] for room in overview["data"]["rooms"]]
        assert overview["data"]["admin"] and class_room in ids and "global:6-11" in ids
        status, sent = await send(client, admin_token, class_room, "ответ")
        assert status == 200 and sent["data"]["author"] == "admin" and sent["data"]["grade"] is None
        # От своего профиля — после вступления и переключения.
        await join(client, admin_token, "Vlad_9")
        await client.post(f"{BASE}/api/chat/identity", params={"token": admin_token}, json={"as_admin": False})
        _, sent = await send(client, admin_token, class_room, "а теперь я")
        assert (sent["data"]["author"], sent["data"]["grade"]) == ("Vlad_9", 9)

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
        _, sent = await send(client, mod, mod_body["data"]["rooms"][1]["id"], "я тут")
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
        _, glob = await send(client, mod, "global:6-11", "привет")
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
        status, _ = await send(client, student_token, "global:6-11", "опрос", poll={"options": ["а", "б"]})
        assert status == 400

    async def test_mentions_and_replies_notify(self, client, app_context, users, student_token):
        import asyncio

        sent_to = []

        async def capture(user, text, *, push=None, kind=""):
            sent_to.append((user.telegram_id, kind))

        app_context.watchers.notifier.send_text = capture
        _, body = await join(client, student_token, "Petya_9")  # ник ученика
        room = body["data"]["rooms"][0]["id"]
        other = await make_student(app_context, users, 50, "Орлова Маша")
        await join(client, other)
        await post(client, f"/api/chat/rooms/{room}/notify", student_token, {"mode": "mentions"})
        await send(client, other, room, "всем привет")  # без упоминания — тихо
        await asyncio.sleep(0.05)
        assert sent_to == []
        await send(client, other, room, "@Petya_9 ты сделал алгебру?")
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
        await send(client, other, room, "@Petya_9 ау")
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


class TestChatContinuation:
    async def test_nicks_unique_reserved_and_private(self, client, app_context, users, student_token, admin_token):
        for nick in ('admin', 'Аdmin', 'adm1n', 'ab', 'bad name'):
            status, _ = await join(client, student_token, nick)
            assert status == 400
        _, profile = await join(client, student_token, 'Cosmos_9')
        room = profile['data']['rooms'][0]['id']
        other = await make_student(app_context, users, 60, 'Петров Иван')
        assert (await join(client, other, 'cоsmоs_9'))[0] == 400  # русские «о»
        assert (await join(client, other, 'Moon_9'))[0] == 200
        _, msg = await send(client, student_token, room, 'Привет')
        assert msg['data']['author'] == 'Cosmos_9'
        listing = await (await client.get(f'{BASE}/api/chat/rooms/{room}/messages', params={'token': other})).json()
        assert 'Пётр' not in str(listing) and 'Иванов' not in str(listing)
        assert 'first_name' not in profile['data']['profile']
        status, _ = await post(client, '/api/chat/nick', student_token, {'nick': 'Other_9'})
        assert status == 403  # смена только через администратора
        response = await client.get(f'{BASE}/api/chat/rooms/{room}/members', params={'token': other, 'q': 'Cos'})
        assert (await response.json())['data']['items'] == [{'nick': 'Cosmos_9', 'grade': 9}]

    async def test_existing_profile_requires_nick_and_migration_scrubs_names(self, client, db, student_token):
        from app.chat.repository import ChatRepository
        repo = ChatRepository(db)
        profile = await repo.save_profile(2, netschool_user_id=202, school_key='oldschool', school_name='Школа',
                                         class_name='9б', grade=9, first_name='Пётр', consent=True)
        room = rules.rooms_for(profile['school_key'], '9б', 9)[0].id
        status, data = await send(client, student_token, room, 'Привет')
        assert status == 403 and data['reason'] == 'nick'
        status, data = await post(client, '/api/chat/nick', student_token, {'nick': 'Cosmos_9'})
        assert status == 200 and data['data']['profile']['nick'] == 'Cosmos_9'
        assert (await send(client, student_token, room, 'Привет'))[0] == 200

    async def test_admin_access_requires_admin_identity(self, client, app_context, users, student_token, admin_token):
        await join(client, student_token, 'Cosmos_9')
        await join(client, admin_token, 'Vlad_9')
        await post(client, '/api/chat/identity', admin_token, {'as_admin': False})
        foreign = make_user(65, student_name='Иван Иванов')
        from dataclasses import replace
        await users.save(replace(foreign, school=replace(foreign.school, name='Школа №65')))
        token = await app_context.miniapp.issue_token(65)
        app_context.diary.chat_identities[65] = {'is_student': True, 'user_id': 265, 'name': 'Иван Иванов', 'class_name': '8а'}
        _, joined = await join(client, token, 'Alien_8')
        room = joined['data']['rooms'][0]['id']
        overview = await (await client.get(f'{BASE}/api/chat', params={'token': admin_token})).json()
        assert room not in [r['id'] for r in overview['data']['rooms']]
        assert not overview['data']['as_admin'] and not overview['data']['moderator']
        assert (await send(client, admin_token, room, 'От себя доступа нет'))[0] == 404
        await post(client, '/api/chat/identity', admin_token, {'as_admin': True})
        _, sent = await send(client, admin_token, room, 'Ответ администратора')
        assert sent['data']['author'] == 'admin' and sent['data']['admin']
        assert (await post(client, f"/api/chat/messages/{sent['data']['id']}/moderate", admin_token, {'action': 'pin'}))[0] == 200
        assert (await send(client, student_token, room, 'Нет доступа'))[0] == 404

    async def test_read_receipts_and_read_bounds(self, client, app_context, users, student_token, admin_token):
        _, joined = await join(client, student_token, 'Cosmos_9')
        room = joined['data']['rooms'][0]['id']
        other = await make_student(app_context, users, 70, 'Петров Иван')
        await join(client, other, 'Moon_9')
        _, msg = await send(client, student_token, room, 'Прочитай')
        mid = msg['data']['id']
        assert (await post(client, f'/api/chat/rooms/{room}/read', other, {'last_id': 99999999}))[0] == 404
        assert (await post(client, f'/api/chat/rooms/{room}/read', other, {'last_id': mid}))[0] == 200
        readers = await (await client.get(f'{BASE}/api/chat/messages/{mid}/reads', params={'token': student_token})).json()
        assert readers['data'] == {'items': [{'nick': 'Moon_9', 'grade': 9}], 'count': 1}
        assert (await client.get(f'{BASE}/api/chat/messages/{mid}/reads', params={'token': other})).status == 403
        listing = await (await client.get(f'{BASE}/api/chat/rooms/{room}/messages', params={'token': student_token})).json()
        assert listing['data']['messages'][-1]['read']
        _, school_msg = await send(client, student_token, joined['data']['rooms'][1]['id'], 'Другая комната')
        assert (await post(client, f'/api/chat/rooms/{room}/read', other, {'last_id': school_msg['data']['id']}))[0] == 404

    async def test_report_jump_and_deletion_actor(self, client, app_context, users, student_token, admin_token):
        _, joined = await join(client, student_token, 'Cosmos_9')
        room = joined['data']['rooms'][0]['id']
        other = await make_student(app_context, users, 75, 'Петров Иван')
        await join(client, other, 'Moon_9')
        _, msg = await send(client, student_token, room, 'Жалоба на старое сообщение')
        mid = msg['data']['id']
        await post(client, f'/api/chat/messages/{mid}/report', other, {})
        from app.chat.repository import ChatRepository
        repo = ChatRepository(app_context.db)
        for _ in range(60):
            await repo.add_message(room, author_id=1, author_name='admin', author_grade=None, is_admin=True, text='Позже')
        latest = await (await client.get(f'{BASE}/api/chat/rooms/{room}/messages', params={'token': admin_token})).json()
        assert mid not in [m['id'] for m in latest['data']['messages']]
        around = await (await client.get(f'{BASE}/api/chat/rooms/{room}/messages', params={'token': admin_token, 'around': mid})).json()
        assert mid in [m['id'] for m in around['data']['messages']]
        _, deleted = await post(client, f'/api/chat/messages/{mid}/moderate', admin_token, {'action': 'delete'})
        assert deleted['data']['deleted_by'] == 'admin' and deleted['data']['text'] is None

    async def test_unread_modes_and_system_messages(self, client, student_token, admin_token):
        _, joined = await join(client, student_token, 'Cosmos_9')
        room = joined['data']['rooms'][0]['id']
        await send(client, admin_token, room, 'Раз')
        await send(client, admin_token, room, 'Два')
        unread = await (await client.get(f'{BASE}/api/chat/unread', params={'token': student_token})).json()
        assert unread['data']['total'] == 2 and unread['data']['live']
        await post(client, f'/api/chat/rooms/{room}/notify', student_token, {'mode': 'off'})
        unread = await (await client.get(f'{BASE}/api/chat/unread', params={'token': student_token})).json()
        assert unread['data']['total'] == 0
        assert (await post(client, f'/api/chat/rooms/{room}/notify', student_token, {'mode': 'unknown'}))[0] == 400
        await post(client, f'/api/chat/rooms/{room}/notify', student_token, {'mode': 'all'})
        unread = await (await client.get(f'{BASE}/api/chat/unread', params={'token': student_token})).json()
        assert unread['data']['total'] == 2

    async def test_notification_modes_admin_mention_and_leave(self, client, app_context, users, student_token, admin_token):
        import asyncio
        from app.web.chat import _last_notified
        _last_notified.clear()
        captured = []
        async def capture(user, text, *, push=None, kind=''):
            captured.append(user.telegram_id)
        app_context.watchers.notifier.send_text = capture
        _, joined = await join(client, student_token, 'Cosmos_9')
        room = joined['data']['rooms'][0]['id']
        other = await make_student(app_context, users, 80, 'Петров Иван')
        await join(client, other, 'Moon_9')
        await send(client, other, room, 'Всем привет')
        await asyncio.sleep(.05)
        assert captured == [2]  # школьные чаты: все сообщения по умолчанию
        await post(client, f'/api/chat/rooms/{room}/notify', student_token, {'mode': 'mentions'})
        await send(client, other, room, 'Просто сообщение')
        await asyncio.sleep(.05)
        assert captured == [2]
        await send(client, other, 'global:6-11', '@Cosmos_9 и @admin привет')
        await asyncio.sleep(.05)
        assert set(captured[-2:]) == {1, 2}
        await post(client, f'/api/chat/rooms/{room}/notify', student_token, {'mode': 'all'})
        await post(client, '/api/chat/leave', student_token, {})
        _last_notified.clear()
        captured.clear()
        await send(client, other, room, 'Ученик уже вышел')
        await asyncio.sleep(.05)
        assert captured == []

    async def test_websocket_ticket_and_room_delivery(self, client, app_context, users, student_token, admin_token):
        import asyncio
        from aiohttp import WSServerHandshakeError
        _, joined = await join(client, student_token, 'Cosmos_9')
        room = joined['data']['rooms'][0]['id']
        _, data = await post(client, '/api/chat/ws-ticket', student_token, {})
        ticket = data['data']['ticket']
        with pytest.raises(WSServerHandshakeError):
            await client.ws_connect(f'{BASE}/api/chat/ws?ticket=bad')
        ws = await client.ws_connect(f'{BASE}/api/chat/ws?ticket={ticket}')
        assert await ws.receive_json(timeout=2) == {'t': 'hello'}
        await ws.send_json({'t': 'open', 'room': room})
        await ws.send_json({'t': 'ping'})
        assert await ws.receive_json(timeout=2) == {'t': 'pong'}
        notified = []
        async def capture(user, text, *, push=None, kind=''):
            notified.append(user.telegram_id)
        app_context.watchers.notifier.send_text = capture
        _, sent = await send(client, admin_token, room, 'Мгновенно')
        await asyncio.sleep(.05)
        assert notified == []  # открытый чат не дублирует событие уведомлением
        event = await ws.receive_json(timeout=2)
        assert event == {'t': 'msg', 'room': room, 'id': sent['data']['id']}
        await post(client, f"/api/chat/messages/{sent['data']['id']}/react", student_token, {'emoji': '🔥'})
        assert (await ws.receive_json(timeout=2))['t'] == 'upd'
        await send(client, admin_token, 'grade:deadbeef0000:9', 'Чужая школа')
        await ws.send_json({'t': 'ping'})
        assert await ws.receive_json(timeout=2) == {'t': 'pong'}  # чужую комнату не слышит
        await post(client, '/api/chat/leave', student_token, {})
        for _ in range(4):
            await ws.receive(timeout=2)
            if ws.closed:
                break
        assert ws.closed
        await ws.close()

    async def test_ws_ticket_requires_pin_unlock_and_expires(self, client, app_context, student_token):
        from app.web.chat import issue_ws_ticket, verify_ws_ticket
        ticket = issue_ws_ticket(app_context, 2, now=100)
        assert verify_ws_ticket(app_context, ticket, now=159) == 2
        assert verify_ws_ticket(app_context, ticket, now=161) is None
        assert verify_ws_ticket(app_context, ticket + 'bad', now=100) is None
        await post(client, '/api/lock/pin', student_token, {'pin': '4826'})
        response = await client.post(f'{BASE}/api/chat/ws-ticket', params={'token': student_token}, json={})
        assert response.status == 423
        _, unlocked = await post(client, '/api/lock/unlock', student_token, {'pin': '4826'})
        response = await client.post(f'{BASE}/api/chat/ws-ticket', params={'token': student_token},
                                     headers={'X-Netschool-Unlock': unlocked['data']['unlock']}, json={})
        assert response.status == 200


async def test_nick_migration_hides_previous_names(tmp_path):
    import aiosqlite
    from app.db.migrations import MIGRATIONS, migrate
    db = await aiosqlite.connect(tmp_path / 'old.sqlite3')
    for version, script in enumerate(MIGRATIONS[:19], 1):
        await db.executescript(script)
        await db.execute(f'PRAGMA user_version = {version}')
    await db.execute('INSERT INTO users (telegram_id) VALUES (2)')
    await db.execute("INSERT INTO chat_messages (room, author_id, author_name, text, created_at, updated_at) VALUES ('global:6-11', 2, 'Пётр', 'Привет', 1, 1)")
    await db.execute("INSERT INTO chat_reads (telegram_id, room, muted) VALUES (2, 'global:6-11', 1)")
    await db.commit()
    await migrate(db)
    assert (await (await db.execute('SELECT author_name FROM chat_messages')).fetchone())[0] == ''
    assert (await (await db.execute('SELECT notify FROM chat_reads')).fetchone())[0] == 'off'
    await db.close()


class TestAdminPrivateChats:
    async def test_private_access_rename_and_rejoin(self, client, app_context, users, student_token, admin_token):
        await join(client, student_token, 'Cosmos_9')
        other = await make_student(app_context, users, 60, 'Петров Иван')
        await join(client, other, 'Moon_9')
        room = 'support:2'
        assert (await client.get(f'{BASE}/api/chat/rooms/{room}/messages', params={'token': other})).status == 404
        from app.chat.repository import ChatRepository
        await ChatRepository(app_context.db).set_moderator(60, rules.school_key(make_user(2).school.url, make_user(2).school.name), True)
        assert (await client.get(f'{BASE}/api/chat/rooms/{room}/messages', params={'token': other})).status == 404
        _, sent = await send(client, student_token, room, 'Прошу сменить ник')
        mid = sent['data']['id']
        _, reply = await send(client, admin_token, room, 'Конечно', reply_to=mid)
        assert reply['data']['admin']
        assert (await post(client, '/api/chat/profiles/2/nick', student_token, {'nick': 'New_9'}))[0] == 403
        assert (await post(client, '/api/chat/profiles/2/nick', admin_token, {'nick': 'Moon_9'}))[0] == 400
        assert (await post(client, '/api/chat/profiles/2/nick', admin_token, {'nick': 'New_9'}))[0] == 200
        messages = await (await client.get(f'{BASE}/api/chat/rooms/{room}/messages', params={'token': student_token})).json()
        assert messages['data']['messages'][0]['author'] == 'New_9'
        assert 'author_id' not in messages['data']['messages'][0] or messages['data']['messages'][0]['author_id'] is None
        readers = await (await client.get(f'{BASE}/api/chat/messages/{mid}/reads', params={'token': student_token})).json()
        assert readers['data']['items'] == [{'nick': 'admin', 'grade': None}]
        await post(client, '/api/chat/leave', student_token, {})
        _, joined = await join(client, student_token, 'Bypass_9')
        assert joined['data']['profile']['nick'] == 'New_9'
        assert (await join(client, await make_student(app_context, users, 61, 'Иван'), 'New_9'))[0] == 400

    async def test_mode_switch_closes_socket_and_blocks_private_room(self, client, student_token, admin_token):
        await join(client, student_token, 'Cosmos_9')
        await join(client, admin_token, 'Vlad_9')
        ticket = (await (await client.post(f'{BASE}/api/chat/ws-ticket', params={'token': admin_token})).json())['data']['ticket']
        socket = await client.ws_connect(f'{BASE}/api/chat/ws?ticket={ticket}')
        assert (await socket.receive_json())['t'] == 'hello'
        await post(client, '/api/chat/identity', admin_token, {'as_admin': False})
        from aiohttp import WSMsgType
        assert (await socket.receive(timeout=2)).type == WSMsgType.CLOSE
        assert (await send(client, admin_token, 'support:2', 'От себя нельзя'))[0] == 404
        assert (await post(client, '/api/chat/profiles/2/nick', admin_token, {'nick': 'New_9'}))[0] == 403
        ticket = (await (await client.post(f'{BASE}/api/chat/ws-ticket', params={'token': admin_token})).json())['data']['ticket']
        socket = await client.ws_connect(f'{BASE}/api/chat/ws?ticket={ticket}')
        await socket.receive_json()
        await send(client, student_token, 'support:2', 'Приватный сигнал')
        await socket.send_json({'t': 'ping'})
        assert (await socket.receive_json(timeout=2))['t'] == 'pong'
        await socket.close()


class TestChatIdentityAndInbox:
    async def test_sender_identity_and_permissions(self, client, student_token, admin_token):
        _, joined = await join(client, student_token, 'Cosmos_9')
        room = joined['data']['rooms'][0]['id']
        await join(client, admin_token, 'Vlad_9')
        _, sent = await send(client, admin_token, room, 'От администратора')
        admin_mid = sent['data']['id']
        assert sent['data']['mine']
        await post(client, '/api/chat/identity', admin_token, {'as_admin': False})
        listing = (await (await client.get(f'{BASE}/api/chat/rooms/{room}/messages', params={'token': admin_token})).json())['data']
        message = next(m for m in listing['messages'] if m['id'] == admin_mid)
        assert not message['mine'] and message['admin'] and message['author'] == 'admin'
        assert (await post(client, f'/api/chat/messages/{admin_mid}/moderate', admin_token, {'action': 'delete'}))[0] == 403
        assert (await client.get(f'{BASE}/api/chat/messages/{admin_mid}/reads', params={'token': admin_token})).status == 403
        _, personal = await send(client, admin_token, room, 'От моего профиля')
        assert personal['data']['mine'] and not personal['data']['admin']
        await post(client, '/api/chat/identity', admin_token, {'as_admin': True})
        listing = (await (await client.get(f'{BASE}/api/chat/rooms/{room}/messages', params={'token': admin_token})).json())['data']
        assert not next(m for m in listing['messages'] if m['id'] == personal['data']['id'])['mine']

    async def test_private_inbox_only_started_rooms_and_admin_can_start(self, client, student_token, admin_token, app_context, users):
        await join(client, student_token, 'Cosmos_9')
        await join(client, admin_token, 'Vlad_9')
        other = await make_student(app_context, users, 60, 'Иван')
        await join(client, other, 'Moon_9')
        async def admin_rooms():
            return (await (await client.get(f'{BASE}/api/chat', params={'token': admin_token})).json())['data']['rooms']
        assert not [r for r in await admin_rooms() if r['kind'] == 'support']
        assert (await send(client, admin_token, 'support:1', 'Самому себе'))[0] == 404
        assert (await client.get(f'{BASE}/api/chat/participants', params={'token': student_token})).status == 403
        participants = (await (await client.get(f'{BASE}/api/chat/participants', params={'token': admin_token})).json())['data']['items']
        assert {item['id'] for item in participants} == {2, 60}
        assert {item['nick'] for item in participants} == {'Cosmos_9', 'Moon_9'}
        # Ученик написал первым — появилась только его переписка.
        await send(client, student_token, 'support:2', 'Прошу помощь')
        assert [r['id'] for r in await admin_rooms() if r['kind'] == 'support'] == ['support:2']
        # Админ может начать переписку без первого сообщения ученика.
        assert (await send(client, admin_token, 'support:60', 'Здравствуйте'))[0] == 200
        assert {r['id'] for r in await admin_rooms() if r['kind'] == 'support'} == {'support:2', 'support:60'}
        await post(client, '/api/chat/identity', admin_token, {'as_admin': False})
        assert not [r for r in await admin_rooms() if r['kind'] == 'support']
        assert (await client.get(f'{BASE}/api/chat/participants', params={'token': admin_token})).status == 403

    @pytest.mark.parametrize('nick', ['admin', 'Admin123', 'a_d_m_i_n', 'Aдmиn', 'администратор', 'модер', 'm0der', 'мoдератор', 'moderator', 'mod_99', 'support_9'])
    async def test_role_nicks_rejected(self, client, student_token, nick):
        assert (await join(client, student_token, nick))[0] == 400
