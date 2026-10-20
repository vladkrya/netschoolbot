"""Запрос к модели: правила оформления, задание, учебник и файлы."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field

from .refs import Refs, parse
from .textbooks import Book, Found

# Меняется вместе с правилами ниже: старые решения в кэше тогда не годятся.
PROMPT_VERSION = 2

RULES = """\
Ты решаешь домашнее задание ученика {grade}российской школы — так, как его сдают учителю: полностью, без пропусков и с оформлением, как в тетради. Пиши по-русски (английский — только в ответах по английскому языку). Без вступлений и без «Конечно!» — сразу решение.

Что решать:
- «Комментарий учителя» — часть задания: в нём часто то, что именно делать (темы, варианты, требования). Выполняй задание вместе с ним. Если комментарий раздаёт разное по рядам или вариантам («1 ряд — …, 2 ряд — …»), сделай для каждого отдельно, с заголовком «### 1 ряд: …», — ученик возьмёт свой.
- «п.13», «§5» рядом с номерами — это теория к ним: используй её, но отдельным разделом не решай и не пиши о ней. Решать нужно номера.
- Каждый номер и каждый пункт из задания, ровно те, что задали: «№289(б,г)» — это пункты б) и г) номера 289. Перед каждым номером — заголовок «### №289». Пункты внутри — «**б)**».
- Учитель нумерует параграфы не всегда как книга: если ниже сказано, что параграф подобран по теме урока, — это и есть заданный параграф. Работай по нему и не пиши, что параграф не нашёлся.
- Условие бери со страниц учебника ниже. Перепиши его коротко перед решением. Если условия номера на страницах нет — так и напиши: «Условие №… не нашлось в учебнике», и не выдумывай его.
- Если дан ответ из конца учебника — сверься с ним до того, как писать. Не сошлось — почти всегда это ты неверно прочитал условие, а не опечатка в учебнике. Самые частые ошибки чтения: «=» вместо «−» и наоборот (соседние пункты устроены по-разному — не переноси схему одного пункта на другой), «+» и «−», показатель степени, где кончается дробь. Перебери такие прочтения, найди то, при котором ответ учебника подходит, и сверь его с картинкой знак за знаком. Подошло — решай его. Только если ни одно прочтение не подходит — дай своё решение и одной строкой отметь расхождение с учебником.
- Сначала реши всё начерно внутри {draft_open}…{draft_close}: перепиши условие с картинки, реши, подставь ответ в исходное уравнение и сверься с учебником. Черновик ученик не увидит. После {draft_close} — только чистовик, как в тетрадь: без проверок наугад, сомнений и «перепроверю».
- Условие со скана переписывай внимательно: оно может отличаться от распознанного текста — верь картинке.
- Каждый номер, данный картинкой, в начале черновика перепиши целиком — со всеми пунктами, а не только заданными, — между {cond_open}N> и {cond_close}, например {cond_open}291>…{cond_close}. Формулы — в LaTeX. Этот текст сохранится и в следующий раз заменит картинку, поэтому без сокращений и пояснений.

Как оформлять:
- Алгебра: преобразования по шагам, каждый шаг с новой строки. Дробно-рациональные уравнения — сначала ОДЗ, в конце проверка корней по ОДЗ. Неравенства — с промежутками. Строка «**Ответ:** …» в конце каждого пункта.
- Геометрия и физика: «**Дано:**», «**Найти:**», «**Решение:**», «**Ответ:**». В решении — почему (по какой теореме, формуле, свойству). Векторы и координаты — с формулами, по которым считаешь. Физика — с единицами измерения.
- Химия: уравнения реакций с коэффициентами; для реакций ионного обмена — молекулярное, полное и сокращённое ионные уравнения; гидролиз — по ступеням, среда раствора.
- Русский язык: переписанный текст упражнения со вставленными буквами и знаками препинания, вставленное выделяй **жирным**; разборы — как принято в школе.
- Английский: выполненное упражнение целиком, по-английски; пояснения — коротко, по-русски.
- История, обществознание, биология, география — «§N», «пересказ», «читать»: план пересказа по тексту параграфа (6–10 пунктов, по 1–2 предложения), главные даты, термины и понятия, затем ответы на вопросы после параграфа, если они есть на страницах. «Сделать таблицу» — таблица в Markdown.
- «Подготовить доклад», «сообщение»: готовый текст на 1–1,5 страницы с заголовком и абзацами — на тему из комментария учителя, а если её там нет — по теме урока. Докладов несколько (по рядам, вариантам) — каждый на полстраницы–страницу.
- Онлайн-тест по ссылке открыть ты не можешь: так и скажи и дай короткую памятку по теме, которая нужна для теста.
- «нет д/з», практика по физкультуре и всё, что не делается письменно, — одна строка, что решать нечего.

