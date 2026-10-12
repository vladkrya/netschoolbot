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

import time
from typing import Any

from aiohttp import web

from ..chat import rules
from ..chat.repository import ChatRepository
from ..netschool.errors import NetSchoolError
from .api import context, current_user, json_error

routes = web.RouteTableDef()

# Профиль перепроверяется в «Сетевом городе» не реже этого: перевели в
# другой класс, выпустился, вышел из аккаунта ученика.
REVERIFY_AFTER_S = 3 * 24 * 3600
# Не чаще одного сообщения в столько секунд.
SEND_INTERVAL_S = 1.5

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
            extra.append(rules.describe(room_id))
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
        })
    return result


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
    return {
        "joined": profile is not None,
        "admin": admin,
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
    return web.json_response({"ok": True, "data": {
        "room": {"id": room.id, "kind": room.kind, "title": room.title, "subtitle": room.subtitle},
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
        is_admin=as_admin, text=text[:rules.MAX_TEXT], reply_to=reply_to,
    )
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
