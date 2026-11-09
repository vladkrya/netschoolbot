"""Карточки Telegram Widget (Bot API 10.4)."""

from __future__ import annotations

import html
import json
import logging
import math
import textwrap
import zlib
from collections import defaultdict
from html.parser import HTMLParser

from aiogram import Bot
from aiogram.types import Message

from ..domain import formatting
from ..domain.records import HomeworkRecord

logger = logging.getLogger("netschoolbot.bot")


async def widget_api_status(bot: Bot) -> str | None:
    """Сообщение о недоступности основного Bot API для этого токена."""
    session = await bot.session.create_session()
    url = f"https://api.telegram.org/bot{bot.token}/getMe"
    try:
        async with session.get(url, timeout=8) as response:
            result = await response.json(content_type=None)
        if result.get("ok"):
            return None
        if response.status == 401:
            return "Telegram отклонил токен бота (401). Виджеты недоступны."
        return f"Bot API сейчас недоступен (HTTP {response.status})."
    except Exception:  # noqa: BLE001 — только проверка доступности, без пользовательских данных
        return "Не удалось связаться с Bot API. Попробуйте позже."


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
    """Оформить ответ как карточку со страницами внутри лимита 600 px."""
    lines = _plain(text).splitlines()
    title = html.escape(lines[0]) if lines else "СГОшка"
    pages: list[list[str]] = [[]]
    used = 0
    for line in lines[1:]:
        if not line.strip():
            continue
        kind = "section" if line.startswith(("📅", "🗓", "📆")) else "row"
        for part in textwrap.wrap(line, 120, break_long_words=True, break_on_hyphens=False) or [line]:
            cost = (25 if kind == "section" else 21) + 20 * max(1, math.ceil(len(part) / 38))
            if pages[-1] and used + cost > 280:
                pages.append([])
                used = 0
            pages[-1].append(f'<div class="{kind}">{html.escape(part)}</div>')
            used += cost
    content = ''.join(f'<div class="page{" active" if i == 0 else ""}">{"".join(rows)}</div>' for i, rows in enumerate(pages))
    pager = ('<nav class="pager"><button id="prev" disabled>← Назад</button>'
             f'<span id="position">1 / {len(pages)}</span><button id="next">Дальше →</button></nav>') if len(pages) > 1 else ''
    return ("<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<style>:root{color-scheme:light;--bg:#eef1f6;--card:#fff;--text:#1d2430;--muted:#6b7585;--border:#dde2ea;--blue:#4a6fae}"
            "@media(prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#141a22;--card:#1e2633;--text:#e3e8ef;--muted:#8e99aa;--border:#2d3748;--blue:#3f63a3}}"
            "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,sans-serif}"
            ".head{background:linear-gradient(135deg,var(--blue),#36558f);color:#fff;padding:15px 16px;border-radius:0 0 16px 16px}"
            ".brand{font-size:11px;letter-spacing:.1em;opacity:.8}.title{font-size:18px;font-weight:700;margin-top:4px}"
            ".card{margin:10px;padding:4px 12px;background:var(--card);border-radius:12px;overflow-wrap:anywhere}"
            ".section{font-weight:700;padding:11px 0 5px;color:var(--blue)}"
            ".row{padding:8px 0;border-bottom:1px solid var(--border)}.row:last-child{border:0}"
            ".page{display:none}.page.active{display:block}.pager{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:10px 10px 0;color:var(--muted);font-size:12px}"
            ".pager button{border:0;border-radius:9px;background:var(--blue);color:white;padding:8px 11px;font:600 12px system-ui}.pager button:disabled{opacity:.4}"
            "</style></head><body><div class=head><div class=brand>СГОшка · дневник</div><div class=title>"
            + title + "</div></div>" + pager + '<div class=card>' + content + "</div>"
            "<script>var p=0,ps=document.querySelectorAll('.page'),prev=document.getElementById('prev'),next=document.getElementById('next'),pos=document.getElementById('position');"
            "function show(){for(var i=0;i<ps.length;i++)ps[i].className=i===p?'page active':'page';if(prev)prev.disabled=p===0;if(next)next.disabled=p===ps.length-1;if(pos)pos.textContent=(p+1)+' / '+ps.length}"
            "if(prev)prev.onclick=function(){if(p>0){p--;show()}};if(next)next.onclick=function(){if(p<ps.length-1){p++;show()}};</script></body></html>")


