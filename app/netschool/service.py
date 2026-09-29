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
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, TypeVar

from ..db.repositories import UserRepository
from ..domain.models import ApiMode, Student, User
from ..domain.records import DiaryDay, HomeworkRecord, MarkRecord, RecentEvents
from .errors import NetSchoolError, Reason, wrap
from .mapping import (
    diary_days,
    find_by_name,
    quarter_key_of_term,
    recent_events_from,
    students_from_diary_init,
    subject_stats_from,
    totals_from_mobile,
)
from .session import SessionPool

logger = logging.getLogger("netschoolbot.netschool")

T = TypeVar("T")

# Насколько назад и вперёд смотрит регулярная проверка. Пять недель назад —
# чтобы поймать оценку, выставленную задним числом.
DEFAULT_WEEKS_BACK = 5
DEFAULT_WEEKS_FORWARD = 5

# Окно ленты событий мобильного API. Приложение шлёт 14 и 1; берём 14: оценка,
# выставленная задним числом, попадает в ленту по дате выставления, а запас
# по дням не даёт потерять её между проверками.
RECENT_EVENTS_DAYS = 14

# За сколько дней до конца мобильного токена его пора продлевать или
# предупреждать пользователя. Токен живёт 60 дней.
TOKEN_WARN_MARGIN = dt.timedelta(days=3)

RETRY_ATTEMPTS = 3
RETRY_INITIAL_DELAY = 1.5
RETRY_MAX_DELAY = 6.0


class TokenState(str, Enum):
    OK = "ok"  # до конца срока далеко
    RENEWED = "renewed"  # был на исходе, продлён через refresh_token
    EXPIRING = "expiring"  # на исходе, продлить не удалось — нужен вход заново


