"""Школьные чаты: параллель, школа и общий чат всех школ.

Пускаем только учеников — это проверяется входом в «Сетевой город», а не
словами человека. Другим видны только ник и номер класса, и только после
согласия. Администратор (TG_ADMIN_ID) видит и модерирует все чаты независимо от
выбранной подписи; писать может как admin или от своего профиля.

Сообщения доставляются так: по WebSocket приходит сигнал «в комнате новое»,
и клиент сразу забирает новые (`after`) и изменённые (`since`) сообщения
обычным запросом. Если сокет не поднялся (прокси, плохая связь), открытый
чат сам спрашивает раз в несколько секунд — так работало и раньше.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any

from aiohttp import WSMsgType, web

from ..chat import rules
from ..chat.hub import ChatHub, Listener
from ..chat.repository import ChatRepository
from ..netschool.errors import NetSchoolError
from .api import _ticket_key, context, current_user, json_error

routes = web.RouteTableDef()
logger = logging.getLogger("netschoolbot.chat")
HUB = web.AppKey("chat_hub", ChatHub)

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
# Уведомление «обо всех сообщениях» из одной комнаты — не чаще раза в столько
# секунд: в живой переписке иначе телефон звенит без остановки.
NOTIFY_EVERY_S = 120
WS_TICKET_TTL = 60

RULES_TEXT = [
    "В чатах только ученики: вход проверяется через «Сетевой город».",
    "Другим видны только твой ник и номер класса — имени и фамилии никто не увидит.",
    "Администрация видит все чаты и может удалять сообщения и блокировать нарушителей.",
    "Нельзя: травля, угрозы, наркотики, призывы к самоповреждению, чужие фото и личные данные.",
    "В общем чате нельзя оставлять ссылки, телефоны и ники из соцсетей.",
    "Сменить выбранный ник может только администратор — напишите в ЛС с админом.",
    "Сообщения хранятся на сервере, даже если их удалили, — для разбора жалоб.",
]


def repo(request: web.Request) -> ChatRepository:
    return ChatRepository(context(request).db)


def hub(request: web.Request) -> ChatHub:
    return request.config_dict[HUB]


def admin_id(request: web.Request) -> int:
    return context(request).settings.telegram.admin_id or 0


def is_admin(request: web.Request) -> bool:
    """Это администратор — неважно, от чьего имени он сейчас пишет."""
    return bool(admin_id(request)) and current_user(request).telegram_id == admin_id(request)


async def admin_mode(request: web.Request) -> bool:
    """Режим администратора: доступ ко всем комнатам и действиям управления."""
    if not is_admin(request):
        return False
    profile = await current_profile(request)
    return not profile or bool(profile["as_admin"])


def participant_label(profile: dict[str, Any]) -> str:
    return profile["nick"] or f"Без ника · {profile['grade']} класс · #{profile['telegram_id']}"


async def describe(request: web.Request, room_id: str) -> rules.Room | None:
    """Чужая комната с названием школы — из профилей её учеников."""
    if room_id.startswith("support:"):
        owner = rules.support_owner(room_id)
        if owner is None or owner == admin_id(request) or not (profile := await repo(request).profile(owner)):
            return None
        own = owner == current_user(request).telegram_id
        return rules.Room(room_id, "support", "ЛС с админом" if own else f"ЛС · {participant_label(profile)}",
                          "Смена ника и помощь" if own else "Личный чат с участником")
    school = rules.room_school(room_id)
    names = request.setdefault("chat_school_names", {})
    if school and school not in names:
        names[school] = await repo(request).school_name(school)
    return rules.describe(room_id, names.get(school, "") if school else "")


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


_NO_PROFILE = object()


async def current_profile(request: web.Request) -> dict[str, Any] | None:
    """Профиль, перепроверенный, если давно не проверяли.

    Школьный сервер недоступен — живём со старыми данными, но если он ответил,
    что это больше не ученик, профиль удаляется. В пределах запроса профиль
    запоминается; после изменений его сбрасывает forget_profile.
    """
    cached = request.get("chat_profile", _NO_PROFILE)
    if cached is not _NO_PROFILE:
        return cached  # type: ignore[return-value]
    chats, uid = repo(request), current_user(request).telegram_id
    profile = await chats.profile(uid)
    if profile and time.time() - profile["verified_at"] > REVERIFY_AFTER_S:
        try:
            profile = await chats.save_profile(uid, **await verify(request))
        except ChatDenied:
            await chats.delete_profile(uid)
            profile = None
        except NetSchoolError:
            pass
    request["chat_profile"] = profile
    return profile


def forget_profile(request: web.Request) -> None:
    request.pop("chat_profile", None)


def profile_rooms(profile: dict[str, Any] | None) -> list[rules.Room]:
    if not profile:
        return []
    return [
        *rules.rooms_for(profile["school_key"], profile["class_name"], profile["grade"], profile["school_name"]),
        rules.Room(f"support:{profile['telegram_id']}", "support", "ЛС с админом", "Смена ника и помощь"),
    ]


async def visible_rooms(request: web.Request, profile: dict[str, Any] | None) -> list[rules.Room]:
    """Свои комнаты, а у администратора — все комнаты всех школ:
    сначала те, где пишут, потом пустые."""
    own = [room for room in profile_rooms(profile) if room.id != f"support:{admin_id(request)}"]
    if not await admin_mode(request):
        return own
    seen = {room.id for room in own}
    extra: list[rules.Room] = []

    async def add(room_id: str) -> None:
        if room_id not in seen and (room := await describe(request, room_id)):
            extra.append(room)
            seen.add(room_id)

    for first, last in rules.AGE_BANDS:
        await add(f"global:{first}-{last}")
    chats = repo(request)
    for room_id in await chats.active_rooms():
        await add(room_id)
    for school, grade in await chats.known_rooms():
        await add(f"school:{school}")
        await add(f"grade:{school}:{grade}")
    return own + extra


async def room_access(request: web.Request, room_id: str) -> tuple[rules.Room, dict[str, Any] | None]:
    profile = await current_profile(request)
    if room_id == f"support:{admin_id(request)}":
        raise ChatDenied("Личный чат с самим собой недоступен", status=404)
    for room in profile_rooms(profile):
        if room.id == room_id:
            return room, profile
    if await admin_mode(request) and (room := await describe(request, room_id)):
        return room, profile
    # Модератор школы заходит во все её комнаты — разбирать жалобы.
    if (room := await describe(request, room_id)) and await repo(request).is_moderator(
            current_user(request).telegram_id, rules.room_school(room_id)):
        return room, profile
    if not profile and not await admin_mode(request):
        raise ChatDenied("Сначала вступите в чаты")
    raise ChatDenied("Нет доступа к этому чату", status=404)


def display(profile: dict[str, Any] | None, as_admin: bool) -> tuple[str, int | None, bool]:
    """Подпись автора: «admin» или ник с номером класса."""
    if as_admin:
        return rules.ADMIN_NAME, None, True
    assert profile is not None
    return profile["nick"] or "", profile["grade"], False


def _author(row: dict[str, Any], profiles: dict[int, dict[str, Any]]) -> str:
    """Подпись берётся из профиля: сменил ник — сменилась и в старых сообщениях."""
    if row["is_admin"]:
        return rules.ADMIN_NAME
    profile = profiles.get(row["author_id"])
    return (profile or {}).get("nick") or row["author_name"] or "Участник"


def _deleted_by(row: dict[str, Any], admin: int) -> str | None:
    if row["deleted_at"] is None:
        return None
    if row["deleted_by"] == row["author_id"]:
        return "author"
    return "admin" if admin and row["deleted_by"] == admin else "moderator"


async def author_roles(request: web.Request, ids: list[int]) -> dict[int, str]:
    """Значок отражает текущую роль аккаунта, не ник или подпись сообщения."""
    roles = {uid: "moderator" for uid in await repo(request).moderator_ids(ids)}
    if admin_id(request) in ids and admin_id(request):
        roles[admin_id(request)] = "admin"
    return roles


async def message_json(
    request: web.Request, rows: list[dict[str, Any]], *, moderator: bool, others_read: int = 0,
) -> list[dict[str, Any]]:
    chats, uid = repo(request), current_user(request).telegram_id
    reactions = await chats.reactions([row["id"] for row in rows])
    votes = await chats.votes([row["id"] for row in rows if row["kind"] == "poll"])
    reply_ids = sorted({row["reply_to"] for row in rows if row["reply_to"]})
    replies = {row["id"]: row for row in await _messages_by_id(chats, reply_ids)}
    profiles = await chats.profiles(sorted({row["author_id"] for row in [*rows, *replies.values()]}))
    roles = await author_roles(request, sorted({row["author_id"] for row in [*rows, *replies.values()]}))
    admin = admin_id(request)
    acting_admin = await admin_mode(request)
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
            "kind": row["kind"],
            "author": _author(row, profiles),
            "author_id": row["author_id"] if acting_admin else None,
            "grade": row["author_grade"],
            "admin": bool(row["is_admin"]),
            "role": roles.get(row["author_id"]),
            "mine": row["author_id"] == uid and bool(row["is_admin"]) == acting_admin,
            "text": None if hidden_for_me else row["text"],
            "deleted": row["deleted_at"] is not None,
            "deleted_by": _deleted_by(row, admin),
            "hidden": bool(row["hidden"]),
            "pinned": bool(row["pinned"]),
            "at": row["created_at"],
            "read": row["author_id"] == uid and bool(row["is_admin"]) == acting_admin and others_read >= row["id"],
            "reply": {
                "id": reply["id"], "author": _author(reply, profiles),
                "role": roles.get(reply["author_id"]),
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
    if await admin_mode(request):
        return True
    school = rules.room_school(room_id)
    return await repo(request).is_moderator(current_user(request).telegram_id, school)


async def others_read(request: web.Request, room_id: str) -> int:
    """Докуда комнату прочитали другие. Прочтения администратора не считаем:
    он заглядывает во все чаты, и ✓✓ от него ничего не значат."""
    excluded = {current_user(request).telegram_id}
    if not room_id.startswith("support:"):
        excluded.add(admin_id(request))
    return await repo(request).others_read(room_id, excluded)


def _notify_mode(reads: dict[str, dict[str, Any]], room: rules.Room) -> str:
    return reads.get(room.id, {}).get("notify") or rules.default_notify(room.kind)


async def unread_total(request: web.Request) -> int:
    """Непрочитанное в своих комнатах — для числа на значке «Почты».
    Комнаты с выключенными уведомлениями не считаем."""
    profile = await current_profile(request)
    rooms = await visible_rooms(request, profile)
    if not rooms:
        return 0
    chats, uid = repo(request), current_user(request).telegram_id
    unread = await chats.unread(uid, [room.id for room in rooms])
    reads = await chats.reads(uid)
    return sum(unread.get(room.id, 0) for room in rooms if _notify_mode(reads, room) != "off")


def _profile_json(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "nick": profile["nick"], "class_name": profile["class_name"], "grade": profile["grade"],
        "banned": bool(profile["banned"]), "muted_until": profile["muted_until"],
        "nick_changed_at": profile["nick_changed_at"],
    }


async def overview(request: web.Request) -> dict[str, Any]:
    forget_profile(request)
    chats, uid = repo(request), current_user(request).telegram_id
    profile = await current_profile(request)
    admin = is_admin(request)
    acting_admin = await admin_mode(request)
    rooms = await visible_rooms(request, profile)
    ids = [room.id for room in rooms]
    last = await chats.last_messages(ids)
    unread = await chats.unread(uid, ids)
    reads = await chats.reads(uid)
    schools = None if acting_admin else await chats.moderated_schools(uid)
    reports = len(await chats.reported(schools)) if acting_admin or schools else 0
    authors = await chats.profiles(sorted({row["author_id"] for row in last.values()}))
    role_ids = {uid, admin_id(request), *(row["author_id"] for row in last.values())}
    role_ids.update(rules.support_owner(room.id) for room in rooms if room.kind == "support")
    roles = await author_roles(request, sorted(role_ids - {None, 0}))
    # Новые сообщения поднимают комнату вверх; пустые комнаты идут последними.
    rooms.sort(key=lambda room: last.get(room.id, {}).get("id", 0), reverse=True)
    return {
        "joined": profile is not None,
        "admin": admin,
        "admin_mode": acting_admin,
        "moderator": acting_admin or bool(schools),
        "reports": reports,
        "reactions": list(REACTIONS),
        "as_admin": acting_admin,
        "role": roles.get(uid),
        "rules": RULES_TEXT,
        "nick_hint": rules.NICK_HINT,
        "profile": _profile_json(profile) if profile else None,
        "unread_total": await unread_total(request),
        "rooms": [{
            "id": room.id, "kind": room.kind, "title": room.title, "subtitle": room.subtitle,
            "role": roles.get(rules.support_owner(room.id) if acting_admin else admin_id(request)) if room.kind == "support" else None,
            "unread": unread.get(room.id, 0), "notify": _notify_mode(reads, room),
            "owner_id": rules.support_owner(room.id) if acting_admin else None,
            "owner_nick": (await chats.profile(rules.support_owner(room.id)) or {}).get("nick") if acting_admin and room.kind == "support" else None,
            "own": room.id in {own.id for own in profile_rooms(profile)},
            "last": {
                "author": _author(last[room.id], authors), "kind": last[room.id]["kind"],
                "role": roles.get(last[room.id]["author_id"]),
                "text": last[room.id]["text"][:120], "at": last[room.id]["created_at"],
            } if room.id in last else None,
        } for room in rooms],
    }


def _denied(exc: ChatDenied) -> web.Response:
    return json_error(str(exc), status=exc.status, reason="chat")


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@routes.get("/api/chat")
async def chat_overview(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "data": await overview(request)})


@routes.get("/api/chat/participants")
async def chat_participants(request: web.Request) -> web.Response:
    """Админ выбирает участника для первого личного сообщения."""
    if not await admin_mode(request):
        return json_error("Переключитесь на аккаунт Админ", status=403, reason="chat")
    items = []
    profiles = await repo(request).all_profiles()
    roles = await author_roles(request, [profile["telegram_id"] for profile in profiles])
    for profile in profiles:
        uid = profile["telegram_id"]
        if uid == admin_id(request):
            continue
        room = await describe(request, f"support:{uid}")
        items.append({"id": uid, "nick": participant_label(profile), "grade": profile["grade"], "role": roles.get(uid),
                      "school": profile["school_name"], "room": {
                          "id": room.id, "kind": room.kind, "title": room.title,
                          "owner_id": uid, "owner_nick": profile["nick"], "notify": "all"}})
    return web.json_response({"ok": True, "data": {"items": items}})


@routes.get("/api/chat/unread")
async def chat_unread(request: web.Request) -> web.Response:
    """Лёгкий запрос для значка «Почты»: число и нужен ли сокет."""
    profile = await current_profile(request)
    return web.json_response({"ok": True, "data": {
        "total": await unread_total(request), "live": profile is not None or is_admin(request)}})


async def _check_new_nick(request: web.Request, nick: str, owner_id: int | None = None) -> str:
    if problem := rules.check_nick(nick):
        return problem
    owner = await repo(request).nick_owner(rules.nick_key(nick))
    if owner is not None and owner != (owner_id if owner_id is not None else current_user(request).telegram_id):
        return "Этот ник уже занят"
    return ""


async def announce(request: web.Request, profile: dict[str, Any], text: str) -> None:
    """Служебная строка «… теперь в чате» в параллели и школе. В общий чат
    всех школ не пишем — там такие строки шли бы сплошным потоком."""
    chats = repo(request)
    for room in profile_rooms(profile):
        if room.kind in {"global", "support"}:
            continue
        message_id = await chats.add_message(
            room.id, author_id=profile["telegram_id"], author_name="", author_grade=None,
            is_admin=False, text=text, kind="system")
        await hub(request).publish(room.id, {"t": "msg", "room": room.id, "id": message_id})


@routes.post("/api/chat/join")
async def chat_join(request: web.Request) -> web.Response:
    """Вступить: согласие с правилами, ник и проверка, что это ученик."""
    body = await _body(request)
    if body.get("consent") is not True:
        return json_error("Нужно согласие с правилами")
    if await current_profile(request):
        return json_error("Вы уже в чатах. Для смены ника напишите админу", reason="nick")
    nick = await repo(request).saved_nick(current_user(request).telegram_id) or str(body.get("nick") or "").strip()
    if problem := await _check_new_nick(request, nick):
        return json_error(problem, reason="nick")
    try:
        data = await verify(request)
    except ChatDenied as exc:
        return _denied(exc)
    uid = current_user(request).telegram_id
    chats = repo(request)
    await chats.save_profile(uid, consent=True, **data)
    if not await chats.set_nick(uid, nick, rules.nick_key(nick)):
        return json_error("Этот ник уже занят", reason="nick")
    profile = await chats.profile(uid)
    assert profile is not None
    await hub(request).disconnect(uid)
    await announce(request, profile, f"👋 {nick} теперь в чате")
    return web.json_response({"ok": True, "data": await overview(request)})


@routes.post("/api/chat/nick")
async def chat_nick(request: web.Request) -> web.Response:
    """Поставить или сменить ник. Смену видят все — строкой в чате."""
    profile = await current_profile(request)
    if not profile:
        return json_error("Сначала вступите в чаты", status=403, reason="chat")
    nick = str((await _body(request)).get("nick") or "").strip()
    if nick == profile["nick"]:
        return web.json_response({"ok": True, "data": await overview(request)})
    if profile["nick"]:
        return json_error("Для смены ника напишите в ЛС с админом", status=403, reason="nick")
    if problem := await _check_new_nick(request, nick):
        return json_error(problem, reason="nick")
    if not await repo(request).set_nick(profile["telegram_id"], nick, rules.nick_key(nick)):
        return json_error("Этот ник уже занят", reason="nick")
    if profile["nick"]:
        await announce(request, profile, f"✏️ {profile['nick']} теперь {nick}")
    return web.json_response({"ok": True, "data": await overview(request)})


@routes.post("/api/chat/profiles/{user_id}/nick")
async def chat_admin_nick(request: web.Request) -> web.Response:
    if not await admin_mode(request):
        return json_error("Переключитесь на аккаунт Админ", status=403, reason="chat")
    value = request.match_info["user_id"]
    if not value.isdigit() or not (profile := await repo(request).profile(int(value))):
        return json_error("Участник не найден", status=404)
    uid = profile["telegram_id"]
    nick = str((await _body(request)).get("nick") or "").strip()
    if problem := await _check_new_nick(request, nick, uid):
        return json_error(problem, reason="nick")
    if not await repo(request).set_nick(uid, nick, rules.nick_key(nick)):
        return json_error("Этот ник уже занят", reason="nick")
    await repo(request).log(current_user(request).telegram_id, "nick", str(uid), nick)
    if profile["nick"] != nick:
        await announce(request, profile, f"✏️ Администратор сменил ник {profile['nick'] or 'участника'} на {nick}")
    # Обновить авторов в открытых комнатах, списки комнат и личный профиль.
    for room in profile_rooms(profile):
        await hub(request).publish(room.id, {"t": "upd", "room": room.id})
    await hub(request).disconnect(uid)
    return web.json_response({"ok": True, "data": {"nick": nick}})


@routes.post("/api/chat/leave")
async def chat_leave(request: web.Request) -> web.Response:
    """Выйти из чатов: профиль удаляется, написанное остаётся на сервере."""
    profile = await current_profile(request)
    if profile and profile["nick"]:
        await announce(request, profile, f"🚪 {profile['nick']} больше не в чате")
    await repo(request).delete_profile(current_user(request).telegram_id)
    await hub(request).disconnect(current_user(request).telegram_id)
    return web.json_response({"ok": True, "data": await overview(request)})


@routes.post("/api/chat/identity")
async def chat_identity(request: web.Request) -> web.Response:
    """Администратор: писать как «admin» или от своего профиля."""
    if not is_admin(request):
        return json_error("Только для администратора", status=403)
    body = await _body(request)
    profile = await repo(request).profile(current_user(request).telegram_id)
    if not profile:
        return json_error("Чтобы писать от себя, вступите в чаты")
    await repo(request).set_profile_field(profile["telegram_id"], "as_admin", int(bool(body.get("as_admin"))))
    forget_profile(request)
    await hub(request).disconnect(profile["telegram_id"])
    return web.json_response({"ok": True, "data": await overview(request)})


def _number(request: web.Request, name: str) -> int | None:
    try:
        return int(request.query[name]) if name in request.query else None
    except ValueError:
        return None


@routes.get("/api/chat/rooms/{room}/messages")
async def chat_messages(request: web.Request) -> web.Response:
    room_id = request.match_info["room"]
    try:
        room, _ = await room_access(request, room_id)
    except ChatDenied as exc:
        return _denied(exc)
    after, before, around = _number(request, "after"), _number(request, "before"), _number(request, "around")
    try:
        since = float(request.query.get("since", "0"))
    except ValueError:
        since = 0.0
    chats = repo(request)
    now = time.time()
    if around is not None:
        rows = await chats.around(room_id, around)
    else:
        rows = await chats.messages(room_id, after=after, before=before)
    changed = await chats.changed_since(room_id, since, after) if after is not None and since else []
    moderator = await can_moderate(request, room_id)
    pinned = await chats.pinned(room_id)
    read_upto = await others_read(request, room_id)
    mode = await chats.notify_mode(current_user(request).telegram_id, room_id)
    profile = await current_profile(request)
    return web.json_response({"ok": True, "data": {
        "room": {"id": room.id, "kind": room.kind, "title": room.title, "subtitle": room.subtitle},
        "pinned": (await message_json(request, [pinned], moderator=moderator))[0] if pinned else None,
        "notify": mode or rules.default_notify(room.kind),
        "admin": await admin_mode(request),
        "need_nick": not await admin_mode(request) and bool(profile) and not profile["nick"],  # type: ignore[index]
        "messages": await message_json(request, rows, moderator=moderator, others_read=read_upto),
        "changed": await message_json(request, changed, moderator=moderator, others_read=read_upto),
        "others_read": read_upto,
        "moderator": moderator,
        "now": now,
    }})


async def _known_mentions(chats: ChatRepository, text: str) -> list[dict[str, Any]]:
    """Профили, упомянутые в тексте по нику."""
    return await chats.profiles_by_nick(rules.mentions(text))


@routes.post("/api/chat/rooms/{room}/messages")
async def chat_send(request: web.Request) -> web.Response:
    room_id = request.match_info["room"]
    try:
        room, profile = await room_access(request, room_id)
    except ChatDenied as exc:
        return _denied(exc)
    body = await _body(request)
    text = str(body.get("text") or "").strip()
    as_admin = await admin_mode(request)
    if not as_admin:
        if not profile:
            return json_error("Сначала вступите в чаты", status=403, reason="chat")
        if not profile["nick"]:
            return json_error("Сначала выберите ник", status=403, reason="nick")
        if profile["banned"]:
            return json_error("Вы заблокированы в чатах", status=403, reason="chat")
        if profile["muted_until"] > time.time():
            minutes = int((profile["muted_until"] - time.time()) // 60) + 1
            return json_error(f"Писать можно будет через {minutes} мин", status=403, reason="chat")
        if time.time() - profile["last_sent_at"] < SEND_INTERVAL_S:
            return json_error("Слишком часто — подождите секунду", status=429, reason="chat")
    chats = repo(request)
    if not as_admin:
        # @ник участника — не контакт из соцсетей, фильтр общего чата его пропускает.
        known = {member["nick_key"] for member in await _known_mentions(chats, text)} | {rules.ADMIN_NAME}
        if problem := rules.check_text(text, global_room=room.is_global, known=known):
            return json_error(problem, reason="chat")
    if as_admin and not text.strip():
        return json_error("Пустое сообщение")
    kind, payload = "text", None
    if isinstance(body.get("poll"), dict):
        if room.is_global:
            return json_error("Опросы — в чатах своей школы")
        options = [str(item).strip()[:POLL_OPTION_LEN] for item in body["poll"].get("options") or []]
        options = [item for item in options if item]
        if not POLL_OPTIONS[0] <= len(options) <= POLL_OPTIONS[1]:
            return json_error(f"Вариантов — от {POLL_OPTIONS[0]} до {POLL_OPTIONS[1]}")
        if not as_admin and (problem := rules.check_text(" / ".join(options), global_room=False)):
            return json_error(problem, reason="chat")
        kind, payload = "poll", json.dumps({"options": options}, ensure_ascii=False)
    reply_to = body.get("reply_to")
    if reply_to is not None:
        target = await chats.message(int(reply_to)) if str(reply_to).isdigit() else None
        if not target or target["room"] != room_id or target["kind"] == "system":
            return json_error("Сообщение для ответа не найдено")
        reply_to = target["id"]
    name, grade, signed_admin = display(profile, as_admin)
    uid = current_user(request).telegram_id
    message_id = await chats.add_message(
        room_id, author_id=uid, author_name=name, author_grade=grade,
        is_admin=signed_admin, text=text[:rules.MAX_TEXT], reply_to=reply_to, kind=kind, payload=payload,
    )
    await chats.mark_read(uid, room_id, message_id)
    await hub(request).publish(room_id, {"t": "msg", "room": room_id, "id": message_id})
    # Уведомления — фоном: отправитель не ждёт Telegram и push.
    task = asyncio.create_task(notify_message(context(request), hub(request), room, message_id))
    _background.add(task)
    task.add_done_callback(_background.discard)
    row = await chats.message(message_id)
    assert row is not None
    payload = await message_json(request, [row], moderator=await can_moderate(request, room_id),
                                 others_read=await others_read(request, room_id))
    return web.json_response({"ok": True, "data": payload[0]})


@routes.post("/api/chat/rooms/{room}/read")
async def chat_read(request: web.Request) -> web.Response:
    room_id = request.match_info["room"]
    try:
        await room_access(request, room_id)
    except ChatDenied as exc:
        return _denied(exc)
    last_id = (await _body(request)).get("last_id")
    uid = current_user(request).telegram_id
    if isinstance(last_id, int) and not isinstance(last_id, bool) and last_id > 0:
        message = await repo(request).message(last_id)
        if not message or message["room"] != room_id:
            return json_error("Сообщение не найдено", status=404, reason="chat")
        await repo(request).mark_read(uid, room_id, last_id)
        if uid != admin_id(request):
            await hub(request).publish(room_id, {"t": "read", "room": room_id, "id": last_id}, skip=uid)
        await hub(request).publish_user(uid, {"t": "unread"})
    return web.json_response({"ok": True})


@routes.get("/api/chat/rooms/{room}/members")
async def chat_members(request: web.Request) -> web.Response:
    """Подсказка ников при наборе «@»."""
    room_id = request.match_info["room"]
    try:
        await room_access(request, room_id)
    except ChatDenied as exc:
        return _denied(exc)
    prefix = request.query.get("q", "").strip().lstrip("@")[:20]
    uid = current_user(request).telegram_id
    members = await repo(request).search_members(room_id, prefix)
    roles = await author_roles(request, [member["telegram_id"] for member in members])
    found = [{"nick": member["nick"], "grade": member["grade"], "role": roles.get(member["telegram_id"])}
             for member in members
             if member["telegram_id"] != uid]
    if admin_id(request) and rules.ADMIN_NAME.startswith(prefix.lower()) and not await admin_mode(request):
        found.insert(0, {"nick": rules.ADMIN_NAME, "grade": None, "role": "admin"})
    return web.json_response({"ok": True, "data": {"items": found[:8]}})


@routes.post("/api/chat/rooms/{room}/notify")
async def chat_notify(request: web.Request) -> web.Response:
    """Уведомления комнаты: обо всех сообщениях, только упоминания или никаких."""
    room_id = request.match_info["room"]
    try:
        room, _ = await room_access(request, room_id)
    except ChatDenied as exc:
        return _denied(exc)
    mode = str((await _body(request)).get("mode") or "")
    if mode not in rules.NOTIFY_MODES:
        return json_error("Неизвестный режим")
    uid = current_user(request).telegram_id
    await repo(request).set_notify(uid, room_id, mode)
    await hub(request).publish_user(uid, {"t": "unread"})
    return web.json_response({"ok": True, "data": {"notify": mode}})


@routes.post("/api/chat/rooms/{room}/mute")
async def chat_mute(request: web.Request) -> web.Response:
    """Старый переключатель (приложение до режимов уведомлений)."""
    room_id = request.match_info["room"]
    try:
        await room_access(request, room_id)
    except ChatDenied as exc:
        return _denied(exc)
    muted = bool((await _body(request)).get("muted"))
    await repo(request).set_notify(current_user(request).telegram_id, room_id, "off" if muted else None)
    return web.json_response({"ok": True, "data": {"muted": muted}})


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
    return (await message_json(request, [row], moderator=await can_moderate(request, row["room"]),
                               others_read=await others_read(request, row["room"])))[0]


async def _changed(request: web.Request, message: dict[str, Any]) -> web.Response:
    """Сообщение поменялось: сигнал открытым чатам и свежий вид в ответ."""
    await hub(request).publish(message["room"], {"t": "upd", "room": message["room"]})
    return web.json_response({"ok": True, "data": await _one(request, message["id"])})


@routes.post("/api/chat/messages/{message_id}/react")
async def chat_react(request: web.Request) -> web.Response:
    try:
        message, _ = await _message_for(request)
    except ChatDenied as exc:
        return _denied(exc)
    emoji = str((await _body(request)).get("emoji") or "")
    if emoji not in REACTIONS:
        return json_error("Такой реакции нет")
    if message["deleted_at"] is not None or message["kind"] == "system":
        return json_error("Сообщение удалено")
    await repo(request).toggle_reaction(message["id"], current_user(request).telegram_id, emoji)
    return await _changed(request, message)


@routes.get("/api/chat/messages/{message_id}/reads")
async def chat_reads(request: web.Request) -> web.Response:
    """Кто прочитал: автору — своё сообщение, администратору — любое."""
    try:
        message, room = await _message_for(request)
    except ChatDenied as exc:
        return _denied(exc)
    uid = current_user(request).telegram_id
    if not await admin_mode(request) and (message["author_id"] != uid or message["is_admin"]):
        return json_error("Прочтения видны только автору", status=403, reason="chat")
    chats = repo(request)
    excluded = {message["author_id"]}
    if room.kind != "support":
        excluded.add(admin_id(request))
    ids = await chats.readers(message["room"], message["id"], excluded)
    profiles = await chats.profiles(ids)
    roles = await author_roles(request, ids)
    items = sorted(
        ({"nick": profile["nick"], "grade": profile["grade"], "role": roles.get(profile["telegram_id"])} for profile in profiles.values()
         if profile["nick"] and room.id in {own.id for own in profile_rooms(profile)}),
        key=lambda item: item["nick"].lower())
    if room.kind == "support" and admin_id(request) in ids:
        items.append({"nick": rules.ADMIN_NAME, "grade": None, "role": "admin"})
    return web.json_response({"ok": True, "data": {"items": items, "count": len(items)}})


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
    if message["kind"] == "system":
        return json_error("Это служебное сообщение")
    count = await repo(request).report(message["id"], uid)
    if count >= AUTO_HIDE_REPORTS and not message["hidden"]:
        await repo(request).set_flag(message["id"], "hidden", 1)
        await repo(request).log(0, "auto_hide", str(message["id"]), f"{count} жалоб")
    return await _changed(request, message)


@routes.post("/api/chat/messages/{message_id}/moderate")
async def chat_moderate(request: web.Request) -> web.Response:
    """Скрыть / показать / удалить / закрепить. Своё сообщение автор может удалить сам."""
    try:
        message, _ = await _message_for(request)
    except ChatDenied as exc:
        return _denied(exc)
    action = str((await _body(request)).get("action") or "")
    uid = current_user(request).telegram_id
    moderator = await can_moderate(request, message["room"])
    own_delete = (action == "delete" and message["author_id"] == uid
                  and bool(message["is_admin"]) == await admin_mode(request) and message["kind"] != "system")
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
    return await _changed(request, message)


@routes.post("/api/chat/messages/{message_id}/author")
async def chat_author(request: web.Request) -> web.Response:
    """Меры к автору: мьют (модератор), бан и назначение модератором (администратор)."""
    try:
        message, _ = await _message_for(request)
    except ChatDenied as exc:
        return _denied(exc)
    body = await _body(request)
    action = str(body.get("action") or "")
    chats, uid = repo(request), current_user(request).telegram_id
    target = await chats.profile(message["author_id"])
    if message["is_admin"] or not target or target["telegram_id"] == uid:
        return json_error("К этому автору это не применить")
    acting_admin = await admin_mode(request)
    if action == "mute":
        if not await can_moderate(request, message["room"]):
            return json_error("Нужны права модератора", status=403, reason="chat")
        minutes = int(body.get("minutes") or 0)
        if not 0 <= minutes <= 7 * 24 * 60:
            return json_error("Некорректный срок")
        await chats.set_profile_field(target["telegram_id"], "muted_until", time.time() + minutes * 60 if minutes else 0)
        details = f"{minutes} мин"
    elif action in ("ban", "unban"):
        if not acting_admin:
            return json_error("Только для администратора", status=403, reason="chat")
        await chats.set_profile_field(target["telegram_id"], "banned", int(action == "ban"))
        details = ""
    elif action in ("moderator", "unmoderator"):
        if not acting_admin:
            return json_error("Только для администратора", status=403, reason="chat")
        affected_rooms = await chats.set_moderator(target["telegram_id"], target["school_key"], action == "moderator")
        for room_id in affected_rooms:
            await hub(request).publish(room_id, {"t": "upd", "room": room_id})
        details = target["school_key"]
    else:
        return json_error("Неизвестное действие")
    await hub(request).disconnect(target["telegram_id"])
    await chats.log(uid, action, str(target["telegram_id"]), details)
    return web.json_response({"ok": True, "data": {
        "muted_until": (await chats.profile(target["telegram_id"]) or {}).get("muted_until", 0),
        "moderator": target["school_key"] in await chats.moderated_schools(target["telegram_id"]),
    }})


@routes.get("/api/chat/reports")
async def chat_reports(request: web.Request) -> web.Response:
    """Очередь жалоб: администратору — все, модератору — своей школы."""
    chats, uid = repo(request), current_user(request).telegram_id
    acting_admin = await admin_mode(request)
    schools = None if acting_admin else await chats.moderated_schools(uid)
    if not acting_admin and not schools:
        return json_error("Нужны права модератора", status=403, reason="chat")
    rows = await chats.reported(schools)
    items = await message_json(request, rows, moderator=True)
    for item, row in zip(items, rows):
        room = await describe(request, row["room"])
        item["room"] = {"id": row["room"], "kind": room.kind if room else "",
                        "title": room.title if room else row["room"], "subtitle": room.subtitle if room else ""}
        item["reports"] = row["reports"]
    return web.json_response({"ok": True, "data": {"items": items}})


@routes.post("/api/chat/messages/{message_id}/vote")
async def chat_vote(request: web.Request) -> web.Response:
    """Голос в опросе; повторный выбор того же варианта снимает голос."""
    try:
        message, _ = await _message_for(request)
    except ChatDenied as exc:
        return _denied(exc)
    if message["kind"] != "poll" or message["deleted_at"] is not None:
        return json_error("Это не опрос")
    option = (await _body(request)).get("option")
    options = json.loads(message["payload"] or "{}").get("options", [])
    if not isinstance(option, int) or not 0 <= option < len(options):
        return json_error("Нет такого варианта")
    uid = current_user(request).telegram_id
    current = dict((who, choice) for who, choice in (await repo(request).votes([message["id"]])).get(message["id"], []))
    await repo(request).vote(message["id"], uid, None if current.get(uid) == option else option)
    return await _changed(request, message)


# ─────────────────────────────── Уведомления ───────────────────────────────

_background: set[asyncio.Task] = set()
_last_notified: dict[tuple[int, str], float] = {}


async def notify_message(app: Any, chat_hub: ChatHub, room: rules.Room, message_id: int) -> None:
    """Уведомить о сообщении по режиму каждого участника.

    «Все» — обо всех сообщениях, но не чаще раза в NOTIFY_EVERY_S из одной
    комнаты; «Упоминания» — только если упомянули по нику (@admin — это
    администратор) или ответили на его сообщение; «Выкл» — ничего. Кто сейчас
    смотрит эту комнату, тому уведомление не нужно.
    """
    notifier = app.watchers.notifier
    if notifier is None:
        return
    try:
        chats = ChatRepository(app.db)
        message = await chats.message(message_id)
        if not message or message["kind"] == "system" or message["hidden"] or message["deleted_at"] is not None:
            return
        mentioned: set[int] = set()
        if message["reply_to"] and (replied := await chats.message(message["reply_to"])):
            if not replied["is_admin"]:
                mentioned.add(replied["author_id"])
            elif app.settings.telegram.admin_id:
                mentioned.add(app.settings.telegram.admin_id)
        keys = rules.mentions(message["text"])
        for profile in await chats.profiles_by_nick(keys):
            if room.id in {own.id for own in profile_rooms(profile)}:
                mentioned.add(profile["telegram_id"])
        admin = app.settings.telegram.admin_id
        if admin and rules.ADMIN_NAME in keys:
            mentioned.add(admin)

        default = rules.default_notify(room.kind)
        modes = await chats.notify_modes(room.id)
        everyone: set[int] = set()
        if default == "all":
            everyone = {member["telegram_id"] for member in await chats.members(room.id)}
            if room.kind == "support" and admin:
                everyone.add(admin)
        else:
            everyone = {uid for uid, mode in modes.items() if mode == "all"}
        targets = (mentioned | everyone) - {message["author_id"]}
        if message["is_admin"] and admin:
            targets.discard(admin)

        profiles = await chats.profiles(sorted(targets | {message["author_id"]}))
        author = rules.ADMIN_NAME if message["is_admin"] else (
            (profiles.get(message["author_id"]) or {}).get("nick") or message["author_name"] or "Участник")
        if not message["is_admin"] and message["author_grade"]:
            author = f"{author} · {message['author_grade']}"
        preview = ("📊 " if message["kind"] == "poll" else "") + message["text"][:120]
        now = time.monotonic()
        for telegram_id in sorted(targets):
            mode = modes.get(telegram_id) or default
            is_mention = telegram_id in mentioned
            if mode == "off" or (mode == "mentions" and not is_mention):
                continue
            profile = profiles.get(telegram_id)
            if telegram_id != admin and (not profile or profile["banned"] or
                    room.id not in {own.id for own in profile_rooms(profile)}):
                continue
            if chat_hub.watching(telegram_id, room.id):
                continue
            key = (telegram_id, room.id)
            if not is_mention and now - _last_notified.get(key, -NOTIFY_EVERY_S) < NOTIFY_EVERY_S:
                continue
            user = await app.users.get(telegram_id)
            if not user:
                continue
            _last_notified[key] = now
            head = "🔔 Вас упомянули" if is_mention else "💬"
            await notifier.send_text(
                user, f"{head} <b>{_esc(author)}</b> в «{_esc(room.title)}»:\n{_esc(preview)}",
                push=(f"{author} · {room.title}", preview), kind="chat")
    except Exception as exc:  # noqa: BLE001 — уведомление не должно ронять чат
        logger.warning("Уведомление о сообщении %s не ушло: %s", message_id, exc)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─────────────────────────────── WebSocket ───────────────────────────────
# Браузер не умеет ставить заголовки на WebSocket, поэтому сокет открывается
# по короткому билету: его выдаёт обычный запрос, уже прошедший вход и PIN.


def _ws_signature(app: Any, body: str) -> str:
    return hmac.new(_ticket_key(app), f"chat-ws|{body}".encode(), hashlib.sha256).hexdigest()[:32]


def issue_ws_ticket(app: Any, telegram_id: int, now: float | None = None) -> str:
    body = f"{telegram_id}.{int((now if now is not None else time.time()) + WS_TICKET_TTL)}"
    return f"{body}.{_ws_signature(app, body)}"


def verify_ws_ticket(app: Any, ticket: str, now: float | None = None) -> int | None:
    try:
        telegram_id, expires, signature = ticket.split(".")
        body = f"{int(telegram_id)}.{int(expires)}"
    except ValueError:
        return None
    if not hmac.compare_digest(_ws_signature(app, body), signature):
        return None
    if int(expires) < (now if now is not None else time.time()):
        return None
    return int(telegram_id)


@routes.post("/api/chat/ws-ticket")
async def chat_ws_ticket(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "data": {
        "ticket": issue_ws_ticket(context(request), current_user(request).telegram_id)}})


@routes.get("/api/chat/ws")
async def chat_ws(request: web.Request) -> web.StreamResponse:
    uid = current_user(request).telegram_id
    profile = await current_profile(request)
    acting_admin = await admin_mode(request)
    schools = set() if acting_admin else set(await repo(request).moderated_schools(uid))
    socket = web.WebSocketResponse(heartbeat=25)
    await socket.prepare(request)
    listener = Listener(uid, {room.id for room in profile_rooms(profile)}, acting_admin, schools, socket=socket)
    chat_hub = hub(request)
    chat_hub.add(listener)
    try:
        await socket.send_json({"t": "hello"})
        async for frame in socket:
            if frame.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(frame.data)
            except ValueError:
                continue
            kind = data.get("t") if isinstance(data, dict) else None
            if kind == "open":
                room = str(data.get("room") or "")[:120]
                listener.open_room = room if listener.hears(room) else None
            elif kind == "close":
                listener.open_room = None
            elif kind == "ping":
                await socket.send_json({"t": "pong"})
    finally:
        chat_hub.remove(listener)
    return socket
