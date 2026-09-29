"""Шифрование паролей «Сетевого города» в базе.

Пароль нужен боту в исходном виде — им он заново входит в дневник, когда
сессия истекает, — поэтому хэш не подходит, только обратимое шифрование.
Ключ живёт в окружении (`NETSCHOOL_SECRET_KEY`), а не рядом с базой: копия
базы или бэкап каталога данных без ключа паролей не раскрывает.

Зашифрованное значение помечено префиксом, по нему отличаем его от пароля,
сохранённого до включения шифрования. Такие пароли читаются как есть и
перешифровываются при старте.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

PREFIX = "enc:v1:"


class PasswordCipherError(RuntimeError):
    pass


class PasswordCipher:
    """Fernet с поддержкой смены ключа.

    Ключей может быть несколько через запятую: шифруем первым, расшифровываем
    любым. Так ключ меняется без простоя — новый ставится первым, старый
    остаётся, пока все пароли не перешифруются.
    """

    def __init__(self, keys: str) -> None:
        parts = [k.strip() for k in keys.split(",") if k.strip()]
        self._fernet: MultiFernet | None = None
        self._rotating = len(parts) > 1
        if not parts:
            return
        try:
            self._fernet = MultiFernet([Fernet(k.encode()) for k in parts])
        except ValueError as exc:
            raise PasswordCipherError(
                "NETSCHOOL_SECRET_KEY не похож на ключ Fernet "
                "(32 байта в urlsafe base64, см. Fernet.generate_key())"
            ) from exc

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    @staticmethod
    def is_encrypted(value: str) -> bool:
        return value.startswith(PREFIX)

    def encrypt(self, value: str) -> str:
        if not value or self._fernet is None or self.is_encrypted(value):
            return value
        return PREFIX + self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        if not self.is_encrypted(value):
            return value
        if self._fernet is None:
            raise PasswordCipherError(
                "В базе зашифрованные пароли, а NETSCHOOL_SECRET_KEY не задан"
            )
        try:
            return self._fernet.decrypt(value[len(PREFIX):].encode()).decode()
        except InvalidToken as exc:
            raise PasswordCipherError(
                "Пароль в базе не расшифровывается текущим NETSCHOOL_SECRET_KEY"
            ) from exc

    def needs_rewrite(self, value: str) -> bool:
        """Открытый пароль при включённом шифровании или идёт смена ключа."""
        if not value or not self.enabled:
            return False
        return not self.is_encrypted(value) or self._rotating

    def rewrite(self, value: str) -> str:
        """Зашифровать заново текущим (первым) ключом."""
        return self.encrypt(self.decrypt(value))


NO_ENCRYPTION = PasswordCipher("")
