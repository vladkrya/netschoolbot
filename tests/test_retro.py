"""Ретро-сайт (app/web/retro.py): страница и ответы в форме сервера netschool-retro."""

import datetime as dt
from types import SimpleNamespace

from app.netschool.errors import NetSchoolError, Reason
from app.web import retro
from tests.test_web import BASE, client, token  # noqa: F401 — фикстуры
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура


def use_client(monkeypatch, app_context, fake):
    async def with_session(user, call):
        return await call(fake)
    monkeypatch.setattr(app_context.diary, "with_client", with_session, raising=False)


class FakeClient:
    current_student = SimpleNamespace(name="Иванов Иван", group_name="7А")

    async def diary(self, start, end):
        lesson = SimpleNamespace(
            day=start, start=dt.time(8, 30), end=dt.time(9, 10), number=1, subject="Алгебра",
            room="12", teacher="Петрова Анна Сергеевна", topic="Дроби", absence="",
            is_distance=False, is_elective=False,
            assignments=[SimpleNamespace(id=5, kind="Контрольная работа", content="Вариант 1",
                                         mark_text="", mark=5, deadline=start)],
        )
        return SimpleNamespace(schedule=[SimpleNamespace(lessons=[lesson])], class_name="", term_name="1 четверть")


async def test_page_renders_their_markup(client):
    response = await client.get(f"{BASE}/retro?demo=1")
    text = await response.text()
    assert response.status == 200
    assert "Котики делают" in text and 'id="grid"' in text
    assert "/retro/bridge.js" in text and "/retro/app.js" in text
    assert '"demo": true' in text


async def test_week_in_their_shape(client, token, app_context, monkeypatch):
    use_client(monkeypatch, app_context, FakeClient())
    response = await client.get(f"{BASE}/api/retro/week?start=2026-10-21", headers={"X-Netschool-Token": token})
    week = await response.json()
    assert week["start"] == "2026-10-19" and week["end"] == "2026-10-24"
    assert week["class"] == "7А" and week["source"] == "diary"
    lesson = week["lessons"][0]
    assert lesson["start"] == "08:30" and lesson["teacher"] == "Петрова А.С."
    assert lesson["assignments"] == [{"id": 5, "kind": "Контрольная работа", "content": "Вариант 1",
                                      "mark": "5", "due": "2026-10-19"}]


async def test_me_and_errors_as_detail(client, token, app_context, monkeypatch):
    use_client(monkeypatch, app_context, FakeClient())
    response = await client.get(f"{BASE}/api/retro/me", headers={"X-Netschool-Token": token})
    assert await response.json() == {"name": "Иванов Иван", "class": "7А", "mode": "web"}

    async def broken(user, call):
        raise NetSchoolError(Reason.AUTH, "Войдите заново")
    monkeypatch.setattr(app_context.diary, "with_client", broken, raising=False)
    response = await client.get(f"{BASE}/api/retro/me", headers={"X-Netschool-Token": token})
    assert response.status == 401
    assert "detail" in await response.json()


async def test_reports_need_mobile_api(client, token, app_context, monkeypatch):
    use_client(monkeypatch, app_context, FakeClient())
    response = await client.get(f"{BASE}/api/retro/reports/totals", headers={"X-Netschool-Token": token})
    assert response.status == 501


async def test_requires_authentication(client):
    response = await client.get(f"{BASE}/api/retro/week")
    assert response.status == 401


async def test_guestbook_post_reply_delete(client, token, monkeypatch):
    monkeypatch.setattr(retro, "MIN_FILL", 0)
    monkeypatch.setattr(retro, "COOLDOWN", 0)
    headers = {"X-Netschool-Token": token}
    url = f"{BASE}/api/retro/guestbook"
    book = await (await client.get(url, headers=headers)).json()
    assert book["entries"] == [] and book["can_post"] is True
    response = await client.post(url, headers=headers, json={"name": "Ваня", "text": "Привет!", "token": book["token"]})
    first = await response.json()
    assert response.status == 200 and first["mine"] is True
    # Токен формы одноразовый.
    response = await client.post(url, headers=headers, json={"name": "Ваня", "text": "Ещё", "token": book["token"]})
    assert response.status == 400
    book = await (await client.get(url, headers=headers)).json()
    response = await client.post(url, headers=headers, json={
        "name": "Ваня", "text": "Ответ", "parent": first["id"], "token": book["token"]})
    assert (await response.json())["parent"] == first["id"]
    # Ссылки и ловушка для ботов отклоняются.
    book = await (await client.get(url, headers=headers)).json()
    response = await client.post(url, headers=headers, json={"name": "Ваня", "text": "https://x.ru", "token": book["token"]})
    assert response.status == 400
    response = await client.post(url, headers=headers, json={"name": "a", "text": "b", "token": "x", "website": "1"})
    assert response.status == 400
    response = await client.delete(f"{url}/{first['id']}", headers=headers)
    assert response.status == 200
    book = await (await client.get(url, headers=headers)).json()
    assert book["entries"] == []  # ветка ушла вместе с ответом


async def test_retro_pref_is_validated(client, token):
    url = f"{BASE}/api/prefs"
    response = await client.post(url, params={"token": token}, json={"retro": "on"})
    assert (await response.json())["data"]["retro"] == "on"
    response = await client.post(url, params={"token": token}, json={"retro": True})
    assert (await response.json())["ok"] is False
