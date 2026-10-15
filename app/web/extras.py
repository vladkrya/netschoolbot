"""Заметки ученика, второй родитель, выгрузка оценок и настройки напоминаний.

Те же возможности, что в боте (/family, /export, /settings), — для приложения.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import asdict, replace
from typing import Any

from aiohttp import web
from aiogram.types import BufferedInputFile

from ..accounts import has_telegram
from ..domain.models import clamp_reminder_hour
from ..exporting import REPORTS, SCOPES, export_report
from ..netschool.errors import NetSchoolError
from .api import BOT, _content_disposition, context, current_user, json_error

logger = logging.getLogger("netschoolbot.web")

routes = web.RouteTableDef()

# Заметки дальше этого окна в обе стороны не запрашиваются.
NOTES_MAX_RANGE = dt.timedelta(days=400)
# За сколько минут до задачи можно попросить напоминание (сутки — накануне).
NOTE_REMINDERS = (0, 5, 10, 15, 30, 60, 120, 1440)


async def _body(request: web.Request) -> dict[str, Any] | None:
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@routes.post("/api/settings/reminder")
async def reminder_settings(request: web.Request) -> web.Response:
    """Час напоминания о несделанных заданиях (и о контрольных)."""
    app, user = context(request), current_user(request)
    body = await _body(request)
    try:
        hour = clamp_reminder_hour(int((body or {}).get("hour")))
    except (TypeError, ValueError):
        return json_error("Некорректное время")
    updated = await app.users.save(replace(user, notifications=replace(user.notifications, reminder_hour=hour)))
    return web.json_response({"ok": True, "data": asdict(updated.notifications)})


@routes.post("/api/settings/marks-filter")
async def marks_filter_settings(request: web.Request) -> web.Response:
    """О каких новых оценках сообщать: пустой список — обо всех."""
    app, user = context(request), current_user(request)
    body = await _body(request)
    values = (body or {}).get("values")
    if not isinstance(values, list) or not all(v in (2, 3, 4, 5) for v in values):
        return json_error("Некорректный набор оценок")
    chosen = tuple(sorted(set(values)))
    if set(chosen) >= {2, 3, 4, 5}:
        chosen = ()
    updated = await app.users.save(replace(user, notifications=replace(user.notifications, marks_filter=chosen)))
    return web.json_response({"ok": True, "data": asdict(updated.notifications)})


@routes.get("/api/notes")
async def list_notes(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    today = user.today()
    start = _date(request.query.get("from", "")) or today - dt.timedelta(days=7)
    end = _date(request.query.get("to", "")) or today + dt.timedelta(days=60)
    if end < start or end - start > NOTES_MAX_RANGE:
        return json_error("Некорректный период")
    items = await app.notes.between(user.telegram_id, start, end)
    return web.json_response({"ok": True, "data": {"items": items}}, headers={"Cache-Control": "no-store"})


def _note_time(value: Any) -> str | None:
    """Время задачи «ЧЧ:ММ», пустая строка — без времени, None — ошибка."""
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return dt.time.fromisoformat(value).strftime("%H:%M") if len(value) == 5 else None
    except ValueError:
        return None


def _note_remind(value: Any) -> int | None | bool:
    """Напоминание за N минут: None — без него, False — ошибка."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)) or str(value) not in map(str, NOTE_REMINDERS):
        return False
    return int(value)


