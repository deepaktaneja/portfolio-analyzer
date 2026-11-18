"""Optional authentication & least-privilege roles (env-gated, default OFF).

Authentication is disabled unless ``APP_AUTH_ENABLED`` is truthy, so local and
test usage is unaffected. When enabled, a simple username/password gate protects
the app and role permissions constrain sensitive actions (backups, job runs).
Passwords may be plain (dev only) or ``sha256:<hex>`` — never logged.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

import yaml

ROLE_PERMISSIONS = {
    "admin": {"view", "run_jobs", "backup", "settings", "ack_alerts"},
    "editor": {"view", "run_jobs", "ack_alerts"},
    "viewer": {"view"},
}

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "auth.yaml"


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def load_auth_config() -> dict:
    cfg: dict = {"enabled": False, "roles": ROLE_PERMISSIONS, "users": []}
    if CONFIG_PATH.exists():
        try:
            cfg.update(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {})
        except Exception:  # noqa: BLE001, S110
            pass
    # Env overrides
    if os.getenv("APP_AUTH_ENABLED") is not None:
        cfg["enabled"] = _truthy(os.getenv("APP_AUTH_ENABLED"))
    return cfg


def auth_enabled() -> bool:
    return bool(load_auth_config().get("enabled"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_password(plain: str, stored: str) -> bool:
    """Constant-time verification of a plain password against a stored value."""
    if not stored:
        return False
    if stored.startswith("sha256:"):
        return hmac.compare_digest(_sha256(plain), stored.split(":", 1)[1])
    return hmac.compare_digest(plain, stored)


def resolve_user(username: str) -> dict | None:
    """Look up a user from env (single-user) or config, returning role info."""
    env_user = os.getenv("APP_USERNAME")
    env_pw = os.getenv("APP_PASSWORD") or os.getenv("APP_PASSWORD_SHA256")
    if env_user and env_pw and username == env_user:
        pw = env_pw if not os.getenv("APP_PASSWORD_SHA256") else f"sha256:{os.getenv('APP_PASSWORD_SHA256')}"
        return {"username": env_user, "password": pw, "role": os.getenv("APP_ROLE", "admin")}
    cfg = load_auth_config()
    if env_user:  # env credentials take precedence; ignore file users
        return None
    for u in cfg.get("users") or []:
        if u.get("username") == username:
            return u
    return None


def can(role: str, action: str) -> bool:
    return action in ROLE_PERMISSIONS.get(role, set())


def require_auth() -> None:
    """Streamlit gate. No-op unless auth is enabled."""
    if not auth_enabled():
        return
    import streamlit as st

    if st.session_state.get("_authenticated"):
        return

    st.title("🔐 Sign in")
    with st.form("login"):
        user = st.text_input("Username")
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        rec = resolve_user(user)
        if rec and verify_password(pw, rec.get("password", "")):
            st.session_state["_authenticated"] = True
            st.session_state["_role"] = rec.get("role", "viewer")
            st.rerun()
        else:
            st.error("Invalid credentials.")
    st.stop()


def current_role() -> str:
    if not auth_enabled():
        return "admin"
    import streamlit as st
    return st.session_state.get("_role", "viewer")


def can_current(action: str) -> bool:
    return can(current_role(), action)
