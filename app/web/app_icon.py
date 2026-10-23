"""Своя иконка приложения на экране «Домой».

Пользователь загружает картинку в настройках, но работает она только после
проверки владельцем (TG_ADMIN_ID) на вкладке «Иконки» админ-настроек.
У каждой иконки свой токен — часть адреса картинки в манифесте и в
apple-touch-icon. Отказ, замена или «Стандартная иконка» стирают токен:
старый адрес отдаёт стандартную иконку, и Android при следующей сверке
манифеста вернёт её на экран. iPhone картинку с экрана «Домой» не
перечитывает — там она меняется только переустановкой.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import logging
import secrets
import time

from aiohttp import web

from . import solver
from .api import context, current_user, json_error

logger = logging.getLogger("netschoolbot.web.app_icon")

routes = web.RouteTableDef()
page_routes = web.RouteTableDef()

SIDE = 512
SIZES = (180, 192, 512)
# Браузер сам ужимает картинку до 512×512 (app-icon.js), так что больше
# этого присылать незачем; aiohttp и так не примет тело больше 1 МБ.
MAX_UPLOAD = 700 * 1024
MAX_PIXELS = 40_000_000
FORMATS = ("PNG", "JPEG", "WEBP")
# Пуши владельцу о новых иконках — включены, пока он их не выключит.
PUSH_OFF_FLAG = "icon_push_off"


class BadImage(ValueError):
    pass


def prepare(raw: bytes) -> bytes:
    """Картинка пользователя → квадратный PNG 512×512 (середина, без EXIF)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(raw), formats=FORMATS) as image:
            if image.width * image.height > MAX_PIXELS:
                raise BadImage("Картинка слишком большая")
            image = ImageOps.exif_transpose(image).convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise BadImage("Нужна картинка PNG, JPEG или WebP") from exc
    side = min(image.size)
    if side < 64:
        raise BadImage("Картинка слишком маленькая — нужно хотя бы 64×64")
    left, top = (image.width - side) // 2, (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side)).resize((SIDE, SIDE), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def resized(png: bytes, size: int) -> bytes:
    from PIL import Image

    if size == SIDE:
        return png
    with Image.open(io.BytesIO(png)) as image:
        out = io.BytesIO()
        image.resize((size, size), Image.LANCZOS).save(out, "PNG", optimize=True)
        return out.getvalue()


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


