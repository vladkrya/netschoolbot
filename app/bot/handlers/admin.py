"""Команды администратора (TG_ADMIN_ID). Остальным они не отвечают."""

from __future__ import annotations

import time

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ...accounts import WEB_ID_BASE
from ...context import AppContext
from .common import reply

router = Router(name="admin")

DESIGNS = {"new": "новый", "classic": "старый"}
LOGINS = {"telegram": "только Telegram", "both": "Telegram и почта", "email": "только почта"}
PLACES = {"telegram": "только в Telegram", "both": "в Telegram и в браузере", "browser": "только в браузере"}


def _is_admin(message: Message, app: AppContext) -> bool:
    admin = app.settings.telegram.admin_id
    return bool(admin) and message.from_user is not None and message.from_user.id == admin


def _num(n: int) -> str:
    """1234 → «1 234» (неразрывный пробел)."""
    return f"{n:,}".replace(",", "\u00a0")


def _pct(n: int, total: int) -> str:
    if not total:
        return "—"
    share = n * 100 / total
    return "&lt;1%" if 0 < share < 1 else f"{round(share)}%"


def _split(counts: dict[str, int], names: dict[str, str]) -> str:
    total = sum(counts.values())
    if not total:
        return "—"
    return ", ".join(f"{names.get(key, key)} {_num(n)} ({_pct(n, total)})"
                     for key, n in sorted(counts.items(), key=lambda item: -item[1]))


def _groups(counts: dict[str, dict[str, int]], names: dict[str, str], *, fresh: bool) -> list[str]:
    """Строки групп: число, доля от всех в разбивке и (fresh) новые за 7 дней."""
    total = sum(item["n"] for item in counts.values())
    lines = []
    for key, name in names.items():
        item = counts[key]
        line = f"• {name}: <b>{_num(item['n'])}</b> ({_pct(item['n'], total)})"
        if fresh:
            line += f" · +{_num(item['new'])}"
        lines.append(line)
    return lines


@router.message(Command("adminstats"))
async def admin_stats(message: Message, app: AppContext) -> None:
    if not _is_admin(message, app):
        return
    await reply(message, await stats_report(app))


async def stats_report(app: AppContext) -> str:
    """Сводка: пользователи, способ входа, где открывают приложение,
    активность, опрос. HTML Telegram — его же показывает «Администрирование»."""
    db = app.db
    now = time.time()
    users = await db.fetch_one("SELECT COUNT(*) AS n, SUM(enabled) AS on_ FROM users")
    chats = await db.fetch_one("SELECT COUNT(*) AS n FROM chat_profiles")
    push = await db.fetch_one("SELECT COUNT(DISTINCT telegram_id) AS n FROM push_subscriptions")
    audience = await app.miniapp.audience_stats(now, WEB_ID_BASE)
    login, email = audience["login"], audience["email"]
    total = users["n"]
    fresh = sum(item["new"] for item in login.values())

    lines = [
        "📊 <b>Статистика</b>",
        "",
        f"👥 <b>Пользователи: {_num(total)}</b>",
        f"• новых за 7 дней: +{_num(fresh)}",
        f"• проверяют оценки: {_num(users['on_'] or 0)} ({_pct(users['on_'] or 0, total)})",
        f"• в чатах: {_num(chats['n'])}",
        f"• с push в браузере: {_num(push['n'])}",
        "",
        "🔑 <b>Как входят</b> <i>(+ новые за 7 дней)</i>",
        *_groups(login, LOGINS, fresh=True),
        f"Почта привязана: {_num(email['linked'])}, подтверждена: {_num(email['verified'])}"
        f" ({_pct(email['verified'], email['linked'])})",
        "",
        "📱 <b>Где открывают приложение</b>",
    ]
    for period, title in (("all", "За всё время"), ("month", "За 30 дней")):
        places = audience["places"][period]
        opened = sum(item["n"] for item in places.values())
        lines.append(f"<u>{title}</u>: {_num(opened)}" + (" <i>(+ новые за 7 дней)</i>" if period == "all" else ""))
        if not opened:
            continue
        lines.extend(_groups(places, PLACES, fresh=period == "all"))
        pwa = sum(item["pwa"] for item in places.values())
        lines.append(f"Установленное приложение (экран «Домой»): {_num(pwa)}")

    lines.append("")
    lines.append("📈 <b>Активность</b>")
    opened, spooky = [], []
    for label, days in (("сутки", 1), ("неделю", 7), ("месяц", 30)):
        stats = await app.miniapp.visit_stats(now - days * 86400)
        opened.append(f"за {label} {_num(sum(stats['design'].values()))}")
        halloween = stats["halloween"]
        if sum(halloween.values()):
            spooky.append(f"за {label} {_num(sum(halloween.values()))} "
                          f"(в новом {_num(halloween.get('new', 0))}, в старом {_num(halloween.get('classic', 0))})")
    lines.append("• открывали: " + ", ".join(opened))
    lines.append(f"• дизайн за месяц: {_split(stats['design'], DESIGNS)}")
    if spooky:
        lines.append("")
        lines.append("🎃 <b>С хеллоуинским оформлением</b>")
        lines.extend(f"• {item}" for item in spooky)

    survey = await app.miniapp.survey_stats()
    if survey["shown"]:
        lines.append("")
        lines.append("💬 <b>Опрос «нравится ли приложение»</b>")
        lines.append(f"• показан: {_num(survey['shown'])}, ответили: {_num(survey['answered'])}"
                     f" ({_pct(survey['answered'], survey['shown'])}), отказались: {_num(survey['declined'])}")
        if survey["answered"]:
            lines.append(f"• средняя оценка: {survey['average']:.1f} из 10")
            lines.append("• оценки: " + ", ".join(f"{score} — {n}" for score, n in sorted(survey["scores"].items())))
    return "\n".join(lines)


