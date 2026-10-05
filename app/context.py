"""Сборка приложения.

Единственное место, где создаются зависимости и связываются друг с другом.
Раньше их роль играл модуль `bot/runtime.py` с глобальными переменными,
которые импортировали и правили из десятка мест — из-за чего порядок
инициализации был неявным, а в тестах подменить что-либо было нельзя.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .caldav.service import CaldavService

from .db.crypto import PasswordCipher
from .db.engine import Database
from .db.repositories import (
    CacheRepository,
    MarkStateRepository,
    MiniappRepository,
    SessionRepository,
    UserRepository,
)
from .netschool.service import DiaryService
from .netschool.session import SessionPool
from .notifications.watcher import Notifier, WatcherRegistry
from .settings import Settings

logger = logging.getLogger("netschoolbot")


@dataclass(slots=True)
class AppContext:
    """Всё, что нужно обработчикам бота и веба."""

    settings: Settings
    db: Database
    users: UserRepository
    state: MarkStateRepository
    sessions: SessionRepository
    miniapp: MiniappRepository
    cache: CacheRepository
    pool: SessionPool
    diary: DiaryService
    watchers: WatcherRegistry
    cache_refreshes: dict[tuple, asyncio.Task] = field(default_factory=dict)
    caldav: CaldavService | None = None

    @classmethod
    async def create(cls, settings: Settings, notifier: Notifier | None = None) -> "AppContext":
        settings.ensure_dirs()

        # Правки чужой библиотеки применяются один раз и до первого запроса.
        from .netschool.patches import esia, http

        http.apply(
            timeout=settings.netschool.http_timeout,
            blocked_host_ttl=settings.netschool.blocked_host_ttl,
            fallback_proxy=settings.netschool.fallback_proxy,
        )
        esia.apply()

        db = Database(settings.db_path)
        await db.connect()

        users = UserRepository(db, password_cipher(settings))
        await _import_legacy_if_empty(settings, db, users)
        rewritten = await users.encrypt_stored_passwords()
        if rewritten:
            logger.info("Зашифровано паролей в базе: %s", rewritten)
        sessions = SessionRepository(db)
        pool = SessionPool(settings.netschool, sessions)
        state = MarkStateRepository(db)
        diary = DiaryService(pool, users)

        return cls(
            settings=settings,
            db=db,
            users=users,
            state=state,
            sessions=sessions,
            miniapp=MiniappRepository(db, login_code_ttl=settings.web.login_code_ttl),
            cache=CacheRepository(db),
            pool=pool,
            diary=diary,
            watchers=WatcherRegistry(
                users=users, state=state, diary=diary, notifier=notifier
            ),
        )

    async def shutdown(self) -> None:
        """Аккуратно закрыть всё, что открыли. Порядок важен."""
        await self.watchers.stop_all()
        await self.stop_cache_refreshes()
        await self.pool.close_all()
        await self.db.close()
        logger.info("Приложение остановлено")

    async def stop_cache_refreshes(self) -> None:
        tasks = list(self.cache_refreshes.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cache_refreshes.clear()


def password_cipher(settings: Settings) -> PasswordCipher:
    cipher = PasswordCipher(settings.secret_key)
    if not cipher.enabled:
        logger.warning(
            "NETSCHOOL_SECRET_KEY не задан — пароли хранятся в базе открытым текстом"
        )
    return cipher


async def _import_legacy_if_empty(settings: Settings, db: Database, users: UserRepository) -> None:
    """Перенести данные старой версии, если база только что создана.

    Развёртывание автоматическое, а перенос вручную требует захода на сервер.
    Без этого после обновления бот поднялся бы с пустой базой, и все
    пользователи разом потеряли бы школу и настройки.

    Условие намеренно узкое: переносим, только если пользователей нет вообще
    и рядом лежат файлы старой версии. Повторно ничего не произойдёт.
    """
    if await users.all_ids():
        return

    legacy_users_file = settings.data_dir / "netschool_users" / "netschool_users.json"
    if not legacy_users_file.exists():
        return

    from .db.import_legacy import LegacyImporter
    from .db.repositories import MarkStateRepository, MiniappRepository, SessionRepository

    logger.info("База пуста, а данные старой версии на месте — переношу")
    importer = LegacyImporter(
        settings.data_dir,
        users=users,
        state=MarkStateRepository(db),
        sessions=SessionRepository(db),
        miniapp=MiniappRepository(db, login_code_ttl=settings.web.login_code_ttl),
    )
    report = await importer.run()
    for line in report.as_text().splitlines():
        logger.info("Перенос: %s", line)
