"""Страница статуса «Сетевого города»: свои проверки всех серверов, версии, жалобы."""

import asyncio

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
    assert hosts[MORDOVIA]["bars"][-1] == 1 and hosts[DOWN]["bars"][-1] == round(1 / 6, 3)
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


async def test_probe_gaps_do_not_fake_recovery_and_success_splits_incidents(db):
    service, now = make_service(db)
    repo = SgoStatusRepository(db)
    at = now[0]
    # Длинный разрыв без удачи — тот же сбой. Удача даже между близкими
    # неудачами завершает серию: прежняя эвристика по 7,5 минуты теряла оба случая.
    await repo.add_probes([
        (DOWN, at - 900, True, 100, "", None),
        (DOWN, at, False, 0, "connect", None),
        (DOWN, at + 600, False, 0, "connect", None),
        (DOWN, at + 660, True, 100, "", None),
        (DOWN, at + 720, False, 0, "http 503", None),
        (DOWN, at + 780, False, 0, "http 503", None),
    ])
    now[0] = at + 840
    data = await service.summary()
    first, second = sorted(data["incidents"], key=lambda i: i["start"])
    assert first["start"] == at and first["end"] == at + 660 and not first["ongoing"]
    assert second["start"] == at + 720 and second["ongoing"]
    host = next(h for h in data["hosts"] if h["host"] == DOWN)
    assert host["down_since"] == at + 720


async def test_last_success_survives_two_hour_summary_window(db):
    service, now = make_service(db)
    await SgoStatusRepository(db).add_probes([
        (DOWN, now[0] - 12 * 3600, True, 100, "", None),
        (DOWN, now[0] - 10 * 3600, False, 0, "connect", None),
        (DOWN, now[0] - 300, False, 0, "connect", None),
        (DOWN, now[0], False, 0, "connect", None),
    ])
    host = next(h for h in (await service.summary())["hosts"] if h["host"] == DOWN)
    assert host["last_ok_at"] == now[0] - 12 * 3600
    live = await service.live_state(f"https://{DOWN}")
    assert live["last_ok_at"] == host["last_ok_at"]
    assert live["down_since"] == host["down_since"] == now[0] - 10 * 3600


async def test_account_results_are_anonymous_and_do_not_change_probe_uptime(db, app_context):
    service, now = make_service(db)
    # Проверяем, что обработчик использует актуальный сервис даже после его замены.
    app_context.status = service
    await service.refresh_catalog()
    await SgoStatusRepository(db).add_probes([(DOWN, now[0], True, 100, "", True)])
    monitor = app_context.watchers.outages
    url = f"https://{DOWN}/"
    # Ошибки входа не попадают ни в счётчики, ни в статус.
    await monitor.record(url, ok=False, server_down=False)
    assert await SgoStatusRepository(db).account_checks(0) == {}
    for _ in range(3):
        await monitor.record(url, ok=False, server_down=True)
    host = next(h for h in (await service.summary())["hosts"] if h["host"] == DOWN)
    assert host["state"] == "unstable" and host["diary_issues"]
    assert host["account_checks"] == {"good": 0, "total": 3}
    assert host["uptime"] == 100 and not host["down_since"]
    for _ in range(4):
        await monitor.record(url, ok=True)
    host = next(h for h in (await service.summary())["hosts"] if h["host"] == DOWN)
    assert host["state"] == "up" and not host["diary_issues"]
    assert host["account_checks"] == {"good": 4, "total": 7}
    columns = {row["name"] for row in await db.fetch_all("PRAGMA table_info(sgo_account_checks)")}
    assert columns == {"host", "bucket", "good", "total"}
    now[0] += 3600
    host = next(h for h in (await service.summary())["hosts"] if h["host"] == DOWN)
    assert host["account_checks"] is None and not host["diary_issues"]


