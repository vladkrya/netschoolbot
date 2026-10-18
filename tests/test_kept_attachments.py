"""Вложения заданий скачиваются заранее и открываются, когда СГО лежит."""

import asyncio
import datetime as dt

from app.attachments.keeper import MAX_FILE_BYTES, AttachmentKeeper
from app.db.repositories import KeptAttachmentRepository
from app.domain.models import School
from app.domain.records import Attachment, HomeworkRecord
from app.netschool.errors import NetSchoolError, Reason
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user


class Diary:
    def __init__(self):
        self.calls: list[int] = []
        self.down = False
        self.files = {1: b"pdf-1", 2: b"docx-2", 3: b"x" * (MAX_FILE_BYTES + 1)}

    async def download_attachment(self, user, attachment_id):
        self.calls.append(attachment_id)
        if self.down:
            raise NetSchoolError(Reason.SERVER_UNAVAILABLE, "лежит")
        return self.files[attachment_id]


def homework(*ids, due=None):
    return HomeworkRecord(subject="Математика", due_date=due or dt.date.today() + dt.timedelta(days=1),
                          assignment_type="ДЗ", text="", attachments=tuple(Attachment(i, f"{i}.pdf") for i in ids))


async def settle(keeper):
    while keeper._running:
        await asyncio.gather(*keeper._running.values())


def make(db, tmp_path, now):
    diary = Diary()
    keeper = AttachmentKeeper(KeptAttachmentRepository(db), diary, tmp_path, days=14, clock=lambda: now[0])
    return keeper, diary


async def test_files_are_kept_for_classmates_and_served_when_server_is_down(db, users, tmp_path, monkeypatch):
    monkeypatch.setattr("app.attachments.keeper.PAUSE_S", 0)
    now = [1_000_000.0]
    keeper, diary = make(db, tmp_path, now)
    school = School(url="https://sgo.example.ru/", name="Школа")
    alice, bob, stranger = (make_user(i, school=school) for i in (1, 2, 3))
    for user in (alice, bob, stranger):
        await users.save(user)

    keeper.offer(alice, [homework(1, 2, 3), homework(9, due=dt.date.today() - dt.timedelta(days=10))])
    await settle(keeper)
    assert diary.calls == [1, 2, 3]  # старое задание не трогаем; большой файл скачан, но не сохранён
    keeper.offer(bob, [homework(1, 2)])
    await settle(keeper)
    assert diary.calls == [1, 2, 3]  # однокласснику — та же копия, без нового скачивания

    diary.down = True
    assert await keeper.get(bob, 1) == b"pdf-1"
    assert await keeper.get(alice, 3) is None
    assert await keeper.get(stranger, 1) is None  # у него этого вложения не было

    now[0] += 15 * 86400
    assert await keeper.get(alice, 1) is None  # две недели прошли
    await keeper.prune(force=True)
    assert not list(tmp_path.glob("*-web-1"))


async def test_server_down_postpones_until_next_check(db, users, tmp_path):
    now = [1_000_000.0]
    keeper, diary = make(db, tmp_path, now)
    user = make_user(1)
    await users.save(user)
    diary.down = True
    keeper.offer(user, [homework(1, 2)])
    await settle(keeper)
    assert diary.calls == [1]  # после первого «лежит» не долбим сервер
    diary.down = False
    keeper.offer(user, [homework(1, 2)])
    await settle(keeper)
    assert await keeper.get(user, 2) == b"docx-2"


async def test_disk_limit_drops_oldest(db, users, tmp_path, monkeypatch):
    monkeypatch.setattr("app.attachments.keeper.PAUSE_S", 0)
    now = [1_000_000.0]
    diary = Diary()
    keeper = AttachmentKeeper(KeptAttachmentRepository(db), diary, tmp_path, days=14, max_bytes=6,
                              clock=lambda: now[0])
    user = make_user(1)
    await users.save(user)
    keeper.offer(user, [homework(1)])
    await settle(keeper)
    now[0] += 60
    keeper.offer(user, [homework(1, 2)])
    await settle(keeper)
    await keeper.prune(force=True)
    assert await keeper.get(user, 1) is None and await keeper.get(user, 2) == b"docx-2"


async def test_endpoint_serves_kept_copy_when_server_is_down(app_context, users, tmp_path):
    from dataclasses import replace

    from aiohttp.test_utils import TestClient, TestServer

    from app.web.server import create_app

    app_context.settings = replace(app_context.settings, data_dir=tmp_path)
    app_context.attachments = AttachmentKeeper(KeptAttachmentRepository(app_context.db), app_context.diary,
                                               tmp_path / "kept")
    await users.save(make_user(1))
    user = await users.get(1)
    await app_context.attachments.store(user, 7, b"saved-file")
    app_context.diary.download_attachment = None  # если бы пошли в СГО — упали бы
    token = await app_context.miniapp.issue_token(1)
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        response = await client.get("/mini/api/attachment/7?name=a.pdf", headers={"X-Netschool-Token": token})
        assert response.status == 200 and await response.read() == b"saved-file"
    finally:
        await client.close()
