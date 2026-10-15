"""Напоминания о своих задачах ученика: «через 15 мин — принести циркуль».

Задачу можно поставить ко времени и попросить напомнить за N минут
(app/web/extras.py). Момент напоминания хранится в базе (remind_at), а здесь
раз в несколько секунд забираются те, чьё время пришло. Отдельный цикл, а не
проверка оценок: та ходит раз в несколько минут, напоминанию нужна минута.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from ..domain import formatting

logger = logging.getLogger("netschoolbot.reminders")

# Как часто смотреть, не пора ли напомнить.
POLL_SECONDS = 20
# Напоминание, проспанное дольше этого (бот лежал), уже не шлём.
STALE_SECONDS = 30 * 60


def _when(minutes: int) -> str:
    if minutes <= 0:
        return "Сейчас"
    if minutes >= 1440 and minutes % 1440 == 0:
        return "Завтра" if minutes == 1440 else f"Через {minutes // 1440} дн."
    if minutes >= 60 and minutes % 60 == 0:
        return f"Через {minutes // 60} ч"
    return f"Через {minutes} мин"


def reminder_text(note: dict[str, Any]) -> tuple[str, str]:
    """Текст для Telegram (HTML) и строка для push."""
    when = _when(int(note.get("remind") or 0))
    subject = f" ({note['subject']})" if note.get("subject") else ""
    line = f"{when}, в {note['time']}: {note['text']}{subject}"
    return f"⏰ {formatting.esc(line)}", line


async def send_due(notes: Any, users: Any, notifier: Any, now: float) -> int:
    """Разослать напоминания, время которых пришло. Возвращает, сколько ушло."""
    sent = 0
    for telegram_id, note, due in await notes.due_reminders(now):
        # Сначала отметить: сбой отправки не должен повторять напоминание
        # каждые 20 секунд.
        await notes.reminded(note["id"])
        if now - due > STALE_SECONDS:
            continue
        user = await users.get(telegram_id)
        if user is None:
            continue
        text, line = reminder_text(note)
        try:
            # Своя задача — дело самого ученика: родным копию не шлём.
            await notifier.send_text(user, text, push=("Напоминание", line), kind="note", family=False)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Напоминание %s не ушло: %s", telegram_id, exc)
    return sent


async def run_note_reminders(notes: Any, users: Any, notifier: Callable[[], Any],
                             *, clock: Callable[[], float] = time.time) -> None:
    """Бесконечный цикл; notifier — функция: уведомитель задают позже."""
    while True:
        target = notifier()
        if target is not None:
            try:
                await send_due(notes, users, target, clock())
            except Exception:  # noqa: BLE001
                logger.exception("Сбой рассылки напоминаний")
        await asyncio.sleep(POLL_SECONDS)
