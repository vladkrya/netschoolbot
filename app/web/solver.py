"""Кнопка «Решение» под домашним заданием (solver/).

Видна тем, кто есть в NETSCHOOL_SOLVER_USERS, а когда владелец (TG_ADMIN_ID)
включил тумблер «Решения заданий всему классу» — и его одноклассникам
(solver_access). Решение пишет модель по страницам учебника и файлам задания
и сохраняется одно на класс: второй запрос того же задания отдаётся из базы
и лимит не тратит.

Задание приходит от приложения как есть. Подменить чужое решение так нельзя:
ключ — хеш самого текста, а файлы сервер берёт своими силами и с проверкой
доступа.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import aclosing
from urllib.parse import urlparse

from aiohttp import web

from ..context import AppContext
from ..domain.models import User
from ..solver.extract import extract_text
from ..solver.gemini import read_conditions
from ..solver.llm import stream_messages
from ..solver.prompt import DraftFilter, Task, cache_key, conditions, system_prompt, user_content
from .api import attachment_payload, context, current_user, json_error
from .assistant import AssistantError

logger = logging.getLogger("netschoolbot.solver")

routes = web.RouteTableDef()

# Черновик и чистовик вместе (prompt.DraftFilter): черновик не показывается.
MAX_TOKENS = 14000
TIMEOUT = 300
PING_S = 15
MAX_TEXT = 2000
MAX_FILES = 5
# Файл ждём недолго: решение без него лучше, чем никакого.
FILE_TIMEOUT_S = 40
# Комментарий учителя, если приложение его ещё не подгрузило.
COMMENT_TIMEOUT_S = 15
# Villnus иногда обрывает поток на первых токенах; повторное нажатие
# обычно работает — повторяем сами.
ATTEMPTS = 3
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Решения, которые идут сейчас: ссылка держит задачу, пока она не кончится.
_running: set[asyncio.Task] = set()

# Тумблер владельца «Решения заданий всему классу» (app_flags).
CLASS_FLAG = "solver_class"
# Одноклассник ли — помним: ответ берётся из кэша дневников, а не из СГО,
# и меняется редко. «Нет» помним недолго: кэш мог ещё не заполниться.
CLASSMATE_YES_S = 6 * 3600
CLASSMATE_NO_S = 15 * 60


def available(app: AppContext, user: User) -> bool:
    return app.settings.assistant.enabled and app.settings.solver.allowed(user.telegram_id)


def is_owner(app: AppContext, user: User) -> bool:
    admin = app.settings.telegram.admin_id
    return bool(admin) and user.telegram_id == admin


async def class_open(app: AppContext) -> bool:
    from ..db.repositories import AppFlagRepository

    return await AppFlagRepository(app.db).get(CLASS_FLAG)


async def solver_access(app: AppContext, user: User) -> bool:
    """Видна ли кнопка «Решение»: список из .env или, при включённом
    тумблере владельца, одноклассник владельца."""
    if not app.settings.assistant.enabled:
        return False
    if available(app, user):
        return True
    if is_owner(app, user) or not app.settings.telegram.admin_id or not await class_open(app):
        return False
    return await classmate(app, user)


def _class_key(name: object) -> str:
    return re.sub(r"[\s\-«»\"']+", "", str(name or "")).lower()


async def _class_name(app: AppContext, user: User) -> str:
    # Пишет /api/student-class, когда человек открывает настройки.
    cached = await app.cache.get(user.telegram_id, f"class:{user.selected_student_id}")
    return _class_key(cached[0]) if cached else ""


async def _assignments(app: AppContext, user: User) -> set:
    ids = set()
    for section in ("homework", "homework:past"):
        if cached := await app.cache.get(user.telegram_id, section):
            ids.update(item["id"] for item in (cached[0] or {}).get("items") or []
                       if isinstance(item, dict) and item.get("id"))
    return ids


async def classmate(app: AppContext, user: User) -> bool:
    """Учится ли выбранный ученик в одном классе с учеником владельца.

    Только по сохранённому, в СГО не ходим. Школа — тот же сервер и то же
    название школы. Класс — по названию из СГО («9б»), если оно известно у
    обоих; иначе по общим заданиям: id задания на сервере свой у каждого
    класса, и у одноклассников задания одни и те же.
    """
    owner = await app.users.get(app.settings.telegram.admin_id)
    if owner is None or not user.school.url:
        return False
    key = (user.telegram_id, user.selected_student_id, owner.selected_student_id)
    now = time.monotonic()
    if (hit := app.classmates.get(key)) and hit[0] > now:
        return hit[1]

    same = (urlparse(owner.school.url).hostname or owner.school.url) == (urlparse(user.school.url).hostname or user.school.url)
    if same and owner.school.name and user.school.name:
        same = owner.school.name.strip().lower() == user.school.name.strip().lower()
    if same:
        mine, theirs = await _class_name(app, owner), await _class_name(app, user)
        if mine and theirs:
            same = mine == theirs
        else:
            same = bool(await _assignments(app, owner) & await _assignments(app, user))
    app.classmates[key] = (now + (CLASSMATE_YES_S if same else CLASSMATE_NO_S), same)
    return same


def _task(body: object) -> Task | None:
    if not isinstance(body, dict):
        return None
    subject = str(body.get("subject") or "").strip()[:100]
    due = str(body.get("date") or "")[:10]
    text = str(body.get("text") or "")[:MAX_TEXT]
    if not subject or not _DATE.fullmatch(due):
        return None
    files = []
    for item in body.get("attachments") or []:
        if isinstance(item, dict) and str(item.get("id", "")).isdigit():
            files.append((int(item["id"]), str(item.get("name") or "файл")[:200]))
    comment = body.get("comment")
    comment = str(comment)[:MAX_TEXT] if isinstance(comment, str) else ""
    # Школа иногда дублирует в комментарий сам текст задания.
    if comment.strip() == text.strip():
        comment = ""
    return Task(subject=subject, due=due, text=text, topic=str(body.get("topic") or "")[:300],
                comment=comment, attachments=files[:MAX_FILES])


async def _fetch_comment(app: AppContext, user: User, body: dict, task: Task) -> None:
    """Комментарий учителя, когда приложение его не прислало (веб-API отдаёт
    его только в карточке задания): без него «1 ряд — …» теряется."""
    if isinstance(body.get("comment"), str) or not str(body.get("id", "")).isdigit():
        return
    try:
        comment = await asyncio.wait_for(app.diary.fetch_assignment_comment(user, int(body["id"])), COMMENT_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — без комментария решаем по заданию
        logger.info("Комментарий к заданию для решения не получен: %s", type(exc).__name__)
        return
    comment = str(comment or "")[:MAX_TEXT]
    if comment.strip() != task.text.strip():
        task.comment = comment


def _take_quota(app: AppContext, user: User) -> int | None:
    today = user.today()
    day, used = app.solver_usage.get(user.telegram_id, (today, 0))
    if day != today:
        used = 0
    if used >= app.settings.solver.daily_limit:
        return None
    app.solver_usage[user.telegram_id] = (today, used + 1)
    return app.settings.solver.daily_limit - used - 1


def _refund_quota(app: AppContext, user: User) -> None:
    day, used = app.solver_usage.get(user.telegram_id, (user.today(), 0))
    app.solver_usage[user.telegram_id] = (day, max(0, used - 1))


async def _file_text(app: AppContext, user: User, attachment_id: int, name: str) -> str:
    try:
        payload = await asyncio.wait_for(attachment_payload(app, user, attachment_id), FILE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — без файла решаем по остальному
        logger.info("Файл %s для решения не скачан: %s", attachment_id, type(exc).__name__)
        return ""
    return await asyncio.to_thread(extract_text, name, payload)


async def build_request(app: AppContext, user: User, task: Task, *, reread: bool = False,
                        imaged: list[tuple[str, int]] | None = None) -> tuple[str, list[dict]]:
    """Системные правила и материалы: учебник (сканы — картинками страниц
    с условиями) и текст файлов задания.

    Условия, уже прочитанные с картинок, уходят текстом; reread («Решить
    заново») читает картинку снова — вдруг в прошлый раз прочиталось неверно.
    В imaged — номера, отправленные картинкой: их условия потом сохраняются."""
    refs = task.refs()
    books = [(book, book.find(refs, task.topic)) for book in app.textbooks.books_for(task.subject)]
    known: dict[tuple[str, int], str] = {}
    if not reread:
        for book, found in books:
            if book.images:
                numbers = sorted({n for nums in found.exercises.values() for n, _ in nums})
                known.update({(book.id, n): text for n, text in (await app.conditions.get_many(book.id, numbers)).items()})
    if app.settings.solver.gemini_key:
        known.update(await _read_with_gemini(app, books, known))
    files = [(name, await _file_text(app, user, attachment_id, name)) for attachment_id, name in task.attachments]
    content = user_content(task, books, files, known, imaged)
    images = [len(b["source"]["data"]) * 3 // 4 for b in content if b["type"] == "image"]
    logger.info("Решение: %s, текст %s симв., картинок %s на %s КБ, условий из базы %s", task.subject,
                sum(len(b.get("text", "")) for b in content), len(images), sum(images) // 1024, len(known))
    return system_prompt(app.textbooks.grade()), content


async def _read_with_gemini(app: AppContext, books, known: dict[tuple[str, int], str]) -> dict[tuple[str, int], str]:
    """Условия номеров со сканов, которых ещё нет в базе, читает Gemini — и
    они уходят решающей модели текстом. Номер без вырезки начала не читаем:
    по одному продолжению условие вышло бы неполным."""
    read: dict[tuple[str, int], str] = {}
    for book, found in books:
        if not book.images:
            continue
        crops: dict[int, list[bytes]] = {}
        started: set[int] = set()
        for index in found.pages:
            for number, continued in found.exercises.get(index, []):
                if (book.id, number) in known or (continued and number not in started):
                    continue
                if (crop := await asyncio.to_thread(book.crop, index, number, continued=continued)) is None:
                    continue
                crops.setdefault(number, []).append(crop)
                started.add(number)
        texts = await read_conditions(app.settings.solver, crops)
        for number, text in texts.items():
            await app.conditions.put(book.id, number, text, time.time())
            read[(book.id, number)] = text
        if crops:
            logger.info("Решение: Gemini прочитал условий %s из %s", len(texts), len(crops))
    return read


async def _with_pings(chunks, interval: float = PING_S):
    """Куски ответа, а пока модель думает молча — None раз в interval:
    nginx рвёт ответ, в котором больше двух минут ничего не приходило."""
    pending = asyncio.ensure_future(anext(chunks))
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield None
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            yield chunk
            pending = asyncio.ensure_future(anext(chunks))
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def _attempt(app: AppContext, system: str, content: list[dict], model: str, send) -> tuple[str, str]:
    """Одна попытка: поток модели без черновика, пока молчит — «wait».
    Возвращает чистовик и спрятанный черновик."""
    text = ""
    async with aclosing(stream_messages(app.settings.assistant, system, content, model=model,
                                        max_tokens=MAX_TOKENS, timeout=TIMEOUT)) as chunks:
        drafts = DraftFilter()
        async for chunk in _with_pings(chunks):
            if chunk is None:
                await send({"type": "wait"})
                continue
            if not (shown := drafts.feed(chunk)):
                continue
            text += shown
            await send({"type": "delta", "text": shown})
        if rest := drafts.rest():
            text += rest
            await send({"type": "delta", "text": rest})
    return text.strip(), drafts.draft


async def _solve_with_retries(app: AppContext, system: str, content: list[dict], model: str, send) -> tuple[str, str]:
    """До ATTEMPTS попыток. Перед повтором — «reset»: приложение стирает
    начатый текст. Перегрузку не повторяем — там поможет только подождать."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            text, draft = await _attempt(app, system, content, model, send)
            if text:
                return text, draft
            why = "пустой ответ"
        except AssistantError as exc:
            if exc.reason == "busy" or attempt == ATTEMPTS:
                raise
            why = str(exc)
        if attempt < ATTEMPTS:
            logger.info("Решение: попытка %s не удалась (%s), повторяю", attempt, why)
            await send({"type": "reset"})
    raise AssistantError("Модель не придумала решение. Попробуйте ещё раз.")


