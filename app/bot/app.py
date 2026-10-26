"""Сборка и запуск Telegram-бота."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,
    MenuButtonCommands, MenuButtonWebApp, WebAppInfo,
)

from ..context import AppContext
from ..settings import Settings
from .handlers import account, admin, auth, family, groups, menu, settings as settings_handlers
from .handlers.common import AppOnlyMiddleware, ContextMiddleware

logger = logging.getLogger("netschoolbot.bot")

COMMANDS = [
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="today", description="Сегодня: уроки, оценки, задания"),
    BotCommand(command="tomorrow", description="Завтра: уроки и задания"),
    BotCommand(command="last", description="Последние оценки"),
    BotCommand(command="dz", description="Домашние задания"),
    BotCommand(command="rasp", description="Расписание"),
    BotCommand(command="grades", description="Оценки по предметам"),
    BotCommand(command="mystats", description="Статистика"),
    BotCommand(command="weeksummary", description="Сводка за неделю"),
    BotCommand(command="app", description="Открыть дневник"),
    BotCommand(command="app_install", description="Установить на телефон"),
    BotCommand(command="demo", description="Демо-версия приложения"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="profile", description="Профиль"),
    BotCommand(command="status", description="Состояние проверки"),
    BotCommand(command="history", description="История изменений оценок"),
    BotCommand(command="export", description="Оценки в Excel"),
    BotCommand(command="family", description="Родные: уведомления и просмотр"),
    BotCommand(command="timezone", description="Часовой пояс"),
    BotCommand(command="child", description="Переключить ребёнка"),
    BotCommand(command="changeapi", description="Сменить API: веб или мобильное"),
    BotCommand(command="login", description="Войти в «Сетевой город»"),
    BotCommand(command="logout", description="Выйти"),
    BotCommand(command="resetpin", description="Сбросить PIN дневника"),
    BotCommand(command="delete_data", description="Удалить мои данные"),
    BotCommand(command="cancel", description="Отменить текущий диалог"),
]


def create_bot(settings: Settings) -> Bot:
    from aiogram.client.session.aiohttp import AiohttpSession

    session = AiohttpSession(proxy=settings.telegram.api_proxy or None)
    return Bot(
        token=settings.telegram.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(context: AppContext) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())

    # В группах бот только публикует служебные уведомления. Отсекаем
    # сообщения и старые кнопки до обработчиков и создания пользователя.
    dispatcher.message.filter(F.chat.type == "private")
    dispatcher.callback_query.filter(F.message.chat.type == "private")

    middleware = ContextMiddleware(context)
    dispatcher.message.middleware(middleware)
    dispatcher.callback_query.middleware(middleware)
    # Бота добавили в группу: пользователя под того, кто добавил, не заводим.
    dispatcher.my_chat_member.middleware(AppOnlyMiddleware(context))

    # Порядок важен: auth перехватывает сообщения внутри диалога входа,
    # поэтому регистрируется раньше общего меню.
    dispatcher.include_router(admin.router)
    dispatcher.include_router(groups.router)
    dispatcher.include_router(account.router)
    dispatcher.include_router(family.router)
    dispatcher.include_router(auth.router)
    dispatcher.include_router(settings_handlers.router)
    dispatcher.include_router(menu.router)
    return dispatcher


async def run_bot(bot: Bot, dispatcher: Dispatcher, miniapp_url: str) -> None:
    await bot.delete_my_commands()
    await bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
    await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_chat_menu_button(
        menu_button=(
            MenuButtonWebApp(text="СГОшка", web_app=WebAppInfo(url=f"{miniapp_url.rstrip('/')}/"))
            if miniapp_url.startswith("https://") else MenuButtonCommands()
        )
    )
    me = await bot.get_me()
    logger.info("Бот @%s запущен", me.username)
    # drop_pending_updates: после простоя очередь может быть забита старыми
    # сообщениями, отвечать на которые уже поздно и странно.
    await dispatcher.start_polling(bot, drop_pending_updates=True)
