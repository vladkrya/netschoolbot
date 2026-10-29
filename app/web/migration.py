"""Срок отключения прежнего адреса для тех, кто уже поставил приложение с основного домена.

Срок у каждого свой: moved_days дней с момента, когда с основного домена
впервые открыли установленное приложение (иконку на экране «Домой»). Хранится
в app_flags под ключом moved:<telegram_id> как время в секундах.
"""
from __future__ import annotations

import time

from aiohttp import web


def _key(telegram_id: int) -> str:
    return f"moved:{telegram_id}"


async def note_installed(request: web.Request, telegram_id: int, db, public_url: str, days: int) -> None:
    """Приложение запущено с иконки на основном домене: назначаем срок, если его ещё нет."""
    if request.headers.get("X-Netschool-Client") != "pwa":
        return
    if request.host.split(":")[0].lower() != public_url.split("//")[-1].lower():
        return
    deadline = time.time() + days * 86400
    await db.execute(
        "INSERT INTO app_flags (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
        (_key(telegram_id), f"{deadline:.0f}"),
    )


async def deadline(db, telegram_id: int) -> float | None:
    row = await db.fetch_one("SELECT value FROM app_flags WHERE key = ?", (_key(telegram_id),))
    try:
        return float(row["value"]) if row else None
    except ValueError:
        return None
