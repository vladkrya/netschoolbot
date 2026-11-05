"""Экспериментальные карточки Telegram Widget (Bot API 10.4 beta)."""

from __future__ import annotations

import html
import logging
import math
import zlib
from collections import defaultdict
from html.parser import HTMLParser

from aiogram import Bot
from aiogram.types import Message

from ..domain import formatting
from ..domain.records import HomeworkRecord

logger = logging.getLogger("netschoolbot.bot")


async def beta_api_status(bot: Bot) -> str | None:
    """Сообщение о недоступности тестового Bot API, если он закрыт для токена."""
    session = await bot.session.create_session()
    url = f"https://api.telegram.org/beta/bot{bot.token}/test/getMe"
    try:
        async with session.get(url, timeout=8) as response:
            result = await response.json(content_type=None)
        if result.get("ok"):
            return None
        if response.status == 401:
            return "Telegram пока не открыл тестовый Bot API для этого бота (401). Виджеты недоступны."
        return f"Тестовый Bot API сейчас недоступен (HTTP {response.status})."
    except Exception:  # noqa: BLE001 — только проверка доступности, без пользовательских данных
        return "Не удалось связаться с тестовым Bot API. Попробуйте позже."


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
    title = html.escape(lines[0]) if lines else "СГОшка"
    blocks = []
    for line in lines[1:]:
        if not line.strip():
            continue
        kind = "section" if line.startswith(("📅", "🗓", "📆")) else "row"
        blocks.append(f'<div class="{kind}">{html.escape(line)}</div>')
    return ("<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<style>:root{color-scheme:light;--bg:#eef1f6;--card:#fff;--text:#1d2430;--muted:#6b7585;--border:#dde2ea;--blue:#4a6fae}"
            "@media(prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#141a22;--card:#1e2633;--text:#e3e8ef;--muted:#8e99aa;--border:#2d3748;--blue:#3f63a3}}"
            "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,sans-serif}"
            ".head{background:linear-gradient(135deg,var(--blue),#36558f);color:#fff;padding:15px 16px;border-radius:0 0 16px 16px}"
            ".brand{font-size:11px;letter-spacing:.1em;opacity:.8}.title{font-size:18px;font-weight:700;margin-top:4px}"
            ".card{margin:10px;padding:4px 12px;background:var(--card);border-radius:12px;overflow-wrap:anywhere}"
            ".section{font-weight:700;padding:11px 0 5px;color:var(--blue)}"
            ".row{padding:8px 0;border-bottom:1px solid var(--border)}.row:last-child{border:0}"
            "</style></head><body><div class=head><div class=brand>СГОшка · дневник</div><div class=title>"
            + title + "</div></div><div class=card>" + "".join(blocks) + "</div></body></html>")


