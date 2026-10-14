"""Точка входа.

Один процесс и один event loop на бота и на веб. В старой версии веб жил в
отдельном потоке под eventlet и общался с ботом через разделяемые словари —
отсюда гонки данных и `asyncio.run()` на каждый HTTP-запрос.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
import threading

from .context import AppContext
from .logging_setup import drain_to_telegram, setup_logging
from .settings import Settings, SettingsError, load_settings

logger = logging.getLogger("netschoolbot")

# Как часто закрывать простаивающие сессии «Сетевого города».
SESSION_CLEANUP_INTERVAL = 300
# Сколько ждать уборки при остановке, прежде чем выйти принудительно.
SHUTDOWN_TIMEOUT = 30.0


class Supervisor:
    """Следит за фоновыми задачами процесса.

    Веб, бот и фоновые циклы живут задачами одного event loop. Раньше
    упавшую задачу никто не замечал: процесс жив, systemd считает сервис
    рабочим, а nginx отдаёт 502. Теперь конец критичной задачи пишется в лог
    с traceback и останавливает процесс с ненулевым кодом — systemd
    (Restart=always) поднимет его заново.
    """

    def __init__(self) -> None:
        self.failed = asyncio.Event()
        self.exit_code = 0
        self.stopping = False

    def watch(self, task: asyncio.Task, *, critical: bool = True, may_finish: bool = False) -> asyncio.Task:
        """critical — падение задачи останавливает процесс; may_finish — штатный
        возврат задачи не считается сбоем (разовая работа, бот после сигнала)."""
        task.add_done_callback(lambda done: self._finished(done, critical=critical, may_finish=may_finish))
        return task

    def _finished(self, task: asyncio.Task, *, critical: bool, may_finish: bool) -> None:
        if self.stopping:
            return
        name = task.get_name()
        if task.cancelled():
            logger.error("Задача %s отменена", name)
        elif (error := task.exception()) is not None:
            logger.error("Задача %s упала", name, exc_info=error)
        elif may_finish:
            if critical:
                # Бот вернулся сам — так он останавливается по SIGTERM.
                self.failed.set()
            return
        else:
            logger.error("Задача %s завершилась без ошибки, хотя должна работать всегда", name)
        if critical:
            self.exit_code = 1
            self.failed.set()

    def arm_deadline(self, timeout: float = SHUTDOWN_TIMEOUT) -> threading.Timer:
        """Выйти принудительно, если уборка зависла.

        Отдельный поток, а не таймер event loop: зависший loop таймер не вызовет.
        """
        def hard_exit() -> None:
            logger.error("Остановка не уложилась в %s с — выхожу принудительно", timeout)
            logging.shutdown()
            os._exit(self.exit_code or 1)

        timer = threading.Timer(timeout, hard_exit)
        timer.daemon = True
        timer.start()
        return timer


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Бот «Сетевого города»")
    parser.add_argument("--no-web", action="store_true", help="запустить только бота")
    parser.add_argument("--no-bot", action="store_true", help="запустить только веб")
    parser.add_argument(
        "--import-legacy",
        metavar="КАТАЛОГ",
        help="перенести данные из каталога старого проекта и выйти",
    )
    return parser.parse_args(argv)


async def run_import(settings: Settings, source: str) -> int:
    """Перенос данных из старого проекта."""
    from pathlib import Path

    from .context import password_cipher
    from .db.engine import Database
    from .db.import_legacy import LegacyImporter
    from .db.repositories import (
        MarkStateRepository,
        MiniappRepository,
        SessionRepository,
        UserRepository,
    )

    settings.ensure_dirs()
    db = Database(settings.db_path)
    await db.connect()
    try:
        cipher = password_cipher(settings)
        importer = LegacyImporter(
            Path(source),
            users=UserRepository(db, cipher),
            state=MarkStateRepository(db),
            sessions=SessionRepository(db, cipher),
            miniapp=MiniappRepository(db),
        )
        report = await importer.run()
    finally:
        await db.close()

    print(report.as_text())
    return 0


async def cleanup_sessions(context: AppContext) -> None:
    """Сохранять сессии и восстанавливать keep-alive Госуслуг."""
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL)
        try:
            await context.pool.checkpoint_all()
            await context.pool.evict_idle()
            await restore_esia_sessions(context)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Сбой уборки сессий")


async def restore_esia_sessions(context: AppContext) -> None:
    """Вернуть keep-alive сессий Госуслуг после рестарта или сетевого сбоя."""
    semaphore = asyncio.Semaphore(5)

    async def restore(user) -> None:
        async with semaphore:
            if await context.pool.has_client(user.telegram_id):
                return
            if not await context.sessions.load(user.telegram_id):
                return
            try:
                async with context.pool.acquire(user):
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.info("Не удалось восстановить сессию Госуслуг %s: %s", user.telegram_id, exc)

    users = await context.users.all_with_school()
    await asyncio.gather(*(restore(user) for user in users if user.credentials.login_type.is_gosuslugi))


async def run(settings: Settings, args: argparse.Namespace) -> int:
    log_queue = setup_logging(settings)

    from .bot.app import create_bot, create_dispatcher, run_bot
    from .bot.notifier import TelegramNotifier

    bot = create_bot(settings)
    context = await AppContext.create(settings)
    # Уведомитель знает про бота, а бот создаётся позже реестра проверок.
    # Явная привязка вместо ленивых импортов внутри функций.
    from .web.push import PushSender

    push = PushSender(settings.push, context.miniapp)
    if not push.configured:
        logger.info("VAPID-ключи не заданы — push в браузер отключён")
    context.watchers.attach_notifier(TelegramNotifier(bot, context.users, push, context.family))
    if settings.telegram.admin_id:
        async def tell_admin(text: str) -> None:
            await bot.send_message(settings.telegram.admin_id, text)
        context.watchers.outages.attach(tell_admin)
    # Версии «Сетевого города» — в лог и админу о смене; пишутся и без админа.
    from .db.repositories import SgoVersionRepository
    from .notifications.sgo_version import VersionTracker, logindata_fetcher

    versions = VersionTracker(SgoVersionRepository(context.db), logindata_fetcher(context))
    try:
        await versions.load()
    except Exception as exc:  # noqa: BLE001 — без истории версий бот работает
        logger.warning("Версии «Сетевого города» не загружены: %s", exc)
    context.watchers.outages.track_versions(versions)

    supervisor = Supervisor()
    tasks: list[asyncio.Task] = [
        supervisor.watch(asyncio.create_task(cleanup_sessions(context), name="session-cleanup")),
        # Разовая задача: её сбой пишем в лог, но процесс из-за него не роняем.
        supervisor.watch(
            asyncio.create_task(restore_esia_sessions(context), name="esia-restore"),
            critical=False,
            may_finish=True,
        ),
    ]
    if settings.telegram.admin_id:
        tasks.append(
            supervisor.watch(
                asyncio.create_task(
                    drain_to_telegram(log_queue, bot, settings.telegram.admin_id),
                    name="log-drain",
                )
            )
        )

    started = await context.watchers.start_all()
    logger.info("Запущено проверок оценок: %s", started)

    if settings.web.enabled and not args.no_web:
        from .web.server import start_web

        tasks.append(supervisor.watch(asyncio.create_task(start_web(context, bot), name="web")))

    try:
        if not args.no_bot:
            dispatcher = create_dispatcher(context)
            # Бот — такая же задача: если он упадёт на старте (Telegram
            # недоступен), веб не должен остаться висеть без бота и без лога.
            tasks.append(
                supervisor.watch(
                    asyncio.create_task(
                        run_bot(bot, dispatcher, settings.web.miniapp_url if settings.web.enabled else ""),
                        name="bot",
                    ),
                    may_finish=True,
                )
            )
        await supervisor.failed.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Остановка по сигналу")
    finally:
        supervisor.stopping = True
        deadline = supervisor.arm_deadline()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await context.shutdown()
        await bot.session.close()
        deadline.cancel()
    return supervisor.exit_code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings(require_bot_token=not args.import_legacy)
    except SettingsError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    if args.import_legacy:
        return asyncio.run(run_import(settings, args.import_legacy))
    return asyncio.run(run(settings, args))


if __name__ == "__main__":
    raise SystemExit(main())
