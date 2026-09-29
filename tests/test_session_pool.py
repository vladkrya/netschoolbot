"""Тесты пула сессий.

Проверяется главное свойство новой архитектуры: на пользователя приходится
одна сессия, а одновременные запросы из бота и из PWA не устраивают
параллельный вход.
"""

import asyncio
import datetime as dt

import pytest

from app.db.repositories import SessionRepository, UserRepository
from app.domain.models import Credentials, LoginType, School, User
from app.netschool.errors import NetSchoolError, Reason
from app.netschool.session import SessionPool
from app.settings import NetSchoolSettings


SETTINGS = NetSchoolSettings(
    default_check_interval=300,
    session_ttl=1800,
    http_timeout=20,
    blocked_host_ttl=600,
    qr_login_ttl=60,
    fallback_proxy="",
    proxy_hosts={"sgo.volganet.ru": "socks5://127.0.0.1:1080"},
)


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class FakeClient:
    """Заглушка netschoolpy: считает вызовы, умеет протухать."""

    instances: list["FakeClient"] = []

    def __init__(self, url, proxy=None):
        self.url = url
        self.proxy = proxy
        self.logins = 0
        self.closed = False
        self.session_status = 200
        self.imported: str | None = None
        self._student_id = None
        self.login_error: Exception | None = None
        FakeClient.instances.append(self)

    async def login(self, login, password, school):
        if self.login_error:
            raise self.login_error
        self.logins += 1
        # Реальный клиент после входа отвечает на запросы.
        self.session_status = 200

    async def _authed_get(self, path):
        return FakeResponse(self.session_status)

    def export_session(self):
        return '{"cookies": "abc"}'

    async def import_session(self, payload):
        self.imported = payload

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_clients():
    FakeClient.instances.clear()
    yield
    FakeClient.instances.clear()


def make_user(telegram_id=1, url="https://sgo.example.ru", **kwargs) -> User:
    return User(
        telegram_id=telegram_id,
        school=School(url=url, name="Школа №1"),
        credentials=kwargs.pop(
            "credentials",
            Credentials(login_type=LoginType.PASSWORD, login="ivan", password="secret"),
        ),
        enabled=True,
        **kwargs,
    )


@pytest.fixture
def pool(db):
    return SessionPool(
        SETTINGS, SessionRepository(db), client_factory=lambda url, proxy: FakeClient(url, proxy)
    )


class TestReuse:
    async def test_second_call_reuses_client(self, pool, users):
        user = make_user()
        await users.save(user)
        async with pool.acquire(user) as first:
            pass
        async with pool.acquire(user) as second:
            pass
        assert first is second
        assert first.logins == 1

    async def test_concurrent_access_logs_in_once(self, pool, users):
        """Бот и PWA обращаются одновременно — вход должен быть один."""
        user = make_user()
        await users.save(user)

        async def use():
            async with pool.acquire(user):
                await asyncio.sleep(0)

        await asyncio.gather(*(use() for _ in range(10)))
        assert len(FakeClient.instances) == 1
        assert FakeClient.instances[0].logins == 1

    async def test_different_users_get_different_clients(self, pool, users):
        a, b = make_user(1), make_user(2)
        await users.save(a)
        await users.save(b)
        async with pool.acquire(a) as ca:
            pass
        async with pool.acquire(b) as cb:
            pass
        assert ca is not cb

    async def test_client_is_not_shared_concurrently(self, pool, users):
        """netschoolpy хранит состояние в объекте — доступ должен быть
        последовательным, иначе выбранный ученик уедет между запросами."""
        user = make_user()
        await users.save(user)
        active = 0
        peak = 0

        async def use():
            nonlocal active, peak
            async with pool.acquire(user):
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(*(use() for _ in range(5)))
        assert peak == 1


