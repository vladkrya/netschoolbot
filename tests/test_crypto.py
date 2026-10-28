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


class TestFileCipher:
    def test_roundtrip_and_legacy_plain_file(self):
        cipher = PasswordCipher(KEY)
        sealed = cipher.encrypt_bytes(b"pdf-data")
        assert b"pdf-data" not in sealed
        assert cipher.decrypt_bytes(sealed) == b"pdf-data"
        # Файл, сохранённый до включения шифрования, читается как есть.
        assert cipher.decrypt_bytes(b"old-plain") == b"old-plain"
        assert cipher.encrypt_bytes(sealed) == sealed

    def test_without_key_nothing_changes_but_sealed_file_is_refused(self):
        sealed = PasswordCipher(KEY).encrypt_bytes(b"x")
        assert PasswordCipher("").encrypt_bytes(b"x") == b"x"
        with pytest.raises(PasswordCipherError):
            PasswordCipher("").decrypt_bytes(sealed)
        with pytest.raises(PasswordCipherError):
            PasswordCipher(OTHER_KEY).decrypt_bytes(sealed)


class TestMiniappTokens:
    async def raw(self, db):
        return await db.fetch_all("SELECT token, token_enc, device, scope, hashed FROM miniapp_tokens")

    async def test_tokens_are_stored_hashed_and_reusable_ones_encrypted(self, db, users):
        from app.db.repositories import MiniappRepository, token_key

        miniapp = MiniappRepository(db, cipher=PasswordCipher(KEY))
        await users.save(make_user())
        shared = await miniapp.issue_token(1)
        device = await miniapp.issue_token(1, device=True)
        calendar = await miniapp.issue_calendar_token(1)
        stored = " ".join(" ".join(map(str, row)) for row in await self.raw(db))
        assert not any(token in stored for token in (shared, device, calendar))
        by_scope = {(row["device"], row["scope"]): row for row in await self.raw(db)}
        assert by_scope[(0, "")]["token"] == token_key(shared) and by_scope[(0, "")]["token_enc"]
        assert by_scope[(1, "")]["token"] == token_key(device) and by_scope[(1, "")]["token_enc"] == ""
        # Повторная выдача отдаёт те же ссылки, а вход по хэшу вместо токена не проходит.
        assert await miniapp.issue_token(1) == shared and await miniapp.issue_calendar_token(1) == calendar
        assert await miniapp.resolve_token(shared) == 1 and await miniapp.resolve_token(token_key(shared)) is None
        assert await miniapp.resolve_calendar_token(calendar) == 1

    async def test_plain_tokens_are_converted_once(self, db, users):
        from app.db.repositories import MiniappRepository

        miniapp = MiniappRepository(db, cipher=PasswordCipher(KEY))
        await users.save(make_user())
        for token, device, scope in (("plain-shared", 0, ""), ("plain-device", 1, ""), ("plain-cal", 0, "calendar")):
            await db.execute("INSERT INTO miniapp_tokens (token, telegram_id, device, scope) VALUES (?,?,?,?)",
                             (token, 1, device, scope))
        await db.execute("INSERT INTO install_codes (code, telegram_id, kind, expires_at, token) VALUES "
                         "('c', 1, 'device', 9e9, 'plain-device')")
        assert await miniapp.protect_tokens() == 3 and await miniapp.protect_tokens() == 0
        stored = " ".join(" ".join(map(str, row)) for row in await self.raw(db))
        assert "plain-" not in stored
        assert (await db.fetch_one("SELECT token FROM install_codes"))["token"] != "plain-device"
        assert [await miniapp.resolve_token("plain-shared"), await miniapp.resolve_token("plain-device")] == [1, 1]
        assert await miniapp.is_device_token("plain-device")
        # Выданные до шифрования ссылки остаются прежними.
        assert await miniapp.issue_token(1) == "plain-shared" and await miniapp.issue_calendar_token(1) == "plain-cal"
