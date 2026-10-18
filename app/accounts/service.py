"""Привязка Telegram к аккаунту, созданному на сайте без него."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from . import has_telegram
from .mailer import letter

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
    problem = await connect_accounts(app, web_id, telegram_id)
    if problem:
        return problem
    moved = await app.users.get(telegram_id)
    if moved is not None and moved.ready_to_check:
        await app.watchers.start(telegram_id)
    logger.info("Аккаунт %s привязан к Telegram %s", web_id, telegram_id)
    await _notify_linked(app, telegram_id)
    return ("✅ Telegram привязан. Уведомления теперь приходят и сюда, а на сайт можно "
            "по-прежнему входить по почте. Откройте приложение заново.")


async def _notify_linked(app: AppContext, telegram_id: int) -> None:
    """Письмо на почту аккаунта: если привязывал не владелец, он об этом узнает."""
    account = await app.accounts.by_user(telegram_id)
    if not account or not app.mailer.configured:
        return
    text, body = letter(
        "Telegram привязан",
        "К вашему аккаунту Дневника привязали Telegram: уведомления теперь приходят и туда, "
        "а на сайт можно по-прежнему входить по почте.",
        note="Если это были не вы — смените пароль в приложении: Настройки → Вход по почте.",
        site=app.mailer.site,
    )
    try:
        await app.mailer.send(account["email"], "Telegram привязан — Дневник", text, html_body=body)
    except Exception as exc:  # noqa: BLE001 — привязка уже прошла, письмо не главное
        logger.warning("Письмо о привязке Telegram не ушло: %s", exc)


async def connect_accounts(app: AppContext, web_id: int, telegram_id: int) -> str | None:
    """Both owners have been authenticated by password or a one-time bot code."""
    existing = await app.users.get(telegram_id)
    source = await app.users.get(web_id)
    if source is None or has_telegram(web_id) or not has_telegram(telegram_id):
        return "Аккаунт не найден — начните привязку заново."
    if await app.accounts.by_user(telegram_id):
        return "К этому Telegram уже привязана другая почта. Смените её в настройках входа."
    if existing and existing.school.configured and source.school.configured:
        if (existing.school.url.rstrip('/'), existing.school.name, existing.selected_student_id) != (
                source.school.url.rstrip('/'), source.school.name, source.selected_student_id):
            return ("В этих аккаунтах открыты разные дневники. Чтобы сохранить оба, привязка отменена. "
                    "Войдите в один и тот же дневник и повторите привязку.")
    keep_telegram = existing is not None and (
        existing.school.configured or await app.accounts.has_own_data(telegram_id))
    if keep_telegram and source.school.configured and not existing.school.configured:
        return ("В Telegram остались данные другого дневника. Войдите в тот же дневник "
                "в Telegram и повторите привязку — ваши данные сохранены.")
    await app.watchers.stop(web_id)
    await app.pool.invalidate(web_id)
    await app.hub.disconnect(web_id)
    attempt = app.web_logins.pop(web_id, None)
    if attempt is not None and getattr(attempt, 'task', None):
        attempt.task.cancel()
    if keep_telegram:
        if not await app.accounts.merge_into(web_id, telegram_id):
            return "Аккаунт уже привязан — обновите страницу."
    else:
        await app.accounts.move_user(web_id, telegram_id)
    return None
