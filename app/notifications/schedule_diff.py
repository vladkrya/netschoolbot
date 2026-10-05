"""Изменения расписания: замены, отменённые и добавленные уроки, смена кабинета.

Чистые функции, как и diff.py: снимок дня — список (номер, предмет,
кабинет), сравниваются снимки одного дня из разных проверок.
«Сетевой город» иногда отдаёт день пустым — это сбой ответа, а не отмена
всех уроков, поэтому пустой день не сравнивается.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..domain.records import DiaryDay

# Сколько дней вперёд следим: дальше расписание ещё не устоялось.
WINDOW_DAYS = 7
# Больше стольких изменений в дне — это перестройка, а не замена:
# одна строка «расписание изменилось» вместо простыни.
MAX_DETAILED = 4

Snapshot = list[tuple[int, str, str]]


@dataclass(frozen=True, slots=True)
class ScheduleChange:
    day: dt.date
    number: int
    # replaced | cancelled | added | room | reshuffled
    kind: str
    old: str = ""
    new: str = ""


def snapshot(day: DiaryDay) -> Snapshot:
    """Уроки дня в сравнимом виде. Уроки без номера нумеруются по порядку."""
    lessons = sorted(day.lessons, key=lambda l: (l.number or 0, l.start))
    result: Snapshot = []
    for index, lesson in enumerate(lessons, start=1):
        result.append((lesson.number or index, lesson.subject.strip(), (lesson.room or "").strip()))
    return result


def watched_days(days: list[DiaryDay], today: dt.date) -> dict[dt.date, Snapshot]:
    """Снимки дней от сегодня на неделю вперёд."""
    last = today + dt.timedelta(days=WINDOW_DAYS)
    return {day.day: snapshot(day) for day in days if today <= day.day <= last}


def diff_day(when: dt.date, old: Snapshot, new: Snapshot) -> list[ScheduleChange]:
    if not old or not new:
        return []
    before: dict[int, tuple[str, str]] = {}
    after: dict[int, tuple[str, str]] = {}
    for number, subject, room in old:
        before.setdefault(number, (subject, room))
    for number, subject, room in new:
        after.setdefault(number, (subject, room))

    changes: list[ScheduleChange] = []
    for number in sorted(set(before) | set(after)):
        was, now = before.get(number), after.get(number)
        if was == now:
            continue
        if now is None:
            changes.append(ScheduleChange(when, number, "cancelled", old=was[0]))
        elif was is None:
            changes.append(ScheduleChange(when, number, "added", new=now[0]))
        elif was[0] != now[0]:
            changes.append(ScheduleChange(when, number, "replaced", old=was[0], new=now[0]))
        elif was[1] != now[1] and now[1]:
            changes.append(ScheduleChange(when, number, "room", old=was[1], new=now[1]))
    if len(changes) > MAX_DETAILED:
        return [ScheduleChange(when, 0, "reshuffled")]
    return changes


def diff_schedule(
    known: dict[dt.date, Snapshot], current: dict[dt.date, Snapshot]
) -> list[ScheduleChange]:
    """Изменения по всем дням, которые видели раньше. Новые дни — без уведомлений."""
    changes: list[ScheduleChange] = []
    for when in sorted(current):
        if when in known:
            changes.extend(diff_day(when, known[when], current[when]))
    return changes
