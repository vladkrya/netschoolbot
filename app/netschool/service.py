"""Сервис дневника — единственная точка, через которую и бот, и мини-приложение
получают данные из «Сетевого города».

Раньше эти же операции существовали в двух вариантах: `netschool/client.py`
для бота и набор приватных функций в `web/miniapp.py` для PWA. Здесь они одни,
и повтор при временных ошибках тоже один — вместо двух разных политик.
"""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import html
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, TypeVar

from ..db.repositories import UserRepository
from ..domain.models import ApiMode, Student, User
from ..domain.records import DiaryDay, HomeworkRecord, MarkRecord, RecentEvents, clean_content
from .errors import NetSchoolError, Reason, wrap
from .mapping import (
    answer_from_mobile,
    diary_days,
    event_from_mobile,
    link_from_join,
    reference_type_from_mobile,
    find_by_name,
    quarter_key_of_term,
    recent_events_from,
    students_from_diary_init,
    subject_stats_from,
    totals_from_mobile,
)
from . import myschool
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

# Вес оценки из карточки задания (см. `_fill_weights`): сколько помнить
# узнанный вес и неудачную попытку, и сколько карточек спрашивать разом.
WEIGHT_TTL = 24 * 3600
WEIGHT_FAILED_TTL = 3600
WEIGHT_CONCURRENCY = 4

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
        if user.api_mode is ApiMode.MYSCHOOL:
            return await self._run(user, lambda client: myschool.fetch_days(client, user, start, end))
        raw_days = await self._with_session(
            user, lambda client: _collect_weeks_weighted(client, start, end, today=today)
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

    async def fetch_school_years(self, user: User) -> list[dict[str, Any]] | None:
        """История обучения выбранного ученика с настоящими названиями классов."""
        if user.effective_api_mode is not ApiMode.MOBILE:
            return None

        async def call(client: Any):
            return await client.education() if hasattr(client, "education") else None

        rows = await self._mobile_only(user, call, "Годы обучения")
        if not isinstance(rows, list):
            return None
        result = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            year = row.get("schoolyear") or {}
            if not isinstance(year.get("id"), int) or year["id"] <= 0:
                continue
            result.append({
                "id": year["id"], "name": str(year.get("name") or ""),
                "class_name": str((row.get("class") or {}).get("name") or ""),
                "start": str(year.get("startDate") or "")[:10],
                "end": str(year.get("endDate") or "")[:10],
                "current": index == 0,
            })
        return result

    async def fetch_totals(self, user: User, *, school_year_id: int | None = None) -> dict[str, dict[str, Any]] | None:
        """Четвертные и годовые оценки по предметам одним запросом — только мобильный режим.

        `None` — данных нет (веб-режим или сбой), экран остаётся на расчётных
        средних. Форма ответа — у `mapping.totals_from_mobile`.
        """
        if user.effective_api_mode is not ApiMode.MOBILE:
            return None

        async def call(client: Any) -> tuple[Any, Any] | None:
            if not hasattr(client, "totals"):
                return None
            kwargs = {"school_year_id": school_year_id} if school_year_id is not None else {}
            return await client.totals(**kwargs), await client.subjects_raw(**kwargs)

        fetched = await self._mobile_only(user, call, "Итоговые оценки")
        if fetched is None:
            return None
        rows, subjects = fetched
        from netschoolpy import is_elective

        elective_ids = {item["id"] for item in subjects or []
                        if isinstance(item, dict) and "id" in item and is_elective(item)}
        rows = [item for item in rows or []
                if not isinstance(item, dict) or
                (not is_elective(item) and item.get("subjectId") not in elective_ids)]
        names = {
            item["id"]: str(item.get("name") or "")
            for item in subjects or []
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        }
        return totals_from_mobile(rows, names)

    async def fetch_terms(self, user: User, *, school_year_id: int | None = None) -> list[dict[str, Any]] | None:
        """Учебные периоды школы с датами (`/terms`). Только мобильный режим."""
        if user.effective_api_mode is not ApiMode.MOBILE:
            return None

        async def call(client: Any) -> Any:
            if not hasattr(client, "terms"):
                return None
            return await client.terms(school_year_id=school_year_id) if school_year_id is not None else await client.terms()

        payload = await self._mobile_only(user, call, "Учебные периоды")
        return [t for t in payload if isinstance(t, dict)] if isinstance(payload, list) else None

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

    async def fetch_class_averages(self, user: User, quarter: str) -> dict[str, float] | None:
        """Средний балл класса по всем предметам за четверть: «предмет → балл».

        Для карточек «Итогов»: список предметов и периоды запрашиваются один
        раз, аналитика — по предмету, несколько сразу. Предмет без данных или
        со сбоем просто пропущен. `None` — веб-режим или нет такой четверти.
        """
        if user.effective_api_mode is not ApiMode.MOBILE:
            return None

        async def call(client: Any) -> Any:
            if not hasattr(client, "subject_performance"):
                return None
            term = next(
                (t for t in await client.terms() if quarter_key_of_term(t.get("name")) == quarter),
                None,
            )
            if term is None:
                return None
            subjects = [s for s in await client.subjects_raw() if isinstance(s, dict) and s.get("name")]
            gate = asyncio.Semaphore(4)

            async def one(subject: dict[str, Any]) -> tuple[str, float | None]:
                async with gate:
                    try:
                        payload = await client.subject_performance(subject["id"], term["id"])
                    except NetSchoolError as exc:
                        if exc.reason.needs_relogin:
                            raise
                        return subject["name"], None
                return subject["name"], subject_stats_from(payload).get("class_average")

            return dict(await asyncio.gather(*(one(s) for s in subjects)))

        result = await self._mobile_only(user, call, "Средние баллы класса")
        if result is None:
            return None
        return {name: value for name, value in result.items() if value is not None}

    async def fetch_bells(self, user: User) -> list[dict[str, Any]] | None:
        """Расписание звонков школы (веб-API ``schedule/times``).

        В мобильном API звонков нет, поэтому в мобильном режиме — разовый
        веб-вход по сохранённому паролю. `None` — звонков взять неоткуда
        (вход через Госуслуги, сбой): приложение соберёт их из уроков недели.
        """

        async def call(client: Any) -> Any:
            # У мобильного клиента bell_schedule собран из уроков — он не нужен.
            if hasattr(client, "classmeetings") or not hasattr(client, "bell_schedule"):
                return None
            return await client.bell_schedule()

        try:
            if user.effective_api_mode is ApiMode.WEB:
                bells = await self._with_session(user, call)
            elif user.credentials.can_relogin:
                bells = await self._pool.with_web_once(user, call)
            else:
                return None
        except NetSchoolError as exc:
            if exc.reason.needs_relogin and user.effective_api_mode is ApiMode.WEB:
                raise
            logger.info("Звонки недоступны для %s: %s", user.telegram_id, exc.reason)
            return None
        if not bells:
            return None
        return [
            {
                "weekday": bell.weekday,
                "shift": bell.shift,
                "number": bell.number,
                "start": bell.start.strftime("%H:%M"),
                "end": bell.end.strftime("%H:%M"),
                "used": bell.used,
            }
            for bell in bells
        ]

    # ── только мобильное API: ответ на задание, ВКС, мероприятия, справки ──

    async def _mobile_call(self, user: User, method: str, *args: Any, **kwargs: Any) -> Any:
        """Метод мобильного клиента, которого нет у веб-клиента."""
        if user.effective_api_mode is not ApiMode.MOBILE:
            raise NetSchoolError(Reason.UNKNOWN, "Доступно только в мобильном режиме API")

        async def call(client: Any) -> Any:
            if not hasattr(client, method):
                raise NetSchoolError(Reason.UNKNOWN, "Доступно только в мобильном режиме API")
            try:
                return await getattr(client, method)(*args, **kwargs)
            except Exception as exc:
                raise _refusal(exc) or exc

        return await self._with_session(user, call)

    async def fetch_answer(self, user: User, assignment_id: int) -> dict[str, Any]:
        raw = await self._mobile_call(user, "assignment_answer", assignment_id)
        return answer_from_mobile(raw)

    async def set_answer_text(self, user: User, assignment_id: int, text: str) -> None:
        await self._mobile_call(user, "set_answer_text", assignment_id, text)

    async def upload_answer_file(self, user: User, assignment_id: int, name: str, data: bytes, description: str = "") -> None:
        await self._mobile_call(user, "upload_answer_file", assignment_id, name, data, description=description)

    async def delete_answer_file(self, user: User, assignment_id: int, attachment_id: int) -> None:
        await self._mobile_call(user, "delete_answer_file", assignment_id, attachment_id)

    async def fetch_distance_link(self, user: User, distance_meeting_id: int) -> str:
        """Ссылка на дистанционный урок. Пустая строка — сервер её не дал."""
        return link_from_join(await self._mobile_call(user, "distance_meeting_join", distance_meeting_id))

    async def fetch_events(self, user: User) -> list[dict[str, Any]]:
        """Олимпиады и конкурсы ученика (``/v2/events``)."""
        items: list[dict[str, Any]] = []
        page = 1
        while page <= 5:
            payload = await self._mobile_call(user, "events", page=page, page_size=50)
            rows = payload.get("items") if isinstance(payload, dict) else None
            if not rows:
                break
            items.extend(event_from_mobile(row) for row in rows if isinstance(row, dict))
            if len(items) >= int(payload.get("totalItems") or 0):
                break
            page += 1
        return sorted(items, key=lambda e: e["start"] or "", reverse=True)

    async def fetch_reference_types(self, user: User) -> list[dict[str, Any]]:
        """Виды справок, которые родитель может заказать в школе."""
        rows = await self._mobile_call(user, "parent_reference_types")
        return [reference_type_from_mobile(row) for row in rows or [] if isinstance(row, dict)]

    async def order_reference(self, user: User, reference_type_id: int) -> None:
        await self._mobile_call(user, "order_parent_reference", reference_type_id)

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
            user, lambda client: _collect_weeks_weighted(client, monday, monday, today=msk_today())
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
        if user.api_mode is ApiMode.MYSCHOOL:
            return await self._sync_myschool_students(user)
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

    async def _sync_myschool_students(self, user: User) -> User:
        found = await self._run(user, myschool.list_students)
        students = myschool.to_domain_students(found)
        current = myschool.pick_student(found, user)
        selected = students[found.index(current)]
        updated = replace(
            user,
            available_students=students,
            selected_student_id=selected.id,
            student_name=selected.name,
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
        return (await self.fetch_mail_page(user, page_size=limit))["messages"]

    async def fetch_mail_page(
        self, user: User, folder: str = "Inbox", page: int = 1, page_size: int = 50,
    ) -> dict[str, Any]:
        """Страница папки школьной почты: письма и сколько всего страниц."""

        async def call(client: Any) -> dict[str, Any]:
            data = await client.mail_list(folder, page=page, page_size=page_size)
            entries = getattr(data, "entries", None)
            if entries is None:
                entries = data if isinstance(data, list) else []
            total = _safe_int(getattr(data, "total_items", None)) or len(entries)
            result = {
                "messages": [_mail_to_dict(item) for item in entries],
                "page": page,
                "pages": max(1, -(-total // page_size)),
            }
            if not entries or all(getattr(item, "read", None) is not None for item in entries):
                # Мобильное API отдаёт флаг прочтения прямо в списке.
                return result
            if folder != "Inbox":
                # Непрочитанные школа отдаёт только по входящим: в других
                # папках без флага письмо не выделяем.
                for message, item in zip(result["messages"], entries):
                    if getattr(item, "read", None) is None:
                        message["read"] = True
                return result
            # В веб-списке писем флага прочтения нет — без этого запроса
            # каждое письмо выглядело непрочитанным.
            try:
                unread = {int(i) for i in await client.mail_unread()}
            except Exception as exc:
                logger.debug("Не удалось получить непрочитанные письма: %s", exc)
            else:
                for message in result["messages"]:
                    message["read"] = _safe_int(message["id"]) not in unread
            return result

        return await self._with_session(user, call)

    async def delete_mail(self, user: User, message_id: int) -> None:
        """Переместить письмо в «Удалённые»."""
        await self._with_session(user, lambda client: client.mail_delete([message_id]))

    async def restore_mail(self, user: User, message_id: int) -> None:
        """Вернуть письмо из «Удалённых» туда, где оно было."""
        await self._with_session(user, lambda client: client.mail_restore([message_id]))

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

    async def fetch_assignment_comment(self, user: User, assignment_id: int) -> str:
        """Комментарий учителя к заданию — поле «описание» из карточки.

        В самом дневнике его нет, поэтому экран задания спрашивает отдельно.
        """

        async def call(client: Any) -> str:
            details = await client.assignment_details(assignment_id)
            text = str(getattr(details, "description", "") or "")
            # В веб-версии описание бывает HTML из редактора учителя.
            text = re.sub(r"<br\s*/?>|</p>|</div>", "\n", text, flags=re.I)
            text = html.unescape(re.sub(r"<[^>]+>", "", text))
            return clean_content(re.sub(r"\n{3,}", "\n\n", text))

        return await self._with_session(user, call)

    async def fetch_class_name(self, user: User) -> str:
        """Класс выбранного ученика, как его пишет «Сетевой город» («9б», «10-А»)."""

        async def call(client: Any) -> str:
            init = await _fetch_diary_init(client)
            raw = init.get("students") or []
            entries = [item for item in (raw.values() if isinstance(raw, dict) else raw) if isinstance(item, dict)]
            entry = next((item for item in entries
                          if _safe_int(item.get("studentId")) == user.selected_student_id), None)
            if entry is None and len(entries) == 1:
                entry = entries[0]
            return str((entry or {}).get("className") or "").strip()

        return await self._with_session(user, call)

    async def fetch_account_id(self, user: User) -> int | None:
        """id пользователя в «Сетевом городе» — чей это аккаунт (netschool/ownership.py)."""
        from .ownership import account_id

        return await self._with_session(user, account_id)

    async def fetch_recipients(self, user: User) -> list[dict[str, str]]:
        """Кому можно написать: учителя и администрация школы."""

        async def call(client: Any) -> list[dict[str, str]]:
            if user.effective_api_mode is ApiMode.MOBILE and hasattr(client, "address_book"):
                result = {}
                categories = {"Teachers": "Учителя", "ClassTeachers": "Учителя", "Students": "Ученики",
                              "ClassStudents": "Ученики", "Parents": "Родители", "ClassParents": "Родители",
                              "Admins": "Администрация", "Principals": "Администрация"}
                def walk(group, category="Другие"):
                    category = categories.get(group.get("groupType"), category)
                    for person in group.get("recipients") or []:
                        if person.get("id"):
                            identity = str(person["id"])
                            result.setdefault(identity, {"id": identity, "name": str(person.get("name") or ""),
                                "organization": str(person.get("organizationName") or ""), "category": category})
                    for child in group.get("subGroups") or []:
                        walk(child, category)
                for group in await client.address_book():
                    walk(group)
                return list(result.values())
            # Веб-почта отдаёт адресную книгу по группам. Раньше бралась одна
            # группа по умолчанию — администрация, и веб-пользователи не
            # видели учителей. Какие группы доступны, сервер говорит сам.
            result: dict[str, dict[str, str]] = {}
            failure: Exception | None = None
            for group, title in await _web_recipient_groups(client):
                try:
                    items = await client.mail_recipients(group)
                except Exception as exc:
                    logger.debug("Группа адресатов %s (%s) не получена: %s", group, title, exc)
                    failure = exc
                    continue
                # Шторка получателей знает только свои пять категорий.
                category = _WEB_RECIPIENT_CATEGORIES.get(title.strip().lower(), "Другие")
                for item in items:
                    identity = str(getattr(item, "id", "") or "")
                    if identity:
                        result.setdefault(identity, {
                            "id": identity,
                            "name": str(getattr(item, "name", "") or ""),
                            "organization": str(getattr(item, "organization_name", "") or ""),
                            "category": category,
                        })
            if not result and failure is not None:
                raise failure
            return list(result.values())

        return await self._with_session(user, call)

    async def send_mail(
        self,
        user: User,
        to: list[str],
        subject: str,
        text: str,
        attachments: list[tuple[str, bytes]] | None = None,
        *, notify: bool = False,
    ) -> None:
        """Отправить письмо. Файлы `[(имя, байты)]` — только в мобильном режиме."""
        if attachments and user.effective_api_mode is not ApiMode.MOBILE:
            # Маршрут загрузки вложений известен только у мобильного API.
            raise NetSchoolError(
                Reason.UNKNOWN,
                "Вложения к письмам работают только в мобильном API. Переключиться: /changeapi",
            )
        # Без _with_session: при сбое он повторяет запрос, а повтор отправки
        # мог бы послать письмо дважды.
        async with self._pool.acquire(user) as client:
            try:
                kwargs = {"notify": notify}
                if attachments:
                    kwargs["attachments"] = attachments
                await client.mail_send(subject, text, to, **kwargs)
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

    async def mark_mail_read(self, user: User, message_ids: list[int]) -> list[int]:
        """Отметить письма прочитанными: отдельной команды у школьного сервера
        нет, письмо становится прочитанным, когда его открывают. Возвращает
        номера писем, которые удалось открыть."""

        async def call(client: Any) -> list[int]:
            done: list[int] = []
            for message_id in message_ids:
                try:
                    await client.mail_read(message_id)
                except Exception as exc:
                    logger.debug("Не удалось открыть письмо %s: %s", message_id, exc)
                else:
                    done.append(message_id)
            return done

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

    async def with_client(self, user: User, call: Callable[[Any], Awaitable[T]]) -> T:
        """Свой вызов клиента netschoolpy этого пользователя — с пулом сессий и
        повторами, как у остальных запросов. Нужен ретро-сайту (web/retro.py):
        его ответы строятся прямо из объектов библиотеки."""
        return await self._with_session(user, call)

    async def _with_session(self, user: User, call: Callable[[Any], Awaitable[T]]) -> T:
        """Операция над клиентом netschoolpy; для «Моей школы» таких нет."""
        if user.api_mode is ApiMode.MYSCHOOL:
            raise NetSchoolError(Reason.UNKNOWN, "Это пока недоступно для «Моей школы».")
        return await self._run(user, call)

    async def _run(self, user: User, call: Callable[[Any], Awaitable[T]]) -> T:
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
                last = wrap(exc, context=f"пользователь {user.telegram_id}",
                            myschool=user.api_mode is ApiMode.MYSCHOOL)

            if last.reason is Reason.CREDENTIALS:
                # Школа не приняла пароль. Повтор не поможет, а лишняя
                # неудачная попытка приближает блокировку входа на минуту.
                await self._pool.invalidate(user.telegram_id)
                raise last
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


_REFUSAL = re.compile(r"HTTP (40[03-9]|4[1-9]\d)\b")


def _refusal(exc: Exception) -> NetSchoolError | None:
    """Отказ сервера по смыслу — не сбой и не протухшая сессия.

    На 403 сервер пишет «Attempted to perform an unauthorized operation», и
    по слову «unauthorized» общий разбор принял бы его за истёкшую сессию:
    пользователя отправили бы входить заново. Ответ 500 с текстом
    («Ошибка обращения к шлюзу ВКС») тоже не временный сбой — повторять его
    незачем, а текст сервера понятнее общего «попробуйте позже».
    """
    try:
        from netschoolpy import exceptions as ns
    except ImportError:
        return None
    text = str(exc)
    if isinstance(exc, ns.SessionExpired):
        return None
    detail = text.split("сервер ответил 500: ", 1)[1] if "сервер ответил 500: " in text else ""
    if isinstance(exc, ns.ServerUnavailable) and detail:
        return NetSchoolError(Reason.UNKNOWN, detail, cause=exc)
    if not isinstance(exc, ns.ServerUnavailable) and _REFUSAL.search(text):
        if " 403 " in text or "HTTP 403" in text:
            return NetSchoolError(Reason.UNKNOWN, "Школьный сервер не разрешает это действие для вашей учётной записи.", cause=exc)
        if "HTTP 404" in text:
            return NetSchoolError(Reason.UNKNOWN, "Школьный сервер этого не поддерживает.", cause=exc)
        match = re.search(r'"message"\s*:\s*"([^"]+)"', text)
        return NetSchoolError(Reason.UNKNOWN, match.group(1) if match else "Школьный сервер отклонил запрос.", cause=exc)
    return None


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
            # Неполный ответ нельзя выдавать за успешный: он затрёт кэш
            # и историю уведомлений. Вызывающий код отдаст сохранённые
            # данные, а фоновая проверка оставит прежнее состояние.
            raise

        for day in getattr(diary, "schedule", None) or []:
            day_date = getattr(day, "day", None)
            if day_date is not None and day_date not in seen:
                seen.add(day_date)
                days.append(day)
        current += dt.timedelta(days=7)

    return days


# Группы веб-адресной книги, если сервер не отдал свой список: как в
# netschoolpy.RecipientGroup.
WEB_RECIPIENT_GROUPS: tuple[tuple[int, str], ...] = (
    (1, "Администраторы"), (2, "Завучи"), (3, "Классные руководители"), (4, "Учителя"), (7, "Одноклассники"),
)
# Названия групп сервера -> категории шторки получателей (как у мобильного API).
_WEB_RECIPIENT_CATEGORIES = {
    "администраторы": "Администрация", "администрация": "Администрация", "завучи": "Администрация",
    "классные руководители": "Учителя", "учителя": "Учителя",
    "родители": "Родители", "ученики": "Ученики", "одноклассники": "Ученики",
}


async def _web_recipient_groups(client: Any) -> list[tuple[int, str]]:
    """Группы адресной книги, которые сайт показывает этому пользователю.

    Список — в `mail/recipients/filterpanel` (фильтр «Группа пользователей»).
    Нет ответа или фильтра — берём группы по умолчанию.
    """
    try:
        response = await client._authed_get(
            "mail/recipients/filterpanel",
            params={"userId": client._student_id, "organizationId": client._school_id, "funcType": 2},
        )
        for source in response.json().get("filterSources") or []:
            if source.get("filterId") == "group":
                groups = [(int(item["value"]), str(item.get("title") or ""))
                          for item in source.get("items") or [] if str(item.get("value", "")).isdigit()]
                if groups:
                    return groups
    except Exception as exc:
        logger.debug("Группы адресной книги не получены: %s", exc)
    return list(WEB_RECIPIENT_GROUPS)


async def _collect_weeks_weighted(
    client: Any, start: dt.date, end: dt.date, *, today: dt.date
) -> list[Any]:
    return await _fill_weights(client, await _collect_weeks(client, start, end, today=today))


# (адрес школы, id задания) -> (вес или None после неудачи, когда узнали).
_weights: dict[tuple[str, int], tuple[int | None, float]] = {}


async def _fill_weights(client: Any, days: list[Any]) -> list[Any]:
    """Подставить вес из карточки заданиям, у которых дневник его не отдал.

    Часть серверов (net.mephi3.ru, 5.17) пишет в веб-дневнике `weight: 0` у
    всех заданий, и бот показывал вес 1 и считал простое среднее вместо
    взвешенного. Настоящий вес есть только в карточке задания, и он свой у
    каждого задания (контрольная бывает и с весом 4, и с 1). Карточки
    запоминаются: регулярная проверка спрашивает только новые задания.
    Карточка не пришла — остаётся прежнее поведение (вес 1), дневник из-за
    этого не ломаем.
    """
    if not hasattr(client, "assignment_details"):
        return days
    school = str(getattr(getattr(client, "_http", None), "base_url", "") or getattr(client, "url", ""))
    now = time.monotonic()
    known: dict[int, int] = {}
    missing: set[int] = set()
    for day in days:
        for lesson in getattr(day, "lessons", None) or []:
            for assignment in getattr(lesson, "assignments", None) or []:
                assignment_id = getattr(assignment, "id", None)
                if getattr(assignment, "weight", None) or not isinstance(assignment_id, int):
                    continue
                cached = _weights.get((school, assignment_id))
                if cached is not None:
                    weight, at = cached
                    if weight is not None and now - at < WEIGHT_TTL:
                        known[assignment_id] = weight
                        continue
                    if weight is None and now - at < WEIGHT_FAILED_TTL:
                        continue
                missing.add(assignment_id)
    if not known and not missing:
        return days

    semaphore = asyncio.Semaphore(WEIGHT_CONCURRENCY)

    async def fetch(assignment_id: int) -> None:
        async with semaphore:
            try:
                details = await client.assignment_details(assignment_id)
                weight = int(getattr(details, "weight", 0) or 0) or None
            except Exception as exc:
                logger.debug("Карточка задания %s не получена: %s", assignment_id, exc)
                weight = None
        _weights[(school, assignment_id)] = (weight, time.monotonic())
        if weight is not None:
            known[assignment_id] = weight

    await asyncio.gather(*(fetch(assignment_id) for assignment_id in sorted(missing)))
    if not known:
        return days
    return [_with_weights(day, known) for day in days]


def _with_weights(day: Any, weights: dict[int, int]) -> Any:
    lessons = []
    for lesson in getattr(day, "lessons", None) or []:
        assignments = [
            _replaced(a, weight=weights[a.id])
            if not getattr(a, "weight", None) and getattr(a, "id", None) in weights else a
            for a in getattr(lesson, "assignments", None) or []
        ]
        lessons.append(_replaced(lesson, assignments=assignments))
    return _replaced(day, lessons=lessons)


def _replaced(obj: Any, **changes: Any) -> Any:
    """Копия объекта с другими полями: модели netschoolpy неизменяемые."""
    if hasattr(obj, "model_copy"):
        return obj.model_copy(update=changes)
    obj = copy.copy(obj)
    for name, value in changes.items():
        setattr(obj, name, value)
    return obj


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
