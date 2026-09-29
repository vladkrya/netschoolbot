"""Тесты сервиса дневника — в частности политики повторов, которая раньше
была разной в боте и в мини-приложении."""

import datetime as dt
from types import SimpleNamespace

import pytest

from app.db.repositories import SessionRepository, UserRepository
from app.domain.models import Credentials, LoginType, School, Student, User
from app.netschool.errors import NetSchoolError, Reason
from app.netschool.service import DiaryService, _monday_of
from app.netschool.session import SessionPool
from tests.test_session_pool import SETTINGS, FakeClient, make_user


class DiaryClient(FakeClient):
    """Клиент, отдающий заранее заданный дневник и считающий запросы недель."""

    def __init__(self, url, proxy=None):
        super().__init__(url, proxy)
        self.weeks_requested: list[dt.date] = []
        self.week_payloads: dict[dt.date, list] = {}
        self.week_errors: dict[dt.date, Exception] = {}
        self.init_payload = {"students": [], "currentStudentId": None}

    async def diary(self, start):
        self.weeks_requested.append(start)
        if start in self.week_errors:
            raise self.week_errors[start]
        return SimpleNamespace(schedule=self.week_payloads.get(start, []))

    async def diary_init(self):
        return self.init_payload

    async def _authed_get(self, path):
        if path == "student/diary/init":
            return SimpleNamespace(
                status_code=self.session_status, json=lambda: self.init_payload
            )
        return SimpleNamespace(status_code=self.session_status)


def day(date, subject="Алгебра", mark=5):
    return SimpleNamespace(
        day=date,
        lessons=[
            SimpleNamespace(
                subject=subject,
                number=1,
                start=dt.time(9, 0),
                end=dt.time(9, 45),
                assignments=[
                    SimpleNamespace(
                        kind="Контрольная",
                        content="Тема",
                        mark=mark,
                        weight=1,
                        comment="",
                        deadline=None,
                        attachments=[],
                    )
                ],
            )
        ],
    )


@pytest.fixture
def client_holder():
    return {}


@pytest.fixture
def service(db, client_holder):
    def factory(url, proxy):
        client = DiaryClient(url, proxy)
        client_holder["client"] = client
        return client

    pool = SessionPool(SETTINGS, SessionRepository(db), client_factory=factory)
    return DiaryService(pool, UserRepository(db))


class TestFetch:
    async def test_weeks_are_walked_from_monday(self, service, users, client_holder):
        user = make_user()
        await users.save(user)
        await service.fetch_diary(
            user, weeks_back=1, weeks_forward=1, today=dt.date(2026, 3, 4)
        )
        requested = client_holder["client"].weeks_requested
        # Все запросы начинаются с понедельника, иначе сервер отдаёт не ту неделю.
        assert all(d.weekday() == 0 for d in requested)
        assert requested == sorted(requested)

    async def test_marks_are_returned(self, service, users, client_holder):
        user = make_user()
        await users.save(user)
        today = dt.date(2026, 3, 4)

        async def prepare():
            async with service._pool.acquire(user) as client:
                client.week_payloads[_monday_of(today)] = [day(today)]

        await prepare()
        marks = await service.fetch_marks(user, weeks_back=0, weeks_forward=0, today=today)
        assert [m.mark for m in marks] == ["5"]

    async def test_duplicate_days_across_weeks_are_deduplicated(
        self, service, users, client_holder
    ):
        user = make_user()
        await users.save(user)
        today = dt.date(2026, 3, 4)
        monday = _monday_of(today)

        async with service._pool.acquire(user) as client:
            # Сервер вернул один и тот же день в ответе на две недели.
            client.week_payloads[monday] = [day(today)]
            client.week_payloads[monday - dt.timedelta(days=7)] = [day(today)]

        marks = await service.fetch_marks(user, weeks_back=1, weeks_forward=0, today=today)
        assert len(marks) == 1

    async def test_failed_week_does_not_lose_others(self, service, users):
        user = make_user()
        await users.save(user)
        today = dt.date(2026, 3, 4)
        monday = _monday_of(today)

        async with service._pool.acquire(user) as client:
            client.week_payloads[monday] = [day(today)]
            client.week_errors[monday - dt.timedelta(days=7)] = Exception("боль")

        marks = await service.fetch_marks(user, weeks_back=1, weeks_forward=0, today=today)
        assert len(marks) == 1

    async def test_409_stops_future_weeks(self, service, users):
        user = make_user()
        await users.save(user)
        today = dt.date(2026, 3, 4)
        monday = _monday_of(today)

        async with service._pool.acquire(user) as client:
            client.week_errors[monday + dt.timedelta(days=7)] = Exception("409 Conflict")

        await service.fetch_diary(user, weeks_back=0, weeks_forward=5, today=today)
        async with service._pool.acquire(user) as client:
            future = [d for d in client.weeks_requested if d > monday]
        # Цикл прекратился на первой 409, а не перебрал все пять недель.
        assert len(future) == 1


