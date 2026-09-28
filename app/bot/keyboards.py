"""Клавиатуры бота."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from ..domain.models import User


def settings_menu(user: User) -> InlineKeyboardMarkup:
    def toggle(label: str, enabled: bool, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=f"{'✅' if enabled else '🔕'} {label}", callback_data=f"toggle:{action}"
        )

    rows = [
        [toggle("Уведомления", user.enabled, "enabled")],
        [toggle("Изменения оценок", user.notifications.changes, "changes")],
        [toggle("Удаления оценок", user.notifications.deletes, "deletes")],
        [toggle("Домашние задания", user.notifications.homework, "homework")],
        [toggle("Школьная почта", user.notifications.mail, "mail")],
        [toggle("Сводка по понедельникам", user.notifications.weekly_summary, "weekly")],
        [
            InlineKeyboardButton(
                text=f"⏱ Интервал: {user.check_interval // 60} мин", callback_data="settings:interval"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🌙 Тихие часы: {user.quiet_hours.as_text()}",
                callback_data="settings:quiet",
            )
        ],
    ]
    if len(user.available_students) > 1:
        rows.append(
            [InlineKeyboardButton(text="👶 Переключить ребёнка", callback_data="settings:child")]
        )
    rows.append([InlineKeyboardButton(text="🚪 Выйти из школы", callback_data="settings:logout")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def interval_choices() -> InlineKeyboardMarkup:
    options = [(3, "3 мин"), (5, "5 мин"), (10, "10 мин"), (30, "30 мин"), (60, "1 час"), (180, "3 часа")]
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"interval:{minutes * 60}")
        for minutes, label in options
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def children(user: User) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅ ' if student.id == user.selected_student_id else ''}{student.name}",
                callback_data=f"child:{student.id}",
            )
        ]
        for student in user.available_students
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subjects(names: list[str], *, prefix: str = "subject") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"{prefix}:{index}")]
        for index, name in enumerate(names)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def login_methods() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Логин и пароль", callback_data="login:password")],
            [InlineKeyboardButton(text="🏛 Госуслуги", callback_data="login:esia")],
            [InlineKeyboardButton(text="📱 QR-код Госуслуг", callback_data="login:qr")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="login:cancel")],
        ]
    )


def confirm(action: str, *, yes: str = "Да", no: str = "Отмена") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=yes, callback_data=f"confirm:{action}"),
                InlineKeyboardButton(text=no, callback_data="confirm:cancel"),
            ]
        ]
    )


def miniapp(url: str) -> InlineKeyboardMarkup:
    """Кнопка открытия мини-приложения внутри Telegram.

    WebAppInfo, а не обычная ссылка: приложение открывается поверх чата, а
    не в браузере, и получает подписанную initData — по ней сервер опознаёт
    человека без токена в адресе.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📱 Открыть дневник", web_app=WebAppInfo(url=url), style="primary")]
        ]
    )


def demo(url: str) -> InlineKeyboardMarkup:
    """Демо: внутри Telegram и отдельной ссылкой — поделиться или открыть в браузере."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Открыть демо", web_app=WebAppInfo(url=url))],
            [InlineKeyboardButton(text="🌐 В браузере", url=url)],
        ]
    )


def miniapp_external(url: str) -> InlineKeyboardMarkup:
    """Обычная ссылка — для установки приложения на домашний экран.

    Внутри Telegram установить PWA нельзя: для этого страницу нужно открыть
    в самом браузере.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🌐 Открыть в браузере", url=url)]]
    )
