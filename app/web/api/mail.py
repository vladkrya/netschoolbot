"""Почта «Сетевого города»."""

from __future__ import annotations


from aiohttp import web

from ...context import AppContext
from ...domain.models import User
from .base import _cached, context, current_user, json_error, routes


# Папки почты, которые видны в приложении, и писем на странице списка.
MAIL_FOLDERS = ("Inbox", "Deleted")
MAIL_PAGE_SIZE = 50


@routes.get("/api/mail")
async def mail(request: web.Request) -> web.Response:
    """Страница папки. Первая страница входящих кэшируется: её же видит
    значок «Почты»; остальные догружаются при прокрутке и не кэшируются."""
    folder = request.query.get("folder", "Inbox")
    try:
        page = int(request.query.get("page", 1))
    except ValueError:
        return json_error("Некорректная страница")
    if folder not in MAIL_FOLDERS or page < 1:
        return json_error("Нет такой папки" if folder not in MAIL_FOLDERS else "Некорректная страница")

    if folder == "Inbox" and page == 1:
        async def produce(app: AppContext, user: User):
            return await app.diary.fetch_mail_page(user, page_size=MAIL_PAGE_SIZE)

        return await _cached(request, "mail", produce)

    app, user = context(request), current_user(request)
    data = await app.diary.fetch_mail_page(user, folder, page, MAIL_PAGE_SIZE)
    return web.json_response({"ok": True, "data": data})


@routes.get("/api/mail/recipients")
async def mail_recipients(request: web.Request) -> web.Response:
    async def produce(app: AppContext, user: User):
        return {"items": await app.diary.fetch_recipients(user)}

    return await _cached(request, "mail:recipients:v2", produce)


# Вложения к письму. Общий предел держим меньше client_max_body_size у nginx.
MAIL_MAX_FILES = 5
MAIL_MAX_FILE_BYTES = 10 * 1024 * 1024
MAIL_MAX_TOTAL_BYTES = 15 * 1024 * 1024


class _BadMail(Exception):
    """Письмо в запросе не годится; текст показывается пользователю."""


def _safe_filename(name: str | None) -> str:
    """Имя файла без пути и управляющих символов; пустое заменяется запасным."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    return base[:150] or "file"


async def _read_mail_request(request: web.Request) -> tuple[list[str], str, str, list[tuple[str, bytes]], bool]:
    """Разобрать запрос отправки: JSON без файлов или multipart с файлами.

    Multipart читается потоком с проверкой размера: слишком большой файл
    отвергается, не оседая целиком в памяти.
    """
    if not request.content_type.startswith("multipart/"):
        try:
            body = await request.json()
        except ValueError as exc:
            raise _BadMail("Некорректный запрос") from exc
        to = [str(item) for item in (body.get("to") or []) if str(item).strip()]
        return to, str(body.get("subject") or "").strip(), str(body.get("text") or "").strip(), [], body.get("notify") is True

    to: list[str] = []
    fields: dict[str, str] = {}
    files: list[tuple[str, bytes]] = []
    total = 0
    try:
        reader = await request.multipart()
        async for part in reader:
            if part.name == "file" and part.filename is not None:
                if len(files) >= MAIL_MAX_FILES:
                    raise _BadMail(f"Не больше {MAIL_MAX_FILES} файлов")
                data = bytearray()
                while chunk := await part.read_chunk(64 * 1024):
                    data += chunk
                    total += len(chunk)
                    if len(data) > MAIL_MAX_FILE_BYTES:
                        raise _BadMail(f"Файл больше {MAIL_MAX_FILE_BYTES // 1024 // 1024} МБ")
                    if total > MAIL_MAX_TOTAL_BYTES:
                        raise _BadMail(f"Файлы больше {MAIL_MAX_TOTAL_BYTES // 1024 // 1024} МБ в сумме")
                files.append((_safe_filename(part.filename), bytes(data)))
            elif part.name == "to":
                value = (await part.text()).strip()
                if value:
                    to.append(value)
            elif part.name in ("subject", "text", "notify"):
                fields[part.name] = (await part.text()).strip()
    except (ValueError, AssertionError) as exc:
        raise _BadMail("Некорректный запрос") from exc
    return to, fields.get("subject", ""), fields.get("text", ""), files, fields.get("notify") == "true"


@routes.post("/api/mail/send")
async def mail_send(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    try:
        to, subject, text, files, notify = await _read_mail_request(request)
    except _BadMail as exc:
        return json_error(str(exc))
    if not to:
        return json_error("Выберите получателя")
    if not subject:
        return json_error("Укажите тему письма")
    if len(subject) > 200 or len(text) > 10000:
        return json_error("Письмо слишком длинное")
    if any(not data for _, data in files):
        return json_error("Файл пустой")
    await app.diary.send_mail(user, to, subject, text, files or None, notify=notify)
    return web.json_response({"ok": True})


# Сколько писем «Прочитать все» открывает за раз: страница списка — 50.
MAIL_READ_ALL_LIMIT = 100


@routes.post("/api/mail/read-all")
async def mail_read_all(request: web.Request) -> web.Response:
    """Отметить прочитанными непрочитанные письма из списка на экране."""
    try:
        body = await request.json()
        ids = list(dict.fromkeys(int(item) for item in body.get("ids") or []))
    except (ValueError, TypeError, AttributeError):
        return json_error("Некорректный запрос")
    if not ids:
        return web.json_response({"ok": True, "data": {"read": []}})
    if len(ids) > MAIL_READ_ALL_LIMIT:
        return json_error("Слишком много писем за раз")

    app, user = context(request), current_user(request)
    done = await app.diary.mark_mail_read(user, ids)
    for message_id in done:
        await app.cache.mark_mail_read(user.telegram_id, message_id)
    return web.json_response({"ok": True, "data": {"read": done}})


def _mail_id(request: web.Request) -> int | None:
    try:
        return int(request.match_info["message_id"])
    except ValueError:
        return None


@routes.delete("/api/mail/{message_id}")
async def mail_delete(request: web.Request) -> web.Response:
    """Переместить письмо в «Удалённые»."""
    message_id = _mail_id(request)
    if message_id is None:
        return json_error("Некорректный номер письма")
    app, user = context(request), current_user(request)
    await app.diary.delete_mail(user, message_id)
    await app.cache.drop_mail(user.telegram_id, message_id)
    return web.json_response({"ok": True})


@routes.post("/api/mail/{message_id}/restore")
async def mail_restore(request: web.Request) -> web.Response:
    """Вернуть письмо из «Удалённых»."""
    message_id = _mail_id(request)
    if message_id is None:
        return json_error("Некорректный номер письма")
    app, user = context(request), current_user(request)
    await app.diary.restore_mail(user, message_id)
    # Где письмо окажется в списке, знает только школьный сервер.
    await app.cache.expire(user.telegram_id, "mail")
    return web.json_response({"ok": True})


@routes.get("/api/mail/{message_id}")
async def mail_message(request: web.Request) -> web.Response:
    """Письмо целиком. Не кэшируется: открывают его по одному разу."""
    try:
        message_id = int(request.match_info["message_id"])
    except ValueError:
        return json_error("Некорректный номер письма")

    app, user = context(request), current_user(request)
    message = await app.diary.fetch_mail_message(user, message_id)
    # Открытое письмо сервер школы считает прочитанным — так и в кэше списка.
    await app.cache.mark_mail_read(user.telegram_id, message_id)
    return web.json_response({"ok": True, "data": message})
