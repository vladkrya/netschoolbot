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
    # Даты от школы или из дневника; общепринятые — «примерно».
    exact: bool = True

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


# Промежуток между периодами длиннее выходных — каникулы.
MIN_GAP_DAYS = 3


def default_vacations(year: int) -> list[Vacation]:
    """Общепринятые каникулы учебного года, начавшегося в сентябре `year`."""
    result = []
    for label, (sm, sd), (em, ed) in DEFAULT_HOLIDAYS:
        start = dt.date(year if sm >= 9 else year + 1, sm, sd)
        end = dt.date(year if em >= 9 else year + 1, em, ed)
        result.append(Vacation(label, start, end, exact=False))
    return result


def _lessons_end(period: Quarter) -> dt.date:
    return period.lessons_end or period.end


def school_vacations(periods: list[Quarter]) -> list[Vacation]:
    """Каникулы — промежутки между периодами школы и лето после последнего."""
    ordered = sorted(periods, key=lambda q: q.start)
    result = []
    for before, after in zip(ordered, ordered[1:]):
        # Выходные между периодами — ещё не каникулы.
        if (after.start - _lessons_end(before)).days > MIN_GAP_DAYS:
            start = _lessons_end(before) + dt.timedelta(days=1)
            result.append(Vacation(_season(start), start, after.start - dt.timedelta(days=1)))
    if ordered:
        last = _lessons_end(ordered[-1])
        summer_end = dt.date(academic_year_start(ordered[0].start) + 1, 8, 31)
        if last < summer_end:
            result.append(Vacation("Летние каникулы", last + dt.timedelta(days=1), summer_end))
    return result


def vacations(today: dt.date, periods: list[Quarter] | None = None, source: str = "default") -> list[Vacation]:
    """Каникулы учебного года: из периодов школы, если они настоящие, иначе общепринятые.

    У школ с полугодиями или триместрами между периодами видно не все
    каникулы: осенние и весенние приходятся на середину полугодия. Тех, что
    школа не показала, добавляем по общепринятым датам — «примерно».
    """
    year = academic_year_start(today)
    # Лето относится к прошлому учебному году, а 1–31 августа ждём уже новый.
    defaults = default_vacations(year - 1)[-1:] + default_vacations(year)
    if not (periods and source == "school"):
        return defaults
    found = school_vacations(periods)
    if not found:
        return defaults
    seasons = {item.label for item in found}
    missing = [item for item in defaults if item.label not in seasons
               and not any(item.start <= other.end and other.start <= item.end for other in found)]
    return sorted(found + missing, key=lambda item: item.start)


# Насколько дальше общепринятых дат смотреть в дневник, уточняя каникулы.
DIARY_MARGIN_DAYS = 7


def refine_by_diary(item: Vacation, days: dict[dt.date, list[bool]]) -> Vacation:
    """Уточнить примерные каникулы по дневнику.

    `days` — дни дневника вокруг каникул: для каждого урока, внеурочка ли
    он. В каникулы в расписании остаётся только внеурочка, поэтому день
    лишь с внеурочкой — каникулы, день с обычным уроком — учёба, а пустой
    день (выходной или ещё не заполненный) ничего не говорит.

    От ближайшего к каникулам дня с одной внеурочкой расширяемся до дней
    учёбы. Где учёбы в дневнике не нашлось, остаётся общепринятая граница.
    Нет ни одного дня с одной внеурочкой — даты не трогаем.
    """
    if item.exact:
        return item
    low = item.start - dt.timedelta(days=DIARY_MARGIN_DAYS)
    high = item.end + dt.timedelta(days=DIARY_MARGIN_DAYS)
    days = {day: flags for day, flags in days.items() if low <= day <= high}
    study = {day for day, flags in days.items() if any(not elective for elective in flags)}
    # Субботний кружок бывает и в учебную неделю — считаем только будни.
    free = sorted(day for day, flags in days.items() if flags and all(flags) and day.weekday() < 5)
    if not free:
        return item
    inside = [day for day in free if item.covers(day)]
    anchor = inside[0] if inside else min(
        free, key=lambda day: min(abs((day - item.start).days), abs((day - item.end).days)))
    one = dt.timedelta(days=1)
    start = anchor
    while start - one >= low and start - one not in study:
        start -= one
    end = anchor
    while end + one <= high and end + one not in study:
        end += one
    bounded_left = start - one in study
    bounded_right = end + one in study
    if not bounded_left:
        start = min(item.start, min(day for day in free if day >= start))
    if not bounded_right:
        end = max(item.end, max(day for day in free if day <= end))
    if (end - start).days < MIN_GAP_DAYS:
        # Один-два дня без уроков — не каникулы, а что-то разовое.
        return item
    return Vacation(item.label, start, end, exact=bounded_left and bounded_right)


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
