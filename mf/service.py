"""MF analytics integration service (Stage 7).

Assembles scheme holdings + valuations + XIRR (reusing the existing XIRR solver),
risk analytics, overlap/concentration and explainable quality assessments into a
single context consumed by the UI. Pure/deterministic given inputs.
"""
from __future__ import annotations

from datetime import date

from mf import analytics, overlap, quality
from src.xirr import compute_xirr

BUY_SIDES = frozenset({"SIP", "PURCHASE", "BUY", "LUMP_SUM", "SUBSCRIPTION",
                       "ALLOTMENT", "BONUS", "GROWTH", "ACCUMULATION", "REINVEST",
                       "SWITCH_IN", "STP_IN"})
SELL_SIDES = frozenset({"REDEMPTION", "SWITCH_OUT", "SWP", "WITHDRAW", "SELL", "STP_OUT"})
DIVIDEND_SIDES = frozenset({"IDCW", "DIVIDEND", "DIVIDEND_PAYOUT", "PAYOUT"})


def _scheme_cashflows(txs: list[dict], terminal_value: float, end_date: str) -> list[tuple[str, float]]:
    flows: list[tuple[str, float]] = []
    for t in txs:
        d = str(t.get("trade_date") or "")[:10]
        if not d:
            continue
        side = str(t.get("side") or "").upper()
        amt = abs(float(t.get("amount") or t.get("net_amount") or 0))
        if side in BUY_SIDES:
            flows.append((d, -amt))
        elif side in SELL_SIDES or side in DIVIDEND_SIDES:
            flows.append((d, amt))
    if terminal_value > 0:
        flows.append((end_date, terminal_value))
    return flows


def _scheme_valuation(txs: list[dict], nav: float | None, end_date: str) -> dict:
    units = 0.0
    invested = 0.0
    redeemed = 0.0
    for t in txs:
        side = str(t.get("side") or "").upper()
        u = float(t.get("units") or 0)
        amt = abs(float(t.get("amount") or 0))
        if side in BUY_SIDES:
            units += u
            invested += amt
        elif side in SELL_SIDES:
            units = max(0.0, units - u)
            redeemed += amt
    units = max(0.0, units)
    current_value = units * nav if nav and nav > 0 else 0.0
    flows = _scheme_cashflows(txs, current_value, end_date)
    xirr = None
    if len(flows) >= 2 and any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows):
        try:
            xirr = compute_xirr(flows)
        except Exception:  # noqa: BLE001
            xirr = None
    return {
        "units": round(units, 4),
        "nav": nav,
        "current_value": round(current_value, 2),
        "invested": round(invested, 2),
        "redeemed": round(redeemed, 2),
        "unrealized_gain": round(current_value - max(0.0, invested - redeemed), 2),
        "xirr": xirr,
        "missing_nav": not nav or nav <= 0,
    }


def build_mf_context(
    mf_transactions: list[dict],
    nav_records_by_scheme: dict[str, list[tuple[str, float]]],
    holdings_by_scheme: dict[str, list[dict]] | None = None,
    as_of_by_scheme: dict[str, str | None] | None = None,
    fund_metadata: dict[str, dict] | None = None,
    direct_holdings: dict[str, float] | None = None,
    benchmark_nav_by_scheme: dict[str, list[tuple[str, float]]] | None = None,
    asset_class_by_scheme: dict[str, str] | None = None,
    end_date: str | None = None,
) -> dict:
    """Build the full MF analytics context from already-loaded inputs (deterministic)."""
    holdings_by_scheme = holdings_by_scheme or {}
    as_of_by_scheme = as_of_by_scheme or {}
    fund_metadata = fund_metadata or {}
    benchmark_nav_by_scheme = benchmark_nav_by_scheme or {}
    end_date = end_date or date.today().isoformat()
    warnings: list[str] = []

    by_scheme: dict[str, list[dict]] = {}
    for t in mf_transactions or []:
        code = str(t.get("scheme_code") or t.get("scheme_name") or "").strip()
        if code:
            by_scheme.setdefault(code, []).append(t)

    scheme_rows: list[dict] = []
    for code, txs in by_scheme.items():
        nav_series = nav_records_by_scheme.get(code, [])
        latest_nav = nav_series[-1][1] if nav_series else None
        val = _scheme_valuation(txs, latest_nav, end_date)
        summ = analytics.summarize_fund(nav_series, benchmark_nav_by_scheme.get(code)) if nav_series else {}
        meta = fund_metadata.get(code, {})
        scheme_rows.append({
            "scheme_code": code,
            "scheme_name": meta.get("scheme_name") or txs[0].get("scheme_name") or code,
            "category": meta.get("category"),
            "expense_ratio": meta.get("expense_ratio_direct") or meta.get("expense_ratio_regular"),
            **val,
            "analytics": summ,
            "nav_status": "ok" if nav_series else "missing",
        })

    total_value = sum(r["current_value"] for r in scheme_rows)
    total_invested = sum(r["invested"] for r in scheme_rows)
    for r in scheme_rows:
        r["weight_pct"] = round(r["current_value"] / total_value * 100, 2) if total_value > 0 else 0.0

    matrix = overlap.overlap_matrix(holdings_by_scheme)
    overlap_flagged = overlap.flag_overlap(matrix["pairs"])
    overlap_max: dict[str, float] = {}
    for p in matrix["pairs"]:
        overlap_max[p["scheme_a"]] = max(overlap_max.get(p["scheme_a"], 0.0), p["weight_overlap_pct"])
        overlap_max[p["scheme_b"]] = max(overlap_max.get(p["scheme_b"], 0.0), p["weight_overlap_pct"])

    scheme_weights = {r["scheme_code"]: r["weight_pct"] for r in scheme_rows}
    indirect = overlap.look_through_exposure(scheme_weights, holdings_by_scheme)
    combined = overlap.combine_with_direct_equity(indirect, direct_holdings or {})
    sector_conc = overlap.sector_concentration(combined)
    marketcap_conc = overlap.market_cap_concentration(combined)
    asset_class_conc = overlap.asset_class_concentration(scheme_weights, asset_class_by_scheme or {})

    quality_rows: list[dict] = []
    for r in scheme_rows:
        s = r.get("analytics", {})
        quality_rows.append(quality.assess_fund_quality({
            "scheme_code": r["scheme_code"],
            "scheme_name": r["scheme_name"],
            "excess_3y": s.get("excess_3y"),
            "sharpe_ratio": s.get("sharpe_ratio"),
            "performance_consistency_1y": s.get("performance_consistency_1y"),
            "expense_ratio": r.get("expense_ratio"),
            "overlap_max": overlap_max.get(r["scheme_code"]),
        }))

    disclosures = overlap.disclosure_status(holdings_by_scheme, as_of_by_scheme)
    if holdings_by_scheme and not any(holdings_by_scheme.values()):
        warnings.append("No scheme holdings disclosures imported — overlap/indirect exposure unavailable.")

    return {
        "scheme_rows": scheme_rows,
        "total_value": round(total_value, 2),
        "total_invested": round(total_invested, 2),
        "total_unrealized_gain": round(sum(r["unrealized_gain"] for r in scheme_rows), 2),
        "overlap": matrix,
        "overlap_flagged": overlap_flagged,
        "indirect_exposure": indirect,
        "combined_exposure": combined,
        "sector_concentration": sector_conc,
        "market_cap_concentration": marketcap_conc,
        "asset_class_concentration": asset_class_conc,
        "quality": quality_rows,
        "disclosures": disclosures,
        "warnings": warnings,
    }


