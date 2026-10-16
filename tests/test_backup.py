"""Резервные копии: снимок работающей базы, проверка и ротация."""

import datetime as dt

import pytest

from app.tools.backup import BackupError, make_backup, prune, verify_backup
from tests.test_repositories import make_user


async def test_backup_of_live_database_is_restorable(db, users, tmp_path):
    await users.save(make_user())
    path = make_backup(db._path, tmp_path / "backups")
    report = verify_backup(path, expect_users=1)
    assert report.users == 1 and report.schema > 0
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_garbage_is_rejected(tmp_path):
    broken = tmp_path / "netschoolbot-x.sqlite3"
    broken.write_bytes(b"not a database" * 100)
    with pytest.raises(BackupError):
        verify_backup(broken)


async def test_missing_users_are_caught(db, tmp_path):
    path = make_backup(db._path, tmp_path)
    with pytest.raises(BackupError):
        verify_backup(path, expect_users=5)


async def test_prune_keeps_latest_hours_and_one_per_day(db, tmp_path):
    target = tmp_path / "backups"
    # Трое суток копий каждые 6 часов: 5-го, 6-го и 7-го октября.
    for day in (5, 6, 7):
        for hour in (0, 6, 12, 18):
            make_backup(db._path, target, now=dt.datetime(2026, 10, day, hour))
    prune(target, keep=2, hourly=3)
    left = sorted(p.name[len("netschoolbot-"):-len(".sqlite3")] for p in target.glob("*.sqlite3"))
    # Три свежих и последняя копия двух последних суток; 5-е октября ушло целиком.
    assert left == ["20261006-180000", "20261007-060000", "20261007-120000", "20261007-180000"]


async def test_prune_daily_only(db, tmp_path):
    target = tmp_path / "backups"
    for hour in range(4):
        make_backup(db._path, target, now=dt.datetime(2026, 10, 5, hour))
    removed = prune(target, keep=2)
    assert len(removed) == 3
    assert [p.name for p in target.glob("*.sqlite3")] == ["netschoolbot-20261005-030000.sqlite3"]


async def test_restore_returns_previous_state(db, users, tmp_path):
    from app.tools.backup import restore_backup

    await users.save(make_user())
    path = make_backup(db._path, tmp_path / "b")
    await users.save(make_user(telegram_id=2))
    await db.close()
    restore_backup(path, db._path)
    await db.connect()
    assert await users.all_ids() == [1]


async def test_backup_leaves_no_wal_companions(db, tmp_path):
    """Копия не в WAL: проверка не оставляет пустых -wal/-shm, а осиротевшие
    служебные файлы убирает ротация."""
    target = tmp_path / "backups"
    path = make_backup(db._path, target)
    verify_backup(path)
    assert sorted(p.name for p in target.iterdir()) == [path.name]
    (target / "netschoolbot-20200101-000000.sqlite3-wal").write_bytes(b"")
    prune(target, keep=5)
    assert sorted(p.name for p in target.iterdir()) == [path.name]
