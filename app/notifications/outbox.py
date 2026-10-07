"""Очередь исходящих уведомлений: формат хранения.

Уведомление попадает в базу той же транзакцией, что и новое состояние
слежения, и удаляется только после успешной отправки. Так недоступный
Telegram или тихие часы откладывают сообщение, а не теряют его.

Здесь только перевод записей дневника в JSON и обратно — без базы и сети.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..domain.models import MarkKind
from .schedule_diff import ScheduleChange
from ..domain.records import (
    AbsenceRecord,
    Attachment,
    HomeworkRecord,
    MarkEvent,
    MarkRecord,
)


class OutboxKind(str, Enum):
    MARKS = "marks"
    HOMEWORK = "homework"
    ABSENCES = "absences"
    SCHEDULE = "schedule"
    MAIL = "mail"


@dataclass(frozen=True, slots=True)
class OutboxItem:
    """Одно отложенное уведомление: пачка событий одного вида."""

    id: int
    kind: OutboxKind
    items: list
    created_at: dt.datetime
    attempts: int = 0


def mark_to_dict(record: MarkRecord) -> dict[str, Any]:
    return {
        "subject": record.subject,
        "date": record.date.isoformat(),
        "assignment_type": record.assignment_type,
        "content": record.content,
        "mark": record.mark,
        "weight": record.weight,
        "comment": record.comment,
        "lesson_number": record.lesson_number,
        "lesson_start": record.lesson_start,
        "lesson_end": record.lesson_end,
        "assignment_index": record.assignment_index,
        "published_at": record.published_at.isoformat() if record.published_at else None,
        "marked_at": record.marked_at.isoformat() if record.marked_at else None,
    }


def mark_from_dict(data: dict[str, Any]) -> MarkRecord:
    return MarkRecord(
        subject=data["subject"],
        date=dt.date.fromisoformat(data["date"]),
        assignment_type=data["assignment_type"],
        content=data.get("content", ""),
        mark=data.get("mark", ""),
        weight=int(data.get("weight") or 1),
        comment=data.get("comment", ""),
        lesson_number=data.get("lesson_number"),
        lesson_start=data.get("lesson_start", ""),
        lesson_end=data.get("lesson_end", ""),
        assignment_index=int(data.get("assignment_index") or 0),
        published_at=dt.datetime.fromisoformat(data["published_at"]) if data.get("published_at") else None,
        marked_at=dt.datetime.fromisoformat(data["marked_at"]) if data.get("marked_at") else None,
    )


def _event_to_dict(event: MarkEvent) -> dict[str, Any]:
    return {
        "kind": event.kind.value,
        "record": mark_to_dict(event.record),
        "old_mark": event.old_mark,
        "new_mark": event.new_mark,
    }


def _event_from_dict(data: dict[str, Any]) -> MarkEvent:
    return MarkEvent(
        kind=MarkKind(data["kind"]),
        record=mark_from_dict(data["record"]),
        old_mark=data.get("old_mark", ""),
        new_mark=data.get("new_mark", ""),
    )


def _homework_to_dict(item: HomeworkRecord) -> dict[str, Any]:
    return {
        "subject": item.subject,
        "due_date": item.due_date.isoformat(),
        "assignment_type": item.assignment_type,
        "text": item.text,
        "lesson_number": item.lesson_number,
        "attachments": [{"id": a.id, "name": a.name} for a in item.attachments],
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "weight": item.weight,
        "topic": item.topic,
    }


def _homework_from_dict(data: dict[str, Any]) -> HomeworkRecord:
    published = data.get("published_at")
    return HomeworkRecord(
        subject=data["subject"],
        due_date=dt.date.fromisoformat(data["due_date"]),
        assignment_type=data.get("assignment_type", ""),
        text=data.get("text", ""),
        lesson_number=data.get("lesson_number"),
        attachments=tuple(
            Attachment(id=a.get("id"), name=a.get("name", "")) for a in data.get("attachments", [])
        ),
        published_at=dt.datetime.fromisoformat(published) if published else None,
        weight=int(data.get("weight") or 1),
        topic=data.get("topic", ""),
    )


def _absence_to_dict(item: AbsenceRecord) -> dict[str, Any]:
    return {
        "subject": item.subject,
        "date": item.date.isoformat(),
        "lesson_number": item.lesson_number,
        "code": item.code,
    }


def _absence_from_dict(data: dict[str, Any]) -> AbsenceRecord:
    return AbsenceRecord(
        subject=data["subject"],
        date=dt.date.fromisoformat(data["date"]),
        lesson_number=data.get("lesson_number"),
        code=data.get("code", ""),
    )


def _schedule_to_dict(item: ScheduleChange) -> dict[str, Any]:
    return {"day": item.day.isoformat(), "number": item.number, "kind": item.kind, "old": item.old, "new": item.new}


def _schedule_from_dict(data: dict[str, Any]) -> ScheduleChange:
    return ScheduleChange(
        day=dt.date.fromisoformat(data["day"]),
        number=int(data.get("number") or 0),
        kind=data["kind"],
        old=data.get("old", ""),
        new=data.get("new", ""),
    )


_ENCODERS = {
    OutboxKind.MARKS: _event_to_dict,
    OutboxKind.HOMEWORK: _homework_to_dict,
    OutboxKind.ABSENCES: _absence_to_dict,
    OutboxKind.SCHEDULE: _schedule_to_dict,
    OutboxKind.MAIL: dict,
}
_DECODERS = {
    OutboxKind.MARKS: _event_from_dict,
    OutboxKind.HOMEWORK: _homework_from_dict,
    OutboxKind.ABSENCES: _absence_from_dict,
    OutboxKind.SCHEDULE: _schedule_from_dict,
    OutboxKind.MAIL: dict,
}


def encode(kind: OutboxKind, items: list) -> str:
    return json.dumps([_ENCODERS[kind](item) for item in items], ensure_ascii=False)


def decode(kind: OutboxKind, payload: str) -> list:
    """Разобрать пачку. Ошибка формата поднимается: вызывающий решает, что с ней делать."""
    return [_DECODERS[kind](item) for item in json.loads(payload)]
