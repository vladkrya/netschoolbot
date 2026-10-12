"""Логирование: файл, консоль и отправка ошибок админу в Telegram.

Отправка в Telegram сделана неблокирующей через очередь. В старой версии
хендлер писал в сеть прямо из вызова `logger.error`, поэтому недоступный
Telegram подвешивал ту самую задачу, которая пыталась сообщить о проблеме.
"""

from __future__ import annotations

import asyncio
import copy
import html
import re
import uuid
import logging
import logging.handlers
from pathlib import Path

from .settings import Settings

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Сколько сообщений держим в очереди на отправку админу. Дальше — отбрасываем:
# лавина ошибок не должна съесть память.
TELEGRAM_QUEUE_SIZE = 100


# Аргументы логов могут содержать URL, учётные данные и школьные ответы.
# Выводим статический шаблон операции, тип ошибки и расположение в коде.
_PLACEHOLDER = re.compile(r"%(?:\([^)]+\))?[#0 +\-]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa%]")


def _error_label(error: BaseException) -> str:
    """Тип ошибки, а у ошибок «Сетевого города» — и причина: auth, server…

    Причина — значение из короткого перечня (Reason), личных данных в ней
    нет. Без неё «NetSchoolError» в логе не говорил, чинить ли что-то у нас
    или у человека просто не проходит вход.
    """
    label = type(error).__name__
    value = getattr(getattr(error, "reason", None), "value", None)
    if isinstance(value, str) and value.isidentifier():
        label += f"({value})"
    cause = getattr(error, "cause", None)
    if isinstance(cause, BaseException):
        label += f" ← {type(cause).__name__}"
    return label


def _safe_message(record: logging.LogRecord) -> str:
    if not record.name.startswith("netschoolbot"):
        return "Событие внешней библиотеки"
    template = str(record.msg)
    # Все сообщения приложения используют статический шаблон и отдельные args.
    if record.args:
        template = _PLACEHOLDER.sub(lambda m: "%" if m.group() == "%%" else "[скрыто]", template)
        values = record.args.values() if isinstance(record.args, dict) else record.args
        types = [_error_label(v) for v in values if isinstance(v, BaseException)]
        if types:
            template += " | " + ", ".join(types)
    return template


class SafeFormatter(logging.Formatter):
    """Без текста исключения, исходных строк кода и динамических параметров."""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "event_id"):
            record.event_id = uuid.uuid4().hex[:12]
        safe = copy.copy(record)
        safe.msg = _safe_message(record)
        safe.args = ()
        safe.exc_text = None  # Чужой formatter мог уже закэшировать сырой traceback.
        safe.stack_info = None
        result = super().format(safe)
        if record.levelno >= logging.ERROR:
            result += f"\nСобытие: {record.event_id}"
        return result

    def formatException(self, exc_info) -> str:
        _, exc, tb = exc_info
        lines = [f"Ошибка: {type(exc).__name__}"]
        while tb is not None:
            code = tb.tb_frame.f_code
            lines.append(f"  {Path(code.co_filename).name}:{tb.tb_lineno} ({code.co_name})")
            tb = tb.tb_next
        return "\n".join(lines)


class AdminFormatter(SafeFormatter):
    def formatException(self, exc_info) -> str:
        return f"Ошибка: {type(exc_info[1]).__name__}"


class TelegramHandler(logging.Handler):
    """Кладёт запись в очередь; отправкой занимается отдельная задача."""

    def __init__(self, queue: asyncio.Queue[str], level: int = logging.ERROR) -> None:
        super().__init__(level)
        self._queue = queue
        self.setFormatter(AdminFormatter(LOG_FORMAT, DATE_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # noqa: BLE001 — форматирование не должно ронять логгер
            return
        try:
            self._queue.put_nowait(message[:3500])
        except asyncio.QueueFull:
            # Очередь переполнена: сообщение теряется, но приложение живёт.
            pass


def setup_logging(settings: Settings) -> asyncio.Queue[str]:
    """Настроить логирование. Возвращает очередь сообщений для админа."""
    settings.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)
    for existing in list(root.handlers):
        root.removeHandler(existing)

    formatter = SafeFormatter(LOG_FORMAT, DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        settings.logs_dir / "netschoolbot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=TELEGRAM_QUEUE_SIZE)
    if settings.telegram.logging_enabled and settings.telegram.admin_id:
        telegram = TelegramHandler(queue)
        root.addHandler(telegram)

    # Чужие библиотеки многословны: их отладка нам не нужна.
    for noisy in ("aiogram.event", "aiohttp.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return queue


async def drain_to_telegram(queue: asyncio.Queue[str], bot, admin_id: int) -> None:
    """Отправлять накопленные ошибки админу. Запускается фоновой задачей."""
    logger = logging.getLogger("netschoolbot.logs")
    while True:
        message = await queue.get()
        try:
            await bot.send_message(admin_id, f"<pre>{html.escape(message)}</pre>", parse_mode="HTML")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Провал отправки лога нельзя логировать через тот же логгер —
            # получится бесконечная петля. Пишем напрямую в файл.
            logger.handlers and logger.debug("Не удалось отправить лог админу: %s", exc)
        finally:
            queue.task_done()
        # Telegram ограничивает частоту сообщений одному чату.
        await asyncio.sleep(1.0)
