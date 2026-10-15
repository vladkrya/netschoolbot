"""Клавиатуры бота."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from ..domain.models import REMINDER_HOURS, TIMEZONES, ApiMode, User, timezone_label


def settings_menu(user: User) -> InlineKeyboardMarkup:
    def toggle(label: str, enabled: bool, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=f"{'✅' if enabled else '🔕'} {label}", callback_data=f"toggle:{action}"
        )

    rows = [
        [toggle("Уведомления", user.enabled, "enabled")],
        # Выключив, можно получать только push из приложения на телефоне.
        [toggle("Присылать в Telegram", user.notifications.telegram, "telegram")],
        [toggle("Изменения оценок", user.notifications.changes, "changes")],
        [toggle("Удаления оценок", user.notifications.deletes, "deletes")],
        [toggle("Домашние задания", user.notifications.homework, "homework")],
        [toggle("Школьная почта", user.notifications.mail, "mail")],
        [toggle("Сводка по понедельникам", user.notifications.weekly_summary, "weekly")],
        [toggle("Вечером — сводка на завтра", user.notifications.evening_summary, "evening")],
        [toggle("Замены и отмены уроков", user.notifications.schedule, "schedule")],
        [toggle("Напоминать о несделанном д/з", user.notifications.homework_reminder, "reminder")],
        [toggle("Контрольные — за день-два", user.notifications.test_reminder, "tests")],
        [toggle("Итоговая оценка под угрозой", user.notifications.grade_risk, "risk")],
        [toggle("Итоги месяца и четверти", user.notifications.monthly_summary, "monthly")],
        *(
            [[toggle("Следить за всеми детьми", user.notifications.all_children, "allkids")]]
            if len(user.available_students) > 1
            else []
        ),
        # Отметки посещаемости есть только в мобильном API.
        *(
            [[toggle("Новые пропуски", user.notifications.absences, "absences")]]
            if user.effective_api_mode is ApiMode.MOBILE
            else []
        ),
        [
            InlineKeyboardButton(
                text=f"⏰ Напоминание в {user.notifications.reminder_hour}:00",
                callback_data="settings:reminder",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🎯 Новые оценки: {marks_filter_label(user.notifications.marks_filter)}",
                callback_data="settings:marks",
            )
        ],
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
        [
            InlineKeyboardButton(
                text=f"🕰 Часовой пояс: {timezone_label(user.timezone)}",
                callback_data="settings:timezone",
            )
        ],
    ]
    if len(user.available_students) > 1:
        rows.append(
            [InlineKeyboardButton(text="👶 Переключить ребёнка", callback_data="settings:child")]
        )
    rows.append([InlineKeyboardButton(text="👪 Уведомления родным", callback_data="family:open")])
    rows.append(
        [InlineKeyboardButton(text=f"🔌 API: {user.effective_api_mode.title}", callback_data="settings:api")]
    )
    rows.append([InlineKeyboardButton(text="🚪 Выйти из школы", callback_data="settings:logout")])
    rows.append([InlineKeyboardButton(text="🗑 Удалить мои данные", callback_data="settings:delete")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def marks_filter_label(values: tuple[int, ...]) -> str:
    if not values or set(values) >= {2, 3, 4, 5}:
        return "все"
    return "только " + ", ".join(str(v) for v in sorted(values))


def reminder_choices(user: User) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if hour == user.notifications.reminder_hour else ''}{hour}:00",
            callback_data=f"remind:{hour}",
        )
        for hour in REMINDER_HOURS
    ]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def marks_filter_choices(user: User) -> InlineKeyboardMarkup:
    chosen = set(user.notifications.marks_filter)
    row = [
        InlineKeyboardButton(
            text=f"{'✅' if not chosen or value in chosen else '⬜'} {value}", callback_data=f"markf:{value}"
        )
        for value in (5, 4, 3, 2)
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            row,
            [InlineKeyboardButton(text="Все оценки", callback_data="markf:all")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:open")],
        ]
    )


def family(followers: list[dict], following: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🔗 Пригласить второго родителя", callback_data="family:invite")]]
    for follower in followers:
        name = follower["name"] or f"ID {follower['id']}"
        rows.append([InlineKeyboardButton(text=f"✖️ Убрать: {name}"[:60], callback_data=f"family:drop:{follower['id']}")])
    for owner_id, label in following:
        rows.append([InlineKeyboardButton(text=f"🚪 Не следить за: {label}"[:60], callback_data=f"family:leave:{owner_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Настройки", callback_data="settings:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def export_periods() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Текущий период", callback_data="export:period"),
                InlineKeyboardButton(text="Весь год", callback_data="export:year"),
            ]
        ]
    )


def interval_choices() -> InlineKeyboardMarkup:
    options = [(3, "3 мин"), (5, "5 мин"), (10, "10 мин"), (30, "30 мин"), (60, "1 час"), (180, "3 часа")]
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"interval:{minutes * 60}")
        for minutes, label in options
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Кнопок «выполнено» не больше этого: длинная клавиатура закрывает чат.
HOMEWORK_BUTTONS = 12


def homework_done(items: list, done: set[str]) -> InlineKeyboardMarkup | None:
    """Кнопка на каждое задание: нажал — отметилось выполненным, и в приложении тоже."""
    rows = []
    # «Не предусмотрено» уже отмечено само — кнопка ему не нужна.
    for item in [item for item in items if not item.nothing_to_do][:HOMEWORK_BUTTONS]:
        mark = "✅" if item.key in done else "⬜"
        label = f"{mark} {item.subject} · {item.due_date:%d.%m}"
        rows.append([InlineKeyboardButton(text=label[:60], callback_data=f"hwdone:{item.key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def timezones(user: User) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if key == user.timezone else ''}{city}", callback_data=f"tz:{key}"
        )
        for key, city in TIMEZONES
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
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
            [InlineKeyboardButton(text="🔑 Логин и пароль школы", callback_data="login:password")],
            [InlineKeyboardButton(text="📱 QR-код Госуслуг", callback_data="login:qr")],
            [InlineKeyboardButton(text="🏛 Госуслуги: логин и пароль", callback_data="login:esia")],
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


def demo(proxied_url: str, direct_url: str) -> InlineKeyboardMarkup:
    """Оба адреса демо доступны как мини-приложение и как обычный сайт."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Миниапп напрямую", web_app=WebAppInfo(url=direct_url))],
            [InlineKeyboardButton(text="☁️ Миниапп через Cloudflare", web_app=WebAppInfo(url=proxied_url))],
            [InlineKeyboardButton(text="🌐 Сайт напрямую", url=direct_url)],
            [InlineKeyboardButton(text="☁️ Сайт через Cloudflare", url=proxied_url)],
        ]
    )


def miniapp_external(url: str, mirror_url: str = "") -> InlineKeyboardMarkup:
    """Обычная ссылка — для установки приложения на домашний экран.

    Внутри Telegram установить PWA нельзя: для этого страницу нужно открыть
    в самом браузере. Резервный адрес — для мобильного интернета по белым
    спискам, когда основной домен не открывается.
    """
    rows = [[InlineKeyboardButton(text="🌐 Открыть в браузере", url=url)]]
    if mirror_url:
        rows.append([InlineKeyboardButton(text="🛟 Резервный адрес", url=mirror_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def api_modes(user: User, mobile_available: bool) -> InlineKeyboardMarkup:
    """Выбор API для /changeapi."""

    def option(mode: ApiMode, label: str) -> InlineKeyboardButton:
        mark = "✅ " if user.effective_api_mode is mode else ""
        return InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"api:{mode.value}")

    rows = [[option(ApiMode.WEB, "🌐 Веб-API")]]
    if mobile_available:
        rows.append([option(ApiMode.MOBILE, "📱 Мобильное API (по умолчанию)")])
    rows.append([InlineKeyboardButton(text="⬅️ Настройки", callback_data="settings:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
