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

from ..domain.models import ApiMode, LoginType, School, User
from . import ownership
from .myschool import MYSCHOOL_URL
from .errors import NetSchoolError, Reason, wrap

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
        await ownership.claim(app, user.telegram_id, user.school.url, await ownership.account_id(client))
        return client.export_session()
    except NetSchoolError:
        raise
    except Exception as exc:
        raise wrap(exc, context="вход") from exc
    finally:
        await _close(client)


async def myschool_login(app: AppContext, user: User, otp_callback: OtpCallback | None = None) -> str:
    """Вход в «Госуслуги Моя школа» логином и паролем Госуслуг. Возвращает сессию."""
    from .myschool import new_client

    client = new_client(app.settings.netschool.proxy_for(user.school.url))
    try:
        await client.login_via_gosuslugi_mobile(
            user.credentials.login, user.credentials.password, otp_callback=otp_callback
        )
        return client.export_session()
    except NetSchoolError:
        raise
    except Exception as exc:
        raise wrap(exc, context="вход в «Мою школу»") from exc
    finally:
        await client.close()


async def qr_login(app: AppContext, url: str, school: str, show_qr: QrCallback, *, telegram_id: int) -> str | None:
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
        await ownership.claim(app, telegram_id, url, await ownership.account_id(client))
        return client.export_session()
    except asyncio.TimeoutError:
        raise
    except Exception as exc:
        raise wrap(exc, context="QR-вход") from exc
    finally:
        await _close(client)


def _school_name(client) -> str:
    """Название активной школы из профиля мобильного API."""
    orgs = (getattr(client, "_user", None) or {}).get("organizations") or []
    active = next((org for org in orgs if org.get("isActive")), orgs[0] if orgs else {})
    return str((active.get("organization") or {}).get("name") or "")


async def mobile_code_login(app: AppContext, telegram_id: int, user_code: str, server_id: str) -> tuple[School, str]:
    """Вход по одноразовому коду из приложения. Возвращает школу и сессию.

    Человек входит на сайте региона во встроенном браузере приложения
    (Госуслуги или логин с паролем), и пароль сюда не попадает. Приложение
    берёт `userCode` из /webapi/mysettings/mobile/pincode, а сервер меняет
    его на 60-дневный токен мобильного API: токен нужен слежению за оценками.
    """
    from netschoolpy import MobileNetSchool
    from netschoolpy.mobile import CATALOG_URL, exchange_code, get_region

    settings = app.settings.netschool
    try:
        region = await get_region(server_id, proxy=settings.proxy_for(CATALOG_URL), timeout=settings.http_timeout)
    except Exception as exc:
        raise wrap(exc, context="регион приложения") from exc
    if region.unavailable:
        raise NetSchoolError(Reason.UNKNOWN, f"Регион «{region.name}» отключён в приложении NetSchool.")
    proxy = settings.proxy_for(region.url)
    secret = settings.mobile_client_secret or None
    client = MobileNetSchool(region.url, proxy=proxy, client_secret=secret)
    try:
        token = await exchange_code(user_code, client_secret=secret, proxy=proxy, timeout=settings.http_timeout)
        await client.login_with_token(token)
        await ownership.claim(app, telegram_id, region.url, await ownership.account_id(client))
        return School(url=region.url, name=_school_name(client) or region.name), client.export_session()
    except NetSchoolError:
        raise
    except Exception as exc:
        raise wrap(exc, context="вход по коду приложения") from exc
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


def _same_school(a: str, b: str) -> bool:
    return bool(a) and a.rstrip("/") == b.rstrip("/")


async def _known_diary(app: AppContext, telegram_id: int) -> tuple[str, int] | None:
    """Чей дневник был до входа: текущий, а после /logout — запомненный при выходе."""
    previous = await app.users.get(telegram_id)
    if previous and previous.school.configured and previous.selected_student_id is not None:
        return previous.school.url, previous.selected_student_id
    return await app.state.last_diary(telegram_id)


async def finish_login(app: AppContext, user: User, payload: str | None, *,
                       mobile_ready: bool = False) -> tuple[User, str]:
    """После удачного входа: сохранить сессию и пользователя, найти детей,
    запустить проверку оценок. Возвращает пользователя и пояснение про API.

    `mobile_ready` — сессия уже мобильная (вход по коду приложения): менять
    на токен нечего, а веб-сессии для этого и нет."""
    if user.api_mode is ApiMode.MYSCHOOL:
        if user.school.url != MYSCHOOL_URL:
            # Вошли в «Сетевой город», а в базе остался режим «Моей школы».
            user = replace(user, api_mode=ApiMode.MOBILE, selected_student_id=None)
        else:
            mobile_ready = True  # веб-сессии, которую надо менять на токен, нет
    if payload:
        await app.sessions.save(user.telegram_id, payload)
    known = await _known_diary(app, user.telegram_id)
    # Вход после /logout: ученик не выбран — тот же, что был до выхода
    # (sync_students оставит его, только если он есть в этом аккаунте).
    if known and user.selected_student_id is None and _same_school(known[0], user.school.url):
        user = replace(user, selected_student_id=known[1])
    saved = await app.users.save(replace(user, enabled=True))
    saved, api_note = (saved, "") if mobile_ready else await settle_api_mode(app, saved)
    try:
        saved = await app.diary.sync_students(saved)
    except NetSchoolError as exc:
        # Вход удался, а список детей — нет. Это не повод откатывать вход.
        logger.info("Не удалось получить список учеников для %s: %s", saved.telegram_id, exc.reason)
    # Новая школа или новый ребёнок — старое состояние слежения неприменимо.
    # Повторный вход в тот же дневник (сессия истекла, сменили пароль) его не
    # трогает: раньше он стирал отметки «выполнено» у заданий и историю оценок.
    if not (known and _same_school(known[0], saved.school.url) and known[1] == saved.selected_student_id):
        await app.state.forget_all(saved.telegram_id)
    await app.state.remember_diary(saved.telegram_id, "", None)
    # Пока не вошли, проверки стояли: накопившееся за это время — молча.
    await app.state.mark_baseline_pending(saved.telegram_id, True)
    await app.cache.clear(saved.telegram_id)
    await app.watchers.start(saved.telegram_id)
    # Календарь (если подключён) — сразу расписанием этого дневника, а не
    # через полчаса очередного цикла: после /logout он пустой.
    if app.caldav is not None:
        app.caldav.request_sync(saved.telegram_id)
    return saved, api_note
