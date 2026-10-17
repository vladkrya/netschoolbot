"""Статистика администратора: способ входа, где открывают приложение, дизайн."""

import time
from types import SimpleNamespace

import aiosqlite

from aiohttp.test_utils import TestClient, TestServer

from app.accounts import WEB_ID_BASE
from app.bot.handlers.admin import admin_stats
from app.db.migrations import MIGRATIONS, migrate
from app.web.server import create_app
from tests.test_bot_handlers import FakeMessage
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user


async def test_visits_are_counted_for_admin(app_context, users):
    await users.save(make_user(1))
    await users.save(make_user(2))
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        for uid, design, where in ((1, "new", "telegram"), (2, "classic", "pwa")):
            token = await app_context.miniapp.issue_token(uid)
            response = await client.post("/mini/api/visit", params={"token": token},
                                         json={"design": design, "client": where})
            assert response.status == 200
        bad = await client.post("/mini/api/visit", params={"token": token}, json={"design": "x", "client": "web"})
        assert bad.status == 400
    finally:
        await client.close()

    message = FakeMessage("/adminstats")
    message.from_user = SimpleNamespace(id=2)
    await admin_stats(message, app_context)
    assert message.answers == []  # не администратор — молчим

    message.from_user = SimpleNamespace(id=1)  # TG_ADMIN_ID в тестах
    await admin_stats(message, app_context)
    text = message.answers[0][0]
    assert "за сутки 2" in text
    assert "новый 1 (50%)" in text and "старый 1 (50%)" in text
    assert "только в Telegram: <b>1</b> (50%)" in text and "только в браузере: <b>1</b> (50%)" in text
    assert "Установленное приложение (экран «Домой»): 1" in text
    assert "только Telegram: <b>2</b> (100%) · +2" in text
    assert "Опрос" not in text  # пока никому не показан

    await users.save(make_user(3))
    await app_context.db.execute("INSERT INTO app_survey (telegram_id) VALUES (1), (2), (3)")
    for uid, score in ((1, 9), (2, 6)):
        assert await app_context.miniapp.survey_pending(uid)
        assert await app_context.miniapp.answer_survey(uid, score)
    assert await app_context.miniapp.survey_pending(3)
    assert await app_context.miniapp.decline_survey(3)
    message = FakeMessage("/adminstats")
    message.from_user = SimpleNamespace(id=1)
    await admin_stats(message, app_context)
    text = message.answers[0][0]
    assert "показан: 3, ответили: 2 (67%), отказались: 1" in text
    assert "средняя оценка: 7.5 из 10" in text and "оценки: 6 — 1, 9 — 1" in text


async def test_audience_by_login_and_place(app_context, users):
    miniapp, accounts, db = app_context.miniapp, app_context.accounts, app_context.db
    now = time.time()
    for uid in (1, 2, 3, 4):
        await users.save(make_user(uid))
    await db.execute("UPDATE users SET created_at = '2020-01-01 00:00:00' WHERE telegram_id IN (3, 4)")
    web = await accounts.create_web_user("web@example.ru", "hash")
    assert web is not None and web > WEB_ID_BASE
    await accounts.mark_verified(web)
    assert await accounts.attach(2, "tg@example.ru", "hash")  # Telegram и почта, не подтверждена

    await miniapp.record_visit(1, "new", "telegram")
    await miniapp.record_visit(1, "new", "web")  # был и в Telegram, и в браузере
    await miniapp.record_visit(2, "new", "telegram")
    await miniapp.record_visit(web, "new", "pwa")
    # Давний пользователь: открывал давно и только в браузере.
    old = now - 40 * 86400
    await db.execute("INSERT INTO app_clients VALUES (3, 'web', ?, ?)", (old, old))

    stats = await miniapp.audience_stats(now, WEB_ID_BASE)
    assert stats["login"] == {"telegram": {"n": 3, "new": 1}, "both": {"n": 1, "new": 1},
                              "email": {"n": 1, "new": 1}}
    assert stats["email"] == {"linked": 2, "verified": 1}
    assert stats["places"]["all"] == {"telegram": {"n": 1, "new": 1, "pwa": 0},
                                      "both": {"n": 1, "new": 1, "pwa": 0},
                                      "browser": {"n": 2, "new": 1, "pwa": 1}}
    assert stats["places"]["month"]["browser"] == {"n": 1, "new": 1, "pwa": 1}

    message = FakeMessage("/adminstats")
    message.from_user = SimpleNamespace(id=1)
    await admin_stats(message, app_context)
    text = message.answers[0][0]
    assert "👥 <b>Пользователи: 5</b>" in text and "новых за 7 дней: +3" in text
    assert "только Telegram: <b>3</b> (60%) · +1" in text
    assert "Telegram и почта: <b>1</b> (20%) · +1" in text
    assert "только почта: <b>1</b> (20%) · +1" in text
    assert "Почта привязана: 2, подтверждена: 1 (50%)" in text
    assert "<u>За всё время</u>: 4" in text and "<u>За 30 дней</u>: 3" in text
    assert "в Telegram и в браузере: <b>1</b> (25%) · +1" in text
    assert "только в браузере: <b>2</b> (50%) · +1" in text


async def test_places_migration_keeps_last_known_place():
    async with aiosqlite.connect(":memory:") as db:
        target = next(i for i, script in enumerate(MIGRATIONS) if "CREATE TABLE app_clients" in script)
        for version, script in enumerate(MIGRATIONS[:target], 1):
            await db.executescript(script)
            await db.execute(f"PRAGMA user_version = {version}")
        await db.execute("INSERT INTO users (telegram_id) VALUES (1), (2)")
        await db.execute("INSERT INTO app_visits VALUES (1, 'new', 'telegram', 10, 20), (2, 'classic', 'pwa', 30, 40)")
        await db.commit()
        await migrate(db)
        async with db.execute("SELECT * FROM app_clients ORDER BY telegram_id") as cursor:
            assert await cursor.fetchall() == [(1, "telegram", 10, 20), (2, "pwa", 30, 40)]
