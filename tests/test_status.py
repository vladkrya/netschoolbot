"""Страница статуса «Сетевого города»: проверки всех серверов, версии, жалобы."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.domain.models import School
from app.db.repositories import SgoStatusRepository, SgoVersionRepository
from app.status.service import PROBE_EVERY_S, REPORT_ALERT_COUNT, StatusService, error_name
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user

DOWN = "sgo.edu-74.ru"  # Челябинская область — есть в справочнике регионов
OURS = "school.example.ru"


def make_service(db, fetch=None, versions=None, notify=None, now=None):
    now = now if now is not None else [1_000_000.0]
    return StatusService(SgoStatusRepository(db), SgoVersionRepository(db), fetch=fetch,
                         versions=versions, notify=notify, clock=lambda: now[0]), now


async def test_version_history_keeps_every_change(db):
    repo = SgoVersionRepository(db)
    await repo.save("a.ru", "5.40", 100)
    await repo.save("a.ru", "5.40", 200)
    await repo.save("a.ru", "5.41", 300)
    history = await repo.history(0)
    assert [(h["version"], h["previous"], h["changed_at"]) for h in history] == [("5.41", "5.40", 300), ("5.40", "", 100)]


async def test_targets_cover_regions_and_our_servers(db, users):
    await users.save(make_user(school=School(url=f"https://{OURS}/", name="Школа")))
    service, _ = make_service(db)
    targets, ours = await service.targets()
    hosts = {t.host: t for t in targets}
    assert DOWN in hosts and hosts[DOWN].region == "Челябинская область"
    assert hosts[OURS].url == f"https://{OURS}" and hosts[OURS].region == ""
    assert ours == {OURS}


async def test_probes_build_states_incidents_and_versions(db, users):
    from app.notifications.sgo_version import VersionTracker

    await users.save(make_user(school=School(url=f"https://{OURS}", name="Школа")))
    sent: list[str] = []

    async def notify(text):
        sent.append(text)

    version = {"v": "5.40"}

    async def fetch(url):
        if DOWN in url:
            raise TimeoutError
        return version["v"]

    now = [1_000_000.0]
    tracker = VersionTracker(SgoVersionRepository(db), fetch, clock=lambda: now[0])
    service, now = make_service(db, fetch, tracker, notify, now)
    for _ in range(4):
        await service.probe_all()
        now[0] += PROBE_EVERY_S
    version["v"] = "5.41"
    await service.probe_all()

    data = await service.summary("24h")
    hosts = {h["host"]: h for h in data["hosts"]}
    assert hosts[DOWN]["state"] == "down" and hosts[DOWN]["error"] == "timeout"
    assert hosts[DOWN]["uptime"] == 0 and hosts[DOWN]["down_since"] == 1_000_000.0
    assert hosts[OURS]["state"] == "up" and hosts[OURS]["uptime"] == 100
    assert hosts[OURS]["version"] == "5.41"
    assert hosts[OURS]["bars"][-1] == 1 and hosts[DOWN]["bars"][-1] == 0
    assert data["overview"]["down"][-1] == 1
    [incident] = data["incidents"]
    assert incident["host"] == DOWN and incident["ongoing"] and incident["probes"] == 5
    # Смена версии видна в истории; админу — только о «наших» серверах.
    assert any(v["host"] == OURS and v["previous"] == "5.40" for v in data["versions"])
    assert sent == [f"⚠️ «Сетевой город» {OURS} обновился: 5.40 → 5.41"]


async def test_single_failure_is_not_an_incident_and_recovery_ends_it(db):
    calls = {"n": 0}

    async def fetch(url):
        if DOWN in url:
            calls["n"] += 1
            if calls["n"] in (2, 4, 5):
                raise ConnectionError
        return "5.40"

    service, now = make_service(db, fetch)
    for _ in range(7):
        await service.probe_all()
        now[0] += PROBE_EVERY_S
    data = await service.summary("24h")
    [incident] = data["incidents"]
    assert incident["probes"] == 2 and not incident["ongoing"] and incident["end"] is not None
    assert {h["host"]: h for h in data["hosts"]}[DOWN]["state"] == "unstable"


async def test_reports_are_limited_and_alert_admin(db):
    sent: list[str] = []

    async def notify(text):
        sent.append(text)

    service, now = make_service(db, notify=notify)
    assert await service.report(DOWN, "down", "  не  открывается ", "1.1.1.1") is None
    assert "уже принята" in await service.report(DOWN, "login", "", "1.1.1.1")
    assert await service.report("evil.example", "down", "", "1.1.1.1") == "Неизвестный сервер"
    assert await service.report(DOWN, "nonsense", "", "2.2.2.2") == "Выберите, что случилось"
    for index in range(2, REPORT_ALERT_COUNT + 1):
        assert sent == []
        assert await service.report(DOWN, "login", "<b>пусто</b>", f"2.2.2.{index}") is None
    assert len(sent) == 1 and DOWN in sent[0] and "Челябинская" in sent[0]
    assert "&lt;b&gt;пусто&lt;/b&gt;" in sent[0]
    await service.report(DOWN, "down", "", "3.3.3.3")
    assert len(sent) == 1  # не чаще раза в час

    data = await service.summary("24h")
    hosts = {h["host"]: h for h in data["hosts"]}
    assert hosts[DOWN]["reports_24h"] == REPORT_ALERT_COUNT + 1
    assert data["overview"]["reports"][-1] == REPORT_ALERT_COUNT + 1
    assert all("comment" not in r for r in data["reports"])  # текст жалоб — только админу


def test_error_names_hide_details():
    class ConnectError(Exception):
        pass

    class Response:
        status_code = 502

    class HTTPStatusError(Exception):
        response = Response()

    assert error_name(TimeoutError()) == "timeout"
    assert error_name(ConnectError("socks5://user:pass@proxy")) == "connect"
    assert error_name(HTTPStatusError()) == "http 502"


@pytest.fixture
async def client(app_context):
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    yield client
    await client.close()


async def test_status_page_is_public(client):
    response = await client.get("/mini/status")
    assert response.status == 200
    text = await response.text()
    assert "Статус «Сетевого города»" in text and "/status/status.js" in text

    response = await client.get("/mini/status/data?range=7d")
    data = await response.json()
    assert data["range"] == "7d" and len(data["hosts"][0]["bars"]) == 84

    response = await client.post("/mini/status/report", json={"host": DOWN, "kind": "down"})
    assert (await response.json()) == {"ok": True}
    response = await client.post("/mini/status/report", json={"host": DOWN, "kind": "down"})
    assert response.status == 400 and "уже принята" in (await response.json())["error"]
    # Ловушка для ботов: ответ «принято», но ничего не записано.
    response = await client.post("/mini/status/report", json={"host": "x", "kind": "down", "website": "spam"})
    assert (await response.json()) == {"ok": True}
