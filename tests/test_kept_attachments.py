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

    keeper.offer(alice, [homework(1, 2, 3), homework(9, due=dt.date.today() - dt.timedelta(days=15))])
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


async def test_download_window_includes_two_weeks_back_and_forward(db, users, tmp_path, monkeypatch):
    monkeypatch.setattr("app.attachments.keeper.PAUSE_S", 0)
    keeper, diary = make(db, tmp_path, [1_000_000.0])
    offsets = [-15, -14, -10, 0, 14, 15]
    diary.files = {i: f"file-{i}".encode() for i in range(1, 7)}
    user = make_user(1)
    await users.save(user)
    keeper.offer(user, [homework(i, due=dt.date.today() + dt.timedelta(days=offset))
                        for i, offset in enumerate(offsets, 1)])
    await settle(keeper)
    assert diary.calls == [2, 3, 4, 5]
    assert await keeper.get(user, 2) == b"file-2"
    assert await keeper.get(user, 5) == b"file-5"
    assert await keeper.get(user, 1) is None
    assert await keeper.get(user, 6) is None


async def test_opened_file_at_50_mib_is_saved_but_larger_is_skipped(db, users, tmp_path):
    keeper, _ = make(db, tmp_path, [1_000_000.0])
    user = make_user(1)
    await users.save(user)
    payload = b"x" * (50 * 1024 * 1024)
    await keeper.store(user, 1, payload)
    assert await keeper.get(user, 1) == payload
    await keeper.store(user, 2, payload + b"x")
    assert await keeper.get(user, 2) is None


async def test_concurrent_classmates_download_shared_attachment_once(db, users, tmp_path, monkeypatch):
    monkeypatch.setattr("app.attachments.keeper.PAUSE_S", 0)
    keeper, diary = make(db, tmp_path, [1_000_000.0])
    alice, bob = make_user(1), make_user(2)
    for user in (alice, bob):
        await users.save(user)
    original_download = diary.download_attachment

    async def slow_download(user, attachment_id):
        await asyncio.sleep(0.05)  # второй ученик проверяется, пока первый ещё скачивает
        return await original_download(user, attachment_id)

    diary.download_attachment = slow_download
    for user in (alice, bob):
        keeper.offer(user, [homework(1)])
    await settle(keeper)
    assert diary.calls == [1]
    assert await keeper.get(alice, 1) == await keeper.get(bob, 1) == b"pdf-1"
    assert len(list(tmp_path.glob("*-web-*"))) == 1


async def test_opened_shared_copy_is_not_rewritten(db, users, tmp_path):
    now = [1_000_000.0]
    keeper, _ = make(db, tmp_path, now)
    alice, bob = make_user(1), make_user(2)
    for user in (alice, bob):
        await users.save(user)
    await keeper.store(alice, 1, b"shared-file")
    now[0] += 60
    await keeper.store(bob, 1, b"shared-file")
    row = await keeper._repo.saved(*keeper._key(alice, 1))
    assert row["saved_at"] == 1_000_000.0
    assert await keeper.get(bob, 1) == b"shared-file"
    assert len(list(tmp_path.glob("*-web-*"))) == 1


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


async def test_keeper_saves_only_for_those_who_turned_it_on(db, users, tmp_path, monkeypatch):
    monkeypatch.setattr("app.attachments.keeper.PAUSE_S", 0)
    wanted = {1: False}

    async def is_wanted(user):
        return wanted.get(user.telegram_id, False)

    diary = Diary()
    keeper = AttachmentKeeper(KeptAttachmentRepository(db), diary, tmp_path, wanted=is_wanted)
    user = make_user(1)
    await users.save(user)
    keeper.offer(user, [homework(1)])
    await settle(keeper)
    await keeper.store(user, 2, b"opened")
    assert diary.calls == [] and await keeper.get(user, 2) is None  # тумблер выключен
    wanted[1] = True
    keeper.offer(user, [homework(1)])
    await settle(keeper)
    assert await keeper.get(user, 1) == b"pdf-1"


async def test_beta_toggle_is_admin_only_until_opened_to_all(app_context, users):
    """Тумблер есть у админа (admin_id=1 в тестовых настройках); остальным —
    только когда админ открыл доступ."""
    from aiohttp.test_utils import TestClient, TestServer

    from app.web.server import create_app

    await users.save(make_user(1))
    await users.save(make_user(2))
    admin = {"X-Netschool-Token": await app_context.miniapp.issue_token(1)}
    other = {"X-Netschool-Token": await app_context.miniapp.issue_token(2)}
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        profile = (await (await client.get("/mini/api/profile", headers=other)).json())["data"]
        assert profile["keep_attachments"]["available"] is False
        response = await client.post("/mini/api/settings/keep-attachments", headers=other, json={"on": True})
        assert response.status == 403
        response = await client.post("/mini/api/settings/keep-attachments", headers=other, json={"all": True})
        assert response.status == 403  # открыть всем может только админ

        profile = (await (await client.get("/mini/api/profile", headers=admin)).json())["data"]
        assert profile["keep_attachments"] == {"available": True, "on": False, "admin": True, "all": False, "days": 14}
        data = (await (await client.post("/mini/api/settings/keep-attachments", headers=admin,
                                         json={"on": True})).json())["data"]
        assert data["on"] is True
        assert await app_context.keep_attachments_wanted(await users.get(1)) is True
        assert await app_context.keep_attachments_wanted(await users.get(2)) is False

        await client.post("/mini/api/settings/keep-attachments", headers=admin, json={"all": True})
        response = await client.post("/mini/api/settings/keep-attachments", headers=other, json={"on": True})
        assert (await response.json())["data"] == {"available": True, "on": True, "admin": False, "all": False,
                                                    "days": 14}
        assert await app_context.keep_attachments_wanted(await users.get(2)) is True
    finally:
        await client.close()
