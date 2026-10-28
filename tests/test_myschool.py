"""«Госуслуги Моя школа» как источник дневника."""

import datetime as dt
from dataclasses import replace

import pytest

from myschoolpy import exceptions as ms_errors
from myschoolpy import models as ms

from app.db.repositories import SessionRepository, UserRepository
from app.domain.models import ApiMode, Credentials, LoginType
from app.netschool.errors import NetSchoolError, Reason, classify
from app.netschool.myschool import MYSCHOOL_URL, build_days, myschool_school
from app.netschool.service import DiaryService
from app.netschool.session import SessionPool
from tests.test_session_pool import SETTINGS, make_user

DAY = dt.date(2026, 10, 28)

STUDENT_B = {"student_id": "bbb", "student_first_name": "Пётр", "student_last_name": "Иванов"}
STUDENT_A = {"student_id": "aaa", "student_first_name": "Иван", "student_last_name": "Иванов"}
LESSONS = [
    {"lesson_id": "l2", "subject_name": "Физика", "start_datetime": "2026-10-28 09:00:00.0",
     "end_datetime": "2026-10-28 09:45:00.0", "room": "12", "lastname": "Петрова",
     "firstname": "Анна", "patronymic": "Сергеевна", "theme": "Законы Ньютона"},
    {"lesson_id": "l1", "subject_name": "Алгебра", "start_datetime": "2026-10-28 08:00:00.0",
     "end_datetime": "2026-10-28 08:45:00.0", "type_value": "Б"},
]
MARKS = [
    {"marks_id": "m2", "subject_name": "Физика", "mark_value1": "4", "mark_date": "2026-10-28",
     "lesson_id": "l2", "work_type_description": "Контрольная", "work_name": "Динамика", "weight": 3.0},
    {"marks_id": "m3", "subject_name": "Физика", "mark_value1": "5", "mark_date": "2026-10-28",
     "lesson_id": "l2", "work_type_description": "Контрольная", "work_name": "Динамика", "weight": 3.0},
    {"marks_id": "m4", "subject_name": "Физика", "mark_value1": None, "mark_date": "2026-10-28"},
]
HOMEWORKS = [
    {"homeworks_id": "h1", "subject_name": "Физика", "description": "§12,\n упр. 3",
     "plan_ready_date": "2026-10-29 00:00:00.0", "ready_lesson_id": None},
    {"homeworks_id": "h2", "subject_name": "Физика", "description": "§13",
     "plan_ready_date": "2026-10-28", "ready_lesson_id": "l2"},
]


def parsed():
    return (
        [ms.Lesson.model_validate(x) for x in LESSONS],
        [ms.Mark.model_validate(x) for x in MARKS],
        [ms.Homework.model_validate(x) for x in HOMEWORKS],
    )


def test_build_days_numbers_lessons_by_time_and_links_records():
    days = build_days(*parsed())
    assert [d.day for d in days] == [DAY, dt.date(2026, 10, 29)]
    today = days[0]
    assert [(l.number, l.subject, l.start, l.end) for l in today.lessons] == [
        (1, "Алгебра", "08:00", "08:45"), (2, "Физика", "09:00", "09:45")]
    assert today.lessons[0].absence == "Б"
    physics = today.lessons[1]
    assert (physics.room, physics.teacher, physics.topic) == ("12", "Петрова Анна Сергеевна", "Законы Ньютона")
    assert [m.mark for m in physics.marks] == ["4", "5"]
    assert [h.text for h in physics.homework] == ["§13"]


def test_marks_get_distinct_identity_and_skip_empty_values():
    marks = build_days(*parsed())[0].marks
    assert [m.mark for m in marks] == ["4", "5"]  # без оценки (m4) не берём
    assert marks[0].identity != marks[1].identity
    assert marks[0].title == "Контрольная Динамика" and marks[0].weight == 3
    assert marks[0].lesson_number == 2


def test_homework_text_is_flattened_and_due_date_is_day():
    tomorrow = build_days(*parsed())[1]
    assert [(h.text, h.due_date, h.comment) for h in tomorrow.homework] == [
        ("§12, упр. 3", dt.date(2026, 10, 29), "")]


def test_classifies_myschool_errors():
    assert classify(ms_errors.SessionExpired("x")) is Reason.AUTH
    assert classify(ms_errors.InvalidCredentials("x")) is Reason.CREDENTIALS
    assert classify(ms_errors.ESIAInvalidCredentials("x")) is Reason.CREDENTIALS
    assert classify(ms_errors.MFAError("x")) is Reason.MFA
    assert classify(ms_errors.AccountLocked("x")) is Reason.RATE_LIMIT
    assert classify(ms_errors.ServerUnavailable("x")) is Reason.SERVER_UNAVAILABLE


# ── пул и сервис ────────────────────────────────────────────