class AppIconRepository:
    def __init__(self, db) -> None:
        self._db = db

    async def add(self, telegram_id: int, image: bytes) -> tuple[int, bool]:
        """Новая иконка на проверку. Прежняя непроверенная заменяется.
        Возвращает id и была ли непроверенная до этого."""
        async with self._db.transaction() as db:
            cursor = await db.execute(
                "DELETE FROM app_icons WHERE telegram_id = ? AND status = 'pending'", (telegram_id,))
            replaced = cursor.rowcount > 0
            # Прошлый отказ больше не показываем — человек прислал новую.
            await db.execute("DELETE FROM app_icons WHERE telegram_id = ? AND status = 'rejected'", (telegram_id,))
            cursor = await db.execute(
                "INSERT INTO app_icons (telegram_id, token, status, image, created_at) VALUES (?, ?, 'pending', ?, ?)",
                (telegram_id, secrets.token_urlsafe(16), image, time.time()),
            )
            return cursor.lastrowid, replaced

    async def get(self, icon_id: int):
        return await self._db.fetch_one("SELECT * FROM app_icons WHERE id = ?", (icon_id,))

    async def of_user(self, telegram_id: int) -> dict:
        rows = await self._db.fetch_all(
            "SELECT * FROM app_icons WHERE telegram_id = ? AND status IN ('pending', 'approved', 'rejected')",
            (telegram_id,))
        return {row["status"]: row for row in rows}

    async def active_token(self, telegram_id: int) -> str:
        row = await self._db.fetch_one(
            "SELECT token FROM app_icons WHERE telegram_id = ? AND status = 'approved'", (telegram_id,))
        return row["token"] if row else ""

    async def by_token(self, token: str):
        return await self._db.fetch_one(
            "SELECT image FROM app_icons WHERE token = ? AND status = 'approved'", (token,))

    async def listing(self, status: str, limit: int = 50):
        return await self._db.fetch_all(
            "SELECT * FROM app_icons WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit))

    async def approve(self, icon_id: int) -> None:
        row = await self.get(icon_id)
        async with self._db.transaction() as db:
            # Одобренная у человека одна: прежняя больше не открывается.
            await db.execute(
                "DELETE FROM app_icons WHERE telegram_id = ? AND status = 'approved'", (row["telegram_id"],))
            await db.execute(
                "UPDATE app_icons SET status = 'approved', decided_at = ? WHERE id = ?", (time.time(), icon_id))

    async def reject(self, icon_id: int) -> None:
        """Отказ или отзыв одобренной: токен стирается, картинка тоже —
        хранить её незачем, человеку достаточно знать, что отказали."""
        await self._db.execute(
            "UPDATE app_icons SET status = 'rejected', token = NULL, image = X'', decided_at = ? WHERE id = ?",
            (time.time(), icon_id),
        )

    async def reset(self, telegram_id: int) -> None:
        await self._db.execute("DELETE FROM app_icons WHERE telegram_id = ?", (telegram_id,))


def repo(request: web.Request) -> AppIconRepository:
    return AppIconRepository(context(request).db)


async def icon_url(app, base: str, telegram_id: int, size: int) -> str:
    """Адрес иконки пользователя: своя одобренная или стандартная."""
    token = await AppIconRepository(app.db).active_token(telegram_id)
    if token:
        return f"{base}/app-icon/{token}/{size}.png"
    return f"{base}/static/{'apple-touch-icon' if size == 180 else f'icon-{size}'}.png"


def _push(app, telegram_id: int, title: str, body: str, kind: str) -> None:
    """Пуш в фоне: загрузка и решение владельца его не ждут."""
    from .push import PushSender

    async def send() -> None:
        try:
            await PushSender(app.settings.push, app.miniapp).send(telegram_id, title, body, kind=kind)
        except Exception:  # noqa: BLE001 — пуш не должен ломать загрузку
            logger.exception("Пуш об иконке не ушёл")

    task = asyncio.create_task(send(), name=f"app-icon-push-{telegram_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


_tasks: set[asyncio.Task] = set()


async def push_on(app) -> bool:
    from ..db.repositories import AppFlagRepository

    return not await AppFlagRepository(app.db).get(PUSH_OFF_FLAG)


async def _body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def _mine(request: web.Request) -> dict:
    app = context(request)
    icons = await repo(request).of_user(current_user(request).telegram_id)
    base = app.settings.web.miniapp_path.rstrip("/")
    approved, pending = icons.get("approved"), icons.get("pending")
    return {
        "active": f"{base}/app-icon/{approved['token']}/512.png" if approved else None,
        "pending": _data_url(pending["image"]) if pending else None,
        "rejected": "rejected" in icons,
    }


@routes.get("/api/app-icon")
async def my_icon(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "data": await _mine(request)})


@routes.post("/api/app-icon")
async def upload_icon(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    raw = (await _body(request)).get("image")
    if not isinstance(raw, str) or "," not in raw:
        return json_error("Некорректный запрос")
    try:
        data = base64.b64decode(raw.split(",", 1)[1], validate=True)
    except (binascii.Error, ValueError):
        return json_error("Некорректный запрос")
    if len(data) > MAX_UPLOAD:
        return json_error("Картинка слишком большая")
    try:
        image = await asyncio.to_thread(prepare, data)
    except BadImage as exc:
        return json_error(str(exc))
    _, replaced = await repo(request).add(user.telegram_id, image)
    admin = app.settings.telegram.admin_id
    # Замена ещё не проверенной — владелец о ней уже знает.
    if admin and admin != user.telegram_id and not replaced and await push_on(app):
        _push(app, admin, "Новая иконка на проверку", f"{user.label} — Админ-настройки → Иконки", "app-icon")
    return web.json_response({"ok": True, "data": await _mine(request)})


@routes.post("/api/app-icon/reset")
async def reset_icon(request: web.Request) -> web.Response:
    await repo(request).reset(current_user(request).telegram_id)
    return web.json_response({"ok": True, "data": await _mine(request)})


def _owner(request: web.Request) -> bool:
    return solver.is_owner(context(request), current_user(request))


async def _admin_state(request: web.Request) -> dict:
    app = context(request)
    items = []
    for status in ("pending", "approved"):
        for row in await repo(request).listing(status):
            user = await app.users.get(row["telegram_id"])
            items.append({
                "id": row["id"],
                "status": status,
                "image": _data_url(await asyncio.to_thread(resized, row["image"], 192)),
                "user": user.label if user else f"ID {row['telegram_id']}",
                "school": user.school.name if user else "",
                "created_at": row["created_at"],
            })
    return {"items": items, "push": await push_on(app)}


@routes.get("/api/admin/icons")
async def admin_icons(request: web.Request) -> web.Response:
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    return web.json_response({"ok": True, "data": await _admin_state(request)})


@routes.post("/api/admin/icons/{icon_id:\\d+}")
async def admin_decide(request: web.Request) -> web.Response:
    """Принять иконку или отказать (одобренную — отозвать)."""
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    decision = (await _body(request)).get("decision")
    row = await repo(request).get(int(request.match_info["icon_id"]))
    if decision not in ("approve", "reject") or row is None:
        return json_error("Некорректный запрос")
    app = context(request)
    if decision == "approve":
        if row["status"] != "pending":
            return json_error("Эту иконку уже проверили", status=409)
        await repo(request).approve(row["id"])
        _push(app, row["telegram_id"], "Иконка одобрена",
              "Своя иконка готова. На iPhone удалите приложение с экрана «Домой» и добавьте снова", "app-icon")
    else:
        if row["status"] not in ("pending", "approved"):
            return json_error("Эту иконку уже проверили", status=409)
        await repo(request).reject(row["id"])
        _push(app, row["telegram_id"], "Иконка не прошла проверку",
              "Вернулась стандартная иконка. Можно загрузить другую в настройках", "app-icon")
    return web.json_response({"ok": True, "data": await _admin_state(request)})


@routes.post("/api/admin/icons/push")
async def admin_icon_push(request: web.Request) -> web.Response:
    """Тумблер «Пуши о новых иконках»."""
    from ..db.repositories import AppFlagRepository

    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    body = await _body(request)
    if "on" not in body:
        return json_error("Некорректный запрос")
    await AppFlagRepository(context(request).db).set(PUSH_OFF_FLAG, not body["on"])
    return web.json_response({"ok": True, "data": {"on": await push_on(context(request))}})


@page_routes.get("/app-icon/{token}/{size:\\d+}.png")
async def icon_image(request: web.Request) -> web.StreamResponse:
    """Картинка иконки без входа: её берут лаунчер и Safari, без cookie.
    Отозванный или чужой токен — стандартная иконка."""
    size = int(request.match_info["size"])
    if size not in SIZES:
        raise web.HTTPNotFound()
    row = await repo(request).by_token(request.match_info["token"])
    if row is None:
        base = context(request).settings.web.miniapp_path.rstrip("/")
        raise web.HTTPFound(f"{base}/static/{'apple-touch-icon' if size == 180 else f'icon-{size}'}.png")
    return web.Response(
        body=await asyncio.to_thread(resized, row["image"], size),
        content_type="image/png",
        # Недолго: после отзыва браузер должен быстро увидеть стандартную.
        headers={"Cache-Control": "public, max-age=3600"},
    )
