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
    redirect_bot_token: str = ""


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

    # Резервный адрес того же приложения: сервер, до которого доходят и
    # тогда, когда мобильный интернет урезан до белых списков.
    mirror_url: str = ""
    # Домен для вложений в установленном на iPhone приложении (PWA). Ссылку
    # на свой же домен iOS открывает внутри PWA, а там pptx и docx не
    # показать — только «Открыть в…». Ссылку на другой домен iOS открывает
    # во встроенном Safari: там «Просмотреть или загрузить» и компас
    # «Открыть в Safari». Пусто — берутся public_url и mirror_url.
    files_url: str = ""
    # KryaCaptcha перед отправкой отчёта об ошибке: адрес сервиса и сколько
    # раундов нужно пройти. Пустой адрес выключает кнопку.
    captcha_url: str = ""
    captcha_rounds: int = 2
    # Домен, привязанный к боту в BotFather (/setdomain), для Telegram Login
    # Widget. Telegram пускает виджет только с него, поэтому на зеркалах
    # кнопки нет. Пусто — вход через виджет выключен.
    login_widget_domain: str = ""
    # Бот, к которому этот домен привязан. У бота домен один, поэтому виджет
    # может жить на другом боте (резервном). Пусто — основной бот.
    login_widget_bot_token: str = ""
    # Вход через Telegram по OpenID Connect (новый «Log In With Telegram»).
    # Client ID и Secret — из @BotFather (бот → Login Widget); там же в
    # Allowed URLs адреса сайтов и адреса возврата
    # https://<домен><путь>/api/account/tg-oidc/callback. Кнопка — только на
    # доменах из tg_oidc_hosts: остальные Telegram не пустит.
    tg_oidc_client_id: str = ""
    tg_oidc_client_secret: str = ""
    tg_oidc_hosts: tuple[str, ...] = ()

    @property
    def tg_oidc_enabled(self) -> bool:
        return bool(self.tg_oidc_client_id and self.tg_oidc_client_secret and self.tg_oidc_hosts)

    @property
    def miniapp_url(self) -> str:
        return f"{self.public_url}{self.miniapp_path}"

    @property
    def attachment_hosts(self) -> list[str]:
        """Домены для вложений по порядку: PWA берёт первый не свой."""
        hosts = (self.files_url, self.public_url, self.mirror_url)
        return list(dict.fromkeys(host for host in hosts if host))

    @property
    def mirror_miniapp_url(self) -> str:
        return f"{self.mirror_url}{self.miniapp_path}" if self.mirror_url else ""


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
    # Секрет приложения NetSchool (`parent-mobile`) для получения токена
    # мобильного API. Пусто — режим «мобильное API» в /changeapi недоступен.
    mobile_client_secret: str = ""
    # host-подстрока -> прокси. Некоторые региональные серверы «Сетевого
    # города» отбивают запросы с datacenter-адресов.
    proxy_hosts: dict[str, str] = field(default_factory=dict)
    # Запасной прокси: через него идёт запрос, если основной не соединился
    # с сервером школы. Только при заданном основном.
    backup_proxy: str = ""
    # Выход yavds для проверок страницы статуса.
    status_yavds_proxy: str = ""
    # Вложения заданий скачиваются заранее и хранятся столько дней — чтобы
    # открывались, когда «Сетевой город» лежит. 0 — не скачивать.
    keep_attachments_days: int = 14
    # Предел места под них, МБ: сверх него удаляются самые старые.
    keep_attachments_mb: int = 3000

    def proxy_for(self, url: str) -> str | None:
        for host, proxy in self.proxy_hosts.items():
            if host != "*" and host in url:
                return proxy or None
        return self.proxy_hosts.get("*") or None


@dataclass(frozen=True, slots=True)
class AssistantSettings:
    """ИИ-помощник мини-приложения через OpenRouter. Без ключа он выключен."""

    api_key: str = ""
    model: str = "openrouter/free"
    base_url: str = "https://openrouter.ai/api/v1"
    proxy: str = ""
    # Сколько вопросов в сутки у одного пользователя: бесплатные модели
    # OpenRouter ограничены и по ключу, а не только по человеку.
    daily_limit: int = 30

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True, slots=True)
class SolverSettings:
    """Решения домашних заданий (solver/). Ключ и адрес — как у помощника,
    модель своя: задачи бесплатной моделью не решить. GPT-6 Luna — потому что
    Claude у Villnus читает формулы со сканов с ошибками («=» как «−»)."""

    model: str = "gpt-6-luna"
    # Кому видна кнопка «Решение», telegram_id через запятую. Пусто — никому.
    users: frozenset[int] = frozenset()
    daily_limit: int = 40

    def allowed(self, telegram_id: int) -> bool:
        return telegram_id in self.users


@dataclass(frozen=True, slots=True)
class MailSettings:
    """Почта для входа без Telegram: коды подтверждения и сброса пароля.
    Без хоста письма не отправляются — вход по почте работает и так."""

    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    sender: str = ""
    # starttls (587), ssl (465) или plain (локальный сервер без шифрования).
    security: str = "starttls"
    # Имя в сертификате сервера, если подключение идёт не по нему — например,
    # через SSH-туннель на 127.0.0.1. Пусто — сверяется с host.
    tls_name: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    db_path: Path
    telegram: TelegramSettings
    web: WebSettings
    push: PushSettings
    netschool: NetSchoolSettings
    debug: bool
    # Ключ(и) Fernet для паролей в базе, через запятую; пусто — без шифрования.
    secret_key: str = ""
    caldav_enabled: bool = True
    caldav_interval: int = 1800
    assistant: AssistantSettings = field(default_factory=AssistantSettings)
    mail: MailSettings = field(default_factory=MailSettings)
    solver: SolverSettings = field(default_factory=SolverSettings)

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def icons_dir(self) -> Path:
        return self.data_dir / "icons"

    @property
    def textbooks_dir(self) -> Path:
        return self.data_dir / "textbooks"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.logs_dir, self.attachments_dir, self.icons_dir):
            path.mkdir(parents=True, exist_ok=True)


