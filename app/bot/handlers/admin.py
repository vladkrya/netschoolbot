"""Команды администратора (TG_ADMIN_ID). Остальным они не отвечают."""

from __future__ import annotations

import time

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ...context import AppContext
from .common import reply

router = Router(name="admin")

DESIGNS = {"new": "новый", "classic": "старый"}
CLIENTS = {"telegram": "в Telegram", "pwa": "установленное приложение", "web": "в браузере"}


def _is_admin(message: Message, app: AppContext) -> bool:
    admin = app.settings.telegram.admin_id
    return bool(admin) and message.from_user is not None and message.from_user.id == admin


def _split(counts: dict[str, int], names: dict[str, str]) -> str:
    total = sum(counts.values())
    if not total:
        return "—"
    return ", ".join(f"{names.get(key, key)} {n} ({round(n * 100 / total)}%)"
                     for key, n in sorted(counts.items(), key=lambda item: -item[1]))


@router.message(Command("adminstats"))
async def admin_stats(message: Message, app: AppContext) -> None:
    """Сводка: пользователи, открытия приложения, дизайн, чаты, опрос."""
    if not _is_admin(message, app):
        return
    db = app.db
    users = await db.fetch_one("SELECT COUNT(*) AS n, SUM(enabled) AS on_ FROM users")
    chats = await db.fetch_one("SELECT COUNT(*) AS n FROM chat_profiles")
    push = await db.fetch_one("SELECT COUNT(DISTINCT telegram_id) AS n FROM push_subscriptions")
    now = time.time()
    lines = [
        "<b>📊 Статистика</b>",
        f"Пользователей: {users['n']}, с проверкой оценок: {users['on_'] or 0}",
        f"В чатах: {chats['n']}, с push в браузере: {push['n']}",
        "",
        "<b>Открывали приложение</b>",
    ]
    for label, days in (("за сутки", 1), ("за неделю", 7), ("за месяц", 30)):
        stats = await app.miniapp.visit_stats(now - days * 86400)
        total = sum(stats["design"].values())
        lines.append(f"{label}: {total}")
        if days == 30 and total:
            lines.append(f"• дизайн: {_split(stats['design'], DESIGNS)}")
            lines.append(f"• где: {_split(stats['client'], CLIENTS)}")
    survey = await app.miniapp.survey_stats()
    if survey["shown"]:
        lines.append("")
        lines.append("<b>Опрос «нравится ли приложение»</b>")
        lines.append(f"Показан: {survey['shown']}, ответили: {survey['answered']}")
        if survey["answered"]:
            lines.append(f"• средняя оценка: {survey['average']:.1f} из 10")
            lines.append("• " + ", ".join(f"{score}: {n}" for score, n in sorted(survey["scores"].items())))
    lines.append("")
    lines.append("<i>Считаются открытия после обновления, в котором появилась статистика.</i>")
    await reply(message, "\n".join(lines))
