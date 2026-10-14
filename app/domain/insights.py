"""Выводы из оценок и заданий: контрольные, риск по оценке, итоги периода.

Чистые функции без сети и базы — их зовут фоновая проверка (напоминания и
сводки) и бот. Правила итоговой оценки те же, что в приложении (grading.js)
и у ИИ-помощника: средний с учётом веса против порогов «5», «4», «3».
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .models import normalize
from .records import HomeworkRecord, MarkRecord, parse_mark_float

DEFAULT_THRESHOLDS: dict[str, float] = {"5": 4.5, "4": 3.5, "3": 2.5}

# Слова в типе работы, по которым она считается проверочной.
TEST_WORDS = (
    "контрольн", "самостоят", "проверочн", "тест", "зачёт", "зачет", "экзамен",
    "диктант", "срез", "впр", "диагностич", "изложени", "сочинени", "аттестац",
)
# Работа с таким весом — проверочная, как бы ни называлась.
TEST_WEIGHT = 3

# Насколько выше порога средний ещё считается «на грани».
EDGE_MARGIN = 0.1


def is_test_work(assignment_type: str, weight: int | float = 1) -> bool:
    kind = normalize(assignment_type).replace("ё", "е")
    if any(word.replace("ё", "е") in kind for word in TEST_WORDS):
        return True
    try:
        return float(weight or 1) >= TEST_WEIGHT
    except (TypeError, ValueError):
        return False


def weighted(records: list[MarkRecord]) -> float | None:
    total = weights = 0.0
    for record in records:
        value = parse_mark_float(record.mark)
        if value is None:
            continue
        weight = float(record.weight) if record.weight and record.weight > 0 else 1.0
        total += value * weight
        weights += weight
    return total / weights if weights else None


def thresholds_for(prefs: dict[str, Any] | None, subject: str) -> dict[str, float]:
    saved = (prefs or {}).get("thresholds") or {}
    own = (saved.get("subjects") or {}).get(subject)
    found = own or saved.get("all") or DEFAULT_THRESHOLDS
    try:
        return {grade: float(found[grade]) for grade in ("5", "4", "3")}
    except (KeyError, TypeError, ValueError):
        return dict(DEFAULT_THRESHOLDS)


def grade_for(average: float, thresholds: dict[str, float]) -> int:
    for grade in ("5", "4", "3"):
        if average >= thresholds[grade] - 1e-9:
            return int(grade)
    return 2


def needed_marks(records: list[MarkRecord], target: float, value: int = 5) -> int | None:
    """Сколько оценок `value` (вес 1) нужно, чтобы средний дошёл до `target`."""
    pairs = [
        (v, float(r.weight) if r.weight and r.weight > 0 else 1.0)
        for r in records
        if (v := parse_mark_float(r.mark)) is not None
    ]
    total = sum(v * w for v, w in pairs)
    weights = sum(w for _, w in pairs)
    if weights and total / weights >= target - 1e-9:
        return 0
    for count in range(1, 101):
        if (total + value * count) / (weights + count) >= target - 1e-9:
            return count
    return None


@dataclass(frozen=True, slots=True)
class RiskAlert:
    """Средний по предмету опустился ниже порога (или стоит на грани)."""

    subject: str
    average: float
    grade: int
    previous_grade: int
    # Сколько пятёрок вернут прежнюю оценку; None — недостижимо.
    fives: int | None


def grade_drops(
    before: list[MarkRecord], after: list[MarkRecord], prefs: dict[str, Any] | None
) -> list[RiskAlert]:
    """Предметы, где итоговая оценка за период после новых оценок стала ниже.

    `before` и `after` — оценки текущего периода до и после проверки.
    """
    def by_subject(records: list[MarkRecord]) -> dict[str, list[MarkRecord]]:
        result: dict[str, list[MarkRecord]] = {}
        for record in records:
            result.setdefault(record.subject, []).append(record)
        return result

    old, new = by_subject(before), by_subject(after)
    alerts = []
    for subject, records in sorted(new.items()):
        thresholds = thresholds_for(prefs, subject)
        now, was = weighted(records), weighted(old.get(subject, []))
        if now is None or was is None:
            continue
        grade, previous = grade_for(now, thresholds), grade_for(was, thresholds)
        if grade < previous:
            alerts.append(RiskAlert(subject, now, grade, previous,
                                    needed_marks(records, thresholds[str(previous)])))
    return alerts


def on_the_edge(records: list[MarkRecord], prefs: dict[str, Any] | None) -> list[RiskAlert]:
    """Предметы, где одна плохая оценка опустит итоговую: средний чуть выше порога."""
    by_subject: dict[str, list[MarkRecord]] = {}
    for record in records:
        by_subject.setdefault(record.subject, []).append(record)
    result = []
    for subject, items in sorted(by_subject.items()):
        average = weighted(items)
        if average is None:
            continue
        thresholds = thresholds_for(prefs, subject)
        grade = grade_for(average, thresholds)
        if grade <= 2:
            continue
        floor = thresholds[str(grade)]
        if average - floor < EDGE_MARGIN:
            result.append(RiskAlert(subject, average, grade, grade, None))
    return result


def upcoming_tests(homework: list[HomeworkRecord], days: list[dt.date]) -> list[HomeworkRecord]:
    """Проверочные работы на эти дни — по одной на предмет и день."""
    wanted = set(days)
    seen: set[tuple[dt.date, str]] = set()
    result = []
    for item in sorted(homework, key=lambda h: (h.due_date, h.lesson_number or 0, h.subject)):
        key = (item.due_date, normalize(item.subject))
        if item.due_date in wanted and is_test_work(item.assignment_type, item.weight) and key not in seen:
            seen.add(key)
            result.append(item)
    return result


@dataclass(frozen=True, slots=True)
class PeriodStats:
    count: int
    average: float | None
    counts: dict[int, int]
    # (предмет, средний, оценок) по убыванию среднего; у предмета от двух оценок.
    subjects: list[tuple[str, float, int]]
    absences: int = 0


def period_stats(records: list[MarkRecord], absences: int = 0) -> PeriodStats:
    numeric = [r for r in records if r.numeric_mark is not None]
    by_subject: dict[str, list[MarkRecord]] = {}
    for record in numeric:
        by_subject.setdefault(record.subject, []).append(record)
    ranked = sorted(
        ((subject, avg, len(items)) for subject, items in by_subject.items()
         if len(items) >= 2 and (avg := weighted(items)) is not None),
        key=lambda item: (-item[1], item[0]),
    )
    return PeriodStats(
        count=len(records),
        average=weighted(numeric),
        counts=dict(Counter(r.numeric_mark for r in numeric)),
        subjects=ranked,
        absences=absences,
    )


def subject_grades(records: list[MarkRecord], prefs: dict[str, Any] | None) -> list[tuple[str, float, int, int]]:
    """(предмет, средний, выходит оценка, оценок) — для итогов периода."""
    by_subject: dict[str, list[MarkRecord]] = {}
    for record in records:
        by_subject.setdefault(record.subject, []).append(record)
    result = []
    for subject, items in sorted(by_subject.items()):
        average = weighted(items)
        if average is not None:
            result.append((subject, average, grade_for(average, thresholds_for(prefs, subject)), len(items)))
    return result


def streak_without(records: list[MarkRecord], today: dt.date, *, worst: int = 2) -> int | None:
    """Сколько учебных дней подряд (до сегодня) не было оценок `worst` и ниже.

    None — плохих оценок не было вовсе (считать не от чего).
    """
    bad = [r.date for r in records if (v := r.numeric_mark) is not None and v <= worst and r.date <= today]
    if not bad:
        return None
    last = max(bad)
    day, count = last + dt.timedelta(days=1), 0
    while day <= today:
        if day.weekday() < 5:
            count += 1
        day += dt.timedelta(days=1)
    return count