def _load_transactions_from_folder(folder: str) -> list[dict]:
    """Parse MF statement files (CSV/XLSX) from a folder into normalised transactions."""
    from pathlib import Path

    from mf.importers import parse_mf_statement

    p = Path(folder)
    if not p.exists():
        return []
    txs: list[dict] = []
    for f in sorted(p.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in (".csv", ".xlsx", ".xls"):
            continue
        try:
            rows, _ = parse_mf_statement(str(f))
            txs.extend(rows)
        except Exception:  # noqa: BLE001, S112
            continue
    return txs


def load_mf_context(folder: str = "data/mf", fetch_nav: bool = False,
                    provider: str = "mfapi", direct_holdings: dict[str, float] | None = None,
                    end_date: str | None = None) -> dict:
    """Load MF data from local sources + NAV cache and build the analytics context.

    NAV fetching from AMFI/mftool is attempted only when ``fetch_nav`` is True; by
    default only the local cache is used (fast, offline, deterministic in tests).
    """
    from pathlib import Path

    from mf.data import (
        MFDataService,
        get_fund_metadata_map,
        load_fund_metadata_csv,
        load_scheme_holdings_csv,
        load_scheme_holdings_from_store,
        save_fund_metadata,
        save_scheme_holdings,
    )

    p = Path(folder)
    # Import any disclosure/metadata CSVs present in the folder.
    for name in ("holdings.csv", "fund_holdings.csv"):
        if (p / name).exists():
            save_scheme_holdings(load_scheme_holdings_csv(p / name))
    for name in ("metadata.csv", "fund_metadata.csv"):
        if (p / name).exists():
            save_fund_metadata(load_fund_metadata_csv(p / name))

    txs = _load_transactions_from_folder(folder)
    holdings_by_scheme, as_of_by_scheme = load_scheme_holdings_from_store()
    metadata = get_fund_metadata_map()

    # Gather scheme codes from transactions (fall back to disclosed schemes).
    scheme_codes = sorted({str(t.get("scheme_code") or "") for t in txs if t.get("scheme_code")})
    if not scheme_codes:
        scheme_codes = sorted(set(holdings_by_scheme) | set(metadata))

    svc = MFDataService(provider=provider)
    nav_map: dict[str, list[tuple[str, float]]] = {}
    cached_codes = svc.cached_scheme_codes()
    for code in scheme_codes:
        if not code:
            continue
        recs, _status = svc.get_nav_history(code, use_cache=not fetch_nav)
        if not recs and cached_codes:
            # Bridge AMC product codes (e.g. P8017) to valuation codes (8017)
            # by suffix match so a valuation-seeded NAV still resolves.
            cu = code.upper()
            for cc in cached_codes:
                ccu = str(cc).upper()
                if ccu and ccu != cu and (cu.endswith(ccu) or ccu.endswith(cu)):
                    recs = svc.cached_nav_history(cc)
                    if recs:
                        break
        if recs:
            nav_map[code] = recs

    ctx = build_mf_context(
        txs, nav_map, holdings_by_scheme=holdings_by_scheme,
        as_of_by_scheme=as_of_by_scheme, fund_metadata=metadata,
        direct_holdings=direct_holdings, end_date=end_date,
    )
    ctx["has_mf_data"] = bool(txs or holdings_by_scheme or metadata)
    ctx["scheme_codes"] = scheme_codes
    return ctx