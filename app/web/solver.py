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
from ..solver.llm import stream_messages
from ..solver.prompt import DraftFilter, Task, cache_key, system_prompt, user_content
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
# Villnus иногда обрывает поток на первых токенах; повторное нажатие
# обычно работает — повторяем сами.
ATTEMPTS = 3
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

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
    return Task(subject=subject, due=due, text=text, topic=str(body.get("topic") or "")[:300],
                attachments=files[:MAX_FILES])


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


async def build_request(app: AppContext, user: User, task: Task) -> tuple[str, list[dict]]:
    """Системные правила и материалы: учебник (сканы — картинками страниц
    с условиями) и текст файлов задания."""
    refs = task.refs()
    books = [(book, book.find(refs, task.topic)) for book in app.textbooks.books_for(task.subject)]
    files = [(name, await _file_text(app, user, attachment_id, name)) for attachment_id, name in task.attachments]
    return system_prompt(app.textbooks.grade()), user_content(task, books, files)


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


async def _attempt(app: AppContext, system: str, content: list[dict], model: str, send) -> str:
    """Одна попытка: поток модели без черновика, пока молчит — «wait»."""
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
    return text.strip()


async def _solve_with_retries(app: AppContext, system: str, content: list[dict], model: str, send) -> str:
    """До ATTEMPTS попыток. Перед повтором — «reset»: приложение стирает
    начатый текст. Перегрузку не повторяем — там поможет только подождать."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            if text := await _attempt(app, system, content, model, send):
                return text
            why = "пустой ответ"
        except AssistantError as exc:
            if exc.reason == "busy" or attempt == ATTEMPTS:
                raise
            why = str(exc)
        if attempt < ATTEMPTS:
            logger.info("Решение: попытка %s не удалась (%s), повторяю", attempt, why)
            await send({"type": "reset"})
    raise AssistantError("Модель не придумала решение. Попробуйте ещё раз.")


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
    model = app.settings.solver.model
    key = cache_key(urlparse(user.school.url).hostname or user.school.url, task, app.textbooks.version(), model)
    fresh = bool(body.get("refresh"))

    if not fresh and (saved := await app.solutions.get(key)):
        return web.json_response({"ok": True, "data": {"text": saved["text"], "cached": True}})

    left = _take_quota(app, user)
    if left is None:
        return json_error("На сегодня решения закончились. Возвращайтесь завтра!", status=429, reason="limit")

    lock = app.solving.setdefault(key, asyncio.Lock())
    response = web.StreamResponse(headers={
        "Content-Type": "application/x-ndjson; charset=utf-8",
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })
    from .server import _secure
    _secure(request, response.headers)

    async def send(payload: dict) -> None:
        await response.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())

    succeeded = False
    try:
        await response.prepare(request)
        await send({"type": "start"})
        async with lock:
            # Одноклассник мог решить это, пока мы ждали.
            if not fresh and (saved := await app.solutions.get(key)):
                await send({"ok": True, "data": {"text": saved["text"], "cached": True}})
                _refund_quota(app, user)
                succeeded = True
                await response.write_eof()
                return response
            system, content = await build_request(app, user, task)
            text = await _solve_with_retries(app, system, content, model, send)
            await app.solutions.put(key, task.subject, task.due, model, text, time.time())
        await send({"ok": True, "data": {"text": text, "cached": False, "left": left}})
        succeeded = True
        await response.write_eof()
    except AssistantError as exc:
        try:
            await send({"ok": False, "error": str(exc), "reason": exc.reason})
            await response.write_eof()
        except (ConnectionError, RuntimeError):
            pass
    except (ConnectionError, RuntimeError):
        pass  # Закрыли экран — поток модели закрывается вместе с генератором.
    finally:
        if not succeeded:
            _refund_quota(app, user)
        if not lock.locked():
            app.solving.pop(key, None)
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
