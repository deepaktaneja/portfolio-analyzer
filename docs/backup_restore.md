# Backup and Restore

## Quick (plain copy)
- Backup: copy `data/xirr.sqlite3` and `data/market_data.sqlite3`.
- Restore: replace files with backups while app is stopped.
- The reports module provides `backup_database`/`restore_database` helpers.

## Stage 10 — Encrypted, verified backups (recommended)
All databases can be backed up with Fernet encryption and integrity verification:

```bash
BACKUP_ENCRYPTION_KEY=<key-or-passphrase> python -m scripts.backup --dest-dir data/backups
```

- Creates `data/backups/<db>.<timestamp>.db.enc` plus a `.sha256` sidecar.
- The `backup_databases` scheduled job does this automatically when `BACKUP_ENCRYPTION_KEY` is set.

Restore (verifies before overwriting):

```bash
BACKUP_ENCRYPTION_KEY=<key-or-passphrase> python -m scripts.restore \
  data/backups/xirr.sqlite3.<ts>.db.enc data/xirr.sqlite3
```

Restore decrypts to a temp file, checks sha256 + `PRAGMA integrity_check`, then atomically replaces the
target. A corrupt or wrong-key backup is rejected — good data is never overwritten with bad data.

Covered databases: xirr, market_data, research, decisions, mf_analytics, scheduler, alerts, usage.
