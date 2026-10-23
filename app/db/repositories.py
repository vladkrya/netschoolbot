"""Репозитории: единственный способ добраться до данных.

Ни бот, ни веб не трогают SQL напрямую — обе стороны ходят через эти классы
и получают доменные объекты, а не сырые словари. Это и есть та развязка,
из-за отсутствия которой в старом коде PWA и бот читали одни и те же данные
двумя несовместимыми способами.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import logging
import secrets
import hashlib
import hmac
from dataclasses import dataclass, replace
from typing import Any

import aiosqlite

from ..domain.models import (
    DEFAULT_TIMEZONE,
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
    clamp_reminder_hour,
    normalize,
    parse_marks_filter,
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
                "INSERT OR IGNORE INTO users (telegram_id, display_name, api_mode) VALUES (?, ?, ?)",
                (telegram_id, display_name, ApiMode.MOBILE.value),
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
                    notify_homework, notify_mail, weekly_summary, notify_absences,
                    quiet_start, quiet_end, api_mode, timezone, evening_summary, notify_schedule,
                    updated_at, remember_credentials, notify_telegram,
                    homework_reminder, reminder_hour, test_reminder, grade_risk,
                    monthly_summary, all_children, marks_filter, weekly_review, vacation_quiet
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    display_name = excluded.display_name,
                    school_url = excluded.school_url,
                    school_name = excluded.school_name,
                    login_type = excluded.login_type,
                    login = excluded.login,
                    password = excluded.password,
                    remember_credentials = excluded.remember_credentials,
                    student_name = excluded.student_name,
                    selected_student_id = excluded.selected_student_id,
                    check_interval = excluded.check_interval,
                    notify_grades = excluded.notify_grades,
                    notify_changes = excluded.notify_changes,
                    notify_deletes = excluded.notify_deletes,
                    notify_homework = excluded.notify_homework,
                    notify_mail = excluded.notify_mail,
                    weekly_summary = excluded.weekly_summary,
                    notify_absences = excluded.notify_absences,
                    quiet_start = excluded.quiet_start,
                    quiet_end = excluded.quiet_end,
                    api_mode = excluded.api_mode,
                    timezone = excluded.timezone,
                    evening_summary = excluded.evening_summary,
                    notify_schedule = excluded.notify_schedule,
                    notify_telegram = excluded.notify_telegram,
                    homework_reminder = excluded.homework_reminder,
                    reminder_hour = excluded.reminder_hour,
                    test_reminder = excluded.test_reminder,
                    grade_risk = excluded.grade_risk,
                    monthly_summary = excluded.monthly_summary,
                    all_children = excluded.all_children,
                    marks_filter = excluded.marks_filter,
                    weekly_review = excluded.weekly_review,
                    vacation_quiet = excluded.vacation_quiet,
                    updated_at = excluded.updated_at
                """,
                (
                    user.telegram_id,
                    int(user.enabled),
                    user.display_name,
                    user.school.url,
                    user.school.name,
                    user.credentials.login_type.value,
                    user.credentials.login if user.credentials.remember else "",
                    self._cipher.encrypt(user.credentials.password) if user.credentials.remember else "",
                    user.student_name,
                    user.selected_student_id,
                    clamp_interval(user.check_interval),
                    int(user.notifications.grades),
                    int(user.notifications.changes),
                    int(user.notifications.deletes),
                    int(user.notifications.homework),
                    int(user.notifications.mail),
                    int(user.notifications.weekly_summary),
                    int(user.notifications.absences),
                    user.quiet_hours.start.strftime("%H:%M") if user.quiet_hours.start else None,
                    user.quiet_hours.end.strftime("%H:%M") if user.quiet_hours.end else None,
                    user.api_mode.value,
                    user.timezone,
                    int(user.notifications.evening_summary),
                    int(user.notifications.schedule),
                    now,
                    int(user.credentials.remember),
                    int(user.notifications.telegram),
                    int(user.notifications.homework_reminder),
                    clamp_reminder_hour(user.notifications.reminder_hour),
                    int(user.notifications.test_reminder),
                    int(user.notifications.grade_risk),
                    int(user.notifications.monthly_summary),
                    int(user.notifications.all_children),
                    ",".join(str(v) for v in sorted(set(user.notifications.marks_filter))),
                    int(user.notifications.weekly_review),
                    int(user.notifications.vacation_quiet),
                ),
            )
            await self._replace_students(db, user)
            await self._replace_filters(db, user)
        credentials = user.credentials
        if not credentials.remember:
            credentials = replace(credentials, login="", password="")
        return replace(user, credentials=credentials, updated_at=dt.datetime.fromisoformat(now))

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
        async with self._db.transaction() as db:
            await db.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('legacy_import_completed', '1')")
            await db.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))

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
                remember=bool(row["remember_credentials"]),
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
                evening_summary=bool(row["evening_summary"]),
                schedule=bool(row["notify_schedule"]),
                absences=bool(row["notify_absences"]),
                telegram=bool(row["notify_telegram"]),
                homework_reminder=bool(row["homework_reminder"]),
                reminder_hour=clamp_reminder_hour(row["reminder_hour"]),
                test_reminder=bool(row["test_reminder"]),
                grade_risk=bool(row["grade_risk"]),
                monthly_summary=bool(row["monthly_summary"]),
                all_children=bool(row["all_children"]),
                marks_filter=parse_marks_filter(row["marks_filter"]),
                weekly_review=bool(row["weekly_review"]),
                vacation_quiet=bool(row["vacation_quiet"]),
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
            timezone=row["timezone"] or DEFAULT_TIMEZONE,
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )


HISTORY_LIMIT = 500


@dataclass(frozen=True, slots=True)
class WatchStatus:
    """Исход фоновых проверок; время — в UTC."""

    last_ok_at: dt.datetime | None = None
    last_error_at: dt.datetime | None = None
    last_error: str = ""
    next_check_at: dt.datetime | None = None
    queued: int = 0


def _utc_now_text() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


async def _write_marks(
    db: aiosqlite.Connection, telegram_id: int, tracked: dict[str, TrackedMark]
) -> None:
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


async def _write_homework(
    db: aiosqlite.Connection, telegram_id: int, identities: set[str], observed_new: set[str]
) -> None:
    cursor = await db.execute(
        "SELECT identity FROM tracked_homework WHERE telegram_id = ?", (telegram_id,)
    )
    existing = {row[0] for row in await cursor.fetchall()}
    added = identities - existing
    if added:
        await db.executemany(
            """INSERT INTO tracked_homework (telegram_id, identity, first_seen_at)
               VALUES (?, ?, CASE WHEN ? THEN datetime('now') END)""",
            [(telegram_id, identity, int(identity in observed_new)) for identity in added],
        )


