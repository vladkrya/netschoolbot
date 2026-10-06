"""Записи дневника и их идентичность.

Главная тонкость домена: API «Сетевого города» возвращает `assignment.id = 0`
для всех оценок, то есть стабильного идентификатора у оценки нет. Приходится
собирать составной ключ из полей урока и работы. Старый код собирал такие
ключи в трёх местах конкатенацией через `_` — из-за чего предмет с
подчёркиванием в названии мог склеиться с соседним полем и дать коллизию
(две разные работы получали один ключ и одна из них молча терялась).

Здесь ключ один, считается в одном месте и разделитель экранируется.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from .models import MarkKind, normalize

_SEP = "\x1f"  # ASCII unit separator: в данных «Сетевого города» не встречается

# Защита от циклических ссылок в объектах netschoolpy.
_MAX_MARK_DEPTH = 5


def _part(value: Any) -> str:
    """Одно поле составного ключа с экранированием разделителя."""
    text = str(value if value is not None else "").strip()
    return text.replace("\\", "\\\\").replace(_SEP, "\\u001f")


def _join(*parts: Any) -> str:
    return _SEP.join(_part(p) for p in parts)


def _time_text(value: Any) -> str:
    return value.strftime("%H:%M") if hasattr(value, "strftime") else _part(value)


@dataclass(frozen=True, slots=True)
class Attachment:
    id: int | None
    name: str


@dataclass(frozen=True, slots=True)
class MarkRecord:
    """Одна выставленная оценка."""

    subject: str
    date: dt.date
    assignment_type: str
    content: str
    mark: str
    weight: int = 1
    comment: str = ""
    lesson_number: int | None = None
    lesson_start: str = ""
    lesson_end: str = ""
    assignment_index: int = 0
    published_at: dt.datetime | None = field(default=None, compare=False)
    marked_at: dt.datetime | None = field(default=None, compare=False)

    @property
    def title(self) -> str:
        """`Контрольная работа Дроби` — тип работы плюс её содержание."""
        content = clean_content(self.content)
        return f"{self.assignment_type} {content}".strip() if content else self.assignment_type

    @property
    def identity(self) -> str:
        """Ключ работы без учёта оценки.

        По нему отслеживаются изменения и удаления: оценка в работе меняется,
        сама работа — нет.
        """
        return _join(
            self.title,
            self.subject,
            self.date.isoformat(),
            self.lesson_number,
            self.lesson_start,
            self.lesson_end,
            self.comment,
            self.assignment_index,
        )

    @property
    def loose_identity(self) -> str:
        """Ключ без порядкового номера работы внутри урока.

        Нужен для подтверждения удаления: при пересборке ответа сервер может
        отдать работы в другом порядке, и строгий ключ разойдётся, хотя
        оценка на месте.
        """
        return _join(
            self.title,
            self.subject,
            self.date.isoformat(),
            self.lesson_number,
            self.lesson_start,
            self.lesson_end,
            self.comment,
        )

    @property
    def numeric_mark(self) -> int | None:
        return mark_to_int(self.mark)


@dataclass(frozen=True, slots=True)
class HomeworkRecord:
    """Одно домашнее задание."""

    subject: str
    due_date: dt.date
    assignment_type: str
    text: str
    lesson_number: int | None = None
    attachments: tuple[Attachment, ...] = ()
    # Когда учитель опубликовал задание. Есть только в мобильном API; в
    # ключ не входит, чтобы смена режима не выдала старые задания за новые.
    published_at: dt.datetime | None = None
    # Вес задания и тема урока — для экрана задания; в ключ не входят.
    weight: int = 1
    topic: str = ""

    @property
    def identity(self) -> str:
        attachments = "|".join(
            f"{a.id or ''}:{a.name}"
            for a in sorted(self.attachments, key=lambda a: (str(a.id or ""), a.name))
        )
        return _join(
            self.subject,
            self.due_date.isoformat(),
            self.lesson_number,
            self.assignment_type,
            self.text,
            attachments,
        )

    @property
    def key(self) -> str:
        """Короткий отпечаток identity: влезает в callback_data Telegram и в адрес."""
        return hashlib.sha1(self.identity.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class RecentEvents:
    """Свежая лента событий мобильного API (`period-events`).

    Событие несёт мало полей: у оценки нет содержания работы, номера урока и
    комментария, поэтому собрать из него `MarkRecord.identity` так, чтобы он
    совпал с ключом из дневника, нельзя. Лента используется как дешёвый
    признак «что-то изменилось», а сами записи берутся из дневника.
    """

    # Устойчивые отпечатки событий: по ним видно, что появилось нового.
    keys: frozenset[str] = frozenset()
    marks: int = 0
    homework: int = 0


# Отметки посещаемости мобильного API (`attendance` урока).
ABSENCE_LABELS = {
    "Б": "Пропуск по болезни",
    "УП": "Пропуск по уважительной причине",
    "НП": "Пропуск без уважительной причины",
    "Н": "Пропуск",
    "ОТ": "Отсутствовал",
    "ОП": "Опоздал",
    "ОСВ": "Освобождён от посещения",
}


def absence_label(code: str) -> str:
    """Расшифровка отметки; неизвестный код показывается как есть."""
    return ABSENCE_LABELS.get(str(code or "").strip().upper(), str(code or "").strip())


@dataclass(frozen=True, slots=True)
class AbsenceRecord:
    """Отметка посещаемости за один урок."""

    subject: str
    date: dt.date
    lesson_number: int | None
    code: str

    @property
    def label(self) -> str:
        return absence_label(self.code)

    @property
    def identity(self) -> str:
        return _join(self.date.isoformat(), self.lesson_number, self.subject, self.code)


@dataclass(frozen=True, slots=True)
class MarkEvent:
    """Что произошло с оценкой между двумя проверками."""

    kind: MarkKind
    record: MarkRecord
    old_mark: str = ""
    new_mark: str = ""


@dataclass(slots=True)
class TrackedMark:
    """Оценка в состоянии слежения, вместе со счётчиком пропаж.

    `missing_streak` — сколько проверок подряд оценки нет в ответе сервера.
    Уведомление об удалении отправляется только после нескольких пропаж
    подряд, потому что «Сетевой город» регулярно отдаёт неполные ответы.
    """

    record: MarkRecord
    missing_streak: int = 0


@dataclass(frozen=True, slots=True)
class Lesson:
    """Урок в расписании дня.

    Раньше день нёс только оценки и задания вперемешку, без привязки к
    уроку. Экран дневника показывает именно расписание — с номером,
    временем, кабинетом и тем, что на этом уроке получили и задали, —
    поэтому урок стал отдельной записью.
    """

    number: int | None
    subject: str
    start: str = ""
    end: str = ""
    room: str = ""
    teacher: str = ""
    topic: str = ""
    absence: str = ""
    marks: tuple[MarkRecord, ...] = ()
    homework: tuple[HomeworkRecord, ...] = ()
    # id заданий урока: учителя дневник не отдаёт, его приходится брать
    # из карточки любого задания (student/diary/assigns/{id}).
    assignment_ids: tuple[int, ...] = ()
    is_elective: bool = False

    @property
    def time_range(self) -> str:
        if self.start and self.end:
            return f"{self.start}–{self.end}"
        return self.start or ""


@dataclass(slots=True)
class DiaryDay:
    day: dt.date
    lessons: list[Lesson] = field(default_factory=list)
    marks: list[MarkRecord] = field(default_factory=list)
    homework: list[HomeworkRecord] = field(default_factory=list)


def clean_content(content: Any) -> str:
    """Убрать служебные заглушки «Сетевого города» из текста задания."""
    text = str(content or "").strip()
    if text in {"---Не указана---", "Не указана"}:
        return ""
    return text.replace("---Не указана---", "").strip()


def extract_mark(source: Any, _depth: int = 0) -> str:
    """Достать значение оценки из чего угодно, что отдаёт netschoolpy.

    Оценка приезжает то объектом с `textMark`, то словарём, то числом.
    Возвращается строка (`""` — оценки нет), чтобы дальше по коду не было
    трёх разных «пустых» значений: None, "" и 0.
    """
    if source is None or _depth > _MAX_MARK_DEPTH:
        return ""

    if isinstance(source, bool):
        # bool — подкласс int, но оценкой быть не может.
        return ""
    if isinstance(source, (int, float)):
        return str(source)
    if isinstance(source, str):
        return source.strip()

    if isinstance(source, dict):
        for key in ("textMark", "mark", "value", "name"):
            value = source.get(key)
            if value not in (None, "", {}):
                return extract_mark(value, _depth + 1)
        return ""

    text_mark = getattr(source, "textMark", None) or getattr(source, "mark_text", None) or getattr(source, "special_mark_code", None)
    if text_mark:
        return extract_mark(text_mark, _depth + 1)

    # Объект-задание: оценка лежит в поле `mark`. Пустое поле означает, что
    # оценки нет — возвращаем "", а не строковое представление объекта.
    if hasattr(source, "mark"):
        inner = getattr(source, "mark")
        return "" if inner is source else extract_mark(inner, _depth + 1)

    return str(source).strip()


def mark_to_int(mark: Any) -> int | None:
    """Числовое значение оценки (1..10) или None для «зачтено», «н/а» и пустых."""
    value = extract_mark(mark)
    if not value:
        return None
    match = re.search(r"\d+", value)
    if not match:
        return None
    number = int(match.group(0))
    return number if 1 <= number <= 10 else None


def parse_mark_float(mark: Any) -> float | None:
    """Оценка как число с плавающей точкой, для средних баллов."""
    value = extract_mark(mark).replace(",", ".")
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if 1.0 <= number <= 10.0 else None


def normalized_titles(record: MarkRecord) -> tuple[str, str]:
    """Тип работы и полное название — для проверки по фильтру исключений."""
    return normalize(record.assignment_type), normalize(record.title)
