"""Отправка web push: что делать с ответами сервиса уведомлений."""

from __future__ import annotations

import pytest

from app.settings import PushSettings
from app.web.push import PushSender


class Repo:
    def __init__(self):
        self.dropped = []
        self.failed = []

    async def push_subscriptions(self, telegram_id):
        return [{"endpoint": "https://push/1", "p256dh": "k", "auth": "a"}]

    async def drop_push_subscription(self, endpoint):
        self.dropped.append(endpoint)

    async def note_push_failure(self, endpoint):
        self.failed.append(endpoint)


class Response:
    def __init__(self, status_code):
        self.status_code = status_code


class WebPushError(Exception):
    def __init__(self, status_code):
        super().__init__("push failed")
        self.response = Response(status_code)


def sender(repo, error):
    push = PushSender(PushSettings("pub", "priv", "mailto:a@b.c"), repo)

    def blocking_send(subscription, payload):
        raise error

    push._blocking_send = blocking_send
    return push


@pytest.mark.asyncio
async def test_revoked_subscription_is_dropped_at_once():
    # pywebpush бросает исключение на 410 — подписку всё равно удаляем сразу,
    # а не после пяти неудач.
    repo = Repo()
    assert await sender(repo, WebPushError(410)).send(1, "Т", "Б") == 0
    assert repo.dropped == ["https://push/1"]
    assert repo.failed == []


@pytest.mark.asyncio
async def test_network_failure_is_counted():
    repo = Repo()
    assert await sender(repo, OSError("timeout")).send(1, "Т", "Б") == 0
    assert repo.dropped == []
    assert repo.failed == ["https://push/1"]
