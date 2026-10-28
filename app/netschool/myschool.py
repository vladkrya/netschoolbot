"""«Госуслуги Моя школа» как источник дневника.

Библиотека `myschoolpy` отдаёт уроки, оценки и задания по неделям; здесь они
собираются в те же `DiaryDay`, с которыми работают слежение, бот и мини-приложение.
Остальное (почта, чат, ответы на задания, средние) в «Моей школе» не реализовано.

Идентификаторы учеников там строки (UUID), а у пользователя бота
`selected_student_id` — число. Поэтому учеником считается номер в списке
`students()` (с единицы); список отсортирован по id и не меняется, пока не
привязали другого ребёнка.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import defaultdict
from typing import Any

from ..domain.models import School, Student, User
from ..domain.records import Attachment, DiaryDay, HomeworkRecord, Lesson, MarkRecord
from .errors import NetSchoolError, Reason

MYSCHOOL_URL = "https://www.gosuslugi.ru/api/myschool"
MYSCHOOL_NAME = "Госуслуги Моя школа"

MAX_MATERIAL_BYTES = 50 * 1024 * 1024
MATERIAL_TIMEOUT_S = 90
# Окно, в котором ищем файл, когда процесс перезапустился и ссылок в памяти нет.
LOOKUP_DAYS_BACK, LOOKUP_DAYS_FORWARD = 90, 60
LINKS_LIMIT = 5000

HOMEWORK_TYPE = "Домашнее задание"
DEFAULT_WORK = "Оценка"


# Файлы заданий в «Моей школе» — ссылки, а бот и мини-приложение называют вложение
# числом. Число считается из ссылки, а ссылки по числу помнятся здесь: качать можно
# только то, что API отдал самому ученику.
_LINKS: dict[int, str] = {}


def material_id(url: str) -> int:
    """Стабильное число для ссылки на файл (помещается в безопасное для JS целое)."""
    digest = hashlib.blake2b(url.encode(), digest_size=7).digest()
    return int.from_bytes(digest, "big") & (2 ** 53 - 1)


def _remember(url: str) -> int:
    if len(_LINKS) >= LINKS_LIMIT:
        for key in list(_LINKS)[: LINKS_LIMIT // 2]:
            del _LINKS[key]
    file_id = material_id(url)
    _LINKS[file_id] = url
    return file_id


def myschool_school() -> School:
    return School(url=MYSCHOOL_URL, name=MYSCHOOL_NAME)


def new_client(proxy: str | None = None) -> Any:
    from myschoolpy import MySchool

    # Продлевать cookies незачем: сессия — bearer-токен, им управляет пул.
    return MySchool(proxy=proxy, keepalive_interval=None)


async def list_students(client: Any) -> list[Any]:
    """Ученики аккаунта: сам ученик или, для родителя, его дети."""
    from myschoolpy import exceptions as ms

    for role in ("student", "parent"):
        try:
            found = await client.students(role=role)
        except (ms.NotFound, ms.AccessDenied):
            continue
        if found:
            return sorted(found, key=lambda s: s.id)
    return []


def to_domain_students(students: list[Any]) -> tuple[Student, ...]:
    return tuple(Student(id=i, name=s.full_name) for i, s in enumerate(students, 1))


def pick_student(students: list[Any], user: User) -> Any:
    if not students:
        raise NetSchoolError(Reason.UNKNOWN, "В «Моей школе» нет учеников для этого аккаунта")
    index = (user.selected_student_id or 1) - 1
    return students[index] if 0 <= index < len(students) else students[0]


async def fetch_days(client: Any, user: User, start: dt.date, end: dt.date) -> list[DiaryDay]:
    student = pick_student(await list_students(client), user)
    lessons = await client.lessons(student, start, end)
    marks = await client.marks(student, start, end)
    homeworks = await client.homeworks(student, start, end)
    return build_days(lessons, marks, homeworks)


def _hhmm(value: dt.datetime | None) -> str:
    return value.strftime("%H:%M") if value else ""


def _int(value: Any, default: int = 1) -> int:
    try:
        return max(1, round(float(value)))
    except (TypeError, ValueError):
        return default


def build_days(lessons: list[Any], marks: list[Any], homeworks: list[Any]) -> list[DiaryDay]:
    """Уроки, оценки и задания → дни дневника."""
    days: dict[dt.date, DiaryDay] = {}

    def day_of(date: dt.date) -> DiaryDay:
        return days.setdefault(date, DiaryDay(day=date))

    numbers: dict[str, int] = {}
    per_day: dict[dt.date, list[Any]] = defaultdict(list)
    for lesson in lessons:
        if lesson.start:
            per_day[lesson.start.date()].append(lesson)
    by_lesson: dict[str, tuple[dt.date, int]] = {}
    for date, items in per_day.items():
        for number, lesson in enumerate(sorted(items, key=lambda x: x.start), 1):
            numbers[lesson.id] = number
            by_lesson[lesson.id] = (date, number)

    mark_records: dict[dt.date, list[MarkRecord]] = defaultdict(list)
    seen: dict[tuple, int] = defaultdict(int)
    for mark in sorted(marks, key=lambda m: (m.date or dt.date.min, m.id)):
        if not mark.date or not mark.value:
            continue
        kind = (mark.work_type or DEFAULT_WORK).strip()
        content = (mark.work_name or "").strip()
        key = (mark.date, mark.subject_name, kind, content)
        index = seen[key]
        seen[key] += 1
        record = MarkRecord(
            subject=mark.subject_name,
            date=mark.date,
            assignment_type=kind,
            content=content,
            mark=str(mark.value).strip(),
            weight=_int(mark.weight),
            comment=(mark.comment or "").strip(),
            lesson_number=numbers.get(mark.lesson_id or ""),
            assignment_index=index,
            published_at=mark.created,
        )
        mark_records[mark.date].append(record)
        day_of(mark.date).marks.append(record)

    homework_records: list[HomeworkRecord] = []
    seen_homework: set[str] = set()
    for item in sorted(homeworks, key=lambda h: (h.plan_ready_date or dt.date.min, h.id)):
        if not item.plan_ready_date:
            continue
        record = HomeworkRecord(
            subject=item.subject_name,
            due_date=item.plan_ready_date,
            assignment_type=HOMEWORK_TYPE,
            text=re.sub(r"\s+", " ", item.description or "").strip(),
            lesson_number=numbers.get(item.ready_lesson_id or ""),
            attachments=tuple(_attachments(item.materials)),
            # Пустой комментарий: карточки задания в «Моей школе» нет, спрашивать нечего.
            comment="",
        )
        # Datamart отдаёт одно задание несколькими строками (по числу выдач).
        if record.identity in seen_homework:
            continue
        seen_homework.add(record.identity)
        homework_records.append(record)
        day_of(item.plan_ready_date).homework.append(record)

    for date, items in per_day.items():
        day = day_of(date)
        for lesson in sorted(items, key=lambda x: x.start):
            number = numbers[lesson.id]
            day.lessons.append(Lesson(
                number=number,
                subject=lesson.subject_name,
                start=_hhmm(lesson.start),
                end=_hhmm(lesson.end),
                room=(lesson.room or "").strip(),
                teacher=lesson.teacher,
                topic=(lesson.theme or "").strip(),
                absence=(lesson.skip_type or "").strip(),
                marks=tuple(
                    m for m in mark_records.get(date, ())
                    if m.subject == lesson.subject_name and m.lesson_number == number
                ),
                homework=tuple(
                    h for h in homework_records
                    if h.due_date == date and h.subject == lesson.subject_name and h.lesson_number == number
                ),
            ))
    return sorted(days.values(), key=lambda d: d.day)


def _attachments(materials: list[Any]) -> list[Attachment]:
    items = []
    for material in materials or []:
        if isinstance(material, dict):
            name = material.get("title") or material.get("name") or material.get("link")
            link = material.get("link") or material.get("url") or ""
        else:
            name, link = str(material), ""
        if not name:
            continue
        file_id = _remember(link) if isinstance(link, str) and link.startswith("https://") else None
        items.append(Attachment(id=file_id, name=str(name)))
    return items


async def download_material(client: Any, user: User, attachment_id: int) -> bytes:
    """Файл задания по числу из `Attachment.id`."""
    import httpx

    url = _LINKS.get(attachment_id)
    if url is None:
        today = dt.date.today()
        await fetch_days(client, user, today - dt.timedelta(days=LOOKUP_DAYS_BACK),
                         today + dt.timedelta(days=LOOKUP_DAYS_FORWARD))
        url = _LINKS.get(attachment_id)
    if url is None:
        raise NetSchoolError(Reason.UNKNOWN, "Файл не найден среди заданий «Моей школы».")
    try:
        async with httpx.AsyncClient(timeout=MATERIAL_TIMEOUT_S, follow_redirects=True) as http:
            async with http.stream("GET", url) as response:
                if response.status_code != 200:
                    raise NetSchoolError(Reason.UNKNOWN, "Не удалось скачать файл из «Моей школы».")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_MATERIAL_BYTES:
                        raise NetSchoolError(Reason.UNKNOWN, "Файл слишком большой.")
                    chunks.append(chunk)
    except httpx.TransportError as exc:
        raise NetSchoolError(Reason.SERVER_UNAVAILABLE, "Сервер школы не отдал файл. Попробуйте позже.") from exc
    return b"".join(chunks)
