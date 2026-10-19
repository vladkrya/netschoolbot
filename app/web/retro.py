"""Ретро-сайт — оформление по желанию: сайт netschool-retro целиком.

Дизайн и фронт — проект netschool-retro (github.com/faustyu1/netschool-retro),
автор faustyu1. Его фронт собран из retro/src без изменений (retro/build.mjs) и
ходит в свой сервер по /api/…; static/retro/bridge.js отправляет эти запросы
сюда, в /api/retro/…, с удостоверением приложения. Ответы — в точности их
формы (server/main.py, sources.py, school.py, guestbook.py там же), данные —
из той же библиотеки netschoolpy через пул сессий бота.

Ошибки — {"detail": …}, как у FastAPI: их api.ts показывает именно его.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any

import aiohttp_jinja2
from aiohttp import web
from netschoolpy import MobileNetSchool

from ..domain.models import User
from ..netschool.errors import NetSchoolError
from .api import context, current_user

logger = logging.getLogger("netschoolbot.web")

routes = web.RouteTableDef()
page_routes = web.RouteTableDef()


class RetroError(Exception):
    """Ошибка для их фронта: HTTP-статус и текст в detail."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _reply(handler):
    """Обработчик → JSON их формы (объект или список); RetroError → {"detail"} с её статусом."""

    async def wrapped(request: web.Request) -> web.Response:
        try:
            return web.json_response(await handler(request))
        except RetroError as exc:
            return web.json_response({"detail": exc.detail}, status=exc.status)
        except NetSchoolError as exc:
            status = 401 if exc.reason.needs_relogin else 502
            return web.json_response({"detail": exc.user_message}, status=status)
        except (TimeoutError, asyncio.TimeoutError):
            return web.json_response({"detail": "«Сетевой город» не ответил вовремя, попробуй ещё раз"}, status=504)

    wrapped.__name__ = handler.__name__
    return wrapped


async def _call(request: web.Request, call):
    """Вызов клиента netschoolpy этого пользователя: тот же пул и те же повторы, что у приложения."""
    app, user = context(request), current_user(request)
    return await app.diary.with_client(user, call)


# Их account.cache: кэш на пользователя в памяти, с теми же сроками.
_caches: dict[int, dict[str, tuple[float, Any]]] = {}


def _cache(user: User) -> dict[str, tuple[float, Any]]:
    return _caches.setdefault(user.telegram_id, {})


async def _cached(request: web.Request, key: str, ttl: float, load):
    cache = _cache(current_user(request))
    hit = cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = await load()
    cache[key] = (time.monotonic(), value)
    return value


def _forget(request: web.Request, prefix: str) -> None:
    cache = _cache(current_user(request))
    for key in [k for k in cache if k.startswith(prefix)]:
        del cache[key]


def _int(request: web.Request, name: str, default: int | None = None) -> int:
    try:
        return int(request.query[name]) if name in request.query else int(default)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise RetroError(422, f"Некорректный параметр {name}") from None


# ───────────────────────── страница ─────────────────────────


