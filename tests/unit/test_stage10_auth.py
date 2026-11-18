"""Unit tests for Stage 10 optional authentication & roles."""
from __future__ import annotations

import hashlib

from app.auth import auth_enabled, can, resolve_user, verify_password


def test_verify_password_plain_and_sha():
    h = hashlib.sha256(b"pw").hexdigest()
    assert verify_password("pw", "pw") is True
    assert verify_password("pw", f"sha256:{h}") is True
    assert verify_password("bad", "pw") is False


def test_roles_least_privilege():
    assert can("admin", "backup") is True
    assert can("editor", "run_jobs") is True
    assert can("viewer", "backup") is False
    assert can("viewer", "view") is True


def test_auth_disabled_by_default(monkeypatch):
    monkeypatch.delenv("APP_AUTH_ENABLED", raising=False)
    assert auth_enabled() is False


def test_env_user_resolution(monkeypatch):
    monkeypatch.setenv("APP_AUTH_ENABLED", "true")
    monkeypatch.setenv("APP_USERNAME", "alice")
    monkeypatch.setenv("APP_PASSWORD", "secret")
    rec = resolve_user("alice")
    assert rec is not None
    assert rec["username"] == "alice"
    assert verify_password("secret", rec["password"]) is True
