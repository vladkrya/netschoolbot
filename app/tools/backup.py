"""Резервная копия базы с проверкой, что из неё можно восстановиться.

    python -m app.tools.backup            # копия в data/backups, хранить 14 последних
    python -m app.tools.backup --keep 30
    python -m app.tools.backup --verify data/backups/netschoolbot-20261005-0300.sqlite3

Копия снимается SQLite-ным online backup API: база в WAL, бот пишет в неё
прямо сейчас, и простое копирование файла дало бы несогласованный снимок.

Проверка открывает копию как отдельную базу и смотрит то, без чего
восстановление бессмысленно: целостность страниц, версию схемы и что
пользователи на месте. Ключа шифрования паролей в копии нет — он в .env, —
поэтому копия без ключа ничего не раскрывает, но и восстановленная без
ключа база заставит всех заново пройти /login.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from ..db.migrations import MIGRATIONS
from ..settings import load_settings

PREFIX = "netschoolbot-"


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BackupReport:
    path: Path
    size: int
    users: int
    schema: int


def make_backup(db_path: Path, target_dir: Path, *, now: dt.datetime | None = None) -> Path:
    """Снять согласованную копию работающей базы."""
    if not db_path.exists():
        raise BackupError(f"Базы нет: {db_path}")
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"{PREFIX}{stamp}.sqlite3"
    partial = target.with_suffix(".partial")
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(partial)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    # Под настоящим именем — только законченная копия.
    partial.replace(target)
    return target


def verify_backup(path: Path, *, expect_users: int | None = None) -> BackupReport:
    """Убедиться, что из копии можно восстановиться."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise BackupError(f"Копия повреждена: {integrity}")
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        if schema < 1 or schema > len(MIGRATIONS):
            raise BackupError(f"Неожиданная версия схемы: {schema}")
        users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"Копия не читается: {exc}") from exc
    finally:
        connection.close()
    if expect_users is not None and users < expect_users:
        raise BackupError(f"В копии пользователей меньше, чем в базе: {users} < {expect_users}")
    return BackupReport(path=path, size=path.stat().st_size, users=users, schema=schema)


def schema_version(path: Path) -> int:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()


def restore_backup(source: Path, db_path: Path) -> BackupReport:
    """Вернуть базу из копии. Служба должна быть остановлена.

    Нужно при откате выкладки: миграции схемы необратимы, и прежний код
    откажется работать с базой, которую уже обновил новый.
    """
    report = verify_backup(source)
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(db_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return report


def prune(target_dir: Path, keep: int) -> list[Path]:
    """Удалить старые копии, оставив `keep` последних. Возвращает удалённые."""
    backups = sorted(target_dir.glob(f"{PREFIX}[0-9]*.sqlite3"))
    stale = backups[:-keep] if keep > 0 else []
    for path in stale:
        path.unlink()
    return stale


def _count_users(db_path: Path) -> int:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Резервная копия базы бота")
    parser.add_argument("--keep", type=int, default=14, help="сколько последних копий хранить")
    parser.add_argument("--dir", type=Path, help="куда класть копии (по умолчанию data/backups)")
    parser.add_argument("--verify", type=Path, help="только проверить готовую копию")
    parser.add_argument("--restore", type=Path, help="вернуть базу из копии (службу остановить заранее)")
    parser.add_argument("--latest", action="store_true", help="напечатать путь к последней копии")
    parser.add_argument("--schema", type=Path, help="напечатать версию схемы базы")
    args = parser.parse_args(argv)

    settings = load_settings(require_bot_token=False)
    try:
        if args.schema:
            print(schema_version(args.schema))
            return 0
        if args.latest:
            found = sorted((args.dir or settings.data_dir / "backups").glob(f"{PREFIX}[0-9]*.sqlite3"))
            if not found:
                raise BackupError("Копий нет")
            print(found[-1])
            return 0
        if args.restore:
            report = restore_backup(args.restore, settings.db_path)
        elif args.verify:
            report = verify_backup(args.verify)
        else:
            target_dir = args.dir or settings.data_dir / "backups"
            live_users = _count_users(settings.db_path)
            path = make_backup(settings.db_path, target_dir)
            report = verify_backup(path, expect_users=live_users)
            removed = prune(target_dir, args.keep)
            if removed:
                print(f"Удалено старых копий: {len(removed)}")
    except BackupError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    print(
        f"OK {report.path} · {report.size // 1024} КБ · пользователей {report.users} · "
        f"схема {report.schema}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
