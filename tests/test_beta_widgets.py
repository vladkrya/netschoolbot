"""Переключатель бета-карточек и безопасный возврат к обычному ответу."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import datetime as dt
import zlib

from app.bot.widgets import widget_api_status, beta_reply, card_html, homework_card_html, send_card
from app.domain.records import HomeworkRecord


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


def test_homework_card_has_app_layout_and_fits_widget_limits():
    item = HomeworkRecord("Алгебра", dt.date(2026, 11, 6), "ДЗ", "<script>alert(1)</script>")
    other = HomeworkRecord("Литература", dt.date(2026, 11, 9), "ДЗ", "Прочитать главу")
    source = homework_card_html([item, other], set())
    assert "СГОшка · дневник" in source
    assert "#4a6fae" in source
    assert "<script>alert(1)</script>" not in source
    assert "&lt;script&gt;" in source
    assert '<details class="task' not in source
    assert 'id="next-day"' not in source
    assert source.count('<div class="page') == 1
    assert 'Пт 06.11' in source and 'Пн 09.11' in source
    assert len(source.encode()) <= 32768
    assert len(zlib.compress(source.encode(), 9)) <= 4096


def test_homework_card_only_expands_extra_content_and_paginates_overflow():
    long_text = "Очень длинное задание " * 7
    items = [HomeworkRecord(f"Предмет {i}", dt.date(2026, 11, 6) + dt.timedelta(days=i), "ДЗ", long_text)
             for i in range(7)]
    source = homework_card_html(items, set())
    assert source.count('<div class="page') > 1
    assert 'id="next-day"' in source
    assert source.index('<nav class="pager">') < source.index('<div class="page')
    assert '<details class="task' in source
    assert '.task.has-full[open] .summary,.task[open] .more{display:none}' in source
    assert '<b>Полное задание:</b>' in source


def test_homework_card_splits_four_assignments_before_widget_clips_them():
    day = dt.date(2026, 11, 9)
    items = [
        HomeworkRecord("История", day, "ДЗ", "§16 (прочитать), выполнить тест https://onlinetestpad.com/o2egfvgixtrgv"),
        HomeworkRecord("Литература", day, "ДЗ", 'Наизусть отрывок "А судьи кто?" из комедии Грибоедова.'),
        HomeworkRecord("Химия", day, "ДЗ", "§9 читать"),
        HomeworkRecord("Русский язык", day, "ДЗ", "Упражнение 45"),
    ]
    source = homework_card_html(items, set())
    assert source.count('<div class="page') >= 2


def test_homework_card_does_not_offer_details_for_medium_text_without_extras():
    item = HomeworkRecord("Алгебра", dt.date(2026, 11, 6), "ДЗ", "Решить номера 125 и 126, повторить правила сложения дробей")
    source = homework_card_html([item], set())
    assert '<details class="task' not in source
    assert 'Подробнее ↓' not in source


@pytest.mark.asyncio
async def test_beta_reply_skips_transport_when_disabled(users):
    await users.get_or_create(101)
    message = AsyncMock()
    app = type("App", (), {"users": users})()
    assert not await beta_reply(message, 101, app, AsyncMock(), "Задание")


@pytest.mark.asyncio
async def test_widget_status_explains_unauthorized_token():
    response = AsyncMock()
    response.status = 401
    response.json.return_value = {"ok": False, "description": "Unauthorized"}
    session = MagicMock()
    session.get.return_value.__aenter__.return_value = response
    bot = AsyncMock()
    bot.token = "test-token"
    bot.session.create_session.return_value = session
    assert "отклонил токен" in await widget_api_status(bot)
    session.get.assert_called_once_with("https://api.telegram.org/bottest-token/getMe", timeout=8)


@pytest.mark.asyncio
async def test_send_card_uses_production_widget_endpoint():
    response = AsyncMock()
    response.json.return_value = {"ok": True, "result": {"message_id": 1}}
    session = MagicMock()
    session.post.return_value.__aenter__.return_value = response
    bot = AsyncMock()
    bot.token = "test-token"
    bot.session.create_session.return_value = session
    message = MagicMock()
    message.chat.id = 123
    assert await send_card(message, bot, "Задание")
    assert session.post.call_args.args[0] == "https://api.telegram.org/bottest-token/sendWidget"
