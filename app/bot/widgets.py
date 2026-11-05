"""Экспериментальные карточки Telegram Widget (Bot API 10.4 beta)."""

from __future__ import annotations

import html
import logging
import zlib
from html.parser import HTMLParser

from aiogram import Bot
from aiogram.types import Message

from ..domain import formatting

logger = logging.getLogger("netschoolbot.bot")


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")


def _plain(value: str) -> str:
    parser = _PlainText()
    parser.feed(value)
    return "".join(parser.parts)


def card_html(text: str) -> str:
    """Оформить существующий ответ как компактную, автономную карточку."""
    lines = _plain(text).splitlines()
    blocks = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        kind = "title" if index == 0 else "section" if line.startswith(("📅", "🗓", "📆")) else "row"
        blocks.append(f'<div class="{kind}">{html.escape(line)}</div>')
    return ("<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<style>html{color-scheme:light dark}body{margin:0;padding:14px;font:14px/1.45 system-ui;"
            "color:var(--tg-theme-text-color,#20242a);background:var(--tg-theme-bg-color,#fff)}"
            ".card{overflow-wrap:anywhere}.title{font-size:17px;font-weight:700;margin-bottom:10px}"
            ".section{font-weight:700;margin:12px 0 4px;color:var(--tg-theme-link-color,#3981dc)}"
            ".row{padding:5px 0;border-bottom:1px solid var(--tg-theme-hint-color,#9993)}"
            ".row:last-child{border:0}"
            "</style></head><body><div class=card>" + "".join(blocks) + "</div></body></html>")


async def send_card(message: Message, bot: Bot, text: str, *, reply_markup=None) -> bool:
    """Отправить виджет через beta API; False позволяет обычный текстовый ответ."""
    source = card_html(text)
    encoded = source.encode("utf-8")
    if len(encoded) > 32768 or len(zlib.compress(encoded, 9)) > 4096:
        return False
    # В beta API методы доступны на отдельном URL. Сессию и прокси берём у aiogram.
    session = await bot.session.create_session()
    url = f"https://api.telegram.org/beta/bot{bot.token}/test/sendWidget"
    caption = _plain(text)
    caption = caption.encode("utf-8")[:850].decode("utf-8", "ignore")
    payload = {
        "chat_id": str(message.chat.id),
        "html": source,
        "caption": caption + "\n\nЕсли карточка не открылась: /beta off",
        "width": "0",
        "height": "0",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup.model_dump_json(exclude_none=True)
    try:
        async with session.post(url, data=payload, timeout=12) as response:
            result = await response.json(content_type=None)
        if result.get("ok"):
            return True
        logger.warning("Beta Widget API rejected sendWidget: %s", str(result.get("description", ""))[:160])
    except Exception as exc:  # noqa: BLE001 — бета недоступна, ответим обычным сообщением
        logger.warning("Beta Widget API unavailable: %s", type(exc).__name__)
    return False


async def beta_reply(message: Message, user_id: int, app, bot: Bot, text: str, *, reply_markup=None) -> bool:
    if not await app.users.beta_widgets(user_id):
        return False
    parts = formatting.split_message(text)
    if not all(len(zlib.compress(card_html(part).encode(), 9)) <= 4096 for part in parts):
        return False
    for index, part in enumerate(parts):
        if not await send_card(message, bot, part, reply_markup=reply_markup if index == len(parts) - 1 else None):
            # Не повторяем уже отправленные части при частичном сбое.
            if index:
                return True
            return False
    return True
