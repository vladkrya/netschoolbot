"""Переключатель бета-карточек и безопасный возврат к обычному ответу."""

from unittest.mock import AsyncMock

import pytest

from app.bot.widgets import beta_reply, card_html


@pytest.mark.asyncio
async def test_beta_setting_is_per_user_and_persistent(users, db):
    await users.get_or_create(101)
    await users.get_or_create(202)
    assert not await users.beta_widgets(101)
    await users.set_beta_widgets(101, True)
    assert await users.beta_widgets(101)
    assert not await users.beta_widgets(202)
    await users.set_beta_widgets(101, False)
    assert not await users.beta_widgets(101)


def test_card_escapes_message_text():
    source = card_html("<b>Задание</b>\n&lt;script&gt;alert(1)&lt;/script&gt;")
    assert "<script>" not in source
    assert "Задание" in source
    assert "&lt;script&gt;" in source


@pytest.mark.asyncio
async def test_beta_reply_skips_transport_when_disabled(users):
    await users.get_or_create(101)
    message = AsyncMock()
    app = type("App", (), {"users": users})()
    assert not await beta_reply(message, 101, app, AsyncMock(), "Задание")
