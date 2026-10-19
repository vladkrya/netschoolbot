"""Что задано: параграфы, номера упражнений и страницы из текста задания.

Учителя пишут как придётся: «п.13,№289(б,г),№290(б)», «§5 читать, письменно
упр. 2-3», «пар. 38», «упр. 48 стр.  47», «§6, стр. 50-59». Разбираем это в
списки чисел, по которым потом ищутся страницы учебника.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Диапазон «2-3» / «50–59» — не больше стольких номеров, иначе «1-500» —
# это не задание, а опечатка.
MAX_RANGE = 15


@dataclass(slots=True)
class Exercise:
    number: int
    # Пункты «(б,г)» — как написал учитель; пусто — всё упражнение.
    parts: tuple[str, ...] = ()

    def label(self) -> str:
        return f"№{self.number}" + (f"({','.join(self.parts)})" if self.parts else "")


@dataclass(slots=True)
class Refs:
    paragraphs: list[int] = field(default_factory=list)
    exercises: list[Exercise] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    # «повторить 38-44» — читать, но не решать.
    review: list[int] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.paragraphs or self.exercises or self.pages)


_NUM = r"\d{1,4}"
_RANGE = rf"({_NUM})(?:\s*[-–—]\s*({_NUM}))?"
_PARAGRAPH = re.compile(rf"(?:§|\bп\s*\.|\bпп\s*\.|\bпар(?:аграф)?\s*\.?|\bпункт)\s*{_RANGE}", re.I)
_PAGE = re.compile(rf"\bстр(?:аниц[аые]?)?\s*\.?\s*{_RANGE}", re.I)
# «№289(б,г)», «№ 946 (а)», «упр. 48», «упражнение 2-3», «задача 12», «зад. 5».
_EXERCISE = re.compile(
    rf"(?:№|\bупр(?:ажнени[ея])?\s*\.?|\bзадач[аи]\s*|\bзад\s*\.)\s*{_RANGE}\s*(?:\(([^)]{{1,40}})\))?", re.I
)
# Номера через запятую после «№»: «№ 12, 14, 15» — продолжение того же списка.
_MORE = re.compile(rf"\s*,\s*({_NUM})\s*(?:\(([^)]{{1,40}})\))?")
_REVIEW = re.compile(rf"\bповтор\w*\s+(?:§|п\s*\.|пар\s*\.?)?\s*{_RANGE}", re.I)


def _span(first: str, last: str | None) -> list[int]:
    a = int(first)
    b = int(last) if last else a
    if b < a or b - a >= MAX_RANGE:
        return [a]
    return list(range(a, b + 1))


def _parts(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(p.strip() for p in re.split(r"[,;\s]+", raw) if p.strip())


def _add(items: list[int], values: list[int]) -> None:
    for value in values:
        if value not in items:
            items.append(value)


def parse(text: str) -> Refs:
    refs = Refs()
    text = text or ""
    # «повторить 38-44» вырезаем, чтобы эти числа не стали параграфами.
    for match in _REVIEW.finditer(text):
        _add(refs.review, _span(match.group(1), match.group(2)))
    text = _REVIEW.sub(" ", text)
    for match in _PAGE.finditer(text):
        _add(refs.pages, _span(match.group(1), match.group(2)))
    text = _PAGE.sub(" ", text)
    for match in _EXERCISE.finditer(text):
        parts = _parts(match.group(3))
        for number in _span(match.group(1), match.group(2)):
            refs.exercises.append(Exercise(number, parts))
        pos = match.end()
        while more := _MORE.match(text, pos):
            refs.exercises.append(Exercise(int(more.group(1)), _parts(more.group(2))))
            pos = more.end()
    text = _EXERCISE.sub(" ", text)
    for match in _PARAGRAPH.finditer(text):
        _add(refs.paragraphs, _span(match.group(1), match.group(2)))
    return refs
