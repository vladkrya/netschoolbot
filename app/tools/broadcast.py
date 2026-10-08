"""Рассылка всем пользователям бота: python -m app.tools.broadcast [--admin] [--dry-run] < текст.

--dry-run — только посчитать получателей; --admin — отправить одному админу
для проверки текста. Текст — HTML-разметка Telegram, читается из stdin.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from ..settings import load_settings

# Telegram пускает около 30 сообщений в секунду; держимся с запасом.
PAUSE_S = 0.1


def recipients(db_path) -> list[int]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        return [row[0] for row in db.execute("SELECT telegram_id FROM users ORDER BY telegram_id")]


async def send_all(text: str, ids: list[int]) -> None:
    settings = load_settings()
    proxy = settings.telegram.api_proxy
    bot = Bot(settings.telegram.bot_token, session=AiohttpSession(proxy=proxy) if proxy else None)
    sent = blocked = failed = 0
    try:
        for chat_id in ids:
            for _ in range(3):
                try:
                    await bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
                    sent += 1
                except TelegramRetryAfter as error:
                    await asyncio.sleep(error.retry_after + 1)
                    continue
                except TelegramForbiddenError:
                    blocked += 1
                except Exception as error:  # noqa: BLE001 — один сбой не срывает рассылку
                    failed += 1
                    print(f"{chat_id}: {error}", file=sys.stderr)
                break
            await asyncio.sleep(PAUSE_S)
    finally:
        await bot.session.close()
    print(f"Отправлено {sent}, заблокировали бота {blocked}, ошибок {failed} из {len(ids)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin", action="store_true", help="отправить только админу")
    parser.add_argument("--dry-run", action="store_true", help="только посчитать получателей")
    args = parser.parse_args()
    text = sys.stdin.read().strip()
    if not text:
        parser.error("пустой текст")
    settings = load_settings()
    ids = [settings.telegram.admin_id] if args.admin else recipients(settings.db_path)
    if args.dry_run:
        print(f"Получателей: {len(ids)}\n\n{text}")
        return 0
    asyncio.run(send_all(text, ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
