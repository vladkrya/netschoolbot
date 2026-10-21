"""Зашифрованная копия базы вне сервера — админу бота в Telegram.

    python -m app.tools.offsite                     # снять копию и отправить
    python -m app.tools.offsite --decrypt a.part1 a.part2 -o restored.sqlite3

Все обычные копии (app.tools.backup) лежат в data/backups на том же диске,
что и база: умрёт сервер — пропадут и они. Раз в сутки (таймер
netschoolbot-offsite) свежая копия сжимается, шифруется AES-256-GCM и уходит
админу документом. Telegram-боту нельзя слать файлы больше 50 МБ, поэтому
большая копия режется на части по 45 МБ; при расшифровке части передаются
в том же порядке.

Ключ — NETSCHOOL_BACKUP_KEY в .env, любая строка-пароль; из неё и случайной
соли scrypt выводит 256-битный ключ. Храните пароль ещё где-нибудь кроме
сервера: без него копия бесполезна. Ключа шифрования паролей SGO
(NETSCHOOL_SECRET_KEY) в копии нет, как и в обычной.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import gzip
import hashlib
import os
import sys
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..settings import load_settings
from .backup import BackupError, _count_users, make_backup, verify_backup

MAGIC = b"NSBK1"
SALT_SIZE = 16
NONCE_SIZE = 12
PART_SIZE = 45 * 1024 * 1024
KEY_ENV = "NETSCHOOL_BACKUP_KEY"


def derive_key(password: str, salt: bytes) -> bytes:
    if not password:
        raise BackupError(f"{KEY_ENV} не задан")
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def encrypt(data: bytes, password: str) -> bytes:
    """MAGIC | соль | nonce | шифртекст с тегом. Заголовок — associated data."""
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    header = MAGIC + salt + nonce
    return header + AESGCM(derive_key(password, salt)).encrypt(nonce, data, header)


def decrypt(blob: bytes, password: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise BackupError("Это не зашифрованная копия бота")
    head = len(MAGIC)
    salt = blob[head:head + SALT_SIZE]
    nonce = blob[head + SALT_SIZE:head + SALT_SIZE + NONCE_SIZE]
    header = blob[:head + SALT_SIZE + NONCE_SIZE]
    try:
        return AESGCM(derive_key(password, salt)).decrypt(nonce, blob[len(header):], header)
    except InvalidTag as exc:
        raise BackupError("Не тот ключ или копия повреждена") from exc


def split(blob: bytes, size: int | None = None) -> list[bytes]:
    size = size or PART_SIZE
    return [blob[i:i + size] for i in range(0, len(blob), size)] or [b""]


def pack(db_path: Path, password: str, *, now: dt.datetime | None = None) -> tuple[str, list[bytes], int]:
    """Снять копию, проверить, сжать и зашифровать. Возвращает имя, части и число пользователей."""
    now = now or dt.datetime.now()
    live_users = _count_users(db_path)
    with tempfile.TemporaryDirectory(prefix="netschoolbot-offsite-") as tmp:
        path = make_backup(db_path, Path(tmp), now=now)
        report = verify_backup(path, expect_users=live_users)
        raw = path.read_bytes()
    blob = encrypt(gzip.compress(raw, compresslevel=6), password)
    return f"netschoolbot-{now:%Y%m%d-%H%M}.sqlite3.gz.enc", split(blob), report.users


def unpack(parts: list[bytes], password: str) -> bytes:
    return gzip.decompress(decrypt(b"".join(parts), password))


async def send(name: str, parts: list[bytes], users: int) -> None:
    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.types import BufferedInputFile

    settings = load_settings()
    if not settings.telegram.admin_id:
        raise BackupError("TG_ADMIN_ID не задан — отправлять некому")
    proxy = settings.telegram.api_proxy
    bot = Bot(settings.telegram.bot_token, session=AiohttpSession(proxy=proxy) if proxy else None)
    total = len(parts)
    try:
        for index, part in enumerate(parts, 1):
            filename = name if total == 1 else f"{name}.part{index}"
            caption = None
            if index == 1:
                size = sum(len(p) for p in parts)
                caption = (
                    f"Копия базы · {size // 1024} КБ · пользователей {users}"
                    + (f" · частей {total}" if total > 1 else "")
                    + "\nРасшифровать: python -m app.tools.offsite --decrypt <файлы по порядку> -o база.sqlite3"
                )
            await bot.send_document(
                settings.telegram.admin_id,
                BufferedInputFile(part, filename=filename),
                caption=caption,
                disable_notification=True,
            )
    finally:
        await bot.session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Зашифрованная копия базы админу в Telegram")
    parser.add_argument("--decrypt", type=Path, nargs="+", help="части копии по порядку")
    parser.add_argument("-o", "--output", type=Path, help="куда записать расшифрованную базу")
    args = parser.parse_args(argv)

    # Читает .env — ключ оттуда же, если не задан в окружении.
    settings = load_settings(require_bot_token=False)
    password = os.environ.get(KEY_ENV, "")
    try:
        if args.decrypt:
            if not args.output:
                parser.error("--decrypt требует -o")
            data = unpack([p.read_bytes() for p in args.decrypt], password)
            args.output.write_bytes(data)
            report = verify_backup(args.output)
            print(f"OK {args.output} · пользователей {report.users} · схема {report.schema}")
            return 0
        name, parts, users = pack(settings.db_path, password)
        asyncio.run(send(name, parts, users))
    except BackupError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    print(f"OK {name} · частей {len(parts)} · пользователей {users}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
