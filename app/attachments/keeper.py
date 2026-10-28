"""Вложения заданий — на диск заранее, чтобы открывались при простое СГО.

Раньше файл скачивался, только когда его открыли, и хранился час: в простой
«Сетевого города» открывалось лишь то, что кто-то смотрел в последний час. А
файлы к домашке нужны как раз вечером накануне, когда сервер чаще всего лежит.

Теперь проверка оценок, увидев задания, отдаёт их сюда; файлы к заданиям на
14 дней назад и вперёд скачиваются в фоне — по одному, с паузой, чтобы не нагружать
школьный сервер, — и хранятся `keep_attachments_days` дней (по умолчанию 14).
Файл к заданию у всего класса общий, поэтому на диске он один на сервер
школы, режим API и номер вложения; отдаётся только тем, у кого это вложение
было в заданиях (kept_attachment_access).

Сохранение включено у всех; выключить можно тумблером в настройках (PREF
в web_prefs). Уже сохранённое открывается и после выключения.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ..db.crypto import NO_ENCRYPTION, FILE_MAGIC, PasswordCipher
from ..netschool.errors import NetSchoolError, Reason

if TYPE_CHECKING:
    from ..db.repositories import KeptAttachmentRepository
    from ..domain.models import User
    from ..domain.records import HomeworkRecord

logger = logging.getLogger("netschoolbot.attachments")

# Задания со сроком в пределах двух недель назад и вперёд от сегодня.
DUE_FROM_DAYS = -14
DUE_TO_DAYS = 14
# Файлы больше этого не храним: видео и архивы съели бы весь диск.
MAX_FILE_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 100
# Скачиваем по одному на весь бот и с паузой: это фоновая работа, школьный
# сервер не должен её заметить.
PAUSE_S = 1.0
PRUNE_EVERY_S = 3600
# Личный тумблер — в настройках приложения (web_prefs).
PREF = "keep_attachments"


# Имя файла в _path: отпечаток сервера школы, режим API, номер вложения.
_KEPT_NAME = re.compile(r"[0-9a-f]{12}-[a-z]+-\d+")


def _host(url: str) -> str:
    return urlparse(url).hostname or url


class AttachmentKeeper:
    def __init__(self, repo: KeptAttachmentRepository, diary, root: Path | Callable[[], Path], *, days: int = 14,
                 max_bytes: int = 3000 * 1024 * 1024, clock: Callable[[], float] = time.time,
                 wanted: Callable[[User], Awaitable[bool]] | None = None,
                 cipher: PasswordCipher = NO_ENCRYPTION) -> None:
        self._repo = repo
        # Файлы на диске шифруются тем же ключом, что пароли: копия каталога без ключа ничего не раскрывает.
        self._cipher = cipher
        # Включено ли сохранение у человека (доступ и тумблер); None — у всех.
        self._wanted = wanted
        self._diary = diary
        self._root = root
        self._ttl = days * 86400
        self._max_bytes = max_bytes
        self._clock = clock
        self._semaphore = asyncio.Semaphore(1)
        self._running: dict[int, asyncio.Task] = {}
        # Что скачать не вышло по своей вине (слишком большое, нет файла) —
        # не пробуем снова до перезапуска.
        self._skip: set[tuple[str, str, int]] = set()
        self._pruned = 0.0

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    def _key(self, user: User, attachment_id: int) -> tuple[str, str, int]:
        return _host(user.school.url), user.effective_api_mode.value, attachment_id

    def _path(self, key: tuple[str, str, int]) -> Path:
        host, mode, attachment_id = key
        digest = hashlib.sha1(host.encode()).hexdigest()[:12]
        root = self._root() if callable(self._root) else self._root
        return root / f"{digest}-{mode}-{attachment_id}"

    # ───────────────────────────── отдача ─────────────────────────────

    async def get(self, user: User, attachment_id: int) -> bytes | None:
        """Сохранённая копия, если она есть и этому человеку положена."""
        if not self.enabled:
            return None
        key = self._key(user, attachment_id)
        row = await self._repo.saved(*key)
        if row is None or self._clock() - row["saved_at"] > self._ttl:
            return None
        if not await self._repo.allowed(user.telegram_id, *key):
            return None
        try:
            return self._cipher.decrypt_bytes(self._path(key).read_bytes())
        except OSError:
            return None

    async def store(self, user: User, attachment_id: int, payload: bytes) -> None:
        """Файл, который человек только что открыл, — тоже в копию."""
        if not self.enabled or len(payload) > MAX_FILE_BYTES:
            return
        key = self._key(user, attachment_id)
        try:
            if self._wanted is not None and not await self._wanted(user):
                return
            if not await self._saved(key):
                await self._save(key, payload)
            await self._repo.grant(user.telegram_id, key[0], key[1], [attachment_id])
        except Exception as exc:  # noqa: BLE001 — копия вторична
            logger.debug("Вложение %s не сохранено: %s", attachment_id, exc)

    # ───────────────────────────── заранее ─────────────────────────────

    def offer(self, user: User, homework: Iterable[HomeworkRecord]) -> None:
        """Проверка оценок увидела задания: скачать их файлы в фоне. Не ждёт."""
        if not self.enabled or not user.school.url:
            return
        today = dt.date.today()
        ids = sorted({
            attachment.id
            for record in homework
            if DUE_FROM_DAYS <= (record.due_date - today).days <= DUE_TO_DAYS
            for attachment in record.attachments
            if attachment.id is not None
        })
        if not ids or user.telegram_id in self._running:
            return
        task = asyncio.create_task(self._keep(user, ids), name=f"keep-attachments-{user.telegram_id}")
        self._running[user.telegram_id] = task
        task.add_done_callback(lambda _: self._running.pop(user.telegram_id, None))

    async def _keep(self, user: User, ids: list[int]) -> None:
        host, mode = _host(user.school.url), user.effective_api_mode.value
        try:
            if self._wanted is not None and not await self._wanted(user):
                return
            # Уже скачанное кем-то из класса — просто открываем доступ.
            await self._repo.grant(user.telegram_id, host, mode, ids)
            saved = 0
            for attachment_id in ids:
                key = (host, mode, attachment_id)
                async with self._semaphore:
                    # Проверяем внутри блокировки: пока ждали, файл мог
                    # сохранить одноклассник или открывший его пользователь.
                    if key in self._skip or await self._saved(key):
                        continue
                    try:
                        payload = await asyncio.wait_for(
                            self._diary.download_attachment(user, attachment_id), DOWNLOAD_TIMEOUT_S)
                    except asyncio.CancelledError:
                        raise
                    except (asyncio.TimeoutError, NetSchoolError) as exc:
                        if isinstance(exc, asyncio.TimeoutError) or exc.reason.is_retryable \
                                or exc.reason is Reason.AUTH:
                            # Сервер лежит или сессия кончилась — до следующей проверки.
                            logger.info("Вложения %s: скачивание отложено (%s)", user.telegram_id,
                                        type(exc).__name__)
                            return
                        self._skip.add(key)
                        continue
                    except Exception as exc:  # noqa: BLE001 — один файл не мешает остальным
                        logger.info("Вложение %s не скачано: %s", attachment_id, type(exc).__name__)
                        self._skip.add(key)
                        continue
                    if len(payload) > MAX_FILE_BYTES:
                        self._skip.add(key)
                        continue
                    await self._save(key, payload)
                    saved += 1
                    await asyncio.sleep(PAUSE_S)
            if saved:
                logger.info("Вложения заданий %s: сохранено %s", user.telegram_id, saved)
            await self.prune()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — фоновая работа не роняет проверку
            logger.warning("Сохранение вложений %s не удалось: %s", user.telegram_id, exc)

    async def _saved(self, key: tuple[str, str, int]) -> bool:
        row = await self._repo.saved(*key)
        return (row is not None and self._clock() - row["saved_at"] <= self._ttl
                and self._path(key).exists())

    async def _save(self, key: tuple[str, str, int], payload: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        partial.write_bytes(self._cipher.encrypt_bytes(payload))
        partial.replace(path)
        await self._repo.add(*key, len(payload), self._clock())

    # ───────────────────────────── шифрование ─────────────────────────────

    def _protect_files(self) -> int:
        root = self._root() if callable(self._root) else self._root
        if not self._cipher.enabled or not root.is_dir():
            return 0
        done = 0
        for path in root.iterdir():
            if not path.is_file() or not _KEPT_NAME.fullmatch(path.name):
                continue
            try:
                with path.open("rb") as handle:
                    if handle.read(len(FILE_MAGIC)) == FILE_MAGIC:
                        continue
                partial = path.with_name(path.name + ".partial")
                partial.write_bytes(self._cipher.encrypt_bytes(path.read_bytes()))
                partial.replace(path)
                done += 1
            except OSError as exc:
                logger.warning("Файл %s не зашифрован: %s", path.name, exc)
        return done

    async def protect_files(self) -> int:
        """Зашифровать файлы, сохранённые до включения шифрования. Идемпотентно;
        работает в потоке — файлов много, а бот в это время отвечает."""
        done = await asyncio.to_thread(self._protect_files)
        if done:
            logger.info("Вложения заданий: зашифровано файлов %s", done)
        return done

    # ───────────────────────────── уборка ─────────────────────────────

    async def prune(self, *, force: bool = False) -> None:
        """Убрать старше срока и самое старое сверх предела места."""
        now = self._clock()
        if not force and now - self._pruned < PRUNE_EVERY_S:
            return
        self._pruned = now
        gone = [(r["host"], r["mode"], r["attachment_id"]) for r in await self._repo.expired(now - self._ttl)]
        total = await self._repo.total_size()
        if total > self._max_bytes:
            for row in await self._repo.oldest(500):
                key = (row["host"], row["mode"], row["attachment_id"])
                if key not in gone:
                    gone.append(key)
                    total -= row["size"]
                if total <= self._max_bytes:
                    break
        for key in gone:
            self._path(key).unlink(missing_ok=True)
        if gone:
            await self._repo.remove(gone)
            logger.info("Вложения заданий: удалено %s старых", len(gone))
