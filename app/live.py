"""Живые соединения приложения (WebSocket): кто на связи.

По сокету идут только сигналы — новые оценки, изменения с других устройств
и завершение сеанса; сами данные клиент забирает обычным запросом. При обрыве
связи ничего не теряется: следующий запрос заберёт всё пропущенное.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("netschoolbot.live")


@dataclass(eq=False)
class Listener:
    telegram_id: int
    socket: Any = None
    # Вкладка приложения (X-Netschool-Tab): своё же изменение ей не шлём.
    tab: str = ""
    # Отпечаток токена устройства; пусто — Telegram, его не завершить.
    device: str = ""


class LiveHub:
    def __init__(self) -> None:
        self._listeners: set[Listener] = set()

    def add(self, listener: Listener) -> None:
        self._listeners.add(listener)

    def remove(self, listener: Listener) -> None:
        self._listeners.discard(listener)

    async def disconnect(self, telegram_id: int) -> None:
        for listener in list(self._listeners):
            if listener.telegram_id == telegram_id:
                self.remove(listener)
                await listener.socket.close()

    async def close(self) -> None:
        for listener in list(self._listeners):
            self.remove(listener)
            await listener.socket.close()

    def online(self, telegram_id: int) -> bool:
        return any(item.telegram_id == telegram_id for item in self._listeners)

    async def publish_user(self, telegram_id: int, event: dict[str, Any], *, skip_tab: str = "") -> None:
        for listener in list(self._listeners):
            if listener.telegram_id == telegram_id and not (skip_tab and listener.tab == skip_tab):
                try:
                    await listener.socket.send_json(event)
                except Exception as exc:  # noqa: BLE001 — оборванное соединение уберёт его обработчик
                    logger.debug("Сокет %s не принял событие: %s", listener.telegram_id, exc)

    async def sign_out_devices(self, telegram_id: int, alive: set[str]) -> None:
        """Сеанс завершили на другом устройстве — открытое приложение там
        узнаёт об этом сразу, а не со следующим запросом."""
        for listener in list(self._listeners):
            if listener.telegram_id != telegram_id or not listener.device or listener.device in alive:
                continue
            self.remove(listener)
            try:
                await listener.socket.send_json({"t": "signed-out"})
                await listener.socket.close()
            except Exception:  # noqa: BLE001
                pass
