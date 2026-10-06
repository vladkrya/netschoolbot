"""Перенос данных из старого проекта в SQLite.

Из старой версии переносится всё, что нельзя восстановить: пользователи с
настройками, сохранённые сессии «Сетевого города», постоянные ссылки на
мини-приложение и прочитанные письма.

Состояние слежения за оценками переносится иначе. Старый ключ оценки —
поля, склеенные через «_», причём без номера урока; новый считается по
другим правилам, и совпасть они не могут. Поэтому перенесённым
пользователям ставится флаг `baseline_pending`: их первая проверка пройдёт
молча и пересоберёт состояние. Иначе человек получил бы весь журнал за год
одним залпом — ровно то, ради чего в старом коде существовал
`_known_grades_initialized`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
from .repositories import MarkStateRepository, MiniappRepository, SessionRepository, UserRepository

logger = logging.getLogger("netschoolbot.import")


@dataclass(slots=True)
class ImportReport:
    users: int = 0
    sessions: int = 0
    tokens: int = 0
    mail_ids: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        lines = [
            f"Пользователей перенесено: {self.users}",
            f"Сессий «Сетевого города»: {self.sessions}",
            f"Ссылок на мини-приложение: {self.tokens}",
            f"Отметок о прочитанных письмах: {self.mail_ids}",
        ]
        if self.skipped:
            lines.append("")
            lines.append("Пропущено:")
            lines.extend(f"  • {item}" for item in self.skipped)
        return "\n".join(lines)


def _read_json(path: Path, default: Any) -> Any:
    """Прочитать JSON, не роняя перенос на одном битом файле."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("Не удалось прочитать %s: %s", path, exc)
        return default


def _parse_time(value: Any) -> dt.time | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.strptime(text, "%H:%M").time()
    except ValueError:
        logger.warning("Некорректные тихие часы: %r", value)
        return None


def user_from_legacy(telegram_id: int, data: dict[str, Any]) -> User:
    """Собрать пользователя из старой записи.

    Все поля читаются терпимо к отсутствию: в старом файле схема
    достраивалась по ходу, и записи разных лет выглядят по-разному.
    """
    quiet = data.get("quiet_hours") or {}
    filters = data.get("filters") or {}
    subject_filters = data.get("subject_filters") or {}

    students: list[Student] = []
    for item in data.get("available_students") or []:
        if not isinstance(item, dict):
            continue
        try:
            students.append(Student(id=int(item["id"]), name=str(item.get("name") or "")))
        except (KeyError, TypeError, ValueError):
            continue

    return User(
        telegram_id=telegram_id,
        api_mode=ApiMode.WEB,  # Сохранённые сессии старого проекта — веб-сессии.
        enabled=bool(data.get("enabled")),
        display_name=str(data.get("display_name") or ""),
        school=School(
            url=str(data.get("netschool_url") or "").strip(),
            name=str(data.get("netschool_school") or "").strip(),
        ),
        credentials=Credentials(
            login_type=LoginType.parse(data.get("login_type")),
            login=str(data.get("login") or ""),
            password=str(data.get("password") or ""),
        ),
        student_name=str(data.get("student_name") or ""),
        selected_student_id=_safe_int(data.get("selected_student_id")),
        available_students=tuple(students),
        check_interval=clamp_interval(_safe_int(data.get("check_interval")) or 300),
        notifications=NotificationPrefs(
            # Отдельного «уведомлять о новых оценках» в старой схеме не было:
            # им управлял общий флаг enabled.
            grades=True,
            changes=bool(data.get("notify_changes", True)),
            deletes=bool(data.get("notify_deletes", True)),
            homework=bool(data.get("notify_homework", True)),
            mail=bool(data.get("notify_mail", False)),
            weekly_summary=bool(data.get("weekly_summary_enabled", False)),
        ),
        filters=Filters(
            exclude_titles=frozenset(
                normalize(v) for v in (filters.get("exclude") or []) if str(v).strip()
            ),
            include_subjects=frozenset(
                normalize(v) for v in (subject_filters.get("include") or []) if str(v).strip()
            ),
        ),
        quiet_hours=QuietHours(
            start=_parse_time(quiet.get("start")), end=_parse_time(quiet.get("end"))
        ),
    )


