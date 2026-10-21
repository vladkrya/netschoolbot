"""Отсчёт до каникул, поиск за год, сводка по детям, расписание картинкой,
«Не выходить из аккаунта» и новые настройки приложения."""

import datetime as dt
from dataclasses import replace

from app.domain.models import ApiMode, Credentials, LoginType, Student
from app.domain.records import HomeworkRecord
from tests.test_repositories import make_user
from tests.test_watcher import record
from tests.test_web import BASE, client, token  # noqa: F401 — фикстуры
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура


async def get(client, path, token, **params):
    response = await client.get(f"{BASE}{path}", params={"token": token, **params})
    return response.status, await response.json()


class TestCountdown:
    async def test_vacation_and_exam(self, client, token, app_context):
        app_context.diary.class_name = "11 Б"
        status, body = await get(client, "/api/countdown", token)
        assert status == 200
        data = body["data"]
        assert data["vacation"]["label"].endswith("каникулы")
        assert data["vacation_exact"] is False
        assert data["exam"]["name"] == "ЕГЭ"

    async def test_no_exam_for_other_classes(self, client, token, app_context):
        app_context.diary.class_name = "7А"
        _, body = await get(client, "/api/countdown", token)
        assert body["data"]["exam"] is None


class TestSearch:
    async def test_finds_homework_and_mail(self, client, token, app_context):
        today = dt.date.today()
        app_context.diary.homework = [
            HomeworkRecord("Алгебра", today - dt.timedelta(days=20), "ДЗ", "Решить уравнения № 5"),
            HomeworkRecord("Физика", today, "ДЗ", "Параграф 3"),
        ]
        app_context.diary.mail = [
            {"id": 1, "subject": "Родительское собрание", "author": "Иванова", "sent": today.isoformat(), "read": True},
            {"id": 2, "subject": "Старое письмо про уравнения", "author": "Петров", "sent": "2020-01-01", "read": True},
        ]
        status, body = await get(client, "/api/search", token, q="УРАВНЕНИЯ")
        assert status == 200
        assert [item["subject"] for item in body["data"]["homework"]] == ["Алгебра"]
        # Письмо прошлых лет не ищется.
        assert body["data"]["mail"] == []
        _, body = await get(client, "/api/search", token, q="собрание иванова")
        assert [m["id"] for m in body["data"]["mail"]] == [1]

    async def test_short_query_rejected(self, client, token):
        status, _ = await get(client, "/api/search", token, q="а")
        assert status == 400


class TestChildren:
    async def test_summary_for_each_child(self, client, app_context, users):
        await users.save(make_user(
            selected_student_id=1, student_name="Аня",
            available_students=(Student(1, "Аня"), Student(2, "Петя")),
        ))
        token = await app_context.miniapp.issue_token(1)
        today = dt.date.today()
        app_context.diary.marks = [record(0, "5", today), record(1, "•", today, subject="Химия")]
        status, body = await get(client, "/api/children", token)
        assert status == 200
        children = body["data"]["children"]
        assert [c["name"] for c in children] == ["Аня", "Петя"] and children[0]["current"]
        assert children[0]["debts"][0]["subject"] == "Химия"
        assert children[0]["count"] == 2

    async def test_single_child_gets_nothing(self, client, token):
        _, body = await get(client, "/api/children", token)
        assert body["data"]["children"] == []


class TestShareSchedule:
    async def test_png_goes_to_chat(self, app_context, users, tmp_path):
        from aiohttp.test_utils import TestClient, TestServer

        from app.web.server import create_app

        await users.save(make_user())
        app_context.settings = replace(app_context.settings, data_dir=tmp_path)
        sent = []

        class Bot:
            async def send_photo(self, chat_id, photo, caption=None):
                sent.append((chat_id, caption))

        client = TestClient(TestServer(create_app(app_context, bot=Bot())))
        await client.start_server()
        try:
            token = await app_context.miniapp.issue_token(1)
            response = await client.post(f"{BASE}/api/share/schedule", params={"token": token},
                                         data=b"\x89PNG\r\n\x1a\n" + b"0" * 100)
            assert response.status == 200
            response = await client.post(f"{BASE}/api/share/schedule", params={"token": token}, data=b"GIF89a")
            assert response.status == 400
        finally:
            await client.close()
        assert sent == [(1, "🗓 Расписание на неделю")]


class TestPrefs:
    async def test_goals_and_flags_are_validated(self, client, token):
        async def save(body):
            response = await client.post(f"{BASE}/api/prefs", params={"token": token}, json=body)
            return response.status, await response.json()

        status, body = await save({"goals": {"Алгебра": "5"}, "keepSignedIn": False, "whatsNew": "6.15.0",
                                   "countdown": "hide"})
        assert status == 200
        assert body["data"]["goals"] == {"Алгебра": "5"} and body["data"]["keepSignedIn"] is False
        for bad in ({"goals": {"Алгебра": "6"}}, {"goals": []}, {"keepSignedIn": "yes"},
                    {"whatsNew": "новое"}, {"countdown": "maybe"}):
            assert (await save(bad))[0] == 400


class TestKeepSignedIn:
    async def test_profile_shows_switch_only_for_gosuslugi(self, client, app_context, users, monkeypatch):
        monkeypatch.setattr(type(app_context.diary), "mobile_available", True, raising=False)
        await users.save(make_user(credentials=Credentials(login_type=LoginType.ESIA)))
        token = await app_context.miniapp.issue_token(1)
        _, body = await get(client, "/api/profile", token)
        assert body["data"]["keep_signed_in"] == {"available": True, "on": True, "mobile": False}
        await users.save(make_user())
        _, body = await get(client, "/api/profile", token)
        assert body["data"]["keep_signed_in"] == {"available": False}

    async def test_context_switches_live_web_session(self, app_context, users, monkeypatch):
        switched = []

        async def switch_api(user, mode):
            switched.append(mode)
            return replace(user, api_mode=mode)

        monkeypatch.setattr(type(app_context.diary), "mobile_available", True, raising=False)
        monkeypatch.setattr(app_context.diary, "switch_api", switch_api, raising=False)
        esia = await users.save(make_user(credentials=Credentials(login_type=LoginType.ESIA)))
        assert await app_context.keep_signed_in(esia) is True
        assert switched == [ApiMode.MOBILE]
        # Выключено в настройках — не трогаем.
        await app_context.miniapp.save_prefs(1, {"keepSignedIn": False})
        assert await app_context.keep_signed_in(esia) is False
        # Вход по паролю перелогинивается сам.
        assert await app_context.keep_signed_in(make_user()) is False


def test_whats_new_lists_current_version():
    """«Что нового» начинается с текущей версии — иначе точка у строки не загорится."""
    import re
    from pathlib import Path

    from app import __version__

    source = (Path(__file__).parent.parent / "app/web/static/js/whatsnew.js").read_text()
    assert re.search(r"version: '([\d.]+)'", source).group(1) == __version__
