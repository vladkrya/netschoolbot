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
