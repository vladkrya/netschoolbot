"""Возврат данных одного пользователя из копии: только недостающее."""

import json
import sqlite3

import pytest

from app.tools.backup import make_backup
from app.tools.restore_user import RestoreError, restore_user
from tests.test_repositories import make_user

USER = 1


def _connect(path):
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


async def _history(db, kind, created_at, user=USER):
    await db.execute(
        "INSERT INTO mark_history (telegram_id, kind, payload, created_at) VALUES (?, ?, '{}', ?)",
        (user, kind, created_at),
    )


async def _seed(db, users):
    """Состояние до сброса: и у нашего пользователя, и у соседа."""
    await users.save(make_user())
    await users.save(make_user(telegram_id=2))
    for user in (USER, 2):
        await db.execute(
            "INSERT INTO homework_done (telegram_id, homework_key, done_at) VALUES (?, 'hw:1', '2026-10-15 10:00:00')",
            (user,),
        )
        await _history(db, "old", "2026-10-15 11:00:00", user)
    await db.execute(
        "INSERT INTO user_notes (telegram_id, day, text, created_at) VALUES (?, '2026-10-17', 'купить тетрадь', 1.5)",
        (USER,),
    )
    await db.execute(
        "INSERT INTO web_prefs (telegram_id, payload) VALUES (?, ?)",
        (USER, json.dumps({"theme": "dark", "lessonEdits": [{"day": 1}]})),
    )


async def test_restores_only_missing_and_keeps_new(db, users, tmp_path):
    await _seed(db, users)
    backup = make_backup(db._path, tmp_path / "b")
    # Сброс, как делал forget_all, и новое после него.
    await db.execute("DELETE FROM homework_done WHERE telegram_id = ?", (USER,))
    await db.execute("DELETE FROM mark_history WHERE telegram_id = ?", (USER,))
    await _history(db, "new", "2026-10-16 07:00:00")
    await db.execute(
        "UPDATE web_prefs SET payload = ? WHERE telegram_id = ?",
        (json.dumps({"theme": "light", "lessonEdits": []}), USER),
    )

    live = _connect(db._path)
    report = restore_user(live, _connect(backup), USER, apply=True)

    assert report.homework_done == 1 and report.mark_history == 1
    assert report.user_notes == 0  # заметка не терялась — дубля нет
    prefs = json.loads(live.execute("SELECT payload FROM web_prefs WHERE telegram_id = ?", (USER,)).fetchone()[0])
    assert prefs == {"theme": "light", "lessonEdits": [{"day": 1}]}  # своё не тронуто, пустые уроки — из копии
    # История по возрастанию id: старое ниже нового.
    kinds = [r[0] for r in live.execute("SELECT kind FROM mark_history WHERE telegram_id = ? ORDER BY id", (USER,))]
    assert kinds == ["old", "new"]
    # Сосед не задет.
    assert live.execute("SELECT COUNT(*) FROM mark_history WHERE telegram_id = 2").fetchone()[0] == 1


async def test_dry_run_writes_nothing(db, users, tmp_path):
    await _seed(db, users)
    backup = make_backup(db._path, tmp_path / "b")
    await db.execute("DELETE FROM homework_done WHERE telegram_id = ?", (USER,))

    live = _connect(db._path)
    report = restore_user(live, _connect(backup), USER, apply=False)
    assert report.homework_done == 1 and report.after["homework_done"] == 1
    assert live.execute("SELECT COUNT(*) FROM homework_done WHERE telegram_id = ?", (USER,)).fetchone()[0] == 0


async def test_missing_user_is_refused(db, users, tmp_path):
    await _seed(db, users)
    backup = make_backup(db._path, tmp_path / "b")
    await db.execute("DELETE FROM users WHERE telegram_id = ?", (USER,))

    live = _connect(db._path)
    with pytest.raises(RestoreError, match="/start"):
        restore_user(live, _connect(backup), USER, apply=True)
    assert live.execute("SELECT COUNT(*) FROM homework_done WHERE telegram_id = ?", (USER,)).fetchone()[0] == 0