class TestSessionPersistence:
    async def test_session_is_saved_after_login(self, pool, users, sessions):
        user = make_user()
        await users.save(user)
        async with pool.acquire(user):
            pass
        assert await sessions.load(1) == '{"cookies": "abc"}'

    async def test_saved_session_avoids_login(self, pool, users, sessions):
        user = make_user()
        await users.save(user)
        await sessions.save(1, '{"cookies": "saved"}')
        async with pool.acquire(user) as client:
            pass
        assert client.imported == '{"cookies": "saved"}'
        assert client.logins == 0

    async def test_updated_session_survives_restart(self, pool, users, sessions):
        user = make_user(credentials=Credentials(login_type=LoginType.ESIA))
        await users.save(user)
        await sessions.save(1, '{"cookies": "old"}')
        async with pool.acquire(user) as client:
            client.export_session = lambda: '{"cookies": "new"}'
        assert await sessions.load(1) == '{"cookies": "new"}'
        await pool.close_all()
        restarted = SessionPool(SETTINGS, sessions, client_factory=lambda url, proxy: FakeClient(url, proxy))
        async with restarted.acquire(user) as restored:
            assert restored.imported == '{"cookies": "new"}'
            assert restored.logins == 0

    async def test_checkpoint_saves_idle_esia_cookie_changes(self, pool, users, sessions):
        user = make_user(credentials=Credentials(login_type=LoginType.ESIA))
        await users.save(user)
        await sessions.save(1, '{"cookies": "old"}')
        async with pool.acquire(user) as client:
            pass
        client.export_session = lambda: '{"cookies": "keepalive"}'
        await pool.checkpoint_all()
        assert await sessions.load(1) == '{"cookies": "keepalive"}'

    async def test_stale_saved_session_falls_back_to_login(self, pool, users, sessions):
        user = make_user()
        await users.save(user)
        await sessions.save(1, '{"cookies": "expired"}')

        # Клиент отвергает восстановленную сессию 401-м.
        def factory(url, proxy):
            client = FakeClient(url, proxy)
            client.session_status = 401
            return client

        pool._client_factory = factory
        async with pool.acquire(user) as client:
            pass
        assert client.logins == 1

    async def test_invalidate_forgets_saved_session(self, pool, users, sessions):
        user = make_user()
        await users.save(user)
        async with pool.acquire(user):
            pass
        await pool.invalidate(1, forget_saved=True)
        assert await sessions.load(1) is None

    async def test_invalidate_closes_client(self, pool, users):
        user = make_user()
        await users.save(user)
        async with pool.acquire(user) as client:
            pass
        await pool.invalidate(1)
        assert client.closed is True


class TestProxy:
    async def test_proxy_applied_for_configured_host(self, pool, users):
        user = make_user(url="https://sgo.volganet.ru")
        await users.save(user)
        async with pool.acquire(user) as client:
            pass
        assert client.proxy == "socks5://127.0.0.1:1080"

    async def test_no_proxy_for_other_hosts(self, pool, users):
        user = make_user(url="https://sgo.example.ru")
        await users.save(user)
        async with pool.acquire(user) as client:
            pass
        assert client.proxy is None


