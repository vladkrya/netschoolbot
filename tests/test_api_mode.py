"""Переключение между веб- и мобильным API (/changeapi)."""

import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.db.repositories import SessionRepository, UserRepository
from app.domain import formatting
from app.domain.models import ApiMode, Credentials, LoginType
from app.domain.records import HomeworkRecord
from app.netschool.errors import NetSchoolError
from app.netschool.mapping import homework_from_day
from app.netschool.service import DiaryService
from app.netschool.session import SessionPool
from tests.test_session_pool import SETTINGS, FakeClient, make_user, reset_clients  # noqa: F401

MOBILE_SETTINGS = replace(SETTINGS, mobile_client_secret="cs")
MSK = dt.timezone(dt.timedelta(hours=3))


class FakeMobile(FakeClient):
    """Заглушка MobileNetSchool: отличается от веб-клиента маршрутом classmeetings."""

    def __init__(self, url, proxy=None, secret=""):
        super().__init__(url, proxy)
        self.secret = secret

    async def classmeetings(self, *args):
        return []

    async def is_alive(self):
        return self.session_status == 200

    def export_session(self):
        return '{"kind": "mobile"}'


@pytest.fixture
def from_web(monkeypatch):
    """Подменить MobileNetSchool.from_web: код из веб-сессии → мобильный клиент."""
    import netschoolpy

    calls = []

    async def fake_from_web(web, *, client_secret=None, timeout=None):
        calls.append((web, client_secret))
        if getattr(web, "pincode_error", None):
            raise web.pincode_error
        return FakeMobile(web.url)

    monkeypatch.setattr(netschoolpy.MobileNetSchool, "from_web", staticmethod(fake_from_web), raising=False)
    return calls


def pool_for(db, settings=MOBILE_SETTINGS) -> SessionPool:
    return SessionPool(settings, SessionRepository(db), client_factory=FakeClient, mobile_factory=FakeMobile)


async def test_api_mode_is_persisted(db):
    users = UserRepository(db)
    await users.save(make_user(api_mode=ApiMode.MOBILE))
    assert (await users.get(1)).api_mode is ApiMode.MOBILE
    await users.save(make_user())
    assert (await users.get(1)).api_mode is ApiMode.WEB


async def test_switch_to_mobile_uses_live_web_session(db, from_web):
    pool = pool_for(db)
    user = await UserRepository(db).save(make_user())
    async with pool.acquire(user) as web:
        pass

    await pool.switch_api(user, ApiMode.MOBILE)

    assert from_web == [(web, "cs")]
    assert web.closed
    async with pool.acquire(replace(user, api_mode=ApiMode.MOBILE)) as client:
        assert isinstance(client, FakeMobile)
    assert await SessionRepository(db).load(1) == '{"kind": "mobile"}'


async def test_switch_to_mobile_falls_back_to_password(db, from_web):
    pool = pool_for(db)
    user = await UserRepository(db).save(make_user())
    async with pool.acquire(user) as web:
        web.pincode_error = RuntimeError("pincode 404")

    await pool.switch_api(user, ApiMode.MOBILE)

    mobile = [c for c in FakeClient.instances if isinstance(c, FakeMobile)]
    assert len(mobile) == 1 and mobile[0].logins == 1 and mobile[0].secret == "cs"


async def test_gosuslugi_user_cannot_fall_back_to_password(db, from_web):
    pool = pool_for(db)
    user = await UserRepository(db).save(make_user(credentials=Credentials(login_type=LoginType.ESIA)))
    await SessionRepository(db).save(1, '{"cookies": "esia"}')
    async with pool.acquire(user) as web:
        web.pincode_error = RuntimeError("pincode 404")

    with pytest.raises(NetSchoolError):
        await pool.switch_api(user, ApiMode.MOBILE)


async def test_mobile_needs_client_secret(db, from_web):
    pool = pool_for(db, settings=SETTINGS)
    assert not pool.mobile_available
    with pytest.raises(NetSchoolError):
        await pool.switch_api(make_user(), ApiMode.MOBILE)


async def test_back_to_web_forgets_token(db, from_web):
    pool = pool_for(db)
    user = await UserRepository(db).save(make_user())
    await pool.switch_api(user, ApiMode.MOBILE)
    await pool.switch_api(replace(user, api_mode=ApiMode.MOBILE), ApiMode.WEB)

    assert await SessionRepository(db).load(1) is None
    async with pool.acquire(user) as client:
        assert not isinstance(client, FakeMobile)


