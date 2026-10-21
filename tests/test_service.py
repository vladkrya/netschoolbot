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

    async def test_assignment_comment_is_plain_text(self, service, users, client_holder):
        user = make_user()
        await users.save(user)

        async def details(assignment_id):
            assert assignment_id == 7
            return SimpleNamespace(description="<p>Решать &laquo;в тетради&raquo;</p><p>---Не указана---</p>")

        async with service._pool.acquire(user) as client:
            client.assignment_details = details
        assert await service.fetch_assignment_comment(user, 7) == "Решать «в тетради»"

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

    async def test_failed_week_rejects_incomplete_diary(self, service, users):
        user = make_user()
        await users.save(user)
        today = dt.date(2026, 3, 4)
        monday = _monday_of(today)

        async with service._pool.acquire(user) as client:
            client.week_payloads[monday] = [day(today)]
            client.week_errors[monday - dt.timedelta(days=7)] = Exception("боль")

        with pytest.raises(NetSchoolError):
            await service.fetch_marks(user, weeks_back=1, weeks_forward=0, today=today)

    async def test_outage_is_retried_instead_of_returning_empty_diary(
        self, service, users, monkeypatch
    ):
        import app.netschool.service as module

        monkeypatch.setattr(module.asyncio, "sleep", _no_sleep)
        user = make_user()
        await users.save(user)
        today = dt.date(2026, 3, 4)
        async with service._pool.acquire(user) as client:
            client.week_errors[_monday_of(today)] = Exception("503 Service Unavailable")
        with pytest.raises(NetSchoolError) as caught:
            await service.fetch_diary(user, weeks_back=0, weeks_forward=0, today=today)
        assert caught.value.reason is Reason.SERVER_UNAVAILABLE
        assert len(client.weeks_requested) == module.RETRY_ATTEMPTS

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


