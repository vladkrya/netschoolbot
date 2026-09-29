"""Переключение между веб- и мобильным API (/changeapi)."""

import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.db.repositories import SessionRepository, UserRepository
from app.domain import formatting
from app.domain.models import ApiMode, Credentials, LoginType
from app.domain.records import HomeworkRecord
from app.netschool.errors import NetSchoolError, Reason
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

    async def mail_send(self, subject, text, to):
        self.sent = getattr(self, "sent", []) + [(subject, text, list(to))]

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


async def test_service_switch_saves_mode_and_sends_mail_through_mobile(db, from_web):
    users = UserRepository(db)
    user = await users.save(make_user())
    pool = pool_for(db)
    service = DiaryService(pool, users)

    updated = await service.switch_api(user, ApiMode.MOBILE)

    assert updated.api_mode is ApiMode.MOBILE
    assert (await users.get(1)).api_mode is ApiMode.MOBILE
    await service.send_mail(updated, ["cmVjaXBpZW50"], "Тема", "Текст")
    async with pool.acquire(updated) as client:
        assert isinstance(client, FakeMobile)
        assert client.sent == [("Тема", "Текст", ["cmVjaXBpZW50"])]


async def test_mobile_mail_error_is_wrapped_and_not_repeated(db, from_web):
    users = UserRepository(db)
    user = await users.save(make_user(api_mode=ApiMode.MOBILE))
    pool = pool_for(db)
    service = DiaryService(pool, users)
    calls = []

    async def failing(subject, text, to):
        calls.append(1)
        raise RuntimeError("HTTP 403")

    async with pool.acquire(user) as client:
        client.mail_send = failing
    with pytest.raises(NetSchoolError):
        await service.send_mail(user, ["a"], "Тема", "Текст")
    # Повтор отправки мог бы послать письмо дважды.
    assert calls == [1]


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


def test_gosuslugi_user_can_use_mobile_api():
    esia = make_user(credentials=Credentials(login_type=LoginType.ESIA), api_mode=ApiMode.MOBILE)
    assert esia.effective_api_mode is ApiMode.MOBILE
    assert make_user(api_mode=ApiMode.MOBILE).effective_api_mode is ApiMode.MOBILE
    assert make_user().effective_api_mode is ApiMode.WEB


async def test_gosuslugi_web_session_is_not_a_mobile_session(db):
    # Веб-сессия Госуслуг мобильному клиенту не подходит, а без человека
    # новый вход невозможен: пул просит /login, где _finish_login сразу
    # меняет новую веб-сессию на токен (или возвращает веб-режим).
    class StrictMobile(FakeMobile):
        async def import_session(self, data):
            from netschoolpy.exceptions import SessionExpired

            raise SessionExpired("Это не сессия мобильного API")

    pool = SessionPool(
        MOBILE_SETTINGS, SessionRepository(db), client_factory=FakeClient, mobile_factory=StrictMobile
    )
    user = await UserRepository(db).save(
        make_user(credentials=Credentials(login_type=LoginType.ESIA), api_mode=ApiMode.MOBILE)
    )
    await SessionRepository(db).save(1, '{"cookies": "esia"}')
    with pytest.raises(NetSchoolError, match="/login"):
        async with pool.acquire(user):
            pass


async def test_service_switches_gosuslugi_user_from_live_web_session(db, from_web):
    users = UserRepository(db)
    user = await users.save(make_user(credentials=Credentials(login_type=LoginType.ESIA)))
    await SessionRepository(db).save(1, '{"cookies": "esia"}')
    service = DiaryService(pool_for(db), users)

    updated = await service.switch_api(user, ApiMode.MOBILE)

    assert updated.api_mode is ApiMode.MOBILE
    assert (await users.get(1)).api_mode is ApiMode.MOBILE
    assert len(from_web) == 1
    # Дальше живёт мобильный токен, ESIA-сессия больше не нужна.
    assert await SessionRepository(db).load(1) == '{"kind": "mobile"}'


def test_keyboard_offers_mobile_to_everyone():
    from app.bot import keyboards

    def labels(user):
        return [b.text for row in keyboards.api_modes(user, True).inline_keyboard for b in row]

    assert any("Мобильное" in t for t in labels(make_user()))
    assert any("Мобильное" in t for t in labels(make_user(credentials=Credentials(login_type=LoginType.ESIA))))


class TestGosuslugiLogin:
    """После входа через Госуслуги мобильный режим получает токен или откатывается на веб."""

    class Pool:
        def __init__(self, error=None):
            self.error, self.calls = error, []

        async def switch_api(self, user, mode):
            self.calls.append(mode)
            if self.error:
                raise self.error

    @staticmethod
    def _app(users, pool):
        return SimpleNamespace(users=users, pool=pool)

    async def test_token_is_taken_right_after_login(self, users):
        from app.bot.handlers.auth import _settle_api_mode

        user = await users.save(make_user(credentials=Credentials(login_type=LoginType.ESIA_QR), api_mode=ApiMode.MOBILE))
        pool = self.Pool()

        settled, note = await _settle_api_mode(user, self._app(users, pool))

        assert pool.calls == [ApiMode.MOBILE]
        assert settled.api_mode is ApiMode.MOBILE and "60 дней" in note
        assert (await users.get(1)).api_mode is ApiMode.MOBILE

    async def test_failure_returns_user_to_web_api(self, users):
        from app.bot.handlers.auth import _settle_api_mode

        user = await users.save(make_user(credentials=Credentials(login_type=LoginType.ESIA), api_mode=ApiMode.MOBILE))
        pool = self.Pool(NetSchoolError(Reason.UNKNOWN, "код не выдан"))

        settled, note = await _settle_api_mode(user, self._app(users, pool))

        # Не застрять в цикле «войдите заново»: остаёмся на вебе и говорим об этом.
        assert settled.api_mode is ApiMode.WEB and settled.effective_api_mode is ApiMode.WEB
        assert (await users.get(1)).api_mode is ApiMode.WEB
        assert "/changeapi" in note

    async def test_unexpected_error_also_falls_back(self, users):
        from app.bot.handlers.auth import _settle_api_mode

        user = await users.save(make_user(credentials=Credentials(login_type=LoginType.ESIA), api_mode=ApiMode.MOBILE))
        settled, _ = await _settle_api_mode(user, self._app(users, self.Pool(RuntimeError("boom"))))
        assert settled.api_mode is ApiMode.WEB

    async def test_web_mode_and_password_users_are_left_alone(self, users):
        from app.bot.handlers.auth import _settle_api_mode

        pool = self.Pool()
        esia_web = await users.save(make_user(credentials=Credentials(login_type=LoginType.ESIA)))
        assert await _settle_api_mode(esia_web, self._app(users, pool)) == (esia_web, "")
        password_mobile = await users.save(make_user(telegram_id=2, api_mode=ApiMode.MOBILE))
        assert await _settle_api_mode(password_mobile, self._app(users, pool)) == (password_mobile, "")
        assert pool.calls == []

    async def test_relogin_with_dead_esia_session_does_not_hang_on_mobile(self, db, from_web):
        # Полный путь: живая веб-сессия → токен → дальше клиент мобильный.
        pool = pool_for(db)
        users = UserRepository(db)
        user = await users.save(make_user(credentials=Credentials(login_type=LoginType.ESIA), api_mode=ApiMode.MOBILE))
        await SessionRepository(db).save(1, '{"cookies": "esia"}')

        await pool.switch_api(user, ApiMode.MOBILE)

        async with pool.acquire(user) as client:
            assert isinstance(client, FakeMobile)
