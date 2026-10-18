"""Падение фоновой задачи останавливает процесс, а не проходит молча."""

import asyncio
import logging
import threading

import pytest

from app import main
from app.main import Supervisor


async def crashing_web() -> None:
    await asyncio.sleep(0)
    raise OSError("address already in use")


async def forever() -> None:
    await asyncio.Event().wait()


async def test_web_crash_stops_process_with_error_code(caplog):
    supervisor = Supervisor()
    supervisor.watch(asyncio.create_task(crashing_web(), name="web"))

    with caplog.at_level(logging.ERROR, logger="netschoolbot"):
        await asyncio.wait_for(supervisor.failed.wait(), timeout=1)

    assert supervisor.exit_code == 1
    record = next(r for r in caplog.records if "web" in r.getMessage())
    assert record.exc_info and isinstance(record.exc_info[1], OSError)


async def test_web_finishing_quietly_is_a_failure(caplog):
    async def quiet() -> None:
        return None

    supervisor = Supervisor()
    supervisor.watch(asyncio.create_task(quiet(), name="web"))

    with caplog.at_level(logging.ERROR, logger="netschoolbot"):
        await asyncio.wait_for(supervisor.failed.wait(), timeout=1)

    assert supervisor.exit_code == 1
    assert any("web" in r.getMessage() for r in caplog.records)


async def test_external_cancel_of_web_is_a_failure():
    supervisor = Supervisor()
    task = supervisor.watch(asyncio.create_task(forever(), name="web"))
    task.cancel()

    await asyncio.wait_for(supervisor.failed.wait(), timeout=1)
    assert supervisor.exit_code == 1


async def test_bot_returning_after_signal_is_clean_stop():
    async def bot() -> None:
        return None

    supervisor = Supervisor()
    supervisor.watch(asyncio.create_task(bot(), name="bot"), may_finish=True)

    await asyncio.wait_for(supervisor.failed.wait(), timeout=1)
    assert supervisor.exit_code == 0


async def test_one_shot_task_failure_is_logged_but_not_fatal(caplog):
    async def restore() -> None:
        raise RuntimeError("Госуслуги недоступны")

    supervisor = Supervisor()
    task = supervisor.watch(asyncio.create_task(restore(), name="esia-restore"), critical=False, may_finish=True)
    with caplog.at_level(logging.ERROR, logger="netschoolbot"):
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)

    assert not supervisor.failed.is_set()
    assert any(r.exc_info for r in caplog.records)


async def test_cancel_during_shutdown_is_not_reported(caplog):
    supervisor = Supervisor()
    task = supervisor.watch(asyncio.create_task(forever(), name="web"))
    await asyncio.sleep(0)
    supervisor.stopping = True
    task.cancel()
    with caplog.at_level(logging.ERROR, logger="netschoolbot"):
        with pytest.raises(asyncio.CancelledError):
            await task

    assert supervisor.exit_code == 0
    assert not caplog.records


def test_hung_shutdown_exits_hard(monkeypatch):
    exited = threading.Event()
    codes: list[int] = []

    def fake_exit(code: int) -> None:
        codes.append(code)
        exited.set()

    monkeypatch.setattr(main.os, "_exit", fake_exit)
    supervisor = Supervisor()
    supervisor.exit_code = 1
    supervisor.arm_deadline(0.01)

    assert exited.wait(1)
    assert codes == [1]


async def test_run_exits_nonzero_when_web_crashes(monkeypatch, tmp_path):
    """Сквозной путь run(): упал веб — run() возвращает 1 и всё убирает."""
    import types

    from app.web import server

    stopped: list[str] = []

    class FakeWatchers:
        outages = types.SimpleNamespace(attach=lambda *_: None, track_versions=lambda *_: None)
        notifier = None

        def attach_notifier(self, _):
            pass

        async def start_all(self):
            return 0

    class FakeContext:
        watchers = FakeWatchers()
        miniapp = users = db = family = notes = None
        attachments = types.SimpleNamespace(enabled=False)

        async def shutdown(self):
            stopped.append("context")

    class FakeVersions:
        def __init__(self, *_):
            pass

        async def load(self):
            pass

    async def fake_create(_settings):
        return FakeContext()

    async def bot_forever(*_):
        await asyncio.Event().wait()

    fake_bot = types.SimpleNamespace(session=types.SimpleNamespace(close=lambda: asyncio.sleep(0)))
    from app.bot import app as bot_app
    from app.notifications import sgo_version
    from app.web import push

    monkeypatch.setattr(main, "setup_logging", lambda _settings: asyncio.Queue())
    monkeypatch.setattr(main.AppContext, "create", staticmethod(fake_create))
    monkeypatch.setattr(bot_app, "create_bot", lambda _settings: fake_bot)
    monkeypatch.setattr(bot_app, "create_dispatcher", lambda _context: None)
    monkeypatch.setattr(bot_app, "run_bot", bot_forever)
    monkeypatch.setattr(push, "PushSender", lambda *_: types.SimpleNamespace(configured=True))
    monkeypatch.setattr(sgo_version, "VersionTracker", FakeVersions)
    monkeypatch.setattr(sgo_version, "logindata_fetcher", lambda _context: None)
    from app.status import service as status_service
    monkeypatch.setattr(status_service, "StatusService",
                        lambda *_, **__: types.SimpleNamespace(run=forever))
    from app.status import alerts as status_alerts
    monkeypatch.setattr(status_alerts, "SgoAlerts",
                        lambda *_, **__: types.SimpleNamespace(run=forever))
    monkeypatch.setattr(server, "start_web", lambda *_: crashing_web())
    monkeypatch.setattr(main, "cleanup_sessions", lambda _context: forever())
    monkeypatch.setattr(main, "restore_esia_sessions", lambda _context: asyncio.sleep(0))

    settings = types.SimpleNamespace(
        telegram=types.SimpleNamespace(admin_id=0),
        web=types.SimpleNamespace(enabled=True, miniapp_url=""),
        push=None,
        netschool=types.SimpleNamespace(proxy_for=lambda _url: None),
        secret_key="",
    )
    args = main.parse_args([])

    code = await asyncio.wait_for(main.run(settings, args), timeout=5)

    assert code == 1
    assert stopped == ["context"]
