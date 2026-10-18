"""Подписки из приложения на сервер «Сетевого города» своей школы.

* «Сообщить, когда заработает» (kind='up') — разовая: человек открыл
  приложение, а сервер школы лежит. Как только он ответит — сообщение в
  Telegram и push на все устройства, подписка снимается.
* «Об обновлениях» (kind='updates') — постоянная: на сервере вышла новая
  версия «Сетевого города».

Состояние сервера берётся у страницы статуса (StatusService): из её круга
проверок, а для серверов вне её списка — своей проверкой того же клиента.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .service import host_of

if TYPE_CHECKING:
    from ..db.repositories import SgoStatusRepository
    from .service import StatusService

logger = logging.getLogger("netschoolbot.status")

KINDS = ("up", "updates")
CHECK_EVERY_S = 2 * 60
# Не дождались за неделю — подписка «когда заработает» больше не нужна.
UP_TTL_S = 7 * 86400

# (кому, текст для Telegram, заголовок push, строка push)
Deliver = Callable[[int, str, str, str], Awaitable[None]]


class SgoAlerts:
    def __init__(self, status: StatusService, repo: SgoStatusRepository, deliver: Deliver | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._status = status
        self._repo = repo
        self._deliver = deliver
        self._clock = clock

    def attach(self, deliver: Deliver) -> None:
        self._deliver = deliver

    async def info(self, telegram_id: int, url: str) -> dict:
        """Состояние сервера школы и подписки человека на него."""
        data = dict(await self._status.live_state(url))
        kinds = await self._repo.alert_kinds(telegram_id, host_of(url))
        data["subscribed"] = {kind: kind in kinds for kind in KINDS}
        return data

    async def subscribe(self, telegram_id: int, url: str, kind: str, on: bool) -> dict:
        """Включить или снять подписку. Возвращает то же, что info, плюс
        already_up — сервер уже отвечает, ждать нечего."""
        if kind not in KINDS:
            raise ValueError(kind)
        host = host_of(url)
        if not on:
            await self._repo.alert_unsubscribe(telegram_id, host, kind)
            return await self.info(telegram_id, url)
        state = await self._status.live_state(url)
        if kind == "up" and state.get("ok"):
            data = await self.info(telegram_id, url)
            data["already_up"] = True
            return data
        await self._repo.alert_subscribe(telegram_id, host, url, kind, state.get("version", ""), self._clock())
        return await self.info(telegram_id, url)

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — подписки не должны ронять бота
                logger.warning("Проверка подписок на «Сетевой город» не удалась: %s", exc)
            await asyncio.sleep(CHECK_EVERY_S)

    async def check(self) -> None:
        """Один проход: кому пора сообщить."""
        now = self._clock()
        await self._repo.alerts_expire("up", now - UP_TTL_S)
        by_host: dict[str, list[dict]] = defaultdict(list)
        for row in await self._repo.alerts():
            by_host[row["host"]].append(row)
        for host, rows in by_host.items():
            state = await self._status.live_state(rows[0]["url"])
            region = html.escape(state.get("region") or host)
            version = state.get("version", "")
            for row in rows:
                if row["kind"] == "up" and state.get("ok"):
                    await self._send(
                        row["telegram_id"],
                        f"✅ Сервер «Сетевого города» ({region}) снова работает — можно заходить в дневник.",
                        "«Сетевой город» снова работает", f"{region}: сервер отвечает",
                    )
                    await self._repo.alert_unsubscribe(row["telegram_id"], host, "up")
                elif row["kind"] == "updates" and version and version != row["version"]:
                    if row["version"]:
                        await self._send(
                            row["telegram_id"],
                            f"🆕 «Сетевой город» ({region}) обновился: {html.escape(row['version'])} → {html.escape(version)}.\n"
                            "Если что-то стало работать иначе — дело, скорее всего, в этом.",
                            "«Сетевой город» обновился", f"{region}: версия {version}",
                        )
                    await self._repo.alert_seen_version(row["telegram_id"], host, version)

    async def _send(self, telegram_id: int, text: str, title: str, body: str) -> None:
        if self._deliver is None:
            return
        try:
            await self._deliver(telegram_id, text, title, body)
        except Exception as exc:  # noqa: BLE001 — один получатель не мешает остальным
            logger.info("Уведомление о «Сетевом городе» для %s не ушло: %s", telegram_id, exc)
