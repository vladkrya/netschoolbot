"""Обработка widget_query, которого пока нет в типах установленного aiogram."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, Update

from ..access import blocked
from ..context import AppContext

logger = logging.getLogger("netschoolbot.bot")


async def _api(bot: Bot, method: str, payload: dict) -> dict:
    session = await bot.session.create_session()
    async with session.post(f"https://api.telegram.org/bot{bot.token}/{method}", data=payload, timeout=10) as response:
        return await response.json(content_type=None)


async def _answer(bot: Bot, query_id: str, result: dict) -> None:
    body = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(body.encode()) > 1024:
        body = '{"error":"Ответ слишком длинный"}'
    reply = await _api(bot, "answerWidgetQuery", {"widget_query_id": query_id, "data": body})
    if not reply.get("ok"):
        logger.warning("answerWidgetQuery failed: %s", str(reply.get("description", ""))[:120])


async def handle_widget_query(query: dict, bot: Bot, app: AppContext) -> None:
    """Разрешаем действия только из своих исходных виджетов в личном чате."""
    query_id = str(query.get("id", ""))
    if not query_id:
        return
    actor = query.get("from") or {}
    chat = query.get("chat") or (query.get("message") or {}).get("chat") or {}
    user_id = actor.get("id")
    message_id = query.get("message_id") or (query.get("message") or {}).get("message_id")
    if not isinstance(user_id, int) or chat.get("type") != "private" or chat.get("id") != user_id or not isinstance(message_id, int):
        await _answer(bot, query_id, {"error": "Действие доступно только в личном чате"})
        return
    user = await app.users.get(user_id)
    if user is None or await blocked(app, user_id) or not await app.users.beta_widgets(user_id):
        await _answer(bot, query_id, {"error": "Откройте /beta и отправьте команду заново"})
        return
    cached = await app.cache.get(user_id, f"widget:{message_id}")
    if not cached:
        await _answer(bot, query_id, {"error": "Карточка устарела. Отправьте команду заново"})
        return
    meta, _ = cached
    if meta.get("student_id") != user.selected_student_id:
        await _answer(bot, query_id, {"error": "Выбран другой ребёнок. Отправьте команду заново"})
        return
    data = str(query.get("data", ""))
    if meta.get("kind") == "homework" and (data == "s" or data.startswith("h:")):
        keys = meta.get("keys", [])
        if data.startswith("h:"):
            parts = data.split(":")
            if len(parts) != 3 or parts[1] not in keys or parts[2] not in {"0", "1"}:
                await _answer(bot, query_id, {"error": "Неизвестное задание"})
                return
            await app.state.set_homework_done(user_id, parts[1], parts[2] == "1")
        done = await app.state.done_homework(user_id)
        active = [key for key in keys if key in done]
        state = json.dumps({"done": active}, separators=(",", ":"))
        if len(state.encode()) > 1024:
            await _answer(bot, query_id, {"error": "Слишком много заданий в карточке"})
            return
        result = {"done": active}
        if data.startswith("h:"):
            result = {"done": parts[1] in done, "all": active}
        await _answer(bot, query_id, result)
        try:
            edited = await _api(bot, "editMessageWidgetData", {"chat_id": user_id, "message_id": message_id, "chat_data": state})
            if not edited.get("ok") and "not modified" not in edited.get("description", "").lower():
                logger.warning("editMessageWidgetData failed: %s", str(edited.get("description", ""))[:120])
        except Exception as exc:  # noqa: BLE001 — ответ уже отправлен; состояние сохранено в БД
            logger.warning("editMessageWidgetData unavailable: %s", type(exc).__name__)
        return
    if meta.get("kind") == "grades" and data.startswith("g:"):
        try:
            _, index, page = data.split(":")
            subject_index = int(index)
            page_number = int(page)
            if subject_index < 0 or page_number < 0:
                raise IndexError
            pages = meta["pages"][subject_index]
            body = pages[page_number]
        except (ValueError, IndexError, KeyError, TypeError):
            await _answer(bot, query_id, {"error": "Неизвестная страница оценок"})
            return
        await _answer(bot, query_id, {"body": body, "page": page_number, "pages": len(pages)})
        return
    await _answer(bot, query_id, {"error": "Неизвестное действие"})


class WidgetQueryMiddleware(BaseMiddleware):
    def __init__(self, app: AppContext) -> None:
        self.app = app

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        if isinstance(event, Update) and isinstance((event.model_extra or {}).get("widget_query"), dict):
            query = event.model_extra["widget_query"]
            try:
                await handle_widget_query(query, data["bot"], self.app)
            except Exception:  # noqa: BLE001 — не ломаем polling из-за одного запроса
                logger.exception("Ошибка обработки widget_query")
                if query.get("id"):
                    try:
                        await _answer(data["bot"], str(query["id"]), {"error": "Не удалось сохранить изменение"})
                    except Exception:  # noqa: BLE001 — Telegram может быть недоступен
                        pass
            return None
        return await handler(event, data)
