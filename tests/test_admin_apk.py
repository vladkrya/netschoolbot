"""APK Android-клиента: скачать может только владелец, в том числе по билету вложений."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.web.api import issue_attachment_ticket
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user

ADMIN = 1  # admin_id в фикстуре app
PATH = "/mini/api/admin/android.apk"


@pytest.fixture
async def client(app_context):
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    yield client
    await client.close()


async def test_apk_only_for_owner(client, app_context, users, tmp_path, monkeypatch):
    await users.save(make_user(ADMIN))
    await users.save(make_user(5))
    admin, other = [await app_context.miniapp.issue_token(uid) for uid in (ADMIN, 5)]
    # data_dir фикстуры — общий /tmp: APK кладём в свою временную папку.
    apk = tmp_path / "android" / "dnevnik.apk"
    monkeypatch.setattr(type(app_context.settings), "android_apk", property(lambda self: apk))
    profile = lambda token: client.get("/mini/api/profile", params={"token": token})  # noqa: E731

    # Файла ещё нет: строки в настройках нет, ссылка отвечает 404.
    assert (await (await profile(admin)).json())["data"]["android_apk"] is None
    assert (await client.get(PATH, params={"token": admin})).status == 404

    apk.parent.mkdir(parents=True, exist_ok=True)
    apk.write_bytes(b"PK\x03\x04 apk")

    info = (await (await profile(admin)).json())["data"]["android_apk"]
    assert info["size"] == 8 and info["updated"].endswith("Z")
    assert (await (await profile(other)).json())["data"]["android_apk"] is None

    assert (await client.get(PATH, params={"token": other})).status == 403
    response = await client.get(PATH, params={"token": admin})
    assert response.status == 200 and await response.read() == b"PK\x03\x04 apk"
    assert response.headers["Content-Type"] == "application/vnd.android.package-archive"
    assert "dnevnik.apk" in response.headers["Content-Disposition"]

    # Без входа — 401 (cookie с токеном сервер ставит после запроса с ?token=).
    client.session.cookie_jar.clear()
    assert (await client.get(PATH)).status == 401

    # Внешний браузер приходит с билетом вложений: владельцу отдаём, чужому нет.
    response = await client.get(PATH, params={"ticket": issue_attachment_ticket(app_context, ADMIN)})
    assert response.status == 200
    response = await client.get(PATH, params={"ticket": issue_attachment_ticket(app_context, 5)})
    assert response.status == 403
