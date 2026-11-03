"""Подключение к SQLite.

Одно соединение на процесс. Это осознанный выбор: писателей у SQLite всё
равно один, а единственное соединение снимает целый класс проблем
(«database is locked», незакрытые курсоры, транзакция, начатая в одном
месте и закоммиченная в другом).

Прежняя версия держала состояние в общем словаре в памяти, который писали
поток бота и поток веб-панели одновременно — отсюда затёртые настройки.
Теперь параллельный доступ разруливает база.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from .migrations import migrate

logger = logging.getLogger("netschoolbot.db")


class Database:
    """Асинхронная обёртка над SQLite с сериализацией записи."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        # SQLite не любит параллельные транзакции даже на одном соединении:
        # блокировка делает каждую транзакцию неделимой.
        self._write_lock = asyncio.Lock()
        self._transaction_owner: asyncio.Task | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("База не открыта: вызовите Database.connect()")
        return self._db

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row

        # WAL: читатели не блокируют писателя. Для «бот пишет, веб читает»
        # это ровно то, что нужно.
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA synchronous = NORMAL")
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA busy_timeout = 5000")

        version = await migrate(self._db)
        logger.info("База %s открыта, версия схемы %s", self._path, version)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Атомарная запись: либо всё, либо ничего.

        Именно этого не хватало JSON-файлам — там «прочитал, изменил,
        записал» из двух потоков терял изменения.
        """
        async with self._write_lock:
            db = self.connection
            try:
                # Резервируем запись до чтения: другой процесс не успеет
                # изменить WAL-снимок между SELECT и первым UPDATE.
                await db.execute("BEGIN IMMEDIATE")
                self._transaction_owner = asyncio.current_task()
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            finally:
                self._transaction_owner = None

    @asynccontextmanager
    async def _read_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        # Чтение внутри своей транзакции уже защищено. Чужой SELECT должен
        # закрыть курсор до начала записи: открытый WAL-снимок иначе может
        # вызвать SQLITE_BUSY_SNAPSHOT при записи из второго процесса.
        if self._transaction_owner is asyncio.current_task():
            yield self.connection
        else:
            async with self._write_lock:
                yield self.connection

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        async with self._read_connection() as db:
            async with db.execute(sql, params) as cursor:
                return await cursor.fetchone()

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        async with self._read_connection() as db:
            async with db.execute(sql, params) as cursor:
                return list(await cursor.fetchall())

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        async with self.transaction() as db:
            await db.execute(sql, params)

    async def execute_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        if not rows:
            return
        async with self.transaction() as db:
            await db.executemany(sql, rows)