@dataclass(frozen=True, slots=True)
class TokenCheck:
    state: TokenState
    expires_at: dt.datetime  # UTC


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

    async def fetch_recent_events(
        self, user: User, *, period_days: int = RECENT_EVENTS_DAYS
    ) -> RecentEvents | None:
        """Лента свежих оценок и заданий — только в мобильном режиме.

        `None` означает «ленты нет, действуйте прежним путём»: веб-режим,
        клиент без `period_events` или сбой запроса. Ошибки, требующие нового
        входа, пробрасываются — их обрабатывает вызывающий код так же, как
        при запросе дневника.
        """
        if user.effective_api_mode is not ApiMode.MOBILE:
            return None

        async def call(client: Any) -> Any:
            if not hasattr(client, "period_events"):
                return None
            return await client.period_events(period_days=period_days)

        payload = await self._mobile_only(user, call, "Лента событий")
        if payload is None:
            return None
        return recent_events_from(payload)

    async def fetch_totals(self, user: User) -> dict[str, dict[str, Any]] | None:
        """Четвертные и годовые оценки по предметам одним запросом — только мобильный режим.

        `None` — данных нет (веб-режим или сбой), экран остаётся на расчётных
        средних. Форма ответа — у `mapping.totals_from_mobile`.
        """
        if user.effective_api_mode is not ApiMode.MOBILE:
            return None

        async def call(client: Any) -> tuple[Any, Any] | None:
            if not hasattr(client, "totals"):
                return None
            return await client.totals(), await client.subjects_raw()

        fetched = await self._mobile_only(user, call, "Итоговые оценки")
        if fetched is None:
            return None
        rows, subjects = fetched
        names = {
            item["id"]: str(item.get("name") or "")
            for item in subjects or []
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        }
        return totals_from_mobile(rows, names)

    async def fetch_subject_stats(
        self, user: User, subject: str, quarter: str
    ) -> dict[str, Any] | None:
        """Аналитика по предмету за четверть: средний балл класса и распределение оценок.

        Только мобильный режим. `None` — предмета или четверти нет в мобильных
        `subjects`/`terms`, данных нет или запрос не удался.
        """
        if user.effective_api_mode is not ApiMode.MOBILE:
            return None

        async def call(client: Any) -> Any:
            if not hasattr(client, "subject_performance"):
                return None
            found = find_by_name(await client.subjects_raw(), subject)
            term = next(
                (t for t in await client.terms() if quarter_key_of_term(t.get("name")) == quarter),
                None,
            )
            if found is None or term is None:
                return None
            return await client.subject_performance(found["id"], term["id"])

        payload = await self._mobile_only(user, call, "Аналитика по предмету")
        return subject_stats_from(payload) if payload is not None else None

    async def _mobile_only(
        self, user: User, call: Callable[[Any], Awaitable[T]], what: str
    ) -> T | None:
        """Необязательный мобильный запрос: сбой означает «данных нет», а не ошибку экрана."""
        try:
            return await self._with_session(user, call)
        except NetSchoolError as exc:
            if exc.reason.needs_relogin:
                raise
            logger.info("%s недоступны для %s: %s", what, user.telegram_id, exc.reason)
            return None

    async def check_mobile_token(
        self, user: User, *, margin: dt.timedelta = TOKEN_WARN_MARGIN
    ) -> TokenCheck | None:
        """Проверить срок мобильного токена и продлить его, если он на исходе.

        `None` — проверять нечего: веб-режим или клиент без токена. Продление
        через refresh_token на живом сервере не проверялось, поэтому любой
        сбой (и «продлили, а срок не вырос») означает `EXPIRING`, а не ошибку:
        пользователю нужно войти заново. Учтите: продление может
        аннулировать refresh-токен приложения NetSchool на телефоне
        пользователя — там придётся войти заново.
        """
        if user.effective_api_mode is not ApiMode.MOBILE:
            return None

        async def call(client: Any) -> TokenCheck | None:
            token = getattr(client, "token", None)
            if token is None or not hasattr(client, "refresh"):
                return None
            if not token.expires_soon(margin):
                return TokenCheck(TokenState.OK, token.expires_at)
            try:
                renewed = await client.refresh()
            except Exception as exc:  # noqa: BLE001 — любой сбой означает «не продлили»
                logger.info("Токен %s не продлён: %s", user.telegram_id, exc)
                return TokenCheck(TokenState.EXPIRING, token.expires_at)
            if renewed.expires_soon(margin):
                # Сервер ответил, но срок не вырос — продлением это не назвать.
                logger.info("Токен %s: продление не увеличило срок", user.telegram_id)
                return TokenCheck(TokenState.EXPIRING, renewed.expires_at)
            logger.info("Токен %s продлён до %s", user.telegram_id, renewed.expires_at)
            return TokenCheck(TokenState.RENEWED, renewed.expires_at)

        return await self._mobile_only(user, call, "Срок токена")

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
            if entries and all(getattr(item, "read", None) is not None for item in entries):
                # Мобильное API отдаёт флаг прочтения прямо в списке.
                return messages
            # В веб-списке писем флага прочтения нет — без этого запроса
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

    async def fetch_teachers(self, user: User, wanted: dict[str, int]) -> dict[str, str]:
        """Учителя по предметам: {предмет: id любого задания} → {предмет: ФИО}.

        В дневнике учителя нет — только в карточке задания. Одного задания на
        предмет достаточно, поэтому вызывающий код кэширует ответ надолго.
        """

        async def call(client: Any) -> dict[str, str]:
            result: dict[str, str] = {}
            for subject, assignment_id in wanted.items():
                try:
                    details = await client.assignment_details(assignment_id)
                    name = details.teacher
                except Exception as exc:
                    logger.debug("Учитель для %s не получен: %s", subject, exc)
                    continue
                if name:
                    result[subject] = name
            return result

        return await self._with_session(user, call)

    async def fetch_recipients(self, user: User) -> list[dict[str, str]]:
        """Кому можно написать: учителя и администрация школы."""

        async def call(client: Any) -> list[dict[str, str]]:
            items = await client.mail_recipients()
            return [
                {
                    "id": str(getattr(item, "id", "")),
                    "name": str(getattr(item, "name", "") or ""),
                    "organization": str(getattr(item, "organization_name", "") or ""),
                }
                for item in items
                if getattr(item, "id", "")
            ]

        return await self._with_session(user, call)

    async def send_mail(self, user: User, to: list[str], subject: str, text: str) -> None:
        # Без _with_session: при сбое он повторяет запрос, а повтор отправки
        # мог бы послать письмо дважды.
        async with self._pool.acquire(user) as client:
            try:
                await client.mail_send(subject, text, to)
            except Exception as exc:
                raise wrap(exc, context=f"письмо от {user.telegram_id}") from exc

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

    @property
    def mobile_available(self) -> bool:
        return self._pool.mobile_available

    async def switch_api(self, user: User, mode: ApiMode) -> User:
        """Сменить API пользователя и сохранить выбор. Возвращает обновлённого пользователя."""
        if user.api_mode is mode:
            return user
        try:
            await self._pool.switch_api(user, mode)
        except NetSchoolError:
            raise
        except Exception as exc:
            raise wrap(exc, context=f"смена API {user.telegram_id}") from exc
        return await self._users.save(replace(user, api_mode=mode))

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
    if hasattr(client, "diary_init"):
        # netschoolpy 9: общий метод у веб- и мобильного клиента.
        return await client.diary_init()
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
