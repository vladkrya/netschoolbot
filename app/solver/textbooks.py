"""Учебники класса: текст страниц и поиск задания по номеру.

Учебники лежат вне git, в `<data_dir>/textbooks/`:

    library.json        — какие книги к каким предметам;
    <id>.pages.json     — текст страниц, его готовит `python -m app.tools.index_textbook`;
    <id>/0000.jpg …     — только у сканов: страницы картинками (1600 px, серые);
    <id>.lines.json     — у сканов: строки с местом на странице, чтобы вырезать номер.

library.json:

    {"grade": 9,
     "books": [{"id": "algebra9", "subjects": ["Алгебра"],
                "title": "Алгебра. 9 класс. Макарычев и др."}]}

Индекс: {"title", "offset", "pages": [текст pdf-страницы 0, 1, …],
"toc": [[заголовок, индекс страницы], …], "answers_from": индекс или null}.
offset — сколько прибавить к индексу pdf-страницы, чтобы получить номер,
напечатанный на странице («стр. 47» в задании — это напечатанный номер).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .refs import Refs

logger = logging.getLogger("netschoolbot.solver")

# Сколько страниц учебника уходит в модель на одно задание: параграф истории —
# это 4–6 страниц, а больше модели не нужно и дорого.
MAX_PAGES = 10
MAX_PARAGRAPH_PAGES = 7
# Ответ из конца учебника — строка вокруг номера, не вся страница.
ANSWER_SNIPPET = 400
# Ширина вырезки упражнения. Провайдер считает картинку по её размеру, и
# вырезки во всю ширину скана (1600 px) делали решение в десятки раз дороже
# текстового; 1200 px — шрифт всё ещё крупнее, чем на целой странице.
CROP_WIDTH = 1200


@dataclass(slots=True)
class Book:
    id: str
    title: str
    subjects: tuple[str, ...]
    pages: list[str] = field(default_factory=list)
    offset: int = 0
    toc: list[tuple[str, int]] = field(default_factory=list)
    answers_from: int | None = None
    # Скан: страницы картинками в <id>/0000.jpg. Текст у него из распознавания —
    # им ищем, а условия упражнений модель читает с картинки: формулы
    # распознавание портит.
    images: Path | None = None
    # Строки скана с местом на странице: [[текст, x0, y0, x1, y1], …] на
    # страницу, доли от 0 до 1, y сверху вниз (<id>.lines.json). По ним из
    # страницы вырезается одно упражнение.
    lines: list[list[list]] = field(default_factory=list)

    def image(self, index: int) -> bytes | None:
        if self.images is None:
            return None
        try:
            return (self.images / f"{index:04d}.jpg").read_bytes()
        except OSError:
            return None

    def _band(self, index: int, number: int, *, continued: bool) -> tuple[float, float] | None:
        """Где на странице упражнение: от его номера до следующего номера.
        continued — продолжение с прошлой страницы: от верха до следующего."""
        if index >= len(self.lines):
            return None
        page = str(self.printed(index))
        rows = [row for row in self.lines[index] if str(row[0]).strip() != page]  # без колонтитула
        own = re.compile(rf"^\s*{number}\s*(?:[.)]|$|\s)")
        following = re.compile(rf"^\s*{number + 1}\s*(?:[.)]|$|\s)")
        if continued:
            top = 0.0
        else:
            starts = [row[2] for row in rows if own.match(str(row[0]))]
            if not starts:
                # Номер не распознался («94O»): берём от предыдущего номера.
                previous = re.compile(rf"^\s*{number - 1}\s*(?:[.)]|$|\s)")
                starts = [row[2] for row in rows if previous.match(str(row[0]))]
            if not starts:
                return None
            top = min(starts)
        ends = [row[2] for row in rows if following.match(str(row[0])) and row[2] > top + 0.01]
        bottom = min(ends) if ends else 1.0
        if continued and bottom < 0.1:
            return None  # следующий номер в самом верху — продолжения нет
        return max(0.0, top - 0.012), min(1.0, max(bottom - 0.004, top + 0.06))

    def crop(self, index: int, number: int, *, continued: bool = False) -> bytes | None:
        """Упражнение со скана крупно: полоса страницы во всю ширину.
        Целая страница в 1000 px — и модель путает x³ с x²."""
        image = self.image(index)
        band = self._band(index, number, continued=continued) if image else None
        if image is None or band is None:
            return None
        import io

        from PIL import Image

        with Image.open(io.BytesIO(image)) as page:
            width, height = page.size
            part = page.crop((0, int(band[0] * height), width, int(band[1] * height))).convert("L")
            if part.width > CROP_WIDTH:
                part = part.resize((CROP_WIDTH, round(part.height * CROP_WIDTH / part.width)), Image.LANCZOS)
            out = io.BytesIO()
            part.save(out, "JPEG", quality=55, optimize=True)
        return out.getvalue()

    # ─────────────────────────── поиск ───────────────────────────

    def printed(self, index: int) -> int:
        return index + self.offset

    def page_index(self, printed: int) -> int | None:
        index = printed - self.offset
        return index if 0 <= index < len(self.pages) else None

    def _body(self) -> range:
        """Страницы с задачами — без раздела «Ответы» в конце."""
        return range(self.answers_from if self.answers_from is not None else len(self.pages))

    def exercise_page(self, number: int, within: list[int] | None = None) -> int | None:
        """Где начинается упражнение: «289.», «289)» в начале строки или
        «938» отдельной строкой — так распознаётся номер в рамке.

        within — где искать сначала: «§5 … упр. 2-3» — это номера внутри
        параграфа, а не второе упражнение книги."""
        for index in [*(within or []), *self._body()]:
            if self.has_exercise(index, number):
                return index
        return None

    @staticmethod
    def _exercise_pattern(number: int) -> re.Pattern[str]:
        return re.compile(rf"(?m)^\s*{number}\s*(?:[.)]\s*\S|$)")

    def has_exercise(self, index: int, number: int) -> bool:
        """Есть ли на странице начало упражнения. Смотрим и строки скана: в
        тексте страницы распознавание порой теряет номер («62. a) Read…»).
        Число отдельной строкой, равное номеру страницы, — колонтитул."""
        texts = [self.pages[index]]
        if index < len(self.lines):
            texts.append("\n".join(str(row[0]) for row in self.lines[index]))
        pattern = self._exercise_pattern(number)
        footer = number == self.printed(index)
        for text in texts:
            for match in pattern.finditer(text):
                if not (footer and match.group().strip() == str(number)):
                    return True
        return False

    def answer(self, number: int) -> str:
        """Ответ к номеру из раздела «Ответы», если он есть."""
        if self.answers_from is None:
            return ""
        pattern = re.compile(rf"(?<![\d.,]){number}\s*[.)]\s*")
        for index in range(self.answers_from, len(self.pages)):
            if match := pattern.search(self.pages[index]):
                text = self.pages[index][match.start():match.start() + ANSWER_SNIPPET]
                # До следующего номера: «289. а) 2; б) -3. 290. …».
                if nxt := re.search(rf"(?<![\d.,]){number + 1}\s*[.)]", text):
                    text = text[:nxt.start()]
                return " ".join(text.split())
        return ""

    def paragraph_pages(self, number: int) -> list[int]:
        """Страницы параграфа: от его заголовка до заголовка следующего."""
        start = self._toc_page(number)
        if start is None:
            start = self._heading_page(number)
        if start is None:
            return []
        end = self._toc_page(number + 1)
        if end is None:
            end = self._heading_page(number + 1, after=start)
        if end is None or end <= start:
            end = start + 1
        # Следующий параграф может начаться посреди страницы — её берём тоже,
        # там конец нашего и часто вопросы к нему.
        return list(range(start, min(end + 1, start + MAX_PARAGRAPH_PAGES, len(self.pages))))

    def _toc_page(self, number: int) -> int | None:
        pattern = re.compile(rf"^\s*(?:§\s*|п\.\s*|глава\s+)?{number}(?:\.|\s|$)", re.I)
        for title, index in self.toc:
            if pattern.match(title) and 0 <= index < len(self.pages):
                return index
        return None

    def _heading_page(self, number: int, after: int = -1) -> int | None:
        section = re.compile(rf"§\s*{number}(?!\d)")
        for index in self._body():
            if index > after and section.search(self.pages[index]):
                return index
        # Без «§»: короткая строка «13. Дробные рациональные уравнения» —
        # заголовок пункта, а не длинное условие упражнения.
        heading = re.compile(rf"(?m)^\s*{number}\.\s+[А-ЯЁ][^\n]{{3,70}}$")
        for index in self._body():
            if index <= after:
                continue
            for match in heading.finditer(self.pages[index]):
                line = match.group(0).strip()
                if not line.endswith((".", ":", "?")) and not re.search(r"\d", line[len(str(number)) + 1:]):
                    return index
        return None

    def topic_section(self, topic: str) -> tuple[str, list[int]] | None:
        """Параграф по теме урока — когда учитель нумерует параграфы по-своему:
        «§6» при «§ 2.4. Табличные информационные модели» в книге. Заголовок
        из оглавления и страницы."""
        words = _stems(topic)
        if not words or not self.toc:
            return None
        best, score = None, 0.0
        for position, (title, index) in enumerate(self.toc):
            overlap = len(words & _stems(title)) / len(words)
            if overlap > score:
                best, score = position, overlap
        if best is None or score < 0.6:
            return None
        title, start = self.toc[best]
        following = [i for _, i in self.toc[best + 1:] if i > start]
        end = following[0] if following else start + 1
        return title, list(range(start, min(end + 1, start + MAX_PARAGRAPH_PAGES, len(self.pages))))

    def find(self, refs: Refs, topic: str = "") -> Found:
        """Страницы для задания, где из них упражнения, и ответы из конца книги."""
        found = Found()

        def add(*indexes: int) -> None:
            for index in indexes:
                if 0 <= index < len(self.pages) and index not in found.pages:
                    found.pages.append(index)

        explicit = [index for printed in refs.pages if (index := self.page_index(printed)) is not None]
        # В книге «§ 2.4», а учитель пишет «§6» — номер ничего не скажет, ищем по теме.
        dotted = any(re.match(r"§\s*\d+\.\d+", title) for title, _ in self.toc)
        paragraphs = [] if dotted else [self.paragraph_pages(number) for number in refs.paragraphs]
        if not any(paragraphs) and (by_topic := self.topic_section(topic)):
            found.section, pages = by_topic
            paragraphs = [pages]
        scope = explicit + [index for pages in paragraphs for index in pages]

        add(*explicit)
        # «упр. 48 стр. 47», а в книге упражнение 47 на странице 48: учитель
        # перепутал. Если упражнения нет на названной странице, а наоборот —
        # есть, берём и эту страницу; что подходит, решит модель по теме.
        if explicit and refs.exercises:
            for exercise in refs.exercises:
                if any(self.has_exercise(i, exercise.number) for i in explicit):
                    continue
                for printed in refs.pages:
                    swapped = self.page_index(exercise.number)
                    if swapped is not None and self.has_exercise(swapped, printed):
                        add(swapped)
                        found.exercises.setdefault(swapped, []).append((printed, False))
                        found.swapped = True
        for exercise in refs.exercises:
            index = self.exercise_page(exercise.number, within=scope)
            if index is None:
                # Распознавание пропустило номер — он рядом с соседним.
                index = self.exercise_page(exercise.number - 1, within=scope)
            if index is None:
                continue
            add(index)
            found.exercises.setdefault(index, []).append((exercise.number, False))
            # Условие переходит на следующую страницу, если следующего номера
            # на этой нет.
            if index + 1 < len(self.pages) and not self.has_exercise(index, exercise.number + 1):
                add(index + 1)
                found.exercises.setdefault(index + 1, []).append((exercise.number, True))
            if answer := self.answer(exercise.number):
                found.answers[exercise.number] = answer
        # «§13» без номеров — параграф целиком; при номерах — его начало:
        # теория к задачам без лишних страниц.
        for pages in paragraphs:
            add(*(pages if not refs.exercises else pages[:1]))
        found.pages = sorted(found.pages[:MAX_PAGES])
        return found


@dataclass(slots=True)
class Found:
    pages: list[int] = field(default_factory=list)
    # Страницы с условиями упражнений: какие номера на них и не продолжение
    # ли это с прошлой страницы. Им нужны формулы и рисунки как есть.
    exercises: dict[int, list[tuple[int, bool]]] = field(default_factory=dict)
    answers: dict[int, str] = field(default_factory=dict)
    # Нашлось и с переставленными страницей и номером упражнения.
    swapped: bool = False
    # Параграф подобран по теме урока — его заголовок в книге («§ 2.4. …»):
    # номер в задании с ним не совпадает, и модели надо это сказать.
    section: str = ""


_STOP = {"и", "в", "во", "на", "с", "со", "к", "по", "о", "об", "от", "для", "их", "его", "её", "как", "что", "из"}


def _stems(text: str) -> set[str]:
    """Основы слов: первые 5 букв — «табличные» и «табличных» совпадают."""
    return {w[:5] for w in re.findall(r"[а-яёa-z]{3,}", text.lower()) if w not in _STOP}


class Library:
    def __init__(self, root: Path | Callable[[], Path]) -> None:
        self._root_source = root
        self._books: dict[str, Book] = {}
        self._grade: int | None = None
        self._loaded_mtime: float | None = None

    @property
    def _root(self) -> Path:
        return self._root_source() if callable(self._root_source) else self._root_source

    def _manifest(self) -> Path:
        return self._root / "library.json"

    def _load(self) -> None:
        """Перечитываем, когда library.json поменяли, — без перезапуска бота."""
        try:
            mtime = self._manifest().stat().st_mtime
        except OSError:
            self._books, self._grade, self._loaded_mtime = {}, None, None
            return
        if mtime == self._loaded_mtime:
            return
        books: dict[str, Book] = {}
        try:
            manifest = json.loads(self._manifest().read_text("utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("library.json не читается: %s", exc)
            manifest = {}
        for entry in manifest.get("books") or []:
            try:
                book_id = str(entry["id"])
                index = json.loads((self._root / f"{book_id}.pages.json").read_text("utf-8"))
            except (KeyError, OSError, ValueError) as exc:
                logger.warning("Учебник %s пропущен: %s", entry.get("id"), exc)
                continue
            books[book_id] = Book(
                id=book_id,
                title=str(entry.get("title") or index.get("title") or book_id),
                subjects=tuple(str(s) for s in entry.get("subjects") or ()),
                pages=[str(p) for p in index.get("pages") or []],
                offset=int(entry.get("offset", index.get("offset", 0))),
                toc=[(str(t), int(i)) for t, i in index.get("toc") or [] if isinstance(i, int)],
                answers_from=entry.get("answers_from", index.get("answers_from")),
                images=self._root / book_id if (self._root / book_id).is_dir() else None,
                lines=self._lines(book_id),
            )
        grade = manifest.get("grade")
        self._grade = int(grade) if isinstance(grade, int) or str(grade).isdigit() else None
        self._books, self._loaded_mtime = books, mtime

    def _lines(self, book_id: str) -> list[list[list]]:
        try:
            return json.loads((self._root / f"{book_id}.lines.json").read_text("utf-8"))
        except (OSError, ValueError):
            return []

    def grade(self) -> int | None:
        """Класс — из library.json: «ученик 9 класса» модели важно."""
        self._load()
        return self._grade

    def books_for(self, subject: str) -> list[Book]:
        self._load()
        subject = subject.strip().casefold()
        return [b for b in self._books.values() if any(s.casefold() == subject for s in b.subjects)]

    def version(self) -> str:
        """Меняется, когда меняют учебники: старые решения тогда не годятся."""
        self._load()
        return str(int(self._loaded_mtime or 0))
