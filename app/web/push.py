"""Web push через VAPID.

Отправка сделана в пуле потоков: pywebpush синхронна, и вызов её напрямую
из корутины подвесил бы весь процесс — теперь, когда бот и веб живут в одном
event loop, это уронило бы и уведомления в Telegram.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..db.repositories import MiniappRepository
from ..settings import PushSettings

logger = logging.getLogger("netschoolbot.push")

# Коды, после которых подписку нужно удалить немедленно: браузер сообщает,
# что её больше нет.
GONE_STATUSES = {404, 410}


class PushSender:
    def __init__(self, settings: PushSettings, miniapp: MiniappRepository) -> None:
        self._settings = settings
        self._miniapp = miniapp

    @property
    def configured(self) -> bool:
        return self._settings.configured

    async def send(self, telegram_id: int, title: str, body: str, url: str = "") -> int:
        """Разослать уведомление на все устройства пользователя.

        Возвращает число успешных отправок.
        """
        if not self.configured:
            return 0
        subscriptions = await self._miniapp.push_subscriptions(telegram_id)
        if not subscriptions:
            return 0

        payload = json.dumps(
            {"title": title, "body": body, "url": url}, ensure_ascii=False
        )
        results = await asyncio.gather(
            *(self._send_one(sub, payload) for sub in subscriptions),
            return_exceptions=True,
        )
        return sum(1 for result in results if result is True)

    async def _send_one(self, subscription: dict[str, str], payload: str) -> bool:
        endpoint = subscription["endpoint"]
        try:
            status = await asyncio.to_thread(self._blocking_send, subscription, payload)
        except Exception as exc:
            # pywebpush сам бросает WebPushException на ответ 4xx/5xx — код
            # ответа внутри. Раньше 404/410 сюда и попадали, и отозванная
            # подписка удалялась лишь после пяти неудач.
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            if status is None:
                logger.warning("Push не ушёл: %s", exc)
                await self._miniapp.note_push_failure(endpoint)
                return False

        if status in GONE_STATUSES:
            # Подписка отозвана в браузере. Узнать об этом иначе нельзя.
            logger.info("Подписка %s больше не существует — удаляем", _short(endpoint))
            await self._miniapp.drop_push_subscription(endpoint)
            return False
        if status >= 400:
            await self._miniapp.note_push_failure(endpoint)
            return False
        return True

    def _blocking_send(self, subscription: dict[str, str], payload: str) -> int:
        from pywebpush import webpush

        response = webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
            },
            data=payload,
            vapid_private_key=self._settings.private_key,
            vapid_claims={"sub": self._settings.subject},
            timeout=10,
        )
        return getattr(response, "status_code", 200)


def _short(endpoint: str) -> str:
    """Урезанный адрес для логов: полный содержит идентификатор устройства."""
    return endpoint[:60] + "…" if len(endpoint) > 60 else endpoint
