"""MF portfolio overlap & concentration engine (Stage 7).

Operations work on plain data structures:
  holdings = [{"stock_isin", "stock_name", "weight_pct", "sector", "market_cap"}]
  scheme_weights = {scheme_code: portfolio_weight_pct}

Missing/outdated disclosures are labelled via :func:`disclosure_status` — holdings
are never invented. All calculations are deterministic.
"""
from __future__ import annotations

from datetime import date


def _weight_map(holdings: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for h in holdings or []:
        isin = str(h.get("stock_isin") or "").strip().upper()
        try:
            w = float(h.get("weight_pct") or 0)
        except (TypeError, ValueError):
            continue
        if isin and w > 0:
            out[isin] = out.get(isin, 0.0) + w
    return out


def pairwise_overlap(holdings_a: list[dict], holdings_b: list[dict]) -> dict:
    """Overlap between two schemes' disclosed holdings.

    ``jaccard`` = |intersection| / |union| on stock ISINs.
    ``weight_overlap_pct`` = sum of min(weight_a, weight_b) across common stocks.
    """
    wa = _weight_map(holdings_a)
    wb = _weight_map(holdings_b)
    sa, sb = set(wa), set(wb)
    common = sa & sb
    union = sa | sb
    jaccard = (len(common) / len(union)) if union else 0.0
    weight_overlap = sum(min(wa[i], wb[i]) for i in common)
    return {
        "common_isins": sorted(common),
        "common_count": len(common),
        "jaccard": round(jaccard, 4),
        "weight_overlap_pct": round(weight_overlap, 4),
    }


def overlap_matrix(holdings_by_scheme: dict[str, list[dict]]) -> dict:
    """Pairwise overlap for every scheme pair, plus a list of flagged pairs."""
    schemes = sorted(holdings_by_scheme)
    pairs: list[dict] = []
    for i in range(len(schemes)):
        for j in range(i + 1, len(schemes)):
            a, b = schemes[i], schemes[j]
            res = pairwise_overlap(holdings_by_scheme[a], holdings_by_scheme[b])
            pairs.append({"scheme_a": a, "scheme_b": b, **res})
    return {"pairs": pairs, "schemes": schemes}


def flag_overlap(pairs: list[dict], weight_threshold: float = 25.0,
                 jaccard_threshold: float = 0.4) -> list[dict]:
    """Flag scheme pairs with excessive overlap."""
    flagged = []
    for p in pairs:
        if p.get("weight_overlap_pct", 0) >= weight_threshold or p.get("jaccard", 0) >= jaccard_threshold:
            flagged.append(p)
    return flagged


def look_through_exposure(scheme_weights: dict[str, float],
                          holdings_by_scheme: dict[str, list[dict]]) -> dict[str, dict]:
    """Indirect stock exposure via MFs: sum(scheme_portfolio_w% * stock_w% / 100)."""
    exposure: dict[str, dict] = {}
    for scheme, scheme_w in (scheme_weights or {}).items():
        try:
            sw = float(scheme_w)
        except (TypeError, ValueError):
            continue
        for h in holdings_by_scheme.get(scheme, []):
            isin = str(h.get("stock_isin") or "").strip().upper()
            if not isin:
                continue
            try:
                stock_w = float(h.get("weight_pct") or 0)
            except (TypeError, ValueError):
                continue
            contrib = sw * stock_w / 100.0
            rec = exposure.setdefault(isin, {
                "stock_isin": isin,
                "stock_name": h.get("stock_name"),
                "sector": h.get("sector"),
                "market_cap": h.get("market_cap"),
                "indirect_pct": 0.0,
            })
            rec["indirect_pct"] += contrib
            if not rec.get("stock_name") and h.get("stock_name"):
                rec["stock_name"] = h.get("stock_name")
    for rec in exposure.values():
        rec["indirect_pct"] = round(rec["indirect_pct"], 4)
    return exposure


def combine_with_direct_equity(indirect: dict[str, dict],
                               direct_holdings: dict[str, float]) -> list[dict]:
    """Combine indirect MF exposure with direct equity weights (both in % of total).

    ``direct_holdings`` = {isin: portfolio_weight_pct}. Returns a combined,
    descending-sorted list of exposures per stock.
    """
    combined: dict[str, dict] = {}
    for isin, rec in (indirect or {}).items():
        combined[isin] = {
            "stock_isin": isin,
            "stock_name": rec.get("stock_name"),
            "sector": rec.get("sector"),
            "market_cap": rec.get("market_cap"),
            "indirect_pct": float(rec.get("indirect_pct") or 0.0),
            "direct_pct": 0.0,
        }
    for isin, w in (direct_holdings or {}).items():
        key = str(isin).strip().upper()
        rec = combined.setdefault(key, {
            "stock_isin": key, "stock_name": None, "sector": None,
            "market_cap": None, "indirect_pct": 0.0, "direct_pct": 0.0,
        })
        try:
            rec["direct_pct"] += float(w)
        except (TypeError, ValueError):
            continue
    for rec in combined.values():
        rec["total_pct"] = round(rec["direct_pct"] + rec["indirect_pct"], 4)
    return sorted(combined.values(), key=lambda r: r["total_pct"], reverse=True)


def _concentration(exposure_values: dict[str, float]) -> dict:
    total = sum(v for v in exposure_values.values() if v > 0)
    if total <= 0:
        return {"total": 0.0, "buckets": {}, "top_share_pct": 0.0}
    buckets = {k: round(v / total * 100, 2) for k, v in sorted(exposure_values.items(), key=lambda x: -x[1]) if k}
    top_share = max(buckets.values()) if buckets else 0.0
    return {"total": round(total, 4), "buckets": buckets, "top_share_pct": top_share}


def sector_concentration(exposure: list[dict]) -> dict:
    """Aggregate total exposure by sector (uses total_pct per stock)."""
    agg: dict[str, float] = {}
    for rec in exposure or []:
        sec = rec.get("sector") or "Unknown"
        agg[sec] = agg.get(sec, 0.0) + float(rec.get("total_pct") or 0.0)
    return _concentration(agg)


def market_cap_concentration(exposure: list[dict]) -> dict:
    """Aggregate total exposure by market-cap bucket (Large/Mid/Small/Unknown)."""
    agg: dict[str, float] = {}
    for rec in exposure or []:
        mc = (rec.get("market_cap") or "Unknown").title() if rec.get("market_cap") else "Unknown"
        agg[mc] = agg.get(mc, 0.0) + float(rec.get("total_pct") or 0.0)
    return _concentration(agg)


def asset_class_concentration(scheme_weights: dict[str, float],
                             scheme_asset_class: dict[str, str]) -> dict:
    """Aggregate MF value by asset class (Equity/Debt/Hybrid/Gold/Other)."""
    agg: dict[str, float] = {}
    for scheme, w in (scheme_weights or {}).items():
        cls = scheme_asset_class.get(scheme, "Equity")
        try:
            agg[cls] = agg.get(cls, 0.0) + float(w)
        except (TypeError, ValueError):
            continue
    return _concentration(agg)


def _age_days(as_of: str | None) -> int | None:
    if not as_of:
        return None
    try:
        d = date.fromisoformat(str(as_of)[:10])
    except (ValueError, TypeError):
        return None
    return (date.today() - d).days


def disclosure_status(holdings_by_scheme: dict[str, list[dict]],
                      as_of_by_scheme: dict[str, str | None],
                      max_age_days: int = 45) -> list[dict]:
    """Label each scheme's disclosure as ok / missing / outdated.

    Ensures the UI clearly distinguishes absent/old fund holdings disclosures.
    """
    rows: list[dict] = []
    for scheme in sorted(holdings_by_scheme):
        holdings = holdings_by_scheme.get(scheme) or []
        as_of = as_of_by_scheme.get(scheme)
        age = _age_days(as_of)
        if not holdings:
            status = "missing"
            reason = "No disclosed holdings available"
        elif age is None:
            status = "outdated"
            reason = "Disclosure date unknown"
        elif age > max_age_days:
            status = "outdated"
            reason = f"Disclosure is {age} days old (> {max_age_days})"
        else:
            status = "ok"
            reason = ""
        rows.append({
            "scheme_code": scheme,
            "as_of_date": as_of,
            "holding_count": len(holdings),
            "age_days": age,
            "status": status,
            "reason": reason,
        })
    return rows