def grades_card_html(subjects: list[str]) -> str:
    """Выбор предмета и просмотр оценок без клавиатуры под сообщением."""
    pages = [subjects[i:i + 5] for i in range(0, len(subjects), 5)] or [[]]
    blocks = []
    for page, names in enumerate(pages):
        buttons = ''.join(
            f'<button class="subject" data-index="{page * 5 + i}">{html.escape(name)}</button>'
            for i, name in enumerate(names)
        )
        blocks.append(f'<div class="subject-page{" active" if page == 0 else ""}">{buttons}</div>')
    return ("<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><style>"
            ":root{color-scheme:light;--bg:#eef1f6;--card:#fff;--text:#1d2430;--muted:#6b7585;--border:#dde2ea;--blue:#4a6fae}"
            "@media(prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#141a22;--card:#1e2633;--text:#e3e8ef;--muted:#8e99aa;--border:#2d3748;--blue:#3f63a3}}"
            "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 system-ui,-apple-system,sans-serif}"
            ".head{background:var(--blue);color:white;padding:16px;border-radius:0 0 16px 16px}.brand{font-size:11px;letter-spacing:.1em;opacity:.8}.title{font-size:19px;font-weight:700;margin-top:4px}"
            ".content{padding:10px}.subject-page{display:none}.subject-page.active{display:block}.subject{display:block;width:100%;text-align:left;border:1px solid var(--border);border-radius:10px;background:var(--card);color:var(--text);padding:11px;margin:0 0 7px;font:inherit}"
            ".nav{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:9px}.nav button{border:0;border-radius:9px;background:var(--blue);color:white;padding:8px;font:600 12px system-ui}.nav button:disabled{opacity:.4}"
            ".detail{display:none;background:var(--card);border-radius:10px;padding:10px;white-space:pre-wrap;overflow-wrap:anywhere}.detail.active{display:block}.error{color:#b84242;font-size:12px}.back{border:0;background:none;color:var(--blue);padding:3px 0 9px;font:inherit}"
            "</style><body><div class=head><div class=brand>СГОшка · дневник</div><div class=title>📊 Оценки по предметам</div></div>"
            '<div class=content><div id=list><nav class=nav><button id=prev disabled>← Назад</button><span id=position></span><button id=next>Дальше →</button></nav>'
            + ''.join(blocks) + '</div><div id=viewer style="display:none"><button id=back class=back>← Предметы</button><div id=selected-subject></div><nav class=nav><button id=detail-prev>← Назад</button><span id=detail-position></span><button id=more>Дальше →</button></nav><div id=detail class="detail active"></div></div><div id=error class=error></div></div>'
            "<script>var W=window.TelegramWidget,ps=document.querySelectorAll('.subject-page'),page=0,index=-1,detailPage=0,total=1;"
            "var prev=document.getElementById('prev'),next=document.getElementById('next'),pos=document.getElementById('position'),err=document.getElementById('error');"
            "function show(){for(var i=0;i<ps.length;i++)ps[i].className=i===page?'subject-page active':'subject-page';prev.disabled=page===0;next.disabled=page===ps.length-1;pos.textContent=(page+1)+' / '+ps.length}"
            "prev.onclick=function(){if(page>0){page--;show()}};next.onclick=function(){if(page<ps.length-1){page++;show()}};show();"
            "function load(i,p){err.textContent='Загружаю…';W.request('g:'+i+':'+p).then(function(a){var x=JSON.parse(a.result||'{}');if(x.error){err.textContent=x.error;return}err.textContent='';"
            "index=i;detailPage=x.page;total=x.pages;document.getElementById('list').style.display='none';document.getElementById('viewer').style.display='block';"
            "document.getElementById('selected-subject').textContent=bs[Number(i)].textContent;"
            "document.getElementById('detail').textContent=x.body;document.getElementById('detail-position').textContent=(x.page+1)+' / '+x.pages;document.getElementById('detail-prev').disabled=x.page===0;document.getElementById('more').disabled=x.page>=x.pages-1;},"
            "function(e){err.textContent='Не загрузилось: '+(e.code||'ошибка')})}"
            "var bs=document.querySelectorAll('.subject');for(var i=0;i<bs.length;i++)bs[i].onclick=function(){load(this.getAttribute('data-index'),0)};"
            "document.getElementById('back').onclick=function(){document.getElementById('viewer').style.display='none';document.getElementById('list').style.display='block'};"
            "document.getElementById('detail-prev').onclick=function(){if(detailPage>0)load(index,detailPage-1)};document.getElementById('more').onclick=function(){if(detailPage+1<total)load(index,detailPage+1)};"
            "</script></body></html>")


