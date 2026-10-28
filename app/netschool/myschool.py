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
import re
from collections import defaultdict
from typing import Any

from ..domain.models import School, Student, User
from ..domain.records import Attachment, DiaryDay, HomeworkRecord, Lesson, MarkRecord
from .errors import NetSchoolError, Reason

MYSCHOOL_URL = "https://www.gosuslugi.ru/api/myschool"
MYSCHOOL_NAME = "Госуслуги Моя школа"

HOMEWORK_TYPE = "Домашнее задание"
DEFAULT_WORK = "Оценка"


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
    names = []
    for material in materials or []:
        if isinstance(material, dict):
            name = material.get("title") or material.get("name") or material.get("url")
        else:
            name = str(material)
        if name:
            names.append(Attachment(id=None, name=str(name)))
    return names