async def _remember_conditions(app: AppContext, imaged: list[tuple[str, int]], draft: str) -> None:
    """Условия, которые модель переписала с картинок, — в базу: в следующий
    раз эти номера уйдут текстом. Только те, что действительно были картинкой."""
    if not imaged:
        return
    read = conditions(draft)
    saved = 0
    for book, number in dict.fromkeys(imaged):
        if text := read.get(number):
            await app.conditions.put(book, number, text, time.time())
            saved += 1
    logger.info("Решение: условий прочитано с картинок %s из %s", saved, len(set(imaged)))


def _finished(job: asyncio.Task) -> None:
    _running.discard(job)
    # Ошибку решения, которое никто уже не ждёт, забираем, чтобы asyncio о
    # ней не кричал: квоту вернул сам work().
    if not job.cancelled() and (exc := job.exception()) and not isinstance(exc, AssistantError):
        logger.warning("Решение упало: %s", exc, exc_info=exc)


@routes.post("/api/solve")
async def solve(request: web.Request) -> web.StreamResponse:
    app, user = context(request), current_user(request)
    if not await solver_access(app, user):
        return json_error("Решения недоступны", status=404, reason="disabled")
    try:
        body = await request.json()
    except ValueError:
        return json_error("Некорректный запрос")
    task = _task(body)
    if task is None:
        return json_error("Некорректное задание")
    await _fetch_comment(app, user, body, task)
    model = app.settings.solver.model
    key = cache_key(urlparse(user.school.url).hostname or user.school.url, task, app.textbooks.version(), model)
    fresh = bool(body.get("refresh"))

    if not fresh and (saved := await app.solutions.get(key)):
        return web.json_response({"ok": True, "data": {"text": saved["text"], "cached": True}})

    # То же задание уже решается (приложение переспрашивает после обрыва, или
    # одноклассник нажал раньше) — ждём его без квоты: решение придёт из базы.
    # Последнее решение дня иначе отвечало бы «закончились» на свой же повтор.
    joining = not fresh and (running := app.solving.get(key)) is not None and running.locked()
    left = None if joining else _take_quota(app, user)
    if left is None and not joining:
        return json_error("На сегодня решения закончились. Возвращайтесь завтра!", status=429, reason="limit")

    lock = app.solving.setdefault(key, asyncio.Lock())
    response = web.StreamResponse(headers={
        "Content-Type": "application/x-ndjson; charset=utf-8",
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })
    from .server import _secure
    _secure(request, response.headers)
    gone = False

    async def send(payload: dict) -> None:
        # Приложение свернули или связь пропала — решаем дальше молча:
        # решение сохранится, и следующий запрос возьмёт его из базы.
        nonlocal gone
        if gone:
            return
        try:
            await response.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        except (ConnectionError, RuntimeError):
            gone = True

    async def work() -> tuple[str, bool]:
        nonlocal left
        succeeded, charged = False, not joining
        try:
            async with lock:
                # Одноклассник мог решить это, пока мы ждали.
                if not fresh and (saved := await app.solutions.get(key)):
                    return saved["text"], True
                if not charged:
                    # Решение, к которому присоединились, не удалось — решаем сами.
                    if (left := _take_quota(app, user)) is None:
                        raise AssistantError("На сегодня решения закончились. Возвращайтесь завтра!",
                                             status=429, reason="limit")
                    charged = True
                imaged: list[tuple[str, int]] = []
                system, content = await build_request(app, user, task, reread=fresh, imaged=imaged)
                text, draft = await _solve_with_retries(app, system, content, model, send)
                await app.solutions.put(key, task.subject, task.due, model, text, time.time())
                await _remember_conditions(app, imaged, draft)
                succeeded = True
                return text, False
        finally:
            if charged and not succeeded:
                _refund_quota(app, user)
            if not lock.locked():
                app.solving.pop(key, None)

    # Решение — отдельной задачей: закрытое соединение не обрывает модель.
    job = asyncio.create_task(work())
    _running.add(job)
    job.add_done_callback(_finished)
    try:
        await response.prepare(request)
        await send({"type": "start"})
        text, cached = await asyncio.shield(job)
        data = {"text": text, "cached": True} if cached else {"text": text, "cached": False, "left": left}
        await send({"ok": True, "data": data})
    except AssistantError as exc:
        await send({"ok": False, "error": str(exc), "reason": exc.reason})
    except (ConnectionError, RuntimeError):
        pass
    if not gone:
        try:
            await response.write_eof()
        except (ConnectionError, RuntimeError):
            pass
    return response


@routes.post("/api/settings/solver-class")
async def class_setting(request: web.Request) -> web.Response:
    """Тумблер владельца «Решения заданий всему классу»."""
    from ..db.repositories import AppFlagRepository

    app, user = context(request), current_user(request)
    if not is_owner(app, user):
        return json_error("Это может только владелец", status=403)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or "on" not in body:
        return json_error("Некорректный запрос")
    await AppFlagRepository(app.db).set(CLASS_FLAG, bool(body["on"]))
    app.classmates.clear()
    return web.json_response({"ok": True, "data": {"on": await class_open(app)}})
