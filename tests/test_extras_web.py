"""HTTP-ручки заметок, настроек напоминаний, второго родителя и выгрузки."""

import io
import zipfile

import pytest

from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_web import BASE, client, token  # noqa: F401 — фикстуры
from tests.test_watcher import record


def auth(token):
    return {"X-Netschool-Token": token}


class TestNotesApi:
    async def test_add_toggle_delete(self, client, token):
        response = await client.post(f"{BASE}/api/notes", headers=auth(token),
                                     json={"date": "2026-10-15", "text": "Циркуль", "subject": "Геометрия"})
        note = (await response.json())["data"]
        response = await client.post(f"{BASE}/api/notes/{note['id']}", headers=auth(token), json={"done": True})
        assert response.status == 200
        listed = await client.get(f"{BASE}/api/notes?from=2026-10-01&to=2026-10-31", headers=auth(token))
        assert (await listed.json())["data"]["items"][0]["done"] is True
        response = await client.post(f"{BASE}/api/notes/{note['id']}", headers=auth(token), json={"delete": True})
        assert response.status == 200
        response = await client.post(f"{BASE}/api/notes/{note['id']}", headers=auth(token), json={"done": True})
        assert response.status == 404

    async def test_rejects_empty(self, client, token):
        response = await client.post(f"{BASE}/api/notes", headers=auth(token), json={"date": "2026-10-15", "text": " "})
        assert response.status == 400


class TestSettingsApi:
    async def test_reminder_hour_and_marks_filter(self, client, token, users):
        response = await client.post(f"{BASE}/api/settings/reminder", headers=auth(token), json={"hour": 25})
        assert (await response.json())["data"]["reminder_hour"] == 21
        response = await client.post(f"{BASE}/api/settings/marks-filter", headers=auth(token), json={"values": [2, 3]})
        assert (await response.json())["data"]["marks_filter"] == [2, 3]
        response = await client.post(f"{BASE}/api/settings/marks-filter", headers=auth(token), json={"values": [7]})
        assert response.status == 400
        assert (await users.get(1)).notifications.marks_filter == (2, 3)

    async def test_new_toggles(self, client, token, users):
        response = await client.post(f"{BASE}/api/settings/notifications", headers=auth(token),
                                     json={"key": "homework_reminder", "value": True})
        assert response.status == 200
        assert (await users.get(1)).notifications.homework_reminder


class TestFamilyApi:
    async def test_lists_and_removes(self, client, token, app_context, users):
        from tests.test_repositories import make_user

        await users.save(make_user(telegram_id=2))
        code = await app_context.family.issue_invite(1)
        await app_context.family.accept(code, 2, "Папа")
        data = (await (await client.get(f"{BASE}/api/family", headers=auth(token))).json())["data"]
        assert data["followers"] == [{"id": 2, "name": "Папа"}]
        data = (await (await client.post(f"{BASE}/api/family/remove", headers=auth(token), json={"id": 2})).json())["data"]
        assert data["followers"] == []


class TestExportApi:
    async def test_download(self, client, token, app_context):
        app_context.diary.marks = [record(0), record(1, "4")]
        response = await client.get(f"{BASE}/api/export.xlsx?scope=period", headers=auth(token))
        assert response.status == 200
        assert "filename" in response.headers["Content-Disposition"]
        with zipfile.ZipFile(io.BytesIO(await response.read())) as book:
            assert "xl/worksheets/sheet1.xml" in book.namelist()

    async def test_opens_by_ticket_like_attachment(self, client, token, app_context):
        # Выгрузка открывается во внешнем браузере, как вложение из ДЗ:
        # сперва prepare собирает книгу, затем прямая ссылка отдаёт её просмотром.
        from app.web.api import issue_attachment_ticket

        app_context.diary.marks = [record(0), record(1, "4")]
        ticket = issue_attachment_ticket(app_context, 1)
        response = await client.get(f"{BASE}/api/export/year.xlsx", params={"ticket": ticket, "prepare": "1"})
        assert (await response.json())["data"]["size"] > 0
        app_context.diary.marks = []
        response = await client.get(f"{BASE}/api/export/year.xlsx", params={"ticket": ticket})
        assert response.status == 200
        assert response.headers["Content-Disposition"].startswith("inline;")
        with zipfile.ZipFile(io.BytesIO(await response.read())) as book:
            assert "xl/worksheets/sheet1.xml" in book.namelist()
        assert (await client.get(f"{BASE}/api/export/year.xlsx", params={"ticket": "garbage"})).status == 401
        assert (await client.get(f"{BASE}/api/export/forever.xlsx", params={"ticket": ticket})).status == 400

    async def test_bad_scope(self, client, token):
        response = await client.get(f"{BASE}/api/export.xlsx?scope=forever", headers=auth(token))
        assert response.status == 400
