"""Сообщение админу, когда «Сетевой город» школы перестал отвечать — и когда ожил.

Отдельные ошибки уходят админу и так (logging_setup), а недоступность
школьного сервера — предупреждения, по одному на пользователя: их не видно,
пока кто-нибудь не пожалуется. Здесь проверки оценок складываются по адресу
сервера: если за окно почти все они упёрлись в недоступность — это простой.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from ..db.engine import Database
    from .sgo_version import VersionTracker

logger = logging.getLogger("netschoolbot.outage")

WINDOW_S = 10 * 60
# Простой: столько проверок за окно, и почти все — «сервер недоступен».
MIN_CHECKS = 5
FAIL_SHARE = 0.8
# Ожил: столько удачных проверок подряд.
RECOVERED_AFTER = 3
# Лежащие серверы помним в app_metadata: раньше после каждого перезапуска
# бот забывал, что о простое уже сказал, и присылал «не отвечает» заново.
DOWN_KEY = "outage_down:"


@dataclass
class _Host:
    checks: deque[tuple[float, bool]] = field(default_factory=deque)
    down_since: float | None = None
    ok_streak: int = 0


class OutageMonitor:
    def __init__(self, send: Callable[[str], Awaitable[None]] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._send = send
        self._clock = clock
        self._hosts: dict[str, _Host] = {}
        self._versions: VersionTracker | None = None
        self._db: Database | None = None
        self.check_observer: Callable[[str, bool], Awaitable[None]] | None = None

    async def persist(self, db: Database) -> None:
        """Хранить «лежит с …» в базе и поднять то, что было до перезапуска."""
        self._db = db
        rows = await db.fetch_all(
            "SELECT key, value FROM app_metadata WHERE key LIKE ?", (DOWN_KEY + "%",))
        for row in rows:
            try:
                since = float(row["value"])
            except ValueError:
                continue
            self._hosts.setdefault(row["key"][len(DOWN_KEY):], _Host()).down_since = since

    async def _remember(self, host: str, since: float | None) -> None:
        if self._db is None:
            return
        try:
            if since is None:
                await self._db.execute("DELETE FROM app_metadata WHERE key = ?", (DOWN_KEY + host,))
            else:
                await self._db.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
                                       (DOWN_KEY + host, repr(since)))
        except Exception as exc:  # noqa: BLE001 — без записи просто повторим сообщение
            logger.warning("Не удалось запомнить простой %s: %s", host, exc)

    def attach(self, send: Callable[[str], Awaitable[None]]) -> None:
        self._send = send

    def track_versions(self, versions: VersionTracker) -> None:
        """Сверять версию «Сетевого города»: раз в несколько часов и сразу
        после простоя — сервер часто возвращается уже обновлённым."""
        self._versions = versions

    async def record(self, school_url: str, *, ok: bool, server_down: bool = False) -> None:
        """Итог проверки. Ошибки не про сервер (вход, неизвестные) не считаем."""
        if not ok and not server_down:
            return
        if self.check_observer is not None:
            try:
                await self.check_observer(school_url, ok)
            except Exception as exc:  # noqa: BLE001 — статус не должен ломать проверку дневника
                logger.warning("Не удалось учесть проверку в статусе: %s", type(exc).__name__)
        host = urlparse(school_url).hostname or school_url
        state = self._hosts.setdefault(host, _Host())
        now = self._clock()
        state.checks.append((now, ok))
        while state.checks and now - state.checks[0][0] > WINDOW_S:
            state.checks.popleft()

        if ok:
            state.ok_streak += 1
            if state.down_since is not None and state.ok_streak >= RECOVERED_AFTER:
                minutes = max(1, round((now - state.down_since) / 60))
                state.down_since = None
                await self._remember(host, None)
                text = f"✅ «Сетевой город» {host} снова отвечает (простой ~{minutes} мин)"
                if self._versions is not None:
                    previous, version = await self._versions.check(host, school_url)
                    if previous and version and previous != version:
                        text += f", версия обновилась: {previous} → {version}"
                    elif version:
                        text += f", версия {version}"
                logger.warning(text)
                await self._notify(text)
            elif self._versions is not None:
                self._versions.check_later(host, school_url, self._notify)
            return
        state.ok_streak = 0
        failed = sum(1 for _, good in state.checks if not good)
        total = len(state.checks)
        if state.down_since is None and total >= MIN_CHECKS and failed / total >= FAIL_SHARE:
            state.down_since = now
            await self._remember(host, now)
            version = self._versions.known(host) if self._versions is not None else ""
            text = (f"⚠️ «Сетевой город» {host} не отвечает: {failed} из {total} проверок "
                    f"за {WINDOW_S // 60} мин" + (f" (версия до простоя {version})" if version else "")
                    + ". Сообщу, когда оживёт.")
            logger.warning(text)
            await self._notify(text)

    async def _notify(self, text: str) -> None:
        if self._send is None:
            return
        try:
            await self._send(text)
        except Exception as exc:  # noqa: BLE001 — сообщение админу не должно ронять проверку
            logger.warning("Не удалось сообщить админу о простое: %s", exc)
