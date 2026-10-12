"""Пароли аккаунтов: scrypt из стандартной библиотеки, соль на каждый пароль."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**14, 8, 1
MIN_LENGTH = 8
MAX_LENGTH = 200


def check_password(password: str) -> str:
    """Почему пароль не подходит ("" — подходит)."""
    if len(password) < MIN_LENGTH:
        return f"Пароль — не короче {MIN_LENGTH} символов"
    if len(password) > MAX_LENGTH:
        return "Слишком длинный пароль"
    if password.isdigit() and len(set(password)) <= 2:
        return "Слишком простой пароль"
    return ""


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt, N, R, P)
    encode = lambda raw: base64.b64encode(raw).decode()  # noqa: E731
    return f"scrypt${N}${R}${P}${encode(salt)}${encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = _derive(password, base64.b64decode(salt), int(n), int(r), int(p))
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False