def grade_detail_pages(subject: str, records: list) -> list[str]:
    """Текст ответов для запроса из виджета: каждый ответ меньше лимита 1024 байта."""
    lines = _plain(formatting.marks_by_subject(subject, records)).splitlines()
    pages = [""]
    for line in lines:
        for part in textwrap.wrap(line, 100, break_long_words=True, break_on_hyphens=False) or [""]:
            addition = part + "\n"
            if pages[-1] and len((pages[-1] + addition).encode()) > 550:
                pages.append("")
            pages[-1] += addition
    return [page.strip() for page in pages]


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
            if used + cost + (0 if chunk else 34) > 280 and (chunk or pages[-1]):
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
                toggle = (f'<button class="toggle" type="button" data-key="{item.key}" data-done="{int(complete)}">{status}</button>'
                          if not item.nothing_to_do else f'<span class="pill">{status}</span>')
                top = f'<div class="task-top"><span class="subject">{subject}</span>{toggle}</div>'
                if details:
                    blocks.append(
                        f'<div class="task{" done" if complete else ""}">{top}'
                        f'<details class="expand{" has-full" if needs_full else ""}"><summary><span class="summary">{short}</span>'
                        '<span class="more">Подробнее ↓</span><span class="less">Свернуть ↑</span></summary>'
                        f'<div class="detail">{"<br>".join(details)}</div></details></div>'
                    )
                else:
                    blocks.append(f'<div class="task{" done" if complete else ""}">{top}<span class="summary">{short}</span></div>')
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
        '.expand summary{list-style:none;cursor:pointer}.expand summary::-webkit-details-marker{display:none}.page.focused .task:not(.active-detail){display:none}'
        '.task.done{border-left-color:var(--green)}.task-top{display:flex;align-items:center;justify-content:space-between;gap:8px}.subject{font-weight:700;font-size:14px}'
        '.pill,.toggle{flex:none;border:0;border-radius:8px;padding:4px 7px;background:var(--light);color:var(--blue);font:700 10px system-ui}.toggle{cursor:pointer}.done .pill,.done .toggle{color:var(--green)}'
        '.summary,.detail{display:block;margin-top:5px;overflow-wrap:anywhere}.detail{padding-top:8px;border-top:1px solid var(--border)}.done .summary{color:var(--muted)}.more,.less{display:block;color:var(--blue);font-size:11px;margin-top:6px}.less{display:none}.expand.has-full[open] .summary,.expand[open] .more{display:none}.expand[open] .less{display:block}'
        '.pager{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:5px 0 8px}.pager button{border:0;border-radius:9px;background:var(--blue);color:white;padding:8px 11px;font:600 12px system-ui}.pager button:disabled{background:var(--light);color:var(--muted)}.pager span{color:var(--muted);font-size:12px}'
        '.empty{padding:18px;text-align:center;background:var(--card);border-radius:12px}.foot{padding:0 13px 12px;color:var(--muted);font-size:11px}.foot button{border:0;background:none;color:var(--blue);font:inherit;cursor:pointer}'
    )
    pager = ('<nav class="pager"><button id="prev-day" type="button">← Назад</button>'
             f'<span id="day-position">1 / {len(pages)}</span>'
             '<button id="next-day" type="button">Дальше →</button></nav>') if len(pages) > 1 else ''
    script = ("<script>var days=document.querySelectorAll('.page'),page=0,W=window.TelegramWidget;"
              "var prev=document.getElementById('prev-day'),next=document.getElementById('next-day'),pos=document.getElementById('day-position');"
              "function showDay(){for(var i=0;i<days.length;i++)days[i].className=i===page?(days[i].querySelector('.expand[open]')?'page active focused':'page active'):'page';"
              "if(prev)prev.disabled=page===0;if(next)next.disabled=page===days.length-1;"
              "if(pos)pos.textContent=(page+1)+' / '+days.length;}"
              "if(prev)prev.addEventListener('click',function(){if(page>0){page--;showDay()}});"
              "if(next)next.addEventListener('click',function(){if(page<days.length-1){page++;showDay()}});"
              "var bs=document.querySelectorAll('.toggle'),note=document.getElementById('sync-status'),count=document.getElementById('open-count');"
              "function setDone(b,d){b.setAttribute('data-done',d?'1':'0');b.textContent=d?'Готово':'Задано';var t=b.parentNode.parentNode,f=t.className.indexOf('active-detail')>=0;t.className=(d?'task done':'task')+(f?' active-detail':'');}"
              "function apply(s){if(!s)return;try{var x=JSON.parse(s),n=0;for(var i=0;i<bs.length;i++){var b=bs[i],d=x.done.indexOf(b.getAttribute('data-key'))>=0;setDone(b,d);if(!d)n++}if(count)count.textContent=n}catch(e){}}"
              "for(var i=0;i<bs.length;i++)bs[i].onclick=function(){var b=this,key=b.getAttribute('data-key'),want=b.getAttribute('data-done')!=='1';b.disabled=true;"
              "W.request('h:'+key+':'+(want?'1':'0')).then(function(a){b.disabled=false;var x=JSON.parse(a.result||'{}');if(x.error){note.textContent=x.error;return}setDone(b,!!x.done);if(x.all)apply(JSON.stringify({done:x.all}));note.textContent='';},"
              "function(e){b.disabled=false;note.textContent='Не сохранилось: '+(e.code||'ошибка');})};"
              "var ex=document.querySelectorAll('.expand');for(var i=0;i<ex.length;i++)ex[i].addEventListener('toggle',function(){var t=this.parentNode,p=t.parentNode.parentNode;"
              "if(this.open){var all=p.querySelectorAll('.expand');for(var j=0;j<all.length;j++)if(all[j]!==this)all[j].open=false;p.className='page active focused';t.className+=' active-detail'}"
              "else{t.className=t.className.replace(' active-detail','');if(!p.querySelector('.expand[open]'))p.className='page active'}});"
              "var refresh=document.getElementById('refresh'),lastSync=0;function sync(silent){lastSync=Date.now();W.request('s').then(function(a){var x=JSON.parse(a.result||'{}');if(x.error){if(!silent)note.textContent=x.error;return}apply(a.result);note.textContent='';},function(e){if(!silent)note.textContent='Не обновилось: '+(e.code||'ошибка')})}"
              "if(refresh)refresh.onclick=function(){sync(false)};if(W&&W.onEvent){W.onEvent('chatDataChanged',function(){apply(W.chatData)});W.onEvent('activated',function(){if(Date.now()-lastSync>10000)sync(true)})}"
              "if(W)apply(W.chatData);showDay();</script>")
    return ("<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><style>"
            + css + "</style><body><div class=head><div class=head-top>СГОшка · дневник</div>"
            f'<div class=head-title>📚 Домашние задания</div><div class=head-sub>На {len(grouped)} учебных дня · осталось <span id=open-count>{open_count}</span></div></div>'
            + '<div class=content>' + pager + ''.join(blocks) + '</div><div class=foot><button id=refresh>Обновить отметки</button> <span id=sync-status></span></div>'
            + script + '</body></html>')


