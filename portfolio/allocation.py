"""Asset allocation, drift & risk analysis (Stage 9).

Operates on generic position dicts:
  {"security_id", "name", "value", "asset_class", "sector", "market_cap", "is_mf"}
All aggregation is deterministic.
"""
from __future__ import annotations

from collections import defaultdict

GOLD_HINTS = ("gold", "silver")
DEBT_HINTS = ("debt", "liquid", "gilt", "bond", "money market", "ultra short", "short duration", "credit risk", "banking and psu")
HYBRID_HINTS = ("hybrid", "balanced", "arbitrage", "multi asset")


def classify_asset_class(name: str | None, is_mf: bool = True, market_cap: str | None = None) -> str:
    """Coarse asset-class classifier from a scheme/security name (no data invented)."""
    n = (name or "").lower()
    if any(h in n for h in GOLD_HINTS):
        return "Gold"
    if any(h in n for h in DEBT_HINTS):
        return "Debt"
    if any(h in n for h in HYBRID_HINTS):
        return "Hybrid"
    if is_mf:
        return "Equity"  # default for equity-oriented MF; user can override via metadata
    return "Equity"


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def normalize_positions(positions: list[dict]) -> list[dict]:
    """Fill defaults (asset_class, value, weight later) on position dicts."""
    out = []
    for p in positions or []:
        val = _num(p.get("value"))
        ac = p.get("asset_class") or classify_asset_class(p.get("name"), bool(p.get("is_mf", False)), p.get("market_cap"))
        out.append({
            "security_id": str(p.get("security_id") or "").strip().upper(),
            "name": p.get("name"),
            "value": val,
            "asset_class": ac,
            "sector": p.get("sector"),
            "market_cap": p.get("market_cap"),
            "is_mf": bool(p.get("is_mf", False)),
        })
    return out


def total_value(positions: list[dict]) -> float:
    return sum(_num(p.get("value")) for p in positions or [])


def aggregate_by(positions: list[dict], key: str) -> dict[str, float]:
    agg: dict[str, float] = defaultdict(float)
    for p in positions or []:
        k = p.get(key) or "Unknown"
        agg[k] += _num(p.get("value"))
    return dict(agg)


def current_allocation(positions: list[dict]) -> dict:
    """Return {asset_class: value} + percentages + total."""
    total = total_value(positions)
    by_class = aggregate_by(positions, "asset_class")
    pct = {k: round(v / total * 100, 2) for k, v in by_class.items()} if total > 0 else {k: 0.0 for k in by_class}
    return {"total": round(total, 2), "by_class": by_class, "pct": pct}


def compute_drift(current_pct: dict, targets: dict) -> dict[str, float]:
    """Signed drift (current - target) in percentage points for each asset class."""
    drift = {}
    for cls, cfg in (targets.get("asset_classes") or {}).items():
        cur = current_pct.get(cls, 0.0)
        drift[cls] = round(cur - float(cfg.get("target", 0.0)), 2)
    return drift


def allocation_report(positions: list[dict], targets: dict) -> dict:
    """Full allocation vs target report with drift and breach flags."""
    alloc = current_allocation(positions)
    drift = compute_drift(alloc["pct"], targets)
    rows = []
    breached = []
    for cls, cfg in (targets.get("asset_classes") or {}).items():
        cur = alloc["pct"].get(cls, 0.0)
        target = float(cfg.get("target", 0.0))
        d = drift.get(cls, 0.0)
        lo, hi = float(cfg.get("min", 0.0)), float(cfg.get("max", 100.0))
        breach = cur < lo or cur > hi
        if breach:
            breached.append(cls)
        rows.append({
            "asset_class": cls, "current_pct": cur, "target_pct": target,
            "drift_pct": d, "min_pct": lo, "max_pct": hi, "breached": breach,
            "value": round(alloc["by_class"].get(cls, 0.0), 2),
        })
    sector = aggregate_by([p for p in positions or [] if p.get("sector")], "sector")
    mc = aggregate_by([p for p in positions or [] if p.get("market_cap")], "market_cap")
    mf_val = sum(_num(p.get("value")) for p in positions or [] if p.get("is_mf"))
    dir_val = sum(_num(p.get("value")) for p in positions or [] if not p.get("is_mf"))
    return {
        "total": alloc["total"],
        "rows": rows,
        "breached": breached,
        "sector_exposure": {k: round(v / alloc["total"] * 100, 2) for k, v in sector.items()} if alloc["total"] else {},
        "market_cap_exposure": {k: round(v / alloc["total"] * 100, 2) for k, v in mc.items()} if alloc["total"] else {},
        "mf_direct_split": {
            "mf_pct": round(mf_val / alloc["total"] * 100, 2) if alloc["total"] else 0.0,
            "direct_pct": round(dir_val / alloc["total"] * 100, 2) if alloc["total"] else 0.0,
        },
    }


def concentration_metrics(positions: list[dict]) -> dict:
    """Herfindahl index + top weight from position values (0..1 HHI)."""
    vals = [_num(p.get("value")) for p in positions or [] if _num(p.get("value")) > 0]
    total = sum(vals)
    if total <= 0:
        return {"hhi": None, "top_pct": None, "n_positions": 0}
    shares = [v / total for v in vals]
    hhi = sum(s * s for s in shares)
    return {"hhi": round(hhi, 4), "top_pct": round(max(shares) * 100, 2), "n_positions": len(vals)}
