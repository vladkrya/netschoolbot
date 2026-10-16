"""Привязка Telegram к аккаунту, созданному на сайте без него."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from . import has_telegram

if TYPE_CHECKING:
    from ..context import AppContext

logger = logging.getLogger("netschoolbot.accounts")


async def link_telegram(app: AppContext, code: str, telegram_id: int) -> str:
    """Перенести аккаунт с сайта на настоящий Telegram. Возвращает ответ боту."""
    if not has_telegram(telegram_id):
        return "Не получилось: это не аккаунт Telegram."
    web_id = await app.accounts.take_link_code(code)
    if web_id is None or has_telegram(web_id):
        return "Код неверный или устарел. Получите новый в приложении: Настройки → Вход по почте."
    existing = await app.users.get(telegram_id)
    if existing is not None and existing.school.configured:
        # У этого Telegram уже свой дневник — склеивать два нельзя.
        return ("Этот Telegram уже вошёл в «Сетевой город». Чтобы входить на сайт по почте, "
                "откройте приложение из бота: Настройки → Вход по почте.")
    if existing is not None and await app.accounts.has_own_data(telegram_id):
        # После /logout школы нет, но заметки, свои уроки и отметки на месте —
        # перенос аккаунта с сайта стёр бы их вместе с записью Telegram.
        return ("У этого Telegram уже есть свои данные: заметки, свои уроки, отметки о заданиях. "
                "Чтобы их не потерять, откройте приложение из бота (/app) и войдите в «Сетевой город» там.")
    # Всё, что живёт в памяти под старым номером, — остановить.
    await app.watchers.stop(web_id)
    await app.pool.invalidate(web_id)
    await app.hub.disconnect(web_id)
    app.web_logins.pop(web_id, None)
    await app.accounts.move_user(web_id, telegram_id)
    moved = await app.users.get(telegram_id)
    if moved is not None and moved.ready_to_check:
        await app.watchers.start(telegram_id)
    logger.info("Аккаунт %s привязан к Telegram %s", web_id, telegram_id)
    return ("✅ Telegram привязан. Уведомления теперь приходят и сюда, а на сайт можно "
            "по-прежнему входить по почте. Откройте приложение заново.")
