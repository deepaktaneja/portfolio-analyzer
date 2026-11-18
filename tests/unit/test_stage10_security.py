"""Unit tests for Stage 10 security (encryption, redaction, encrypted backups)."""
from __future__ import annotations

import sqlite3

import pytest

from security.backup import backup_database, restore_database, verify_backup
from security.crypto import decrypt_text, derive_key, encrypt_text
from security.redaction import redact_secret, redact_text


def test_crypto_roundtrip():
    key = derive_key("correct horse battery staple")
    token = encrypt_text("sensitive portfolio value", key)
    assert token != "sensitive portfolio value"
    assert decrypt_text(token, key) == "sensitive portfolio value"


def test_wrong_key_rejected():
    token = encrypt_text("data", derive_key("key-one"))
    with pytest.raises(ValueError):
        decrypt_text(token, derive_key("key-two"))


def test_redact_text_masks_secrets_and_emails():
    raw = "api_key=ABC1234567890 email alice@example.com sk-abcdefghijklmnop"
    out = redact_text(raw)
    assert "[REDACTED]" in out
    assert "alice@example.com" not in out
    assert "***@example.com" in out


def test_redact_secret_keeps_tail():
    masked = redact_secret("supersecret", keep_last=3)
    assert masked.endswith("ret")
    assert masked.startswith("*")


def _make_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()


def test_backup_verify_restore(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    meta = backup_database(str(db), str(tmp_path / "bk"), "passphrase-123")
    assert meta["status"] == "ok"
    assert verify_backup(meta["backup"], "passphrase-123")["ok"] is True

    db.write_bytes(b"corrupted")
    res = restore_database(meta["backup"], str(db), "passphrase-123")
    assert res["restored"] is True
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT x FROM t").fetchone()[0] == 42
    conn.close()


def test_corrupt_backup_rejected(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    meta = backup_database(str(db), str(tmp_path / "bk"), "passphrase-123")
    bad = tmp_path / "bk" / "tampered.db.enc"
    bad.write_bytes(meta["backup"].encode() if isinstance(meta["backup"], bytes) else b"not-a-valid-token")
    assert verify_backup(str(bad), "passphrase-123")["ok"] is False


def test_wrong_key_restore_does_not_overwrite(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    meta = backup_database(str(db), str(tmp_path / "bk"), "right-key")
    db.write_text("SENTINEL", encoding="utf-8")
    res = restore_database(meta["backup"], str(db), "wrong-key")
    assert res["restored"] is False
    assert db.read_text(encoding="utf-8") == "SENTINEL"
