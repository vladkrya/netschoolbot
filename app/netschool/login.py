"""Вход в «Сетевой город» — общий для бота и сайта.

Бот ведёт диалог в Telegram, сайт — по шагам на странице, но сам вход и то,
что после него (сессия, режим API, дети, запуск проверки), одинаковы.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from ..domain.models import ApiMode, LoginType, User
from .errors import NetSchoolError, wrap

if TYPE_CHECKING:
    from ..context import AppContext

logger = logging.getLogger("netschoolbot.netschool")

OtpCallback = Callable[[str, dict], Awaitable[str]]
QrCallback = Callable[[str], Awaitable[None]]


def _client(app: AppContext, url: str):
    from netschoolpy import NetSchool

    proxy = app.settings.netschool.proxy_for(url)
    return NetSchool(url, proxy=proxy) if proxy else NetSchool(url)


async def _close(client) -> None:
    # Клиент входа больше не нужен: дальше работает пул.
    try:
        http = getattr(client, "_http", None)
        if http is not None and hasattr(http, "aclose"):
            await http.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Не удалось закрыть клиент после входа: %s", exc)


async def password_login(app: AppContext, user: User, otp_callback: OtpCallback | None = None) -> str | None:
    """Вход по паролю «Сетевого города» или логину Госуслуг. Возвращает сессию."""
    client = _client(app, user.school.url)
    try:
        if user.credentials.login_type is LoginType.ESIA:
            await client.login_via_gosuslugi(
                esia_login=user.credentials.login,
                esia_password=user.credentials.password,
                school=user.school.name or None,
                timeout=60,
                otp_callback=otp_callback,
            )
        else:
            await client.login(
                user_name=user.credentials.login,
                password=user.credentials.password,
                school=user.school.name,
            )
        return client.export_session()
    except NetSchoolError:
        raise
    except Exception as exc:
        raise wrap(exc, context="вход") from exc
    finally:
        await _close(client)


async def qr_login(app: AppContext, url: str, school: str, show_qr: QrCallback) -> str | None:
    """Вход по QR-коду Госуслуг. asyncio.TimeoutError — код истёк."""
    ttl = app.settings.netschool.qr_login_ttl
    client = _client(app, url)
    try:
        await asyncio.wait_for(
            client.login_via_gosuslugi_qr(qr_callback=show_qr, qr_timeout=ttl + 30, school=school, timeout=30),
            # Чуть больше, чем живёт код: иначе сканирование на последней
            # секунде пропадало впустую.
            timeout=ttl + 45,
        )
        return client.export_session()
    except asyncio.TimeoutError:
        raise
    except Exception as exc:
        raise wrap(exc, context="QR-вход") from exc
    finally:
        await _close(client)


async def settle_api_mode(app: AppContext, user: User) -> tuple[User, str]:
    """Довести новую веб-сессию входа до рабочего мобильного режима.

    После входа есть только веб-сессия, а мобильному клиенту нужен токен.
    Пока пользователь в мобильном режиме, меняем живую веб-сессию на токен
    сразу — позже, без нового кода подтверждения, этого не сделать. Не
    вышло — возвращаем веб-режим, чтобы человек не застрял в цикле «войдите
    заново». Возвращает пользователя и пояснение (пустое — сказать нечего).
    """
    if user.api_mode is not ApiMode.MOBILE:
        return user, ""
    try:
        await app.pool.switch_api(user, ApiMode.MOBILE)
    except Exception as exc:  # noqa: BLE001 — любой сбой означает «остаёмся на вебе»
        logger.info("Мобильный токен после входа не получен для %s: %s", user.telegram_id, exc)
        reverted = await app.users.save(replace(user, api_mode=ApiMode.WEB))
        return reverted, (
            "⚠️ Мобильное API включить не удалось — работаю через веб-API. "
            "Попробовать снова: /changeapi."
        )
    return user, "📱 Мобильное API включено: вход держится 60 дней."


def _same_diary(previous: User | None, current: User) -> bool:
    """Тот же ученик той же школы, что и до входа."""
    return (
        previous is not None
        and previous.school.configured
        and previous.school.url.rstrip("/") == current.school.url.rstrip("/")
        and previous.selected_student_id is not None
        and previous.selected_student_id == current.selected_student_id
    )


async def finish_login(app: AppContext, user: User, payload: str | None) -> tuple[User, str]:
    """После удачного входа: сохранить сессию и пользователя, найти детей,
    запустить проверку оценок. Возвращает пользователя и пояснение про API."""
    if payload:
        await app.sessions.save(user.telegram_id, payload)
    previous = await app.users.get(user.telegram_id)
    saved = await app.users.save(replace(user, enabled=True))
    saved, api_note = await settle_api_mode(app, saved)
    try:
        saved = await app.diary.sync_students(saved)
    except NetSchoolError as exc:
        # Вход удался, а список детей — нет. Это не повод откатывать вход.
        logger.info("Не удалось получить список учеников для %s: %s", saved.telegram_id, exc.reason)
    # Новая школа или новый ребёнок — старое состояние слежения неприменимо.
    # Повторный вход в тот же дневник (сессия истекла, сменили пароль) его не
    # трогает: раньше он стирал отметки «выполнено» у заданий и историю оценок.
    if not _same_diary(previous, saved):
        await app.state.forget_all(saved.telegram_id)
    # Пока не вошли, проверки стояли: накопившееся за это время — молча.
    await app.state.mark_baseline_pending(saved.telegram_id, True)
    await app.cache.clear(saved.telegram_id)
    await app.watchers.start(saved.telegram_id)
    return saved, api_note
