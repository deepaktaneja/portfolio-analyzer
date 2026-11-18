"""Copilot context: a lightweight, read-only portfolio snapshot for tool execution.

Built from the existing :class:`AppContext`. Equity positions come straight from the
holdings DataFrame; MF positions are best-effort (guarded) so the copilot works even
when Streamlit/MF data is unavailable (e.g. in tests or offline).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.context import AppContext


@dataclass
class CopilotContext:
    app_ctx: AppContext
    positions: list[dict] = field(default_factory=list)
    total_value: float = 0.0
    targets: dict = field(default_factory=dict)
    mf: dict = field(default_factory=dict)
    holdings_df: object = None
    txs: list = field(default_factory=list)
    price_lookup: object = None
    end_date: object = None
    names: dict[str, str] = field(default_factory=dict)

    @property
    def security_ids(self) -> list[str]:
        return [p["security_id"] for p in self.positions if p.get("security_id")]

    def resolve_security(self, token: str | None) -> str | None:
        """Resolve a free-text token to a security_id (ISIN or name substring)."""
        if not token:
            return None
        t = str(token).strip().upper()
        ids = self.security_ids
        if t in ids:
            return t
        # exact name match
        for sid, name in self.names.items():
            if t == str(name).strip().upper():
                return sid
        # substring on name or id
        for sid in ids:
            name = str(self.names.get(sid, "")).upper()
            if t and (t in name or t in sid):
                return sid
        return None

    def position(self, sid: str) -> dict | None:
        for p in self.positions:
            if p.get("security_id") == sid:
                return p
        return None

    def weight_pct(self, sid: str) -> float:
        p = self.position(sid)
        if not p or self.total_value <= 0:
            return 0.0
        return float(p.get("value") or 0) / self.total_value * 100.0


def _equity_positions(holdings) -> list[dict]:
    if holdings is None or getattr(holdings, "empty", True):
        return []
    out = []
    for _, r in holdings.iterrows():
        isin = str(r.get("isin") or "").strip().upper()
        if not isin:
            continue
        try:
            val = float(r.get("current_value") or 0)
        except (TypeError, ValueError):
            val = 0.0
        out.append({
            "security_id": isin,
            "name": str(r.get("symbol") or isin),
            "value": val,
            "asset_class": "Equity",
            "sector": r.get("sector"),
            "market_cap": r.get("market_cap"),
            "price": r.get("current_price"),
            "quantity": r.get("quantity"),
            "avg_price": r.get("avg_price"),
            "is_mf": False,
        })
    return out


def _mf_positions(app_ctx: AppContext) -> tuple[list[dict], dict]:
    """Best-effort MF context (never raises; empty when unavailable/outside Streamlit)."""
    try:
        from streamlit.runtime import exists as _st_exists
        if not _st_exists():
            return [], {}
    except Exception:  # noqa: BLE001 — not running under Streamlit (e.g. tests/CLI)
        return [], {}
    try:
        from app.mf_ui import get_mf_context
        from portfolio.allocation import classify_asset_class
        mf = get_mf_context(app_ctx)
    except Exception:  # noqa: BLE001
        return [], {}
    if not mf or not mf.get("has_mf_data"):
        return [], (mf or {})
    out = []
    for row in mf.get("scheme_rows", []):
        code = row.get("scheme_code")
        name = row.get("scheme_name") or code
        out.append({
            "security_id": str(code),
            "name": str(name),
            "value": float(row.get("current_value") or 0),
            "asset_class": classify_asset_class(name, is_mf=True, market_cap=row.get("category")),
            "sector": None,
            "market_cap": row.get("category"),
            "price": row.get("nav"),
            "is_mf": True,
        })
    return out, mf


def build_copilot_context(app_ctx: AppContext) -> CopilotContext:
    """Assemble a read-only snapshot for the copilot tools."""
    equity = _equity_positions(app_ctx.holdings)
    mf_pos, mf_ctx = _mf_positions(app_ctx)
    positions = equity + mf_pos
    total = sum(float(p.get("value") or 0) for p in positions)
    try:
        from portfolio.config import load_targets
        targets = load_targets()
    except Exception:  # noqa: BLE001
        targets = {}
    names = {p["security_id"]: p.get("name") or p["security_id"] for p in positions}
    return CopilotContext(
        app_ctx=app_ctx,
        positions=positions,
        total_value=total,
        targets=targets,
        mf=mf_ctx,
        holdings_df=app_ctx.holdings,
        txs=list(app_ctx.txs or []),
        price_lookup=app_ctx.pl,
        end_date=app_ctx.filters.end_date if getattr(app_ctx, "filters", None) else None,
        names=names,
    )
