"""Сервис дневника — единственная точка, через которую и бот, и мини-приложение
получают данные из «Сетевого города».

Раньше эти же операции существовали в двух вариантах: `netschool/client.py`
для бота и набор приватных функций в `web/miniapp.py` для PWA. Здесь они одни,
и повтор при временных ошибках тоже один — вместо двух разных политик.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, TypeVar

from ..db.repositories import UserRepository
from ..domain.models import Student, User
from ..domain.records import DiaryDay, HomeworkRecord, MarkRecord
from .errors import NetSchoolError, Reason, wrap
from .mapping import diary_days, students_from_diary_init
from .session import SessionPool

logger = logging.getLogger("netschoolbot.netschool")

T = TypeVar("T")

# Насколько назад и вперёд смотрит регулярная проверка. Пять недель назад —
# чтобы поймать оценку, выставленную задним числом.
DEFAULT_WEEKS_BACK = 5
DEFAULT_WEEKS_FORWARD = 5

RETRY_ATTEMPTS = 3
RETRY_INITIAL_DELAY = 1.5
RETRY_MAX_DELAY = 6.0


class DiaryService:
    def __init__(self, pool: SessionPool, users: UserRepository) -> None:
        self._pool = pool
        self._users = users

    async def fetch_diary(
        self,
        user: User,
        *,
        weeks_back: int = DEFAULT_WEEKS_BACK,
        weeks_forward: int = DEFAULT_WEEKS_FORWARD,
        today: dt.date | None = None,
    ) -> list[DiaryDay]:
        """Дневник за диапазон недель вокруг сегодняшнего дня."""
        today = today or msk_today()
        start = _monday_of(today - dt.timedelta(weeks=weeks_back))
        end = today + dt.timedelta(weeks=weeks_forward)
        return await self.fetch_period(user, start, end, today=today)

    async def fetch_period(
        self, user: User, start: dt.date, end: dt.date, *, today: dt.date | None = None
    ) -> list[DiaryDay]:
        """Дневник за произвольный период. Запрашивается по неделям."""
        today = today or msk_today()
        raw_days = await self._with_session(
            user, lambda client: _collect_weeks(client, start, end, today=today)
        )
        return diary_days(raw_days, today=today)

    async def fetch_marks(self, user: User, **kwargs) -> list[MarkRecord]:
        days = await self.fetch_diary(user, **kwargs)
        return [record for day in days for record in day.marks]

    async def fetch_homework(self, user: User, **kwargs) -> list[HomeworkRecord]:
        days = await self.fetch_diary(user, **kwargs)
        return [record for day in days for record in day.homework]

    async def marks_present_in_week(self, user: User, week_day: dt.date) -> set[str]:
        """Свободные ключи работ с оценками за неделю, содержащую `week_day`.

        Используется для подтверждения удаления: сверяется `loose_identity`,
        потому что при пересборке ответа сервер может отдать работы в другом
        порядке, и строгий ключ разойдётся, хотя оценка на месте.
        """
        monday = _monday_of(week_day)
        raw_days = await self._with_session(
            user, lambda client: _collect_weeks(client, monday, monday, today=msk_today())
        )
        present: set[str] = set()
        for day in diary_days(raw_days):
            for record in day.marks:
                present.add(record.loose_identity)
        return present

    async def sync_students(self, user: User) -> User:
        """Обновить список детей и вернуть пользователя с актуальными данными.

        Возвращает новый объект, а не правит переданный: изменяемое состояние,
        которое молча переписывают вглубь стека, было источником сюрпризов
        в старом коде.
        """
        payload = await self._with_session(user, _fetch_diary_init)
        raw_students, current_id = students_from_diary_init(payload)
        students = tuple(Student(id=s["id"], name=s["name"]) for s in raw_students)

        available_ids = {s.id for s in students}
        selected = (
            user.selected_student_id
            if user.selected_student_id in available_ids
            else current_id
        )
        name = next((s.name for s in students if s.id == selected), user.student_name)

        updated = replace(
            user,
            available_students=students,
            selected_student_id=selected,
            student_name=name or user.student_name,
        )
        if (
            updated.available_students != user.available_students
            or updated.selected_student_id != user.selected_student_id
            or updated.student_name != user.student_name
        ):
            updated = await self._users.save(updated)
        return updated

    async def fetch_mail(self, user: User, limit: int = 20) -> list[dict[str, Any]]:
        """Письма школьной почты."""

        async def call(client: Any) -> list[dict[str, Any]]:
            page = await client.mail_list(page=1, page_size=limit)
            entries = getattr(page, "entries", None)
            if entries is None:
                entries = page if isinstance(page, list) else []
            messages = [_mail_to_dict(item) for item in entries]
            # В списке писем netschoolpy флага прочтения нет — без этого запроса
            # каждое письмо выглядело непрочитанным.
            try:
                unread = {int(i) for i in await client.mail_unread()}
            except Exception as exc:
                logger.debug("Не удалось получить непрочитанные письма: %s", exc)
            else:
                for message in messages:
                    message["read"] = _safe_int(message["id"]) not in unread
            return messages

        return await self._with_session(user, call)

    async def fetch_announcements(self, user: User, limit: int = 30) -> list[dict[str, Any]]:
        """Объявления школы — экран под колокольчиком."""

        async def call(client: Any) -> list[dict[str, Any]]:
            items = await client.announcements(take=limit)
            return [_announcement_to_dict(item) for item in items]

        return await self._with_session(user, call)

    async def fetch_mail_message(self, user: User, message_id: int) -> dict[str, Any]:
        """Одно письмо с текстом и вложениями."""

        async def call(client: Any) -> dict[str, Any]:
            message = await client.mail_read(message_id)
            return _message_to_dict(message)

        return await self._with_session(user, call)

    async def download_attachment(self, user: User, attachment_id: int) -> bytes:
        return await self._with_session(
            user, lambda client: client.download_attachment_as_bytes(attachment_id)
        )

    async def logout(self, user: User) -> None:
        await self._pool.invalidate(user.telegram_id, forget_saved=True)

    async def _with_session(self, user: User, call: Callable[[Any], Awaitable[T]]) -> T:
        """Выполнить операцию, повторяя её только при временных ошибках.

        Единая политика повтора вместо двух разных: бот раньше не повторял
        вовсе, а мини-приложение повторяло что угодно, включая ошибки, где
        повтор бессмыслен.
        """
        delay = RETRY_INITIAL_DELAY
        last: NetSchoolError | None = None

        for attempt in range(RETRY_ATTEMPTS):
            try:
                async with self._pool.acquire(user) as client:
                    return await call(client)
            except NetSchoolError as exc:
                last = exc
            except Exception as exc:
                last = wrap(exc, context=f"пользователь {user.telegram_id}")

            if last.reason.needs_relogin:
                # Сессия протухла: сбрасываем клиент и даём одну попытку войти
                # заново. Сохранённую сессию не стираем — пул сам проверит её
                # при восстановлении и выбросит, только если она мертва. Иначе
                # случайный 403 на одном запросе (закрытая для ученика почта)
                # стоил пользователю Госуслуг нового входа с кодом.
                await self._pool.invalidate(user.telegram_id)
                if attempt == 0:
                    continue
                raise last
            if not last.reason.is_retryable or attempt == RETRY_ATTEMPTS - 1:
                raise last

            await asyncio.sleep(delay)
            delay = min(delay * 2, RETRY_MAX_DELAY)

        assert last is not None
        raise last


async def _collect_weeks(
    client: Any, start: dt.date, end: dt.date, *, today: dt.date
) -> list[Any]:
    """Пройти дневник понедельными запросами.

    Некоторые школы отвечают 409 на будущие недели — на этом цикл вперёд
    прекращается, иначе логи забиваются одинаковыми ошибками. «Сегодня»
    передаётся снаружи: считать его здесь означало бы, что поведение
    функции нельзя проверить тестом.
    """
    days: list[Any] = []
    seen: set[dt.date] = set()
    current = _monday_of(start)

    while current <= end:
        try:
            diary = await client.diary(start=current)
        except Exception as exc:
            if current > today and "409" in str(exc):
                logger.debug("Будущие недели закончились на %s", current)
                break
            # Одна недоступная неделя не должна лишать данных за остальные.
            logger.warning("Не удалось получить неделю с %s: %s", current, exc)
            current += dt.timedelta(days=7)
            continue

        for day in getattr(diary, "schedule", None) or []:
            day_date = getattr(day, "day", None)
            if day_date is not None and day_date not in seen:
                seen.add(day_date)
                days.append(day)
        current += dt.timedelta(days=7)

    return days


async def _fetch_diary_init(client: Any) -> dict[str, Any]:
    response = await client._authed_get("student/diary/init")
    return response.json()


def _message_to_dict(message: Any) -> dict[str, Any]:
    """Письмо целиком. Форма ответа та же, что у списка, плюс текст."""

    def field(name: str, default: Any = "") -> Any:
        if isinstance(message, dict):
            return message.get(name, default)
        return getattr(message, name, default)

    sent = field("sent")
    attachments = []
    for item in field("file_attachments", []) or []:
        att_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
        attachments.append({"id": att_id, "name": str(name or f"file_{att_id}")})

    return {
        "id": field("id"),
        "subject": str(field("subject") or "Без темы"),
        "author": str(field("author_name") or ""),
        "text": str(field("text") or ""),
        "sent": sent.isoformat() if hasattr(sent, "isoformat") else str(sent or ""),
        "read": bool(field("is_read", False)),
        "attachments": attachments,
    }


def _announcement_to_dict(item: Any) -> dict[str, Any]:
    author = getattr(item, "author", None)
    posted = getattr(item, "post_date", None)
    return {
        "title": str(getattr(item, "name", "") or "Объявление"),
        "text": str(getattr(item, "content", "") or ""),
        "author": str(getattr(author, "full_name", "") or getattr(author, "nickname", "") or ""),
        "posted": posted.isoformat() if hasattr(posted, "isoformat") else str(posted or ""),
        "attachments": [
            {"id": a.id, "name": a.name} for a in getattr(item, "attachments", None) or []
        ],
    }


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mail_to_dict(message: Any) -> dict[str, Any]:
    def field(name: str, default: Any = "") -> Any:
        if isinstance(message, dict):
            return message.get(name, default)
        return getattr(message, name, default)

    sent = field("sent") or field("date")
    return {
        "id": field("id"),
        "subject": str(field("subject") or "Без темы"),
        "author": str(field("author") or field("from") or ""),
        "sent": sent.isoformat() if hasattr(sent, "isoformat") else str(sent or ""),
        "read": bool(field("read", False)),
    }


def msk_tz() -> dt.timezone:
    """Часовой пояс школьного расписания."""
    return dt.timezone(dt.timedelta(hours=3))


def msk_today() -> dt.date:
    return dt.datetime.now(msk_tz()).date()


def msk_now() -> dt.datetime:
    return dt.datetime.now(msk_tz())


def _monday_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())
