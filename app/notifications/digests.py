"""Сводки по расписанию: вечером — на завтра, в понедельник — за неделю.

Когда пора слать, решают чистые функции по местному времени пользователя;
отметка о разосланном периоде хранится в базе, поэтому перезапуск бота не
присылает ту же сводку дважды.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

# Вечерняя сводка — с этого часа и до конца окна; позже уже не актуально.
EVENING_FROM = dt.time(19, 0)
EVENING_UNTIL = dt.time(22, 30)
# Недельная — в понедельник утром, но не раньше этого часа.
WEEKLY_FROM = dt.time(8, 0)


# Напоминание о заданиях — с выбранного часа и до этого времени.
REMINDER_UNTIL = dt.time(22, 30)
# Месячная сводка — в первые дни месяца (если бот лежал 1-го, придёт позже).
MONTHLY_DAYS = 3
# Итоги периода — в течение недели после его конца.
TERM_DAYS = 7


class DigestKind(str, Enum):
    EVENING = "evening"
    WEEKLY = "weekly"
    REMINDER = "reminder"
    MONTHLY = "monthly"
    TERM = "term"


def next_school_day(today: dt.date) -> dt.date:
    """Следующий будний день: с пятницы и субботы — понедельник."""
    day = today + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day


def evening_period(now: dt.datetime) -> str | None:
    """День, на который пора прислать вечернюю сводку, или None.

    Только вечером накануне учебного дня: в пятницу и субботу вечером
    сводку на понедельник рано, её пришлют в воскресенье.
    """
    if not EVENING_FROM <= now.time() <= EVENING_UNTIL:
        return None
    tomorrow = now.date() + dt.timedelta(days=1)
    if tomorrow.weekday() >= 5:
        return None
    return tomorrow.isoformat()


def weekly_period(now: dt.datetime) -> str | None:
    """Понедельник недели, за которую пора прислать сводку прошлой недели."""
    if now.weekday() != 0 or now.time() < WEEKLY_FROM:
        return None
    return now.date().isoformat()


def previous_week(monday: dt.date) -> tuple[dt.date, dt.date]:
    start = monday - dt.timedelta(days=7)
    return start, start + dt.timedelta(days=6)


def reminder_period(now: dt.datetime, hour: int) -> str | None:
    """Учебный день, о заданиях на который пора напомнить, или None.

    Как и вечерняя сводка — только накануне учебного дня.
    """
    if not dt.time(hour, 0) <= now.time() <= REMINDER_UNTIL:
        return None
    tomorrow = now.date() + dt.timedelta(days=1)
    if tomorrow.weekday() >= 5:
        return None
    return tomorrow.isoformat()


def monthly_period(now: dt.datetime) -> str | None:
    """Первое число прошлого месяца, если пора прислать сводку за него."""
    if now.day > MONTHLY_DAYS or (now.day == 1 and now.time() < WEEKLY_FROM):
        return None
    last = now.date().replace(day=1) - dt.timedelta(days=1)
    return last.replace(day=1).isoformat()


def month_bounds(first: dt.date) -> tuple[dt.date, dt.date]:
    following = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return first, following - dt.timedelta(days=1)


def ended_period(now: dt.datetime, year: list) -> object | None:
    """Учебный период, который закончился за последние дни, или None.

    Последний период года (до конца августа) итогов не получает: учебный год
    заканчивается раньше его формального конца, а итоги года ставит школа.
    """
    today = now.date()
    if now.time() < WEEKLY_FROM:
        return None
    for index, item in enumerate(year):
        if index + 1 == len(year):
            break
        if item.end < today <= item.end + dt.timedelta(days=TERM_DAYS):
            return item
    return None
