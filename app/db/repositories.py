"""Репозитории: единственный способ добраться до данных.

Ни бот, ни веб не трогают SQL напрямую — обе стороны ходят через эти классы
и получают доменные объекты, а не сырые словари. Это и есть та развязка,
из-за отсутствия которой в старом коде PWA и бот читали одни и те же данные
двумя несовместимыми способами.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import secrets
from dataclasses import replace
from typing import Any

import aiosqlite

from ..domain.models import (
    ApiMode,
    Credentials,
    Filters,
    LoginType,
    NotificationPrefs,
    QuietHours,
    School,
    Student,
    User,
    clamp_interval,
    normalize,
)
from ..domain.records import MarkRecord, TrackedMark
from .crypto import NO_ENCRYPTION, PasswordCipher
from .engine import Database

logger = logging.getLogger("netschoolbot.db")


def _parse_time(value: str | None) -> dt.time | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%H:%M").time()
    except ValueError:
        logger.warning("Некорректное время в базе: %r", value)
        return None


def _parse_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


class UserRepository:
    def __init__(self, db: Database, cipher: PasswordCipher = NO_ENCRYPTION) -> None:
        self._db = db
        self._cipher = cipher

    async def get(self, telegram_id: int) -> User | None:
        row = await self._db.fetch_one(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        if row is None:
            return None
        return await self._hydrate(row)

    async def get_or_create(self, telegram_id: int, *, display_name: str = "") -> User:
        user = await self.get(telegram_id)
        if user is not None:
            return user
        async with self._db.transaction() as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (telegram_id, display_name) VALUES (?, ?)",
                (telegram_id, display_name),
            )
        created = await self.get(telegram_id)
        assert created is not None
        return created

    async def all_active(self) -> list[User]:
        """Пользователи, для которых имеет смысл запускать проверку."""
        rows = await self._db.fetch_all(
            "SELECT * FROM users WHERE enabled = 1 AND school_url != '' ORDER BY telegram_id"
        )
        return [await self._hydrate(row) for row in rows]

    async def all_with_school(self) -> list[User]:
        """Пользователи со школой, даже если уведомления отключены."""
        rows = await self._db.fetch_all(
            "SELECT * FROM users WHERE school_url != '' ORDER BY telegram_id"
        )
        return [await self._hydrate(row) for row in rows]

    async def all_ids(self) -> list[int]:
        rows = await self._db.fetch_all("SELECT telegram_id FROM users")
        return [int(row["telegram_id"]) for row in rows]

    async def save(self, user: User) -> User:
        """Записать пользователя целиком, включая учеников и фильтры."""
        now = dt.datetime.now().isoformat(timespec="seconds")
        async with self._db.transaction() as db:
            await db.execute(
                """
                INSERT INTO users (
                    telegram_id, enabled, display_name, school_url, school_name,
                    login_type, login, password, student_name, selected_student_id,
                    check_interval, notify_grades, notify_changes, notify_deletes,
                    notify_homework, notify_mail, weekly_summary,
                    quiet_start, quiet_end, api_mode, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    display_name = excluded.display_name,
                    school_url = excluded.school_url,
                    school_name = excluded.school_name,
                    login_type = excluded.login_type,
                    login = excluded.login,
                    password = excluded.password,
                    student_name = excluded.student_name,
                    selected_student_id = excluded.selected_student_id,
                    check_interval = excluded.check_interval,
                    notify_grades = excluded.notify_grades,
                    notify_changes = excluded.notify_changes,
                    notify_deletes = excluded.notify_deletes,
                    notify_homework = excluded.notify_homework,
                    notify_mail = excluded.notify_mail,
                    weekly_summary = excluded.weekly_summary,
                    quiet_start = excluded.quiet_start,
                    quiet_end = excluded.quiet_end,
                    api_mode = excluded.api_mode,
                    updated_at = excluded.updated_at
                """,
                (
                    user.telegram_id,
                    int(user.enabled),
                    user.display_name,
                    user.school.url,
                    user.school.name,
                    user.credentials.login_type.value,
                    user.credentials.login,
                    self._cipher.encrypt(user.credentials.password),
                    user.student_name,
                    user.selected_student_id,
                    clamp_interval(user.check_interval),
                    int(user.notifications.grades),
                    int(user.notifications.changes),
                    int(user.notifications.deletes),
                    int(user.notifications.homework),
                    int(user.notifications.mail),
                    int(user.notifications.weekly_summary),
                    user.quiet_hours.start.strftime("%H:%M") if user.quiet_hours.start else None,
                    user.quiet_hours.end.strftime("%H:%M") if user.quiet_hours.end else None,
                    user.api_mode.value,
                    now,
                ),
            )
            await self._replace_students(db, user)
            await self._replace_filters(db, user)
        return replace(user, updated_at=dt.datetime.fromisoformat(now))

    async def encrypt_stored_passwords(self) -> int:
        """Зашифровать пароли, сохранённые открытым текстом или старым ключом.

        Возвращает число переписанных записей. Без ключа ничего не делает.
        """
        if not self._cipher.enabled:
            return 0
        rows = await self._db.fetch_all(
            "SELECT telegram_id, password FROM users WHERE password != ''"
        )
        updates = [
            (self._cipher.rewrite(row["password"]), row["telegram_id"])
            for row in rows
            if self._cipher.needs_rewrite(row["password"])
        ]
        await self._db.execute_many(
            "UPDATE users SET password = ? WHERE telegram_id = ?", updates
        )
        return len(updates)

    async def delete(self, telegram_id: int) -> None:
        """Полное удаление пользователя. Связанные записи уходят по CASCADE."""
        await self._db.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))

    async def _replace_students(self, db: aiosqlite.Connection, user: User) -> None:
        await db.execute("DELETE FROM students WHERE telegram_id = ?", (user.telegram_id,))
        if user.available_students:
            await db.executemany(
                "INSERT INTO students (telegram_id, student_id, name, position) VALUES (?,?,?,?)",
                [
                    (user.telegram_id, s.id, s.name, index)
                    for index, s in enumerate(user.available_students)
                ],
            )

    async def _replace_filters(self, db: aiosqlite.Connection, user: User) -> None:
        await db.execute("DELETE FROM filters WHERE telegram_id = ?", (user.telegram_id,))
        rows = [
            (user.telegram_id, "exclude_title", value)
            for value in sorted(user.filters.exclude_titles)
        ] + [
            (user.telegram_id, "include_subject", value)
            for value in sorted(user.filters.include_subjects)
        ]
        if rows:
            await db.executemany(
                "INSERT INTO filters (telegram_id, kind, value) VALUES (?,?,?)", rows
            )

    async def _hydrate(self, row: aiosqlite.Row) -> User:
        telegram_id = int(row["telegram_id"])
        student_rows = await self._db.fetch_all(
            "SELECT student_id, name FROM students WHERE telegram_id = ? ORDER BY position",
            (telegram_id,),
        )
        filter_rows = await self._db.fetch_all(
            "SELECT kind, value FROM filters WHERE telegram_id = ?", (telegram_id,)
        )
        return User(
            telegram_id=telegram_id,
            enabled=bool(row["enabled"]),
            display_name=row["display_name"],
            school=School(url=row["school_url"], name=row["school_name"]),
            credentials=Credentials(
                login_type=LoginType.parse(row["login_type"]),
                login=row["login"],
                password=self._cipher.decrypt(row["password"]),
            ),
            student_name=row["student_name"],
            selected_student_id=row["selected_student_id"],
            available_students=tuple(
                Student(id=int(r["student_id"]), name=r["name"]) for r in student_rows
            ),
            check_interval=int(row["check_interval"]),
            notifications=NotificationPrefs(
                grades=bool(row["notify_grades"]),
                changes=bool(row["notify_changes"]),
                deletes=bool(row["notify_deletes"]),
                homework=bool(row["notify_homework"]),
                mail=bool(row["notify_mail"]),
                weekly_summary=bool(row["weekly_summary"]),
            ),
            filters=Filters(
                exclude_titles=frozenset(
                    r["value"] for r in filter_rows if r["kind"] == "exclude_title"
                ),
                include_subjects=frozenset(
                    r["value"] for r in filter_rows if r["kind"] == "include_subject"
                ),
            ),
            quiet_hours=QuietHours(
                start=_parse_time(row["quiet_start"]), end=_parse_time(row["quiet_end"])
            ),
            api_mode=ApiMode.parse(row["api_mode"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )


class MarkStateRepository:
    """Состояние слежения за оценками и домашними заданиями."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def load_marks(self, telegram_id: int) -> dict[str, TrackedMark]:
        rows = await self._db.fetch_all(
            "SELECT identity, payload, missing_streak FROM tracked_marks WHERE telegram_id = ?",
            (telegram_id,),
        )
        tracked: dict[str, TrackedMark] = {}
        for row in rows:
            record = _record_from_json(row["payload"])
            if record is None:
                continue
            tracked[row["identity"]] = TrackedMark(
                record=record, missing_streak=int(row["missing_streak"])
            )
        return tracked

    async def has_history(self, telegram_id: int) -> bool:
        """Есть ли у пользователя история слежения.

        Отличать «первый запуск» от «журнал пуст» обязательно: на первом
        запуске уведомления не отправляются вовсе. Пользователь, помеченный
        как перенесённый из старого проекта, тоже считается без истории:
        его ключи оценок посчитаны по другим правилам и не совпадут.
        """
        row = await self._db.fetch_one(
            """SELECT
                   (SELECT 1 FROM tracked_marks WHERE telegram_id = ? LIMIT 1) AS has_marks,
                   (SELECT baseline_pending FROM users WHERE telegram_id = ?) AS pending""",
            (telegram_id, telegram_id),
        )
        if row is None or row["has_marks"] is None:
            return False
        return not row["pending"]

    async def mark_baseline_pending(self, telegram_id: int, pending: bool = True) -> None:
        """Пометить, что состояние нужно пересобрать молча."""
        await self._db.execute(
            "UPDATE users SET baseline_pending = ? WHERE telegram_id = ?",
            (int(pending), telegram_id),
        )

    async def is_baseline_pending(self, telegram_id: int) -> bool:
        row = await self._db.fetch_one(
            "SELECT baseline_pending FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        return bool(row["baseline_pending"]) if row else False

    async def replace_marks(self, telegram_id: int, tracked: dict[str, TrackedMark]) -> None:
        """Заменить состояние целиком, одной транзакцией."""
        async with self._db.transaction() as db:
            await db.execute("DELETE FROM tracked_marks WHERE telegram_id = ?", (telegram_id,))
            if tracked:
                await db.executemany(
                    """INSERT INTO tracked_marks (telegram_id, identity, payload, missing_streak)
                       VALUES (?,?,?,?)""",
                    [
                        (telegram_id, identity, _record_to_json(item.record), item.missing_streak)
                        for identity, item in tracked.items()
                    ],
                )

    async def load_homework(self, telegram_id: int) -> set[str]:
        rows = await self._db.fetch_all(
            "SELECT identity FROM tracked_homework WHERE telegram_id = ?", (telegram_id,)
        )
        return {row["identity"] for row in rows}

    async def replace_homework(
        self, telegram_id: int, identities: set[str], observed_new: set[str] | None = None
    ) -> None:
        """Обновить слежение, сохранив время обнаружения существующих заданий."""
        observed_new = observed_new or set()
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "SELECT identity FROM tracked_homework WHERE telegram_id = ?", (telegram_id,)
            )
            existing = {row[0] for row in await cursor.fetchall()}
            removed = existing - identities
            added = identities - existing
            if removed:
                await db.executemany(
                    "DELETE FROM tracked_homework WHERE telegram_id = ? AND identity = ?",
                    [(telegram_id, identity) for identity in removed],
                )
            if added:
                await db.executemany(
                    """INSERT INTO tracked_homework (telegram_id, identity, first_seen_at)
                       VALUES (?, ?, CASE WHEN ? THEN datetime('now') END)""",
                    [(telegram_id, identity, int(identity in observed_new)) for identity in added],
                )

    async def observed_homework(self, telegram_id: int) -> dict[str, str]:
        rows = await self._db.fetch_all(
            """SELECT identity, first_seen_at FROM tracked_homework
               WHERE telegram_id = ? AND first_seen_at IS NOT NULL""",
            (telegram_id,),
        )
        return {row["identity"]: row["first_seen_at"].replace(" ", "T") + "Z" for row in rows}

    async def seen_mail_ids(self, telegram_id: int) -> set[int]:
        rows = await self._db.fetch_all(
            "SELECT message_id FROM seen_mail WHERE telegram_id = ?", (telegram_id,)
        )
        return {int(row["message_id"]) for row in rows}

    async def mark_mail_seen(self, telegram_id: int, message_ids: set[int]) -> None:
        if not message_ids:
            return
        await self._db.execute_many(
            "INSERT OR IGNORE INTO seen_mail (telegram_id, message_id) VALUES (?,?)",
            [(telegram_id, mid) for mid in message_ids],
        )

    async def forget_all(self, telegram_id: int) -> None:
        """Сбросить слежение — например при смене школы или ученика."""
        async with self._db.transaction() as db:
            for table in ("tracked_marks", "tracked_homework", "seen_mail"):
                await db.execute(f"DELETE FROM {table} WHERE telegram_id = ?", (telegram_id,))


class SessionRepository:
    """Сохранённые сессии «Сетевого города»."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def load(self, telegram_id: int) -> str | None:
        row = await self._db.fetch_one(
            "SELECT payload FROM netschool_sessions WHERE telegram_id = ?", (telegram_id,)
        )
        return row["payload"] if row else None

    async def save(self, telegram_id: int, payload: str) -> None:
        await self._db.execute(
            """INSERT INTO netschool_sessions (telegram_id, payload, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(telegram_id) DO UPDATE SET
                   payload = excluded.payload, updated_at = excluded.updated_at""",
            (telegram_id, payload),
        )

    async def drop(self, telegram_id: int) -> None:
        await self._db.execute(
            "DELETE FROM netschool_sessions WHERE telegram_id = ?", (telegram_id,)
        )


class MiniappRepository:
    """Постоянные ссылки на PWA, коды подтверждения и push-подписки."""

    def __init__(self, db: Database, *, login_code_ttl: int = 600) -> None:
        self._db = db
        self._login_code_ttl = login_code_ttl

    async def issue_token(self, telegram_id: int, *, revoke_existing: bool = False) -> str:
        """Выдать (или переиспользовать) постоянный токен доступа к PWA."""
        if revoke_existing:
            await self.revoke_tokens(telegram_id)
        else:
            row = await self._db.fetch_one(
                """SELECT token FROM miniapp_tokens
                   WHERE telegram_id = ? AND revoked_at IS NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (telegram_id,),
            )
            if row:
                return row["token"]

        token = secrets.token_urlsafe(32)
        await self._db.execute(
            "INSERT INTO miniapp_tokens (token, telegram_id) VALUES (?,?)",
            (token, telegram_id),
        )
        return token

    async def import_token(self, token: str, telegram_id: int) -> None:
        """Записать готовый токен — для переноса из старого проекта.

        Обычный выпуск токена генерирует новый: если бы перенос делал так же,
        у людей перестали бы открываться уже сохранённые ссылки на PWA.
        """
        await self._db.execute(
            "INSERT OR IGNORE INTO miniapp_tokens (token, telegram_id) VALUES (?,?)",
            (token, telegram_id),
        )

    async def resolve_token(self, token: str) -> int | None:
        if not token:
            return None
        row = await self._db.fetch_one(
            "SELECT telegram_id FROM miniapp_tokens WHERE token = ? AND revoked_at IS NULL",
            (token,),
        )
        return int(row["telegram_id"]) if row else None

    async def revoke_tokens(self, telegram_id: int) -> None:
        await self._db.execute(
            """UPDATE miniapp_tokens SET revoked_at = datetime('now')
               WHERE telegram_id = ? AND revoked_at IS NULL""",
            (telegram_id,),
        )

    async def issue_login_code(self, telegram_id: int) -> str:
        """Шестизначный код для подтверждения входа в PWA через Telegram."""
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=self._login_code_ttl)
        async with self._db.transaction() as db:
            # Один активный код на пользователя: старые сразу гасим.
            await db.execute(
                "DELETE FROM login_codes WHERE telegram_id = ? OR expires_at < ?",
                (telegram_id, dt.datetime.now(dt.timezone.utc).isoformat()),
            )
            await db.execute(
                "INSERT INTO login_codes (code, telegram_id, expires_at) VALUES (?,?,?)",
                (code, telegram_id, expires.isoformat()),
            )
        return code

    async def consume_login_code(self, code: str) -> int | None:
        """Проверить и погасить код. Повторное использование невозможно."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                """SELECT telegram_id, expires_at FROM login_codes
                   WHERE code = ? AND used_at IS NULL""",
                (code.strip(),),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            expires = _parse_dt(row["expires_at"])
            if expires is None or expires < dt.datetime.now(dt.timezone.utc):
                await db.execute("DELETE FROM login_codes WHERE code = ?", (code.strip(),))
                return None
            await db.execute(
                "UPDATE login_codes SET used_at = datetime('now') WHERE code = ?", (code.strip(),)
            )
            return int(row["telegram_id"])

    async def add_push_subscription(
        self, telegram_id: int, endpoint: str, p256dh: str, auth: str
    ) -> None:
        await self._db.execute(
            """INSERT INTO push_subscriptions (endpoint, telegram_id, p256dh, auth)
               VALUES (?,?,?,?)
               ON CONFLICT(endpoint) DO UPDATE SET
                   telegram_id = excluded.telegram_id,
                   p256dh = excluded.p256dh,
                   auth = excluded.auth,
                   failures = 0""",
            (endpoint, telegram_id, p256dh, auth),
        )

    async def push_subscriptions(self, telegram_id: int) -> list[dict[str, str]]:
        rows = await self._db.fetch_all(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE telegram_id = ?",
            (telegram_id,),
        )
        return [
            {"endpoint": r["endpoint"], "p256dh": r["p256dh"], "auth": r["auth"]} for r in rows
        ]

    async def drop_push_subscription(self, endpoint: str) -> None:
        await self._db.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        )

    async def note_push_failure(self, endpoint: str, *, drop_after: int = 5) -> None:
        """Считать неудачи доставки и убирать мёртвые подписки.

        Браузер не сообщает об отзыве подписки — узнать о ней можно только по
        накопившимся ошибкам отправки.
        """
        async with self._db.transaction() as db:
            await db.execute(
                "UPDATE push_subscriptions SET failures = failures + 1 WHERE endpoint = ?",
                (endpoint,),
            )
            await db.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = ? AND failures >= ?",
                (endpoint, drop_after),
            )


class CacheRepository:
    """Кэш ответов для мини-приложения."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, telegram_id: int, section: str) -> tuple[Any, dt.datetime] | None:
        row = await self._db.fetch_one(
            "SELECT payload, updated_at FROM cache_entries WHERE telegram_id = ? AND section = ?",
            (telegram_id, section),
        )
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            logger.warning("Повреждённый кэш %s для %s — игнорируем", section, telegram_id)
            return None
        updated = _parse_dt(row["updated_at"]) or dt.datetime.min
        return payload, updated

    async def put(self, telegram_id: int, section: str, payload: Any) -> None:
        await self._db.execute(
            """INSERT INTO cache_entries (telegram_id, section, payload, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(telegram_id, section) DO UPDATE SET
                   payload = excluded.payload, updated_at = excluded.updated_at""",
            (telegram_id, section, json.dumps(payload, ensure_ascii=False)),
        )

    async def clear(self, telegram_id: int) -> None:
        await self._db.execute("DELETE FROM cache_entries WHERE telegram_id = ?", (telegram_id,))


