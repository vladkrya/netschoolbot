"""Один аккаунт «Сетевого города» — один аккаунт приложения.

Без этого правила второй аккаунт приложения мог войти в чужой Сетевой город
и тихо читать оценки и почту. Аккаунт СГ узнаём по серверу школы и id
пользователя в СГ: id один и тот же при входе и по паролю, и через Госуслуги.

Проверка идёт сразу после входа, пока жив клиент входа, — до того как сессия
сохранится. Занятый СГ подключить нельзя, кроме двух случаев: вход в свой же
СГ заново и СГ админа, если он разрешил это тумблером в настройках.
Кто подключил СГ до этого правила, продолжает работать: их записи заполняет
`backfill` при запуске, и повторный вход для них — «свой же СГ».
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .errors import NetSchoolError, Reason

if TYPE_CHECKING:
    from ..context import AppContext

logger = logging.getLogger("netschoolbot.netschool")

# app_flags: админ разрешил другим аккаунтам подключать свой Сетевой город.
SHARE_FLAG = "netschool_share_admin"
TAKEN_MESSAGE = ("Этот аккаунт «Сетевого города» уже подключён к другому аккаунту приложения. "
                 "Войдите в тот аккаунт или в свой аккаунт «Сетевого города».")


class AccountTaken(NetSchoolError):
    """Аккаунт СГ уже у другого аккаунта приложения."""

    def __init__(self) -> None:
        super().__init__(Reason.UNKNOWN, TAKEN_MESSAGE)


def school_key(url: str) -> str:
    return url.strip().rstrip("/").lower()


def _as_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def account_id(client: Any) -> int | None:
    """id пользователя в СГ у живого клиента (веб или мобильного); None — не узнали."""
    try:
        if hasattr(client, "users"):
            return _as_id(((await client.users()) or [{}])[0].get("id"))
        context = (await client._authed_get("context")).json()
        return _as_id(context.get("userId") or (context.get("user") or {}).get("id"))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — не узнали id — вход не ломаем
        logger.warning("Не удалось узнать id пользователя «Сетевого города»: %s", exc)
        return None


async def sharing_allowed(app: AppContext) -> bool:
    from ..db.repositories import AppFlagRepository
    return await AppFlagRepository(app.db).get(SHARE_FLAG)


async def set_sharing(app: AppContext, on: bool) -> None:
    from ..db.repositories import AppFlagRepository
    await AppFlagRepository(app.db).set(SHARE_FLAG, on)


async def claim(app: AppContext, telegram_id: int, school_url: str, ns_user_id: int | None) -> None:
    """Закрепить СГ за аккаунтом приложения или AccountTaken, если он чужой."""
    if ns_user_id is None or not school_url:
        return
    url = school_key(school_url)
    holders = await app.netschool_accounts.holders(url, ns_user_id)
    others = [holder for holder in holders if holder != telegram_id]
    admin = app.settings.telegram.admin_id
    allowed = (
        not others
        or telegram_id in holders  # свой же СГ, в том числе дубли до этого правила
        or (admin and telegram_id == admin)
        or (admin and admin in others and await sharing_allowed(app))
    )
    if not allowed:
        logger.warning("Отказ: аккаунт %s пробовал подключить Сетевой город аккаунта %s (%s, id %s)",
                       telegram_id, others[0], url, ns_user_id)
        raise AccountTaken()
    await app.netschool_accounts.set(telegram_id, url, ns_user_id)


async def backfill(app: AppContext, *, pause: float = 1.0) -> int:
    """Записать СГ тех, кто вошёл до этого правила. Возвращает, сколько записано.

    Только у кого есть сохранённая сессия: нового входа ради этого не делаем.
    Дубли при этом сохраняются как есть — их никто не выкидывает.
    """
    written = 0
    for user in await app.users.all_with_school():
        if await app.netschool_accounts.get(user.telegram_id) is not None:
            continue
        if not await app.sessions.load(user.telegram_id):
            continue
        try:
            ns_user_id = await app.diary.fetch_account_id(user)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — дозапишем при следующем запуске или входе
            logger.info("Не узнали Сетевой город %s: %s", user.telegram_id, exc)
            ns_user_id = None
        if ns_user_id is not None:
            await app.netschool_accounts.set(user.telegram_id, school_key(user.school.url), ns_user_id)
            written += 1
        await asyncio.sleep(pause)
    if written:
        logger.info("Записаны аккаунты «Сетевого города» у %s пользователей", written)
    return written
