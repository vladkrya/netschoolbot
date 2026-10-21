"""Копия базы вне сервера: шифрование, нарезка на части и обратная сборка."""

import pytest

from app.tools import offsite
from app.tools.backup import BackupError
from tests.test_repositories import make_user


def test_encrypt_roundtrip_and_wrong_key():
    blob = offsite.encrypt(b"secret data", "pass")
    assert b"secret data" not in blob
    assert offsite.decrypt(blob, "pass") == b"secret data"
    with pytest.raises(BackupError):
        offsite.decrypt(blob, "other")
    with pytest.raises(BackupError):
        offsite.decrypt(b"junk" + blob, "pass")


def test_tampered_blob_is_rejected():
    blob = bytearray(offsite.encrypt(b"x" * 100, "pass"))
    blob[-1] ^= 1
    with pytest.raises(BackupError):
        offsite.decrypt(bytes(blob), "pass")


def test_empty_key_refused():
    with pytest.raises(BackupError):
        offsite.encrypt(b"x", "")


def test_split_respects_part_size():
    parts = offsite.split(b"a" * 25, size=10)
    assert [len(p) for p in parts] == [10, 10, 5]


async def test_pack_and_unpack_live_database(db, users, tmp_path, monkeypatch):
    await users.save(make_user())
    monkeypatch.setattr(offsite, "PART_SIZE", 1000)
    name, parts, count = offsite.pack(db._path, "pass")
    assert name.endswith(".sqlite3.gz.enc") and count == 1
    assert len(parts) > 1
    restored = tmp_path / "restored.sqlite3"
    restored.write_bytes(offsite.unpack(parts, "pass"))
    from app.tools.backup import verify_backup

    assert verify_backup(restored).users == 1