class TestLoginFailures:
    async def test_gosuslugi_session_loss_asks_for_relogin(self, pool, users):
        user = make_user(
            credentials=Credentials(login_type=LoginType.ESIA, login="", password="")
        )
        await users.save(user)
        with pytest.raises(NetSchoolError) as info:
            async with pool.acquire(user):
                pass
        assert info.value.reason is Reason.AUTH
        assert "/login" in info.value.user_message

    async def test_missing_credentials_are_reported_clearly(self, pool, users):
        user = make_user(credentials=Credentials(login_type=LoginType.PASSWORD))
        await users.save(user)
        with pytest.raises(NetSchoolError) as info:
            async with pool.acquire(user):
                pass
        assert info.value.reason is Reason.AUTH

    async def test_server_error_is_classified(self, pool, users):
        user = make_user()
        await users.save(user)

        def factory(url, proxy):
            client = FakeClient(url, proxy)
            client.login_error = Exception("503 Service Unavailable")
            return client

        pool._client_factory = factory
        with pytest.raises(NetSchoolError) as info:
            async with pool.acquire(user):
                pass
        assert info.value.reason is Reason.SERVER_UNAVAILABLE

    async def test_unreachable_server_does_not_force_relogin(self, pool, users):
        """Недоступность сервера — не повод считать сессию протухшей."""
        user = make_user()
        await users.save(user)
        async with pool.acquire(user) as client:
            pass
        client.login_error = Exception("должен переиспользоваться, а не входить заново")

        original = client._authed_get

        async def flaky(path):
            raise Exception("All connection attempts failed")

        client._authed_get = flaky
        async with pool.acquire(user) as again:
            pass
        assert again is client
        assert client.logins == 1


class TestEviction:
    async def test_idle_sessions_are_closed(self, pool, users):
        user = make_user()
        await users.save(user)
        async with pool.acquire(user) as client:
            pass
        pool._entries[1].last_used = dt.datetime.now() - dt.timedelta(hours=2)
        assert await pool.evict_idle() == 1
        assert client.closed is True

    async def test_fresh_sessions_survive(self, pool, users):
        user = make_user()
        await users.save(user)
        async with pool.acquire(user):
            pass
        assert await pool.evict_idle() == 0

    async def test_close_all(self, pool, users):
        for i in (1, 2, 3):
            user = make_user(i)
            await users.save(user)
            async with pool.acquire(user):
                pass
        await pool.close_all()
        assert all(c.closed for c in FakeClient.instances)


class TestSelectedStudent:
    async def test_selected_child_is_applied(self, pool, users):
        user = make_user(selected_student_id=77)
        await users.save(user)
        async with pool.acquire(user) as client:
            assert client._student_id == 77


class TestGosuslugiSession:
    """Сессию Госуслуг без человека не восстановить — её надо беречь."""

    def esia_user(self):
        return make_user(credentials=Credentials(login_type=LoginType.ESIA, login="", password=""))

    async def test_expired_session_is_detected(self, pool, users, sessions):
        """netschoolpy 3.x сообщает о протухшей сессии SessionExpired — раньше
        это считалось неизвестной ошибкой, и мёртвый клиент выдавался за живой."""
        from netschoolpy.exceptions import SessionExpired

        user = make_user()
        await users.save(user)
        async with pool.acquire(user) as client:
            pass

        async def expired(path):
            raise SessionExpired("Сессия истекла. Авторизуйтесь заново.")

        client._authed_get = expired
        async with pool.acquire(user) as again:
            pass
        assert again is not client
        assert client.closed is True

    async def test_network_error_keeps_saved_session(self, pool, users, sessions):
        from netschoolpy.exceptions import SessionExpired

        user = self.esia_user()
        await users.save(user)
        await sessions.save(1, '{"cookies": "esia"}')

        def factory(url, proxy):
            client = FakeClient(url, proxy)

            async def flaky_import(payload):
                raise SessionExpired("Сессия истекла или невалидна: ReadTimeout timed out")

            client.import_session = flaky_import
            return client

        pool._client_factory = factory
        with pytest.raises(NetSchoolError) as info:
            async with pool.acquire(user):
                pass
        assert info.value.reason is Reason.SERVER_UNAVAILABLE
        assert await sessions.load(1) == '{"cookies": "esia"}'

    async def test_idle_gosuslugi_client_is_not_evicted(self, pool, users, sessions):
        user = self.esia_user()
        await users.save(user)
        await sessions.save(1, '{"cookies": "esia"}')
        async with pool.acquire(user) as client:
            pass
        pool._entries[1].last_used = dt.datetime.now() - dt.timedelta(hours=2)
        assert await pool.evict_idle() == 0
        assert client.closed is False
