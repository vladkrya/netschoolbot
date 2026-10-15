"""Звонки и маршруты мобильного приложения 2.3.1: ответ на задание, ВКС,
мероприятия, справки."""

import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.db.repositories import SessionRepository, UserRepository
from app.domain.models import ApiMode, Credentials, LoginType
from app.netschool.errors import NetSchoolError, Reason
from app.netschool.mapping import answer_from_mobile, event_from_mobile, link_from_join
from app.netschool.service import DiaryService, _refusal
from app.netschool.session import SessionPool
from tests.test_session_pool import SETTINGS, FakeClient, make_user
from tests.test_web import BASE, client, token  # noqa: F401 — фикстуры
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура

BELLS = [
    SimpleNamespace(weekday=1, shift=1, number=0, start=dt.time(8, 0), end=dt.time(8, 25), used=True),
    SimpleNamespace(weekday=1, shift=1, number=1, start=dt.time(8, 30), end=dt.time(9, 10), used=True),
]


class WebClient(FakeClient):
    logouts = 0

    async def bell_schedule(self):
        return BELLS

    async def logout(self):
        WebClient.logouts += 1


class MobileClient(FakeClient):
    async def classmeetings(self, *a):
        return []

    async def bell_schedule(self):
        raise AssertionError("звонки из уроков сервису не нужны")


@pytest.fixture
def service(db):
    WebClient.logouts = 0
    pool = SessionPool(
        SETTINGS, SessionRepository(db),
        client_factory=lambda url, proxy: WebClient(url, proxy),
        mobile_factory=lambda url, proxy, secret: MobileClient(url, proxy),
    )
    return DiaryService(pool, UserRepository(db))


class TestBells:
    async def test_web_user_gets_school_bells(self, service, users):
        user = make_user()
        await users.save(user)
        bells = await service.fetch_bells(user)
        assert bells[0] == {"weekday": 1, "shift": 1, "number": 0, "start": "08:00", "end": "08:25", "used": True}

    async def test_mobile_user_with_password_logs_into_web_once(self, service, users):
        user = make_user(api_mode=ApiMode.MOBILE)
        await users.save(user)
        assert [b["number"] for b in await service.fetch_bells(user)] == [0, 1]
        # Разовая веб-сессия закрыта и в пул не попала.
        assert WebClient.logouts == 1
        assert not await service._pool.has_client(user.telegram_id)

    async def test_gosuslugi_mobile_user_falls_back_to_lessons(self, service, users):
        user = make_user(api_mode=ApiMode.MOBILE, credentials=Credentials(login_type=LoginType.ESIA, login="", password=""))
        await users.save(user)
        assert await service.fetch_bells(user) is None


class TestRefusals:
    def test_forbidden_is_not_an_expired_session(self):
        from netschoolpy import exceptions as ns

        error = _refusal(ns.NetSchoolError('x: HTTP 403 {"message":"Attempted to perform an unauthorized operation."}'))
        assert error.reason is Reason.UNKNOWN and not error.reason.needs_relogin

    def test_server_text_on_500_is_shown_without_retry(self):
        from netschoolpy import exceptions as ns

        error = _refusal(ns.ServerUnavailable("join: сервер ответил 500: Ошибка обращения к шлюзу ВКС"))
        assert (error.reason, error.user_message) == (Reason.UNKNOWN, "Ошибка обращения к шлюзу ВКС")
        assert _refusal(ns.ServerUnavailable("x: сервер ответил 503")) is None
        assert _refusal(ns.SessionExpired("401")) is None

    async def test_mobile_only_methods_refuse_in_web_mode(self, service, users):
        user = make_user()
        with pytest.raises(NetSchoolError, match="мобильном"):
            await service.fetch_events(user)


class TestMapping:
    def test_answer(self):
        assert answer_from_mobile({"text": None, "files": []}) == {"text": "", "files": []}
        answer = answer_from_mobile({"text": "Ответ", "files": [{"attachmentId": 5, "fileName": "a.pdf"}]})
        assert answer["files"] == [{"id": 5, "name": "a.pdf", "description": ""}]

    def test_join_link_found_anywhere(self):
        assert link_from_join("https://vks.example/1") == "https://vks.example/1"
        assert link_from_join({"data": {"joinUrl": "https://vks.example/2"}}) == "https://vks.example/2"
        assert link_from_join({"message": "нет"}) == ""

    def test_event_labels(self):
        event = event_from_mobile({
            "eventId": 1, "name": "Олимпиада", "awardEventType": "Olympiad", "level": "Regional",
            "status": "RegisteringMembers", "startTime": "2025-01-30T00:00:00+03:00", "files": [],
        })
        assert (event["kind"], event["level"], event["status"], event["start"]) == (
            "Олимпиада", "Региональный", "Идёт регистрация", "2025-01-30")


class TestApi:
    async def test_bells_endpoint(self, client, token, app_context):
        async def fetch_bells(user):
            return [{"weekday": 1, "shift": 1, "number": 1, "start": "08:30", "end": "09:10", "used": True}]

        app_context.diary.fetch_bells = fetch_bells
        response = await client.get(f"{BASE}/api/bells", headers={"X-Netschool-Token": token})
        assert (await response.json())["data"]["items"][0]["start"] == "08:30"

    async def test_answer_needs_mobile_mode(self, client, token):
        response = await client.get(f"{BASE}/api/homework/5/answer", headers={"X-Netschool-Token": token})
        assert (await response.json())["data"] == {"available": False}

    async def test_answer_text_is_saved(self, client, token, app_context, users):
        await users.save(replace(await users.get(1), api_mode=ApiMode.MOBILE))
        saved = {}

        async def set_answer_text(user, assignment_id, text):
            saved[assignment_id] = text

        async def fetch_answer(user, assignment_id):
            return {"text": saved.get(assignment_id, ""), "files": []}

        app_context.diary.set_answer_text = set_answer_text
        app_context.diary.fetch_answer = fetch_answer
        response = await client.post(f"{BASE}/api/homework/5/answer/text", json={"text": " Решение "},
                                     headers={"X-Netschool-Token": token})
        assert (await response.json())["data"]["text"] == "Решение"

    async def test_unknown_reference_type_is_rejected(self, client, token, app_context, users):
        await users.save(replace(await users.get(1), api_mode=ApiMode.MOBILE))

        async def fetch_reference_types(user):
            return [{"id": 3, "name": "Справка об обучении"}]

        async def order_reference(user, type_id):
            raise AssertionError("заказывать нельзя")

        app_context.diary.fetch_reference_types = fetch_reference_types
        app_context.diary.order_reference = order_reference
        response = await client.post(f"{BASE}/api/references", json={"type_id": 9}, headers={"X-Netschool-Token": token})
        assert response.status == 400
