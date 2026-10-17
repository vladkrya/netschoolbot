"""Вход на сайт через бота: страница создаёт запрос, человек подтверждает его в боте.

Страница открывает t.me/<бот>?start=web_<код>, бот показывает, откуда
просят вход, и спрашивает подтверждение кнопкой. Без кнопки вход не
происходит: иначе чужую ссылку можно было бы подсунуть, и после /start
автор ссылки вошёл бы в чужой дневник. Страница опрашивает запрос и после
подтверждения получает свой токен устройства. Запросы живут в памяти —
это минуты, переживать перезапуск им незачем.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass

PREFIX = "web_"
TTL_SECONDS = 10 * 60
MAX_PENDING = 2000


@dataclass
class SiteLogin:
    created: float
    ip: str
    browser: str
    telegram_id: int | None = None
    declined: bool = False

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > TTL_SECONDS


class SiteLogins:
    def __init__(self) -> None:
        self._pending: dict[str, SiteLogin] = {}

    def _prune(self) -> None:
        for code in [code for code, item in self._pending.items() if item.expired]:
            del self._pending[code]

    def start(self, ip: str, browser: str) -> str | None:
        """Новый запрос. None — слишком много незавершённых."""
        self._prune()
        if len(self._pending) >= MAX_PENDING:
            return None
        code = secrets.token_urlsafe(16)  # 22 символа: влезает в параметр /start
        self._pending[code] = SiteLogin(time.monotonic(), ip, browser)
        return code

    def get(self, code: str) -> SiteLogin | None:
        item = self._pending.get(code)
        if item is None or item.expired:
            self._pending.pop(code, None)
            return None
        return item

    def decide(self, code: str, telegram_id: int, approve: bool) -> SiteLogin | None:
        """Ответ из бота. Решённый запрос второй раз не решается."""
        item = self.get(code)
        if item is None or item.telegram_id is not None or item.declined:
            return None
        if approve:
            item.telegram_id = telegram_id
        else:
            item.declined = True
        return item

    def take(self, code: str) -> SiteLogin | None:
        """Забрать подтверждённый запрос — один раз."""
        item = self.get(code)
        if item is None or item.telegram_id is None:
            return None
        del self._pending[code]
        return item


def describe_browser(user_agent: str) -> str:
    """«Chrome на Windows» — чтобы в боте было видно, откуда просят вход."""
    ua = user_agent or ""
    browser = next((name for key, name in (
        ("YaBrowser", "Яндекс Браузер"), ("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox", "Firefox"),
        ("Chrome", "Chrome"), ("CriOS", "Chrome"), ("Safari", "Safari"),
    ) if key in ua), "браузер")
    system = next((name for key, name in (
        ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
        ("Mac OS X", "Mac"), ("Linux", "Linux"),
    ) if key in ua), "")
    return f"{browser} на {system}" if system else browser


_ANDROID_MODEL = re.compile(r"Android [\d.]+; ([^;)]+?)(?: Build/[^;)]*)?[;)]")


def device_model(user_agent: str) -> str:
    """Модель телефона, если браузер её сообщает (Android — да, iPhone — нет)."""
    match = _ANDROID_MODEL.search(user_agent or "")
    model = match.group(1).strip() if match else ""
    # Chrome последних версий вместо модели пишет «K».
    return "" if model in ("K", "wv") else model[:40]


def system_version(user_agent: str) -> str:
    """«iOS 18.7», «Android 14», «Windows» — что получится разобрать."""
    ua = user_agent or ""
    if match := re.search(r"(?:iPhone|CPU) OS (\d+)[_.](\d+)", ua):
        return f"iOS {match.group(1)}.{match.group(2)}"
    if match := re.search(r"Android ([\d.]+)", ua):
        return f"Android {match.group(1)}"
    if match := re.search(r"Mac OS X (\d+)[_.](\d+)", ua):
        return f"macOS {match.group(1)}.{match.group(2)}"
    if "Windows" in ua:
        return "Windows"
    return ""
