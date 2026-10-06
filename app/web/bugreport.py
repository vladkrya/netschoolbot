"""Отчёт об ошибке из мини-приложения: текст уходит админу бота в Telegram.

Перед отправкой человек проходит KryaCaptcha. Токен капчи проверяется здесь,
на сервере, через `siteverify` с привязкой к пользователю (`bind`) и
минимальным числом раундов: токен одноразовый, и решённую одним человеком
капчу нельзя предъявить за другого.
"""

from __future__ import annotations

import html
import json
import logging
import mimetypes
import time
from urllib.parse import unquote

import aiohttp
from aiogram.types import BufferedInputFile, InputMediaDocument
from aiohttp import web

from .api import BOT, _safe_filename, context, current_user, json_error

logger = logging.getLogger("netschoolbot.web")

routes = web.RouteTableDef()

MIN_TEXT = 10
MAX_TEXT = 4000
# Капча одноразовая, но решить её можно и дважды подряд — не чаще раза в минуту.
COOLDOWN_SECONDS = 60
VERIFY_TIMEOUT = 15
# Фото и видео к отчёту. nginx пропускает тело запроса до 16 МБ.
MAX_FILES = 5
MAX_TOTAL_BYTES = 15 * 1024 * 1024
MEDIA_TYPES = ("image/", "video/")

_last_sent: dict[int, float] = {}


def bind_for(telegram_id: int) -> str:
    return f"netschoolbot:bug:{telegram_id}"


async def verify_captcha(url: str, token: str, bind: str, rounds: int) -> tuple[bool, str]:
    """Спросить сервис капчи, настоящий ли токен. → (успех, код ошибки)."""
    timeout = aiohttp.ClientTimeout(total=VERIFY_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{url}/api/v1/siteverify",
            json={"token": token, "bind": bind, "min_rounds": rounds},
        ) as response:
            data = await response.json(content_type=None)
    if data.get("success"):
        return True, ""
    return False, ",".join(data.get("error-codes") or []) or "unknown"


def _clip(value, limit: int) -> str:
    return str(value or "")[:limit]


def format_report(user, text: str, meta: dict) -> str:
    """Сообщение админу. У бота parse_mode=HTML — всё пользовательское экранируется."""
    lines = [
        "🐞 <b>Отчёт об ошибке</b>",
        f"От: {html.escape(user.display_name or '—')} (<code>{user.telegram_id}</code>)",
        f"Школа: {html.escape(user.school.name or '—')}",
        f"API: {user.effective_api_mode.value}",
        "",
        html.escape(text),
    ]
    device = [
        f"{label}: {html.escape(_clip(meta.get(key), 300))}"
        for key, label in (
            ("screen", "Экран"),
            ("platform", "Платформа"),
            ("version", "Telegram"),
            ("viewport", "Окно"),
            ("ua", "Браузер"),
        )
        if meta.get(key)
    ]
    if device:
        lines += ["", *device]
    return "\n".join(lines)


@routes.get("/api/bug-report")
async def bug_report_config(request: web.Request) -> web.Response:
    """Что нужно экрану отчёта: включена ли кнопка, `bind` и число раундов."""
    settings = context(request).settings.web
    user = current_user(request)
    if not settings.captcha_url:
        return web.json_response({"ok": True, "data": {"enabled": False}})
    return web.json_response({
        "ok": True,
        "data": {
            "enabled": True,
            "bind": bind_for(user.telegram_id),
            "rounds": settings.captcha_rounds,
        },
    })


class BadReport(Exception):
    pass


def _is_media(filename: str, content_type: str | None) -> bool:
    kind = (content_type or "").lower()
    if not kind or kind == "application/octet-stream":
        kind = mimetypes.guess_type(filename)[0] or ""
    return kind.startswith(MEDIA_TYPES)


