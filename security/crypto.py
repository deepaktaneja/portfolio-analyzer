"""Symmetric encryption helpers (Fernet) with PBKDF2 key derivation.

Used to encrypt backups and at-rest secrets. Keys are never logged. If the
``cryptography`` package is unavailable, callers receive a clear RuntimeError
rather than silently storing plaintext.
"""
from __future__ import annotations

import base64
import os

try:  # cryptography is an optional-but-preferred dependency
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    _HAVE_CRYPTO = True
except Exception:  # noqa: BLE001
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore
    _HAVE_CRYPTO = False

DEFAULT_SALT = b"portfolio-analyzer-stage10"
_ITERATIONS = 200_000


def _require():
    if not _HAVE_CRYPTO:
        raise RuntimeError("cryptography package is required for encryption features")


def derive_key(passphrase: str | bytes, salt: bytes = DEFAULT_SALT) -> str:
    """Derive a Fernet-compatible key (url-safe base64) from a passphrase."""
    _require()
    if isinstance(passphrase, str):
        passphrase = passphrase.encode("utf-8")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(passphrase)).decode("ascii")


def _fernet(key: str | bytes):
    _require()
    if isinstance(key, str):
        key = key.encode("utf-8")
    return Fernet(key)


def encrypt_bytes(data: bytes, key: str | bytes) -> bytes:
    _require()
    return _fernet(key).encrypt(data)


def decrypt_bytes(token: bytes, key: str | bytes) -> bytes:
    _require()
    try:
        return _fernet(key).decrypt(token)
    except InvalidToken as exc:  # wrong key or corrupt data
        raise ValueError("decryption failed: wrong key or corrupt data") from exc


def encrypt_text(text: str, key: str | bytes) -> str:
    return encrypt_bytes(text.encode("utf-8"), key).decode("ascii")


def decrypt_text(token: str | bytes, key: str | bytes) -> str:
    if isinstance(token, str):
        token = token.encode("ascii")
    return decrypt_bytes(token, key).decode("utf-8")


def generate_key() -> str:
    """Generate a fresh Fernet key (for BACKUP_ENCRYPTION_KEY)."""
    _require()
    return Fernet.generate_key().decode("ascii")


def key_from_env(value: str | None = None) -> str | None:
    """Accept either a Fernet key (44-char base64) or a passphrase; derive if needed."""
    raw = value if value is not None else os.environ.get("BACKUP_ENCRYPTION_KEY")
    if not raw:
        return None
    raw = raw.strip()
    if len(raw) == 44 and raw.endswith("="):
        return raw  # already a Fernet key
    return derive_key(raw)
