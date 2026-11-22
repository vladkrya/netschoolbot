"""Вложения заданий и писем: билеты, кэш, имена файлов."""

from __future__ import annotations

import asyncio
import mimetypes
import re
import time
from urllib.parse import quote

from aiohttp import web

from ...context import AppContext
from ...domain.models import ApiMode, User
from .base import DOWNLOAD_PATH, context, current_user, issue_attachment_ticket, json_error, logger, routes


@routes.get("/api/attachment-ticket")
async def attachment_ticket(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    resource = request.query.get("resource", "")
    if not DOWNLOAD_PATH.fullmatch(resource):
        return json_error("Некорректный адрес скачивания")
    if resource.startswith("/api/export/"):
        from ...exporting import REPORTS

        report = request.query.get("report", "grades")
        if report not in REPORTS:
            return json_error("Некорректный вид отчёта")
        resource += ":" + report
    ticket = issue_attachment_ticket(app, user.telegram_id, resource)
    return web.json_response({"ok": True, "data": {"ticket": ticket}})


async def attachment_payload(app: AppContext, user: User, attachment_id: int) -> bytes:
    """Файл задания: из часового кэша, из сохранённых заранее или из СГО.
    asyncio.TimeoutError — школьный сервер не отдал файл вовремя."""
    cached = _attachment_cache(app, user, attachment_id)
    if cached.exists() and time.time() - cached.stat().st_mtime < ATTACHMENT_CACHE_S:
        return cached.read_bytes()
    if app.attachments is not None and (kept := await app.attachments.get(user, attachment_id)) is not None:
        # Скачано заранее (attachments/keeper.py): открывается сразу и тогда,
        # когда «Сетевой город» лежит.
        return kept
    payload = await asyncio.wait_for(app.diary.download_attachment(user, attachment_id), ATTACHMENT_TIMEOUT_S)
    _store_attachment(cached, payload)
    if app.attachments is not None:
        await app.attachments.store(user, attachment_id, payload)
    return payload


@routes.get("/api/attachment/{attachment_id}")
async def attachment(request: web.Request) -> web.Response:
    try:
        attachment_id = int(request.match_info["attachment_id"])
    except ValueError:
        return json_error("Некорректный идентификатор вложения")

    app, user = context(request), current_user(request)
    try:
        payload = await attachment_payload(app, user, attachment_id)
    except asyncio.TimeoutError:
        return json_error(
            "«Сетевой город» не отдал файл за полторы минуты — сервер школы перегружен. "
            "Попробуйте ещё раз позже.", status=504, reason="server")
    if request.query.get("prepare"):
        # Страница ожидания сперва ждёт файл здесь, а потом открывает прямую
        # ссылку — она отдаётся из кэша сразу, и браузер сам решает, показать
        # файл (pdf, картинка) или сохранить под верным именем.
        return web.json_response({"ok": True, "data": {"size": len(payload)}})
    # Без имени и типа файл сохранялся как «attachment-123» без расширения,
    # и телефон не понимал, что это docx или pdf.
    name = _attachment_name(request.query.get("name", ""), attachment_id)
    disposition = _content_disposition(name)
    headers = {
        "Content-Type": _attachment_content_type(name),
        "Content-Disposition": disposition,
    }
    if disposition.startswith("attachment;"):
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; frame-ancestors 'none'"
    return web.Response(body=payload, headers=headers)


# Файл из «Сетевого города» ждём не дольше этого: перегруженный сервер школы
# иначе держал вкладку пустой без конца. Скачанное час лежит на диске —
# повторное открытие (или «Повторить» после обрыва) мгновенное. Таймаут
# меньше proxy_read_timeout у nginx (120 с): иначе вместо понятной ошибки
# браузер получил бы HTML-страницу 504 от nginx.
ATTACHMENT_TIMEOUT_S = 100
ATTACHMENT_CACHE_S = 3600


def _attachment_cache(app: AppContext, user: User, attachment_id: int):
    key = str(user.telegram_id)
    if user.effective_api_mode is ApiMode.MYSCHOOL:
        key += f"-myschool-{user.selected_student_id or 1}"
    return app.settings.attachments_dir / f"attachment-{key}-{attachment_id}"


def _store_attachment(path, payload: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        partial.write_bytes(payload)
        partial.replace(path)
        now = time.time()
        for old in path.parent.glob("attachment-*"):
            if now - old.stat().st_mtime > ATTACHMENT_CACHE_S:
                old.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Вложение не сохранено в кэш: %s", exc)


_UNSAFE_NAME = re.compile(r'[\x00-\x1f\x7f/\\"]+')


def _attachment_name(raw: str, attachment_id: int) -> str:
    """Имя файла для скачивания: из ссылки, без путей и управляющих символов."""
    name = _UNSAFE_NAME.sub("_", raw).strip(" .")[:200]
    return name or f"attachment-{attachment_id}"


# Что браузер умеет показать сам — открываем просмотром, а не скачиванием:
# вложение теперь открывается во внешнем браузере. Safari на iPhone и iPad
# показывает и документы Office; с «attachment» он предлагал только
# «Открыть в…», и pptx из ДЗ было не посмотреть. Браузеры, которые Office
# не показывают, с «inline» просто скачивают файл, как раньше.
_INLINE_TYPES = (
    "application/pdf", "image/", "text/plain", "application/rtf",
    "application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.",
)


def _attachment_content_type(name: str) -> str:
    content_type = mimetypes.guess_type(name)[0] or ""
    if name.lower().endswith((".svg", ".svgz")) or content_type == "image/svg+xml":
        return "application/octet-stream"
    return content_type or "application/octet-stream"


def _content_disposition(name: str) -> str:
    """Заголовок с именем в UTF-8 (RFC 6266) и ASCII-запасным для старых клиентов."""
    fallback = name.encode("ascii", "replace").decode().replace("?", "_")
    content_type = _attachment_content_type(name)
    kind = "inline" if content_type.startswith(_INLINE_TYPES) else "attachment"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