@routes.post("/api/notes")
async def add_note(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    body = await _body(request) or {}
    day = _date(body.get("date", ""))
    text = str(body.get("text") or "")
    subject = str(body.get("subject") or "")
    at, remind = _note_time(body.get("time")), _note_remind(body.get("remind"))
    if day is None or not text.strip():
        return json_error("Нужны дата и текст")
    if at is None or remind is False:
        return json_error("Некорректное время или напоминание")
    note = await app.notes.add(user.telegram_id, day, text, subject, at=at, remind=remind, tz=user.tz)
    if note is None:
        return json_error("Пустая заметка")
    return web.json_response({"ok": True, "data": note})


@routes.post("/api/notes/{note_id:\\d+}")
async def update_note(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    body = await _body(request) or {}
    note_id = int(request.match_info["note_id"])
    if body.get("delete"):
        if not await app.notes.delete(user.telegram_id, note_id):
            return json_error("Заметка не найдена", status=404)
        return web.json_response({"ok": True})
    changes: dict[str, Any] = {}
    if body.get("done") is not None:
        changes["done"] = bool(body["done"])
    if body.get("text") is not None:
        if not str(body["text"]).strip():
            return json_error("Пустая заметка")
        changes["text"] = str(body["text"])
    if "date" in body:
        if (day := _date(body.get("date", ""))) is None:
            return json_error("Некорректная дата")
        changes["day"] = day
    if "subject" in body:
        changes["subject"] = str(body.get("subject") or "")
    if "time" in body:
        if (at := _note_time(body.get("time"))) is None:
            return json_error("Некорректное время")
        changes["at"] = at
    if "remind" in body:
        if (remind := _note_remind(body.get("remind"))) is False:
            return json_error("Некорректное напоминание")
        changes["remind"] = remind
    if not changes:
        return json_error("Нечего менять")
    note = await app.notes.update(user.telegram_id, note_id, tz=user.tz, **changes)
    if note is None:
        return json_error("Заметка не найдена", status=404)
    return web.json_response({"ok": True, "data": note})


async def _bot_username(request: web.Request) -> str:
    bot = request.config_dict.get(BOT)
    if bot is None:
        return ""
    try:
        return (await bot.me()).username or ""
    except Exception:  # noqa: BLE001
        return ""


async def _rights(body: dict[str, Any]) -> tuple[bool, bool] | None:
    notify, view = body.get("notify", True), body.get("view", True)
    if not isinstance(notify, bool) or not isinstance(view, bool) or not (notify or view):
        return None
    return notify, view


@routes.get("/api/family")
async def family_info(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    following = []
    for item in await app.family.access(user.telegram_id):
        owner = await app.users.get(item["owner_id"])
        following.append({"id": item["owner_id"], "name": owner.label if owner else f"ID {item['owner_id']}",
                          "notify": item["notify"], "view": item["view"]})
    return web.json_response({"ok": True, "data": {
        "followers": [{"id": f["id"], "name": f["name"], "notify": f["notify"], "view": f["view"]}
                      for f in await app.family.followers(user.telegram_id)],
        "following": following,
        "can_invite": user.school.configured,
    }}, headers={"Cache-Control": "no-store"})


@routes.post("/api/family/invite")
async def family_invite(request: web.Request) -> web.Response:
    """Ссылка-приглашение на сайт: там родной заходит через Telegram или
    регистрируется по почте. Ссылка прямо в бота — для тех, у кого Telegram."""
    app, user = context(request), current_user(request)
    if not user.school.configured:
        return json_error("Сначала войдите в «Сетевой город»")
    rights = await _rights(await _body(request) or {})
    if rights is None:
        return json_error("Выберите, что будет доступно")
    code = await app.family.issue_invite(user.telegram_id, notify=rights[0], view=rights[1])
    username = await _bot_username(request)
    return web.json_response({"ok": True, "data": {
        "url": f"{app.settings.web.miniapp_url}/?invite={code}",
        "telegram": f"https://t.me/{username}?start=f_{code}" if username else "",
    }})


@routes.get("/api/family/invite-info")
async def family_invite_info(request: web.Request) -> web.Response:
    """Чьё приглашение и что оно даёт — до входа: страница показывает его
    и предлагает войти через Telegram или по почте."""
    app = context(request)
    code = request.query.get("code", "")[:64]
    invite = await app.family.invite(code) if code else None
    owner = await app.users.get(invite["owner_id"]) if invite else None
    if invite is None or owner is None:
        return json_error("Приглашение недействительно: истекло или уже использовано. "
                          "Попросите прислать новое.", status=404)
    username = await _bot_username(request)
    return web.json_response({"ok": True, "data": {
        "name": owner.label, "school": owner.school.name,
        "notify": invite["notify"], "view": invite["view"],
        "telegram": f"https://t.me/{username}?start=f_{code}" if username else "",
    }}, headers={"Cache-Control": "no-store"})


@routes.post("/api/family/accept")
async def family_accept(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    code = str((await _body(request) or {}).get("code") or "")[:64]
    invite = await app.family.invite(code) if code else None
    if invite and invite["owner_id"] == user.telegram_id:
        return json_error("Это ваше собственное приглашение — перешлите его тому, кого приглашаете.")
    account = await app.accounts.by_user(user.telegram_id) if app.accounts else None
    name = user.display_name or (account["email"].split("@")[0] if account else "")
    owner_id = await app.family.accept(code, user.telegram_id, name)
    if owner_id is None:
        return json_error("Приглашение недействительно: истекло, уже использовано "
                          "или у ученика уже много приглашённых.")
    if invite and invite["view"]:
        # Только что принятый дневник — сразу его и показываем.
        await app.family.set_viewing(user.telegram_id, owner_id)
    await _tell_owner(request, owner_id, name)
    return await family_info(request)


async def _tell_owner(request: web.Request, owner_id: int, name: str) -> None:
    bot = request.config_dict.get(BOT)
    if bot is None or not has_telegram(owner_id):
        return
    try:
        await bot.send_message(owner_id, f"👪 {name or 'Приглашённый'} принял приглашение. "
                                         "Изменить его доступ или убрать: Настройки → Родные.")
    except Exception as exc:  # noqa: BLE001 — уведомление вторично
        logger.info("Владельцу %s не сообщили о приглашённом: %s", owner_id, exc)


@routes.post("/api/family/rights")
async def family_rights(request: web.Request) -> web.Response:
    """Ученик меняет, что доступно приглашённому."""
    app, user = context(request), current_user(request)
    body = await _body(request) or {}
    try:
        other = int(body.get("id"))
    except (TypeError, ValueError):
        return json_error("Некорректный запрос")
    notify, view = body.get("notify"), body.get("view")
    if not isinstance(notify, bool) or not isinstance(view, bool):
        return json_error("Некорректный запрос")
    await app.family.set_rights(user.telegram_id, other, notify=notify, view=view)
    return await family_info(request)


@routes.post("/api/family/remove")
async def family_remove(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    body = await _body(request) or {}
    try:
        other = int(body.get("id"))
    except (TypeError, ValueError):
        return json_error("Некорректный запрос")
    if body.get("leave"):
        await app.family.remove(other, user.telegram_id)
    else:
        await app.family.remove(user.telegram_id, other)
    return await family_info(request)


@routes.post("/api/view")
async def switch_view(request: web.Request) -> web.Response:
    """Свой дневник или дневник ученика, пригласившего на просмотр."""
    from . import viewing

    app, user = context(request), current_user(request)
    raw = (await _body(request) or {}).get("id")
    if raw is None:
        if not user.school.configured:
            return json_error("Своего дневника нет — войдите в «Сетевой город»")
        await app.family.set_viewing(user.telegram_id, None)
        return web.json_response({"ok": True})
    try:
        owner_id = int(raw)
    except (TypeError, ValueError):
        return json_error("Некорректный запрос")
    if owner_id not in {owner.telegram_id for owner in await viewing.choices(app, user)}:
        return json_error("Нет доступа к этому дневнику", status=403)
    await app.family.set_viewing(user.telegram_id, owner_id)
    return web.json_response({"ok": True})


@routes.get("/api/export.xlsx")
async def export_download(request: web.Request) -> web.Response:
    """Прежний адрес выгрузки — для приложений, открытых до обновления."""
    return await _export_file(request, request.query.get("scope", "period"))


@routes.get("/api/export/{scope}.xlsx")
async def export_file(request: web.Request) -> web.Response:
    """Оценки файлом — как вложение из ДЗ: страница ожидания сперва собирает
    книгу (prepare), затем браузер открывает прямую ссылку и сам решает,
    показать файл просмотром или сохранить."""
    return await _export_file(request, request.match_info["scope"])


# Собранная книга лежит недолго: только чтобы страница ожидания после
# prepare получила тот же файл, а не собирала его заново ещё минуту.
EXPORT_CACHE_S = 600

EXPORT_CAPTIONS = {
    "grades": "📊 Оценки: лист «Итоги» — средние по предметам, «Оценки» — все работы.",
    "absences": "📊 Пропуски: по предметам и списком.",
    "homework": "📊 Домашние задания за период.",
    "lessons": "📊 Журнал уроков: темы, учителя, оценки и пропуски.",
}


async def _export_file(request: web.Request, scope: str) -> web.Response:
    app, user = context(request), current_user(request)
    report = request.query.get("report", "grades")
    if scope not in SCOPES or report not in REPORTS:
        return json_error("Некорректный период")
    cached = app.settings.attachments_dir / f"export-{user.telegram_id}-{report}-{scope}"
    name_file = cached.with_suffix(".name")
    if (cached.exists() and name_file.exists()
            and time.time() - cached.stat().st_mtime < EXPORT_CACHE_S and not request.query.get("prepare")):
        data, name = cached.read_bytes(), name_file.read_text(encoding="utf-8")
    else:
        data, name = await export_report(app, user, scope, report)
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
            name_file.write_text(name, encoding="utf-8")
        except OSError as exc:
            logger.debug("Выгрузка не сохранена в кэш: %s", exc)
    if request.query.get("prepare"):
        return web.json_response({"ok": True, "data": {"size": len(data)}})
    return web.Response(body=data, headers={
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": _content_disposition(name),
        "Cache-Control": "no-store",
    })


@routes.post("/api/export/send")
async def export_send(request: web.Request) -> web.Response:
    """Оценки файлом в чат с ботом: внутри Telegram скачать файл из окна нельзя."""
    app, user = context(request), current_user(request)
    body = await _body(request) or {}
    scope, report = body.get("scope", "period"), body.get("report", "grades")
    if scope not in SCOPES or report not in REPORTS:
        return json_error("Некорректный период")
    bot = request.config_dict.get(BOT)
    if bot is None or not has_telegram(user.telegram_id):
        return json_error("Telegram не привязан — скачайте файл", reason="no_telegram")
    try:
        data, name = await export_report(app, user, scope, report)
    except NetSchoolError:
        raise
    await bot.send_document(
        user.telegram_id, BufferedInputFile(data, filename=name),
        caption=EXPORT_CAPTIONS[report],
    )
    return web.json_response({"ok": True})
