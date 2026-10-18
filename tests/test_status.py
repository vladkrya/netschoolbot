"""Страница статуса «Сетевого города»: свои проверки всех серверов, версии, жалобы."""

import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.db.repositories import SgoStatusRepository
from app.status.service import (
    PROBE_EVERY_S, REPORT_ALERT_COUNT, HttpProber, Probe, StatusService, Target, error_name, merge_targets,
)
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура

DOWN = "sgo.edu-74.ru"  # Челябинская область — есть и в справочнике, и в каталоге
MORDOVIA = "sgo.e-mordovia.ru"
CATALOG = [
    {"name": "Респ. Мордовия", "url": f"https://{MORDOVIA}/api/mobile", "unavailable": False},
    {"name": "Челябинская область", "url": f"https://{DOWN}/api/mobile", "unavailable": True,
     "comment": "С 1 сентября  приложение\nзаменяется"},
    {"name": "Новый регион", "url": "https://sgo.new.ru/api/mobile"},
    {"name": "Demo school", "url": "https://nc-rel-mobile-m.ir-tech.ru/api/mobile"},
]


def make_service(db, prober=None, notify=None, catalog=None, now=None):
    now = now if now is not None else [1_000_000.0]

    async def fetch_catalog():
        return CATALOG if catalog is None else catalog

    return StatusService(SgoStatusRepository(db), prober=prober, catalog=fetch_catalog, notify=notify,
                         clock=lambda: now[0]), now


def test_targets_merge_catalog_and_regions():
    targets = {t.host: t for t in merge_targets(CATALOG)}
    assert "nc-rel-mobile-m.ir-tech.ru" not in targets  # служебные серверы ИРТех
    assert targets[MORDOVIA].region == "Республика Мордовия"  # полное имя из справочника
    assert targets[MORDOVIA].mobile_url == f"https://{MORDOVIA}/api/mobile"
    assert targets[DOWN].unavailable and targets[DOWN].notice == "С 1 сентября приложение заменяется"
    assert targets["sgo.new.ru"].region == "Новый регион" and targets["sgo.new.ru"].url == "https://sgo.new.ru"
    # Волгограда нет в каталоге: остаётся, но без мобильного API.
    assert targets["sgo.volganet.ru"].mobile_url == ""
    # Сахалин: веб-адрес с портом из справочника, хост тот же.
    assert targets["netcity.admsakhalin.ru"].url.endswith(":11111")


async def test_probes_build_states_incidents_and_versions(db):
    version = {"v": "5.40"}

    async def prober(target):
        if target.host == DOWN and rounds[0]:
            return Probe(False, 20000, error="timeout", mobile=False)
        return Probe(True, 300, version=version["v"], mobile=True if target.mobile_url else None)

    rounds = [0]
    service, now = make_service(db, prober)
    for _ in range(5):
        await service.probe_all()
        now[0] += PROBE_EVERY_S
        rounds[0] += 1
    version["v"] = "5.41"
    await service.probe_all()

    data = await service.summary("24h")
    hosts = {h["host"]: h for h in data["hosts"]}
    assert "sgo.new.ru" in hosts  # список — из каталога
    assert hosts[DOWN]["state"] == "down" and hosts[DOWN]["error"] == "timeout"
    assert hosts[DOWN]["uptime"] == round(100 / 6, 2) and hosts[DOWN]["down_since"] == 1_000_000.0 + PROBE_EVERY_S
    assert hosts[DOWN]["app_disabled"] and hosts[DOWN]["notice"]
    assert hosts[DOWN]["mobile"] is False
    assert hosts[MORDOVIA]["state"] == "up" and hosts[MORDOVIA]["uptime"] == 100
    assert hosts[MORDOVIA]["mobile"] is True and hosts[MORDOVIA]["in_app"]
    assert hosts["sgo.volganet.ru"]["mobile"] is None and not hosts["sgo.volganet.ru"]["in_app"]
    assert hosts[MORDOVIA]["version"] == "5.41"
    assert hosts[MORDOVIA]["bars"][-1] == 1 and hosts[DOWN]["bars"][-1] == 0
    assert data["overview"]["down"][-1] == 1
    [incident] = data["incidents"]
    assert incident["host"] == DOWN and incident["ongoing"] and incident["probes"] == 5
    assert any(v["host"] == MORDOVIA and v["previous"] == "5.40" for v in data["versions"])


async def test_catalog_failure_keeps_regions(db):
    async def broken():
        raise httpx.ConnectError("нет")

    async def prober(target):
        return Probe(True, 100)

    service = StatusService(SgoStatusRepository(db), prober=prober, catalog=broken)
    await service.probe_all()
    hosts = {t.host for t in service.targets()}
    assert DOWN in hosts and "sgo.new.ru" not in hosts


