"""Один аккаунт «Сетевого города» — один аккаунт приложения (netschool/ownership.py)."""

from dataclasses import replace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.netschool import login as login_flow
from app.netschool import ownership
from app.netschool.ownership import AccountTaken
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user

URL = "https://sgo.example.ru/"
ADMIN = 1  # admin_id в фикстуре app


class FakeWebClient:
    """Клиент входа netschoolpy: /context отдаёт id пользователя СГ."""

    def __init__(self, ns_user_id):
        self.ns_user_id = ns_user_id
        self.logged_in = False

    async def login(self, **kwargs):
        self.logged_in = True

    async def _authed_get(self, path):
        assert path == "context"
        ns_user_id = self.ns_user_id

        class Response:
            def json(self):
                return {"userId": ns_user_id}

        return Response()

    def export_session(self):
        return '{"session": 1}'


@pytest.fixture
def ns_client(monkeypatch):
    holder = {"id": 500}
    monkeypatch.setattr(login_flow, "_client", lambda app, url: FakeWebClient(holder["id"]))
    return holder


class TestClaim:
    async def test_first_takes_then_others_refused(self, app_context, users):
        for uid in (10, 11):
            await users.get_or_create(uid)
        await ownership.claim(app_context, 10, URL, 500)
        with pytest.raises(AccountTaken):
            await ownership.claim(app_context, 11, "https://SGO.example.ru", 500)
        # Свой же СГ заново и другой СГ — можно.
        await ownership.claim(app_context, 10, URL, 500)
        await ownership.claim(app_context, 11, URL, 501)
        assert await app_context.netschool_accounts.holders("https://sgo.example.ru", 500) == [10]

    async def test_one_app_account_holds_one_netschool_account(self, app_context, users):
        await users.get_or_create(10)
        await users.get_or_create(11)
        await ownership.claim(app_context, 10, URL, 500)
        # 10 ушёл в другой СГ — первый освободился.
        await ownership.claim(app_context, 10, URL, 600)
        await ownership.claim(app_context, 11, URL, 500)
        assert await app_context.netschool_accounts.get(10) == ("https://sgo.example.ru", 600)

    async def test_unknown_id_does_not_block(self, app_context, users):
        await users.get_or_create(10)
        await ownership.claim(app_context, 10, URL, None)
        assert await app_context.netschool_accounts.get(10) is None

    async def test_admin_shares_own_netschool_by_switch(self, app_context, users):
        for uid in (ADMIN, 11, 12):
            await users.get_or_create(uid)
        await ownership.claim(app_context, ADMIN, URL, 500)
        with pytest.raises(AccountTaken):
            await ownership.claim(app_context, 11, URL, 500)
        await ownership.set_sharing(app_context, True)
        await ownership.claim(app_context, 11, URL, 500)
        await ownership.set_sharing(app_context, False)
        # Кто подключился, пока было можно, остаётся; новые — нет.
        await ownership.claim(app_context, 11, URL, 500)
        with pytest.raises(AccountTaken):
            await ownership.claim(app_context, 12, URL, 500)

    async def test_switch_does_not_open_other_peoples_netschool(self, app_context, users):
        for uid in (ADMIN, 10, 11):
            await users.get_or_create(uid)
        await ownership.set_sharing(app_context, True)
        await ownership.claim(app_context, 10, URL, 500)
        with pytest.raises(AccountTaken):
            await ownership.claim(app_context, 11, URL, 500)

    async def test_released_on_logout_and_delete(self, app_context, users):
        await users.save(make_user(10))
        await users.get_or_create(11)
        await ownership.claim(app_context, 10, URL, 500)
        await app_context.sign_out_netschool(await users.get(10))
        await ownership.claim(app_context, 11, URL, 500)
        await users.delete(11)
        assert await app_context.netschool_accounts.holders("https://sgo.example.ru", 500) == []


class TestLogin:
    async def test_taken_account_gives_no_session(self, app_context, users, ns_client):
        await users.get_or_create(10)
        await users.get_or_create(11)
        assert await login_flow.password_login(app_context, make_user(10, school=replace(
            make_user(10).school, url=URL))) == '{"session": 1}'
        with pytest.raises(AccountTaken):
            await login_flow.password_login(app_context, make_user(11))
        ns_client["id"] = 777
        assert await login_flow.password_login(app_context, make_user(11)) == '{"session": 1}'


class TestBackfill:
    async def test_existing_duplicates_keep_working(self, app_context, users, sessions, monkeypatch):
        for uid in (10, 11, 12):
            await users.save(make_user(uid))
        for uid in (10, 11):
            await sessions.save(uid, '{"s": 1}')  # у 12 сессии нет — его не трогаем

        async def fetch_account_id(user):
            return 500

        monkeypatch.setattr(app_context.diary, "fetch_account_id", fetch_account_id, raising=False)
        assert await ownership.backfill(app_context, pause=0) == 2
        assert await ownership.backfill(app_context, pause=0) == 0
        # Оба старых дубля входят заново, новый — нет.
        await ownership.claim(app_context, 10, URL, 500)
        await ownership.claim(app_context, 11, URL, 500)
        with pytest.raises(AccountTaken):
            await ownership.claim(app_context, 12, URL, 500)


class TestShareSwitch:
    @pytest.fixture
    async def client(self, app_context):
        client = TestClient(TestServer(create_app(app_context)))
        await client.start_server()
        yield client
        await client.close()

    async def test_only_admin_sees_and_changes_it(self, client, app_context, users):
        await users.save(make_user(ADMIN))
        await users.save(make_user(5))
        admin, other = [await app_context.miniapp.issue_token(uid) for uid in (ADMIN, 5)]

        async def info(token):
            return (await (await client.get("/mini/api/account", params={"token": token})).json())["data"]

        assert (await info(admin))["share_sgo"] is False
        assert (await info(other))["share_sgo"] is None
        response = await client.post("/mini/api/account/share-sgo", json={"on": True}, params={"token": other})
        assert response.status == 403 and not await ownership.sharing_allowed(app_context)
        response = await client.post("/mini/api/account/share-sgo", json={"on": True}, params={"token": admin})
        assert response.status == 200 and (await response.json())["data"]["share_sgo"] is True
        assert await ownership.sharing_allowed(app_context)
