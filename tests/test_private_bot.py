"""Группы получают уведомления, но не управляют личным дневником."""

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot.app import COMMANDS, create_dispatcher, run_bot
from app.domain.models import Credentials, LoginType, School, User as SchoolUser


async def test_dispatcher_ignores_group_commands_dialogs_and_buttons():
    context = SimpleNamespace(users=SimpleNamespace(get_or_create=AsyncMock(
        return_value=SchoolUser(
            telegram_id=42, school=School(url="", name=""),
            credentials=Credentials(login_type=LoginType.PASSWORD),
        ),
    )))
    bot = Bot("123456:abcdef")
    dispatcher = create_dispatcher(context)
    sender = User(id=42, is_bot=False, first_name="Ученик")
    bot.session.make_request = AsyncMock()
    update_id = 0
    try:
        for chat_type in ("group", "supergroup"):
            chat = Chat(id=-42, type=chat_type)
            state = dispatcher.fsm.get_context(bot=bot, chat_id=-42, user_id=42)
            await state.set_state("Login:method")
            for text in ("/start", "/login", "/logout", "/grades", "/app", "/status", "Пароль"):
                update_id += 1
                message = Message(
                    message_id=update_id, date=dt.datetime.now(dt.timezone.utc),
                    chat=chat, from_user=sender, text=text,
                )
                await dispatcher.feed_update(bot, Update(update_id=update_id, message=message))
            update_id += 1
            await dispatcher.feed_update(bot, Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id=str(update_id), from_user=sender, chat_instance="group",
                    message=message, data="hwdone:task",
                ),
            ))
            assert await state.get_state() == "Login:method"
        context.users.get_or_create.assert_not_awaited()
        bot.session.make_request.assert_not_awaited()

        update_id += 1
        await dispatcher.feed_update(bot, Update(update_id=update_id, message=Message(
            message_id=update_id, date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=42, type="private"), from_user=sender, text="/start",
        )))
        context.users.get_or_create.assert_awaited_once()
        bot.session.make_request.assert_awaited_once()
        assert "/login" in bot.session.make_request.call_args.args[1].text
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


async def test_command_menu_is_only_published_in_private_chats():
    bot = SimpleNamespace(
        delete_my_commands=AsyncMock(), set_my_commands=AsyncMock(),
        set_chat_menu_button=AsyncMock(), get_me=AsyncMock(return_value=SimpleNamespace(username="bot")),
    )
    dispatcher = SimpleNamespace(start_polling=AsyncMock())
    await run_bot(bot, dispatcher, "https://example.com/mini")
    assert bot.delete_my_commands.await_count == 2
    assert bot.delete_my_commands.await_args_list[0].kwargs == {}
    assert bot.delete_my_commands.await_args_list[1].kwargs["scope"].type == "all_group_chats"
    bot.set_my_commands.assert_awaited_once()
    assert bot.set_my_commands.call_args.args[0] == COMMANDS
    assert bot.set_my_commands.call_args.kwargs["scope"].type == "all_private_chats"
