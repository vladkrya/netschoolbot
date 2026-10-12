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
from dataclasses import dataclass, replace
from typing import Any

from ..db.repositories import SessionRepository
from ..domain.models import ApiMode, LoginType, User
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
        mobile_factory: Callable[[str, str | None, str], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._entries: dict[int, _Entry] = {}
        # Блокировка на сам словарь: создание записи должно быть атомарным,
        # иначе два одновременных запроса заведут два клиента.
        self._registry_lock = asyncio.Lock()
        self._client_factory = client_factory or _default_client_factory
        self._mobile_factory = mobile_factory or _default_mobile_factory

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
            try:
                yield client
            finally:
                await self._persist(user.telegram_id, client)

    async def invalidate(self, telegram_id: int, *, forget_saved: bool = False) -> None:
        """Закрыть сессию пользователя. `forget_saved` — стереть и сохранённую."""
        async with self._registry_lock:
            entry = self._entries.pop(telegram_id, None)
        if entry is not None:
            async with entry.lock:
                await _close_client(entry.client)
        if forget_saved:
            await self._sessions.drop(telegram_id)

    async def close_all(self) -> None:
        async with self._registry_lock:
            entries = list(self._entries.items())
            self._entries.clear()
        for telegram_id, entry in entries:
            async with entry.lock:
                if entry.client is not None:
                    await self._persist(telegram_id, entry.client)
                    await _close_client(entry.client)

    async def checkpoint_all(self) -> None:
        """Сохранить текущие cookies живых клиентов на случай внезапного рестарта."""
        async with self._registry_lock:
            entries = list(self._entries.items())
        for telegram_id, entry in entries:
            async with entry.lock:
                if entry.client is not None:
                    await self._persist(telegram_id, entry.client)

    async def has_client(self, telegram_id: int) -> bool:
        async with self._registry_lock:
            entry = self._entries.get(telegram_id)
            return entry is not None and entry.client is not None

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
            async with entry.lock:
                await _close_client(entry.client)
        if stale:
            logger.debug("Закрыто простаивающих сессий: %s", len(stale))
        return len(stale)

    @property
    def mobile_available(self) -> bool:
        return bool(self._settings.mobile_client_secret)

    async def switch_api(self, user: User, mode: ApiMode) -> None:
        """Перевести сессию пользователя на другое API.

        В мобильное — из живой веб-сессии: сервер выдаёт одноразовый код, он
        меняется на 60-дневный токен. Так переключаются и пользователи
        Госуслуг, у которых нет пароля. Если код получить не удалось, а
        пароль сохранён, входим в мобильное API по паролю.

        Обратно в веб — просто забываем токен: следующий запрос войдёт
        заново по паролю (пользователям Госуслуг понадобится /login).
        """
        entry = await self._entry_for(user.telegram_id)
        async with entry.lock:
            if mode is ApiMode.MOBILE:
                if not self.mobile_available:
                    raise NetSchoolError(Reason.UNKNOWN, "Мобильное API не настроено на сервере бота")
                web_user = replace(user, api_mode=ApiMode.WEB)
                web = await self._ensure_logged_in(web_user, entry)
                mobile = await self._mobile_from_web(web_user, web)
                await _close_client(web)
                entry.client = mobile
                entry.keep_alive = False
                _apply_student(mobile, user)
                await self._persist(user.telegram_id, mobile)
            else:
                client, entry.client = entry.client, None
                await _close_client(client)
                await self._sessions.drop(user.telegram_id)

    async def _mobile_from_web(self, user: User, web: Any) -> Any:
        secret = self._settings.mobile_client_secret
        try:
            from netschoolpy import MobileNetSchool

            return await MobileNetSchool.from_web(web, client_secret=secret)
        except Exception as exc:
            if not user.credentials.can_relogin:
                raise wrap(exc, context=f"мобильный токен {user.telegram_id}") from exc
            logger.info("Код из веб-сессии %s не получен (%s), вход по паролю", user.telegram_id, exc)
        client = self._new_client(user, ApiMode.MOBILE)
        await self._login(replace(user, api_mode=ApiMode.MOBILE), client)
        return client

    def _new_client(self, user: User, mode: ApiMode) -> Any:
        proxy = self._settings.proxy_for(user.school.url)
        if mode is ApiMode.MOBILE:
            return self._mobile_factory(user.school.url, proxy, self._settings.mobile_client_secret)
        return self._client_factory(user.school.url, proxy)

    async def _entry_for(self, telegram_id: int) -> _Entry:
        async with self._registry_lock:
            entry = self._entries.get(telegram_id)
            if entry is None:
                entry = _Entry(client=None, last_used=dt.datetime.now(), lock=asyncio.Lock())
                self._entries[telegram_id] = entry
            return entry

    async def _ensure_logged_in(self, user: User, entry: _Entry) -> Any:
        entry.keep_alive = (
            user.credentials.login_type.is_gosuslugi and user.effective_api_mode is ApiMode.WEB
        )
        if entry.client is not None and _client_mode(entry.client) is not user.effective_api_mode:
            # Режим сменили, а в пуле остался клиент другого API.
            await _close_client(entry.client)
            entry.client = None
        if entry.client is not None and await self._is_alive(entry.client):
            return entry.client

        if entry.client is not None:
            await _close_client(entry.client)
            entry.client = None

        client = self._new_client(user, user.effective_api_mode)

        try:
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
        except BaseException:
            await _close_client(client)
            raise

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
            if reason is not Reason.AUTH:
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
            try:
                await self._sessions.save(telegram_id, payload)
            except Exception:
                logger.exception("Не удалось сохранить сессию %s", telegram_id)

    async def _login(self, user: User, client: Any) -> None:
        if user.credentials.login_type.is_gosuslugi:
            # Вход через Госуслуги нельзя повторить без участия человека:
            # он требует кода или подтверждения в приложении.
            if user.effective_api_mode is ApiMode.MOBILE:
                raise NetSchoolError(
                    Reason.AUTH,
                    "Токен мобильного API истёк. Войдите заново: /login",
                )
            raise NetSchoolError(
                Reason.AUTH,
                "Сессия Госуслуг истекла. Войдите заново: /login",
            )
        if not user.credentials.can_relogin:
            raise NetSchoolError(Reason.AUTH, "Не удалось восстановить вход в «Сетевой город». Для повторного входа используйте /login")
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
        check = getattr(client, "is_alive", None)
        if check is not None:
            try:
                return bool(await check())
            except Exception as exc:
                return not wrap(exc).reason.needs_relogin
        try:
            response = await client._authed_get("student/diary/init")
        except Exception as exc:
            return not wrap(exc).reason.needs_relogin
        return getattr(response, "status_code", 200) not in (401, 403)


def _default_client_factory(url: str, proxy: str | None) -> Any:
    from netschoolpy import NetSchool

    return NetSchool(url, proxy=proxy) if proxy else NetSchool(url)


def _client_mode(client: Any) -> ApiMode:
    # У мобильного клиента есть маршруты, которых нет в веб-API.
    return ApiMode.MOBILE if hasattr(client, "classmeetings") else ApiMode.WEB


def _default_mobile_factory(url: str, proxy: str | None, client_secret: str) -> Any:
    from netschoolpy import MobileNetSchool

    return MobileNetSchool(url, proxy=proxy, client_secret=client_secret or None)


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