async def test_single_failure_is_not_an_incident_and_recovery_ends_it(db):
    calls = {"n": 0}

    async def prober(target):
        if target.host == DOWN:
            calls["n"] += 1
            if calls["n"] in (2, 4, 5):
                return Probe(False, 0, error="connect")
        return Probe(True, 100, version="5.40")

    service, now = make_service(db, prober)
    for _ in range(7):
        await service.probe_all()
        now[0] += PROBE_EVERY_S
    data = await service.summary("24h")
    [incident] = data["incidents"]
    assert incident["probes"] == 2 and not incident["ongoing"] and incident["end"] is not None
    assert {h["host"]: h for h in data["hosts"]}[DOWN]["state"] == "unstable"


async def test_http_prober_checks_web_and_mobile(monkeypatch):
    def handler(request):
        if request.url.path == "/webapi/logindata":
            if request.url.host == "maint.ru":
                return httpx.Response(200, text="<html>Технические работы</html>")
            return httpx.Response(200, json={"version": "5.58.7"})
        if request.url.path == "/api/mobile/users":
            return httpx.Response(401 if request.url.host == "ok.ru" else 502)
        return httpx.Response(404)

    prober = HttpProber()
    monkeypatch.setattr(prober, "_client", lambda url, timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    probe = await prober(Target("ok.ru", "https://ok.ru", "Регион", mobile_url="https://ok.ru/api/mobile"))
    assert probe.ok and probe.version == "5.58.7" and probe.mobile is True
    probe = await prober(Target("bad.ru", "https://bad.ru", "Регион", mobile_url="https://bad.ru/api/mobile"))
    assert probe.ok and probe.mobile is False
    probe = await prober(Target("maint.ru", "https://maint.ru", "Регион"))
    assert not probe.ok and probe.error == "maintenance" and probe.mobile is None


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
    class Response:
        status_code = 502

    class HTTPStatusError(Exception):
        response = Response()

    assert error_name(TimeoutError()) == "timeout"
    assert error_name(httpx.ConnectError("socks5://user:pass@proxy")) == "connect"
    assert error_name(httpx.ReadTimeout("x")) == "timeout"
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
    assert 'class="embed"' not in text
    # Экран в приложении: облегчённый вид и тема приложения; чужая тема не проходит.
    text = await (await client.get("/mini/status?embed=1&theme=dark")).text()
    assert 'class="embed"' in text and 'data-theme="dark"' in text
    text = await (await client.get('/mini/status?theme="><script>')).text()
    assert "data-theme" not in text

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


async def test_migration_moves_version_history_from_shared_table(tmp_path):
    """На сервере уже стоит миграция 41: история версий переезжает в свою таблицу."""
    import aiosqlite

    from app.db.migrations import MIGRATIONS, migrate

    async with aiosqlite.connect(tmp_path / "old.sqlite3") as conn:
        for index, script in enumerate(MIGRATIONS[:41]):
            await conn.executescript(script)
            await conn.execute(f"PRAGMA user_version = {index + 1}")
        await conn.execute("INSERT INTO sgo_version_history VALUES ('a.ru', '5.41', '5.40', 100)")
        await conn.execute("INSERT INTO sgo_probes (host, at, ok) VALUES ('a.ru', 1, 1)")
        await conn.commit()
        assert await migrate(conn) == len(MIGRATIONS)
        rows = await (await conn.execute("SELECT host, version, previous, changed_at FROM sgo_status_versions")).fetchall()
        assert rows == [("a.ru", "5.41", "5.40", 100)]
        assert (await (await conn.execute("SELECT mobile FROM sgo_probes")).fetchone()) == (None,)
        tables = {row[0] for row in await (await conn.execute("SELECT name FROM sqlite_master")).fetchall()}
        assert "sgo_version_history" not in tables


async def test_never_reachable_server_has_no_data_instead_of_down(db):
    async def prober(target):
        if target.host == MORDOVIA:
            return Probe(False, 0, error="connect")  # с нашего сервера не открывается
        if target.host == DOWN:
            return Probe(True, 100) if service_round[0] == 0 else Probe(False, 0, error="timeout")
        return Probe(True, 100)

    service_round = [0]
    service, now = make_service(db, prober)
    for index in range(4):
        service_round[0] = index
        await service.probe_all()
        now[0] += PROBE_EVERY_S
    data = await service.summary("24h")
    hosts = {h["host"]: h for h in data["hosts"]}
    assert hosts[MORDOVIA]["state"] == "blocked" and hosts[MORDOVIA]["uptime"] is None
    assert all(bar is None for bar in hosts[MORDOVIA]["bars"])
    assert hosts[DOWN]["state"] == "down"  # отвечал раньше — значит, правда лёг
    assert {i["host"] for i in data["incidents"]} == {DOWN}
    assert data["overview"]["down"][-1] == 1


async def test_app_alerts_when_server_is_back_and_on_update(db, users):
    from app.domain.models import School
    from app.status.alerts import SgoAlerts
    from tests.test_repositories import make_user

    await users.save(make_user(school=School(url=f"https://{DOWN}/", name="Школа")))
    up = {"v": False, "version": "5.40"}

    async def prober(target):
        return Probe(True, 100, version=up["version"]) if up["v"] else Probe(False, 0, error="timeout")

    sent: list[tuple] = []

    async def deliver(telegram_id, text, title, body):
        sent.append((telegram_id, text, title))

    service, now = make_service(db, prober)
    repo = SgoStatusRepository(db)
    alerts = SgoAlerts(service, repo, deliver, clock=lambda: now[0])
    # Сервер отвечал раньше, теперь лёг.
    up["v"] = True
    await service.probe_all()
    now[0] += PROBE_EVERY_S
    up["v"] = False
    for _ in range(2):
        await service.probe_all()
        now[0] += PROBE_EVERY_S

    info = await alerts.info(1, f"https://{DOWN}/")
    assert info["state"] == "down" and info["region"] == "Челябинская область" and info["down_since"]
    assert info["subscribed"] == {"up": False, "updates": False}
    info = await alerts.subscribe(1, f"https://{DOWN}/", "up", True)
    assert info["subscribed"]["up"]
    await alerts.subscribe(1, f"https://{DOWN}/", "updates", True)

    await alerts.check()
    assert sent == []  # всё ещё лежит
    up.update(v=True, version="5.41")
    await service.probe_all()
    await alerts.check()
    texts = [text for _, text, _ in sent]
    assert any("снова работает" in t and "Челябинская" in t for t in texts)
    assert any("5.40 → 5.41" in t for t in texts)
    assert (await alerts.info(1, f"https://{DOWN}/"))["subscribed"] == {"up": False, "updates": True}

    sent.clear()
    await alerts.check()
    assert sent == []  # повторно не пишем


async def test_back_up_only_when_subscribers_own_diary_opens(db, users):
    """Страница входа отвечает, а журнал не грузится: «заработал» не шлём,
    пока не загрузится дневник самого подписчика."""
    from app.status.alerts import DIARY_EVERY_S, SgoAlerts
    from tests.test_repositories import make_user

    await users.save(make_user())
    sent: list[str] = []
    opens = {"v": False}
    tries: list[int] = []

    async def deliver(telegram_id, text, title, body):
        sent.append(text)

    async def bot_route(url):
        return "5.58.7"  # logindata отвечает

    async def diary_opens(telegram_id):
        tries.append(telegram_id)
        if not opens["v"]:
            raise TimeoutError
        return True

    service, now = make_service(db)
    alerts = SgoAlerts(service, SgoStatusRepository(db), deliver, fetch=bot_route, clock=lambda: now[0],
                       diary_opens=diary_opens)
    url = f"https://{DOWN}/"
    assert (await alerts.subscribe(1, url, "up", True))["subscribed"]["up"]
    await alerts.check()
    assert sent == [] and tries == [1]
    now[0] += 60
    await alerts.check()
    assert tries == [1]  # дневник пробуем не чаще раза в 5 минут
    opens["v"] = True
    now[0] += DIARY_EVERY_S
    await alerts.check()
    assert len(sent) == 1 and "дневник загрузился" in sent[0]
    await alerts.check()
    assert len(sent) == 1  # подписка снята


async def test_back_up_when_login_page_returns(db, users):
    from app.status.alerts import SgoAlerts
    from tests.test_repositories import make_user

    await users.save(make_user())
    sent: list[str] = []
    up = {"v": False}

    async def deliver(telegram_id, text, title, body):
        sent.append(text)

    async def bot_route(url):
        if not up["v"]:
            raise TimeoutError
        return "5.58.7"

    service, now = make_service(db)
    alerts = SgoAlerts(service, SgoStatusRepository(db), deliver, fetch=bot_route, clock=lambda: now[0])
    await alerts.subscribe(1, f"https://{DOWN}/", "up", True)
    await alerts.check()
    assert sent == []
    up["v"] = True
    await alerts.check()
    assert len(sent) == 1 and "снова работает" in sent[0]


async def test_live_state_probes_servers_outside_the_list(db):
    calls = []

    async def prober(target):
        calls.append(target.url)
        return Probe(False, 0, error="connect")

    service, _ = make_service(db, prober)
    state = await service.live_state("https://sgo.other.ru/some/path")
    assert calls == ["https://sgo.other.ru"] and state["state"] == "blocked"
    await service.live_state("https://sgo.other.ru/")
    assert len(calls) == 1  # помним две минуты


async def test_app_endpoints(client, app_context, users):
    from app.domain.models import School
    from tests.test_repositories import make_user

    await users.save(make_user(school=School(url=f"https://{DOWN}", name="Школа")))
    token = await app_context.miniapp.issue_token(1)
    headers = {"X-Netschool-Token": token}
    response = await client.get("/mini/api/sgo/server", headers=headers)
    data = (await response.json())["data"]
    assert data["state"] == "unknown" and data["subscribed"] == {"up": False, "updates": False}
    response = await client.post("/mini/api/sgo/notify", headers=headers, json={"kind": "updates", "on": True})
    assert (await response.json())["data"]["subscribed"]["updates"]
    response = await client.get("/mini/api/profile", headers=headers)
    assert (await response.json())["data"]["sgo_updates"] is True
    response = await client.post("/mini/api/sgo/notify", headers=headers, json={"kind": "nope"})
    assert response.status == 400
