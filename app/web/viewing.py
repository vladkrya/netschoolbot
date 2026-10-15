"""Просмотр дневника родными: только чтение, по приглашению ученика.

Приглашённый входит своим аккаунтом (Telegram или почта). Если ученик дал
ему просмотр, запросы к дневнику идут от имени ученика, но только чтение:
всё, что меняет данные или пишет от лица ученика (отметки ДЗ, свои задачи,
письма, чат, настройки уведомлений), отклоняется. Свои вещи — вход, PIN,
оформление, push, родные — остаются у самого приглашённого.

Если у приглашённого есть свой дневник, он переключается между ним и
дневником ученика; без своего — сразу видит дневник ученика. Когда у обоих
один и тот же ученик, переключаться незачем — такой просмотр не предлагается.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiohttp import web

from ..domain.models import User

if TYPE_CHECKING:
    from ..context import AppContext

# Кто на самом деле делает запрос, когда USER — ученик, чей дневник смотрят.
VIEWER = "netschool_viewer"

# Чтение дневника ученика — только GET.
OWNER_READS = frozenset((
    "/api/diary", "/api/homework", "/api/homework/done", "/api/marks", "/api/marks/period",
    "/api/marks/subject", "/api/subject", "/api/history", "/api/notes", "/api/student-class",
    "/api/announcements", "/api/profile", "/api/export.xlsx", "/api/bells",
))
# /api/homework/{id}/comment и …/answer (ответ виден, но не меняется — только
# GET), вложения и выгрузка Excel. Вход на дистанционный урок, справки и
# олимпиады — от лица ученика, их при просмотре нет.
OWNER_READ_PREFIXES = ("/api/homework/", "/api/attachment/", "/api/export/")

# Это всегда про самого приглашённого.
VIEWER_PREFIXES = (
    "/api/account", "/api/lock/", "/api/prefs", "/api/push/", "/api/family", "/api/bug-report",
    "/api/sgo/", "/api/attachment-ticket", "/api/visit", "/api/pwa-link", "/api/view",
    "/api/settings/timezone", "/api/notices/",
)

READ_ONLY_MESSAGE = "Это просмотр дневника ученика — менять в нём ничего нельзя"


def _api_path(path: str) -> str:
    return path[path.index("/api/"):].rstrip("/") if "/api/" in path else path


def is_owner_read(method: str, path: str) -> bool:
    if method not in ("GET", "HEAD"):
        return False
    path = _api_path(path)
    return path in OWNER_READS or path.startswith(OWNER_READ_PREFIXES)


def is_viewer_own(path: str) -> bool:
    return _api_path(path).startswith(VIEWER_PREFIXES)


def same_student(viewer: User, owner: User) -> bool:
    """У приглашённого в своём дневнике тот же ученик — смотреть нечего."""
    if not (viewer.school.configured and owner.selected_student_id is not None):
        return False
    if viewer.school.url.rstrip("/") != owner.school.url.rstrip("/"):
        return False
    return any(student.id == owner.selected_student_id for student in viewer.available_students)


async def choices(app: AppContext, viewer: User) -> list[User]:
    """Чьи дневники приглашённый может смотреть."""
    owners = []
    for item in await app.family.access(viewer.telegram_id):
        if not item["view"]:
            continue
        owner = await app.users.get(item["owner_id"])
        if owner is not None and owner.school.configured and not same_student(viewer, owner):
            owners.append(owner)
    return owners


async def target(app: AppContext, viewer: User) -> User | None:
    """Чей дневник сейчас открыт у приглашённого (None — свой)."""
    owners = await choices(app, viewer)
    if not owners:
        return None
    chosen = await app.family.viewing(viewer.telegram_id)
    for owner in owners:
        if owner.telegram_id == chosen:
            return owner
    # Своего дневника нет — сразу дневник ученика.
    return None if viewer.school.configured else owners[0]


async def apply(request: web.Request, app: AppContext, user_key: str) -> web.Response | None:
    """Подменить пользователя запроса на ученика, если идёт просмотр.

    Возвращает ответ-отказ для запросов, которые при просмотре нельзя.
    """
    viewer: User = request[user_key]
    owner = await target(app, viewer)
    if owner is None:
        return None
    request[VIEWER] = viewer
    if is_owner_read(request.method, request.path):
        request[user_key] = owner
        return None
    if is_viewer_own(request.path):
        return None
    return web.json_response({"ok": False, "error": READ_ONLY_MESSAGE, "reason": "view"}, status=403)


async def summary(app: AppContext, viewer: User, current: User | None) -> list[dict[str, Any]]:
    """Переключатель «Мой дневник / дневник ученика» для профиля."""
    owners = await choices(app, viewer)
    if not owners:
        return []
    items = []
    if viewer.school.configured:
        items.append({"id": None, "name": "Мой дневник", "current": current is None})
    items += [{"id": owner.telegram_id, "name": owner.label,
               "school": owner.school.name,
               "current": current is not None and current.telegram_id == owner.telegram_id}
              for owner in owners]
    return items
