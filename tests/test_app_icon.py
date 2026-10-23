"""Своя иконка приложения (web/app_icon.py): проверка владельцем, отзыв токена, пуши."""

import base64
import io

import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from app.web import app_icon
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user

ADMIN = 1  # admin_id в фикстуре app
USER = 5


@pytest.fixture
async def client(app_context):
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    yield client
    await client.close()


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(app_icon, "_push", lambda app, uid, title, body, kind: sent.append((uid, title)))
    return sent


def picture(width=300, height=200, fmt="PNG") -> str:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, fmt)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(out.getvalue()).decode()


async def tokens(app_context, users):
    await users.save(make_user(ADMIN))
    await users.save(make_user(USER))
    return [await app_context.miniapp.issue_token(uid) for uid in (ADMIN, USER)]


def test_prepare_crops_to_square():
    png = app_icon.prepare(base64.b64decode(picture(800, 300).split(",", 1)[1]))
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (512, 512) and image.format == "PNG"
    with pytest.raises(app_icon.BadImage):
        app_icon.prepare(b"<svg></svg>")
    with pytest.raises(app_icon.BadImage):
        app_icon.prepare(base64.b64decode(picture(40, 40).split(",", 1)[1]))


async def test_icon_works_only_after_approval(client, app_context, users, pushes):
    admin, user = await tokens(app_context, users)
    get = lambda path, token: client.get(path, params={"token": token})  # noqa: E731
    post = lambda path, token, body: client.post(path, json=body, params={"token": token})  # noqa: E731

    response = await post("/mini/api/app-icon", user, {"image": picture()})
    data = (await response.json())["data"]
    assert response.status == 200 and data["pending"].startswith("data:image/png") and data["active"] is None
    assert pushes == [(ADMIN, "Новая иконка на проверку")]
    # Замена непроверенной — владельца второй раз не дёргаем.
    await post("/mini/api/app-icon", user, {"image": picture(fmt="JPEG")})
    assert len(pushes) == 1

    manifest = await (await client.get("/mini/manifest.webmanifest", params={"token": user})).json()
    assert manifest["icons"][0]["src"] == "/mini/static/icon-192.png"

    # Чужим вкладка закрыта.
    assert (await get("/mini/api/admin/icons", user)).status == 403
    assert (await post("/mini/api/admin/icons/1", user, {"decision": "approve"})).status == 403

    listing = (await (await get("/mini/api/admin/icons", admin)).json())["data"]
    assert listing["push"] is True and [item["status"] for item in listing["items"]] == ["pending"]
    assert (await (await get("/mini/api/admin", admin)).json())["data"]["icons_pending"] == 1
    icon_id = listing["items"][0]["id"]

    response = await post(f"/mini/api/admin/icons/{icon_id}", admin, {"decision": "approve"})
    assert response.status == 200 and pushes[-1] == (USER, "Иконка одобрена")
    active = (await (await get("/mini/api/app-icon", user)).json())["data"]["active"]
    assert active.startswith("/mini/app-icon/")

    manifest = await (await client.get("/mini/manifest.webmanifest", params={"token": user})).json()
    src = manifest["icons"][0]["src"]
    assert src.endswith("/192.png") and "/app-icon/" in src
    image = await client.get(src)
    assert image.status == 200 and image.content_type == "image/png"
    with Image.open(io.BytesIO(await image.read())) as png:
        assert png.size == (192, 192)
    page = await (await client.get("/mini/", params={"token": user})).text()
    assert src.replace("192", "180") in page

    # Отзыв: адрес гаснет, вместо картинки — стандартная иконка.
    response = await post(f"/mini/api/admin/icons/{icon_id}", admin, {"decision": "reject"})
    assert response.status == 200 and pushes[-1] == (USER, "Иконка не прошла проверку")
    gone = await client.get(src, allow_redirects=False)
    assert gone.status == 302 and gone.headers["Location"] == "/mini/static/icon-192.png"
    mine = (await (await get("/mini/api/app-icon", user)).json())["data"]
    assert mine == {"active": None, "pending": None, "rejected": True}
    assert (await post(f"/mini/api/admin/icons/{icon_id}", admin, {"decision": "approve"})).status == 409


async def test_new_approval_replaces_old_and_reset(client, app_context, users, pushes):
    admin, user = await tokens(app_context, users)
    repo = app_icon.AppIconRepository(app_context.db)
    post = lambda path, token, body: client.post(path, json=body, params={"token": token})  # noqa: E731

    srcs = []
    for _ in range(2):
        await post("/mini/api/app-icon", user, {"image": picture()})
        (pending,) = await repo.listing("pending")
        await post(f"/mini/api/admin/icons/{pending['id']}", admin, {"decision": "approve"})
        srcs.append(await app_icon.icon_url(app_context, "/mini", USER, 512))
    assert srcs[0] != srcs[1]
    assert (await client.get(srcs[0], allow_redirects=False)).status == 302
    assert (await client.get(srcs[1])).status == 200

    await post("/mini/api/app-icon/reset", user, {})
    assert (await client.get(srcs[1], allow_redirects=False)).status == 302
    assert await app_icon.icon_url(app_context, "/mini", USER, 180) == "/mini/static/apple-touch-icon.png"


async def test_push_toggle_and_bad_uploads(client, app_context, users, pushes):
    admin, user = await tokens(app_context, users)
    post = lambda path, token, body: client.post(path, json=body, params={"token": token})  # noqa: E731

    assert (await post("/mini/api/admin/icons/push", user, {"on": False})).status == 403
    response = await post("/mini/api/admin/icons/push", admin, {"on": False})
    assert (await response.json())["data"] == {"on": False}
    await post("/mini/api/app-icon", user, {"image": picture()})
    assert pushes == []

    assert (await post("/mini/api/app-icon", user, {"image": "data:image/png;base64,!!"})).status == 400
    bad = "data:image/png;base64," + base64.b64encode(b"not an image").decode()
    response = await post("/mini/api/app-icon", user, {"image": bad})
    assert response.status == 400 and "PNG" in (await response.json())["error"]
    assert (await client.get("/mini/app-icon/whatever/64.png")).status == 404