class LegacyImporter:
    def __init__(
        self,
        source: Path,
        *,
        users: UserRepository,
        state: MarkStateRepository,
        sessions: SessionRepository,
        miniapp: MiniappRepository,
    ) -> None:
        self._source = source
        self._users = users
        self._state = state
        self._sessions = sessions
        self._miniapp = miniapp

    async def run(self) -> ImportReport:
        report = ImportReport()
        imported_ids = await self._import_users(report)
        await self._import_sessions(imported_ids, report)
        await self._import_tokens(imported_ids, report)
        await self._import_mail(imported_ids, report)
        return report

    async def _import_users(self, report: ImportReport) -> set[int]:
        path = self._source / "netschool_users" / "netschool_users.json"
        if not path.exists():
            # Старый макет держал файл прямо в корне каталога данных.
            path = self._source / "netschool_users.json"
        payload = _read_json(path, {})
        raw_users = payload.get("users") if isinstance(payload, dict) else None
        if not isinstance(raw_users, dict):
            report.skipped.append(f"Файл пользователей не найден или пуст: {path}")
            return set()

        imported: set[int] = set()
        for key, data in raw_users.items():
            telegram_id = _safe_int(key)
            if telegram_id is None or not isinstance(data, dict):
                report.skipped.append(f"Некорректная запись пользователя: {key!r}")
                continue
            user = user_from_legacy(telegram_id, data)
            await self._users.save(user)
            # Ключи оценок несовместимы — первая проверка пройдёт молча.
            await self._state.mark_baseline_pending(telegram_id, True)
            imported.add(telegram_id)
            report.users += 1
        return imported

    async def _import_sessions(self, known: set[int], report: ImportReport) -> None:
        directory = self._source / "netschool_sessions"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("session_*.json")):
            telegram_id = _safe_int(path.stem.removeprefix("session_"))
            if telegram_id is None or telegram_id not in known:
                continue
            try:
                payload = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                report.skipped.append(f"Сессия {path.name}: {exc}")
                continue
            if payload:
                await self._sessions.save(telegram_id, payload)
                report.sessions += 1

    async def _import_tokens(self, known: set[int], report: ImportReport) -> None:
        payload = _read_json(self._source / "netschool_users" / "miniapp_tokens.json", {})
        tokens = payload.get("tokens") if isinstance(payload, dict) else None
        if not isinstance(tokens, dict):
            return
        # Токены переносим только действующие: срок жизни у старых постоянных
        # ссылок — год, и протухшие незачем тянуть в новую базу.
        now = dt.datetime.now().timestamp()
        for token, entry in tokens.items():
            if not isinstance(entry, dict):
                continue
            telegram_id = _safe_int(entry.get("user_id"))
            if telegram_id is None or telegram_id not in known:
                continue
            expires_at = _safe_int(entry.get("expires_at")) or 0
            if expires_at and expires_at <= now:
                continue
            await self._miniapp.import_token(token, telegram_id)
            report.tokens += 1

    async def _import_mail(self, known: set[int], report: ImportReport) -> None:
        path = self._source / "netschool_users" / "netschool_users.json"
        payload = _read_json(path, {})
        raw_users = payload.get("users") if isinstance(payload, dict) else {}
        if not isinstance(raw_users, dict):
            return
        for key, data in raw_users.items():
            telegram_id = _safe_int(key)
            if telegram_id is None or telegram_id not in known or not isinstance(data, dict):
                continue
            ids = {
                value
                for value in (_safe_int(v) for v in (data.get("mail_seen_ids") or []))
                if value is not None
            }
            if ids:
                await self._state.mark_mail_seen(telegram_id, ids)
                report.mail_ids += len(ids)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
