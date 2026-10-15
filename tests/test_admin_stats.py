"""Статистика администратора: открытия приложения и дизайн."""

from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from app.bot.handlers.admin import admin_stats
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
    assert "за сутки: 2" in text
    assert "новый 1 (50%)" in text and "старый 1 (50%)" in text
    assert "в Telegram 1" in text and "установленное приложение 1" in text
    assert "Опрос" not in text  # пока никому не показан

    await app_context.db.execute("INSERT INTO app_survey (telegram_id) VALUES (1), (2)")
    for uid, score in ((1, 9), (2, 6)):
        assert await app_context.miniapp.claim_survey(uid)
        assert await app_context.miniapp.answer_survey(uid, score)
    message = FakeMessage("/adminstats")
    message.from_user = SimpleNamespace(id=1)
    await admin_stats(message, app_context)
    text = message.answers[0][0]
    assert "Показан: 2, ответили: 2" in text and "средняя оценка: 7.5 из 10" in text and "6: 1, 9: 1" in text
