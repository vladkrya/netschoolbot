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


class DigestKind(str, Enum):
    EVENING = "evening"
    WEEKLY = "weekly"


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