def homework_card_html(items: list[HomeworkRecord], done: set[str]) -> str:
    """Карточка /dz: несколько дней на странице, пока они помещаются."""
    grouped: dict = defaultdict(list)
    for item in sorted(items, key=lambda h: (h.due_date, h.lesson_number or 0, h.subject)):
        grouped[item.due_date].append(item)
    open_count = sum(not item.nothing_to_do and item.key not in done for item in items)
    # У виджета нет внутренней прокрутки, а высота пузыря ограничена 600 px.
    # Оставляем место для шапки, переключателя и подписи; несколько коротких
    # дней могут попасть на одну страницу. Большой день делится по заданиям.
    pages: list[list[tuple[object, list[HomeworkRecord], bool]]] = [[]]
    used = 0
    for day, records in grouped.items():
        chunk: list[HomeworkRecord] = []
        shown = 0
        for item in records:
            preview_lines = max(1, math.ceil(min(len(item.text or ""), 95) / 38))
            cost = 51 + preview_lines * 17 + (12 if len(item.subject) > 25 else 0)
            if used + cost + (0 if chunk else 34) > 395 and (chunk or pages[-1]):
                if chunk:
                    pages[-1].append((day, chunk, shown > 0))
                    shown += len(chunk)
                    chunk = []
                pages.append([])
                used = 0
            if not chunk:
                used += 34
            chunk.append(item)
            used += cost
        if chunk:
            pages[-1].append((day, chunk, shown > 0))
    blocks: list[str] = []
    for page_index, chunks in enumerate(pages):
        blocks.append(f'<div class="page{" active" if page_index == 0 else ""}">')
        for day, records, continued in chunks:
            suffix = " · продолжение" if continued else ""
            blocks.append(f'<section class="day"><div class="day-title"><span>📅 {html.escape(formatting.date_label(day))}{suffix}</span><span class="day-count">{len(records)}</span></div>')
            for item in records:
                complete = item.nothing_to_do or item.key in done
                subject = html.escape(item.subject)
                task_text = item.text or "Задание не указано"
                task = html.escape(task_text)
                needs_full = len(task_text) > 120
                short = html.escape(task_text[:72]) + ("…" if needs_full else html.escape(task_text[72:]))
                status = "Готово" if complete else "Задано"
                extras = []
                if item.topic:
                    extras.append(f"<b>Тема:</b> {html.escape(item.topic)}")
                if item.comment:
                    extras.append(f"<b>Комментарий:</b> {html.escape(item.comment)}")
                if item.attachments:
                    extras.append("<b>Файлы:</b> " + ", ".join(html.escape(file.name) for file in item.attachments))
                details = ([f"<b>Полное задание:</b><br>{task}"] if needs_full else []) + extras
                main = (f'<span class="task-top"><span class="subject">{subject}</span><span class="pill">{status}</span></span>'
                        f'<span class="summary">{short}</span>')
                if details:
                    blocks.append(
                        f'<details class="task{" done" if complete else ""}{" has-full" if needs_full else ""}"><summary>{main}'
                        '<span class="more">Подробнее ↓</span><span class="less">Свернуть ↑</span></summary>'
                        f'<div class="detail">{"<br>".join(details)}</div></details>'
                    )
                else:
                    blocks.append(f'<div class="task{" done" if complete else ""}">{main}</div>')
            blocks.append('</section>')
        blocks.append('</div>')
    if not items:
        blocks = ['<div class="page active"><div class="empty">На ближайшие учебные дни заданий нет ✨</div></div>']
    css = (
        ':root{color-scheme:light;--bg:#eef1f6;--card:#fff;--text:#1d2430;--muted:#6b7585;--border:#dde2ea;--blue:#4a6fae;--light:#f4f6fa;--green:#2f9e4f}'
        '@media(prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#141a22;--card:#1e2633;--text:#e3e8ef;--muted:#8e99aa;--border:#2d3748;--blue:#3f63a3;--light:#252f3e;--green:#4cc76a}}'
        '*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 system-ui,-apple-system,sans-serif}'
        '.head{background:linear-gradient(135deg,var(--blue),#36558f);color:white;padding:16px 16px 18px;border-radius:0 0 16px 16px}'
        '.head-top{font-size:11px;letter-spacing:.1em;text-transform:uppercase;opacity:.8}.head-title{font-size:19px;font-weight:700;margin:4px 0 2px}'
        '.head-sub{font-size:12px;opacity:.85}.content{padding:10px}.page{display:none}.page.active{display:block}.day{margin:0 0 13px}.day-title{display:flex;justify-content:space-between;align-items:center;font-weight:700;margin:4px 3px 8px}'
        '.day-count{background:var(--light);color:var(--muted);padding:2px 8px;border-radius:9px;font-size:11px}'
        '.task{display:block;text-align:left;width:100%;font:inherit;color:var(--text);background:var(--card);border:1px solid var(--border);border-left:3px solid var(--blue);border-radius:12px;padding:11px 12px;margin:0 0 7px}'
        '.task summary{list-style:none;cursor:pointer}.task summary::-webkit-details-marker{display:none}'
        '.task.done{border-left-color:var(--green)}.task-top{display:flex;align-items:center;justify-content:space-between;gap:8px}.subject{font-weight:700;font-size:14px}'
        '.pill{flex:none;border-radius:8px;padding:3px 7px;background:var(--light);color:var(--blue);font-size:10px;font-weight:700}.done .pill{color:var(--green)}'
        '.summary,.detail{display:block;margin-top:5px;overflow-wrap:anywhere}.detail{padding-top:8px;border-top:1px solid var(--border)}.done .summary{color:var(--muted)}.more,.less{display:block;color:var(--blue);font-size:11px;margin-top:6px}.less{display:none}.task.has-full[open] .summary,.task[open] .more{display:none}.task[open] .less{display:block}'
        '.pager{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:5px 0 8px}.pager button{border:0;border-radius:9px;background:var(--blue);color:white;padding:8px 11px;font:600 12px system-ui}.pager button:disabled{background:var(--light);color:var(--muted)}.pager span{color:var(--muted);font-size:12px}'
        '.empty{padding:18px;text-align:center;background:var(--card);border-radius:12px}.foot{padding:0 13px 12px;color:var(--muted);font-size:11px}'
    )
    pager = ('<nav class="pager"><button id="prev-day" type="button">← Назад</button>'
             f'<span id="day-position">1 / {len(pages)}</span>'
             '<button id="next-day" type="button">Дальше →</button></nav>') if len(pages) > 1 else ''
    script = ("<script>var days=document.querySelectorAll('.page'),page=0;"
              "var prev=document.getElementById('prev-day'),next=document.getElementById('next-day'),pos=document.getElementById('day-position');"
              "function showDay(){for(var i=0;i<days.length;i++)days[i].className=i===page?'page active':'page';"
              "if(prev)prev.disabled=page===0;if(next)next.disabled=page===days.length-1;"
              "if(pos)pos.textContent=(page+1)+' / '+days.length;}"
              "if(prev)prev.addEventListener('click',function(){if(page>0){page--;showDay()}});"
              "if(next)next.addEventListener('click',function(){if(page<days.length-1){page++;showDay()}});showDay();</script>")
    return ("<!doctype html><html lang=ru><meta name=viewport content='width=device-width,initial-scale=1'><style>"
            + css + "</style><body><div class=head><div class=head-top>СГОшка · дневник</div>"
            f'<div class=head-title>📚 Домашние задания</div><div class=head-sub>На {len(grouped)} учебных дня · осталось {open_count}</div></div>'
            + '<div class=content>' + ''.join(blocks) + pager + '</div><div class=foot>Отметить выполненное можно кнопками под карточкой</div>'
            + script + '</body></html>')


