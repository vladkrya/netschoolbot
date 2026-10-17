"""Сборка приложения.

Единственное место, где создаются зависимости и связываются друг с другом.
Раньше их роль играл модуль `bot/runtime.py` с глобальными переменными,
которые импортировали и правили из десятка мест — из-за чего порядок
инициализации был неявным, а в тестах подменить что-либо было нельзя.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .caldav.service import CaldavService
    from .domain.models import User

from .db.crypto import PasswordCipher
from .db.engine import Database
from .db.repositories import (
    CacheRepository,
    FamilyRepository,
    MarkStateRepository,
    NotesRepository,
    MiniappRepository,
    SessionRepository,
    UserRepository,
)
from .netschool.service import DiaryService
from .netschool.session import SessionPool
from .accounts.mailer import Mailer
from .accounts.site_login import SiteLogins
from .accounts.repository import AccountRepository
from .chat.hub import ChatHub
from .notifications.watcher import Notifier, WatcherRegistry
from .settings import Settings

logger = logging.getLogger("netschoolbot")


@dataclass(slots=True)
class AppContext:
    """Всё, что нужно обработчикам бота и веба."""

    settings: Settings
    db: Database
    users: UserRepository
    state: MarkStateRepository
    sessions: SessionRepository
    miniapp: MiniappRepository
    cache: CacheRepository
    pool: SessionPool
    diary: DiaryService
    watchers: WatcherRegistry
    # Живые соединения приложения (WebSocket): чаты и мгновенные оценки.
    hub: ChatHub = field(default_factory=ChatHub)
    cache_refreshes: dict[tuple, asyncio.Task] = field(default_factory=dict)
    caldav: CaldavService | None = None
    login_tasks: dict[int, set[asyncio.Task]] = field(default_factory=dict)
    # Вопросы ИИ-помощнику за сутки: {telegram_id: (день, сколько)}.
    assistant_usage: dict[int, tuple] = field(default_factory=dict)
    # Вход по почте без Telegram и письма с кодами.
    accounts: AccountRepository | None = None
    mailer: Mailer | None = None
    # Незавершённые входы в «Сетевой город» с сайта: {telegram_id: попытка}.
    web_logins: dict[int, object] = field(default_factory=dict)
    # Входы на сайт с подтверждением в боте (accounts/site_login.py).
    site_logins: SiteLogins = field(default_factory=SiteLogins)
    # Второй родитель и свои заметки ученика.
    family: FamilyRepository | None = None
    notes: NotesRepository | None = None

    def __post_init__(self) -> None:
        if self.family is None:
            self.family = FamilyRepository(self.db)
        if self.notes is None:
            self.notes = NotesRepository(self.db)
        if self.accounts is None:
            self.accounts = AccountRepository(self.db)
        if self.mailer is None:
            self.mailer = Mailer(self.settings.mail, site=self.settings.web.miniapp_url)

    @classmethod
    async def create(cls, settings: Settings, notifier: Notifier | None = None) -> "AppContext":
        settings.ensure_dirs()

        # Правки чужой библиотеки применяются один раз и до первого запроса.
        from .netschool.patches import assignments, esia, http

        http.apply(
            timeout=settings.netschool.http_timeout,
            blocked_host_ttl=settings.netschool.blocked_host_ttl,
            fallback_proxy=settings.netschool.fallback_proxy,
            backup_proxy=settings.netschool.backup_proxy,
        )
        esia.apply()
        assignments.apply()

        db = Database(settings.db_path)
        await db.connect()

        cipher = password_cipher(settings)
        users = UserRepository(db, cipher)
        await _import_legacy_if_empty(settings, db, users)
        rewritten = await users.encrypt_stored_passwords()
        if rewritten:
            logger.info("Зашифровано паролей в базе: %s", rewritten)
        sessions = SessionRepository(db, cipher)
        await sessions.encrypt_stored_sessions()
        pool = SessionPool(settings.netschool, sessions)
        state = MarkStateRepository(db)
        diary = DiaryService(pool, users)

        return cls(
            settings=settings,
            db=db,
            users=users,
            state=state,
            sessions=sessions,
            miniapp=MiniappRepository(db, login_code_ttl=settings.web.login_code_ttl),
            cache=CacheRepository(db),
            pool=pool,
            diary=diary,
            watchers=WatcherRegistry(
                users=users, state=state, diary=diary, notifier=notifier,
                notes=NotesRepository(db),
            ),
        )

    async def shutdown(self) -> None:
        """Аккуратно закрыть всё, что открыли. Порядок важен."""
        await self.watchers.stop_all()
        await self.stop_cache_refreshes()
        await self.pool.close_all()
        await self.db.close()
        logger.info("Приложение остановлено")

    @asynccontextmanager
    async def login_attempt(self, telegram_id: int):
        task = asyncio.current_task()
        tasks = self.login_tasks.setdefault(telegram_id, set())
        tasks.add(task)
        try:
            yield
        finally:
            tasks.discard(task)
            if not tasks:
                self.login_tasks.pop(telegram_id, None)

    async def delete_user_data(self, telegram_id: int) -> None:
        logins = list(self.login_tasks.get(telegram_id, ()))
        for task in logins:
            task.cancel()
        if logins:
            await asyncio.gather(*logins, return_exceptions=True)
        await self.watchers.stop(telegram_id)
        tasks = [task for key, task in list(self.cache_refreshes.items()) if key[0] == telegram_id]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.caldav is not None:
            await self.caldav.delete_user(telegram_id)
        await self.pool.invalidate(telegram_id, forget_saved=True)
        await self.users.delete(telegram_id)

    async def sign_out_netschool(self, user: "User") -> None:
        """Выйти из «Сетевого города» (/logout в боте и выход в приложении).

        Каждый шаг перечислен явно: раньше часть чистки пряталась внутри
        чужих методов, и понять, что именно остаётся после выхода, было нельзя.
        """
        await self.watchers.stop(user.telegram_id)
        await self.pool.invalidate(user.telegram_id, forget_saved=True)
        # Отметки «выполнено», история оценок и слежение остаются до следующего
        # входа: тот же дневник продолжит с ними, другой их сотрёт (finish_login).
        if user.school.configured and user.selected_student_id is not None:
            await self.state.remember_diary(user.telegram_id, user.school.url, user.selected_student_id)
        await self.state.drop_outbox(user.telegram_id)
        await self.cache.clear(user.telegram_id)
        # Ссылку на приложение и подписку на календарь (CalDAV) не отзываем: без
        # ссылки приложение на телефоне показывало «Не удалось вас опознать», а с
        # ней само предлагает войти заново. Уроки из календаря уходят (ниже), а
        # после входа — в этот же аккаунт или в другой — появляется новое расписание.
        await self.users.save(
            replace(
                user,
                enabled=False,
                school=replace(user.school, url="", name=""),
                credentials=replace(user.credentials, login="", password=""),
                student_name="",
                selected_student_id=None,
                available_students=(),
            )
        )
        # Уже после сохранения: синхронизация, начатая до выхода, видит смену
        # школы и не вернёт старые уроки.
        if self.caldav is not None:
            try:
                await self.caldav.clear(user.telegram_id)
            except Exception:  # noqa: BLE001 — выход не должен падать из-за календаря
                logger.exception("Не удалось очистить календарь при выходе")

    async def stop_cache_refreshes(self) -> None:
        tasks = list(self.cache_refreshes.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cache_refreshes.clear()


def password_cipher(settings: Settings) -> PasswordCipher:
    cipher = PasswordCipher(settings.secret_key)
    if not cipher.enabled:
        logger.warning(
            "NETSCHOOL_SECRET_KEY не задан — пароли и сессии хранятся в базе открытым текстом"
        )
    return cipher


async def _import_legacy_if_empty(settings: Settings, db: Database, users: UserRepository) -> None:
    """Перенести данные старой версии, если база только что создана.

    Развёртывание автоматическое, а перенос вручную требует захода на сервер.
    Без этого после обновления бот поднялся бы с пустой базой, и все
    пользователи разом потеряли бы школу и настройки.

    Условие намеренно узкое: переносим, только если пользователей нет вообще
    и рядом лежат файлы старой версии. Повторно ничего не произойдёт.
    """
    if await db.fetch_one("SELECT 1 FROM app_metadata WHERE key = 'legacy_import_completed'"):
        return
    if await users.all_ids():
        await db.execute("INSERT OR REPLACE INTO app_metadata VALUES ('legacy_import_completed', '1')")
        return

    legacy_users_file = settings.data_dir / "netschool_users" / "netschool_users.json"
    if not legacy_users_file.exists():
        return

    from .db.import_legacy import LegacyImporter
    from .db.repositories import MarkStateRepository, MiniappRepository, SessionRepository

    logger.info("База пуста, а данные старой версии на месте — переношу")
    importer = LegacyImporter(
        settings.data_dir,
        users=users,
        state=MarkStateRepository(db),
        sessions=SessionRepository(db, password_cipher(settings)),
        miniapp=MiniappRepository(db, login_code_ttl=settings.web.login_code_ttl),
    )
    report = await importer.run()
    if await users.all_ids():
        await db.execute("INSERT OR REPLACE INTO app_metadata VALUES ('legacy_import_completed', '1')")
    for line in report.as_text().splitlines():
        logger.info("Перенос: %s", line)
