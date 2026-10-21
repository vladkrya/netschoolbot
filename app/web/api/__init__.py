"""HTTP API мини-приложения.

Раньше это был файл на 2953 строки, в котором каждый обработчик сам ходил в
«Сетевой город» через `asyncio.run()` — то есть на каждый запрос создавался
новый event loop, а соединения не переиспользовались. Теперь обработчики
асинхронные и работают через общий сервис дневника.

Обработчики разложены по модулям пакета; все регистрируются в общей таблице
`routes` из base.py. Порядок импорта ниже — порядок регистрации маршрутов:
у aiohttp выигрывает первый подходящий, поэтому `/api/mail/recipients`
должен быть раньше `/api/mail/{message_id}` (внутри mail.py так и есть).
"""

from .base import (
    BOT,
    CONTEXT,
    USER,
    _ticket_key,
    auth_middleware,
    context,
    current_user,
    error_middleware,
    halloween_season,
    issue_attachment_ticket,
    json_error,
    logger,
    routes,
    verify_attachment_ticket,
)
from . import profile, diary, marks, homework, mail, attachments, prefs  # noqa: F401  регистрация маршрутов
from .attachments import _content_disposition, attachment_payload
from .mail import _safe_filename
from .marks import _lesson_over

__all__ = [
    "BOT", "CONTEXT", "USER", "_content_disposition", "_lesson_over", "_safe_filename", "_ticket_key",
    "attachment_payload", "auth_middleware", "context", "current_user", "error_middleware",
    "halloween_season", "issue_attachment_ticket", "json_error", "logger", "routes",
    "verify_attachment_ticket",
]
