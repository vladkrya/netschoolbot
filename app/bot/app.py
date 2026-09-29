"""Сборка и запуск Telegram-бота."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonCommands, MenuButtonWebApp, WebAppInfo

from ..context import AppContext
from ..settings import Settings
from .handlers import auth, menu, settings as settings_handlers
from .handlers.common import ContextMiddleware

logger = logging.getLogger("netschoolbot.bot")

COMMANDS = [
    BotCommand(command="menu", description="Главное меню"),
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
    BotCommand(command="child", description="Переключить ребёнка"),
    BotCommand(command="changeapi", description="Сменить API: веб или мобильное"),
    BotCommand(command="login", description="Войти в «Сетевой город»"),
    BotCommand(command="logout", description="Выйти"),
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

    middleware = ContextMiddleware(context)
    dispatcher.message.middleware(middleware)
    dispatcher.callback_query.middleware(middleware)

    # Порядок важен: auth перехватывает сообщения внутри диалога входа,
    # поэтому регистрируется раньше общего меню.
    dispatcher.include_router(auth.router)
    dispatcher.include_router(settings_handlers.router)
    dispatcher.include_router(menu.router)
    return dispatcher


async def run_bot(bot: Bot, dispatcher: Dispatcher, miniapp_url: str) -> None:
    await bot.set_my_commands(COMMANDS)
    await bot.set_chat_menu_button(
        menu_button=(
            MenuButtonWebApp(text="Дневник", web_app=WebAppInfo(url=f"{miniapp_url.rstrip('/')}/"))
            if miniapp_url.startswith("https://") else MenuButtonCommands()
        )
    )
    me = await bot.get_me()
    logger.info("Бот @%s запущен", me.username)
    # drop_pending_updates: после простоя очередь может быть забита старыми
    # сообщениями, отвечать на которые уже поздно и странно.
    await dispatcher.start_polling(bot, drop_pending_updates=True)