def homework_widget_parts(items: list[HomeworkRecord], done: set[str]) -> list[list[HomeworkRecord]]:
    """Разбить большой список до отправки: Telegram ограничивает виджет 4 КБ zlib."""
    if not items:
        return [[]]
    parts: list[list[HomeworkRecord]] = []
    current: list[HomeworkRecord] = []
    for item in items:
        candidate = current + [item]
        source = homework_card_html(candidate, done).encode()
        if current and (len(source) > 32768 or len(zlib.compress(source, 9)) > 3700):
            parts.append(current)
            current = [item]
        else:
            current = candidate
        single = homework_card_html(current, done).encode()
        if len(single) > 32768 or len(zlib.compress(single, 9)) > 4096:
            return []
    if current:
        parts.append(current)
    return parts


async def send_card(message: Message, bot: Bot, text: str, *, reply_markup=None, source_html: str | None = None,
                    interactive: bool = False, chat_data: str | None = None) -> int | None:
    """Отправить виджет; ID сообщения нужен для обработки действий в HTML."""
    source = source_html or card_html(text)
    encoded = source.encode("utf-8")
    if len(encoded) > 32768 or len(zlib.compress(encoded, 9)) > 4096:
        return None
    # Сессию и прокси берём у aiogram.
    session = await bot.session.create_session()
    url = f"https://api.telegram.org/bot{bot.token}/sendWidget"
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
    if interactive:
        payload["allow_queries"] = "true"
    if chat_data is not None:
        payload["chat_data"] = chat_data
    try:
        async with session.post(url, data=payload, timeout=12) as response:
            result = await response.json(content_type=None)
        if result.get("ok"):
            return int(result["result"]["message_id"])
        logger.warning("Widget API rejected sendWidget: %s", str(result.get("description", ""))[:160])
    except Exception as exc:  # noqa: BLE001 — бета недоступна, ответим обычным сообщением
        logger.warning("Widget API unavailable: %s", type(exc).__name__)
    return None