class TestRetryPolicy:
    async def test_transient_error_is_retried(self, service, users, monkeypatch):
        import app.netschool.service as module

        monkeypatch.setattr(module.asyncio, "sleep", _no_sleep)
        user = make_user()
        await users.save(user)
        attempts = 0

        async def flaky(client):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise Exception("503 Service Unavailable")
            return "готово"

        assert await service._with_session(user, flaky) == "готово"
        assert attempts == 3

    async def test_unknown_error_is_not_retried(self, service, users):
        user = make_user()
        await users.save(user)
        attempts = 0

        async def broken(client):
            nonlocal attempts
            attempts += 1
            raise ValueError("опечатка в коде")

        with pytest.raises(NetSchoolError) as info:
            await service._with_session(user, broken)
        # Повторять ошибку программиста бессмысленно — раньше PWA повторяло.
        assert attempts == 1
        assert info.value.reason is Reason.UNKNOWN

    async def test_auth_error_relogins_once(self, service, users):
        user = make_user()
        await users.save(user)
        attempts = 0

        async def expired(client):
            nonlocal attempts
            attempts += 1
            raise Exception("401 Unauthorized")

        with pytest.raises(NetSchoolError):
            await service._with_session(user, expired)
        # Одна попытка входа заново, а не бесконечный цикл.
        assert attempts == 2

    async def test_retries_are_bounded(self, service, users, monkeypatch):
        import app.netschool.service as module

        monkeypatch.setattr(module.asyncio, "sleep", _no_sleep)
        user = make_user()
        await users.save(user)
        attempts = 0

        async def always_down(client):
            nonlocal attempts
            attempts += 1
            raise Exception("503")

        with pytest.raises(NetSchoolError):
            await service._with_session(user, always_down)
        assert attempts == module.RETRY_ATTEMPTS


class TestStudents:
    async def test_sync_persists_children(self, service, users):
        user = make_user()
        await users.save(user)
        async with service._pool.acquire(user) as client:
            client.init_payload = {
                "students": [
                    {"studentId": 10, "fio": "Иванов И."},
                    {"studentId": 11, "fio": "Иванова А."},
                ],
                "currentStudentId": 10,
            }

        updated = await service.sync_students(user)
        assert updated.available_students == (
            Student(10, "Иванов И."),
            Student(11, "Иванова А."),
        )
        assert updated.selected_student_id == 10
        assert updated.student_name == "Иванов И."

        # И это уехало в базу, а не осталось в памяти.
        reloaded = await users.get(1)
        assert reloaded.available_students == updated.available_students

    async def test_sync_does_not_mutate_input(self, service, users):
        user = make_user()
        await users.save(user)
        async with service._pool.acquire(user) as client:
            client.init_payload = {"students": [{"studentId": 10, "fio": "И."}]}
        await service.sync_students(user)
        # Переданный объект остался прежним: неожиданные правки вглубь стека
        # были отдельным источником багов.
        assert user.available_students == ()

    async def test_existing_choice_is_kept(self, service, users):
        user = make_user(selected_student_id=11)
        await users.save(user)
        async with service._pool.acquire(user) as client:
            client.init_payload = {
                "students": [{"studentId": 10, "fio": "А"}, {"studentId": 11, "fio": "Б"}],
                "currentStudentId": 10,
            }
        updated = await service.sync_students(user)
        assert updated.selected_student_id == 11

    async def test_stale_choice_falls_back(self, service, users):
        user = make_user(selected_student_id=99)
        await users.save(user)
        async with service._pool.acquire(user) as client:
            client.init_payload = {
                "students": [{"studentId": 10, "fio": "А"}],
                "currentStudentId": 10,
            }
        updated = await service.sync_students(user)
        assert updated.selected_student_id == 10


async def _no_sleep(_seconds):
    return None
