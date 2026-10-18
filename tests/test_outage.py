"""Админ узнаёт о простое школьного сервера один раз — и о возвращении."""

from app.notifications.outage import OutageMonitor


async def test_outage_and_recovery_are_reported_once():
    sent: list[str] = []
    now = [1000.0]

    async def send(text):
        sent.append(text)

    monitor = OutageMonitor(send, clock=lambda: now[0])
    url = "https://sgo.example.ru/"
    for _ in range(4):
        await monitor.record(url, ok=False, server_down=True)
        now[0] += 30
    assert sent == []  # мало проверок — ещё не простой
    await monitor.record(url, ok=False, server_down=True)
    assert len(sent) == 1 and "sgo.example.ru не отвечает: 5 из 5" in sent[0]
    for _ in range(5):
        await monitor.record(url, ok=False, server_down=True)
    assert len(sent) == 1  # повторно не пишем
    for _ in range(3):
        now[0] += 60
        await monitor.record(url, ok=True)
    assert len(sent) == 2 and "снова отвечает" in sent[1]


async def test_login_errors_do_not_count_and_other_school_is_separate():
    sent: list[str] = []

    async def send(text):
        sent.append(text)

    monitor = OutageMonitor(send)
    for _ in range(10):
        await monitor.record("https://a.example.ru", ok=False, server_down=False)
        await monitor.record("https://b.example.ru", ok=True)
    assert sent == []


def test_healthcheck_alerts_after_two_failures_and_on_recovery():
    from app.tools.healthcheck import step

    state, message = step({}, healthy=False, now=0)
    assert message is None
    state, message = step(state, healthy=False, now=120)
    assert message and "не отвечает" in message
    state, message = step(state, healthy=False, now=240)
    assert message is None  # не повторяем
    state, message = step(state, healthy=True, now=600)
    assert message and "снова отвечает" in message and "10 мин" in message
    state, message = step(state, healthy=True, now=720)
    assert message is None


class FakeVersions:
    """Сервер, отдающий версию из logindata; None — не ответил."""

    def __init__(self, version="5.58.6"):
        self.version = version
        self.calls = 0

    async def __call__(self, url):
        self.calls += 1
        if self.version is None:
            raise ConnectionError("нет ответа")
        return self.version


async def test_version_is_stored_and_change_is_reported(db):
    from app.db.repositories import SgoVersionRepository
    from app.notifications.sgo_version import CHECK_EVERY_S, VersionTracker

    sent: list[str] = []

    async def send(text):
        sent.append(text)

    now = [1000.0]
    server = FakeVersions()
    repo = SgoVersionRepository(db)
    tracker = VersionTracker(repo, server, clock=lambda: now[0])
    host, url = "sgo.example.ru", "https://sgo.example.ru/"

    # Впервые: записали, админа не тревожим.
    assert await tracker.check(host, url, send) == ("", "5.58.6")
    assert sent == [] and await repo.get(host) == "5.58.6"
    # Та же версия — тишина; сверка не чаще раза в CHECK_EVERY_S.
    assert await tracker.check(host, url, send) == ("5.58.6", "5.58.6")
    assert sent == [] and not tracker.due(host)
    now[0] += CHECK_EVERY_S
    assert tracker.due(host)
    # Обновили — админу и в БД.
    server.version = "5.58.7"
    assert await tracker.check(host, url, send) == ("5.58.6", "5.58.7")
    assert sent == ["⚠️ «Сетевой город» sgo.example.ru обновился: 5.58.6 → 5.58.7"]
    (row,) = await repo.all()
    assert (row["version"], row["previous"]) == ("5.58.7", "5.58.6")
    # Сервер не ответил — версия прежняя, проверка не падает.
    server.version = None
    assert await tracker.check(host, url, send) == ("", "")
    assert await repo.get(host) == "5.58.7"
    # После перезапуска известная версия берётся из БД.
    fresh = VersionTracker(repo, server)
    assert fresh.known(host) == ""
    await fresh.load()
    assert fresh.known(host) == "5.58.7"


async def test_outage_messages_carry_version(db):
    import asyncio

    from app.db.repositories import SgoVersionRepository
    from app.notifications.sgo_version import VersionTracker

    sent: list[str] = []

    async def send(text):
        sent.append(text)

    now = [1000.0]
    server = FakeVersions("5.58.6")
    tracker = VersionTracker(SgoVersionRepository(db), server, clock=lambda: now[0])
    monitor = OutageMonitor(send, clock=lambda: now[0])
    monitor.track_versions(tracker)
    url = "https://sgo.example.ru/"

    # Удачная проверка сверяет версию в фоне, один раз за период.
    await monitor.record(url, ok=True)
    await monitor.record(url, ok=True)
    await asyncio.gather(*tracker._tasks.values())
    assert server.calls == 1 and tracker.known("sgo.example.ru") == "5.58.6"

    for _ in range(10):
        now[0] += 30
        await monitor.record(url, ok=False, server_down=True)
    assert len(sent) == 1 and "не отвечает" in sent[-1] and "версия до простоя 5.58.6" in sent[-1]

    # Вернулся обновлённым: одно сообщение и с простоем, и с версией.
    server.version = "5.58.7"
    for _ in range(3):
        now[0] += 60
        await monitor.record(url, ok=True)
    assert len(sent) == 2
    assert "снова отвечает" in sent[1] and "версия обновилась: 5.58.6 → 5.58.7" in sent[1]
    assert server.calls == 2  # после простоя — сразу, не дожидаясь периода


async def test_restart_does_not_repeat_outage(db):
    """После перезапуска бот помнит, что о простое уже сказал."""
    sent: list[str] = []
    now = [1000.0]

    async def send(text):
        sent.append(text)

    url = "https://sgo.example.ru/"
    monitor = OutageMonitor(send, clock=lambda: now[0])
    await monitor.persist(db)
    for _ in range(5):
        await monitor.record(url, ok=False, server_down=True)
        now[0] += 30
    assert len(sent) == 1

    restarted = OutageMonitor(send, clock=lambda: now[0])
    await restarted.persist(db)
    for _ in range(10):
        await restarted.record(url, ok=False, server_down=True)
        now[0] += 30
    assert len(sent) == 1  # «не отвечает» не повторяем
    for _ in range(3):
        await restarted.record(url, ok=True)
    assert len(sent) == 2 and "снова отвечает" in sent[1]

    again = OutageMonitor(send, clock=lambda: now[0])
    await again.persist(db)
    for _ in range(3):
        await again.record(url, ok=True)
    assert len(sent) == 2  # ожил — забыли, второй раз «снова отвечает» не шлём
