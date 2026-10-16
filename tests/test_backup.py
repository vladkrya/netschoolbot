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


async def test_prune_keeps_latest(db, tmp_path):
    for hour in range(4):
        make_backup(db._path, tmp_path, now=dt.datetime(2026, 10, 5, hour))
    removed = prune(tmp_path, keep=2)
    assert len(removed) == 2
    assert sorted(p.name for p in tmp_path.glob("*.sqlite3"))[0].endswith("020000.sqlite3")


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


async def test_replica_check_sees_fresh_probe(db, users, tmp_path):
    from app.tools.backup import check_replica

    await users.save(make_user())

    def restore(config, db_path, target):
        make_backup(db_path, target.parent).replace(target)

    report = check_replica(db._path, tmp_path / "litestream.yml", wait=0, restore=restore)
    assert report.users == 1


async def test_replica_check_catches_stale_replica(db, users, tmp_path):
    from app.tools.backup import check_replica

    await users.save(make_user())
    stale = make_backup(db._path, tmp_path / "stale")

    def restore(config, db_path, target):
        target.write_bytes(stale.read_bytes())

    with pytest.raises(BackupError, match="отстала"):
        check_replica(db._path, tmp_path / "litestream.yml", wait=0, restore=restore)
