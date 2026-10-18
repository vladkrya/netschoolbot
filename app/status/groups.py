"""Сообщения о «Сетевом городе» в группы и каналы Telegram.

Админ подключает группу по её id (/addgroup) и выбирает, о чём ей писать:
простои, обновления версии или то и другое, — и по каким регионам (пусто —
по всем). Источник — тот же круг проверок всех серверов, что у страницы
статуса (StatusService.probe_all), а не проверки оценок пользователей.

* Лёг — DOWN_AFTER неудачных проверок подряд (~10 мин) у сервера, который
  раньше отвечал боту. Ожил — UP_AFTER удачных подряд после этого.
* Если в одном круге не ответила большая часть серверов, дело, скорее
  всего, в нашей сети — такой круг не считаем, иначе группы засыпало бы
  сообщениями обо всех регионах сразу.
* Обновился — версия в /webapi/logindata сменилась.

Всё, что случилось за круг, уходит в группу одним сообщением.
"""

from __future__ import annotations

import html
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db.repositories import BroadcastGroupRepository, SgoStatusRepository
    from .service import Probe, Target

logger = logging.getLogger("netschoolbot.status")

KINDS = {"outages": "Недоступность", "updates": "Обновления версии"}
DOWN_AFTER = 2
UP_AFTER = 2
# Не ответило больше этой доли серверов (и не меньше MASS_MIN) — сбой у нас.
MASS_SHARE = 0.5
MASS_MIN = 5

# (id чата, текст) — бот шлёт с parse_mode=HTML.
Send = Callable[[int, str], Awaitable[None]]


@dataclass
class _Host:
    fails: int = 0
    oks: int = 0
    first_fail: float | None = None
    first_ok: float | None = None
    down_since: float | None = None


@dataclass(frozen=True, slots=True)
class Event:
    host: str
    kind: str  # outages | updates
    line: str


class GroupBroadcast:
    def __init__(self, repo: BroadcastGroupRepository, send: Send | None = None, *,
                 status_url: str = "", clock: Callable[[], float] = time.time) -> None:
        self._repo = repo
        self._send = send
        self._status_url = status_url
        self._clock = clock
        self._hosts: dict[str, _Host] = {}
        # Серверы, которые хоть раз ответили: о прочих «не отвечает» — неправда,
        # с нашего сервера они не открываются вовсе.
        self._reachable: set[str] = set()

    def attach(self, send: Send) -> None:
        self._send = send

    async def load(self, status: SgoStatusRepository) -> None:
        self._reachable = await status.reachable_hosts()
        for host, since in (await self._repo.down_hosts()).items():
            self._hosts.setdefault(host, _Host()).down_since = since

    async def observe(self, results: list[tuple[Target, Probe]], versions: dict[str, tuple[str, str]],
                      at: float) -> None:
        """Итог круга проверок; versions — host → (прежняя, новая) у сменивших версию."""
        events = [
            Event(target.host, "updates",
                  f"🆕 <b>{html.escape(target.region or target.host)}</b>: "
                  f"{html.escape(versions[target.host][0])} → {html.escape(versions[target.host][1])}")
            for target, _ in results if target.host in versions
        ]
        events += await self._outages(results, at)
        if events:
            await self.publish(events)

    async def _outages(self, results: list[tuple[Target, Probe]], at: float) -> list[Event]:
        for target, probe in results:
            if probe.ok:
                self._reachable.add(target.host)
        watched = [(t, p) for t, p in results if t.host in self._reachable]
        failed = sum(1 for _, probe in watched if not probe.ok)
        if failed >= MASS_MIN and failed > MASS_SHARE * len(watched):
            logger.info("Не ответили %s из %s серверов — похоже, сбой у нас; группам не пишем", failed, len(watched))
            return []
        events = []
        for target, probe in watched:
            state = self._hosts.setdefault(target.host, _Host())
            region = html.escape(target.region or target.host)
            if probe.ok:
                state.fails, state.first_fail = 0, None
                state.oks += 1
                if state.first_ok is None:
                    state.first_ok = at
                if state.down_since is not None and state.oks >= UP_AFTER:
                    # Простой — до первой удачной проверки, а не до второй.
                    minutes = max(1, round((state.first_ok - state.down_since) / 60))
                    state.down_since = None
                    await self._repo.set_down(target.host, None)
                    events.append(Event(target.host, "outages",
                                        f"🟢 <b>{region}</b> снова работает (не отвечал ~{_duration(minutes)})"))
                continue
            state.oks, state.first_ok = 0, None
            state.fails += 1
            if state.first_fail is None:
                state.first_fail = at
            if state.down_since is None and state.fails >= DOWN_AFTER:
                state.down_since = state.first_fail
                await self._repo.set_down(target.host, state.down_since)
                reason = f" — {html.escape(probe.error)}" if probe.error else ""
                events.append(Event(target.host, "outages", f"🔴 <b>{region}</b> не отвечает{reason}"))
        return events

    async def publish(self, events: list[Event]) -> int:
        """Разослать события подходящим группам. Возвращает, скольким ушло."""
        if self._send is None:
            return 0
        sent = 0
        for group in await self._repo.all():
            lines = [event.line for event in events if wants(group, event.kind, event.host)]
            if not lines:
                continue
            if await self._deliver(group["chat_id"], self._text(lines)):
                sent += 1
        return sent

    def _text(self, lines: list[str]) -> str:
        text = "«Сетевой город»\n" + "\n".join(lines)
        if self._status_url:
            text += f'\n\n<a href="{html.escape(self._status_url)}">Статус всех регионов</a>'
        return text

    async def _deliver(self, chat_id: int, text: str) -> bool:
        from aiogram.exceptions import TelegramMigrateToChat

        try:
            await self._send(chat_id, text)
        except TelegramMigrateToChat as exc:
            # Группа стала супергруппой: запоминаем новый id и шлём туда.
            await self._repo.move(chat_id, exc.migrate_to_chat_id)
            return await self._deliver(exc.migrate_to_chat_id, text)
        except Exception as exc:  # noqa: BLE001 — одна группа не мешает остальным
            logger.warning("Сообщение о «Сетевом городе» в группу %s не ушло: %s", chat_id, exc)
            return False
        return True


def wants(group: dict, kind: str, host: str) -> bool:
    return bool(group.get(kind)) and (not group["regions"] or host in group["regions"])


def _duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} мин"
    hours, rest = divmod(minutes, 60)
    return f"{hours} ч {rest} мин" if rest else f"{hours} ч"