WIDGET_MODES = {
    "main": "основной бот",
    "reserve": "резервный бот (NETSCHOOL_TG_LOGIN_BOT_TOKEN)",
    "off": "выключен",
}


async def widget_mode(app: AppContext) -> str:
    """main, reserve или off — какой бот сейчас у Login Widget."""
    from ...db.repositories import AppFlagRepository
    from ...web.account import WIDGET_OFF_FLAG, widget_bot_token

    if await AppFlagRepository(app.db).get(WIDGET_OFF_FLAG):
        return "off"
    return "main" if await widget_bot_token(app) == app.settings.telegram.bot_token else "reserve"


async def set_widget_mode(app: AppContext, mode: str) -> None:
    from ...db.repositories import AppFlagRepository
    from ...web.account import WIDGET_MAIN_FLAG, WIDGET_OFF_FLAG

    flags = AppFlagRepository(app.db)
    await flags.set(WIDGET_OFF_FLAG, mode == "off")
    await flags.set(WIDGET_MAIN_FLAG, mode == "main")


@router.message(Command("tgwidget"))
async def tg_widget(message: Message, command: CommandObject, app: AppContext) -> None:
    """Вход на сайт и привязка Telegram через Login Widget: какой бот или выключить.

    Домен привязывается в BotFather (/setdomain) к одному боту — переключать
    сюда нужно на тот, к которому он сейчас привязан.
    """
    if not _is_admin(message, app):
        return
    from ...web.account import widget_bot_token

    mode = (command.args or "").strip().lower()
    if mode in WIDGET_MODES:
        await set_widget_mode(app, mode)

    domain = app.settings.web.login_widget_domain
    token = await widget_bot_token(app)
    state = WIDGET_MODES[await widget_mode(app)]
    lines = [
        "🔑 <b>Вход на сайт и привязка Telegram</b>",
        "",
        f"Сейчас: <b>{state}</b>" + (f", бот id {token.partition(':')[0]}" if token else ""),
        f"Домен: <code>{domain}</code>" if domain else "Домен не задан — NETSCHOOL_TG_LOGIN_DOMAIN в .env, кнопки нет",
        "",
        "/tgwidget main — основной бот",
        "/tgwidget reserve — резервный бот",
        "/tgwidget off — выключить кнопку (и вход, и привязку)",
        "",
        "<i>Домен должен быть привязан в @BotFather (/setdomain) именно к выбранному боту.</i>",
    ]
    await reply(message, "\n".join(lines))