class TestWeightsFromCards:
    """net.mephi3.ru отдаёт в веб-дневнике weight 0, настоящий вес — в карточке."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        from app.netschool import service as service_module
        service_module._weights.clear()
        yield
        service_module._weights.clear()

    def zero_weight_day(self, date):
        payload = day(date)
        payload.lessons[0].assignments[0].weight = 0
        payload.lessons[0].assignments[0].id = 18603
        return payload

    async def prepare(self, service, user, today, details):
        async with service._pool.acquire(user) as client:
            client.week_payloads[_monday_of(today)] = [self.zero_weight_day(today)]
            client.assignment_details = details
        return client

    async def test_weight_comes_from_assignment_card(self, service, users):
        user = make_user()
        await users.save(user)
        today = dt.date(2026, 10, 15)
        asked = []

        async def details(assignment_id):
            asked.append(assignment_id)
            return SimpleNamespace(weight=4)

        await self.prepare(service, user, today, details)
        marks = await service.fetch_marks(user, weeks_back=0, weeks_forward=0, today=today)
        assert [m.weight for m in marks] == [4]
        # Второй раз карточку не спрашиваем.
        marks = await service.fetch_marks(user, weeks_back=0, weeks_forward=0, today=today)
        assert [m.weight for m in marks] == [4]
        assert asked == [18603]

    async def test_failed_card_keeps_diary_working(self, service, users):
        user = make_user()
        await users.save(user)
        today = dt.date(2026, 10, 15)
        asked = []

        async def details(assignment_id):
            asked.append(assignment_id)
            raise Exception("Server error '500 Internal Server Error'")

        await self.prepare(service, user, today, details)
        marks = await service.fetch_marks(user, weeks_back=0, weeks_forward=0, today=today)
        assert [m.weight for m in marks] == [1]
        await service.fetch_marks(user, weeks_back=0, weeks_forward=0, today=today)
        assert asked == [18603]

    async def test_diary_weight_is_trusted(self, service, users):
        user = make_user()
        await users.save(user)
        today = dt.date(2026, 10, 15)

        async def details(assignment_id):
            raise AssertionError("вес из дневника не перепроверяем")

        async with service._pool.acquire(user) as client:
            payload = day(today)
            payload.lessons[0].assignments[0].weight = 2
            payload.lessons[0].assignments[0].id = 1
            client.week_payloads[_monday_of(today)] = [payload]
            client.assignment_details = details
        marks = await service.fetch_marks(user, weeks_back=0, weeks_forward=0, today=today)
        assert [m.weight for m in marks] == [2]


def test_frozen_netschoolpy_models_get_weight():
    from netschoolpy.models import Assignment
    from app.netschool.service import _with_weights

    assignment = Assignment(id=5, comment="", kind="К", content="", mark=4, weight=0,
                            is_duty=False, deadline=dt.date(2026, 10, 15))
    lesson = SimpleNamespace(assignments=[assignment])
    result = _with_weights(SimpleNamespace(day=dt.date(2026, 10, 15), lessons=[lesson]), {5: 3})
    assert result.lessons[0].assignments[0].weight == 3
    assert assignment.weight == 0


class TestWebRecipients:
    """Веб-почта отдаёт адресную книгу по группам; раньше бралась только администрация."""

    def filterpanel(self, groups):
        items = [{"title": title, "value": str(value)} for value, title in groups]
        return {"filterPanel": {"filters": []},
                "filterSources": [{"filterId": "org", "items": [{"title": "Школа", "value": "2"}]},
                                  {"filterId": "group", "items": items}]}

    async def setup(self, service, user, groups, people, failing=()):
        async with service._pool.acquire(user) as client:
            client._student_id, client._school_id = 450, 2
            asked = []

            async def authed_get(path, params=None):
                assert path == "mail/recipients/filterpanel"
                return SimpleNamespace(status_code=200, json=lambda: self.filterpanel(groups))

            async def mail_recipients(group):
                asked.append(group)
                if group in failing:
                    raise Exception("Server error '500 Internal Server Error'")
                return [SimpleNamespace(id=i, name=n, organization_name="Школа") for i, n in people.get(group, [])]

            original = client._authed_get
            client._authed_get = lambda path, params=None: (
                authed_get(path, params) if path.startswith("mail/") else original(path))
            client.mail_recipients = mail_recipients
        return asked

    async def test_all_server_groups_are_collected(self, service, users):
        user = make_user()
        await users.save(user)
        asked = await self.setup(
            service, user,
            [(1, "Администраторы"), (3, "Классные руководители"), (4, "Учителя"), (6, "Родители")],
            {1: [("a", "Директор")], 3: [("t", "Классный")], 4: [("t", "Классный"), ("u", "Учитель")],
             6: [("p", "Родитель")]})
        recipients = await service.fetch_recipients(user)
        assert asked == [1, 3, 4, 6]
        assert {r["id"]: r["category"] for r in recipients} == {
            "a": "Администрация", "t": "Учителя", "u": "Учителя", "p": "Родители"}

    async def test_failed_group_is_skipped(self, service, users):
        user = make_user()
        await users.save(user)
        await self.setup(service, user, [(1, "Администраторы"), (4, "Учителя")],
                         {1: [("a", "Директор")]}, failing=(4,))
        assert [r["id"] for r in await service.fetch_recipients(user)] == ["a"]

    async def test_unknown_group_goes_to_other(self, service, users):
        user = make_user()
        await users.save(user)
        await self.setup(service, user, [(9, "Кружки")], {9: [("k", "Руководитель")]})
        assert (await service.fetch_recipients(user))[0]["category"] == "Другие"


async def test_default_groups_without_filterpanel():
    from app.netschool.service import WEB_RECIPIENT_GROUPS, _web_recipient_groups

    async def broken(path, params=None):
        raise Exception("HTTP 404")
    client = SimpleNamespace(_student_id=1, _school_id=2, _authed_get=broken)
    assert await _web_recipient_groups(client) == list(WEB_RECIPIENT_GROUPS)


class TestMailPages:
    async def setup(self, service, user, entries, total):
        async with service._pool.acquire(user) as client:
            asked = []

            async def mail_list(folder, page=1, page_size=20):
                asked.append((folder, page, page_size))
                return SimpleNamespace(entries=entries, page=page, total_items=total)

            async def mail_unread():
                return [2]

            client.mail_list = mail_list
            client.mail_unread = mail_unread
        return asked

    def entry(self, message_id):
        return SimpleNamespace(id=message_id, subject="Тема", author="Учитель", sent=None, read=None)

    async def test_page_counts_and_web_read_flags(self, service, users):
        user = make_user()
        await users.save(user)
        asked = await self.setup(service, user, [self.entry(1), self.entry(2)], 101)
        data = await service.fetch_mail_page(user, page=3, page_size=50)
        assert asked == [("Inbox", 3, 50)]
        assert (data["page"], data["pages"]) == (3, 3)
        assert [m["read"] for m in data["messages"]] == [True, False]

    async def test_deleted_folder_skips_unread_lookup(self, service, users):
        """Непрочитанные школа отдаёт только для входящих."""
        user = make_user()
        await users.save(user)
        await self.setup(service, user, [self.entry(2)], 1)
        data = await service.fetch_mail_page(user, "Deleted")
        assert data["pages"] == 1
        assert data["messages"][0]["read"] is True
