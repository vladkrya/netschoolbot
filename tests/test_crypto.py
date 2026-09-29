"""Шифрование паролей в базе."""

import pytest
from cryptography.fernet import Fernet

from app.db.crypto import PasswordCipher, PasswordCipherError
from app.db.repositories import UserRepository

from .test_repositories import make_user

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()


async def raw_password(db, telegram_id=1) -> str:
    row = await db.fetch_one("SELECT password FROM users WHERE telegram_id = ?", (telegram_id,))
    return row["password"]


class TestPasswordCipher:
    def test_roundtrip(self):
        cipher = PasswordCipher(KEY)
        encrypted = cipher.encrypt("secret")
        assert encrypted != "secret" and cipher.is_encrypted(encrypted)
        assert cipher.decrypt(encrypted) == "secret"

    def test_empty_password_stays_empty(self):
        assert PasswordCipher(KEY).encrypt("") == ""

    def test_without_key_is_passthrough(self):
        cipher = PasswordCipher("")
        assert not cipher.enabled
        assert cipher.encrypt("secret") == "secret"
        assert cipher.decrypt("secret") == "secret"

    def test_encrypted_without_key_fails_loudly(self):
        encrypted = PasswordCipher(KEY).encrypt("secret")
        with pytest.raises(PasswordCipherError):
            PasswordCipher("").decrypt(encrypted)

    def test_wrong_key_fails_loudly(self):
        encrypted = PasswordCipher(KEY).encrypt("secret")
        with pytest.raises(PasswordCipherError):
            PasswordCipher(OTHER_KEY).decrypt(encrypted)

    def test_malformed_key_is_rejected(self):
        with pytest.raises(PasswordCipherError):
            PasswordCipher("не-ключ")

    def test_rotation_decrypts_with_old_key(self):
        old = PasswordCipher(OTHER_KEY).encrypt("secret")
        rotating = PasswordCipher(f"{KEY},{OTHER_KEY}")
        assert rotating.needs_rewrite(old)
        rewritten = rotating.rewrite(old)
        assert PasswordCipher(KEY).decrypt(rewritten) == "secret"


class TestEncryptedUserRepository:
    async def test_password_is_not_stored_in_plaintext(self, db):
        users = UserRepository(db, PasswordCipher(KEY))
        await users.save(make_user())
        assert "secret" not in await raw_password(db)
        loaded = await users.get(1)
        assert loaded.credentials.password == "secret"

    async def test_plaintext_passwords_are_encrypted_on_startup(self, db):
        await UserRepository(db).save(make_user())
        assert await raw_password(db) == "secret"

        users = UserRepository(db, PasswordCipher(KEY))
        # Старая запись читается и до перешифровки.
        assert (await users.get(1)).credentials.password == "secret"
        assert await users.encrypt_stored_passwords() == 1
        assert PasswordCipher.is_encrypted(await raw_password(db))
        assert (await users.get(1)).credentials.password == "secret"
        # Повторный запуск ничего не трогает.
        assert await users.encrypt_stored_passwords() == 0

    async def test_key_rotation_rewrites_passwords(self, db):
        await UserRepository(db, PasswordCipher(OTHER_KEY)).save(make_user())
        users = UserRepository(db, PasswordCipher(f"{KEY},{OTHER_KEY}"))
        assert await users.encrypt_stored_passwords() == 1
        only_new = UserRepository(db, PasswordCipher(KEY))
        assert (await only_new.get(1)).credentials.password == "secret"
