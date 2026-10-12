"""Живые соединения чатов (WebSocket): кто на связи и какую комнату смотрит.

По сокету идут только сигналы «в комнате новое» — сами сообщения клиент
забирает обычным запросом. Так не нужно собирать сообщение отдельно для
каждого (модератор видит скрытое, у каждого свои реакции), а при обрыве
связи ничего не теряется: следующий запрос заберёт всё пропущенное.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .rules import room_school

logger = logging.getLogger("netschoolbot.chat")


@dataclass(eq=False)
class Listener:
    telegram_id: int
    rooms: set[str]
    every_room: bool = False  # администратор в режиме admin
    schools: set[str] = field(default_factory=set)  # модератор школы
    open_room: str | None = None
    socket: Any = None
    name: str = ""  # подпись для «печатает…»; пусто — писать не может
    typed_at: float = 0.0

    def hears(self, room: str) -> bool:
        return self.every_room or room in self.rooms or room_school(room) in self.schools


class ChatHub:
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

    def watching(self, telegram_id: int, room: str) -> bool:
        """Человек сейчас смотрит эту комнату — уведомлять незачем."""
        return any(item.telegram_id == telegram_id and item.open_room == room for item in self._listeners)

    def online(self, telegram_id: int) -> bool:
        return any(item.telegram_id == telegram_id for item in self._listeners)

    async def publish(self, room: str, event: dict[str, Any], *, skip: int | None = None) -> None:
        for listener in list(self._listeners):
            if listener.telegram_id == skip or not listener.hears(room):
                continue
            try:
                await listener.socket.send_json(event)
            except Exception as exc:  # noqa: BLE001 — оборванное соединение уберёт его обработчик
                logger.debug("Сокет чата %s не принял событие: %s", listener.telegram_id, exc)

    async def publish_user(self, telegram_id: int, event: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            if listener.telegram_id == telegram_id:
                try:
                    await listener.socket.send_json(event)
                except Exception:  # noqa: BLE001
                    pass