Формат: Markdown — заголовки «###», **жирный**, списки, таблицы. Формулы — в $…$, отдельной строкой — в $$…$$. Внутри формул только: \\frac{{}}{{}}, \\sqrt{{}}, ^{{}}, _{{}}, \\cdot, \\pm, \\le, \\ge, \\ne, \\approx, \\Rightarrow, \\Leftrightarrow, \\in, \\cup, \\infty, \\degree, \\angle, \\triangle, \\vec{{}}, \\overline{{}}, \\alpha, \\beta, \\gamma, \\pi, \\text{{}}, \\to, \\{{ \\}}. Системы уравнений — \\begin{{cases}}…\\end{{cases}}, строки через \\\\. Химические формулы — тоже в $…$: $H_2SO_4$, $Fe^{{3+}}$."""


@dataclass(slots=True)
class Task:
    """Задание, как его видит приложение."""

    subject: str
    due: str
    text: str
    topic: str = ""
    # Комментарий учителя к заданию: «1 ряд — …, 2 ряд — …».
    comment: str = ""
    attachments: list[tuple[int, str]] = field(default_factory=list)

    def refs(self) -> Refs:
        return parse(self.text)


def cache_key(host: str, task: Task, library_version: str, model: str) -> str:
    """Одно решение на класс: у одноклассников задание то же — до буквы."""
    raw = json.dumps([
        PROMPT_VERSION, host, task.subject.strip().casefold(), task.due, " ".join(task.text.split()),
        " ".join(task.comment.split()), sorted(a for a, _ in task.attachments), library_version, model,
    ], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


# Все картинки одного запроса вместе. Провайдер считает их по размеру файла:
# пять вырезок во всю ширину скана стоили 470 тысяч токенов (16 ₽ за решение).
# Что не влезло — уходит распознанным текстом страницы.
MAX_IMAGE_BYTES = 200_000

DRAFT_OPEN, DRAFT_CLOSE = "<черновик>", "</черновик>"
# Условие номера, прочитанное с картинки: «<условие №291>…</условие>».
COND_OPEN, COND_CLOSE = "<условие №", "</условие>"
_CONDITION = re.compile(r"<условие №(\d{1,4})>(.*?)</условие>", re.S)


def system_prompt(grade: int | None) -> str:
    return RULES.format(grade=f"{grade} класса " if grade else "", draft_open=DRAFT_OPEN, draft_close=DRAFT_CLOSE,
                        cond_open=COND_OPEN, cond_close=COND_CLOSE)


def conditions(draft: str) -> dict[int, str]:
    """Условия, которые модель переписала в черновике с картинок."""
    found = {}
    for number, text in _CONDITION.findall(draft):
        if 20 <= len(text := text.strip()) <= 4000:
            found[int(number)] = text
    return found


class DraftFilter:
    """Прячет черновик модели: всё до </черновик> не показывается. Если модель
    начала сразу с чистовика — пропускает всё как есть."""

    def __init__(self) -> None:
        self._buffer = ""
        self._state = "start"  # start → draft → clean
        # Спрятанный черновик: из него берутся прочитанные условия.
        self.draft = ""

    def feed(self, chunk: str) -> str:
        if self._state == "clean":
            return chunk
        self._buffer += chunk
        if self._state == "start":
            head = self._buffer.lstrip()
            if len(head) < len(DRAFT_OPEN) and DRAFT_OPEN.startswith(head):
                return ""
            if not head.startswith(DRAFT_OPEN):
                self._state = "clean"
                out, self._buffer = self._buffer, ""
                return out
            self._state = "draft"
        end = self._buffer.find(DRAFT_CLOSE)
        if end < 0:
            return ""
        self._state = "clean"
        self.draft = self._buffer[:end]
        out = self._buffer[end + len(DRAFT_CLOSE):].lstrip("\n")
        self._buffer = ""
        return out

    def rest(self) -> str:
        """Ответ кончился, а черновик так и не закрыт: лучше он, чем ничего."""
        out = self._buffer.replace(DRAFT_OPEN, "", 1).strip() if self._state != "clean" else ""
        self._buffer, self._state = "", "clean"
        return out


def _pictures(book: Book, index: int, numbers: list[tuple[int, bool]]) -> list[tuple[str, bytes]]:
    """Картинки упражнений со скана: каждый номер вырезкой крупно, а если номер
    на странице не нашёлся — страница целиком, уменьшенная."""
    if not numbers:
        return []
    page = book.printed(index)
    pictures = []
    for number, continued in numbers:
        if (crop := book.crop(index, number, continued=continued)) is not None:
            pictures.append((f"№{number}{' (продолжение)' if continued else ''}, стр. {page}", crop))
    if pictures:
        return pictures
    whole = book.image(index)
    return [(f"страница {page}", _shrink(whole))] if whole else []


def _shrink(image: bytes, side: int = 1100) -> bytes:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(image)) as page:
        page = page.convert("L")
        page.thumbnail((side, side))
        out = io.BytesIO()
        page.save(out, "JPEG", quality=50, optimize=True)
    return out.getvalue()


