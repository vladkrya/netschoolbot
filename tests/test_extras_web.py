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

    async def test_time_reminder_and_edit(self, client, token):
        response = await client.post(f"{BASE}/api/notes", headers=auth(token),
                                     json={"date": "2026-10-15", "text": "Кружок", "time": "16:30", "remind": 15})
        note = (await response.json())["data"]
        assert (note["time"], note["remind"]) == ("16:30", 15)
        response = await client.post(f"{BASE}/api/notes/{note['id']}", headers=auth(token),
                                     json={"text": "Шахматы", "date": "2026-10-16", "subject": "Физкультура",
                                           "time": "17:00", "remind": 60})
        edited = (await response.json())["data"]
        assert (edited["text"], edited["date"], edited["subject"], edited["time"], edited["remind"]) == (
            "Шахматы", "2026-10-16", "Физкультура", "17:00", 60)
        response = await client.post(f"{BASE}/api/notes/{note['id']}", headers=auth(token),
                                     json={"time": "", "remind": None})
        assert (await response.json())["data"]["time"] == ""
        for body in ({"time": "25:00"}, {"time": "9:00"}, {"remind": 7}, {"remind": True},
                     {"date": "вчера"}, {"text": " "}, {}):
            response = await client.post(f"{BASE}/api/notes/{note['id']}", headers=auth(token), json=body)
            assert response.status == 400, body
        response = await client.post(f"{BASE}/api/notes", headers=auth(token),
                                     json={"date": "2026-10-15", "text": "x", "remind": 3})
        assert response.status == 400

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
        assert data["followers"] == [{"id": 2, "name": "Папа", "notify": True, "view": True}]
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

    async def test_other_report(self, client, token, app_context):
        response = await client.get(f"{BASE}/api/export/period.xlsx?report=lessons", headers=auth(token))
        assert response.status == 200
        assert "%D0%A3%D1%80%D0%BE%D0%BA%D0%B8" in response.headers["Content-Disposition"]  # «Уроки»
        response = await client.get(f"{BASE}/api/export/period.xlsx?report=secret", headers=auth(token))
        assert response.status == 400

    async def test_bad_scope(self, client, token):
        response = await client.get(f"{BASE}/api/export.xlsx?scope=forever", headers=auth(token))
        assert response.status == 400


