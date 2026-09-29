"""Сравнение состояния оценок между проверками.

Это чистая функция без сети, файлов и Telegram — единственная причина, по
которой логику уведомлений вообще можно протестировать. В старом коде она
была частью метода `check_new_grades` на 568 строк вперемешку с HTTP-запросами
и отправкой сообщений, поэтому проверить её было нечем.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import Filters, MarkKind
from ..domain.records import MarkEvent, MarkRecord, TrackedMark, normalized_titles

# Сколько проверок подряд оценка должна отсутствовать, прежде чем считать её
# удалённой. «Сетевой город» стабильно отдаёт неполные ответы, и одна пропажа
# ничего не значит.
MISSING_STREAK_FOR_DELETE = 3

# Если в ответе меньше этой доли от известных оценок, ответ считается
# обрезанным и удаления в этом проходе не рассматриваются вовсе.
MIN_COMPLETENESS_RATIO = 0.7
MIN_COMPLETENESS_ABSOLUTE = 5

# Ограничители на один проход, чтобы сбой на стороне школы не превратился
# в лавину сообщений пользователю.
MAX_DELETES_PER_RUN = 10
MAX_NEW_PER_RUN = 50


@dataclass(slots=True)
class DiffResult:
    """Что изменилось и каким стало состояние слежения."""

    events: list[MarkEvent]
    tracked: dict[str, TrackedMark]
    # Ключи, для которых нужно подтвердить удаление отдельным запросом,
    # прежде чем сообщать пользователю.
    pending_deletes: list[MarkRecord]
    # Ответ выглядит обрезанным — удаления в этом проходе не рассматривались.
    response_looked_truncated: bool = False


def response_is_complete(known_count: int, current_count: int) -> bool:
    """Похож ли ответ сервера на полный.

    Резкое падение количества оценок означает, что «Сетевой город» отдал
    неполный дневник, а не что учитель разом стёр половину журнала.
    """
    if known_count == 0:
        return True
    minimum = max(MIN_COMPLETENESS_ABSOLUTE, int(known_count * MIN_COMPLETENESS_RATIO))
    return current_count >= minimum


def diff_marks(
    known: dict[str, TrackedMark],
    current: list[MarkRecord],
    *,
    filters: Filters,
    first_run: bool,
    notify_new: bool = True,
    notify_changes: bool = True,
    notify_deletes: bool = True,
) -> DiffResult:
    """Сравнить известные оценки с текущими.

    `first_run=True` означает, что для пользователя ещё нет истории: тогда всё
    найденное просто запоминается без единого уведомления, иначе человек при
    подключении получил бы весь журнал за год одним залпом.
    """
    if first_run:
        # Молчаливая пересборка — с чистого листа. Старые записи, которых нет
        # в новом ответе (после смены API их ключи другие), не переносим:
        # иначе они копили бы «пропажи» и через три прохода объявились бы
        # удалёнными оценками, которые на месте.
        known = {}
    current_map = _index(current, filters)
    events: list[MarkEvent] = []
    tracked: dict[str, TrackedMark] = {}
    pending_deletes: list[MarkRecord] = []

    for key, record in current_map.items():
        previous = known.get(key)
        tracked[key] = TrackedMark(record=record, missing_streak=0)

        if first_run:
            continue
        if previous is None:
            if notify_new and len(events) < MAX_NEW_PER_RUN:
                events.append(MarkEvent(kind=MarkKind.NEW, record=record))
            continue
        if previous.record.mark != record.mark:
            if notify_changes:
                events.append(
                    MarkEvent(
                        kind=MarkKind.CHANGED,
                        record=record,
                        old_mark=previous.record.mark,
                        new_mark=record.mark,
                    )
                )

    truncated = not response_is_complete(len(known), len(current_map))

    for key, previous in known.items():
        if key in current_map:
            continue
        if truncated:
            # Ответ обрезан: сохраняем оценку и счётчик как есть, ничего не
            # засчитываем в пропажи. Иначе пара неполных ответов подряд
            # «удалила» бы половину журнала.
            tracked[key] = previous
            continue

        streak = previous.missing_streak + 1
        tracked[key] = TrackedMark(record=previous.record, missing_streak=streak)

        if first_run or not notify_deletes:
            continue
        if not _passes_filters(previous.record, filters):
            continue
        if streak < MISSING_STREAK_FOR_DELETE:
            continue
        if len(pending_deletes) >= MAX_DELETES_PER_RUN:
            continue
        pending_deletes.append(previous.record)

    return DiffResult(
        events=events,
        tracked=tracked,
        pending_deletes=pending_deletes,
        response_looked_truncated=truncated,
    )


def confirm_deletions(
    result: DiffResult,
    confirmed_keys: set[str],
) -> list[MarkEvent]:
    """Превратить подтверждённые пропажи в события удаления.

    Неподтверждённым сбрасывается счётчик: оценка на месте, значит прошлые
    пропажи были дефектом ответа сервера.
    """
    events: list[MarkEvent] = []
    for record in result.pending_deletes:
        key = record.identity
        if key in confirmed_keys:
            events.append(MarkEvent(kind=MarkKind.DELETED, record=record))
            result.tracked.pop(key, None)
        elif key in result.tracked:
            result.tracked[key].missing_streak = 0
    return events


def diff_homework(
    known: set[str],
    current: list,
    *,
    first_run: bool,
) -> tuple[list, set[str]]:
    """Новые домашние задания и обновлённое множество известных."""
    current_keys = {item.identity for item in current}
    if first_run:
        return [], current_keys
    fresh = [item for item in current if item.identity not in known]
    return fresh, current_keys


def _index(records: list[MarkRecord], filters: Filters) -> dict[str, MarkRecord]:
    indexed: dict[str, MarkRecord] = {}
    for record in records:
        if not record.mark:
            continue
        if not _passes_filters(record, filters):
            continue
        indexed[record.identity] = record
    return indexed


def _passes_filters(record: MarkRecord, filters: Filters) -> bool:
    if not filters.allows_subject(record.subject):
        return False
    assignment_type, title = normalized_titles(record)
    return filters.allows_title(assignment_type, title)