@pytest.mark.parametrize("status,body,root,error", [
    (503, "Service unavailable", "<h1>Техническое обслуживание</h1>", "maintenance"),
    (503, "Service unavailable", "Service unavailable", "http 503"),
    (500, "NullReference in StudentDiaryService", "", "http 500"),
    (200, "<h1>Технические <b>работы</b></h1>", "", "maintenance"),
    (200, "<html>Unknown error</html>", "", "invalid_response"),
])
async def test_maintenance_needs_an_explicit_announcement(db, monkeypatch, status, body, root, error):
    def handler(request):
        return httpx.Response(status, text=body if request.url.path == "/webapi/logindata" else root)

    prober = HttpProber()
    monkeypatch.setattr(prober, "_client", lambda url, timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    probe = await prober(Target(DOWN, f"https://{DOWN}", "Регион"))
    assert not probe.ok and probe.error == error
    service, now = make_service(db)
    await SgoStatusRepository(db).add_probes([
        (DOWN, now[0] - 600, True, 100, "", None),
        (DOWN, now[0] - 300, False, 0, probe.error, None),
        (DOWN, now[0], False, 0, probe.error, None),
    ])
    assert (await service.live_state(f"https://{DOWN}"))["error"] == error


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


async def test_public_websocket_signals_checks_and_reports_and_cleans_up(client, app_context):
    service = app_context.status
    async with client.ws_connect("/mini/status/ws") as socket:
        initial = await socket.receive_json(timeout=2)
        assert initial == {"t": "status", "revision": 0}
        await service.record_account_check(f"https://{DOWN}", False)
        update = await socket.receive_json(timeout=2)
        assert set(update) == {"t", "revision"} and update["revision"] > initial["revision"]
        data = await (await client.get("/mini/status/data")).json()
        host = next(h for h in data["hosts"] if h["host"] == DOWN)
        assert host["account_checks"] == {"good": 0, "total": 1}
        assert data["probe_every_s"] == 60
        await client.post("/mini/status/report", json={"host": DOWN, "kind": "down"})
        report = await socket.receive_json(timeout=2)
        assert report["revision"] > update["revision"]
    async with asyncio.timeout(2):
        while service._listeners:
            await asyncio.sleep(0.01)
    assert not service._listeners


async def test_status_notifications_coalesce_for_slow_clients(db):
    service, _ = make_service(db, prober=lambda target: asyncio.sleep(0, result=Probe(True, 100)))
    queue = service.subscribe()
    await queue.get()
    await service.probe_all()
    await service.record_account_check(f"https://{DOWN}", True)
    await service.record_account_check(f"https://{DOWN}", False)
    assert queue.qsize() == 1
    await queue.get()
    service.unsubscribe(queue)
    await service.record_account_check(f"https://{DOWN}", False)
    assert queue.empty()


async def test_status_shutdown_closes_open_socket(client, app_context):
    socket = await client.ws_connect("/mini/status/ws")
    await socket.receive_json(timeout=2)
    app_context.status.close_listeners()
    message = await socket.receive(timeout=2)
    from aiohttp import WSMsgType
    assert message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
    await socket.close()
    assert not app_context.status._listeners


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


async def test_subscribe_up_does_not_wait_for_server(db, users):
    """«Сообщить, когда заработает» не ходит в лежащий сервер: раньше кнопка
    в приложении гасла на 20 с, пока бот ждал версию, которая ей не нужна."""
    from app.status.alerts import SgoAlerts
    from tests.test_repositories import make_user

    await users.save(make_user())
    fetched: list[str] = []

    async def bot_route(url):
        fetched.append(url)
        return "5.58.7"

    service, now = make_service(db)
    alerts = SgoAlerts(service, SgoStatusRepository(db), fetch=bot_route, clock=lambda: now[0])
    url = f"https://{DOWN}/"
    assert (await alerts.subscribe(1, url, "up", True))["subscribed"]["up"]
    assert fetched == []
    await alerts.subscribe(1, url, "updates", True)
    assert fetched == [url]  # версию для «об обновлениях» запоминаем


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


def test_wrapped_tls_and_connect_timeout_are_reported_precisely():
    import ssl

    tls = httpx.ConnectError("private proxy address")
    tls.__cause__ = ssl.SSLCertVerificationError("untrusted certificate")
    assert error_name(tls) == "ssl"
    assert error_name(httpx.ConnectTimeout("private proxy address")) == "timeout"


@pytest.mark.parametrize("status,expected,error", [(401, True, ""), (403, False, "http 403"),
                                                    (503, False, "http 503")])
async def test_mobile_probe_keeps_http_reason_and_rejects_waf(monkeypatch, status, expected, error):
    def handler(request):
        if request.url.path == "/webapi/logindata":
            return httpx.Response(200, json={"version": "5.59.1"})
        return httpx.Response(status, text="Forbidden" if status == 403 else "")

    prober = HttpProber()
    monkeypatch.setattr(prober, "_client", lambda url, timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    result = await prober(Target("ok.ru", "https://ok.ru", "Регион", "https://ok.ru:444/api/mobile"))
    assert result.ok
    assert result.mobile is expected and result.mobile_error == error


async def test_mobile_timeout_does_not_erase_successful_web_probe(monkeypatch):
    import app.status.service as module

    monkeypatch.setattr(module, "MOBILE_TIMEOUT_S", 0.01)

    async def handler(request):
        if request.url.path == "/webapi/logindata":
            return httpx.Response(200, json={"version": "5.59.1"})
        await asyncio.sleep(10)

    prober = HttpProber()
    monkeypatch.setattr(prober, "_client", lambda url, timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))
    result = await prober(Target("ok.ru", "https://ok.ru", "Регион", "https://ok.ru/api/mobile"))
    assert result.ok and result.version == "5.59.1"
    assert result.mobile is False and result.mobile_error == "timeout"


async def test_status_checks_only_configured_exit_and_keeps_catalog_api(db, monkeypatch):

    calls = []

    async def check(self, target):
        proxy = self._proxy_for(target.url)
        calls.append((target.host, proxy, target.mobile_url))
        if proxy:
            return Probe(True, 120, version="5.59.1", mobile=False, mobile_error="http 503")
        return Probe(False, 20000, error="timeout", mobile=False, mobile_error="timeout")

    monkeypatch.setattr(HttpProber, "__call__", check)
    proxy = "socks5://user:secret@127.0.0.1:1082"
    prober = HttpProber(lambda _: proxy)
    service, _ = make_service(db, prober)
    await service.probe_all()
    data = await service.summary()
    host = next(h for h in data["hosts"] if h["host"] == MORDOVIA)
    assert host["web_state"] == "up" and host["uptime"] == 100
    assert host["mobile_url"] == f"https://{MORDOVIA}/api/mobile"
    checks = host["latest_check"]
    assert checks["ok"]
    assert checks["mobile_error"] == "http 503"
    assert len(calls) == len(service.targets())
    assert all(call[1] == proxy for call in calls)  # прямых проверок нет
    assert "secret" not in str(data) and "socks5" not in str(data)
    # Кэш сводки также сохраняет независимые результаты.
    again = await service.summary()
    assert next(h for h in again["hosts"] if h["host"] == MORDOVIA)["latest_check"] == checks


def test_wrapped_dns_error_is_not_reported_as_generic_connection_failure():
    import socket

    error = httpx.ConnectError("private address")
    error.__cause__ = socket.gaierror("not found")
    assert error_name(error) == "dns"


async def test_app_loads_count_once_per_user_per_five_minutes(db):
    now = [10_000.0]
    service, _ = make_service(db, now=now)
    repo = SgoStatusRepository(db)
    for _ in range(5):
        await service.record_account_check(f"https://{DOWN}", False, who=1)
    await service.record_account_check(f"https://{DOWN}", False, who=2)
    await service.record_account_check(f"https://{DOWN}", True)
    await service.record_account_check(f"https://{DOWN}", True)
    assert (await repo.account_checks(0))[DOWN] == {"good": 2, "total": 4}
    now[0] += 301
    await service.record_account_check(f"https://{DOWN}", False, who=1)
    assert (await repo.account_checks(0))[DOWN] == {"good": 2, "total": 5}


def test_school_side_failures_break_the_diary_and_account_ones_do_not():
    from app.netschool.errors import Reason

    assert Reason.SERVER_UNAVAILABLE.breaks_diary and Reason.UNKNOWN.breaks_diary
    assert not any(r.breaks_diary for r in (Reason.AUTH, Reason.CREDENTIALS, Reason.MFA, Reason.ESIA, Reason.RATE_LIMIT))