def _record_to_json(record: MarkRecord) -> str:
    return json.dumps(
        {
            "subject": record.subject,
            "date": record.date.isoformat(),
            "assignment_type": record.assignment_type,
            "content": record.content,
            "mark": record.mark,
            "weight": record.weight,
            "comment": record.comment,
            "lesson_number": record.lesson_number,
            "lesson_start": record.lesson_start,
            "lesson_end": record.lesson_end,
            "assignment_index": record.assignment_index,
        },
        ensure_ascii=False,
    )


def _record_from_json(payload: str) -> MarkRecord | None:
    try:
        data = json.loads(payload)
        return MarkRecord(
            subject=data["subject"],
            date=dt.date.fromisoformat(data["date"]),
            assignment_type=data["assignment_type"],
            content=data.get("content", ""),
            mark=data.get("mark", ""),
            weight=int(data.get("weight") or 1),
            comment=data.get("comment", ""),
            lesson_number=data.get("lesson_number"),
            lesson_start=data.get("lesson_start", ""),
            lesson_end=data.get("lesson_end", ""),
            assignment_index=int(data.get("assignment_index") or 0),
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        # Повреждённая запись не должна ронять весь цикл проверки.
        logger.warning("Не удалось прочитать сохранённую оценку: %s", exc)
        return None
