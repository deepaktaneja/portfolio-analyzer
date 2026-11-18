"""Encrypted, verified SQLite backups with a tested restore path.

Backups are Fernet-encrypted files plus a ``.sha256`` sidecar for integrity.
Restore decrypts to a temp file, runs ``PRAGMA integrity_check`` and atomically
replaces the target only if the backup verifies — never overwrites with corrupt data.
"""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from security.crypto import decrypt_bytes, derive_key, encrypt_bytes

DEFAULT_DB_PATHS = [
    "data/xirr.sqlite3",
    "data/market_data.sqlite3",
    "data/research.sqlite3",
    "data/decisions.sqlite3",
    "data/mf_analytics.sqlite3",
    "data/scheduler.sqlite3",
    "data/alerts.sqlite3",
]


def _normalize_key(key: str) -> str:
    k = str(key).strip()
    if len(k) == 44 and k.endswith("="):
        return k  # already a Fernet key
    return derive_key(k)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def backup_database(db_path: str | Path, dest_dir: str | Path, key: str,
                    now: datetime | None = None) -> dict:
    """Encrypt a single SQLite DB into ``dest_dir``. Returns metadata (or status=missing)."""
    src = Path(db_path)
    if not src.exists():
        return {"db": str(src), "status": "missing"}
    data = src.read_bytes()
    fkey = _normalize_key(key)
    token = encrypt_bytes(data, fkey)
    ts = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S")
    out_dir = Path(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{src.name}.{ts}.db.enc"
    out.write_bytes(token)
    digest = _sha(data)
    out.with_suffix(out.suffix + ".sha256").write_text(digest, encoding="utf-8")
    return {"db": str(src), "backup": str(out), "sha256": digest, "size": len(data),
            "status": "ok", "created_at": (now or datetime.now(UTC)).isoformat()}


def _sqlite_integrity(path: Path) -> bool:
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def verify_backup(enc_path: str | Path, key: str, check_sqlite: bool = True) -> dict:
    """Decrypt a backup and verify integrity (hash sidecar + optional SQLite check)."""
    enc = Path(enc_path)
    if not enc.exists():
        return {"ok": False, "reason": "backup file not found"}
    try:
        data = decrypt_bytes(enc.read_bytes(), _normalize_key(key))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"decrypt failed: {exc}"}
    digest = _sha(data)
    sidecar = enc.with_suffix(enc.suffix + ".sha256")
    if sidecar.exists():
        expected = sidecar.read_text(encoding="utf-8").strip()
        if expected and expected != digest:
            return {"ok": False, "reason": "sha256 mismatch", "sha256": digest}
    if check_sqlite:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            if not _sqlite_integrity(Path(tmp.name)):
                return {"ok": False, "reason": "sqlite integrity_check failed", "sha256": digest}
    return {"ok": True, "sha256": digest, "size": len(data)}


def restore_database(enc_path: str | Path, target_path: str | Path, key: str,
                     verify: bool = True) -> dict:
    """Restore an encrypted backup to ``target_path`` after verification."""
    enc = Path(enc_path)
    if verify:
        check = verify_backup(enc, key)
        if not check.get("ok"):
            return {"restored": False, "reason": check.get("reason", "verification failed")}
    data = decrypt_bytes(enc.read_bytes(), _normalize_key(key))
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=str(target.parent), suffix=".restore", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    tmp_path.replace(target)
    return {"restored": True, "target": str(target), "sha256": _sha(data), "size": len(data)}


def backup_all(db_paths: list[str] | None = None, dest_dir: str | Path = "data/backups",
               key: str | None = None, now: datetime | None = None) -> list[dict]:
    """Back up all provided (or default) databases; returns metadata for created backups."""
    if not key:
        raise ValueError("encryption key is required for backups")
    results = []
    for p in (db_paths or DEFAULT_DB_PATHS):
        res = backup_database(p, dest_dir, key, now=now)
        if res.get("status") == "ok":
            results.append(res)
    return results
