"""Переключатель бета-карточек и безопасный возврат к обычному ответу."""

from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import pytest

import datetime as dt
import zlib

from app.bot.widgets import widget_api_status, beta_reply, card_html, homework_card_html, grades_card_html, grade_detail_pages, send_card
from app.bot.widget_queries import handle_widget_query
from app.bot.widget_queries import WidgetQueryMiddleware
from aiogram import Bot, Dispatcher
from aiogram.types import Update
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
    assert "<script>alert(1)</script>" not in source
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
    assert '<details class="expand' in source
    assert '.expand.has-full[open] .summary,.expand[open] .more{display:none}' in source
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


def test_generic_card_paginates_long_command_output():
    source = card_html("🗓 Расписание\n" + "\n".join(f"{i}. Математика — домашнее задание" for i in range(20)))
    assert source.count('<div class="page') > 1
    assert source.index('id="next"') < source.index('<div class="page')


def test_grades_card_has_subject_actions_inside_widget():
    source = grades_card_html([f"Предмет {i}" for i in range(12)])
    assert source.count('class="subject-page') == 3
    assert 'W.request(\'g:\'+i+\':\'+p)' in source
    assert len(zlib.compress(source.encode(), 9)) <= 4096


def test_grade_detail_pages_fit_widget_answer_limit():
    from app.domain.records import MarkRecord
    records = [MarkRecord("Алгебра", dt.date(2026, 11, 1), "Контрольная работа", str(i), "5") for i in range(100)]
    pages = grade_detail_pages("Алгебра", records)
    assert len(pages) > 1
    assert all(len(page.encode()) <= 550 for page in pages)


@pytest.mark.asyncio
async def test_homework_widget_query_saves_and_answers(monkeypatch):
    from app.bot import widget_queries
    key = "1234567890abcdef"
    api = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(widget_queries, "_api", api)
    monkeypatch.setattr(widget_queries, "blocked", AsyncMock(return_value=False))
    app = SimpleNamespace(
        users=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(selected_student_id=4)),
                              beta_widgets=AsyncMock(return_value=True)),
        cache=SimpleNamespace(get=AsyncMock(return_value=({"kind": "homework", "keys": [key], "student_id": 4}, None))),
        state=SimpleNamespace(set_homework_done=AsyncMock(), done_homework=AsyncMock(return_value={key})),
    )
    query = {"id": "77", "from": {"id": 123}, "chat": {"id": 123, "type": "private"},
             "message_id": 5, "data": f"h:{key}:1"}
    await handle_widget_query(query, MagicMock(), app)
    app.state.set_homework_done.assert_awaited_once_with(123, key, True)
    assert api.await_args_list[0].args[1] == "answerWidgetQuery"
    assert api.await_args_list[1].args[1] == "editMessageWidgetData"
    assert '"done":["1234567890abcdef"]' in api.await_args_list[1].args[2]["chat_data"]


@pytest.mark.asyncio
async def test_widget_reply_keeps_keyboard_outside_only_in_text_fallback(monkeypatch):
    from app.bot import widgets
    send = AsyncMock(return_value=12)
    monkeypatch.setattr(widgets, "send_card", send)
    app = SimpleNamespace(users=SimpleNamespace(beta_widgets=AsyncMock(return_value=True)),
                          cache=SimpleNamespace(put=AsyncMock()))
    meta = {"kind": "homework", "keys": ["abc"], "done": [], "student_id": 4}
    assert await beta_reply(MagicMock(), 123, app, MagicMock(), "📚 ДЗ", reply_markup=MagicMock(),
                            source_html=homework_card_html([], set()), widget_meta=meta)
    assert send.await_args.kwargs["reply_markup"] is None
    assert send.await_args.kwargs["interactive"] is True
    app.cache.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_widget_query_rejects_keys_outside_its_message(monkeypatch):
    from app.bot import widget_queries
    api = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(widget_queries, "_api", api)
    monkeypatch.setattr(widget_queries, "blocked", AsyncMock(return_value=False))
    app = SimpleNamespace(
        users=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(selected_student_id=4)),
                              beta_widgets=AsyncMock(return_value=True)),
        cache=SimpleNamespace(get=AsyncMock(return_value=({"kind": "homework", "keys": ["safe"], "student_id": 4}, None))),
        state=SimpleNamespace(set_homework_done=AsyncMock()),
    )
    query = {"id": "77", "from": {"id": 123}, "chat": {"id": 123, "type": "private"},
             "message_id": 5, "data": "h:unsafe:1"}
    await handle_widget_query(query, MagicMock(), app)
    app.state.set_homework_done.assert_not_awaited()
    assert api.await_count == 1
    assert "Неизвестное задание" in api.await_args.args[2]["data"]


@pytest.mark.asyncio
async def test_grade_widget_query_returns_selected_page(monkeypatch):
    from app.bot import widget_queries
    api = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(widget_queries, "_api", api)
    monkeypatch.setattr(widget_queries, "blocked", AsyncMock(return_value=False))
    app = SimpleNamespace(
        users=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(selected_student_id=4)),
                              beta_widgets=AsyncMock(return_value=True)),
        cache=SimpleNamespace(get=AsyncMock(return_value=({"kind": "grades", "pages": [["one", "two"]], "student_id": 4}, None))),
    )
    query = {"id": "77", "from": {"id": 123}, "chat": {"id": 123, "type": "private"},
             "message_id": 5, "data": "g:0:1"}
    await handle_widget_query(query, MagicMock(), app)
    assert api.await_count == 1
    assert '"body":"two"' in api.await_args.args[2]["data"]


@pytest.mark.asyncio
async def test_aiogram_dispatcher_routes_unrecognized_widget_update(monkeypatch):
    from app.bot import widget_queries
    handle = AsyncMock()
    monkeypatch.setattr(widget_queries, "handle_widget_query", handle)
    app = MagicMock()
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(WidgetQueryMiddleware(app))
    bot = Bot(token="123456:TEST_TOKEN")
    try:
        update = Update.model_validate({"update_id": 42, "widget_query": {"id": "q", "data": "s"}},
                                       context={"bot": bot})
        await dispatcher.feed_update(bot, update)
        handle.assert_awaited_once()
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


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