async def send_card(message: Message, bot: Bot, text: str, *, reply_markup=None, source_html: str | None = None) -> bool:
    """Отправить виджет через beta API; False позволяет обычный текстовый ответ."""
    source = source_html or card_html(text)
    encoded = source.encode("utf-8")
    if len(encoded) > 32768 or len(zlib.compress(encoded, 9)) > 4096:
        return False
    # В beta API методы доступны на отдельном URL. Сессию и прокси берём у aiogram.
    session = await bot.session.create_session()
    url = f"https://api.telegram.org/beta/bot{bot.token}/test/sendWidget"
    caption = _plain(text).splitlines()[0] if source_html else _plain(text)
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


async def beta_reply(message: Message, user_id: int, app, bot: Bot, text: str, *, reply_markup=None, source_html: str | None = None) -> bool:
    if not await app.users.beta_widgets(user_id):
        return False
    parts = formatting.split_message(text)
    if source_html is not None:
        parts = [text]
    if not all(len(zlib.compress((source_html or card_html(part)).encode(), 9)) <= 4096 for part in parts):
        return False
    for index, part in enumerate(parts):
        if not await send_card(message, bot, part, reply_markup=reply_markup if index == len(parts) - 1 else None, source_html=source_html):
            # Не повторяем уже отправленные части при частичном сбое.
            if index:
                return True
            return False
    return True