async def _write_absences(db: aiosqlite.Connection, telegram_id: int, identities: set[str]) -> None:
    cursor = await db.execute(
        "SELECT identity FROM tracked_absences WHERE telegram_id = ?", (telegram_id,)
    )
    existing = {row[0] for row in await cursor.fetchall()}
    added = identities - existing
    if added:
        await db.executemany(
            "INSERT INTO tracked_absences (telegram_id, identity) VALUES (?, ?)",
            [(telegram_id, identity) for identity in added],
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
        # Не только оценки: в начале года их нет ни одной, и без этого каждая
        # проверка считалась бы первой — новые задания молча проглатывались бы.
        row = await self._db.fetch_one(
            """SELECT
                   COALESCE(
                       (SELECT 1 FROM tracked_marks WHERE telegram_id = ? LIMIT 1),
                       (SELECT 1 FROM tracked_homework WHERE telegram_id = ? LIMIT 1),
                       (SELECT 1 FROM tracked_schedule WHERE telegram_id = ? LIMIT 1)
                   ) AS has_state,
                   (SELECT baseline_pending FROM users WHERE telegram_id = ?) AS pending""",
            (telegram_id, telegram_id, telegram_id, telegram_id),
        )
        if row is None or row["has_state"] is None:
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
            await _write_marks(db, telegram_id, tracked)

    async def load_homework(self, telegram_id: int) -> set[str]:
        rows = await self._db.fetch_all(
            "SELECT identity FROM tracked_homework WHERE telegram_id = ?", (telegram_id,)
        )
        return {row["identity"] for row in rows}

    async def replace_homework(
        self, telegram_id: int, identities: set[str], observed_new: set[str] | None = None
    ) -> None:
        """Запомнить задания и время обнаружения, не забывая пропавшие из окна.

        Отсутствие задания в ответе не означает, что оно новое при следующем
        появлении. История сбрасывается явно через forget_all.
        """
        async with self._db.transaction() as db:
            await _write_homework(db, telegram_id, identities, observed_new or set())

    async def token_warned_at(self, telegram_id: int) -> dt.datetime | None:
        """Когда предупредили об истечении мобильного токена. None — не предупреждали."""
        row = await self._db.fetch_one(
            "SELECT token_warned_at FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        return _parse_dt(row["token_warned_at"]) if row and row["token_warned_at"] else None

    async def set_token_warned(self, telegram_id: int, when: dt.datetime | None) -> None:
        """Отметить предупреждение (или сбросить его: `None`)."""
        await self._db.execute(
            "UPDATE users SET token_warned_at = ? WHERE telegram_id = ?",
            (when.isoformat(timespec="seconds") if when else None, telegram_id),
        )

    async def load_absences(self, telegram_id: int) -> set[str]:
        rows = await self._db.fetch_all(
            "SELECT identity FROM tracked_absences WHERE telegram_id = ?", (telegram_id,)
        )
        return {row["identity"] for row in rows}

    async def replace_absences(self, telegram_id: int, identities: set[str]) -> None:
        """Запомнить виденные отметки, включая временно пропавшие из ответа."""
        async with self._db.transaction() as db:
            await _write_absences(db, telegram_id, identities)

    async def save_check(
        self,
        telegram_id: int,
        *,
        tracked: dict[str, TrackedMark],
        homework: set[str],
        observed_new_homework: set[str],
        absences: set[str] | None,
        outbox: list[tuple[str, str]],
        history: list[tuple[str, str, str, str]],
        baseline_done: bool,
        schedule: dict[dt.date, list] | None = None,
        today: dt.date | None = None,
    ) -> None:
        """Итог одной проверки — одной транзакцией.

        Состояние слежения и уведомления о найденных изменениях пишутся
        вместе: либо запомнили и поставили в очередь, либо ни то ни другое.
        `outbox` — пары (вид, JSON), `history` — (вид, JSON оценки, было, стало).
        """
        async with self._db.transaction() as db:
            await _write_marks(db, telegram_id, tracked)
            await _write_homework(db, telegram_id, homework, observed_new_homework)
            if absences is not None:
                await _write_absences(db, telegram_id, absences)
            if outbox:
                await db.executemany(
                    "INSERT INTO outbox (telegram_id, kind, payload) VALUES (?, ?, ?)",
                    [(telegram_id, kind, payload) for kind, payload in outbox],
                )
            if history:
                await db.executemany(
                    """INSERT INTO mark_history (telegram_id, kind, payload, old_mark, new_mark)
                       VALUES (?, ?, ?, ?, ?)""",
                    [(telegram_id, *row) for row in history],
                )
                # История — не архив навсегда: последних записей хватает,
                # чтобы восстановить пропущенное.
                await db.execute(
                    """DELETE FROM mark_history WHERE telegram_id = ? AND id NOT IN (
                           SELECT id FROM mark_history WHERE telegram_id = ?
                           ORDER BY id DESC LIMIT ?)""",
                    (telegram_id, telegram_id, HISTORY_LIMIT),
                )
            if schedule is not None:
                if schedule:
                    await db.executemany(
                        """INSERT INTO tracked_schedule (telegram_id, day, payload) VALUES (?, ?, ?)
                           ON CONFLICT(telegram_id, day) DO UPDATE SET payload = excluded.payload""",
                        [
                            (telegram_id, day.isoformat(), json.dumps(lessons, ensure_ascii=False))
                            for day, lessons in schedule.items()
                            # Пустой день — скорее сбой ответа: прежний снимок не затираем.
                            if lessons
                        ],
                    )
                if today is not None:
                    await db.execute(
                        "DELETE FROM tracked_schedule WHERE telegram_id = ? AND day < ?",
                        (telegram_id, today.isoformat()),
                    )
            if baseline_done:
                await db.execute(
                    "UPDATE users SET baseline_pending = 0 WHERE telegram_id = ?", (telegram_id,)
                )

    async def load_schedule(self, telegram_id: int) -> dict[dt.date, list]:
        rows = await self._db.fetch_all(
            "SELECT day, payload FROM tracked_schedule WHERE telegram_id = ?", (telegram_id,)
        )
        result: dict[dt.date, list] = {}
        for row in rows:
            try:
                result[dt.date.fromisoformat(row["day"])] = [tuple(item) for item in json.loads(row["payload"])]
            except (ValueError, TypeError):
                continue
        return result

    async def pending_outbox(self, telegram_id: int) -> list[aiosqlite.Row]:
        """Неотправленные уведомления в порядке появления."""
        return await self._db.fetch_all(
            """SELECT id, kind, payload, created_at, attempts, student FROM outbox
               WHERE telegram_id = ? ORDER BY id""",
            (telegram_id,),
        )

    async def ack_outbox(self, item_id: int) -> None:
        await self._db.execute("DELETE FROM outbox WHERE id = ?", (item_id,))

    async def fail_outbox(self, item_id: int, error: str) -> None:
        await self._db.execute(
            "UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?",
            (error[:500], item_id),
        )

    async def mark_history(
        self, telegram_id: int, *, limit: int = 50, before_id: int | None = None
    ) -> list[aiosqlite.Row]:
        """Последние изменения оценок, новые сверху."""
        return await self._db.fetch_all(
            """SELECT id, kind, payload, old_mark, new_mark, created_at FROM mark_history
               WHERE telegram_id = ? AND id < ? ORDER BY id DESC LIMIT ?""",
            (telegram_id, before_id if before_id is not None else 2**62, limit),
        )

    async def mark_watch_ok(self, telegram_id: int) -> None:
        """Дневник открылся вручную — это тоже успешная проверка.

        Срок следующей фоновой проверки не трогаем: она идёт по своему расписанию.
        """
        await self._db.execute(
            """INSERT INTO watch_status (telegram_id, last_ok_at) VALUES (?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET last_ok_at = excluded.last_ok_at""",
            (telegram_id, _utc_now_text()),
        )

    async def set_watch_status(
        self,
        telegram_id: int,
        *,
        ok: bool,
        error: str = "",
        next_check_at: dt.datetime | None = None,
    ) -> None:
        """Запомнить исход проверки: для /status и экрана профиля."""
        now = _utc_now_text()
        next_text = next_check_at.isoformat(timespec="seconds") if next_check_at else None
        if ok:
            await self._db.execute("DELETE FROM watch_error_notices WHERE telegram_id = ?", (telegram_id,))
            sql = """INSERT INTO watch_status (telegram_id, last_ok_at, next_check_at)
                     VALUES (?, ?, ?)
                     ON CONFLICT(telegram_id) DO UPDATE SET
                         last_ok_at = excluded.last_ok_at,
                         next_check_at = excluded.next_check_at"""
            params: tuple = (telegram_id, now, next_text)
        else:
            sql = """INSERT INTO watch_status (telegram_id, last_error_at, last_error, next_check_at)
                     VALUES (?, ?, ?, ?)
                     ON CONFLICT(telegram_id) DO UPDATE SET
                         last_error_at = excluded.last_error_at,
                         last_error = excluded.last_error,
                         next_check_at = excluded.next_check_at"""
            params = (telegram_id, now, error[:500], next_text)
        try:
            await self._db.execute(sql, params)
        except aiosqlite.IntegrityError:
            # Пользователя уже удалили, а проверка ещё доходила круг.
            pass

    async def digest_period(self, telegram_id: int, kind: str) -> str:
        """За какой период сводка этого вида ушла последней; "" — ни разу."""
        row = await self._db.fetch_one(
            "SELECT period FROM digests_sent WHERE telegram_id = ? AND kind = ?",
            (telegram_id, kind),
        )
        return row["period"] if row else ""

    async def set_digest_period(self, telegram_id: int, kind: str, period: str) -> None:
        await self._db.execute(
            """INSERT INTO digests_sent (telegram_id, kind, period) VALUES (?, ?, ?)
               ON CONFLICT(telegram_id, kind) DO UPDATE SET period = excluded.period""",
            (telegram_id, kind, period),
        )

    async def save_term_marks(self, telegram_id: int, snapshot: str, outbox: list[tuple[str, str]]) -> None:
        """Снимок четвертных оценок и уведомления о новых — одной транзакцией.

        Снимок — одна строка JSON, поэтому живёт в `digests_sent` под своим
        видом, а не в отдельной таблице.
        """
        async with self._db.transaction() as db:
            await db.execute(
                """INSERT INTO digests_sent (telegram_id, kind, period) VALUES (?, 'term-marks', ?)
                   ON CONFLICT(telegram_id, kind) DO UPDATE SET period = excluded.period""",
                (telegram_id, snapshot),
            )
            if outbox:
                await db.executemany(
                    "INSERT INTO outbox (telegram_id, kind, payload) VALUES (?, ?, ?)",
                    [(telegram_id, kind, payload) for kind, payload in outbox],
                )

    async def claim_error_notice(self, telegram_id: int, reason: str) -> bool:
        """Один отправитель для каждой причины до следующей успешной проверки."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "INSERT OR IGNORE INTO watch_error_notices (telegram_id, reason) VALUES (?, ?)",
                (telegram_id, reason))
            return cursor.rowcount == 1

    async def release_error_notice(self, telegram_id: int, reason: str) -> None:
        await self._db.execute(
            "DELETE FROM watch_error_notices WHERE telegram_id = ? AND reason = ?", (telegram_id, reason))

    async def watch_status(self, telegram_id: int) -> WatchStatus:
        row = await self._db.fetch_one(
            """SELECT last_ok_at, last_error_at, last_error, next_check_at,
                      (SELECT COUNT(*) FROM outbox WHERE telegram_id = ?) AS queued
               FROM watch_status WHERE telegram_id = ?""",
            (telegram_id, telegram_id),
        )
        if row is None:
            return WatchStatus()
        return WatchStatus(
            last_ok_at=_parse_dt(row["last_ok_at"]),
            last_error_at=_parse_dt(row["last_error_at"]),
            last_error=row["last_error"] or "",
            next_check_at=_parse_dt(row["next_check_at"]),
            queued=int(row["queued"] or 0),
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

    async def save_mail_check(self, telegram_id: int, messages: list[dict], *, notify: bool) -> None:
        """Снимок почты и очередь атомарны; первая сверка всегда молчит."""
        from ..notifications.outbox import OutboxKind, encode

        async with self._db.transaction() as db:
            cursor = await db.execute("SELECT 1 FROM mail_baselines WHERE telegram_id = ?", (telegram_id,))
            initialized = await cursor.fetchone() is not None
            cursor = await db.execute("SELECT message_id FROM seen_mail WHERE telegram_id = ?", (telegram_id,))
            known = {int(row[0]) for row in await cursor.fetchall()}
            fresh = []
            current = set()
            for message in messages:
                try:
                    mid = int(message["id"])
                except (KeyError, ValueError, TypeError):
                    continue
                if mid <= 0 or mid in current:
                    continue
                current.add(mid)
                if mid not in known:
                    fresh.append(message)
            if initialized and notify and fresh:
                await db.execute("INSERT INTO outbox (telegram_id, kind, payload) VALUES (?, ?, ?)",
                                 (telegram_id, OutboxKind.MAIL.value, encode(OutboxKind.MAIL, fresh)))
            await db.executemany("INSERT OR IGNORE INTO seen_mail (telegram_id, message_id) VALUES (?, ?)",
                                 [(telegram_id, mid) for mid in current])
            await db.execute("INSERT OR IGNORE INTO mail_baselines (telegram_id) VALUES (?)", (telegram_id,))

    async def done_homework(self, telegram_id: int) -> set[str]:
        rows = await self._db.fetch_all(
            "SELECT homework_key FROM homework_done WHERE telegram_id = ?", (telegram_id,)
        )
        return {row["homework_key"] for row in rows}

    async def set_homework_done(self, telegram_id: int, key: str, done: bool) -> None:
        if done:
            await self._db.execute(
                "INSERT OR IGNORE INTO homework_done (telegram_id, homework_key) VALUES (?, ?)",
                (telegram_id, key),
            )
        else:
            await self._db.execute(
                "DELETE FROM homework_done WHERE telegram_id = ? AND homework_key = ?",
                (telegram_id, key),
            )

    async def user_prefs(self, telegram_id: int) -> dict[str, Any]:
        """Настройки приложения (пороги оценок, учебные периоды) — для сводок."""
        row = await self._db.fetch_one(
            "SELECT payload FROM web_prefs WHERE telegram_id = ?", (telegram_id,)
        )
        try:
            data = json.loads(row["payload"]) if row else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    async def load_child(self, telegram_id: int, student_id: int) -> dict[str, Any] | None:
        """Состояние слежения за невыбранным ребёнком; None — ещё не сверяли."""
        rows = await self._db.fetch_all(
            "SELECT section, payload FROM child_state WHERE telegram_id = ? AND student_id = ?",
            (telegram_id, student_id),
        )
        if not rows:
            return None
        state: dict[str, Any] = {}
        for row in rows:
            try:
                state[row["section"]] = json.loads(row["payload"])
            except ValueError:
                continue
        marks: dict[str, TrackedMark] = {}
        for identity, item in (state.get("marks") or {}).items():
            record = _record_from_json(json.dumps(item.get("record"), ensure_ascii=False))
            if record is not None:
                marks[identity] = TrackedMark(record=record, missing_streak=int(item.get("streak") or 0))
        return {
            "marks": marks,
            "homework": set(state.get("homework") or []),
            "absences": set(state.get("absences") or []),
        }

    async def save_child_check(
        self,
        telegram_id: int,
        student_id: int,
        student: str,
        *,
        tracked: dict[str, TrackedMark],
        homework: set[str],
        absences: set[str] | None,
        outbox: list[tuple[str, str]],
    ) -> None:
        """Итог проверки невыбранного ребёнка и его уведомления — одной транзакцией."""
        sections = {
            "marks": {
                identity: {"record": json.loads(_record_to_json(item.record)), "streak": item.missing_streak}
                for identity, item in tracked.items()
            },
            "homework": sorted(homework),
        }
        if absences is not None:
            sections["absences"] = sorted(absences)
        async with self._db.transaction() as db:
            await db.executemany(
                """INSERT INTO child_state (telegram_id, student_id, section, payload) VALUES (?, ?, ?, ?)
                   ON CONFLICT(telegram_id, student_id, section) DO UPDATE SET payload = excluded.payload""",
                [
                    (telegram_id, student_id, section, json.dumps(payload, ensure_ascii=False))
                    for section, payload in sections.items()
                ],
            )
            if outbox:
                await db.executemany(
                    "INSERT INTO outbox (telegram_id, kind, payload, student) VALUES (?, ?, ?, ?)",
                    [(telegram_id, kind, payload, student) for kind, payload in outbox],
                )

    async def forget_children(self, telegram_id: int, keep: set[int] | None = None) -> None:
        """Забыть состояние детей, кроме `keep` (выбранный ребёнок сменился и т. п.)."""
        rows = await self._db.fetch_all(
            "SELECT DISTINCT student_id FROM child_state WHERE telegram_id = ?", (telegram_id,)
        )
        drop = [(telegram_id, int(r["student_id"])) for r in rows if int(r["student_id"]) not in (keep or set())]
        if drop:
            await self._db.execute_many(
                "DELETE FROM child_state WHERE telegram_id = ? AND student_id = ?", drop
            )

    async def remember_diary(self, telegram_id: int, school_url: str, student_id: int | None) -> None:
        """Запомнить при выходе, чей дневник был открыт; пустой url — забыть."""
        await self._db.execute(
            "UPDATE users SET last_school_url = ?, last_student_id = ? WHERE telegram_id = ?",
            (school_url, student_id, telegram_id),
        )

    async def last_diary(self, telegram_id: int) -> tuple[str, int] | None:
        """Школа и ученик до выхода, если выходили."""
        row = await self._db.fetch_one(
            "SELECT last_school_url, last_student_id FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        if not row or not row["last_school_url"] or row["last_student_id"] is None:
            return None
        return row["last_school_url"], int(row["last_student_id"])

    async def drop_outbox(self, telegram_id: int) -> None:
        """Неотправленные уведомления — после выхода они уже не к месту."""
        await self._db.execute("DELETE FROM outbox WHERE telegram_id = ?", (telegram_id,))

    async def forget_all(self, telegram_id: int) -> None:
        """Сбросить слежение — например при смене школы или ученика."""
        async with self._db.transaction() as db:
            for table in (
                "tracked_marks", "tracked_homework", "seen_mail", "mail_baselines", "tracked_absences",
                "outbox", "mark_history", "homework_done", "tracked_schedule", "child_state",
            ):
                await db.execute(f"DELETE FROM {table} WHERE telegram_id = ?", (telegram_id,))


FOLLOW_INVITE_TTL = 24 * 3600
MAX_FOLLOWERS = 5


class FamilyRepository:
    """Родные ученика: приглашения, копии уведомлений и просмотр дневника.

    Что доступно приглашённому, выбирает ученик: can_notify — копии
    уведомлений (Telegram и push), can_view — дневник в приложении только для
    чтения. Права можно поменять или отозвать, приглашённый может отказаться.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def issue_invite(self, owner_id: int, *, notify: bool = True, view: bool = True) -> str:
        if not (notify or view):
            raise ValueError("Приглашение без прав")
        code = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")
        now = time.time()
        async with self._db.transaction() as db:
            await db.execute("DELETE FROM follow_invites WHERE expires_at < ? OR owner_id = ?", (now, owner_id))
            await db.execute(
                "INSERT INTO follow_invites (code, owner_id, expires_at, can_notify, can_view) VALUES (?, ?, ?, ?, ?)",
                (code, owner_id, now + FOLLOW_INVITE_TTL, int(notify), int(view)),
            )
        return code

    async def invite(self, code: str) -> dict[str, Any] | None:
        """Действующее приглашение: чьё и что даёт."""
        row = await self._db.fetch_one(
            "SELECT owner_id, can_notify, can_view FROM follow_invites WHERE code = ? AND expires_at >= ?",
            (code, time.time()),
        )
        if not row:
            return None
        return {"owner_id": int(row["owner_id"]), "notify": bool(row["can_notify"]), "view": bool(row["can_view"])}

    async def invite_owner(self, code: str) -> int | None:
        invite = await self.invite(code)
        return invite["owner_id"] if invite else None

    async def accept(self, code: str, follower_id: int, name: str = "") -> int | None:
        """Принять приглашение. Возвращает владельца или None (просрочено, своё, лимит)."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "SELECT owner_id, can_notify, can_view FROM follow_invites WHERE code = ? AND expires_at >= ?",
                (code, time.time()),
            )
            row = await cursor.fetchone()
            if row is None or int(row[0]) == follower_id:
                return None
            owner_id, notify, view = int(row[0]), int(row[1]), int(row[2])
            cursor = await db.execute(
                "SELECT COUNT(*) FROM followers WHERE owner_id = ? AND follower_id != ?", (owner_id, follower_id))
            if (await cursor.fetchone())[0] >= MAX_FOLLOWERS:
                return None
            # Повторное приглашение того же человека заменяет его права.
            await db.execute(
                """INSERT INTO followers (owner_id, follower_id, name, created_at, can_notify, can_view)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(owner_id, follower_id) DO UPDATE SET
                       name = excluded.name, can_notify = excluded.can_notify, can_view = excluded.can_view""",
                (owner_id, follower_id, name[:64], time.time(), notify, view),
            )
            # Ссылка одноразовая: пересланная дальше, она уже не сработает.
            await db.execute("DELETE FROM follow_invites WHERE code = ?", (code,))
        return owner_id

    async def followers(self, owner_id: int, *, notify_only: bool = False) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT follower_id, name, created_at, can_notify, can_view FROM followers WHERE owner_id = ?"
            + (" AND can_notify = 1" if notify_only else "") + " ORDER BY created_at",
            (owner_id,),
        )
        return [{"id": int(r["follower_id"]), "name": r["name"], "since": r["created_at"],
                 "notify": bool(r["can_notify"]), "view": bool(r["can_view"])} for r in rows]

    async def following(self, follower_id: int) -> list[int]:
        return [item["owner_id"] for item in await self.access(follower_id)]

    async def access(self, follower_id: int) -> list[dict[str, Any]]:
        """К кому у приглашённого есть доступ и какой."""
        rows = await self._db.fetch_all(
            "SELECT owner_id, can_notify, can_view FROM followers WHERE follower_id = ? ORDER BY created_at",
            (follower_id,),
        )
        return [{"owner_id": int(r["owner_id"]), "notify": bool(r["can_notify"]), "view": bool(r["can_view"])}
                for r in rows]

    async def set_rights(self, owner_id: int, follower_id: int, *, notify: bool, view: bool) -> bool:
        """Ученик меняет права. Без единого права приглашённый убирается."""
        if not (notify or view):
            return await self.remove(owner_id, follower_id)
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "UPDATE followers SET can_notify = ?, can_view = ? WHERE owner_id = ? AND follower_id = ?",
                (int(notify), int(view), owner_id, follower_id),
            )
            if not view:
                await db.execute("DELETE FROM follow_views WHERE viewer_id = ? AND owner_id = ?", (follower_id, owner_id))
            return cursor.rowcount > 0

    async def remove(self, owner_id: int, follower_id: int) -> bool:
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "DELETE FROM followers WHERE owner_id = ? AND follower_id = ?", (owner_id, follower_id)
            )
            await db.execute("DELETE FROM follow_views WHERE viewer_id = ? AND owner_id = ?", (follower_id, owner_id))
            return cursor.rowcount > 0

    async def viewing(self, viewer_id: int) -> int | None:
        """Чей дневник приглашённый выбрал смотреть (None — свой или не выбирал)."""
        row = await self._db.fetch_one("SELECT owner_id FROM follow_views WHERE viewer_id = ?", (viewer_id,))
        return int(row["owner_id"]) if row else None

    async def set_viewing(self, viewer_id: int, owner_id: int | None) -> None:
        if owner_id is None:
            await self._db.execute("DELETE FROM follow_views WHERE viewer_id = ?", (viewer_id,))
            return
        await self._db.execute(
            """INSERT INTO follow_views (viewer_id, owner_id) VALUES (?, ?)
               ON CONFLICT(viewer_id) DO UPDATE SET owner_id = excluded.owner_id""",
            (viewer_id, owner_id),
        )


MAX_NOTES = 300
MAX_NOTE_TEXT = 500


class NotesRepository:
    """Свои заметки и задачи ученика: к дате (и, если нужно, ко времени) и
    к предмету. Ко времени можно попросить напоминание за N минут."""

    _COLUMNS = "id, day, subject, text, done, at, remind"

    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _row(row: aiosqlite.Row) -> dict[str, Any]:
        return {"id": int(row["id"]), "date": row["day"], "subject": row["subject"],
                "text": row["text"], "done": bool(row["done"]), "time": row["at"] or "",
                "remind": row["remind"]}

    @staticmethod
    def remind_moment(day: str, at: str, remind: int | None, tz: dt.tzinfo) -> float | None:
        """Когда напомнить (UTC, секунды): за remind минут до day at."""
        if not at or remind is None:
            return None
        moment = dt.datetime.combine(dt.date.fromisoformat(day), dt.time.fromisoformat(at), tzinfo=tz)
        return (moment - dt.timedelta(minutes=remind)).timestamp()

    async def between(self, telegram_id: int, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            f"""SELECT {self._COLUMNS} FROM user_notes
               WHERE telegram_id = ? AND day >= ? AND day <= ? ORDER BY day, at = '', at, id""",
            (telegram_id, start.isoformat(), end.isoformat()),
        )
        return [self._row(r) for r in rows]

    async def add(self, telegram_id: int, day: dt.date, text: str, subject: str = "", *,
                  at: str = "", remind: int | None = None, tz: dt.tzinfo = dt.timezone.utc) -> dict[str, Any] | None:
        text = " ".join(text.split())[:MAX_NOTE_TEXT]
        if not text:
            return None
        remind = remind if at else None
        remind_at = self.remind_moment(day.isoformat(), at, remind, tz)
        async with self._db.transaction() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM user_notes WHERE telegram_id = ?", (telegram_id,))
            if (await cursor.fetchone())[0] >= MAX_NOTES:
                # Старые выполненные уступают место новым.
                await db.execute(
                    """DELETE FROM user_notes WHERE id IN (SELECT id FROM user_notes
                       WHERE telegram_id = ? ORDER BY done DESC, day LIMIT 1)""",
                    (telegram_id,),
                )
            cursor = await db.execute(
                """INSERT INTO user_notes (telegram_id, day, subject, text, created_at, at, remind, remind_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (telegram_id, day.isoformat(), subject.strip()[:120], text, time.time(), at, remind, remind_at),
            )
            note_id = cursor.lastrowid
        return {"id": note_id, "date": day.isoformat(), "subject": subject.strip()[:120], "text": text,
                "done": False, "time": at, "remind": remind}

    async def update(self, telegram_id: int, note_id: int, *, done: bool | None = None,
                     text: str | None = None, day: dt.date | None = None, subject: str | None = None,
                     at: str | None = None, remind: Any = ...,
                     tz: dt.tzinfo = dt.timezone.utc) -> dict[str, Any] | None:
        """Изменить задачу. Возвращает её новый вид или None, если её нет.
        remind=None — без напоминания; не передан (...) — как было."""
        if text is not None:
            text = " ".join(text.split())[:MAX_NOTE_TEXT]
            if not text:
                return None
        async with self._db.transaction() as db:
            cursor = await db.execute(
                f"SELECT {self._COLUMNS}, remind_at FROM user_notes WHERE id = ? AND telegram_id = ?",
                (note_id, telegram_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            note = self._row(row)
            if done is not None:
                note["done"] = bool(done)
            if text is not None:
                note["text"] = text
            if day is not None:
                note["date"] = day.isoformat()
            if subject is not None:
                note["subject"] = subject.strip()[:120]
            if at is not None:
                note["time"] = at
            if remind is not ...:
                note["remind"] = remind
            if not note["time"]:
                note["remind"] = None
            remind_at = row["remind_at"]
            # Сдвинули время или напоминание — напомнить заново, к новому сроку.
            if (day, at, remind) != (None, None, ...):
                remind_at = self.remind_moment(note["date"], note["time"], note["remind"], tz)
            await db.execute(
                """UPDATE user_notes SET done = ?, text = ?, day = ?, subject = ?, at = ?, remind = ?,
                   remind_at = ? WHERE id = ? AND telegram_id = ?""",
                (int(note["done"]), note["text"], note["date"], note["subject"], note["time"],
                 note["remind"], remind_at, note_id, telegram_id),
            )
        return note

    async def due_reminders(self, now: float) -> list[tuple[int, dict[str, Any], float]]:
        """Напоминания, время которых пришло: (ученик, задача, когда надо было)."""
        rows = await self._db.fetch_all(
            f"""SELECT telegram_id, remind_at, {self._COLUMNS} FROM user_notes
               WHERE remind_at IS NOT NULL AND remind_at <= ? AND done = 0 ORDER BY remind_at""",
            (now,),
        )
        return [(int(r["telegram_id"]), self._row(r), float(r["remind_at"])) for r in rows]

    async def reminded(self, note_id: int) -> None:
        await self._db.execute("UPDATE user_notes SET remind_at = NULL WHERE id = ?", (note_id,))

    async def delete(self, telegram_id: int, note_id: int) -> bool:
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "DELETE FROM user_notes WHERE id = ? AND telegram_id = ?", (note_id, telegram_id)
            )
            return cursor.rowcount > 0


class SessionRepository:
    """Сохранённые сессии «Сетевого города»."""

    def __init__(self, db: Database, cipher: PasswordCipher = NO_ENCRYPTION) -> None:
        self._db = db
        self._cipher = cipher

    async def encrypt_stored_sessions(self) -> int:
        rows = await self._db.fetch_all("SELECT telegram_id, payload FROM netschool_sessions")
        updates = [(self._cipher.rewrite(r["payload"]), r["telegram_id"])
                   for r in rows if self._cipher.needs_rewrite(r["payload"])]
        await self._db.execute_many("UPDATE netschool_sessions SET payload = ? WHERE telegram_id = ?", updates)
        return len(updates)

    async def load(self, telegram_id: int) -> str | None:
        row = await self._db.fetch_one(
            "SELECT payload FROM netschool_sessions WHERE telegram_id = ?", (telegram_id,)
        )
        return self._cipher.decrypt(row["payload"]) if row else None

    async def save(self, telegram_id: int, payload: str) -> None:
        await self._db.execute(
            """INSERT INTO netschool_sessions (telegram_id, payload, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(telegram_id) DO UPDATE SET
                   payload = excluded.payload, updated_at = excluded.updated_at""",
            (telegram_id, self._cipher.encrypt(payload)),
        )

    async def drop(self, telegram_id: int) -> None:
        await self._db.execute(
            "DELETE FROM netschool_sessions WHERE telegram_id = ?", (telegram_id,)
        )


def token_fingerprint(token: str) -> str:
    """Короткий отпечаток токена: по нему экран «Устройства» и сокеты
    различают сеансы, не показывая сам токен."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


class MiniappRepository:
    """Постоянные ссылки на PWA, коды подтверждения и push-подписки."""

    def __init__(self, db: Database, *, login_code_ttl: int = 600) -> None:
        self._db = db
        self._login_code_ttl = login_code_ttl

    async def issue_token(
        self, telegram_id: int, *, revoke_existing: bool = False, device: bool = False, method: str = "",
    ) -> str:
        """Выдать (или переиспользовать) постоянный токен доступа к PWA.

        device=True — новый токен для входа на одном устройстве: его
        отзывает выход на этом устройстве, общий токен при этом не трогается."""
        if revoke_existing:
            await self.revoke_tokens(telegram_id)
        elif not device:
            row = await self._db.fetch_one(
                """SELECT token FROM miniapp_tokens
                   WHERE telegram_id = ? AND revoked_at IS NULL AND device = 0 AND scope = ''
                   ORDER BY created_at DESC LIMIT 1""",
                (telegram_id,),
            )
            if row:
                return row["token"]

        token = secrets.token_urlsafe(32)
        await self._db.execute(
            "INSERT INTO miniapp_tokens (token, telegram_id, device, method) VALUES (?,?,?,?)",
            (token, telegram_id, int(device), method),
        )
        return token

    async def token_method(self, token: str) -> str:
        row = await self._db.fetch_one("SELECT method FROM miniapp_tokens WHERE token = ?", (token,))
        return row["method"] if row else ""

    async def end_inactive_sessions(self, telegram_id: int | None = None, now: float | None = None) -> int:
        """Завершить сеансы, где давно не открывали дневник, — у тех, кто
        включил это в «Устройствах» (web_prefs.devicesInactiveDays).

        Только устройства и ссылки из бота: Telegram не токен, а календарь
        открывает одно расписание и опрашивается сам. Отсчёт — от последней
        активности, а если её не было — от входа."""
        now = time.time() if now is None else now
        where = "AND t.telegram_id = ?" if telegram_id is not None else ""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                f"""UPDATE miniapp_tokens AS t SET revoked_at = datetime('now')
                    FROM web_prefs AS p
                    WHERE p.telegram_id = t.telegram_id AND t.revoked_at IS NULL AND t.scope = ''
                      AND CAST(json_extract(p.payload, '$.devicesInactiveDays') AS INTEGER) > 0
                      AND COALESCE(t.last_used, CAST(strftime('%s', t.created_at) AS REAL))
                          < ? - CAST(json_extract(p.payload, '$.devicesInactiveDays') AS INTEGER) * 86400
                      {where}""",
                (now, telegram_id) if telegram_id is not None else (now,),
            )
            return cursor.rowcount

    async def revoke_device_token(self, token: str) -> bool:
        """Выход на одном устройстве: отозвать его токен, если он «устройственный»."""
        if not token:
            return False
        async with self._db.transaction() as db:
            cursor = await db.execute(
                """UPDATE miniapp_tokens SET revoked_at = datetime('now')
                   WHERE token = ? AND device = 1 AND revoked_at IS NULL""",
                (token,),
            )
            return cursor.rowcount > 0

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
            "SELECT telegram_id FROM miniapp_tokens WHERE token = ? AND revoked_at IS NULL AND scope = ''",
            (token,),
        )
        return int(row["telegram_id"]) if row else None

    async def issue_calendar_token(self, telegram_id: int) -> str:
        """Токен подписки на календарь: годится только для calendar.ics.
        Повторный запрос ссылки отдаёт тот же — подписка не ломается."""
        row = await self._db.fetch_one(
            """SELECT token FROM miniapp_tokens
               WHERE telegram_id = ? AND revoked_at IS NULL AND scope = 'calendar'
               ORDER BY created_at DESC LIMIT 1""",
            (telegram_id,),
        )
        if row:
            return row["token"]
        token = secrets.token_urlsafe(32)
        await self._db.execute(
            "INSERT INTO miniapp_tokens (token, telegram_id, scope) VALUES (?,?,'calendar')",
            (token, telegram_id),
        )
        return token

    async def resolve_calendar_token(self, token: str) -> int | None:
        if not token:
            return None
        row = await self._db.fetch_one(
            "SELECT telegram_id FROM miniapp_tokens WHERE token = ? AND revoked_at IS NULL AND scope = 'calendar'",
            (token,),
        )
        return int(row["telegram_id"]) if row else None

    async def revoke_tokens(self, telegram_id: int) -> None:
        async with self._db.transaction() as db:
            await db.execute(
                """UPDATE miniapp_tokens SET revoked_at = datetime('now')
                   WHERE telegram_id = ? AND revoked_at IS NULL""",
                (telegram_id,),
            )
            # Ссылки установки и коды иконок открыли бы новый токен — тоже гасим.
            await db.execute("DELETE FROM install_codes WHERE telegram_id = ?", (telegram_id,))

    async def is_device_token(self, token: str) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 FROM miniapp_tokens WHERE token = ? AND device = 1 AND revoked_at IS NULL AND scope = ''",
            (token,),
        )
        return row is not None

    async def touch_token(self, token: str, label: str, model: str = "", ip: str = "") -> int | None:
        """Отметить активность токена для экрана «Устройства»; название
        устройства запоминается при первом использовании. По общему токену
        («Ссылки из бота») ходит несколько устройств — каждое пишется отдельно."""
        now = time.time()
        async with self._db.transaction() as db:
            await db.execute(
                """UPDATE miniapp_tokens SET last_used = ?, label = ?, model = ?, ip = ?
                   WHERE token = ? AND revoked_at IS NULL""",
                (now, label, model, ip, token),
            )
            cursor = await db.execute(
                "SELECT telegram_id, device, scope FROM miniapp_tokens WHERE token = ? AND revoked_at IS NULL",
                (token,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            if row[1] or row[2]:
                return int(row[0])
            await db.execute(
                """INSERT INTO token_clients (telegram_id, token_hash, client, model, ip, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT (token_hash, client, model) DO UPDATE SET last_seen = excluded.last_seen, ip = excluded.ip""",
                (row[0], hashlib.sha256(token.encode()).hexdigest(), label, model, ip, now, now),
            )
        return int(row[0])

    async def link_clients(self, telegram_id: int) -> list[dict]:
        """Устройства, ходившие по действующим общим токенам пользователя."""
        tokens = await self._db.fetch_all(
            """SELECT token FROM miniapp_tokens
               WHERE telegram_id = ? AND revoked_at IS NULL AND device = 0 AND scope = ''""",
            (telegram_id,),
        )
        hashes = [hashlib.sha256(row["token"].encode()).hexdigest() for row in tokens]
        if not hashes:
            return []
        marks = ",".join("?" * len(hashes))
        rows = await self._db.fetch_all(
            f"""SELECT client, model, ip, first_seen, last_seen FROM token_clients
                WHERE telegram_id = ? AND token_hash IN ({marks}) ORDER BY last_seen DESC""",
            (telegram_id, *hashes),
        )
        return [dict(row) for row in rows]

    async def live_fingerprints(self, telegram_id: int) -> set[str]:
        """Отпечатки действующих токенов приложения (без календаря)."""
        rows = await self._db.fetch_all(
            "SELECT token FROM miniapp_tokens WHERE telegram_id = ? AND revoked_at IS NULL AND scope = ''",
            (telegram_id,),
        )
        return {token_fingerprint(row["token"]) for row in rows}

    async def set_token_name(self, telegram_id: int, token: str, name: str) -> bool:
        """Своя подпись сеанса на экране «Устройства» («Планшет на кухне»)."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                """UPDATE miniapp_tokens SET name = ?
                   WHERE telegram_id = ? AND token = ? AND revoked_at IS NULL""",
                (name, telegram_id, token),
            )
            return cursor.rowcount > 0

    async def sessions(self, telegram_id: int) -> list[dict]:
        """Действующие токены пользователя — для экрана «Устройства»."""
        rows = await self._db.fetch_all(
            """SELECT token, device, scope, label, name, method, model, ip, created_at, last_used FROM miniapp_tokens
               WHERE telegram_id = ? AND revoked_at IS NULL ORDER BY created_at""",
            (telegram_id,),
        )
        return [dict(row) for row in rows]

    async def revoke_where(self, telegram_id: int, condition: str, params: tuple = ()) -> int:
        """Отозвать токены пользователя по условию (только из кода экрана «Устройства»)."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                f"""UPDATE miniapp_tokens SET revoked_at = datetime('now')
                    WHERE telegram_id = ? AND revoked_at IS NULL AND ({condition})""",
                (telegram_id, *params),
            )
            return cursor.rowcount

    async def drop_install_codes(self, telegram_id: int) -> None:
        await self._db.execute("DELETE FROM install_codes WHERE telegram_id = ?", (telegram_id,))

    ENTER_TTL = 24 * 3600
    ICON_TTL = 180 * 24 * 3600

    async def issue_enter_code(self, telegram_id: int) -> str:
        """Ссылка установки из /app_install: сутки, открывать можно не раз
        (сначала встроенный браузер Telegram, потом Safari) — каждый раз
        это новый токен устройства."""
        code = secrets.token_urlsafe(24)
        await self._db.execute(
            "INSERT INTO install_codes (code, telegram_id, kind, expires_at) VALUES (?,?,'enter',?)",
            (code, telegram_id, time.time() + self.ENTER_TTL),
        )
        return code

    async def use_enter_code(self, code: str) -> int | None:
        row = await self._db.fetch_one(
            "SELECT telegram_id FROM install_codes WHERE code = ? AND kind = 'enter' AND expires_at > ?",
            (code, time.time()),
        )
        return int(row["telegram_id"]) if row else None

    DEVICE_TTL = 15 * 60

    async def issue_device_code(self, telegram_id: int) -> tuple[str, float]:
        """Ссылка с экрана «Добавить устройство»: 15 минут и только одно открытие —
        её показывают на экране и QR-кодом, пересылать дальше незачем."""
        code, expires = secrets.token_urlsafe(24), time.time() + self.DEVICE_TTL
        async with self._db.transaction() as db:
            await db.execute("DELETE FROM install_codes WHERE expires_at < ?", (time.time(),))
            await db.execute(
                "INSERT INTO install_codes (code, telegram_id, kind, expires_at) VALUES (?,?,'device',?)",
                (code, telegram_id, expires),
            )
        return code, expires

    async def use_device_code(self, code: str) -> str | None:
        """Первое открытие: код гаснет и сразу даёт токен устройства."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                """UPDATE install_codes SET used_at = ? WHERE code = ? AND kind = 'device'
                   AND used_at IS NULL AND expires_at > ?""",
                (time.time(), code, time.time()),
            )
            if cursor.rowcount == 0:
                return None
            cursor = await db.execute("SELECT telegram_id FROM install_codes WHERE code = ?", (code,))
            telegram_id = int((await cursor.fetchone())[0])
        token = await self.issue_token(telegram_id, device=True, method="device-link")
        await self._db.execute("UPDATE install_codes SET token = ? WHERE code = ?", (token, code))
        return token

    async def device_code(self, telegram_id: int, code: str) -> dict | None:
        """Состояние своей ссылки: ждёт, открыта (и чем) или истекла."""
        row = await self._db.fetch_one(
            """SELECT c.expires_at, c.used_at, c.token, t.label, t.revoked_at FROM install_codes c
               LEFT JOIN miniapp_tokens t ON t.token = c.token
               WHERE c.code = ? AND c.telegram_id = ? AND c.kind = 'device'""",
            (code, telegram_id),
        )
        return dict(row) if row else None

    async def drop_device_code(self, telegram_id: int, code: str) -> bool:
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "DELETE FROM install_codes WHERE code = ? AND telegram_id = ? AND kind = 'device' AND used_at IS NULL",
                (code, telegram_id),
            )
            return cursor.rowcount > 0

    async def icon_code(self, telegram_id: int, source: str) -> str:
        """Код для start_url иконки. Пока иконку не запустили, на каждый
        запрос манифеста отдаётся тот же код, а не плодится новый."""
        row = await self._db.fetch_one(
            """SELECT code FROM install_codes WHERE telegram_id = ? AND kind = 'icon' AND source = ?
               AND used_at IS NULL AND expires_at > ? ORDER BY expires_at DESC LIMIT 1""",
            (telegram_id, source, time.time()),
        )
        if row:
            return row["code"]
        code = secrets.token_urlsafe(24)
        async with self._db.transaction() as db:
            await db.execute("DELETE FROM install_codes WHERE expires_at < ?", (time.time(),))
            await db.execute(
                "INSERT INTO install_codes (code, telegram_id, kind, source, expires_at) VALUES (?,?,'icon',?,?)",
                (code, telegram_id, source, time.time() + self.ICON_TTL),
            )
        return code

    async def use_icon_code(self, code: str) -> int | None:
        """Первый запуск иконки: код срабатывает один раз."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                """UPDATE install_codes SET used_at = ? WHERE code = ? AND kind = 'icon'
                   AND used_at IS NULL AND expires_at > ?""",
                (time.time(), code, time.time()),
            )
            if cursor.rowcount == 0:
                return None
            cursor = await db.execute("SELECT telegram_id FROM install_codes WHERE code = ?", (code,))
            row = await cursor.fetchone()
        return int(row[0]) if row else None

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

    async def record_visit(self, telegram_id: int, design: str, client: str,
                           halloween: bool | None = None) -> None:
        """Открытие приложения: последний дизайн и откуда открыто (и включено
        ли хеллоуинское оформление), а также все места, где его открывали."""
        now = time.time()
        flag = None if halloween is None else int(halloween)
        async with self._db.transaction() as db:
            await db.execute(
                """INSERT INTO app_visits (telegram_id, design, client, first_seen_at, seen_at, halloween)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(telegram_id) DO UPDATE SET
                       design = excluded.design, client = excluded.client, seen_at = excluded.seen_at,
                       halloween = excluded.halloween""",
                (telegram_id, design, client, now, now, flag),
            )
            await db.execute(
                """INSERT INTO app_clients (telegram_id, client, first_seen_at, seen_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(telegram_id, client) DO UPDATE SET seen_at = excluded.seen_at""",
                (telegram_id, client, now, now),
            )

    async def has_installed_app(self, telegram_id: int) -> bool:
        """Открывали ли приложение с экрана «Домой»: последний вход оттуда
        или пуш-подписка Apple (на iPhone она есть только у установленного)."""
        row = await self._db.fetch_one(
            """SELECT 1 FROM app_visits WHERE telegram_id = ? AND client = 'pwa'
               UNION ALL
               SELECT 1 FROM push_subscriptions
               WHERE telegram_id = ? AND endpoint LIKE 'https://web.push.apple.com/%'
               LIMIT 1""",
            (telegram_id, telegram_id),
        )
        return row is not None

    async def visit_stats(self, since: float) -> dict[str, dict[str, int]]:
        """Сколько открывавших с `since` — по дизайну и по месту открытия;
        halloween — по дизайну те, у кого включено хеллоуинское оформление."""
        result: dict[str, dict[str, int]] = {}
        for column in ("design", "client"):
            rows = await self._db.fetch_all(
                f"SELECT {column} AS key, COUNT(*) AS n FROM app_visits WHERE seen_at >= ? GROUP BY {column}",
                (since,))
            result[column] = {row["key"]: row["n"] for row in rows}
        rows = await self._db.fetch_all(
            "SELECT design AS key, COUNT(*) AS n FROM app_visits WHERE seen_at >= ? AND halloween = 1 GROUP BY design",
            (since,))
        result["halloween"] = {row["key"]: row["n"] for row in rows}
        return result

    async def audience_stats(self, now: float, web_id_base: int) -> dict[str, Any]:
        """Для /adminstats: пользователи по способу входа и по местам, где
        открывали приложение. «Новые» — за 7 дней до `now`.

        login: telegram (без почты), both (Telegram и почта), email (только
        почта — служебный id от `web_id_base`). places — за всё время (all) и
        за 30 дней (month): telegram — только в Telegram, both — и там, и в
        браузере, browser — только в браузере (сайт или установленное);
        pwa — сколько из них открывали установленное приложение.
        """
        week_ago = now - 7 * 86400
        week_ago_text = dt.datetime.fromtimestamp(week_ago, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        rows = await self._db.fetch_all(
            """SELECT CASE WHEN u.telegram_id >= ? THEN 'email'
                           WHEN a.telegram_id IS NULL THEN 'telegram' ELSE 'both' END AS kind,
                      COUNT(*) AS n, SUM(u.created_at >= ?) AS fresh
               FROM users u LEFT JOIN accounts a ON a.telegram_id = u.telegram_id
               GROUP BY kind""",
            (web_id_base, week_ago_text),
        )
        login = {key: {"n": 0, "new": 0} for key in ("telegram", "both", "email")}
        for row in rows:
            login[row["kind"]] = {"n": row["n"], "new": row["fresh"] or 0}
        email = await self._db.fetch_one(
            "SELECT COUNT(*) AS linked, COUNT(email_verified_at) AS verified FROM accounts")
        places = {}
        for period, since in (("all", 0.0), ("month", now - 30 * 86400)):
            rows = await self._db.fetch_all(
                """SELECT kind, COUNT(*) AS n, SUM(first >= ?) AS fresh, SUM(pwa) AS pwa FROM (
                       SELECT CASE WHEN MAX(client = 'telegram') AND MAX(client != 'telegram') THEN 'both'
                                   WHEN MAX(client = 'telegram') THEN 'telegram' ELSE 'browser' END AS kind,
                              MIN(first_seen_at) AS first, MAX(client = 'pwa') AS pwa
                       FROM app_clients WHERE seen_at >= ? GROUP BY telegram_id)
                   GROUP BY kind""",
                (week_ago, since),
            )
            counts = {key: {"n": 0, "new": 0, "pwa": 0} for key in ("telegram", "both", "browser")}
            for row in rows:
                counts[row["kind"]] = {"n": row["n"], "new": row["fresh"] or 0, "pwa": row["pwa"] or 0}
            places[period] = counts
        return {"login": login, "email": dict(email), "places": places}

    async def claim_chat_launch_notice(self, telegram_id: int) -> bool:
        """Выдать объявление один раз на аккаунт, в том числе между устройствами."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "DELETE FROM chat_launch_notices WHERE telegram_id = ?", (telegram_id,)
            )
            return cursor.rowcount > 0

    async def survey_pending(self, telegram_id: int) -> bool:
        """Показать ли опрос: аккаунт был до него и ещё не ответил и не отказался."""
        async with self._db.transaction() as db:
            await db.execute(
                "UPDATE app_survey SET shown_at = ? WHERE telegram_id = ? AND shown_at IS NULL",
                (time.time(), telegram_id),
            )
            cursor = await db.execute(
                "SELECT 1 FROM app_survey WHERE telegram_id = ? AND score IS NULL AND declined_at IS NULL",
                (telegram_id,),
            )
            return await cursor.fetchone() is not None

    async def answer_survey(self, telegram_id: int, score: int) -> bool:
        """Записать оценку 1–10 — один раз и только тем, кому опрос положен."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "UPDATE app_survey SET score = ?, answered_at = ? "
                "WHERE telegram_id = ? AND score IS NULL AND declined_at IS NULL",
                (score, time.time(), telegram_id),
            )
            return cursor.rowcount > 0

    async def decline_survey(self, telegram_id: int) -> bool:
        """«Не хочу»: больше не спрашивать."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "UPDATE app_survey SET declined_at = ? "
                "WHERE telegram_id = ? AND score IS NULL AND declined_at IS NULL",
                (time.time(), telegram_id),
            )
            return cursor.rowcount > 0

    async def survey_stats(self) -> dict[str, Any]:
        """Итоги опроса для админа: сколько показали, ответили, отказались, оценки."""
        totals = await self._db.fetch_one(
            "SELECT COUNT(shown_at) AS shown, COUNT(score) AS answered, COUNT(declined_at) AS declined, "
            "AVG(score) AS average FROM app_survey"
        )
        rows = await self._db.fetch_all(
            "SELECT score, COUNT(*) AS n FROM app_survey WHERE score IS NOT NULL GROUP BY score"
        )
        return {
            "shown": totals["shown"],
            "answered": totals["answered"],
            "declined": totals["declined"],
            "average": totals["average"],
            "scores": {row["score"]: row["n"] for row in rows},
        }

    async def prefs(self, telegram_id: int) -> dict[str, Any]:
        """Настройки мини-приложения: одни и те же в Telegram и на сайте."""
        row = await self._db.fetch_one(
            "SELECT payload FROM web_prefs WHERE telegram_id = ?", (telegram_id,)
        )
        if not row:
            return {}
        try:
            data = json.loads(row["payload"])
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    async def save_prefs(self, telegram_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        """Дописать изменённые ключи к сохранённым и вернуть итог."""
        merged = {**await self.prefs(telegram_id), **changes}
        await self._db.execute(
            """INSERT INTO web_prefs (telegram_id, payload, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(telegram_id) DO UPDATE SET
                   payload = excluded.payload, updated_at = excluded.updated_at""",
            (telegram_id, json.dumps(merged, ensure_ascii=False)),
        )
        return merged

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

    async def mark_mail_read(self, telegram_id: int, message_id: int) -> None:
        """Отметить письмо прочитанным в сохранённом списке, не продлевая ему
        свежесть: иначе значок «Почты» до часа показывал прочитанное как новое."""
        cached = await self.get(telegram_id, "mail")
        if cached is None:
            return
        payload, _ = cached
        changed = False
        for message in payload.get("messages") or []:
            if str(message.get("id")) == str(message_id) and message.get("read") is False:
                message["read"] = True
                changed = True
        if changed:
            await self._db.execute(
                "UPDATE cache_entries SET payload = ? WHERE telegram_id = ? AND section = 'mail'",
                (json.dumps(payload, ensure_ascii=False), telegram_id),
            )

    async def drop_mail(self, telegram_id: int, message_id: int) -> None:
        """Убрать удалённое письмо из сохранённого списка, не продлевая ему свежесть."""
        cached = await self.get(telegram_id, "mail")
        if cached is None:
            return
        payload, _ = cached
        messages = payload.get("messages") or []
        kept = [message for message in messages if str(message.get("id")) != str(message_id)]
        if len(kept) != len(messages):
            payload["messages"] = kept
            await self._db.execute(
                "UPDATE cache_entries SET payload = ? WHERE telegram_id = ? AND section = 'mail'",
                (json.dumps(payload, ensure_ascii=False), telegram_id),
            )

    async def expire(self, telegram_id: int, section: str) -> None:
        """Считать сохранённое устаревшим: следующий запрос сходит на сервер,
        а при его сбое данные всё ещё будут."""
        await self._db.execute(
            "UPDATE cache_entries SET updated_at = '2000-01-01 00:00:00' WHERE telegram_id = ? AND section = ?",
            (telegram_id, section),
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


class CaldavRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def new_login(self, telegram_id: int) -> str:
        """Логин нового формата: номер аккаунта в приложении, без Telegram ID."""
        row = await self._db.fetch_one("SELECT account_no FROM users WHERE telegram_id=?", (telegram_id,))
        if not row or not row['account_no']:
            return f'ns{telegram_id}'
        login = f'cal-{row["account_no"]}'
        # Номерами могли поменяться (владельцу — 10001), а логин у прежнего
        # хозяина номера остался: тогда к номеру добавляем хвост.
        taken = await self._db.fetch_one("SELECT 1 FROM caldav_accounts WHERE login=? AND telegram_id<>?",
                                         (login, telegram_id))
        return f'{login}-{secrets.token_hex(2)}' if taken else login

    async def login(self, telegram_id: int) -> str:
        """Выданный логин (он же папка календаря), а без доступа — каким он станет."""
        row = await self.get(telegram_id)
        return row['login'] if row and row['login'] else await self.new_login(telegram_id)

    async def create(self, telegram_id: int) -> str:
        """Новый пароль. Логин — всегда нового формата: «ns<id>» остаётся
        только у тех, кто подключил календарь раньше и не пересоздавал доступ."""
        password = secrets.token_urlsafe(32)
        digest = hashlib.sha256(password.encode()).hexdigest()
        await self._db.execute(
            "INSERT INTO caldav_accounts (telegram_id, password_hash, login) VALUES (?, ?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET password_hash=excluded.password_hash, login=excluded.login, "
            "created_at=datetime('now')",
            (telegram_id, digest, await self.new_login(telegram_id)),
        )
        return password

    async def get(self, telegram_id: int) -> dict | None:
        row = await self._db.fetch_one("SELECT * FROM caldav_accounts WHERE telegram_id=?", (telegram_id,))
        return dict(row) if row else None

    async def authenticate(self, login: str, password: str) -> tuple[int, str] | None:
        """(telegram_id, логин) при верном пароле."""
        if not login or len(login) > 40 or not login.isascii():
            return None
        row = await self._db.fetch_one("SELECT telegram_id, password_hash FROM caldav_accounts WHERE login=?", (login,))
        digest = hashlib.sha256(password.encode()).hexdigest()
        if row and hmac.compare_digest(row['password_hash'], digest):
            return row['telegram_id'], login
        return None

    async def revoke(self, telegram_id: int) -> None:
        await self._db.execute("DELETE FROM caldav_accounts WHERE telegram_id=?", (telegram_id,))

    async def ids(self) -> list[int]:
        rows = await self._db.fetch_all("SELECT telegram_id FROM caldav_accounts ORDER BY telegram_id")
        return [row['telegram_id'] for row in rows]

    async def synced(self, telegram_id: int, error: str = '') -> None:
        if error:
            await self._db.execute("UPDATE caldav_accounts SET last_error=? WHERE telegram_id=?", (error, telegram_id))
        else:
            await self._db.execute("UPDATE caldav_accounts SET last_sync_at=datetime('now'), last_error=NULL WHERE telegram_id=?", (telegram_id,))


class SgoVersionRepository:
    """Последняя увиденная версия «Сетевого города» на каждом сервере."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, host: str) -> str:
        row = await self._db.fetch_one("SELECT version FROM sgo_versions WHERE host=?", (host,))
        return row["version"] if row else ""

    async def save(self, host: str, version: str, now: float) -> str:
        """Записать увиденную версию. Возвращает прежнюю ('' — сервер новый)."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute("SELECT version FROM sgo_versions WHERE host=?", (host,))
            row = await cursor.fetchone()
            previous = row[0] if row else ""
            if previous == version:
                await conn.execute("UPDATE sgo_versions SET seen_at=? WHERE host=?", (now, host))
            else:
                await conn.execute(
                    "INSERT INTO sgo_versions (host, version, previous, changed_at, seen_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(host) DO UPDATE SET "
                    "version=excluded.version, previous=excluded.previous, "
                    "changed_at=excluded.changed_at, seen_at=excluded.seen_at",
                    (host, version, previous, now, now),
                )
        return previous

    async def all(self) -> list[dict]:
        rows = await self._db.fetch_all("SELECT * FROM sgo_versions ORDER BY host")
        return [dict(row) for row in rows]


class SgoStatusRepository:
    """Страница статуса «Сетевого города»: свои проверки серверов, своя история
    версий и жалобы. С пользователями и проверками оценок не связано.

    Личных данных нет: адрес сервера, время, итог и обезличенные счётчики
    фоновых проверок аккаунтов. Клиент жалобы — хэш
    адреса, только чтобы ограничить частоту.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def add_probes(self, rows: list[tuple[str, float, bool, int, str, bool | None]]) -> None:
        await self._db.execute_many(
            "INSERT INTO sgo_probes (host, at, ok, latency_ms, error, mobile) VALUES (?, ?, ?, ?, ?, ?)",
            [(host, at, int(ok), latency, error, None if mobile is None else int(mobile))
             for host, at, ok, latency, error, mobile in rows],
        )

    async def recent_probes(self, since: float) -> list[dict]:
        """Проверки с `since`, новые первыми — для текущего состояния."""
        rows = await self._db.fetch_all(
            "SELECT host, at, ok, latency_ms, error, mobile FROM sgo_probes WHERE at >= ? ORDER BY at DESC",
            (since,),
        )
        return [dict(row) for row in rows]

    async def totals(self, since: float) -> dict[str, dict]:
        """По серверу: сколько проверок, сколько удачных, средний отклик удачных."""
        rows = await self._db.fetch_all(
            "SELECT host, COUNT(*) AS total, SUM(ok) AS good, "
            "AVG(CASE WHEN ok THEN latency_ms END) AS latency, "
            "COUNT(mobile) AS mobile_total, SUM(mobile) AS mobile_good "
            "FROM sgo_probes WHERE at >= ? GROUP BY host",
            (since,),
        )
        return {row["host"]: dict(row) for row in rows}

    async def buckets(self, since: float, size: float) -> list[dict]:
        """Проверки по отрезкам времени: (сервер, № отрезка, всего, удачных)."""
        rows = await self._db.fetch_all(
            "SELECT host, CAST((at - ?) / ? AS INTEGER) AS bucket, COUNT(*) AS total, SUM(ok) AS good "
            "FROM sgo_probes WHERE at >= ? GROUP BY host, bucket",
            (since, size, since),
        )
        return [dict(row) for row in rows]

    async def account_buckets(self, since: float, size: float) -> list[dict]:
        """Загрузки дневников по отрезкам времени (из sgo_account_checks): сервер,
        № отрезка, всего, удачных. Нужно, чтобы на полоске было видно, когда у
        людей не грузились дневники, даже если сайт отвечал на проверки."""
        rows = await self._db.fetch_all(
            "SELECT host, CAST((bucket - ?) / ? AS INTEGER) AS slot, "
            "SUM(total) AS total, SUM(good) AS good "
            "FROM sgo_account_checks WHERE bucket >= ? GROUP BY host, slot",
            (since, size, since),
        )
        return [dict(row) for row in rows]

    async def failures(self, since: float) -> list[dict]:
        """Неудачные проверки по порядку — из них складываются сбои."""
        rows = await self._db.fetch_all(
            "SELECT host, at, error, previous_ok, next_at, next_ok FROM ("
            "SELECT host, at, error, ok, "
            "LAG(ok) OVER (PARTITION BY host ORDER BY at) AS previous_ok, "
            "LEAD(at) OVER (PARTITION BY host ORDER BY at) AS next_at, "
            "LEAD(ok) OVER (PARTITION BY host ORDER BY at) AS next_ok "
            "FROM sgo_probes WHERE at >= ?) WHERE ok = 0 ORDER BY host, at",
            (since,),
        )
        return [dict(row) for row in rows]

    async def versions(self) -> dict[str, dict]:
        """Текущая версия каждого сервера и когда она появилась."""
        rows = await self._db.fetch_all(
            "SELECT host, version, previous, MAX(changed_at) AS changed_at FROM sgo_status_versions GROUP BY host"
        )
        return {row["host"]: dict(row) for row in rows}

    async def save_version(self, host: str, version: str, now: float) -> str:
        """Записать увиденную версию. Возвращает прежнюю ('' — сервер новый)."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "SELECT version FROM sgo_status_versions WHERE host = ? ORDER BY changed_at DESC LIMIT 1", (host,))
            row = await cursor.fetchone()
            previous = row[0] if row else ""
            if previous != version:
                await conn.execute(
                    "INSERT INTO sgo_status_versions (host, version, previous, changed_at) VALUES (?, ?, ?, ?)",
                    (host, version, previous, now),
                )
        return previous

    async def version_history(self, since: float, limit: int = 200) -> list[dict]:
        """Смены версий, новые первыми."""
        rows = await self._db.fetch_all(
            "SELECT host, version, previous, changed_at FROM sgo_status_versions "
            "WHERE changed_at >= ? ORDER BY changed_at DESC LIMIT ?",
            (since, limit),
        )
        return [dict(row) for row in rows]

    async def add_report(self, host: str, kind: str, comment: str, client: str, now: float) -> None:
        await self._db.execute(
            "INSERT INTO sgo_reports (host, kind, comment, client, created_at) VALUES (?, ?, ?, ?, ?)",
            (host, kind, comment, client, now),
        )

    async def reports(self, since: float) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT host, kind, comment, created_at FROM sgo_reports WHERE created_at >= ? "
            "ORDER BY created_at DESC",
            (since,),
        )
        return [dict(row) for row in rows]

    async def reports_from(self, client: str, since: float, host: str | None = None) -> int:
        if host is None:
            row = await self._db.fetch_one(
                "SELECT COUNT(*) FROM sgo_reports WHERE client = ? AND created_at >= ?", (client, since))
        else:
            row = await self._db.fetch_one(
                "SELECT COUNT(*) FROM sgo_reports WHERE client = ? AND host = ? AND created_at >= ?",
                (client, host, since))
        return int(row[0]) if row else 0

    async def reachable_hosts(self) -> set[str]:
        """Серверы, которые хоть раз ответили боту. Остальные с нашего
        сервера не открываются вовсе — о них нечего сказать."""
        rows = await self._db.fetch_all("SELECT DISTINCT host FROM sgo_probes WHERE ok = 1")
        return {row[0] for row in rows}

    async def latest_successes(self) -> dict[str, float]:
        rows = await self._db.fetch_all("SELECT host, MAX(at) AS at FROM sgo_probes WHERE ok = 1 GROUP BY host")
        return {row["host"]: row["at"] for row in rows}

    async def add_account_check(self, host: str, at: float, ok: bool) -> None:
        await self._db.execute(
            "INSERT INTO sgo_account_checks (host, bucket, good, total) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(host, bucket) DO UPDATE SET good = good + excluded.good, total = total + 1",
            (host, int(at // 300) * 300, int(ok)),
        )

    async def account_checks(self, since: float) -> dict[str, dict]:
        rows = await self._db.fetch_all(
            "SELECT host, SUM(good) AS good, SUM(total) AS total "
            "FROM sgo_account_checks WHERE bucket >= ? GROUP BY host", (since,),
        )
        return {row["host"]: {"good": row["good"], "total": row["total"]} for row in rows}

    async def host_probes(self, host: str, since: float) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT host, at, ok, latency_ms, error, mobile FROM sgo_probes "
            "WHERE host = ? AND at >= ? ORDER BY at DESC",
            (host, since),
        )
        return [dict(row) for row in rows]

    async def host_ever_ok(self, host: str) -> bool:
        row = await self._db.fetch_one("SELECT 1 FROM sgo_probes WHERE host = ? AND ok = 1 LIMIT 1", (host,))
        return row is not None

    async def host_check_history(self, host: str) -> dict:
        row = await self._db.fetch_one(
            "SELECT MAX(CASE WHEN ok = 1 THEN at END) AS last_ok_at, "
            "MIN(CASE WHEN ok = 0 AND at > COALESCE(("
            "SELECT MAX(at) FROM sgo_probes WHERE host = ? AND ok = 1), 0) THEN at END) AS down_since "
            "FROM sgo_probes WHERE host = ?", (host, host),
        )
        return dict(row)

    # Подписки из приложения (sgo_alerts).

    async def alert_kinds(self, telegram_id: int, host: str) -> set[str]:
        rows = await self._db.fetch_all(
            "SELECT kind FROM sgo_alerts WHERE telegram_id = ? AND host = ?", (telegram_id, host))
        return {row[0] for row in rows}

    async def alert_subscribe(self, telegram_id: int, host: str, url: str, kind: str,
                              version: str, now: float) -> None:
        await self._db.execute(
            "INSERT INTO sgo_alerts (telegram_id, host, url, kind, version, created_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(telegram_id, host, kind) DO UPDATE SET url = excluded.url, created_at = excluded.created_at",
            (telegram_id, host, url, kind, version, now),
        )

    async def alert_unsubscribe(self, telegram_id: int, host: str, kind: str) -> None:
        await self._db.execute(
            "DELETE FROM sgo_alerts WHERE telegram_id = ? AND host = ? AND kind = ?", (telegram_id, host, kind))

    async def alerts(self) -> list[dict]:
        rows = await self._db.fetch_all("SELECT * FROM sgo_alerts ORDER BY host, telegram_id")
        return [dict(row) for row in rows]

    async def alert_seen_version(self, telegram_id: int, host: str, version: str) -> None:
        await self._db.execute(
            "UPDATE sgo_alerts SET version = ? WHERE telegram_id = ? AND host = ? AND kind = 'updates'",
            (version, telegram_id, host),
        )

    async def alerts_expire(self, kind: str, before: float) -> None:
        await self._db.execute("DELETE FROM sgo_alerts WHERE kind = ? AND created_at < ?", (kind, before))

    async def prune(self, probes_before: float, reports_before: float) -> None:
        await self._db.execute("DELETE FROM sgo_probes WHERE at < ?", (probes_before,))
        await self._db.execute("DELETE FROM sgo_account_checks WHERE bucket < ?", (probes_before,))
        await self._db.execute("DELETE FROM sgo_reports WHERE created_at < ?", (reports_before,))


class ConditionRepository:
    """Условия упражнений со сканов, прочитанные моделью (solver/): общие для
    всех, кто учится по этой книге."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_many(self, book: str, numbers: list[int]) -> dict[int, str]:
        if not numbers:
            return {}
        marks = ",".join("?" * len(numbers))
        rows = await self._db.fetch_all(
            f"SELECT number, text FROM exercise_conditions WHERE book = ? AND number IN ({marks})", (book, *numbers))
        return {int(row["number"]): row["text"] for row in rows}

    async def put(self, book: str, number: int, text: str, now: float) -> None:
        await self._db.execute(
            "INSERT INTO exercise_conditions (book, number, text, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(book, number) DO UPDATE SET text = excluded.text, created_at = excluded.created_at",
            (book, number, text, now),
        )


class SolutionRepository:
    """Готовые решения домашних заданий — общие для класса (solver/)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> dict | None:
        row = await self._db.fetch_one(
            "SELECT text, model, created_at FROM homework_solutions WHERE key = ?", (key,))
        return dict(row) if row else None

    async def put(self, key: str, subject: str, due: str, model: str, text: str, now: float) -> None:
        await self._db.execute(
            "INSERT INTO homework_solutions (key, subject, due, model, text, created_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET model = excluded.model, text = excluded.text, created_at = excluded.created_at",
            (key, subject, due, model, text, now),
        )

    async def delete(self, key: str) -> None:
        await self._db.execute("DELETE FROM homework_solutions WHERE key = ?", (key,))

    async def prune(self, before: float) -> None:
        await self._db.execute("DELETE FROM homework_solutions WHERE created_at < ?", (before,))


class KeptAttachmentRepository:
    """Вложения заданий, скачанные заранее: что лежит на диске и кому его отдавать."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def saved(self, host: str, mode: str, attachment_id: int) -> dict | None:
        row = await self._db.fetch_one(
            "SELECT size, saved_at FROM kept_attachments WHERE host = ? AND mode = ? AND attachment_id = ?",
            (host, mode, attachment_id),
        )
        return dict(row) if row else None

    async def add(self, host: str, mode: str, attachment_id: int, size: int, now: float) -> None:
        await self._db.execute(
            "INSERT INTO kept_attachments (host, mode, attachment_id, size, saved_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(host, mode, attachment_id) DO UPDATE SET size = excluded.size, saved_at = excluded.saved_at",
            (host, mode, attachment_id, size, now),
        )

    async def grant(self, telegram_id: int, host: str, mode: str, ids: list[int]) -> None:
        await self._db.execute_many(
            "INSERT OR IGNORE INTO kept_attachment_access (telegram_id, host, mode, attachment_id) VALUES (?, ?, ?, ?)",
            [(telegram_id, host, mode, attachment_id) for attachment_id in ids],
        )

    async def allowed(self, telegram_id: int, host: str, mode: str, attachment_id: int) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 FROM kept_attachment_access WHERE telegram_id = ? AND host = ? AND mode = ? AND attachment_id = ?",
            (telegram_id, host, mode, attachment_id),
        )
        return row is not None

    async def total_size(self) -> int:
        row = await self._db.fetch_one("SELECT COALESCE(SUM(size), 0) FROM kept_attachments")
        return int(row[0]) if row else 0

    async def oldest(self, limit: int = 100) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT host, mode, attachment_id, size, saved_at FROM kept_attachments ORDER BY saved_at LIMIT ?", (limit,))
        return [dict(row) for row in rows]

    async def expired(self, before: float) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT host, mode, attachment_id FROM kept_attachments WHERE saved_at < ?", (before,))
        return [dict(row) for row in rows]

    async def remove(self, items: list[tuple[str, str, int]]) -> None:
        await self._db.execute_many(
            "DELETE FROM kept_attachments WHERE host = ? AND mode = ? AND attachment_id = ?", items)
        await self._db.execute_many(
            "DELETE FROM kept_attachment_access WHERE host = ? AND mode = ? AND attachment_id = ?", items)


class NetschoolAccountRepository:
    """Какой аккаунт «Сетевого города» у какого аккаунта приложения
    (netschool/ownership.py): по одной записи на аккаунт приложения."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def holders(self, school_url: str, ns_user_id: int) -> list[int]:
        """Аккаунты приложения с этим СГ — от подключившего раньше всех."""
        rows = await self._db.fetch_all(
            "SELECT telegram_id FROM netschool_accounts WHERE school_url = ? AND ns_user_id = ? "
            "ORDER BY created_at, rowid", (school_url, ns_user_id))
        return [row["telegram_id"] for row in rows]

    async def get(self, telegram_id: int) -> tuple[str, int] | None:
        row = await self._db.fetch_one(
            "SELECT school_url, ns_user_id FROM netschool_accounts WHERE telegram_id = ?", (telegram_id,))
        return (row["school_url"], row["ns_user_id"]) if row else None

    async def set(self, telegram_id: int, school_url: str, ns_user_id: int) -> None:
        """Запомнить СГ аккаунта; другой СГ того же аккаунта заменяется."""
        await self._db.execute(
            "INSERT INTO netschool_accounts (telegram_id, school_url, ns_user_id) VALUES (?, ?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET school_url = excluded.school_url, "
            "ns_user_id = excluded.ns_user_id, created_at = CASE WHEN "
            "netschool_accounts.school_url = excluded.school_url AND netschool_accounts.ns_user_id = excluded.ns_user_id "
            "THEN netschool_accounts.created_at ELSE excluded.created_at END",
            (telegram_id, school_url, ns_user_id),
        )

    async def release(self, telegram_id: int) -> None:
        await self._db.execute("DELETE FROM netschool_accounts WHERE telegram_id = ?", (telegram_id,))


class AppFlagRepository:
    """Переключатели на весь бот (app_flags): бета-возможности и т. п."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> bool:
        row = await self._db.fetch_one("SELECT value FROM app_flags WHERE key = ?", (key,))
        return bool(row) and row["value"] == "1"

    async def set(self, key: str, on: bool) -> None:
        await self._db.execute(
            "INSERT INTO app_flags (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, "1" if on else "0"),
        )


class BroadcastGroupRepository:
    """Группы и каналы, куда бот пишет о простоях и обновлениях «Сетевого
    города» (status/groups.py). Здесь же — какие серверы уже объявлены
    лежащими: после перезапуска не повторять «не отвечает»."""

    DOWN_KEY = "group_down:"

    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _row(row: aiosqlite.Row) -> dict:
        item = dict(row)
        item["outages"] = bool(item["outages"])
        item["updates"] = bool(item["updates"])
        try:
            regions = json.loads(item["regions"] or "[]")
        except ValueError:
            regions = []
        item["regions"] = [str(host) for host in regions] if isinstance(regions, list) else []
        return item

    async def all(self) -> list[dict]:
        rows = await self._db.fetch_all("SELECT * FROM broadcast_groups ORDER BY added_at")
        return [self._row(row) for row in rows]

    async def get(self, chat_id: int) -> dict | None:
        row = await self._db.fetch_one("SELECT * FROM broadcast_groups WHERE chat_id = ?", (chat_id,))
        return self._row(row) if row else None

    async def add(self, chat_id: int, title: str, now: float) -> bool:
        """Подключить группу (оба вида сообщений, все регионы). False — уже была;
        тогда обновляется только название."""
        if await self.get(chat_id) is not None:
            await self._db.execute("UPDATE broadcast_groups SET title = ? WHERE chat_id = ?", (title, chat_id))
            return False
        await self._db.execute(
            "INSERT INTO broadcast_groups (chat_id, title, added_at) VALUES (?, ?, ?)", (chat_id, title, now))
        return True

    async def remove(self, chat_id: int) -> None:
        await self._db.execute("DELETE FROM broadcast_groups WHERE chat_id = ?", (chat_id,))

    async def set_kind(self, chat_id: int, kind: str, on: bool) -> None:
        if kind not in ("outages", "updates"):
            raise ValueError(kind)
        await self._db.execute(f"UPDATE broadcast_groups SET {kind} = ? WHERE chat_id = ?", (int(on), chat_id))

    async def set_regions(self, chat_id: int, hosts: list[str]) -> None:
        await self._db.execute("UPDATE broadcast_groups SET regions = ? WHERE chat_id = ?",
                               (json.dumps(sorted(set(hosts))), chat_id))

    async def move(self, old_id: int, new_id: int) -> None:
        """Группа стала супергруппой — у неё новый id."""
        await self._db.execute("UPDATE broadcast_groups SET chat_id = ? WHERE chat_id = ?", (new_id, old_id))

    async def down_hosts(self) -> dict[str, float]:
        rows = await self._db.fetch_all(
            "SELECT key, value FROM app_metadata WHERE key LIKE ?", (self.DOWN_KEY + "%",))
        down = {}
        for row in rows:
            try:
                down[row["key"][len(self.DOWN_KEY):]] = float(row["value"])
            except ValueError:
                continue
        return down

    async def set_down(self, host: str, since: float | None) -> None:
        if since is None:
            await self._db.execute("DELETE FROM app_metadata WHERE key = ?", (self.DOWN_KEY + host,))
        else:
            await self._db.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
                                   (self.DOWN_KEY + host, repr(since)))
