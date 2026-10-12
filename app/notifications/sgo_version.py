"""Версия «Сетевого города» на каждом сервере школ.

После простоя сервер часто возвращается уже обновлённым, и новые ошибки
в логах («поле пропало», «вход сломался») надо сопоставлять с версией.
Раньше её никто не записывал. Версию отдаёт `/webapi/logindata` без входа;
она хранится по хосту в БД, смена пишется в лог и сообщается админу.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import AppContext
    from ..db.repositories import SgoVersionRepository

logger = logging.getLogger("netschoolbot.sgo")

# Адрес сервера и версия — не личные данные: в логе их видно целиком.
PUBLIC = {"public": True}

# Как часто сверять версию у работающего сервера. После простоя — сразу.
CHECK_EVERY_S = 6 * 3600
# logindata — маленький ответ без входа; дольше ждать нет смысла.
FETCH_TIMEOUT_S = 15

Fetch = Callable[[str], Awaitable[str]]
Notify = Callable[[str], Awaitable[None]]


class VersionTracker:
    def __init__(self, repo: SgoVersionRepository, fetch: Fetch,
                 clock: Callable[[], float] = time.time) -> None:
        self._repo = repo
        self._fetch = fetch
        self._clock = clock
        self._checked: dict[str, float] = {}
        self._known: dict[str, str] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    async def load(self) -> None:
        """Версии из БД — чтобы и сразу после перезапуска сообщение о простое
        называло версию, с которой сервер упал."""
        for row in await self._repo.all():
            self._known[row["host"]] = row["version"]

    def known(self, host: str) -> str:
        """Последняя версия, которую видели в этом процессе ('' — не знаем)."""
        return self._known.get(host, "")

    def due(self, host: str) -> bool:
        last = self._checked.get(host)
        return last is None or self._clock() - last >= CHECK_EVERY_S

    def check_later(self, host: str, school_url: str, notify: Notify) -> None:
        """Сверить версию в фоне, если пора: проверку оценок не задерживаем."""
        if not self.due(host) or host in self._tasks:
            return
        task = asyncio.create_task(self.check(host, school_url, notify), name=f"sgo-version-{host}")
        self._tasks[host] = task
        task.add_done_callback(lambda _: self._tasks.pop(host, None))

    async def check(self, host: str, school_url: str, notify: Notify | None = None) -> tuple[str, str]:
        """Узнать версию и записать её. Возвращает (прежняя, текущая);
        ('', '') — узнать не вышло, прежняя '' — сервер видим впервые.
        О смене версии сообщает в notify, если он задан."""
        self._checked[host] = self._clock()
        try:
            version = (await asyncio.wait_for(self._fetch(school_url), FETCH_TIMEOUT_S) or "").strip()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — версия вторична, проверку не роняем
            logger.info("Версию «Сетевого города» %s узнать не удалось: %s", host, type(exc).__name__, extra=PUBLIC)
            return "", ""
        if not version:
            return "", ""
        try:
            previous = await self._repo.save(host, version, self._clock())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Версия «Сетевого города» %s не записана: %s", host, exc)
            previous = self._known.get(host, "")
        self._known[host] = version
        if previous == version:
            logger.debug("«Сетевой город» %s: версия %s", host, version, extra=PUBLIC)
        elif not previous:
            logger.info("«Сетевой город» %s: версия %s", host, version, extra=PUBLIC)
        else:
            logger.warning("«Сетевой город» %s сменил версию: %s → %s", host, previous, version, extra=PUBLIC)
            if notify is not None:
                await notify(f"⚠️ «Сетевой город» {host} обновился: {previous} → {version}")
        return previous, version


def logindata_fetcher(app: AppContext) -> Fetch:
    """Версия сервера школы из /webapi/logindata — без входа, тем же клиентом,
    что и вход (прокси, сертификаты Минцифры)."""
    from ..netschool.login import _client, _close

    async def fetch(school_url: str) -> str:
        client = _client(app, school_url)
        try:
            methods = await client.login_methods(timeout=FETCH_TIMEOUT_S)
        finally:
            await _close(client)
        return methods.version

    return fetch
