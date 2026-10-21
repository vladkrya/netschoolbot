"""Разбор недели от ИИ-помощника для понедельничной сводки.

Все числа (средние, что выходит, сколько пятёрок нужно) считаются здесь:
модель только пересказывает готовые факты живым языком и советует, на чём
сосредоточиться. Имя ученика и школа в модель не уходят.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .insights import grade_for, needed_marks, thresholds_for, weighted
from .records import HomeworkRecord, MarkRecord

SYSTEM_PROMPT = """\
Ты — школьный помощник. По фактам об оценках ученика за прошлую неделю и \
текущую четверть напиши короткий разбор для родителя и ученика: где просел, \
что подтянуть в первую очередь и к каким работам готовиться на этой неделе. \
3–5 коротких предложений, без приветствий, без списков из одного пункта, без \
выдуманных фактов и чисел — бери только то, что дано. Пиши по-русски, \
доброжелательно и конкретно. Без Markdown."""

MAX_REVIEW = 900


def _fives(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "пятёрка"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "пятёрки"
    return "пятёрок"


def format_average(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _by_subject(records: list[MarkRecord]) -> dict[str, list[MarkRecord]]:
    result: dict[str, list[MarkRecord]] = {}
    for record in records:
        if record.numeric_mark is not None:
            result.setdefault(record.subject, []).append(record)
    return result


def review_facts(
    week: list[MarkRecord],
    period: list[MarkRecord],
    prefs: dict[str, Any] | None,
    tests: list[HomeworkRecord],
    *,
    period_label: str = "",
) -> str | None:
    """Факты для модели. None — разбирать нечего (ни оценок, ни работ)."""
    lines: list[str] = []
    week_subjects = _by_subject(week)
    if week_subjects:
        lines.append("Оценки за прошлую неделю:")
        for subject, items in sorted(week_subjects.items()):
            marks = ", ".join(r.mark for r in sorted(items, key=lambda r: r.date))
            lines.append(f"- {subject}: {marks}")
    else:
        lines.append("За прошлую неделю оценок не было.")

    period_subjects = _by_subject(period)
    weak, edge = [], []
    for subject, items in sorted(period_subjects.items()):
        average = weighted(items)
        if average is None:
            continue
        thresholds = thresholds_for(prefs, subject)
        grade = grade_for(average, thresholds)
        before = weighted([r for r in items if r not in week])
        trend = ""
        if before is not None and subject in week_subjects:
            delta = average - before
            if abs(delta) >= 0.05:
                trend = f", за неделю {'вырос' if delta > 0 else 'упал'} на {format_average(abs(delta))}"
        line = f"- {subject}: средний {format_average(average)}, выходит «{grade}»{trend}"
        if grade < 5:
            target = thresholds[str(grade + 1)]
            need = needed_marks(items, target)
            if need:
                line += f"; до «{grade + 1}» нужно ещё {need} {_fives(need)}"
        if grade <= 3:
            weak.append(line)
        elif average - thresholds[str(grade)] < 0.15:
            edge.append(line + " (на грани)")
    if weak or edge:
        lines.append(f"Текущий период{f' ({period_label})' if period_label else ''} — что требует внимания:")
        lines.extend(weak + edge)

    if tests:
        lines.append("Проверочные и контрольные на этой неделе:")
        for item in tests:
            lines.append(f"- {item.due_date:%d.%m}, {item.subject}: {item.assignment_type or 'работа'}"
                         + (f" (вес {item.weight:g})" if item.weight and item.weight > 1 else ""))

    if not week_subjects and not tests and not weak and not edge:
        return None
    return "\n".join(lines)


def clean_review(text: str) -> str:
    """Ответ модели — в сводку: без Markdown-звёздочек и не длиннее предела."""
    text = text.replace("**", "").replace("__", "").strip()
    if len(text) > MAX_REVIEW:
        cut = text[:MAX_REVIEW].rsplit(". ", 1)[0]
        text = cut + "."
    return text


def week_ahead(day: dt.date) -> list[dt.date]:
    """Учебные дни недели, начинающейся с `day` (понедельник сводки)."""
    return [day + dt.timedelta(days=offset) for offset in range(6)]
