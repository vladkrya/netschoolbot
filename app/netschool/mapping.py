"""Преобразование ответов netschoolpy в доменные записи.

Вынесено отдельно и без сети: разбор чужих объектов — самая хрупкая часть
(библиотека отдаёт то объект, то словарь, то None), и её нужно уметь
проверять тестами на зафиксированных примерах ответов.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import replace
from typing import Any

from ..domain.models import normalize
from ..domain.periods import term_key
from ..domain.records import (
    AbsenceRecord,
    Attachment,
    DiaryDay,
    HomeworkRecord,
    Lesson,
    MarkRecord,
    RecentEvents,
    clean_content,
    extract_mark,
)

logger = logging.getLogger("netschoolbot.netschool")

# Домашние задания за пределами этого окна не показываем: прошлые уже не
# нужны, а слишком далёкие будущие школа обычно ещё правит.
HOMEWORK_PAST_DAYS = 1
HOMEWORK_FUTURE_DAYS = 30


def _attr(source: Any, name: str, default: Any = None) -> Any:
    """Достать поле хоть из объекта, хоть из словаря."""
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _time_text(value: Any) -> str:
    return value.strftime("%H:%M") if hasattr(value, "strftime") else ""


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_datetime(value: Any) -> dt.datetime | None:
    return value if isinstance(value, dt.datetime) else None


def marks_from_day(day: Any) -> list[MarkRecord]:
    """Все выставленные оценки одного дня дневника."""
    day_date = _as_date(_attr(day, "day"))
    if day_date is None:
        return []

    records: list[MarkRecord] = []
    for lesson in _attr(day, "lessons", []) or []:
        subject = str(_attr(lesson, "subject", "") or "—")
        lesson_number = _attr(lesson, "number")
        lesson_start = _time_text(_attr(lesson, "start"))
        lesson_end = _time_text(_attr(lesson, "end"))

        for index, assignment in enumerate(_attr(lesson, "assignments", []) or []):
            mark = extract_mark(assignment)
            if not mark:
                continue
            records.append(
                MarkRecord(
                    subject=subject,
                    date=day_date,
                    assignment_type=str(
                        _attr(assignment, "kind") or _attr(assignment, "type") or "Задание"
                    ).strip(),
                    content=str(_attr(assignment, "content", "") or ""),
                    mark=mark,
                    weight=int(_attr(assignment, "weight") or 1),
                    comment=str(_attr(assignment, "comment", "") or ""),
                    lesson_number=lesson_number,
                    lesson_start=lesson_start,
                    lesson_end=lesson_end,
                    assignment_index=index,
                    published_at=_as_datetime(_attr(assignment, "published_at")),
                    marked_at=_as_datetime(_attr(assignment, "mark_set_at")),
                )
            )
    return records


def homework_from_day(
    day: Any, *, today: dt.date | None = None, only_current: bool = True
) -> list[HomeworkRecord]:
    """Домашние задания одного дня дневника.

    `only_current` оставляет только актуальные — для списка «Д/З» и
    уведомлений. Урокам в дневнике нужны все: иначе прошлые дни недели
    показывались без домашки.
    """
    day_date = _as_date(_attr(day, "day"))
    if day_date is None:
        return []
    today = today or dt.date.today()
    earliest = today - dt.timedelta(days=HOMEWORK_PAST_DAYS)
    latest = today + dt.timedelta(days=HOMEWORK_FUTURE_DAYS)

    records: list[HomeworkRecord] = []
    for lesson in _attr(day, "lessons", []) or []:
        subject = str(_attr(lesson, "subject", "") or "—")
        lesson_number = _attr(lesson, "number")
        topic = str(_attr(lesson, "topic", "") or _attr(lesson, "lessonTopic", "") or "")

        for index, assignment in enumerate(_attr(lesson, "assignments", []) or []):
            raw_content = _attr(assignment, "content", "") or ""
            text = clean_content(raw_content)
            attachments = _attachments(assignment)
            # «---Не указана---» означает «задание есть, текста нет».
            explicitly_empty = str(raw_content).strip() in {"---Не указана---", "Не указана"}
            if not text and not attachments and not explicitly_empty:
                continue

            due = _as_date(_attr(assignment, "deadline")) or day_date
            if only_current and not earliest <= due <= latest:
                continue

            records.append(
                HomeworkRecord(
                    subject=subject,
                    due_date=due,
                    assignment_type=str(
                        _attr(assignment, "kind") or _attr(assignment, "type") or "Задание"
                    ).strip(),
                    text=text or ("Не указана" if explicitly_empty else ""),
                    lesson_number=lesson_number or (index + 1),
                    attachments=attachments,
                    published_at=_as_datetime(_attr(assignment, "published_at")),
                    weight=_safe_int(_attr(assignment, "weight")) or 1,
                    topic=topic,
                    assignment_id=_safe_int(_attr(assignment, "id")),
                    comment=_comment(assignment),
                )
            )
    return records


def _comment(assignment: Any) -> str | None:
    """Комментарий к заданию из дневника: "" — его нет, None — неизвестно.

    Поле есть только у мобильного API (см. patches/assignments.py).
    """
    value = _attr(assignment, "description")
    return None if value is None else clean_content(value)


def _attachments(assignment: Any) -> tuple[Attachment, ...]:
    result: list[Attachment] = []
    for item in _attr(assignment, "attachments", []) or []:
        att_id = _attr(item, "id")
        name = _attr(item, "name") or (f"file_{att_id}" if att_id is not None else "Вложение")
        result.append(
            Attachment(id=int(att_id) if att_id is not None else None, name=str(name))
        )
    return tuple(result)


def lessons_from_day(day: Any, *, today: dt.date | None = None) -> list[Lesson]:
    """Расписание одного дня вместе с оценками и заданиями каждого урока.

    Оценки и задания собираются здесь же, а не сопоставляются потом по
    предмету: один предмет может стоять в дне дважды, и связать их обратно
    по названию уже нельзя.
    """
    from netschoolpy import is_elective

    day_date = _as_date(_attr(day, "day"))
    if day_date is None:
        return []

    all_marks = marks_from_day(day)
    all_homework = homework_from_day(day, today=today, only_current=False)

    lessons: list[Lesson] = []
    for position, raw in enumerate(_attr(day, "lessons", []) or []):
        subject = str(_attr(raw, "subject", "") or "—")
        number = _safe_int(_attr(raw, "number"))
        lessons.append(
            Lesson(
                number=number if number is not None else position + 1,
                subject=subject,
                start=_time_text(_attr(raw, "start")),
                end=_time_text(_attr(raw, "end")),
                room=str(_attr(raw, "room", "") or ""),
                teacher=str(_attr(raw, "teacher", "") or ""),
                topic=str(_attr(raw, "topic", "") or _attr(raw, "lessonTopic", "") or ""),
                absence=str(_attr(raw, "absence", "") or _attr(raw, "absenceReason", "") or ""),
                is_elective=_attr(raw, "is_elective", False) is True or is_elective(raw),
                assignment_ids=tuple(
                    int(a_id)
                    for a_id in (_attr(a, "id") for a in _attr(raw, "assignments", []) or [])
                    if _safe_int(a_id)
                ),
                marks=tuple(
                    m for m in all_marks
                    if m.subject == subject and m.lesson_number == number
                ),
                homework=tuple(
                    h for h in all_homework
                    if h.subject == subject and h.lesson_number == number
                ),
            )
        )

    # Сетевой город иногда отдаёт текущий урок с номером 1 вместо его
    # номера в расписании. При повторяющихся номерах восстанавливаем
    # последовательность по времени, сохраняя пропущенные номера уроков.
    numbers = [item.number for item in lessons]
    if len(set(numbers)) != len(numbers) and all(item.start for item in lessons):
        lessons.sort(key=lambda item: (item.start, item.number or 0))
        previous = 0
        for index, item in enumerate(lessons):
            previous = max(item.number or 0, previous + 1)
            lessons[index] = replace(item, number=previous)
    else:
        lessons.sort(key=lambda item: (item.number or 0, item.start))
    return lessons


def diary_days(days: list[Any], *, today: dt.date | None = None) -> list[DiaryDay]:
    """Собрать дни дневника: расписание, оценки и домашние задания."""
    result: list[DiaryDay] = []
    for day in days:
        day_date = _as_date(_attr(day, "day"))
        if day_date is None:
            continue
        result.append(
            DiaryDay(
                day=day_date,
                lessons=lessons_from_day(day, today=today),
                marks=marks_from_day(day),
                homework=homework_from_day(day, today=today),
            )
        )
    result.sort(key=lambda d: d.day)
    return result


def absences_from_days(days: list[DiaryDay]) -> list[AbsenceRecord]:
    """Отметки посещаемости из уроков дневника (поле `absence` мобильного API)."""
    records: list[AbsenceRecord] = []
    for day in days:
        for lesson in day.lessons:
            code = (lesson.absence or "").strip()
            if code:
                records.append(
                    AbsenceRecord(
                        subject=lesson.subject, date=day.day, lesson_number=lesson.number, code=code
                    )
                )
    return records


def recent_events_from(payload: Any) -> RecentEvents:
    """Разобрать ответ `period-events`: оценки (`ResultInfo`) и задания (`HomeworkInfo`).

    Отпечаток события включает то, что меняется, когда учитель правит
    работу: оценку и время выставления/публикации. Так исправленная оценка
    тоже выглядит новым событием.
    """
    events = _attr(payload, "events", None) if isinstance(payload, dict) else None
    keys: set[str] = set()
    marks = homework = 0
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "ResultInfo" and isinstance(event.get("result"), dict):
            item = event["result"]
            keys.add(
                f"r:{item.get('assignmentId')}:{item.get('result')}:{item.get('resultDate')}"
            )
            marks += 1
        elif kind == "HomeworkInfo" and isinstance(event.get("homework"), dict):
            item = event["homework"]
            keys.add(f"h:{item.get('assignmentId')}:{item.get('assignmentDate')}")
            homework += 1
    return RecentEvents(keys=frozenset(keys), marks=marks, homework=homework)


def quarter_key_of_term(name: Any) -> str | None:
    """Ключ периода по названию из мобильного API: «1 четверть» → `q1`, «2 полугодие» → `h2`.

    Имя осталось от времён, когда таблица знала только четверти.
    """
    return term_key(name)


# Служебные значения `markCode` в `/totals`: статус периода, а не отметка.
# `NotExamined` — оценки ещё нет, `PointSystem` — итог считается по среднему.
# В таблице «Итогов» они стояли вместо оценок.
_STATUS_CODE = re.compile(r"^[A-Z][a-z]+(?:[A-Z][a-z]+)+$|^(?:None|Null|NotSet)$")


def _mark_code(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return "" if _STATUS_CODE.match(text) else text


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def totals_from_mobile(
    rows: Any, subject_names: dict[int, str]
) -> dict[str, dict[str, Any]]:
    """Итоговые оценки из `/totals`: {предмет: {terms, year, final}}.

    `terms` — четвертные оценки по ключам `q1`…`q4`: `{mark, average}`,
    `year` — годовая, `final` — итоговая (`periodType` `Year`/`Total`).
    Предметы без единой оценки пропускаются, а не занимают строку таблицы.
    """
    result: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = subject_names.get(_safe_int(row.get("subjectId")) or -1)
        if not name:
            continue

        terms: dict[str, dict[str, Any]] = {}
        for item in row.get("termTotals") or []:
            key = quarter_key_of_term((item.get("term") or {}).get("name"))
            mark, average = (_mark_code(item.get("mark")) or _mark_code(item.get("markCode"))), _number(item.get("avgMark"))
            if key and (mark or average is not None):
                terms[key] = {"mark": mark, "average": average}

        year = final = ""
        for item in row.get("yearTotals") or []:
            period = item.get("period") or {}
            kind = str(period.get("periodType") or period.get("periodName") or "").lower()
            mark = _mark_code(item.get("mark")) or _mark_code(item.get("markCode"))
            if kind in ("year", "годовая"):
                year = mark or year
            elif kind in ("total", "итоговая"):
                final = mark or final

        if terms or year or final:
            result[name] = {"terms": terms, "year": year, "final": final}
    return result


def subject_stats_from(payload: Any) -> dict[str, Any]:
    """Итоги по предмету из `/analytics/subject-performance`.

    Ответ на экран: средний балл ученика и класса, распределение оценок и
    сколько уроков прошло из запланированных.
    """
    if not isinstance(payload, dict):
        return {}
    stats = payload.get("classmeetingsStats") or {}
    return {
        "average": _number(payload.get("averageMark")),
        "class_average": _number(payload.get("classAverageMark")),
        "max_mark": _number(payload.get("maxMark")),
        "mark_stats": [
            {
                "mark": _mark_code(item.get("mark")),
                "count": _safe_int(item.get("count")) or 0,
                "fraction": _number(item.get("fraction")),
            }
            for item in payload.get("markStats") or []
            if isinstance(item, dict)
        ],
        "lessons": {
            "passed": _safe_int(stats.get("passed")) or 0,
            "scheduled": _safe_int(stats.get("scheduled")) or 0,
        },
    }


def find_by_name(items: Any, wanted: str) -> dict[str, Any] | None:
    """Предмет из мобильного списка `subjects` по названию (без учёта регистра и пробелов)."""
    target = normalize(wanted)
    for item in items or []:
        if isinstance(item, dict) and normalize(item.get("name")) == target:
            return item
    return None


# Поля, в которых «Сетевой город» держит имя ученика, в порядке предпочтения.
# nickName — основное: именно его отдаёт `student/diary/init`. Остальные
# встречаются в ответах других установок и оставлены как запасные.
STUDENT_NAME_FIELDS = ("nickName", "fio", "fullName", "name", "shortName")


def students_from_diary_init(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    """Разобрать список детей из ответа `student/diary/init`.

    Две тонкости, на которых легко ошибиться:

    1. `students` приходит то списком, то словарём — обе формы встречаются.

    2. `currentStudentId`, вопреки названию, в списочной форме содержит
       **индекс** в этом списке, а не идентификатор ученика. Так его читает
       и сама netschoolpy: ``info["students"][info["currentStudentId"]]``.
       Если принять его за идентификатор, текущий ученик не найдётся и
       выбор молча уедет не туда.
    """
    raw = payload.get("students") or {}
    current_raw = _safe_int(payload.get("currentStudentId"))

    if isinstance(raw, dict):
        items: list[tuple[Any, Any]] = list(raw.items())
    elif isinstance(raw, list):
        items = list(enumerate(raw))
    else:
        items = []

    students: list[dict[str, Any]] = []
    for key, student in items:
        if not isinstance(student, dict):
            continue
        student_id = _safe_int(student.get("studentId"))
        if student_id is None:
            student_id = _safe_int(key)
        if student_id is None:
            continue
        students.append({"id": student_id, "name": _student_name(student, student_id)})

    return students, _resolve_current(students, current_raw, is_list=isinstance(raw, list))


def is_placeholder_name(name: str) -> bool:
    """Похоже ли имя на заглушку `Ученик <id>`.

    Нужно, чтобы починить уже сохранённые записи: пользователи, вошедшие
    до исправления разбора, держат в базе заглушку, и сама она не
    обновится — вход-то они больше не проходят.
    """
    return not name.strip() or bool(re.fullmatch(r"Ученик \d+", name.strip()))


def _student_name(student: dict[str, Any], student_id: int) -> str:
    for field in STUDENT_NAME_FIELDS:
        value = str(student.get(field) or "").strip()
        if value:
            return value
    # Имени в ответе нет — показываем хотя бы идентификатор, чтобы список
    # детей оставался различимым.
    return f"Ученик {student_id}"


def _resolve_current(
    students: list[dict[str, Any]], current: int | None, *, is_list: bool
) -> int | None:
    """Определить идентификатор текущего ученика.

    Порядок проверок важен: сначала пробуем трактовать значение как
    идентификатор (словарная форма ответа), и только потом как индекс
    (списочная). Наоборот было бы опаснее: у первого ребёнка индекс 0,
    и любой ноль уводил бы выбор на него.
    """
    if not students:
        return None
    if current is None:
        return students[0]["id"]

    known_ids = {item["id"] for item in students}
    if current in known_ids:
        return current
    if is_list and 0 <= current < len(students):
        return students[current]["id"]
    return students[0]["id"]


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