class TestFamilyViewing:
    """Родной с правом просмотра видит дневник ученика, но ничего не меняет."""

    @staticmethod
    async def invite(client, app_context, viewer_token, *, notify=True, view=True):
        code = await app_context.family.issue_invite(1, notify=notify, view=view)
        response = await client.post(f"{BASE}/api/family/accept", headers=auth(viewer_token), json={"code": code})
        assert response.status == 200, await response.text()
        return response

    async def test_viewer_without_diary_reads_owner_only(self, client, token, app_context, users):
        from app.domain.models import School
        from tests.test_repositories import make_user

        await users.save(make_user(telegram_id=2, school=School("", ""), enabled=False))
        viewer = await app_context.miniapp.issue_token(2)
        account = (await (await client.get(f"{BASE}/api/account", headers=auth(viewer))).json())["data"]
        assert account["needs_sgo"] is True
        await self.invite(client, app_context, viewer, notify=False)
        account = (await (await client.get(f"{BASE}/api/account", headers=auth(viewer))).json())["data"]
        assert account["needs_sgo"] is False

        profile = (await (await client.get(f"{BASE}/api/profile", headers=auth(viewer))).json())["data"]
        assert profile["readonly"] is True and profile["name"] == "Иванов И."
        # Своего дневника нет — переключаться не на что, кроме ученика.
        assert [item["id"] for item in profile["views"]] == [1]

        for path, body in (("/api/notes", {"date": "2026-10-15", "text": "x"}),
                           ("/api/settings/notifications", {"key": "grades", "value": False}),
                           ("/api/homework/done", {"key": "k", "done": True}),
                           ("/api/family/invite", {})):
            response = await client.post(f"{BASE}{path}", headers=auth(viewer), json=body)
            if path == "/api/family/invite":
                # Своё приглашение — про свой дневник, а его нет.
                assert response.status == 400
                continue
            assert response.status == 403, path
            assert (await response.json())["reason"] == "view"
        assert (await client.get(f"{BASE}/api/mail", headers=auth(viewer))).status == 403

        # Оформление — своё, у ученика не меняется.
        response = await client.post(f"{BASE}/api/prefs", headers=auth(viewer), json={"theme": "dark"})
        assert response.status == 200
        assert "theme" not in await app_context.miniapp.prefs(1)

        # Ученик забрал просмотр — дневника больше нет.
        await client.post(f"{BASE}/api/family/rights", headers=auth(token), json={"id": 2, "notify": True, "view": False})
        profile = (await (await client.get(f"{BASE}/api/profile", headers=auth(viewer))).json())["data"]
        assert profile["readonly"] is False and profile["views"] == []

    async def test_viewer_with_own_diary_switches(self, client, token, app_context, users):
        from app.domain.models import School, Student
        from tests.test_repositories import make_user

        await users.save(make_user(telegram_id=2, school=School("https://other.example.ru", "Школа №2"),
                                   student_name="Петров П.", selected_student_id=7,
                                   available_students=(Student(7, "Петров П."),)))
        viewer = await app_context.miniapp.issue_token(2)
        await self.invite(client, app_context, viewer)
        # Только что принятый дневник открывается сразу.
        profile = (await (await client.get(f"{BASE}/api/profile", headers=auth(viewer))).json())["data"]
        assert profile["readonly"] is True
        assert [(item["id"], item["current"]) for item in profile["views"]] == [(None, False), (1, True)]

        assert (await client.post(f"{BASE}/api/view", headers=auth(viewer), json={"id": None})).status == 200
        profile = (await (await client.get(f"{BASE}/api/profile", headers=auth(viewer))).json())["data"]
        assert profile["readonly"] is False and profile["name"] == "Петров П."
        assert (await client.post(f"{BASE}/api/view", headers=auth(viewer), json={"id": 999})).status == 403

        # Отказался — доступа нет.
        await client.post(f"{BASE}/api/family/remove", headers=auth(viewer), json={"id": 1, "leave": True})
        assert (await client.post(f"{BASE}/api/view", headers=auth(viewer), json={"id": 1})).status == 403

    async def test_same_student_is_not_offered(self, client, token, app_context, users):
        from app.domain.models import Student
        from tests.test_repositories import make_user

        owner = await users.get(1)
        await users.save(make_user(telegram_id=1, student_name="Иванов И.", selected_student_id=5,
                                   available_students=(Student(5, "Иванов И."),)))
        await users.save(make_user(telegram_id=2, school=owner.school, selected_student_id=5,
                                   available_students=(Student(5, "Иванов И."),)))
        viewer = await app_context.miniapp.issue_token(2)
        await self.invite(client, app_context, viewer)
        profile = (await (await client.get(f"{BASE}/api/profile", headers=auth(viewer))).json())["data"]
        assert profile["readonly"] is False and profile["views"] == []

    async def test_invite_page_info_is_public_and_link_points_to_site(self, client, token, app_context):
        response = await client.post(f"{BASE}/api/family/invite", headers=auth(token),
                                     json={"notify": False, "view": True})
        data = (await response.json())["data"]
        assert "/?invite=" in data["url"]
        code = data["url"].rsplit("=", 1)[1]
        info = (await (await client.get(f"{BASE}/api/family/invite-info", params={"code": code})).json())["data"]
        assert info == {"name": "Иванов И.", "school": "Школа №1", "notify": False, "view": True,
                        "telegram": info["telegram"]}
        assert (await client.get(f"{BASE}/api/family/invite-info", params={"code": "nope"})).status == 404
        response = await client.post(f"{BASE}/api/family/invite", headers=auth(token),
                                     json={"notify": False, "view": False})
        assert response.status == 400
        # Своё приглашение принять нельзя.
        response = await client.post(f"{BASE}/api/family/accept", headers=auth(token), json={"code": code})
        assert response.status == 400
