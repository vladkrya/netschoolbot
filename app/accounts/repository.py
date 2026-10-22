"""Аккаунты с почтой и паролем, коды и перенос данных на настоящий Telegram."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from ..db.engine import Database
from . import WEB_ID_BASE

CODE_TTL_S = 15 * 60
CODE_ATTEMPTS = 5
LINK_CODE_TTL_S = 15 * 60
RESET_LINK_TTL_S = 30 * 60
# Колонки с id пользователя, кроме telegram_id: перенос при привязке Telegram.
# owner_id, follower_id, viewer_id — родные (followers, follow_invites, follow_views).
USER_COLUMNS = ("telegram_id", "author_id", "reporter_id", "actor_id", "deleted_by",
                "owner_id", "follower_id", "viewer_id")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class AccountRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def by_email(self, email: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM accounts WHERE email = ?", (normalize_email(email),))
        return dict(row) if row else None

    async def by_user(self, telegram_id: int) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM accounts WHERE telegram_id = ?", (telegram_id,))
        return dict(row) if row else None

    async def create_web_user(self, email: str, password_hash: str) -> int | None:
        """Новый пользователь без Telegram и его аккаунт. None — почта занята."""
        email = normalize_email(email)
        now = time.time()
        async with self._db.transaction() as db:
            cursor = await db.execute("SELECT 1 FROM accounts WHERE email = ?", (email,))
            if await cursor.fetchone():
                return None
            cursor = await db.execute("SELECT MAX(telegram_id) FROM users WHERE telegram_id >= ?", (WEB_ID_BASE,))
            row = await cursor.fetchone()
            telegram_id = max(int(row[0] or 0) + 1, WEB_ID_BASE + 1)
            await db.execute("INSERT INTO users (telegram_id, api_mode) VALUES (?, 'mobile')", (telegram_id,))
            await db.execute(
                """INSERT INTO accounts (telegram_id, email, password_hash, created_at, password_changed_at)
                   VALUES (?, ?, ?, ?, ?)""", (telegram_id, email, password_hash, now, now))
        return telegram_id

    async def attach(self, telegram_id: int, email: str, password_hash: str) -> bool:
        """Почта и пароль для уже существующего пользователя (из Telegram).
        False — почта занята другим."""
        email = normalize_email(email)
        now = time.time()
        async with self._db.transaction() as db:
            cursor = await db.execute("SELECT telegram_id FROM accounts WHERE email = ?", (email,))
            owner = await cursor.fetchone()
            if owner and owner[0] != telegram_id:
                return False
            await db.execute(
                """INSERT INTO accounts (telegram_id, email, password_hash, created_at, password_changed_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(telegram_id) DO UPDATE SET email = excluded.email,
                       password_hash = excluded.password_hash, password_changed_at = excluded.password_changed_at,
                       email_verified_at = CASE WHEN accounts.email = excluded.email
                                                THEN accounts.email_verified_at END""",
                (telegram_id, email, password_hash, now, now))
        return True

    async def set_password(self, telegram_id: int, password_hash: str) -> None:
        await self._db.execute(
            "UPDATE accounts SET password_hash = ?, password_changed_at = ? WHERE telegram_id = ?",
            (password_hash, time.time(), telegram_id))

    async def mark_verified(self, telegram_id: int) -> None:
        await self._db.execute("UPDATE accounts SET email_verified_at = ? WHERE telegram_id = ?",
                               (time.time(), telegram_id))

    async def remove(self, telegram_id: int) -> None:
        await self._db.execute("DELETE FROM accounts WHERE telegram_id = ?", (telegram_id,))

    # ─────────────────────────── Коды из письма ───────────────────────────

    async def issue_code(self, email: str, purpose: str) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        await self._db.execute(
            """INSERT INTO account_codes (email, purpose, code_hash, expires_at, attempts) VALUES (?, ?, ?, ?, 0)
               ON CONFLICT(email, purpose) DO UPDATE SET code_hash = excluded.code_hash,
                   expires_at = excluded.expires_at, attempts = 0""",
            (normalize_email(email), purpose, _digest(code), time.time() + CODE_TTL_S))
        return code

    async def check_code(self, email: str, purpose: str, code: str) -> bool:
        """Верный и свежий код гасится; после CODE_ATTEMPTS ошибок — тоже."""
        email = normalize_email(email)
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "SELECT code_hash, expires_at, attempts FROM account_codes WHERE email = ? AND purpose = ?",
                (email, purpose))
            row = await cursor.fetchone()
            if not row or row[1] < time.time() or row[2] >= CODE_ATTEMPTS:
                return False
            if secrets.compare_digest(row[0], _digest(code.strip())):
                await db.execute("DELETE FROM account_codes WHERE email = ? AND purpose = ?", (email, purpose))
                return True
            await db.execute("UPDATE account_codes SET attempts = attempts + 1 WHERE email = ? AND purpose = ?",
                             (email, purpose))
            return False

    # ─────────────────────────── Привязка Telegram ───────────────────────────

    async def number(self, telegram_id: int) -> int | None:
        """Номер аккаунта в приложении (виден в настройках, логин CalDAV)."""
        row = await self._db.fetch_one("SELECT account_no FROM users WHERE telegram_id = ?", (telegram_id,))
        return row["account_no"] if row else None

    async def issue_reset_link(self, email: str) -> str:
        """Секрет для ссылки сброса пароля. Хранится только хэш, новая ссылка
        отменяет прежнюю; живёт RESET_LINK_TTL_S и гасится при использовании."""
        token = secrets.token_urlsafe(32)
        await self._db.execute(
            """INSERT INTO account_codes (email, purpose, code_hash, expires_at, attempts) VALUES (?, 'reset', ?, ?, 0)
               ON CONFLICT(email, purpose) DO UPDATE SET code_hash = excluded.code_hash,
                   expires_at = excluded.expires_at, attempts = 0""",
            (normalize_email(email), _digest(token), time.time() + RESET_LINK_TTL_S))
        return token

    async def reset_link_email(self, token: str, *, take: bool = False) -> str | None:
        """Почта, к которой ведёт живая ссылка сброса; take — сразу погасить."""
        if not token or len(token) > 128:
            return None
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "SELECT email FROM account_codes WHERE purpose = 'reset' AND code_hash = ? AND expires_at > ?",
                (_digest(token), time.time()))
            row = await cursor.fetchone()
            if row and take:
                await db.execute("DELETE FROM account_codes WHERE email = ? AND purpose = 'reset'", (row[0],))
            return row[0] if row else None

    async def issue_link_code(self, telegram_id: int) -> str:
        code = secrets.token_hex(4).upper()
        async with self._db.transaction() as db:
            await db.execute("DELETE FROM telegram_link_codes WHERE telegram_id = ? OR expires_at < ?",
                             (telegram_id, time.time()))
            await db.execute("INSERT INTO telegram_link_codes (code, telegram_id, expires_at) VALUES (?, ?, ?)",
                             (code, telegram_id, time.time() + LINK_CODE_TTL_S))
        return code

    async def take_link_code(self, code: str) -> int | None:
        """id пользователя без Telegram по коду; код одноразовый."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "SELECT telegram_id, expires_at FROM telegram_link_codes WHERE code = ?", (code.strip().upper(),))
            row = await cursor.fetchone()
            await db.execute("DELETE FROM telegram_link_codes WHERE code = ?", (code.strip().upper(),))
        if not row or row[1] < time.time():
            return None
        return int(row[0])

    async def has_own_data(self, telegram_id: int) -> bool:
        """Есть ли у пользователя то, что он сам завёл: заметки, настройки
        (в них и свои уроки), отметки о заданиях, история оценок или дневник
        до /logout. move_user стёр бы это вместе с его записью."""
        row = await self._db.fetch_one(
            """SELECT COALESCE(
                   (SELECT 1 FROM user_notes WHERE telegram_id = ? LIMIT 1),
                   (SELECT 1 FROM web_prefs WHERE telegram_id = ? AND payload NOT IN ('', '{}') LIMIT 1),
                   (SELECT 1 FROM homework_done WHERE telegram_id = ? LIMIT 1),
                   (SELECT 1 FROM mark_history WHERE telegram_id = ? LIMIT 1),
                   (SELECT 1 FROM users WHERE telegram_id = ? AND last_school_url != '' LIMIT 1)
               ) AS found""",
            (telegram_id,) * 5,
        )
        return bool(row and row["found"])

    async def move_user(self, old_id: int, new_id: int) -> None:
        """Перенести все данные пользователя на другой id (привязка Telegram).

        Пустая запись нового id (бот заводит её при первом сообщении)
        удаляется. Внешние ключи проверяются в конце транзакции: сначала
        меняется сам пользователь, потом всё, что на него ссылается.
        """
        # Запись Telegram могла появиться раньше аккаунта с почтой: дата
        # первого прихода и меньший номер аккаунта остаются за человеком.
        earlier = await self._db.fetch_one("SELECT created_at, account_no FROM users WHERE telegram_id = ?", (new_id,))
        async with self._db.transaction() as db:
            await db.execute("PRAGMA defer_foreign_keys = ON")
            await db.execute("DELETE FROM users WHERE telegram_id = ?", (new_id,))
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
            tables = [row[0] for row in await cursor.fetchall()]
            for table in tables:
                cursor = await db.execute(f'PRAGMA table_info("{table}")')
                columns = {row[1] for row in await cursor.fetchall()}
                for column in USER_COLUMNS:
                    if column not in columns:
                        continue
                    if column == "telegram_id" and table != "users":
                        # Остатки нового id в таблицах без внешнего ключа.
                        await db.execute(f'DELETE FROM "{table}" WHERE telegram_id = ?', (new_id,))
                    await db.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?', (new_id, old_id))
            for table in ("chat_messages", "chat_reads"):
                await db.execute(f"UPDATE {table} SET room = ? WHERE room = ?", (f"support:{new_id}", f"support:{old_id}"))
        if earlier:
            await self._db.execute(
                "UPDATE users SET created_at = MIN(created_at, ?), "
                "account_no = MIN(COALESCE(account_no, ?), COALESCE(?, account_no)) WHERE telegram_id = ?",
                (earlier[0], earlier[1], earlier[1], new_id))

    async def merge_into(self, old_id: int, new_id: int) -> bool:
        """Join a verified email account to Telegram, retaining Telegram's diary.

        Independent rows (notes, tokens, history, messages) move with their ids.
        For duplicate per-user keys, the existing Telegram row wins. Email is
        moved only if Telegram does not already have an email account.
        """
        async with self._db.transaction() as db:
            await db.execute("PRAGMA defer_foreign_keys = ON")
            cursor = await db.execute("SELECT 1 FROM accounts WHERE telegram_id = ?", (new_id,))
            if await cursor.fetchone():
                return False
            cursor = await db.execute("SELECT 1 FROM accounts WHERE telegram_id = ?", (old_id,))
            if not await cursor.fetchone():
                return False
            # Web preferences may contain custom lessons; retain both sets of
            # keys, with the current Telegram settings taking precedence.
            cursor = await db.execute("SELECT telegram_id, payload FROM web_prefs WHERE telegram_id IN (?, ?)",
                                      (old_id, new_id))
            prefs = {row[0]: row[1] for row in await cursor.fetchall()}
            if old_id in prefs and new_id in prefs:
                old_prefs, new_prefs = json.loads(prefs[old_id]), json.loads(prefs[new_id])
                merged = {**old_prefs, **new_prefs}
                # Custom lessons have independent ids. Keep both collections;
                # only edits with the same id use Telegram's current value.
                edits = {edit["id"]: edit for prefs in (old_prefs, new_prefs)
                         for edit in prefs.get("lessonEdits", [])}
                if edits:
                    merged["lessonEdits"] = list(edits.values())
                await db.execute("UPDATE web_prefs SET payload = ? WHERE telegram_id = ?",
                                 (json.dumps(merged, ensure_ascii=False), new_id))
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
            for (table,) in await cursor.fetchall():
                if table == "users":
                    continue
                cursor = await db.execute(f'PRAGMA table_info("{table}")')
                columns = {row[1] for row in await cursor.fetchall()}
                for column in USER_COLUMNS:
                    if column in columns:
                        await db.execute(f'UPDATE OR IGNORE "{table}" SET "{column}" = ? WHERE "{column}" = ?',
                                         (new_id, old_id))
                        # Conflicting composite keys are duplicates of the
                        # destination record; remove only those left behind.
                        await db.execute(f'DELETE FROM "{table}" WHERE "{column}" = ?', (old_id,))
            for table in ("chat_messages", "chat_reads"):
                await db.execute(f"UPDATE OR IGNORE {table} SET room = ? WHERE room = ?",
                                 (f"support:{new_id}", f"support:{old_id}"))
            await db.execute("DELETE FROM users WHERE telegram_id = ?", (old_id,))
        return True
