"""Обновить username известных пользователей через getChat, без отправки сообщений."""
from __future__ import annotations

import asyncio
from aiogram.exceptions import TelegramRetryAfter
from ..accounts import has_telegram
from ..bot.app import create_bot
from ..db.engine import Database
from ..db.repositories import UserRepository
from ..settings import load_settings


async def main():
    settings = load_settings()
    db = Database(settings.db_path)
    await db.connect()
    users = UserRepository(db)
    bot = create_bot(settings)
    counts = {"checked": 0, "usernames": 0, "unavailable": 0}
    semaphore = asyncio.Semaphore(3)
    async def refresh(uid):
        async with semaphore:
            try:
                try:
                    chat = await bot.get_chat(uid, request_timeout=10)
                except TelegramRetryAfter as exc:
                    if exc.retry_after > 10:
                        raise
                    await asyncio.sleep(exc.retry_after)
                    chat = await bot.get_chat(uid, request_timeout=10)
                await users.remember_telegram_username(uid, chat.username or "")
                counts["checked"] += 1
                counts["usernames"] += bool(chat.username)
            except Exception:  # noqa: BLE001 — один недоступный чат не мешает остальным
                counts["unavailable"] += 1
    try:
        await asyncio.gather(*(refresh(uid) for uid in await users.all_ids() if has_telegram(uid)))
        print(counts)
    finally:
        await bot.session.close()
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
