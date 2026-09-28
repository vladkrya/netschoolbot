"""Пул сессий «Сетевого города» — один на весь процесс.

Это центральное изменение всей переработки. Раньше сессии жили в двух местах:
`NETSCHOOL_SESSION_CACHE` в модуле бота и отдельные клиенты, создаваемые в
`web/miniapp.py` на каждый HTTP-запрос через `asyncio.run()`. Два кэша не
знали друг о друге, поэтому:

  * один и тот же пользователь держал две сессии на школьном сервере;
  * веб создавал новый event loop на запрос, и соединения не переиспользовались;
  * протухание сессии в одном месте не было видно другому.

Теперь клиент один, живёт в общем event loop и выдаётся под блокировкой на
пользователя: два одновременных запроса (проверка оценок из фона и открытие
дневника в PWA) не начнут вход дважды.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..db.repositories import SessionRepository
from ..domain.models import LoginType, User
from ..settings import NetSchoolSettings
from .errors import NetSchoolError, Reason, classify, wrap

logger = logging.getLogger("netschoolbot.netschool")


@dataclass(slots=True)
class _Entry:
    client: Any
    last_used: dt.datetime
    lock: asyncio.Lock
    # Сессию Госуслуг без человека не восстановить, поэтому её клиент не
    # закрываем за простой: пока он жив, keep-alive netschoolpy продлевает
    # сессию на сервере школы.
    keep_alive: bool = False


class SessionPool:
    """Хранит живые клиенты netschoolpy и умеет их восстанавливать."""

    def __init__(
        self,
        settings: NetSchoolSettings,
        sessions: SessionRepository,
        *,
        client_factory: Callable[[str, str | None], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._entries: dict[int, _Entry] = {}
        # Блокировка на сам словарь: создание записи должно быть атомарным,
        # иначе два одновременных запроса заведут два клиента.
        self._registry_lock = asyncio.Lock()
        self._client_factory = client_factory or _default_client_factory

    @asynccontextmanager
    async def acquire(self, user: User) -> AsyncIterator[Any]:
        """Выдать вошедший клиент для пользователя.

        Блокировка держится на всё время работы с клиентом: netschoolpy хранит
        состояние (выбранный ученик, куки) в самом объекте, поэтому делить его
        между одновременными запросами нельзя.
        """
        entry = await self._entry_for(user.telegram_id)
        async with entry.lock:
            client = await self._ensure_logged_in(user, entry)
            entry.last_used = dt.datetime.now()
            yield client

    async def invalidate(self, telegram_id: int, *, forget_saved: bool = False) -> None:
        """Закрыть сессию пользователя. `forget_saved` — стереть и сохранённую."""
        async with self._registry_lock:
            entry = self._entries.pop(telegram_id, None)
        if entry is not None:
            await _close_client(entry.client)
        if forget_saved:
            await self._sessions.drop(telegram_id)

    async def close_all(self) -> None:
        async with self._registry_lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            await _close_client(entry.client)

    async def evict_idle(self) -> int:
        """Закрыть сессии, которыми давно не пользовались.

        Школьные серверы ограничивают число одновременных сессий, а держать
        клиент ради пользователя, заходившего час назад, незачем.
        """
        deadline = dt.datetime.now() - dt.timedelta(seconds=self._settings.session_ttl)
        async with self._registry_lock:
            stale = [
                uid
                for uid, e in self._entries.items()
                if e.last_used < deadline and not e.keep_alive
            ]
            entries = [self._entries.pop(uid) for uid in stale]
        for entry in entries:
            await _close_client(entry.client)
        if stale:
            logger.debug("Закрыто простаивающих сессий: %s", len(stale))
        return len(stale)

    async def _entry_for(self, telegram_id: int) -> _Entry:
        async with self._registry_lock:
            entry = self._entries.get(telegram_id)
            if entry is None:
                entry = _Entry(client=None, last_used=dt.datetime.now(), lock=asyncio.Lock())
                self._entries[telegram_id] = entry
            return entry

    async def _ensure_logged_in(self, user: User, entry: _Entry) -> Any:
        entry.keep_alive = user.credentials.login_type.is_gosuslugi
        if entry.client is not None and await self._is_alive(entry.client):
            return entry.client

        if entry.client is not None:
            await _close_client(entry.client)
            entry.client = None

        client = self._client_factory(user.school.url, self._settings.proxy_for(user.school.url))

        if await self._restore(user.telegram_id, client):
            if await self._is_alive(client):
                _apply_student(client, user)
                entry.client = client
                return client
            # Сохранённая сессия протухла — она больше не пригодится.
            await self._sessions.drop(user.telegram_id)

        await self._login(user, client)
        _apply_student(client, user)
        await self._persist(user.telegram_id, client)
        entry.client = client
        return client

    async def _restore(self, telegram_id: int, client: Any) -> bool:
        payload = await self._sessions.load(telegram_id)
        if not payload:
            return False
        try:
            await client.import_session(payload)
            return True
        except Exception as exc:
            # netschoolpy заворачивает в SessionExpired любую ошибку проверки,
            # включая таймаут. Судим по тексту: из-за сетевого сбоя сессию не
            # выбрасываем — для Госуслуг это означало бы новый вход с кодом.
            # Префикс «Сессия истекла или невалидна:» сам похож на ошибку
            # входа, поэтому классифицируем исходную причину после него.
            cause = Exception(str(exc).split(": ", 1)[-1])
            reason = classify(cause)
            if reason.is_retryable or reason is Reason.ESIA:
                raise wrap(cause, context=f"восстановление {telegram_id}") from exc
            logger.info("Сохранённая сессия %s не восстановилась: %s", telegram_id, exc)
            await self._sessions.drop(telegram_id)
            return False

    async def _persist(self, telegram_id: int, client: Any) -> None:
        try:
            payload = client.export_session()
        except Exception as exc:
            logger.debug("Не удалось выгрузить сессию %s: %s", telegram_id, exc)
            return
        if payload:
            await self._sessions.save(telegram_id, payload)

    async def _login(self, user: User, client: Any) -> None:
        if user.credentials.login_type.is_gosuslugi:
            # Вход через Госуслуги нельзя повторить без участия человека:
            # он требует кода или подтверждения в приложении.
            raise NetSchoolError(
                Reason.AUTH,
                "Сессия Госуслуг истекла. Войдите заново: /login",
            )
        if not user.credentials.usable:
            raise NetSchoolError(Reason.AUTH, "Логин или пароль не сохранены. Войдите: /login")
        try:
            await client.login(
                user.credentials.login,
                user.credentials.password,
                user.school.name,
            )
        except Exception as exc:
            raise wrap(exc, context=f"вход {user.telegram_id}") from exc

    async def _is_alive(self, client: Any) -> bool:
        """Проверить, что сессия ещё принимается сервером.

        Недоступность сервера не означает протухшую сессию — в этом случае
        считаем её живой, чтобы не устраивать бессмысленный повторный вход.
        """
        try:
            response = await client._authed_get("student/diary/init")
        except Exception as exc:
            return not wrap(exc).reason.needs_relogin
        return getattr(response, "status_code", 200) not in (401, 403)


def _default_client_factory(url: str, proxy: str | None) -> Any:
    from netschoolpy import NetSchool

    return NetSchool(url, proxy=proxy) if proxy else NetSchool(url)


def _apply_student(client: Any, user: User) -> None:
    """Переключить клиент на выбранного ребёнка."""
    if user.selected_student_id is not None:
        client._student_id = int(user.selected_student_id)


async def _close_client(client: Any) -> None:
    """Закрыть клиент, не роняя вызывающий код.

    Здесь `except Exception` уместен: мы уже отпускаем ресурс, и ошибка
    закрытия не должна маскировать причину, по которой мы сюда попали.
    """
    if client is None:
        return
    try:
        http = getattr(client, "_http", None)
        if http is not None and hasattr(http, "aclose"):
            await http.aclose()
        elif hasattr(client, "close"):
            result = client.close()
            if asyncio.iscoroutine(result):
                await result
    except Exception as exc:
        logger.debug("Ошибка при закрытии клиента «Сетевого города»: %s", exc)