def _parse_proxy_hosts(raw: str, default_proxy: str) -> dict[str, str]:
    """`host=proxy,*=proxy` -> dict; `*` задаёт прокси для остальных хостов.

    `default_proxy` (NETSCHOOL_PROXY) — прокси для всех школьных серверов;
    записи из списка его уточняют, `host=` — напрямую.
    """
    hosts: dict[str, str] = {"*": default_proxy} if default_proxy else {}
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
    # Пустой путь — приложение с корня домена. Иначе "/" давал бы "//" в ссылках.
    miniapp_path = _env("NETSCHOOL_MINIAPP_PATH").strip("/")
    miniapp_path = f"/{miniapp_path}" if miniapp_path else ""

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
            redirect_bot_token=_env("NETSCHOOL_REDIRECT_BOT_TOKEN"),
        ),
        web=WebSettings(
            enabled=web_enabled,
            host=_env("NETSCHOOL_WEB_HOST", "127.0.0.1"),
            port=_env_int("NETSCHOOL_WEB_PORT", 8283),
            public_url=public_url,
            mirror_url=_env("NETSCHOOL_MIRROR_URL").rstrip("/"),
            files_url=_env("NETSCHOOL_FILES_URL").rstrip("/"),
            captcha_url=_env("NETSCHOOL_CAPTCHA_URL", "https://captcha.ikrya.ru").rstrip("/"),
            captcha_rounds=max(1, _env_int("NETSCHOOL_CAPTCHA_ROUNDS", 2)),
            login_widget_domain=_env("NETSCHOOL_TG_LOGIN_DOMAIN").strip().lower(),
            login_widget_bot_token=_env("NETSCHOOL_TG_LOGIN_BOT_TOKEN"),
            tg_oidc_client_id=_env("NETSCHOOL_TG_OIDC_CLIENT_ID").strip(),
            tg_oidc_client_secret=_env("NETSCHOOL_TG_OIDC_CLIENT_SECRET").strip(),
            tg_oidc_hosts=tuple(
                host.strip().lower()
                for host in (_env("NETSCHOOL_TG_OIDC_HOSTS") or public_url.split("//")[-1].split("/")[0]).split(",")
                if host.strip()
            ),
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
            mobile_client_secret=_env("NETSCHOOL_MOBILE_CLIENT_SECRET"),
            proxy_hosts=_parse_proxy_hosts(
                _env("NETSCHOOL_PROXY_HOSTS"),
                _env("NETSCHOOL_PROXY"),
            ),
            backup_proxy=_env("NETSCHOOL_BACKUP_PROXY"),
            status_yavds_proxy=_env("NETSCHOOL_STATUS_YAVDS_PROXY"),
            keep_attachments_days=max(0, _env_int("NETSCHOOL_KEEP_ATTACHMENTS_DAYS", 14)),
            keep_attachments_mb=max(50, _env_int("NETSCHOOL_KEEP_ATTACHMENTS_MB", 3000)),
        ),
        debug=_env_bool("DEBUG_MODE", False),
        secret_key=_env("NETSCHOOL_SECRET_KEY"),
        caldav_enabled=_env_bool("NETSCHOOL_CALDAV_ENABLED", True),
        caldav_interval=max(300, _env_int("NETSCHOOL_CALDAV_INTERVAL", 1800)),
        assistant=AssistantSettings(
            api_key=_env("NETSCHOOL_ASSISTANT_API_KEY") or _env("OPENROUTER_API_KEY"),
            model=_env("NETSCHOOL_ASSISTANT_MODEL", "openrouter/free"),
            base_url=_env("NETSCHOOL_ASSISTANT_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            proxy=_env("NETSCHOOL_ASSISTANT_PROXY"),
            daily_limit=max(1, _env_int("NETSCHOOL_ASSISTANT_DAILY_LIMIT", 30)),
        ),
        mail=MailSettings(
            host=_env("MAIL_SMTP_HOST"),
            port=_env_int("MAIL_SMTP_PORT", 587),
            user=_env("MAIL_SMTP_USER"),
            password=_env("MAIL_SMTP_PASSWORD"),
            sender=_env("MAIL_FROM"),
            security=(_env("MAIL_SMTP_SECURITY") or "starttls").lower(),
            tls_name=_env("MAIL_SMTP_TLS_NAME"),
        ),
        solver=SolverSettings(
            model=_env("NETSCHOOL_SOLVER_MODEL", "gpt-6-luna"),
            users=frozenset(
                int(chunk) for chunk in _env("NETSCHOOL_SOLVER_USERS").replace(" ", "").split(",")
                if chunk.lstrip("-").isdigit()
            ),
            daily_limit=max(1, _env_int("NETSCHOOL_SOLVER_DAILY_LIMIT", 40)),
        ),
    )
