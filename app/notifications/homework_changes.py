"""Снимок текста, комментария и вложений, привязанный к школьному ID задания."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..domain.models import User
from ..domain.records import HomeworkRecord


@dataclass(frozen=True, slots=True)
class HomeworkPoll:
    user: User
    items: list[HomeworkRecord]
    fresh: list[HomeworkRecord]
    notify: bool


def changes_between(before: dict, after: dict) -> dict:
    changes = {}
    if before["text"] != after["text"]:
        changes.update(old_text=before["text"], new_text=after["text"])

    previous_comment, current_comment = before.get("comment"), after.get("comment")
    if previous_comment is not None and current_comment is not None and previous_comment != current_comment:
        changes.update(old_comment=previous_comment, new_comment=current_comment)

    old = Counter(map(tuple, before["files"]))
    new = Counter(map(tuple, after["files"]))
    for key, difference in (("added_files", new - old), ("removed_files", old - new)):
        if difference:
            changes[key] = [name for _, name in sorted(difference.elements())]
    return changes


def homework_snapshot(item: HomeworkRecord) -> dict:
    return {
        "text": item.text,
        "comment": item.comment,
        "files": sorted((str(a.id or ""), a.name) for a in item.attachments),
    }
