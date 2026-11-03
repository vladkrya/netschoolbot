"""Два соединения, как у бота и служебной команды индекса username."""
import asyncio
import logging
import sqlite3

import aiosqlite
import pytest

from app.db.engine import Database
from app.logging_setup import AdminFormatter, SafeFormatter


async def test_open_reader_cannot_overlap_write_after_other_process_commit(db, tmp_path, monkeypatch):
    other = Database(tmp_path / 'test.sqlite3')
    await other.connect()
    await db.execute('CREATE TABLE lock_probe (value INTEGER)')
    await db.execute_many('INSERT INTO lock_probe VALUES (?)', [(1,), (2,)])
    opened, release = asyncio.Event(), asyncio.Event()
    original = aiosqlite.Cursor.fetchone
    reader = None

    async def paused_fetch(cursor):
        if asyncio.current_task() is reader:
            opened.set()
            await release.wait()
        return await original(cursor)

    monkeypatch.setattr(aiosqlite.Cursor, 'fetchone', paused_fetch)
    writer = None
    try:
        reader = asyncio.create_task(db.fetch_one('SELECT value FROM lock_probe'))
        await asyncio.wait_for(opened.wait(), 2)
        # WAL разрешает второму процессу запись при открытом курсоре первого.
        await other.execute('UPDATE lock_probe SET value = value + 10')
        writer = asyncio.create_task(db.execute('UPDATE lock_probe SET value = value + 100'))
        finished, _ = await asyncio.wait({writer}, timeout=.05)
        release.set()
        result = await asyncio.wait_for(reader, 2)
        await asyncio.wait_for(writer, 2)
        assert not finished, 'запись должна дождаться закрытия курсора'
        assert result['value'] == 1
        assert [row['value'] for row in await db.fetch_all('SELECT value FROM lock_probe')] == [111, 112]
    finally:
        release.set()
        if reader:
            await asyncio.gather(reader, return_exceptions=True)
        if writer:
            await asyncio.gather(writer, return_exceptions=True)
        await other.close()


async def test_read_modify_write_reserves_writer_before_read_and_supports_own_reads(db, tmp_path):
    other = Database(tmp_path / 'test.sqlite3')
    await other.connect()
    await db.execute('CREATE TABLE counter (value INTEGER)')
    await db.execute('INSERT INTO counter VALUES (0)')
    concurrent = None
    try:
        async with db.transaction() as connection:
            async with asyncio.timeout(2):
                row = await db.fetch_one('SELECT value FROM counter')
            concurrent = asyncio.create_task(other.execute('UPDATE counter SET value = value + 10'))
            finished, _ = await asyncio.wait({concurrent}, timeout=.05)
            await connection.execute('UPDATE counter SET value = ?', (row['value'] + 1,))
        await asyncio.wait_for(concurrent, 2)
        assert not finished, 'другой процесс не должен писать до завершения транзакции'
        assert (await db.fetch_one('SELECT value FROM counter'))['value'] == 11
    finally:
        if concurrent:
            await asyncio.gather(concurrent, return_exceptions=True)
        await other.close()


async def test_rollback_releases_read_and_write_access(db):
    await db.execute('CREATE TABLE counter (value INTEGER)')
    with pytest.raises(RuntimeError):
        async with db.transaction() as connection:
            await connection.execute('INSERT INTO counter VALUES (1)')
            assert (await db.fetch_one('SELECT value FROM counter'))['value'] == 1
            raise RuntimeError('rollback')
    assert await asyncio.wait_for(db.fetch_all('SELECT * FROM counter'), 2) == []
    await asyncio.wait_for(db.execute('INSERT INTO counter VALUES (2)'), 2)


def test_sqlite_diagnostics_show_code_without_exception_text():
    connection = sqlite3.connect(':memory:')
    try:
        try:
            connection.execute('SELECT secret_column FROM private_table')
        except sqlite3.OperationalError as error:
            record = logging.LogRecord('netschoolbot.db', logging.ERROR, __file__, 1, 'Ошибка базы', (),
                                       (type(error), error, error.__traceback__))
            for formatter in (SafeFormatter(), AdminFormatter()):
                text = formatter.format(record)
                assert 'OperationalError(SQLITE_ERROR)' in text
                assert 'private_table' not in text and 'secret_column' not in text
    finally:
        connection.close()