class MySchool:  # имя важно: пул узнаёт клиента «Моей школы» по классу
    instances: list["MySchool"] = []

    def __init__(self, proxy=None):
        self.import_error: Exception | None = None
        self.imported: str | None = None
        self.closed = False
        self.calls = []
        MySchool.instances.append(self)

    async def import_session(self, payload):
        if self.import_error:
            raise self.import_error
        self.imported = payload

    def export_session(self):
        return '{"mode": "bearer", "access_token": "T"}'

    async def is_alive(self):
        return True

    async def students(self, role="student"):
        return [ms.Student.model_validate(STUDENT_B), ms.Student.model_validate(STUDENT_A)] if role == "student" else []

    async def lessons(self, student, start, end=None):
        self.calls.append(("lessons", student.id))
        return parsed()[0]

    async def marks(self, student, start, end=None):
        return parsed()[1]

    async def homeworks(self, student, start, end=None):
        return parsed()[2]

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset():
    MySchool.instances.clear()
    yield


def myschool_user(**kwargs):
    base = make_user(url=MYSCHOOL_URL, api_mode=ApiMode.MYSCHOOL,
                     credentials=Credentials(login_type=LoginType.ESIA, remember=False), **kwargs)
    return replace(base, school=myschool_school())


async def service_for(db, user):
    await UserRepository(db).save(user)
    await SessionRepository(db).save(user.telegram_id, '{"mode": "bearer", "access_token": "T"}')
    pool = SessionPool(SETTINGS, SessionRepository(db), myschool_factory=lambda proxy: MySchool(proxy))
    return DiaryService(pool, UserRepository(db))


async def test_fetch_diary_reads_myschool_with_saved_token(db):
    user = myschool_user()
    service = await service_for(db, user)
    days = await service.fetch_period(user, DAY, DAY + dt.timedelta(days=1), today=DAY)
    client = MySchool.instances[0]
    assert client.imported == '{"mode": "bearer", "access_token": "T"}'
    assert client.calls == [("lessons", "aaa")]  # ученик №1 — первый по id, не по порядку ответа
    assert [m.mark for d in days for m in d.marks] == ["4", "5"]


async def test_sync_students_numbers_students_and_keeps_selection(db):
    user = myschool_user()
    service = await service_for(db, user)
    synced = await service.sync_students(user)
    assert [(s.id, s.name) for s in synced.available_students] == [
        (1, "Иванов Иван"), (2, "Иванов Пётр")]
    assert (synced.selected_student_id, synced.student_name) == (1, "Иванов Иван")

    second = await service.sync_students(replace(synced, selected_student_id=2))
    assert (second.selected_student_id, second.student_name) == (2, "Иванов Пётр")
    days = await service.fetch_period(second, DAY, DAY, today=DAY)
    assert MySchool.instances[0].calls[-1] == ("lessons", "bbb") and days


async def test_expired_token_asks_to_login_again(db):
    user = myschool_user()
    service = await service_for(db, user)
    original = MySchool.__init__

    def failing(self, proxy=None):
        original(self, proxy)
        self.import_error = ms_errors.SessionExpired("истёк")

    MySchool.__init__ = failing
    try:
        with pytest.raises(NetSchoolError) as caught:
            await service.fetch_period(user, DAY, DAY, today=DAY)
    finally:
        MySchool.__init__ = original
    assert caught.value.reason is Reason.AUTH and "/login" in caught.value.user_message
    assert await SessionRepository(db).load(user.telegram_id) is None


async def test_netschool_only_features_are_refused(db):
    user = myschool_user()
    service = await service_for(db, user)
    with pytest.raises(NetSchoolError) as caught:
        await service.fetch_mail(user)
    assert "«Моей школы»" in caught.value.user_message
    assert await service.fetch_recent_events(user) is None
    with pytest.raises(NetSchoolError):
        await service.switch_api(user, ApiMode.MOBILE)


class TestMaterials:
    LINK = "https://sgo.example.ru/webapi/attachments/abc"

    def _homework(self, materials):
        return ms.Homework.model_validate({
            "homeworks_id": "h", "subject_name": "Физика", "description": "",
            "plan_ready_date": "2026-10-23 00:00:00.0", "materials": materials})

    def test_material_gets_stable_id_and_link_is_remembered(self):
        from app.netschool import myschool
        material = {"name": "Презентация.pptx", "link": self.LINK, "id": "1_2"}
        (day,) = build_days([], [], [self._homework([material])])
        (attachment,) = day.homework[0].attachments
        assert attachment.name == "Презентация.pptx"
        assert attachment.id == myschool.material_id(self.LINK) < 2 ** 53
        assert myschool._LINKS[attachment.id] == self.LINK

    def test_material_without_link_has_no_id(self):
        (day,) = build_days([], [], [self._homework([{"name": "Файл"}])])
        assert day.homework[0].attachments[0].id is None

    async def test_download_uses_remembered_link(self, monkeypatch):
        import httpx
        from app.netschool import myschool
        file_id = myschool._remember(self.LINK)
        real = httpx.AsyncClient
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, content=b"data")

        monkeypatch.setattr(httpx, "AsyncClient",
                            lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        assert await myschool.download_material(None, None, file_id) == b"data"
        assert seen == [self.LINK]
