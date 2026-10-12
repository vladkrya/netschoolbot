"""Хранение чатов в SQLite: профили, сообщения, прочитанное."""

from __future__ import annotations

import time
from typing import Any

from ..db.engine import Database

PAGE = 50


class ChatRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ─────────────────────────────── Профили ───────────────────────────────

    async def profile(self, telegram_id: int) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM chat_profiles WHERE telegram_id = ?", (telegram_id,))
        return dict(row) if row else None

    async def save_profile(
        self, telegram_id: int, *, netschool_user_id: int | None, school_key: str, school_name: str,
        class_name: str, grade: int, first_name: str, consent: bool = False,
    ) -> dict[str, Any]:
        """Создать профиль (нужно согласие) или обновить проверенные данные."""
        now = time.time()
        if consent:
            await self._db.execute(
                """INSERT INTO chat_profiles (telegram_id, netschool_user_id, school_key, school_name,
                       class_name, grade, first_name, consented_at, verified_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(telegram_id) DO UPDATE SET
                       netschool_user_id = excluded.netschool_user_id, school_key = excluded.school_key,
                       school_name = excluded.school_name, class_name = excluded.class_name,
                       grade = excluded.grade, first_name = excluded.first_name,
                       verified_at = excluded.verified_at""",
                (telegram_id, netschool_user_id, school_key, school_name, class_name, grade, first_name, now, now),
            )
        else:
            await self._db.execute(
                """UPDATE chat_profiles SET netschool_user_id = ?, school_key = ?, school_name = ?,
                       class_name = ?, grade = ?, first_name = ?, verified_at = ?
                   WHERE telegram_id = ?""",
                (netschool_user_id, school_key, school_name, class_name, grade, first_name, now, telegram_id),
            )
        profile = await self.profile(telegram_id)
        assert profile is not None
        return profile

    async def touch_verified(self, telegram_id: int) -> None:
        await self._db.execute("UPDATE chat_profiles SET verified_at = ? WHERE telegram_id = ?",
                               (time.time(), telegram_id))

    async def delete_profile(self, telegram_id: int) -> None:
        await self._db.execute("DELETE FROM chat_profiles WHERE telegram_id = ?", (telegram_id,))

    async def set_profile_field(self, telegram_id: int, field: str, value: Any) -> None:
        if field not in {"as_admin", "muted_until", "banned", "last_sent_at"}:
            raise ValueError(field)
        await self._db.execute(f"UPDATE chat_profiles SET {field} = ? WHERE telegram_id = ?", (value, telegram_id))

    # ─────────────────────────────── Сообщения ──────────────────────────────

    async def add_message(
        self, room: str, *, author_id: int, author_name: str, author_grade: int | None, is_admin: bool,
        text: str, reply_to: int | None = None, kind: str = "text", payload: str | None = None,
    ) -> int:
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO chat_messages (room, author_id, author_name, author_grade, is_admin,
                       text, reply_to, kind, payload, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (room, author_id, author_name, author_grade, int(is_admin), text, reply_to, kind, payload,
                 time.time(), time.time()),
            )
            message_id = cursor.lastrowid
            await conn.execute("UPDATE chat_profiles SET last_sent_at = ? WHERE telegram_id = ?",
                               (time.time(), author_id))
        assert message_id is not None
        return int(message_id)

    async def message(self, message_id: int) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
        return dict(row) if row else None

    async def messages(
        self, room: str, *, after: int | None = None, before: int | None = None, limit: int = PAGE,
    ) -> list[dict[str, Any]]:
        """Страница сообщений по возрастанию id.

        after — новые после известного (опрос открытого чата), before — более
        старые при прокрутке вверх, без них — последние `limit`.
        """
        if after is not None:
            rows = await self._db.fetch_all(
                "SELECT * FROM chat_messages WHERE room = ? AND id > ? ORDER BY id LIMIT ?", (room, after, limit))
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM chat_messages WHERE room = ? AND id < ? ORDER BY id DESC LIMIT ?",
                (room, before if before is not None else 1 << 62, limit))
            rows = list(reversed(rows))
        return [dict(row) for row in rows]

    async def changed_since(self, room: str, since: float, up_to: int) -> list[dict[str, Any]]:
        """Уже показанные сообщения, которые поменялись (скрыли, удалили,
        закрепили, отреагировали), — чтобы это дошло до открытых чатов."""
        rows = await self._db.fetch_all(
            "SELECT * FROM chat_messages WHERE room = ? AND updated_at > ? AND id <= ? ORDER BY id",
            (room, since, up_to))
        return [dict(row) for row in rows]

    async def touch(self, message_id: int) -> None:
        await self._db.execute("UPDATE chat_messages SET updated_at = ? WHERE id = ?", (time.time(), message_id))

    async def reactions(self, ids: list[int]) -> dict[int, list[tuple[int, str]]]:
        """{сообщение: [(кто, эмодзи)]}."""
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = await self._db.fetch_all(
            f"SELECT message_id, telegram_id, emoji FROM chat_reactions WHERE message_id IN ({marks})", tuple(ids))
        result: dict[int, list[tuple[int, str]]] = {}
        for row in rows:
            result.setdefault(row["message_id"], []).append((row["telegram_id"], row["emoji"]))
        return result

    async def last_messages(self, rooms: list[str]) -> dict[str, dict[str, Any]]:
        if not rooms:
            return {}
        marks = ",".join("?" * len(rooms))
        rows = await self._db.fetch_all(
            f"""SELECT * FROM chat_messages WHERE id IN (
                    SELECT MAX(id) FROM chat_messages
                    WHERE room IN ({marks}) AND deleted_at IS NULL AND hidden = 0 GROUP BY room)""",
            tuple(rooms))
        return {row["room"]: dict(row) for row in rows}

    async def unread(self, telegram_id: int, rooms: list[str]) -> dict[str, int]:
        if not rooms:
            return {}
        marks = ",".join("?" * len(rooms))
        rows = await self._db.fetch_all(
            f"""SELECT m.room, COUNT(*) AS n FROM chat_messages m
                LEFT JOIN chat_reads r ON r.telegram_id = ? AND r.room = m.room
                WHERE m.room IN ({marks}) AND m.id > COALESCE(r.last_read, 0)
                  AND m.author_id != ? AND m.deleted_at IS NULL AND m.hidden = 0
                GROUP BY m.room""",
            (telegram_id, *rooms, telegram_id))
        return {row["room"]: int(row["n"]) for row in rows}

    async def mark_read(self, telegram_id: int, room: str, last_id: int) -> None:
        await self._db.execute(
            """INSERT INTO chat_reads (telegram_id, room, last_read) VALUES (?, ?, ?)
               ON CONFLICT(telegram_id, room) DO UPDATE SET last_read = MAX(last_read, excluded.last_read)""",
            (telegram_id, room, last_id))

    async def reads(self, telegram_id: int) -> dict[str, dict[str, int]]:
        rows = await self._db.fetch_all("SELECT * FROM chat_reads WHERE telegram_id = ?", (telegram_id,))
        return {row["room"]: {"last_read": row["last_read"], "muted": row["muted"]} for row in rows}

    async def active_rooms(self) -> list[str]:
        """Все комнаты, где кто-то писал, — список для администратора."""
        rows = await self._db.fetch_all(
            "SELECT room, MAX(id) AS last FROM chat_messages GROUP BY room ORDER BY last DESC")
        return [row["room"] for row in rows]

    async def school_name(self, school: str) -> str:
        row = await self._db.fetch_one(
            "SELECT school_name FROM chat_profiles WHERE school_key = ? AND school_name != '' LIMIT 1", (school,))
        return row["school_name"] if row else ""

    async def is_moderator(self, telegram_id: int, school: str) -> bool:
        return bool(school) and bool(await self._db.fetch_one(
            "SELECT 1 FROM chat_moderators WHERE telegram_id = ? AND school_key = ?", (telegram_id, school)))

    # ─────────────────────── Реакции, жалобы, модерация ───────────────────────

    async def toggle_reaction(self, message_id: int, telegram_id: int, emoji: str) -> bool:
        """Поставить или снять реакцию. True — теперь стоит."""
        exists = await self._db.fetch_one(
            "SELECT 1 FROM chat_reactions WHERE message_id = ? AND telegram_id = ? AND emoji = ?",
            (message_id, telegram_id, emoji))
        if exists:
            await self._db.execute(
                "DELETE FROM chat_reactions WHERE message_id = ? AND telegram_id = ? AND emoji = ?",
                (message_id, telegram_id, emoji))
        else:
            await self._db.execute(
                "INSERT INTO chat_reactions (message_id, telegram_id, emoji) VALUES (?, ?, ?)",
                (message_id, telegram_id, emoji))
        await self.touch(message_id)
        return not exists

    async def report(self, message_id: int, reporter_id: int) -> int:
        """Жалоба (повторная от того же человека не считается). Возвращает число
        нерассмотренных жалоб на сообщение."""
        await self._db.execute(
            "INSERT OR IGNORE INTO chat_reports (message_id, reporter_id, created_at) VALUES (?, ?, ?)",
            (message_id, reporter_id, time.time()))
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM chat_reports WHERE message_id = ? AND resolved = 0", (message_id,))
        return int(row["n"]) if row else 0

    async def resolve_reports(self, message_id: int) -> None:
        await self._db.execute("UPDATE chat_reports SET resolved = 1 WHERE message_id = ?", (message_id,))

    async def set_flag(self, message_id: int, field: str, value: Any, *, actor: int | None = None) -> None:
        if field not in {"hidden", "pinned", "deleted_at"}:
            raise ValueError(field)
        if field == "deleted_at":
            await self._db.execute(
                "UPDATE chat_messages SET deleted_at = ?, deleted_by = ?, updated_at = ? WHERE id = ?",
                (value, actor, time.time(), message_id))
        else:
            await self._db.execute(f"UPDATE chat_messages SET {field} = ?, updated_at = ? WHERE id = ?",
                                   (value, time.time(), message_id))

    async def pinned(self, room: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """SELECT * FROM chat_messages WHERE room = ? AND pinned = 1 AND deleted_at IS NULL AND hidden = 0
               ORDER BY id DESC LIMIT 1""", (room,))
        return dict(row) if row else None

    async def reported(self, schools: list[str] | None) -> list[dict[str, Any]]:
        """Сообщения с нерассмотренными жалобами. schools=None — все (администратор)."""
        rows = await self._db.fetch_all(
            """SELECT m.*, COUNT(r.reporter_id) AS reports FROM chat_messages m
               JOIN chat_reports r ON r.message_id = m.id AND r.resolved = 0
               WHERE m.deleted_at IS NULL
               GROUP BY m.id ORDER BY MAX(r.created_at) DESC LIMIT 100""")
        result = [dict(row) for row in rows]
        if schools is None:
            return result
        from .rules import room_school
        return [row for row in result if room_school(row["room"]) in schools]

    async def moderated_schools(self, telegram_id: int) -> list[str]:
        rows = await self._db.fetch_all("SELECT school_key FROM chat_moderators WHERE telegram_id = ?", (telegram_id,))
        return [row["school_key"] for row in rows]

    async def set_moderator(self, telegram_id: int, school: str, on: bool) -> None:
        if on:
            await self._db.execute(
                "INSERT OR IGNORE INTO chat_moderators (telegram_id, school_key) VALUES (?, ?)", (telegram_id, school))
        else:
            await self._db.execute(
                "DELETE FROM chat_moderators WHERE telegram_id = ? AND school_key = ?", (telegram_id, school))

    async def log(self, actor: int, action: str, target: str, details: str = "") -> None:
        await self._db.execute(
            "INSERT INTO chat_mod_log (actor_id, action, target, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (actor, action, target, details, time.time()))

    # ─────────────────────── Опросы, участники, заглушение ───────────────────────

    async def vote(self, message_id: int, telegram_id: int, option: int | None) -> None:
        if option is None:
            await self._db.execute("DELETE FROM chat_poll_votes WHERE message_id = ? AND telegram_id = ?",
                                   (message_id, telegram_id))
        else:
            await self._db.execute(
                """INSERT INTO chat_poll_votes (message_id, telegram_id, option) VALUES (?, ?, ?)
                   ON CONFLICT(message_id, telegram_id) DO UPDATE SET option = excluded.option""",
                (message_id, telegram_id, option))
        await self.touch(message_id)

    async def votes(self, ids: list[int]) -> dict[int, list[tuple[int, int]]]:
        """{опрос: [(кто, вариант)]}."""
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = await self._db.fetch_all(
            f"SELECT message_id, telegram_id, option FROM chat_poll_votes WHERE message_id IN ({marks})", tuple(ids))
        result: dict[int, list[tuple[int, int]]] = {}
        for row in rows:
            result.setdefault(row["message_id"], []).append((row["telegram_id"], row["option"]))
        return result

    async def members(self, room: str) -> list[dict[str, Any]]:
        """Участники комнаты (по профилям), кроме заблокированных."""
        kind, _, rest = room.partition(":")
        if kind == "global":
            first, last = (int(x) for x in rest.split("-"))
            rows = await self._db.fetch_all(
                "SELECT * FROM chat_profiles WHERE banned = 0 AND grade BETWEEN ? AND ?", (first, last))
        elif kind == "school":
            rows = await self._db.fetch_all(
                "SELECT * FROM chat_profiles WHERE banned = 0 AND school_key = ?", (rest,))
        elif kind == "grade":
            school, grade = rest.rsplit(":", 1)
            rows = await self._db.fetch_all(
                "SELECT * FROM chat_profiles WHERE banned = 0 AND school_key = ? AND grade = ?", (school, int(grade)))
        else:
            rows = []
        return [dict(row) for row in rows]

    async def set_muted(self, telegram_id: int, room: str, muted: bool) -> None:
        await self._db.execute(
            """INSERT INTO chat_reads (telegram_id, room, muted) VALUES (?, ?, ?)
               ON CONFLICT(telegram_id, room) DO UPDATE SET muted = excluded.muted""",
            (telegram_id, room, int(muted)))

    async def is_muted(self, telegram_id: int, room: str) -> bool:
        row = await self._db.fetch_one(
            "SELECT muted FROM chat_reads WHERE telegram_id = ? AND room = ?", (telegram_id, room))
        return bool(row and row["muted"])
