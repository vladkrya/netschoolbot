"""Вернуть одному пользователю его данные из резервной копии.

    python -m app.tools.restore_user --from data/backups/netschoolbot-20261016-033410.sqlite3 --user 1588860201
    python -m app.tools.restore_user --from … --user … --apply

Без --apply только показывает, что вернётся. С --apply сначала снимает
свежую копию рабочей базы, потом одной транзакцией дописывает недостающее.
Бота останавливать не нужно. Существующее не удаляется и не перезаписывается,
кроме истории оценок: она показывается по возрастанию id, поэтому старые
записи нельзя дописать в конец — история сливается, сортируется по времени
и записывается заново.

Возвращается то, что пользователь сделал сам и чего «Сетевой город» не
восстановит: отметки «выполнено» у ДЗ, история оценок, заметки и задачи,
настройки приложения (web_prefs, в том числе свои уроки — lessonEdits).

Копию на нужный момент из Litestream:
    litestream restore -config /etc/litestream.yml -timestamp 2026-10-16T06:00:00Z \\
        -o /tmp/point.sqlite3 /opt/netschoolbot/data/netschoolbot.sqlite3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..settings import load_settings
from .backup import make_backup

NOTE_COLUMNS = ("day", "subject", "text", "done", "created_at", "at", "remind", "remind_at")
HISTORY_COLUMNS = ("kind", "payload", "old_mark", "new_mark", "created_at")
HISTORY_LIMIT = 500


class RestoreError(RuntimeError):
    pass


@dataclass(slots=True)
class RestoreReport:
    homework_done: int = 0
    user_notes: int = 0
    prefs_keys: list[str] = field(default_factory=list)
    lesson_edits: bool = False
    mark_history: int = 0
    history_total: int = 0
    before: dict[str, object] = field(default_factory=dict)
    after: dict[str, object] = field(default_factory=dict)


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _prefs(connection: sqlite3.Connection, user: int) -> dict | None:
    if "web_prefs" not in _tables(connection):
        return None
    row = connection.execute("SELECT payload FROM web_prefs WHERE telegram_id = ?", (user,)).fetchone()
    return json.loads(row[0]) if row else None


def counts(connection: sqlite3.Connection, user: int) -> dict[str, object]:
    tables = _tables(connection)

    def count(table: str) -> int | None:
        if table not in tables:
            return None
        return connection.execute(f"SELECT COUNT(*) FROM {table} WHERE telegram_id = ?", (user,)).fetchone()[0]

    prefs = _prefs(connection, user)
    return {
        "homework_done": count("homework_done"),
        "mark_history": count("mark_history"),
        "user_notes": count("user_notes"),
        "web_prefs": len(prefs) if prefs is not None else None,
        "lessonEdits": len(prefs.get("lessonEdits") or []) if prefs is not None else None,
    }


def _rows(connection: sqlite3.Connection, table: str, columns: tuple[str, ...], user: int) -> list[tuple]:
    if table not in _tables(connection):
        return []
    available = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    # В старых копиях части колонок ещё нет — берём значение по умолчанию.
    select = ", ".join(column if column in available else "NULL" for column in columns)
    return connection.execute(
        f"SELECT {select} FROM {table} WHERE telegram_id = ? ORDER BY rowid", (user,)
    ).fetchall()


def restore_user(live: sqlite3.Connection, source: sqlite3.Connection, user: int, *, apply: bool) -> RestoreReport:
    """Дописать в `live` недостающие данные `user` из `source`.

    Без `apply` транзакция откатывается: отчёт тот же, база не меняется.
    """
    live.execute("BEGIN IMMEDIATE")
    try:
        if not live.execute("SELECT 1 FROM users WHERE telegram_id = ?", (user,)).fetchone():
            raise RestoreError(f"Пользователя {user} нет в базе: пусть сначала напишет боту /start")
        report = RestoreReport(before=counts(live, user))

        rows = source.execute(
            "SELECT homework_key, done_at FROM homework_done WHERE telegram_id = ?", (user,)
        ).fetchall() if "homework_done" in _tables(source) else []
        changes = live.total_changes
        live.executemany(
            "INSERT OR IGNORE INTO homework_done (telegram_id, homework_key, done_at) VALUES (?, ?, ?)",
            [(user, key, done_at) for key, done_at in rows],
        )
        report.homework_done = live.total_changes - changes

        # Одна и та же заметка — совпадают день, текст и время создания.
        have = {(r[0], r[2], r[4]) for r in _rows(live, "user_notes", NOTE_COLUMNS, user)}
        notes = [r for r in _rows(source, "user_notes", NOTE_COLUMNS, user) if (r[0], r[2], r[4]) not in have]
        placeholders = ", ".join("?" * (len(NOTE_COLUMNS) + 1))
        live.executemany(
            f"INSERT INTO user_notes (telegram_id, {', '.join(NOTE_COLUMNS)}) VALUES ({placeholders})",
            [(user, *r) for r in notes],
        )
        report.user_notes = len(notes)

        old_prefs = _prefs(source, user) or {}
        prefs = _prefs(live, user)
        merged = dict(prefs or {})
        report.prefs_keys = [key for key in old_prefs if key not in merged]
        merged.update({key: old_prefs[key] for key in report.prefs_keys})
        # Пустой список своих уроков — не настройка, а след сброса.
        lessons = merged.get("lessonEdits")
        if isinstance(lessons, list) and not lessons and old_prefs.get("lessonEdits"):
            merged["lessonEdits"] = old_prefs["lessonEdits"]
            report.lesson_edits = True
        payload = json.dumps(merged, ensure_ascii=False)
        if prefs is None and old_prefs:
            live.execute("INSERT INTO web_prefs (telegram_id, payload) VALUES (?, ?)", (user, payload))
        elif report.prefs_keys or report.lesson_edits:
            live.execute(
                "UPDATE web_prefs SET payload = ?, updated_at = datetime('now') WHERE telegram_id = ?",
                (payload, user),
            )

        current = _rows(live, "mark_history", HISTORY_COLUMNS, user)
        known = set(current)
        missing = [r for r in _rows(source, "mark_history", HISTORY_COLUMNS, user) if r not in known]
        report.mark_history = len(missing)
        if missing:
            # Сортировка устойчивая: записи одной секунды остаются в прежнем порядке.
            history = sorted(current + missing, key=lambda r: r[4])[-HISTORY_LIMIT:]
            report.history_total = len(history)
            live.execute("DELETE FROM mark_history WHERE telegram_id = ?", (user,))
            placeholders = ", ".join("?" * (len(HISTORY_COLUMNS) + 1))
            live.executemany(
                f"INSERT INTO mark_history (telegram_id, {', '.join(HISTORY_COLUMNS)}) VALUES ({placeholders})",
                [(user, *r) for r in history],
            )
        else:
            report.history_total = len(current)

        broken = live.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise RestoreError(f"Нарушены внешние ключи: {broken[:3]}")
        report.after = counts(live, user)
    except BaseException:
        live.execute("ROLLBACK")
        raise
    live.execute("COMMIT" if apply else "ROLLBACK")
    return report


def _open_source(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise RestoreError(f"Копии нет: {path}")
    # immutable — копию только читаем, и рядом не появятся -wal/-shm.
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)


def _print(report: RestoreReport, applied: bool) -> None:
    print("Вернулось:" if applied else "Вернётся (ничего не записано, нужен --apply):")
    print(f"  homework_done  +{report.homework_done}")
    print(f"  user_notes     +{report.user_notes}")
    print(f"  web_prefs      ключи: {', '.join(report.prefs_keys) or 'нет'}"
          f"{'; lessonEdits из копии' if report.lesson_edits else ''}")
    print(f"  mark_history   +{report.mark_history} (всего {report.history_total})")
    print("До:    ", report.before)
    print("После: ", report.after)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Вернуть пользователю данные из резервной копии")
    parser.add_argument("--from", dest="source", type=Path, required=True, help="файл копии")
    parser.add_argument("--user", type=int, required=True, help="telegram_id")
    parser.add_argument("--apply", action="store_true", help="записать (без него — только показать)")
    args = parser.parse_args(argv)

    settings = load_settings(require_bot_token=False)
    try:
        source = _open_source(args.source)
        if args.apply:
            fresh = make_backup(settings.db_path, settings.data_dir / "backups")
            print(f"Свежая копия перед записью: {fresh}")
        live = sqlite3.connect(settings.db_path, isolation_level=None, timeout=30)
        live.execute("PRAGMA busy_timeout = 30000")
        live.execute("PRAGMA foreign_keys = ON")
        report = restore_user(live, source, args.user, apply=args.apply)
    except (RestoreError, sqlite3.DatabaseError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    _print(report, args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
