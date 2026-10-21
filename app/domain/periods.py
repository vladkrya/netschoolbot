"""Учебные периоды: четверти, триместры или полугодия.

Веб-API «Сетевого города» границ периодов не отдаёт (только текущего), а
школы делят год по-разному. Поэтому периоды берутся так:

1. свои даты пользователя, если он их задал;
2. в мобильном API — настоящие периоды школы из `/terms`;
3. иначе — общепринятые для российской школы даты выбранной схемы.

Модуль без зависимостей: это единственная часть логики, где приходится
угадывать, и её нужно уметь проверять отдельно.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class PeriodScheme(str, Enum):
    QUARTERS = "quarters"
    TRIMESTERS = "trimesters"
    HALVES = "halves"


# (ключ, подпись, месяц и день начала). Учебный год начинается 1 сентября.
SCHEME_BOUNDS: dict[PeriodScheme, tuple[tuple[str, str, tuple[int, int]], ...]] = {
    PeriodScheme.QUARTERS: (
        ("q1", "1 четверть", (9, 1)),
        ("q2", "2 четверть", (11, 1)),
        ("q3", "3 четверть", (1, 9)),
        ("q4", "4 четверть", (4, 1)),
    ),
    PeriodScheme.TRIMESTERS: (
        ("t1", "1 триместр", (9, 1)),
        ("t2", "2 триместр", (12, 1)),
        ("t3", "3 триместр", (3, 1)),
    ),
    PeriodScheme.HALVES: (
        ("h1", "1 полугодие", (9, 1)),
        ("h2", "2 полугодие", (1, 9)),
    ),
}

QUARTER_BOUNDS = SCHEME_BOUNDS[PeriodScheme.QUARTERS]
QUARTER_KEYS = tuple(key for key, _, _ in QUARTER_BOUNDS)
QUARTER_LABELS = {key: label for key, label, _ in QUARTER_BOUNDS}
# Все ключи всех схем: для проверки параметров запроса.
PERIOD_KEYS = frozenset(key for bounds in SCHEME_BOUNDS.values() for key, _, _ in bounds)
_PREFIX = {PeriodScheme.QUARTERS: "q", PeriodScheme.TRIMESTERS: "t", PeriodScheme.HALVES: "h"}


@dataclass(frozen=True, slots=True)
class Quarter:
    """Учебный период. Имя историческое: сначала были только четверти."""

    key: str
    label: str
    start: dt.date
    end: dt.date
    # «2/4» — номер и сколько всего периодов, для узкой колонки таблицы.
    short: str = ""
    # Последний учебный день по данным школы: `end` растянут до следующего
    # периода (см. periods_from_terms), а каникулы ищутся по настоящему.
    lessons_end: dt.date | None = None

    def covers(self, day: dt.date) -> bool:
        return self.start <= day <= self.end


Period = Quarter


def academic_year_start(today: dt.date) -> int:
    """Год, в котором начался текущий учебный год.

    До сентября мы всё ещё в учебном году, начавшемся прошлой осенью.
    """
    return today.year if today.month >= 9 else today.year - 1


def _chain(year: int, starts: list[tuple[str, str, dt.date]]) -> list[Quarter]:
    """Периоды подряд: каждый длится до начала следующего, последний — до конца августа."""
    starts = sorted(starts, key=lambda item: item[2])
    total = len(starts)
    result: list[Quarter] = []
    for index, (key, label, start) in enumerate(starts):
        if index + 1 < total:
            end = starts[index + 1][2] - dt.timedelta(days=1)
        else:
            end = dt.date(year + 1, 8, 31)
        result.append(Quarter(key, label, start, end, f"{index + 1}/{total}"))
    return result


def periods(
    today: dt.date,
    scheme: PeriodScheme = PeriodScheme.QUARTERS,
    starts: list[tuple[int, int]] | None = None,
) -> list[Quarter]:
    """Периоды учебного года, к которому относится день.

    `starts` — свои (месяц, день) начала каждого периода вместо общепринятых;
    используются, только если их ровно столько, сколько периодов в схеме.
    """
    year = academic_year_start(today)
    bounds = SCHEME_BOUNDS[scheme]
    if starts is not None and len(starts) != len(bounds):
        starts = None
    items: list[tuple[str, str, dt.date]] = []
    for index, (key, label, default) in enumerate(bounds):
        month, day = starts[index] if starts else default
        # Январь–август — это уже следующий календарный год.
        calendar_year = year if month >= 9 else year + 1
        try:
            items.append((key, label, dt.date(calendar_year, month, day)))
        except ValueError:
            return periods(today, scheme)
    return _chain(year, items)


def quarters(today: dt.date) -> list[Quarter]:
    """Общепринятые четверти учебного года."""
    return periods(today)


_TERM_NAME = re.compile(r"(\d)\s*[-‑]?\s*(?:[а-я]{1,2}\s+)?(четверт|триместр|полугод)", re.IGNORECASE)
_TERM_SCHEME = {"четверт": PeriodScheme.QUARTERS, "триместр": PeriodScheme.TRIMESTERS, "полугод": PeriodScheme.HALVES}


def term_key(name: Any) -> str | None:
    """Ключ периода по названию из «Сетевого города»: «1 четверть» → `q1`, «2 полугодие» → `h2`."""
    match = _TERM_NAME.search(str(name or ""))
    if not match:
        return None
    scheme = _TERM_SCHEME[match.group(2).lower()]
    number = int(match.group(1))
    key = f"{_PREFIX[scheme]}{number}"
    return key if key in PERIOD_KEYS else None


def periods_from_terms(terms: list[dict[str, Any]], today: dt.date) -> list[Quarter] | None:
    """Периоды школы из `/terms` мобильного API.

    Школа может вести и четверти, и полугодия сразу (для разных предметов),
    поэтому берётся самая подробная схема, где известны все периоды.
    None — данных не хватает, вызывающий берёт общепринятые даты.
    """
    by_scheme: dict[PeriodScheme, list[tuple[str, str, dt.date, dt.date]]] = {}
    for term in terms or []:
        key = term_key(term.get("name"))
        if key is None:
            continue
        try:
            start = dt.date.fromisoformat(str(term.get("startDate"))[:10])
            end = dt.date.fromisoformat(str(term.get("endDate"))[:10])
        except ValueError:
            continue
        scheme = next(s for s, prefix in _PREFIX.items() if key.startswith(prefix))
        by_scheme.setdefault(scheme, []).append((key, str(term.get("name")).strip(), start, end))

    for scheme in (PeriodScheme.QUARTERS, PeriodScheme.TRIMESTERS, PeriodScheme.HALVES):
        found = sorted(by_scheme.get(scheme, []), key=lambda item: item[2])
        if len(found) != len(SCHEME_BOUNDS[scheme]) or len({k for k, *_ in found}) != len(found):
            continue
        total = len(found)
        result = []
        for index, (key, label, start, end) in enumerate(found):
            # Каникулы между периодами приписываем предыдущему: оценка,
            # выставленная задним числом на каникулах, не должна потеряться.
            lessons_end = end
            if index + 1 < total:
                end = max(end, found[index + 1][2] - dt.timedelta(days=1))
            result.append(Quarter(key, label, start, end, f"{index + 1}/{total}", lessons_end))
        return result
    return None


def period_of(day: dt.date, year: list[Quarter]) -> str | None:
    """Ключ периода, в который попадает день. None — вне учебного года."""
    for item in year:
        if item.covers(day):
            return item.key
    return None


def quarter_of(day: dt.date, *, today: dt.date | None = None) -> str | None:
    """Ключ общепринятой четверти, в которую попадает день."""
    return period_of(day, quarters(today or day))


def current_quarter(today: dt.date) -> str:
    """Текущая четверть. На каникулах — ближайшая прошедшая.

    Возвращает всегда что-то: экран калькулятора должен открываться и
    летом, просто с последней четвертью.
    """
    found = quarter_of(today)
    if found:
        return found
    return QUARTER_KEYS[0]


def resolve_periods(setting: dict[str, Any] | None, terms: list[dict[str, Any]] | None, today: dt.date) -> list[Quarter]:
    """Периоды по настройке приложения (`prefs.periods`) и периодам школы.

    Тот же порядок, что у экрана «Итоги»: свои даты, периоды школы, общепринятые.
    """
    setting = setting or {}
    scheme_name = setting.get("scheme") or "auto"
    if scheme_name != "auto":
        try:
            scheme = PeriodScheme(scheme_name)
            starts = setting.get("starts")
            parsed = [tuple(int(x) for x in item.split("-")) for item in starts] if starts else None
            return periods(today, scheme, parsed)
        except (ValueError, TypeError):
            return periods(today)
    return periods_from_terms(terms or [], today) or periods(today)


def current_period(year: list[Quarter], today: dt.date) -> Quarter:
    """Текущий период; на каникулах — последний начавшийся."""
    for item in year:
        if item.covers(today):
            return item
    started = [item for item in year if item.start <= today]
    return started[-1] if started else year[0]