def user_content(task: Task, books: list[tuple[Book, Found]], files: list[tuple[str, str]],
                 known: dict[tuple[str, int], str] | None = None,
                 imaged: list[tuple[str, int]] | None = None) -> list[dict]:
    """Задание и материалы блоками Anthropic Messages: страницы учебника
    текстом, а у сканов страницы с условиями — картинкой.

    known — условия, уже прочитанные раньше: такой номер уходит текстом, без
    картинки. В imaged дописываются номера, отправленные картинкой."""
    known = known or {}
    blocks: list[dict] = []
    parts = [f"Предмет: {task.subject}", f"Срок: {task.due}"]
    if task.topic:
        parts.append(f"Тема урока: {task.topic}")
    parts.append(f"Задание: {task.text.strip() or '(без текста)'}")
    if task.comment.strip():
        parts.append(f"Комментарий учителя к заданию:\n{task.comment.strip()}")
    budget = MAX_IMAGE_BYTES

    def flush() -> None:
        if parts:
            blocks.append({"type": "text", "text": "\n".join(parts)})
            parts.clear()

    for book, found in books:
        if not found.pages and not found.answers:
            parts.append(f"\nУчебник «{book.title}»: нужных страниц не нашлось.")
            continue
        parts.append(f"\n=== Учебник «{book.title}» ===")
        if found.section:
            parts.append(f"(Параграф подобран по теме урока: в книге это «{found.section}». "
                         "Номер в задании — нумерация учителя, задан именно он.)")
        for index in found.pages:
            numbers = found.exercises.get(index, [])
            remembered = sorted({n for n, _ in numbers if (book.id, n) in known})
            for number in remembered:
                parts.append(f"--- №{number}, стр. {book.printed(index)} (условие со скана, переписано текстом) ---\n"
                             f"{known[(book.id, number)]}")
            numbers = [(n, c) for n, c in numbers if (book.id, n) not in known]
            if remembered and not numbers:
                continue  # всё нужное на этой странице уже есть текстом
            pictures = _pictures(book, index, numbers) if book.images else []
            size = sum(len(picture) for _, picture in pictures)
            if size > budget:
                pictures = []
            else:
                budget -= size
            if pictures:
                if imaged is not None:
                    imaged.extend((book.id, n) for n, continued in numbers if not continued)
                for caption, picture in pictures:
                    parts.append(f"--- {caption} (картинка ниже) ---")
                    flush()
                    blocks.append({"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(picture).decode()}})
            else:
                note = " (распознанный скан: формулы могут быть искажены)" if book.images else ""
                parts.append(f"--- страница {book.printed(index)}{note} ---\n{book.pages[index].strip()}")
        if found.swapped:
            parts.append("(Похоже, учитель перепутал номер страницы и упражнения: даны оба варианта. "
                         "Выбери тот, что подходит к теме урока, и скажи об этом одной строкой.)")
        if found.answers:
            parts.append("--- ответы из конца учебника ---")
            parts += [f"№{number}: {text}" for number, text in sorted(found.answers.items())]
    if not books:
        parts.append("\nУчебника по этому предмету нет — решай по заданию, теме урока и файлам.")

    for name, text in files:
        parts.append(f"\n=== Файл «{name}» ===\n{text}" if text else f"\n=== Файл «{name}»: прочитать не удалось ===")
    flush()
    return blocks
