"""Каникулы и экзамены: обратный отсчёт на «Дне» и тихий режим уведомлений.

Каникулы берутся из периодов школы, когда они есть (мобильное API отдаёт
даты четвертей с промежутками между ними). Иначе — общепринятые даты,
которые Минпросвещения из года в год рекомендует всем школам: осенние с
конца октября, зимние с 31 декабря, весенние в конце марта, летние с июня.

Даты ОГЭ и ЕГЭ — начало основного периода. Расписание на следующий год
Рособрнадзор утверждает зимой, поэтому до того — по прошлогоднему, с
пометкой «примерно». Обновлять здесь, в EXAM_STARTS.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from .periods import Quarter, academic_year_start

# (название, месяц и день начала, месяц и день конца) — даты рекомендаций
# Минпросвещения последних лет.
DEFAULT_HOLIDAYS: tuple[tuple[str, tuple[int, int], tuple[int, int]], ...] = (
    ("Осенние каникулы", (10, 25), (11, 3)),
    ("Зимние каникулы", (12, 31), (1, 11)),
    ("Весенние каникулы", (3, 28), (4, 5)),
    ("Летние каникулы", (6, 1), (8, 31)),
)

# Начало основного периода: {учебный год (год сентября): {экзамен: дата}}.
# Утверждённого расписания нет — берётся последний известный год со сдвигом.
EXAM_STARTS: dict[int, dict[str, dt.date]] = {
    2025: {"ОГЭ": dt.date(2026, 5, 19), "ЕГЭ": dt.date(2026, 6, 1)},
}
# Утверждено ли расписание экзаменов этого учебного года.
EXAMS_CONFIRMED: frozenset[int] = frozenset({2025})


@dataclass(frozen=True, slots=True)
class Vacation:
    label: str
    start: dt.date
    end: dt.date

    def covers(self, day: dt.date) -> bool:
        return self.start <= day <= self.end


def _season(start: dt.date) -> str:
    if start.month in (6, 7, 8):
        return "Летние каникулы"
    if start.month in (10, 11):
        return "Осенние каникулы"
    if start.month in (12, 1, 2):
        return "Зимние каникулы"
    return "Весенние каникулы"


def default_vacations(year: int) -> list[Vacation]:
    """Общепринятые каникулы учебного года, начавшегося в сентябре `year`."""
    result = []
    for label, (sm, sd), (em, ed) in DEFAULT_HOLIDAYS:
        start = dt.date(year if sm >= 9 else year + 1, sm, sd)
        end = dt.date(year if em >= 9 else year + 1, em, ed)
        result.append(Vacation(label, start, end))
    return result


def school_vacations(periods: list[Quarter]) -> list[Vacation]:
    """Каникулы — промежутки между периодами школы и лето после последнего."""
    ordered = sorted(periods, key=lambda q: q.start)
    result = []
    for before, after in zip(ordered, ordered[1:]):
        if (after.start - before.end).days > 1:
            start = before.end + dt.timedelta(days=1)
            result.append(Vacation(_season(start), start, after.start - dt.timedelta(days=1)))
    if ordered:
        last = ordered[-1].end
        summer_end = dt.date(academic_year_start(ordered[0].start) + 1, 8, 31)
        if last < summer_end:
            result.append(Vacation("Летние каникулы", last + dt.timedelta(days=1), summer_end))
    return result


def vacations(today: dt.date, periods: list[Quarter] | None = None, source: str = "default") -> list[Vacation]:
    """Каникулы учебного года: из периодов школы, если они настоящие, иначе общепринятые."""
    if periods and source == "school":
        found = school_vacations(periods)
        if found:
            return found
    year = academic_year_start(today)
    # Лето относится к прошлому учебному году, а 1–31 августа ждём уже новый.
    return default_vacations(year - 1)[-1:] + default_vacations(year)


def on_vacation(today: dt.date, items: list[Vacation]) -> Vacation | None:
    return next((item for item in items if item.covers(today)), None)


def next_vacation(today: dt.date, items: list[Vacation]) -> Vacation | None:
    upcoming = [item for item in items if item.start > today]
    return min(upcoming, key=lambda item: item.start) if upcoming else None


_GRADE = re.compile(r"^\s*(\d{1,2})")


def grade_of(class_name: str) -> int | None:
    """Номер класса из названия: «11 Б» → 11, «9А» → 9."""
    match = _GRADE.match(class_name or "")
    return int(match.group(1)) if match else None


def exam_for(class_name: str, today: dt.date) -> tuple[str, dt.date, bool] | None:
    """Экзамен этого года для 9-го (ОГЭ) и 11-го (ЕГЭ) класса: название,
    начало основного периода и утверждено ли расписание."""
    grade = grade_of(class_name)
    name = {9: "ОГЭ", 11: "ЕГЭ"}.get(grade or 0)
    if name is None:
        return None
    year = academic_year_start(today)
    known = max((y for y in EXAM_STARTS if y <= year), default=None)
    if known is None:
        return None
    base = EXAM_STARTS[known][name]
    try:
        start = base.replace(year=base.year + (year - known))
    except ValueError:
        start = base.replace(year=base.year + (year - known), day=28)
    if start < today:
        return None
    return name, start, year in EXAMS_CONFIRMED