@page_routes.get("/retro")
async def retro_page(request: web.Request) -> web.Response:
    """Их index.html (templates/retro.html). Удостоверение — как у главной
    страницы: токен из ссылки или cookie, в Telegram — initData в браузере."""
    from .. import __version__
    from .server import STATIC_VERSION, _base

    app = context(request)
    base = _base(request)
    demo = "demo" in request.query
    token = ""
    if not demo:
        for candidate in (request.query.get("token"), request.cookies.get("netschool_token")):
            candidate = (candidate or "").strip()
            if candidate and await app.miniapp.resolve_token(candidate):
                token = candidate
                break
    static = f"{base}/static/v{STATIC_VERSION}"
    query = f"?token={token}" if request.query.get("token") and token else ""
    response = aiohttp_jinja2.render_template(
        "retro.html",
        request,
        {
            "static": static,
            "app_version": __version__,
            "icons": f"{static}/retro/icons",
            "config": {
                "base": base,
                "token": token,
                "demo": demo,
                "app_url": f"{base}/demo" if demo else f"{base}/{query}",
            },
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


# ───────────────────────── дневник (sources.py) ─────────────────────────


def _hhmm(value: Any) -> str:
    if isinstance(value, (dt.time, dt.datetime)):
        return value.strftime("%H:%M")
    return str(value or "")[:5]


def _short_name(full: str) -> str:
    """«Иванова Анна Петровна» -> «Иванова А.П.» — как на карточках макета."""
    parts = full.split()
    return parts[0] + " " + "".join(p[0] + "." for p in parts[1:]) if len(parts) > 1 else full


def _from_diary(diary: Any, start: dt.date, end: dt.date, *, source: str) -> dict:
    lessons = [
        {
            "day": lesson.day.isoformat() if lesson.day else None,
            "start": _hhmm(lesson.start),
            "end": _hhmm(lesson.end),
            "number": lesson.number,
            "subject": lesson.subject,
            "room": lesson.room,
            "teacher": _short_name(lesson.teacher) if lesson.teacher else "",
            "topic": lesson.topic,
            "absence": lesson.absence,
            "distance": lesson.is_distance,
            "elective": lesson.is_elective,
            "assignments": [
                {
                    "id": a.id,
                    "kind": a.kind,
                    "content": a.content,
                    "mark": a.mark_text or (str(a.mark) if a.mark is not None else None),
                    "due": a.deadline.isoformat() if a.deadline else None,
                }
                for a in lesson.assignments
            ],
        }
        for day in diary.schedule
        for lesson in day.lessons
    ]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "class": diary.class_name,
        "term": diary.term_name,
        "lessons": lessons,
        "source": source,
    }


def _mode(client: Any) -> str:
    return "mobile" if isinstance(client, MobileNetSchool) else "web"


@routes.get("/api/retro/me")
@_reply
async def me(request: web.Request) -> dict:
    async def call(client: Any) -> dict:
        student = client.current_student
        return {
            "name": student.name if student else "",
            "class": (student.group_name or "") if student else "",
            "mode": _mode(client),
        }

    return await _call(request, call)


@routes.get("/api/retro/week")
@_reply
async def week(request: web.Request) -> dict:
    try:
        start = dt.date.fromisoformat(request.query["start"]) if request.query.get("start") else current_user(request).today()
    except ValueError:
        raise RetroError(422, "Некорректная дата") from None
    start -= dt.timedelta(days=start.weekday())
    end = start + dt.timedelta(days=5)

    async def load(client: Any) -> dict:
        diary = await client.diary(start, end)
        result = _from_diary(diary, start, end, source="mobile" if _mode(client) == "mobile" else "diary")
        student = client.current_student
        if not result["class"] and student:
            result["class"] = student.group_name or ""
        return result

    return await _call(request, load)


@routes.get("/api/retro/assignment/{assignment_id:\\d+}")
@_reply
async def assignment(request: web.Request) -> dict:
    """Карточка задания из дневника: полное описание и вложения."""
    assignment_id = int(request.match_info["assignment_id"])

    async def load() -> dict:
        async def call(client: Any) -> dict:
            d = await asyncio.wait_for(client.assignment_details(assignment_id), timeout=10)
            return {
                "name": d.name,
                "description": d.description,
                "teacher": d.teacher,
                "attachments": [{"id": a.id, "name": a.name} for a in d.attachments],
            }
        return await _call(request, call)

    return await _cached(request, f"card:{assignment_id}", 10**9, load)


# ───────────────────────── почта (school.py) ─────────────────────────

FOLDERS = {"Inbox": "Входящие", "Sent": "Отправленные", "Deleted": "Удалённые"}
PAGE_SIZE = 20


def _when(value: dt.datetime | str | None) -> str:
    return value.isoformat(timespec="minutes") if isinstance(value, dt.datetime) else (value or "")


@routes.get("/api/retro/mail")
@_reply
async def mail_list(request: web.Request) -> dict:
    folder = request.query.get("folder", "Inbox")
    page = max(1, _int(request, "page", 1))
    if folder not in FOLDERS:
        raise RetroError(400, "Нет такой папки")

    async def call(client: Any) -> dict:
        data = await asyncio.wait_for(client.mail_list(folder, page, PAGE_SIZE), timeout=20)
        return {
            "folder": folder,
            "page": data.page,
            "pages": max(1, -(-data.total_items // PAGE_SIZE)),
            "total": data.total_items,
            "entries": [
                {
                    "id": e.id,
                    "subject": e.subject,
                    "author": e.author,
                    "to": e.to_names or "",
                    "sent": _when(e.sent),
                    "read": e.read is not False,
                }
                for e in data.entries
            ],
        }

    return await _call(request, call)


@routes.get("/api/retro/mail/unread")
@_reply
async def mail_unread(request: web.Request) -> dict:
    async def load() -> int:
        async def call(client: Any) -> int:
            if isinstance(client, MobileNetSchool):
                return await client.mail_unread_count()
            return len(await client.mail_unread())
        return await _call(request, call)

    return {"count": await _cached(request, "mail:unread", 60, load)}


@routes.get("/api/retro/mail/recipients")
@_reply
async def mail_recipients(request: web.Request) -> list[dict]:
    async def load() -> list[dict]:
        async def call(client: Any) -> list[dict]:
            people = await client.mail_recipients()
            return sorted(
                ({"id": p.id, "name": p.name, "org": p.organization_name} for p in people),
                key=lambda p: p["name"],
            )
        return await _call(request, call)

    return await _cached(request, "mail:recipients", 3600, load)


@routes.get("/api/retro/mail/{message_id:\\d+}")
@_reply
async def mail_read(request: web.Request) -> dict:
    message_id = int(request.match_info["message_id"])

    async def call(client: Any) -> dict:
        m = await asyncio.wait_for(client.mail_read(message_id), timeout=20)
        return {
            "id": m.id,
            "subject": m.subject,
            "text": m.text,
            "sent": _when(m.sent),
            "author": m.author_name,
            "to": m.to_names,
            "folder": m.mailbox,
            "can_reply": m.can_reply,
            "attachments": [{"id": a.id, "name": a.name} for a in m.file_attachments if a.id],
        }

    result = await _call(request, call)
    _forget(request, "mail:unread")  # чтение помечает письмо прочитанным
    return result


@routes.get("/api/retro/mail/{message_id:\\d+}/reply")
@_reply
async def mail_reply(request: web.Request) -> dict:
    message_id = int(request.match_info["message_id"])

    async def call(client: Any) -> dict:
        d = await client.mail_draft(message_id, "reply")
        return {"subject": d.subject, "text": d.text, "to": [{"id": p.id, "name": p.name} for p in d.to]}

    return await _call(request, call)


@routes.delete("/api/retro/mail/{message_id:\\d+}")
@_reply
async def mail_delete(request: web.Request) -> dict:
    message_id = int(request.match_info["message_id"])
    await _call(request, lambda client: client.mail_delete([message_id]))
    _forget(request, "mail:unread")
    return {"ok": True}


@routes.post("/api/retro/mail")
@_reply
async def mail_send(request: web.Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise RetroError(422, "Некорректный запрос") from None
    if not isinstance(body, dict):
        raise RetroError(422, "Некорректный запрос")
    to = [str(item) for item in body.get("to") or []]
    subject, text = str(body.get("subject") or "").strip(), str(body.get("text") or "").strip()
    if not to:
        raise RetroError(400, "Выбери получателя")
    if not subject or not text:
        raise RetroError(400, "Нужны тема и текст письма")
    if len(subject) > 200 or len(text) > 10_000:
        raise RetroError(400, "Слишком длинное письмо")
    return {"id": await _call(request, lambda client: client.mail_send(subject, text, to))}


# ───────────────────────── отчёты (school.py) ─────────────────────────


def _mobile(client: Any) -> MobileNetSchool:
    if not isinstance(client, MobileNetSchool):
        raise RetroError(501, "Отчёты доступны только через мобильный API")
    return client


def _mark(total: dict) -> dict:
    """Итог за период: средний балл и выставленная отметка, если есть."""
    mark = total.get("mark") or total.get("markValue") or total.get("value")
    if isinstance(mark, dict):
        mark = mark.get("name") or mark.get("value")
    return {"avg": total.get("avgMark"), "mark": mark, "code": total.get("markCode")}


@routes.get("/api/retro/reports/totals")
@_reply
async def report_totals(request: web.Request) -> dict:
    async def call(client: Any) -> dict:
        mobile = _mobile(client)
        subjects, terms, rows = await asyncio.gather(mobile.subjects_raw(), mobile.terms(), mobile.totals())
        names = {s["id"]: (s["name"], s.get("order", 0)) for s in subjects}
        periods: dict[str, str] = {}
        for t in terms:
            periods[f"t{t['id']}"] = t["name"]
        result = []
        for row in rows:
            cells: dict[str, dict] = {}
            for t in row.get("termTotals") or []:
                cells[f"t{t['term']['id']}"] = _mark(t)
            for y in row.get("yearTotals") or []:
                key = f"y{y['period']['id']}"
                periods.setdefault(key, y["period"]["periodName"])
                cells[key] = _mark(y)
            name, order = names.get(row["subjectId"], (f"Предмет {row['subjectId']}", 999))
            result.append({"id": row["subjectId"], "name": name, "order": order, "cells": cells})
        result.sort(key=lambda r: (r["order"], r["name"]))
        current = next((f"t{t['id']}" for t in terms if t.get("current")), None)
        return {
            "periods": [{"key": k, "name": v} for k, v in periods.items()],
            "terms": [{"id": t["id"], "name": t["name"], "current": bool(t.get("current"))} for t in terms],
            "current": current,
            "subjects": result,
        }

    return await _cached(request, "report:totals", 600, lambda: _call(request, call))


@routes.get("/api/retro/reports/subject")
@_reply
async def report_subject(request: web.Request) -> dict:
    subject_id, term_id = _int(request, "subject"), _int(request, "term")

    async def call(client: Any) -> dict:
        p = await _mobile(client).subject_performance(subject_id, term_id)
        stats = p.get("classmeetingsStats") or {}
        return {
            "subject": (p.get("subject") or {}).get("name", ""),
            "term": (p.get("term") or {}).get("name", ""),
            "avg": p.get("averageMark"),
            "class_avg": p.get("classAverageMark"),
            "max": p.get("maxMark") or 5,
            "lessons": {"passed": stats.get("passed"), "scheduled": stats.get("scheduled")},
            "teachers": [t.get("name", "") for t in p.get("teachers") or []],
            "results": [
                {
                    "date": (r.get("classMeetingDate") or r.get("date") or "")[:10],
                    "mark": r.get("result"),
                    "duty": bool(r.get("duty")),
                    "weight": r.get("weight"),
                    "kind": r.get("assignmentTypeName") or "",
                    "abbr": r.get("assignmentTypeAbbr") or "",
                    "comment": r.get("comment") or "",
                }
                for r in p.get("results") or []
            ],
            "stats": [{"mark": s.get("mark"), "count": s.get("count", 0)} for s in p.get("markStats") or []],
        }

    return await _cached(request, f"report:perf:{subject_id}:{term_id}", 600, lambda: _call(request, call))


# ───────────────────────── гостевая книга (guestbook.py) ─────────────────────────
# Общая для всех, кто открыл ретро-сайт; файл в каталоге данных бота.
# Защита от спама — их, по слоям: автор — пользователь бота (подпись его id),
# пауза и лимиты, одноразовый подписанный токен формы, поле-ловушка, длина,
# ссылки, повторы, дубликаты.

MAX_NAME = 32
MAX_TEXT = 500
MAX_ENTRIES = 1000
MAX_REPLIES = 50
COOLDOWN = 30  # секунд между сообщениями одного автора
PER_HOUR = 10  # сообщений в час на автора
SITE_WINDOW, SITE_LIMIT = 600, 60  # на весь сайт: не больше 60 сообщений за 10 минут
MIN_FILL = 3  # быстрее человек форму не заполнит
TOKEN_TTL = 2 * 3600

LINK = re.compile(r"https?://|www\.|t\.me/|@\w{4,}|\b[\w-]{2,}\.(?:ru|com|net|org|io|me|xyz|su|top|site|online|рф)\b", re.I)
REPEAT = re.compile(r"(.)\1{11,}", re.S)
CONTROL = re.compile(r"[\u0000-\u0008\u000b-\u001f\u007f​-‏‪-‮⁠-⁤﻿]")

_lock = asyncio.Lock()
_posts: dict[str, deque[float]] = {}
_site_posts: deque[float] = deque()
_used_tokens: dict[str, float] = {}


def _book_file(request: web.Request) -> Path:
    return context(request).settings.data_dir / "retro_guestbook.json"


def _secret(request: web.Request) -> bytes:
    return hmac.new(b"retro-guestbook", context(request).settings.telegram.bot_token.encode(), hashlib.sha256).digest()


def _sign(request: web.Request, value: str) -> str:
    return hmac.new(_secret(request), value.encode(), hashlib.sha256).hexdigest()[:24]


def _author(request: web.Request) -> str:
    return _sign(request, f"author:{current_user(request).telegram_id}")


def _load(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return []


def _save(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False))
    tmp.chmod(0o600)
    tmp.replace(path)


def _form_token(request: web.Request) -> str:
    stamp = f"{int(time.time())}.{secrets.token_hex(8)}"
    return f"{stamp}.{_sign(request, 'form:' + stamp)}"


def _check_token(request: web.Request, token: str) -> None:
    now = time.time()
    for used, at in list(_used_tokens.items()):
        if now - at > TOKEN_TTL:
            del _used_tokens[used]
    try:
        issued, nonce, signature = token.split(".")
        age = now - int(issued)
    except ValueError:
        raise RetroError(400, "Обнови страницу и попробуй ещё раз") from None
    if not hmac.compare_digest(signature, _sign(request, f"form:{issued}.{nonce}")) or age > TOKEN_TTL:
        raise RetroError(400, "Форма устарела — обнови страницу")
    if age < MIN_FILL:
        raise RetroError(429, "Слишком быстро! Подожди пару секунд")
    if nonce in _used_tokens:
        raise RetroError(400, "Это сообщение уже отправлено")
    _used_tokens[nonce] = now


def _clean(value: Any, limit: int, what: str) -> str:
    value = CONTROL.sub("", unicodedata.normalize("NFKC", str(value or "")))
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if not value:
        raise RetroError(400, f"{what}: пусто")
    if len(value) > limit:
        raise RetroError(400, f"{what}: не длиннее {limit} символов")
    if LINK.search(value):
        raise RetroError(400, "Ссылки и упоминания в гостевой книге нельзя")
    if REPEAT.search(value):
        raise RetroError(400, "Слишком много одинаковых символов подряд")
    return value


def _rate_limit(author: str) -> None:
    now = time.time()
    while _site_posts and now - _site_posts[0] > SITE_WINDOW:
        _site_posts.popleft()
    if len(_site_posts) >= SITE_LIMIT:
        raise RetroError(429, "Гостевая книга перегружена, попробуй через несколько минут")
    times = _posts.setdefault(author, deque())
    while times and now - times[0] > 3600:
        times.popleft()
    if times and now - times[-1] < COOLDOWN:
        raise RetroError(429, f"Подожди {int(COOLDOWN - (now - times[-1])) + 1} с перед следующим сообщением")
    if len(times) >= PER_HOUR:
        raise RetroError(429, "Хватит на этот час — возвращайся позже")


def _public(entry: dict, author: str | None) -> dict:
    return {key: entry[key] for key in ("id", "parent", "reply_to", "name", "text", "at")} | {
        "mine": author is not None and entry["author"] == author
    }


@routes.get("/api/retro/guestbook")
@_reply
async def guestbook_list(request: web.Request) -> dict:
    author = _author(request)
    return {
        "entries": [_public(e, author) for e in _load(_book_file(request))],
        "token": _form_token(request),
        "can_post": True,
    }


@routes.post("/api/retro/guestbook")
@_reply
async def guestbook_post(request: web.Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise RetroError(422, "Некорректный запрос") from None
    if not isinstance(body, dict):
        raise RetroError(422, "Некорректный запрос")
    if body.get("website"):  # поле-ловушку заполняют только боты
        raise RetroError(400, "Обнови страницу и попробуй ещё раз")
    author = _author(request)
    name = _clean(body.get("name"), MAX_NAME, "Имя")
    text = _clean(body.get("text"), MAX_TEXT, "Сообщение")
    parent = body.get("parent") or None
    path = _book_file(request)
    async with _lock:
        _check_token(request, str(body.get("token") or ""))
        _rate_limit(author)
        entries = _load(path)
        reply_to = None
        if parent:
            target = next((e for e in entries if e["id"] == parent), None)
            if target is None:
                raise RetroError(404, "Сообщение, на которое ты отвечаешь, удалено")
            # Ветки одного уровня: ответ на ответ попадает в ту же ветку с пометкой «кому».
            reply_to = target["name"] if target["parent"] else None
            parent = target["parent"] or target["id"]
            if sum(e["parent"] == parent for e in entries) >= MAX_REPLIES:
                raise RetroError(400, "В этой ветке уже слишком много ответов")
        now = time.time()
        if any(e["text"].casefold() == text.casefold() and now - e["at"] < 86400 for e in entries):
            raise RetroError(400, "Такое сообщение уже есть")
        entry = {
            "id": secrets.token_hex(6),
            "parent": parent,
            "reply_to": reply_to,
            "name": name,
            "text": text,
            "at": int(now),
            "author": author,
        }
        entries.append(entry)
        if len(entries) > MAX_ENTRIES:  # старые ветки уходят целиком, вместе с ответами
            roots = [e["id"] for e in entries if not e["parent"]]
            gone = set(roots[: len(roots) // 10 or 1])
            entries = [e for e in entries if e["id"] not in gone and e["parent"] not in gone]
        _save(path, entries)
        _posts.setdefault(author, deque()).append(now)
        _site_posts.append(now)
    return _public(entry, author)


@routes.delete("/api/retro/guestbook/{entry_id}")
@_reply
async def guestbook_delete(request: web.Request) -> dict:
    entry_id = request.match_info["entry_id"]
    author = _author(request)
    path = _book_file(request)
    async with _lock:
        entries = _load(path)
        entry = next((e for e in entries if e["id"] == entry_id), None)
        if entry is None:
            raise RetroError(404, "Сообщение уже удалено")
        if entry["author"] != author:
            raise RetroError(403, "Удалять можно только свои сообщения")
        _save(path, [e for e in entries if e["id"] != entry_id and e["parent"] != entry_id])
    return {"ok": True}
