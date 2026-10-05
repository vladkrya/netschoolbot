"""Сообщение админу бота: python -m app.tools.notify_admin "текст".

Нужно скрипту выкладки: отчёт об упавшем шаге приходит в Telegram тем же
ботом и через тот же прокси, что и обычные уведомления.
"""

from __future__ import annotations

import asyncio
import sys

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from ..settings import load_settings


async def send(text: str) -> None:
    settings = load_settings()
    if not settings.telegram.admin_id:
        print("TG_ADMIN_ID не задан — сообщать некому", file=sys.stderr)
        return
    proxy = settings.telegram.api_proxy
    bot = Bot(settings.telegram.bot_token, session=AiohttpSession(proxy=proxy) if proxy else None)
    try:
        await bot.send_message(settings.telegram.admin_id, text[:4000])
    finally:
        await bot.session.close()


def main() -> int:
    text = " ".join(sys.argv[1:]) or sys.stdin.read()
    asyncio.run(send(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