async def read_report(request: web.Request) -> tuple[dict, list[tuple[str, bytes]]]:
    """Поля отчёта и файлы: JSON без файлов или multipart с фото и видео.

    Multipart читается потоком: слишком большой файл отвергается, не оседая
    целиком в памяти.
    """
    if not request.content_type.startswith("multipart/"):
        try:
            body = await request.json()
        except ValueError as exc:
            raise BadReport("Некорректный запрос") from exc
        if not isinstance(body, dict):
            raise BadReport("Некорректный запрос")
        return body, []

    fields: dict = {}
    files: list[tuple[str, bytes]] = []
    total = 0
    try:
        reader = await request.multipart()
        async for part in reader:
            if part.name == "file" and part.filename is not None:
                # Браузеры шлют имя в UTF-8, некоторые клиенты — %-кодированным.
                name = _safe_filename(unquote(part.filename))
                if not _is_media(name, part.headers.get("Content-Type")):
                    raise BadReport(f"«{name}» — не фото и не видео")
                if len(files) >= MAX_FILES:
                    raise BadReport(f"Не больше {MAX_FILES} файлов")
                data = bytearray()
                while chunk := await part.read_chunk(64 * 1024):
                    data += chunk
                    total += len(chunk)
                    if total > MAX_TOTAL_BYTES:
                        raise BadReport(f"Файлы больше {MAX_TOTAL_BYTES // 1024 // 1024} МБ в сумме")
                if not data:
                    raise BadReport(f"«{name}» пустой")
                files.append((name, bytes(data)))
            elif part.name in ("text", "token"):
                fields[part.name] = await part.text()
            elif part.name == "meta":
                try:
                    fields["meta"] = json.loads(await part.text())
                except ValueError:
                    fields["meta"] = {}
    except (ValueError, AssertionError) as exc:
        raise BadReport("Некорректный запрос") from exc
    return fields, files


async def deliver(bot, chat_id: int, text: str, files: list[tuple[str, bytes]]) -> None:
    """Текст отчёта и следом вложения — документами, чтобы Telegram их не пережимал."""
    message = await bot.send_message(chat_id, text)
    if not files:
        return
    documents = [BufferedInputFile(data, filename=name) for name, data in files]
    reply = {"reply_to_message_id": getattr(message, "message_id", None)}
    if len(documents) == 1:
        await bot.send_document(chat_id, documents[0], **reply)
    else:
        await bot.send_media_group(chat_id, [InputMediaDocument(media=d) for d in documents], **reply)


@routes.post("/api/bug-report")
async def bug_report(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    settings = app.settings
    if not settings.web.captcha_url:
        return json_error("Отчёты об ошибках сейчас выключены", status=404)
    bot = request.app[BOT]
    if bot is None or not settings.telegram.admin_id:
        return json_error("Отправить отчёт сейчас некуда", status=503)

    try:
        body, files = await read_report(request)
    except BadReport as exc:
        return json_error(str(exc))
    text = str(body.get("text") or "").strip()
    token = body.get("token")
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    if len(text) < MIN_TEXT:
        return json_error("Опишите, что случилось, хотя бы парой слов")
    if len(text) > MAX_TEXT:
        return json_error(f"Слишком длинно: не больше {MAX_TEXT} символов")
    if not isinstance(token, str) or not token or len(token) > 4096:
        return json_error("Сначала пройдите капчу")

    now = time.monotonic()
    if now - _last_sent.get(user.telegram_id, -COOLDOWN_SECONDS) < COOLDOWN_SECONDS:
        return json_error("Отчёт только что отправлен. Подождите минуту.", status=429)

    try:
        ok, code = await verify_captcha(
            settings.web.captcha_url, token, bind_for(user.telegram_id), settings.web.captcha_rounds
        )
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        logger.warning("Капча недоступна: %s", exc)
        return json_error("Сервис капчи не отвечает, попробуйте позже", status=503)
    if not ok:
        logger.info("Капча отклонила отчёт %s: %s", user.telegram_id, code)
        return json_error("Капча не подтвердилась, пройдите её заново", reason="captcha")

    _last_sent[user.telegram_id] = now
    try:
        await deliver(bot, settings.telegram.admin_id, format_report(user, text, meta), files)
    except Exception:
        _last_sent.pop(user.telegram_id, None)
        logger.exception("Не удалось переслать отчёт об ошибке от %s", user.telegram_id)
        return json_error("Не удалось отправить отчёт, попробуйте позже", status=503)
    return web.json_response({"ok": True})
