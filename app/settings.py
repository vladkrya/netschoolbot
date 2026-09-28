"""Настройки приложения.

Один источник правды: dataclass, собираемый из окружения ровно один раз при
старте. В отличие от старого `config.py`, здесь нет модуля-с-константами,
который каждый импортёр переименовывает под себя.

Отношение к некорректным значениям намеренно снисходительное: странное
значение подменяется значением по умолчанию с записью в лог, и только
отсутствие токена бота останавливает запуск. Причина в том, что служба
живёт на сервере с рукописным .env, который переживает обновления
приложения: отказ стартовать означает не «безопасно», а «бот лежит, пока
кто-нибудь не зайдёт по SSH».
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("netschoolbot.settings")

from .domain.models import MAX_CHECK_INTERVAL, MIN_CHECK_INTERVAL

BASE_DIR = Path(__file__).resolve().parent.parent

MSK_UTC_OFFSET_HOURS = 3


class SettingsError(RuntimeError):
    """Конфигурация непригодна для запуска.

    Поднимается только тогда, когда работать действительно нечем — по сути,
    при отсутствии токена бота. Всё остальное чинится подстановкой значения
    по умолчанию с записью в лог.

    Так сделано не из вкуса к снисходительности. Служба живёт на сервере с
    рукописным .env, который переживает обновления приложения. Отказ
    стартовать из-за подозрительного значения означает не «безопасно», а
    «бот молча лежит, пока кто-нибудь не зайдёт по SSH» — что уже
    случилось из-за настройки, которая ни на что не влияла.
    """


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if not raw:
        return default
    # Значения вида "300  # комментарий" встречаются в рукописных .env.
    raw = raw.split("#", 1)[0].strip().strip("'\"")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r — не число, беру значение по умолчанию %s", key, raw, default)
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    logger.warning("%s=%r — не да/нет, беру значение по умолчанию %s", key, raw, default)
    return default


@dataclass(frozen=True, slots=True)
class TelegramSettings:
    bot_token: str
    admin_id: int
    log_bot_token: str = ""
    logging_enabled: bool = True
    api_proxy: str = ""


@dataclass(frozen=True, slots=True)
class WebSettings:
    enabled: bool
    host: str
    port: int
    public_url: str
    miniapp_path: str
    token_ttl: int
    login_code_ttl: int
    cache_fresh_seconds: int

    @property
    def miniapp_url(self) -> str:
        return f"{self.public_url}{self.miniapp_path}"


@dataclass(frozen=True, slots=True)
class PushSettings:
    public_key: str
    private_key: str
    subject: str

    @property
    def configured(self) -> bool:
        return bool(self.public_key and self.private_key)


@dataclass(frozen=True, slots=True)
class NetSchoolSettings:
    default_check_interval: int
    session_ttl: int
    http_timeout: int
    blocked_host_ttl: int
    qr_login_ttl: int
    fallback_proxy: str
    # host-подстрока -> прокси. Некоторые региональные серверы «Сетевого
    # города» отбивают запросы с datacenter-адресов.
    proxy_hosts: dict[str, str] = field(default_factory=dict)

    def proxy_for(self, url: str) -> str | None:
        for host, proxy in self.proxy_hosts.items():
            if host in url:
                return proxy or None
        return None


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    db_path: Path
    telegram: TelegramSettings
    web: WebSettings
    push: PushSettings
    netschool: NetSchoolSettings
    debug: bool

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def icons_dir(self) -> Path:
        return self.data_dir / "icons"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.logs_dir, self.attachments_dir, self.icons_dir):
            path.mkdir(parents=True, exist_ok=True)


def _parse_proxy_hosts(raw: str, volgograd_proxy: str) -> dict[str, str]:
    """`host=proxy,host2=proxy2` -> dict. Пусто — берём исторический дефолт."""
    if not raw:
        return {"sgo.volganet.ru": volgograd_proxy} if volgograd_proxy else {}
    hosts: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        host, _, proxy = chunk.partition("=")
        host = host.strip()
        if not host:
            logger.warning("NETSCHOOL_PROXY_HOSTS: пропускаю запись без имени хоста: %r", chunk)
            continue
        hosts[host] = proxy.strip()
    return hosts


def load_settings(*, require_bot_token: bool = True) -> Settings:
    """Собрать и проверить настройки. Вызывается один раз на старте."""
    load_dotenv(BASE_DIR / ".env")
    data_dir = Path(_env("NETSCHOOL_DATA_DIR") or (BASE_DIR / "data")).resolve()
    # .env рядом с данными не перекрывает основной — только дополняет.
    load_dotenv(data_dir / ".env", override=False)

    bot_token = _env("NETSCHOOL_BOT_TOKEN") or _env("TG_BOT_TOKEN")
    if require_bot_token and not bot_token:
        raise SettingsError(
            "NETSCHOOL_BOT_TOKEN не задан — боту нечем подключиться к Telegram"
        )

    public_url = (_env("NETSCHOOL_PUBLIC_URL") or "https://ns.ikrya.ru").rstrip("/")
    miniapp_path = "/" + _env("NETSCHOOL_MINIAPP_PATH", "/mini/netschool").strip("/")

    # Секрета для подписи сессий здесь нет намеренно. Он был нужен Flask,
    # который подписывал им cookie сессии; в текущем веб-слое авторизация
    # построена на токенах мини-приложения, и подписанных сессий нет вовсе.
    # Требовать его — значит не пускать приложение стартовать из-за
    # настройки, которая ни на что не влияет.
    web_enabled = _env_bool("NETSCHOOL_WEB_ENABLED", True)

    check_interval = _env_int("CHECK_INTERVAL", 300)
    clamped = max(MIN_CHECK_INTERVAL, min(MAX_CHECK_INTERVAL, check_interval))
    if clamped != check_interval:
        # Слишком частые проверки банит уже школьный сервер, но это не повод
        # не запускать бота: подрезаем до допустимого и говорим об этом.
        logger.warning(
            "CHECK_INTERVAL=%s вне допустимых %s..%s — использую %s",
            check_interval, MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL, clamped,
        )
        check_interval = clamped

    return Settings(
        data_dir=data_dir,
        db_path=Path(_env("NETSCHOOL_DB_PATH") or (data_dir / "netschoolbot.sqlite3")),
        telegram=TelegramSettings(
            bot_token=bot_token,
            admin_id=_env_int("TG_ADMIN_ID", 0),
            log_bot_token=_env("LOG_BOT_TOKEN"),
            logging_enabled=_env_bool("TELEGRAM_LOGGING_ENABLED", True),
            api_proxy=_env("TELEGRAM_API_PROXY") or _env("ALL_PROXY") or _env("HTTPS_PROXY"),
        ),
        web=WebSettings(
            enabled=web_enabled,
            host=_env("NETSCHOOL_WEB_HOST", "127.0.0.1"),
            port=_env_int("NETSCHOOL_WEB_PORT", 8283),
            public_url=public_url,
            miniapp_path=miniapp_path,
            token_ttl=_env_int("NETSCHOOL_MINIAPP_TOKEN_TTL", 900),
            login_code_ttl=_env_int("NETSCHOOL_LOGIN_CODE_TTL", 600),
            cache_fresh_seconds=max(60, _env_int("NETSCHOOL_CACHE_FRESH_SECONDS", 3600)),
        ),
        push=PushSettings(
            public_key=_env("NETSCHOOL_VAPID_PUBLIC_KEY"),
            private_key=_env("NETSCHOOL_VAPID_PRIVATE_KEY"),
            subject=_env("NETSCHOOL_VAPID_SUBJECT", f"mailto:admin@{public_url.split('//')[-1]}"),
        ),
        netschool=NetSchoolSettings(
            default_check_interval=check_interval,
            session_ttl=_env_int("NETSCHOOL_SESSION_TTL", 1800),
            http_timeout=max(5, _env_int("NETSCHOOL_HTTP_TIMEOUT", 20)),
            blocked_host_ttl=max(60, _env_int("NETSCHOOL_BLOCKED_HOST_TTL", 600)),
            qr_login_ttl=max(15, _env_int("NETSCHOOL_QR_TTL", 60)),
            fallback_proxy=_env("NETSCHOOL_FALLBACK_PROXY"),
            proxy_hosts=_parse_proxy_hosts(
                _env("NETSCHOOL_PROXY_HOSTS"),
                _env("VOLGOGRAD_PROXY", "socks5://127.0.0.1:1080"),
            ),
        ),
        debug=_env_bool("DEBUG_MODE", False),
    )