async def beta_reply(message: Message, user_id: int, app, bot: Bot, text: str, *, reply_markup=None,
                     source_html: str | None = None, widget_meta: dict | None = None,
                     widget_parts: list[tuple[str, dict]] | None = None) -> bool:
    if not await app.users.beta_widgets(user_id):
        return False
    payloads = ([(text, source, meta) for source, meta in widget_parts] if widget_parts is not None
                else [(text, source_html, widget_meta)] if source_html is not None
                else [(part, None, None) for part in formatting.split_message(text)])
    if not payloads or not all(len((source or card_html(part)).encode()) <= 32768 and
                               len(zlib.compress((source or card_html(part)).encode(), 9)) <= 4096
                               for part, source, _ in payloads):
        return False
    for index, (part, source, meta) in enumerate(payloads):
        chat_data = None
        if meta and meta.get("kind") == "homework":
            chat_data = json.dumps({"done": meta["done"]}, separators=(",", ":"))
            if len(chat_data.encode()) > 1024:
                return bool(index)
        sent_id = await send_card(message, bot, part, reply_markup=reply_markup if index == len(payloads) - 1 and meta is None else None,
                                  source_html=source, interactive=meta is not None, chat_data=chat_data)
        if not sent_id:
            # Не повторяем уже отправленные части при частичном сбое.
            if index:
                return True
            return False
        if meta:
            await app.cache.put(user_id, f"widget:{sent_id}", {k: v for k, v in meta.items() if k != "done"})
    return True