async def test_pool_replaces_client_of_other_mode(db):
    pool = pool_for(db)
    user = make_user()
    async with pool.acquire(user) as web:
        pass
    async with pool.acquire(replace(user, api_mode=ApiMode.MOBILE)) as client:
        assert isinstance(client, FakeMobile) and client.logins == 1
    assert web.closed


async def test_service_switch_saves_mode_and_blocks_mail(db, from_web):
    users = UserRepository(db)
    user = await users.save(make_user())
    service = DiaryService(pool_for(db), users)

    updated = await service.switch_api(user, ApiMode.MOBILE)

    assert updated.api_mode is ApiMode.MOBILE
    assert (await users.get(1)).api_mode is ApiMode.MOBILE
    with pytest.raises(NetSchoolError, match="/changeapi"):
        await service.send_mail(updated, ["1"], "Тема", "Текст")


def test_homework_keeps_publication_time():
    published = dt.datetime(2026, 9, 27, 2, 5, 45, tzinfo=MSK)
    day = SimpleNamespace(
        day=dt.date(2026, 9, 29),
        lessons=[
            SimpleNamespace(
                subject="Русский язык",
                number=1,
                topic="Союзы",
                assignments=[
                    SimpleNamespace(
                        kind="Домашнее задание", content="Стр. 78", deadline=dt.date(2026, 9, 29),
                        attachments=[], published_at=published, weight=2,
                    )
                ],
            )
        ],
    )
    (record,) = homework_from_day(day, today=dt.date(2026, 9, 29))
    assert record.published_at == published
    assert (record.weight, record.topic) == (2, "Союзы")
    # Время публикации не входит в ключ: смена API не делает задание «новым».
    assert record.identity == replace(record, published_at=None).identity


def test_homework_list_shows_publication_time():
    item = HomeworkRecord(
        "Русский язык", dt.date(2026, 9, 29), "ДЗ", "Стр. 78",
        published_at=dt.datetime(2026, 9, 26, 23, 5, tzinfo=dt.timezone.utc),
    )
    assert "опубл. 27.09 02:05" in formatting.homework_list([item], "ДЗ")
    assert "опубл." not in formatting.homework_list([replace(item, published_at=None)], "ДЗ")


def test_gosuslugi_user_always_uses_web_api():
    esia = make_user(credentials=Credentials(login_type=LoginType.ESIA), api_mode=ApiMode.MOBILE)
    assert not esia.mobile_api_supported
    assert esia.effective_api_mode is ApiMode.WEB
    assert make_user(api_mode=ApiMode.MOBILE).effective_api_mode is ApiMode.MOBILE


async def test_gosuslugi_user_left_in_mobile_mode_gets_web_client(db):
    # Раньше такой пользователь застревал: пул просил /login, а вход через
    # Госуслуги сохранял веб-сессию, которую мобильный клиент не принимал.
    pool = pool_for(db)
    user = await UserRepository(db).save(
        make_user(credentials=Credentials(login_type=LoginType.ESIA), api_mode=ApiMode.MOBILE)
    )
    await SessionRepository(db).save(1, '{"cookies": "esia"}')
    async with pool.acquire(user) as client:
        assert not isinstance(client, FakeMobile)
        assert client.imported == '{"cookies": "esia"}'


async def test_service_refuses_mobile_for_gosuslugi(db):
    users = UserRepository(db)
    user = await users.save(make_user(credentials=Credentials(login_type=LoginType.ESIA)))
    service = DiaryService(pool_for(db), users)
    with pytest.raises(NetSchoolError, match="логину и паролю"):
        await service.switch_api(user, ApiMode.MOBILE)
    assert (await users.get(1)).api_mode is ApiMode.WEB


def test_keyboard_hides_mobile_for_gosuslugi():
    from app.bot import keyboards

    def labels(user):
        return [b.text for row in keyboards.api_modes(user, True).inline_keyboard for b in row]

    assert any("Мобильное" in t for t in labels(make_user()))
    assert not any("Мобильное" in t for t in labels(make_user(credentials=Credentials(login_type=LoginType.ESIA))))
