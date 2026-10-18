"""Подписки из приложения на сервер «Сетевого города» своей школы.

* «Сообщить, когда заработает» (kind='up') — разовая: человек открыл
  приложение, а сервер школы лежит. Как только он ответит — сообщение в
  Telegram и push на все устройства, подписка снимается.
* «Об обновлениях» (kind='updates') — постоянная: на сервере вышла новая
  версия «Сетевого города».

«Заработал» — только когда видно, что дневник снова открывается:

* фоновая проверка оценок кого-то из пользователей этого сервера прошла
  (OutageMonitor сообщает сюда итог каждой проверки), или
* страница входа (`/webapi/logindata` тем же путём, каким бот ходит за
  дневником) после подписки не отвечала, а потом ответила.

Просто «страница входа отвечает» — мало: бывает, что она открывается, а
дневник — нет, и человек получил бы «заработал», когда ничего не работает.
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
# Версия сервера школы маршрутом бота (sgo_version.logindata_fetcher).
Fetch = Callable[[str], Awaitable[str]]
FETCH_TIMEOUT_S = 20


class SgoAlerts:
    def __init__(self, status: StatusService, repo: SgoStatusRepository, deliver: Deliver | None = None,
                 fetch: Fetch | None = None, clock: Callable[[], float] = time.time) -> None:
        self._status = status
        self._repo = repo
        self._deliver = deliver
        self._fetch = fetch
        self._clock = clock
        # Когда проверка оценок на сервере в последний раз прошла.
        self._diary_ok: dict[str, float] = {}
        # Подписки «когда заработает», при которых страница входа уже не отвечала.
        self._saw_down: set[tuple[int, str]] = set()

    def attach(self, deliver: Deliver) -> None:
        self._deliver = deliver

    async def info(self, telegram_id: int, url: str) -> dict:
        """Состояние сервера школы и подписки человека на него."""
        data = dict(await self._status.live_state(url))
        kinds = await self._repo.alert_kinds(telegram_id, host_of(url))
        data["subscribed"] = {kind: kind in kinds for kind in KINDS}
        return data

    async def record_check(self, school_url: str, ok: bool) -> None:
        """Итог фоновой проверки оценок (OutageMonitor): дневник открылся или нет."""
        if ok:
            self._diary_ok[host_of(school_url)] = self._clock()

    async def subscribe(self, telegram_id: int, url: str, kind: str, on: bool) -> dict:
        """Включить или снять подписку. Возвращает то же, что info."""
        if kind not in KINDS:
            raise ValueError(kind)
        host = host_of(url)
        if not on:
            await self._repo.alert_unsubscribe(telegram_id, host, kind)
            return await self.info(telegram_id, url)
        # «Когда заработает» принимаем всегда: человек просит после того,
        # как дневник не загрузился, — значит, для него сервер не работает.
        ok, version = await self._probe(url)
        if kind == "up" and not ok:
            self._saw_down.add((telegram_id, host))
        await self._repo.alert_subscribe(telegram_id, host, url, kind, version, self._clock())
        return await self.info(telegram_id, url)

    async def _probe(self, url: str) -> tuple[bool, str]:
        """Отвечает ли сервер боту и какая версия. Без своего клиента (тесты,
        страница без бота) — по странице статуса."""
        if self._fetch is None:
            state = await self._status.live_state(url)
            return bool(state.get("ok")), state.get("version", "")
        try:
            version = (await asyncio.wait_for(self._fetch(url), FETCH_TIMEOUT_S) or "").strip()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — не ответил, это и есть ответ
            return False, ""
        return True, version

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
            ok, version = await self._probe(rows[0]["url"])
            region = html.escape(self._status.region(host) or host)
            for row in rows:
                key = (row["telegram_id"], host)
                if row["kind"] == "up" and not ok:
                    self._saw_down.add(key)
                if row["kind"] == "up" and (
                    self._diary_ok.get(host, 0) > row["created_at"] or (ok and key in self._saw_down)
                ):
                    self._saw_down.discard(key)
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
