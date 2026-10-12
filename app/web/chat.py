"""Школьные чаты: класс, параллель, школа и общий чат по возрастным группам.

Пускаем только учеников — это проверяется входом в «Сетевой город», а не
словами человека. Показываем только имя и номер класса, и только после
согласия. Администратор (TG_ADMIN_ID) видит все чаты и пишет как «admin»
или от своего профиля.

Сообщения приходят опросом: открытый чат раз в несколько секунд спрашивает
новые (`after`) и изменённые (`since`). Так работает и через прокси, и при
плохой связи, где долгие соединения рвутся.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from aiohttp import web

from ..chat import rules
from ..chat.repository import ChatRepository
from ..netschool.errors import NetSchoolError
from .api import context, current_user, json_error

routes = web.RouteTableDef()
logger = logging.getLogger("netschoolbot.chat")

# Профиль перепроверяется в «Сетевом городе» не реже этого: перевели в
# другой класс, выпустился, вышел из аккаунта ученика.
REVERIFY_AFTER_S = 3 * 24 * 3600
# Не чаще одного сообщения в столько секунд.
SEND_INTERVAL_S = 1.5
# Столько жалоб от разных людей — и сообщение прячется до решения модератора.
AUTO_HIDE_REPORTS = 3
REACTIONS = ("👍", "❤️", "😂", "😮", "😢", "🔥")
POLL_OPTIONS = (2, 6)
POLL_OPTION_LEN = 80
MENTION = re.compile(r"@([A-Za-zА-Яа-яЁё]{2,30})")

RULES_TEXT = [
    "В чатах только ученики: вход проверяется через «Сетевой город».",
    "Другим видно только твоё имя и номер класса — без фамилии.",
    "Администрация видит все чаты и может удалять сообщения и блокировать нарушителей.",
    "Нельзя: травля, угрозы, наркотики, призывы к самоповреждению, чужие фото и личные данные.",
    "В общем чате нельзя оставлять ссылки, телефоны и ники.",
    "Сообщения хранятся на сервере, даже если их удалили, — для разбора жалоб.",
]


def repo(request: web.Request) -> ChatRepository:
    return ChatRepository(context(request).db)


def is_admin(request: web.Request) -> bool:
    admin_id = context(request).settings.telegram.admin_id
    return bool(admin_id) and current_user(request).telegram_id == admin_id


class ChatDenied(Exception):
    def __init__(self, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.status = status


async def verify(request: web.Request) -> dict[str, Any]:
    """Проверить в «Сетевом городе», что это ученик, и узнать класс."""
    user = current_user(request)
    identity = await context(request).diary.fetch_chat_identity(user)
    if not identity["is_student"]:
        raise ChatDenied("Чаты только для учеников: войдите в дневник под аккаунтом ученика")
    grade = rules.grade_of(identity["class_name"])
    if grade is None:
        raise ChatDenied("Не удалось узнать класс в «Сетевом городе»")
    return {
        "netschool_user_id": identity["user_id"],
        "school_key": rules.school_key(user.school.url, user.school.name),
        "school_name": user.school.name,
        "class_name": identity["class_name"].strip(),
        "grade": grade,
        "first_name": rules.first_name(identity["name"]),
    }


async def current_profile(request: web.Request) -> dict[str, Any] | None:
    """Профиль, перепроверенный, если давно не проверяли.

    Школьный сервер недоступен — живём со старыми данными, но если он ответил,
    что это больше не ученик, профиль удаляется.
    """
    chats, uid = repo(request), current_user(request).telegram_id
    profile = await chats.profile(uid)
    if profile and time.time() - profile["verified_at"] > REVERIFY_AFTER_S:
        try:
            profile = await chats.save_profile(uid, **await verify(request))
        except ChatDenied:
            await chats.delete_profile(uid)
            return None
        except NetSchoolError:
            pass
    return profile


def profile_rooms(profile: dict[str, Any] | None) -> list[rules.Room]:
    if not profile:
        return []
    return rules.rooms_for(profile["school_key"], profile["class_name"], profile["grade"], profile["school_name"])


async def visible_rooms(request: web.Request, profile: dict[str, Any] | None) -> list[rules.Room]:
    """Свои комнаты, а у администратора — ещё все остальные."""
    own = profile_rooms(profile)
    if not is_admin(request):
        return own
    seen = {room.id for room in own}
    extra: list[rules.Room] = []
    for first, last in rules.AGE_BANDS:
        room_id = f"global:{first}-{last}"
        if room_id not in seen:
            extra.append(rules.describe(room_id))  # type: ignore[arg-type]
            seen.add(room_id)
    for room_id in await repo(request).active_rooms():
        if room_id not in seen and (room := rules.describe(room_id)):
            extra.append(room)
            seen.add(room_id)
    return own + extra


async def room_access(request: web.Request, room_id: str) -> tuple[rules.Room, dict[str, Any] | None]:
    profile = await current_profile(request)
    for room in profile_rooms(profile):
        if room.id == room_id:
            return room, profile
    if is_admin(request) and (room := rules.describe(room_id)):
        return room, profile
    # Модератор школы заходит во все её комнаты — разбирать жалобы.
    if (room := rules.describe(room_id)) and await repo(request).is_moderator(
            current_user(request).telegram_id, rules.room_school(room_id)):
        return room, profile
    if not profile and not is_admin(request):
        raise ChatDenied("Сначала вступите в чаты")
    raise ChatDenied("Нет доступа к этому чату", status=404)


def display(profile: dict[str, Any] | None, admin: bool) -> tuple[str, int | None, bool]:
    """Подпись автора: «admin» или имя с номером класса."""
    if admin and (not profile or profile["as_admin"]):
        return rules.ADMIN_NAME, None, True
    assert profile is not None
    return profile["first_name"], profile["grade"], False


async def message_json(
    request: web.Request, rows: list[dict[str, Any]], *, moderator: bool,
) -> list[dict[str, Any]]:
    chats, uid = repo(request), current_user(request).telegram_id
    reactions = await chats.reactions([row["id"] for row in rows])
    votes = await chats.votes([row["id"] for row in rows if row["kind"] == "poll"])
    reply_ids = sorted({row["reply_to"] for row in rows if row["reply_to"]})
    replies = {row["id"]: row for row in await _messages_by_id(chats, reply_ids)}
    result = []
    for row in rows:
        hidden_for_me = (row["deleted_at"] is not None) or (row["hidden"] and not moderator)
        counts: dict[str, int] = {}
        mine: list[str] = []
        for who, emoji in reactions.get(row["id"], []):
            counts[emoji] = counts.get(emoji, 0) + 1
            if who == uid:
                mine.append(emoji)
        reply = replies.get(row["reply_to"]) if row["reply_to"] else None
        result.append({
            "id": row["id"],
            "author": row["author_name"],
            "grade": row["author_grade"],
            "admin": bool(row["is_admin"]),
            "mine": row["author_id"] == uid,
            "text": None if hidden_for_me else row["text"],
            "deleted": row["deleted_at"] is not None,
            "hidden": bool(row["hidden"]),
            "pinned": bool(row["pinned"]),
            "at": row["created_at"],
            "reply": {
                "id": reply["id"], "author": reply["author_name"],
                "text": None if reply["deleted_at"] is not None or (reply["hidden"] and not moderator)
                else reply["text"][:140],
            } if reply else None,
            "reactions": counts,
            "my_reactions": mine,
            "poll": _poll_json(row, votes.get(row["id"], []), uid) if row["kind"] == "poll" and not hidden_for_me else None,
        })
    return result


def _poll_json(row: dict[str, Any], votes: list[tuple[int, int]], uid: int) -> dict[str, Any]:
    options = json.loads(row["payload"] or "{}").get("options", [])
    counts = [0] * len(options)
    my = None
    for who, option in votes:
        if 0 <= option < len(counts):
            counts[option] += 1
            if who == uid:
                my = option
    return {"options": [{"text": text, "votes": counts[i]} for i, text in enumerate(options)],
            "total": sum(counts), "my": my}


async def _messages_by_id(chats: ChatRepository, ids: list[int]) -> list[dict[str, Any]]:
    rows = []
    for message_id in ids:
        if row := await chats.message(message_id):
            rows.append(row)
    return rows


async def can_moderate(request: web.Request, room_id: str) -> bool:
    if is_admin(request):
        return True
    school = rules.room_school(room_id)
    return await repo(request).is_moderator(current_user(request).telegram_id, school)


async def overview(request: web.Request) -> dict[str, Any]:
    chats, uid = repo(request), current_user(request).telegram_id
    profile = await current_profile(request)
    admin = is_admin(request)
    rooms = await visible_rooms(request, profile)
    ids = [room.id for room in rooms]
    last = await chats.last_messages(ids)
    unread = await chats.unread(uid, ids)
    reads = await chats.reads(uid)
    schools = None if admin else await chats.moderated_schools(uid)
    reports = len(await chats.reported(schools)) if admin or schools else 0
    return {
        "joined": profile is not None,
        "admin": admin,
        "moderator": admin or bool(schools),
        "reports": reports,
        "reactions": list(REACTIONS),
        "as_admin": bool(profile["as_admin"]) if profile else admin,
        "rules": RULES_TEXT,
        "profile": {
            "first_name": profile["first_name"], "class_name": profile["class_name"],
            "grade": profile["grade"], "banned": bool(profile["banned"]),
            "muted_until": profile["muted_until"],
        } if profile else None,
        "rooms": [{
            "id": room.id, "kind": room.kind, "title": room.title, "subtitle": room.subtitle,
            "unread": unread.get(room.id, 0), "muted": bool(reads.get(room.id, {}).get("muted")),
            "last": {
                "author": last[room.id]["author_name"], "text": last[room.id]["text"][:120],
                "at": last[room.id]["created_at"],
            } if room.id in last else None,
        } for room in rooms],
    }


def _denied(exc: ChatDenied) -> web.Response:
    return json_error(str(exc), status=exc.status, reason="chat")


@routes.get("/api/chat")
async def chat_overview(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "data": await overview(request)})


@routes.post("/api/chat/join")
async def chat_join(request: web.Request) -> web.Response:
    """Вступить: согласие на показ имени и класса + проверка ученика."""
    try:
        body = await request.json()
    except ValueError:
        return json_error("Некорректный запрос")
    if body.get("consent") is not True:
        return json_error("Нужно согласие с правилами")
    try:
        data = await verify(request)
    except ChatDenied as exc:
        return _denied(exc)
    await repo(request).save_profile(current_user(request).telegram_id, consent=True, **data)
    return web.json_response({"ok": True, "data": await overview(request)})


@routes.post("/api/chat/leave")
async def chat_leave(request: web.Request) -> web.Response:
    """Выйти из чатов: профиль удаляется, написанное остаётся на сервере."""
    await repo(request).delete_profile(current_user(request).telegram_id)
    return web.json_response({"ok": True, "data": await overview(request)})


@routes.post("/api/chat/identity")
async def chat_identity(request: web.Request) -> web.Response:
    """Администратор: писать как «admin» или от своего профиля."""
    if not is_admin(request):
        return json_error("Только для администратора", status=403)
    body = await request.json()
    profile = await repo(request).profile(current_user(request).telegram_id)
    if not profile:
        return json_error("Чтобы писать от себя, вступите в чаты")
    await repo(request).set_profile_field(profile["telegram_id"], "as_admin", int(bool(body.get("as_admin"))))
    return web.json_response({"ok": True, "data": await overview(request)})


@routes.get("/api/chat/rooms/{room}/messages")
async def chat_messages(request: web.Request) -> web.Response:
    room_id = request.match_info["room"]
    try:
        room, _ = await room_access(request, room_id)
    except ChatDenied as exc:
        return _denied(exc)

    def number(name: str) -> int | None:
        try:
            return int(request.query[name]) if name in request.query else None
        except ValueError:
            return None

    after, before = number("after"), number("before")
    try:
        since = float(request.query.get("since", "0"))
    except ValueError:
        since = 0.0
    chats = repo(request)
    now = time.time()
    rows = await chats.messages(room_id, after=after, before=before)
    changed = await chats.changed_since(room_id, since, after) if after is not None and since else []
    moderator = await can_moderate(request, room_id)
    pinned = await chats.pinned(room_id)
    return web.json_response({"ok": True, "data": {
        "room": {"id": room.id, "kind": room.kind, "title": room.title, "subtitle": room.subtitle},
        "pinned": (await message_json(request, [pinned], moderator=moderator))[0] if pinned else None,
        "muted": await chats.is_muted(current_user(request).telegram_id, room_id),
        "admin": is_admin(request),
        "messages": await message_json(request, rows, moderator=moderator),
        "changed": await message_json(request, changed, moderator=moderator),
        "moderator": moderator,
        "now": now,
    }})


@routes.post("/api/chat/rooms/{room}/messages")
async def chat_send(request: web.Request) -> web.Response:
    room_id = request.match_info["room"]
    try:
        room, profile = await room_access(request, room_id)
    except ChatDenied as exc:
        return _denied(exc)
    try:
        body = await request.json()
    except ValueError:
        return json_error("Некорректный запрос")
    text = str(body.get("text") or "").strip()
    admin = is_admin(request)
    if profile and not admin:
        if profile["banned"]:
            return json_error("Вы заблокированы в чатах", status=403, reason="chat")
        if profile["muted_until"] > time.time():
            minutes = int((profile["muted_until"] - time.time()) // 60) + 1
            return json_error(f"Писать можно будет через {minutes} мин", status=403, reason="chat")
        if time.time() - profile["last_sent_at"] < SEND_INTERVAL_S:
            return json_error("Слишком часто — подождите секунду", status=429, reason="chat")
    if not admin and (problem := rules.check_text(text, global_room=room.is_global)):
        return json_error(problem, reason="chat")
    if admin and not text.strip():
        return json_error("Пустое сообщение")
    kind, payload = "text", None
    if isinstance(body.get("poll"), dict):
        if room.is_global:
            return json_error("Опросы — в чатах своей школы")
        options = [str(item).strip()[:POLL_OPTION_LEN] for item in body["poll"].get("options") or []]
        options = [item for item in options if item]
        if not POLL_OPTIONS[0] <= len(options) <= POLL_OPTIONS[1]:
            return json_error(f"Вариантов — от {POLL_OPTIONS[0]} до {POLL_OPTIONS[1]}")
        if not admin and (problem := rules.check_text(" / ".join(options), global_room=False)):
            return json_error(problem, reason="chat")
        kind, payload = "poll", json.dumps({"options": options}, ensure_ascii=False)
    reply_to = body.get("reply_to")
    chats = repo(request)
    if reply_to is not None:
        target = await chats.message(int(reply_to)) if str(reply_to).isdigit() else None
        if not target or target["room"] != room_id:
            return json_error("Сообщение для ответа не найдено")
        reply_to = target["id"]
    name, grade, as_admin = display(profile, admin)
    message_id = await chats.add_message(
        room_id, author_id=current_user(request).telegram_id, author_name=name, author_grade=grade,
        is_admin=as_admin, text=text[:rules.MAX_TEXT], reply_to=reply_to, kind=kind, payload=payload,
    )
    # Уведомления — фоном: отправитель не ждёт Telegram и push.
    task = asyncio.create_task(notify_mentions(context(request), room, message_id))
    _background.add(task)
    task.add_done_callback(_background.discard)
    await chats.mark_read(current_user(request).telegram_id, room_id, message_id)
    row = await chats.message(message_id)
    assert row is not None
    payload = await message_json(request, [row], moderator=await can_moderate(request, room_id))
    return web.json_response({"ok": True, "data": payload[0]})


@routes.post("/api/chat/rooms/{room}/read")
async def chat_read(request: web.Request) -> web.Response:
    room_id = request.match_info["room"]
    try:
        await room_access(request, room_id)
    except ChatDenied as exc:
        return _denied(exc)
    body = await request.json()
    last_id = body.get("last_id")
    if isinstance(last_id, int) and last_id > 0:
        await repo(request).mark_read(current_user(request).telegram_id, room_id, last_id)
    return web.json_response({"ok": True})


# ─────────────────────── Реакции, жалобы, модерация ───────────────────────


async def _message_for(request: web.Request) -> tuple[dict[str, Any], rules.Room]:
    try:
        message_id = int(request.match_info["message_id"])
    except ValueError:
        raise ChatDenied("Сообщение не найдено", status=404) from None
    message = await repo(request).message(message_id)
    if not message:
        raise ChatDenied("Сообщение не найдено", status=404)
    room, _ = await room_access(request, message["room"])
    return message, room


async def _one(request: web.Request, message_id: int) -> dict[str, Any]:
    row = await repo(request).message(message_id)
    assert row is not None
    return (await message_json(request, [row], moderator=await can_moderate(request, row["room"])))[0]


@routes.post("/api/chat/messages/{message_id}/react")
async def chat_react(request: web.Request) -> web.Response:
    try:
        message, _ = await _message_for(request)
    except ChatDenied as exc:
        return _denied(exc)
    emoji = str((await request.json()).get("emoji") or "")
    if emoji not in REACTIONS:
        return json_error("Такой реакции нет")
    if message["deleted_at"] is not None:
        return json_error("Сообщение удалено")
    await repo(request).toggle_reaction(message["id"], current_user(request).telegram_id, emoji)
    return web.json_response({"ok": True, "data": await _one(request, message["id"])})


@routes.post("/api/chat/messages/{message_id}/report")
async def chat_report(request: web.Request) -> web.Response:
    """Жалоба. После трёх от разных людей сообщение прячется до решения модератора."""
    try:
        message, _ = await _message_for(request)
    except ChatDenied as exc:
        return _denied(exc)
    uid = current_user(request).telegram_id
    if message["author_id"] == uid:
        return json_error("На своё сообщение жаловаться нельзя")
    count = await repo(request).report(message["id"], uid)
    if count >= AUTO_HIDE_REPORTS and not message["hidden"]:
        await repo(request).set_flag(message["id"], "hidden", 1)
        await repo(request).log(0, "auto_hide", str(message["id"]), f"{count} жалоб")
    return web.json_response({"ok": True, "data": await _one(request, message["id"])})


@routes.post("/api/chat/messages/{message_id}/moderate")
async def chat_moderate(request: web.Request) -> web.Response:
    """Скрыть / показать / удалить / закрепить. Своё сообщение автор может удалить сам."""
    try:
        message, _ = await _message_for(request)
    except ChatDenied as exc:
        return _denied(exc)
    action = str((await request.json()).get("action") or "")
    uid = current_user(request).telegram_id
    moderator = await can_moderate(request, message["room"])
    own_delete = action == "delete" and message["author_id"] == uid
    if not moderator and not own_delete:
        return json_error("Нужны права модератора", status=403, reason="chat")
    chats = repo(request)
    if action == "hide":
        await chats.set_flag(message["id"], "hidden", 1)
    elif action == "unhide":
        await chats.set_flag(message["id"], "hidden", 0)
    elif action == "delete":
        await chats.set_flag(message["id"], "deleted_at", time.time(), actor=uid)
    elif action == "pin":
        await chats.set_flag(message["id"], "pinned", 1)
    elif action == "unpin":
        await chats.set_flag(message["id"], "pinned", 0)
    elif action == "dismiss":
        pass
    else:
        return json_error("Неизвестное действие")
    if moderator:
        await chats.resolve_reports(message["id"])
        await chats.log(uid, action, str(message["id"]))
    return web.json_response({"ok": True, "data": await _one(request, message["id"])})


@routes.post("/api/chat/messages/{message_id}/author")
async def chat_author(request: web.Request) -> web.Response:
    """Меры к автору: мьют (модератор), бан и назначение модератором (администратор)."""
    try:
        message, _ = await _message_for(request)
    except ChatDenied as exc:
        return _denied(exc)
    body = await request.json()
    action = str(body.get("action") or "")
    chats, uid = repo(request), current_user(request).telegram_id
    target = await chats.profile(message["author_id"])
    if message["is_admin"] or not target or target["telegram_id"] == uid:
        return json_error("К этому автору это не применить")
    admin = is_admin(request)
    if action == "mute":
        if not await can_moderate(request, message["room"]):
            return json_error("Нужны права модератора", status=403, reason="chat")
        minutes = int(body.get("minutes") or 0)
        if not 0 <= minutes <= 7 * 24 * 60:
            return json_error("Некорректный срок")
        await chats.set_profile_field(target["telegram_id"], "muted_until", time.time() + minutes * 60 if minutes else 0)
        details = f"{minutes} мин"
    elif action in ("ban", "unban"):
        if not admin:
            return json_error("Только для администратора", status=403, reason="chat")
        await chats.set_profile_field(target["telegram_id"], "banned", int(action == "ban"))
        details = ""
    elif action in ("moderator", "unmoderator"):
        if not admin:
            return json_error("Только для администратора", status=403, reason="chat")
        await chats.set_moderator(target["telegram_id"], target["school_key"], action == "moderator")
        details = target["school_key"]
    else:
        return json_error("Неизвестное действие")
    await chats.log(uid, action, str(target["telegram_id"]), details)
    return web.json_response({"ok": True, "data": {
        "muted_until": (await chats.profile(target["telegram_id"]) or {}).get("muted_until", 0),
        "moderator": target["school_key"] in await chats.moderated_schools(target["telegram_id"]),
    }})


@routes.get("/api/chat/reports")
async def chat_reports(request: web.Request) -> web.Response:
    """Очередь жалоб: администратору — все, модератору — своей школы."""
    chats, uid = repo(request), current_user(request).telegram_id
    admin = is_admin(request)
    schools = None if admin else await chats.moderated_schools(uid)
    if not admin and not schools:
        return json_error("Нужны права модератора", status=403, reason="chat")
    rows = await chats.reported(schools)
    items = await message_json(request, rows, moderator=True)
    for item, row in zip(items, rows):
        room = rules.describe(row["room"])
        item["room"] = {"id": row["room"], "title": room.title if room else row["room"]}
        item["reports"] = row["reports"]
    return web.json_response({"ok": True, "data": {"items": items}})


# ─────────────────────── Опросы, уведомления, заглушение ───────────────────────

_background: set[asyncio.Task] = set()


async def notify_mentions(app: Any, room: rules.Room, message_id: int) -> None:
    """Уведомить тех, кого упомянули (@Имя) или кому ответили. Обо всех
    сообщениях подряд не уведомляем — иначе push быстро выключат совсем."""
    notifier = app.watchers.notifier
    if notifier is None:
        return
    try:
        chats = ChatRepository(app.db)
        message = await chats.message(message_id)
        if not message:
            return
        targets: set[int] = set()
        if message["reply_to"] and (replied := await chats.message(message["reply_to"])):
            targets.add(replied["author_id"])
        names = {_fold(name) for name in MENTION.findall(message["text"])}
        if names:
            for member in await chats.members(room.id):
                if _fold(member["first_name"]) in names:
                    targets.add(member["telegram_id"])
            admin_id = app.settings.telegram.admin_id
            if admin_id and _fold(rules.ADMIN_NAME) in names:
                targets.add(admin_id)
        targets.discard(message["author_id"])
        author = rules.ADMIN_NAME if message["is_admin"] else (
            f"{message['author_name']} · {message['author_grade']}" if message["author_grade"] else message["author_name"])
        preview = message["text"][:120]
        for telegram_id in targets:
            if await chats.is_muted(telegram_id, room.id):
                continue
            user = await app.users.get(telegram_id)
            if not user:
                continue
            await notifier.send_text(
                user, f"💬 <b>{_esc(author)}</b> в «{_esc(room.title)}»:\n{_esc(preview)}",
                push=(f"{author} · {room.title}", preview), kind="chat")
    except Exception as exc:  # noqa: BLE001 — уведомление не должно ронять чат
        logger.warning("Уведомление о сообщении %s не ушло: %s", message_id, exc)


def _fold(name: str) -> str:
    return name.strip().lower().replace("ё", "е")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@routes.post("/api/chat/messages/{message_id}/vote")
async def chat_vote(request: web.Request) -> web.Response:
    """Голос в опросе; повторный выбор того же варианта снимает голос."""
    try:
        message, _ = await _message_for(request)
    except ChatDenied as exc:
        return _denied(exc)
    if message["kind"] != "poll" or message["deleted_at"] is not None:
        return json_error("Это не опрос")
    option = (await request.json()).get("option")
    options = json.loads(message["payload"] or "{}").get("options", [])
    if not isinstance(option, int) or not 0 <= option < len(options):
        return json_error("Нет такого варианта")
    uid = current_user(request).telegram_id
    current = dict((who, choice) for who, choice in (await repo(request).votes([message["id"]])).get(message["id"], []))
    await repo(request).vote(message["id"], uid, None if current.get(uid) == option else option)
    return web.json_response({"ok": True, "data": await _one(request, message["id"])})


@routes.post("/api/chat/rooms/{room}/mute")
async def chat_mute(request: web.Request) -> web.Response:
    room_id = request.match_info["room"]
    try:
        await room_access(request, room_id)
    except ChatDenied as exc:
        return _denied(exc)
    muted = bool((await request.json()).get("muted"))
    await repo(request).set_muted(current_user(request).telegram_id, room_id, muted)
    return web.json_response({"ok": True, "data": {"muted": muted}})
