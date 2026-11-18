"""Tax-aware rebalancing optimizer (Stage 9).

Deterministic greedy planner:
  1. Compare current vs target class allocation.
  2. Fund under-weight classes with available cash first (no selling).
  3. Only if still under-weight, sell from over-weight positions — preferring
     tax-efficient lots (LTCG/low tax per rupee), boosting positions flagged
     EXIT/TRIM by Stage 8, and protecting compounders unless a clear exit signal.
  4. Respect max turnover and minimum trade size.

Advisory only — no trades executed.
"""
from __future__ import annotations

from portfolio import allocation
from portfolio.tax import get_tax_rules
from portfolio.tax_lots import estimate_lot_tax


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _signals_map(signals) -> dict[str, str]:
    if isinstance(signals, dict):
        return {str(k).strip().upper(): v for k, v in signals.items()}
    out = {}
    for s in signals or []:
        sid = str(s.get("security_id") or "").strip().upper()
        if sid and s.get("action"):
            out[sid] = s["action"]
    return out


def _sell_tax(pos: dict, amount: float, lots_by_security: dict, rules: dict, sell_date) -> tuple[float, bool]:
    """Estimate tax + missing-cost-basis flag for selling ``amount`` rupees of a position."""
    price = _num(pos.get("price") or pos.get("current_price"))
    if price <= 0:
        return 0.0, True
    qty = amount / price
    lots = (lots_by_security or {}).get(pos["security_id"]) or []
    if not lots:
        return 0.0, True
    est = estimate_lot_tax(lots, sell_date, price, qty, rules=rules, is_mf=bool(pos.get("is_mf")))
    return float(est.get("estimated_tax") or 0.0), bool(est.get("missing_cost_basis"))


def _rank_sell_candidates(overweight_positions, signals, compounders, lots_by_security, rules, sell_date):
    """Rank overweight positions for selling: lower tax-per-rupee first; signal boost; compounder penalty."""
    ranked = []
    for pos in overweight_positions:
        sid = pos["security_id"]
        sample = min(100000.0, max(1.0, _num(pos.get("value"))))
        tax, missing = _sell_tax(pos, sample, lots_by_security, rules, sell_date)
        tax_per_rupee = (tax / sample) if sample > 0 else 0.0
        sig = signals.get(sid)
        boost = 1.0
        if sig in ("EXIT_REVIEW", "REPLACEMENT_REVIEW"):
            boost = 0.3  # strongly prioritize selling
        elif sig == "TRIM_REVIEW":
            boost = 0.6
        protected = sid in (compounders or set()) and sig not in ("EXIT_REVIEW", "REPLACEMENT_REVIEW")
        score = tax_per_rupee * boost * (5.0 if protected else 1.0)
        ranked.append({"pos": pos, "tax_per_rupee": round(tax_per_rupee, 6), "missing": missing,
                       "signal": sig, "protected_compounder": protected, "score": score})
    ranked.sort(key=lambda r: r["score"])
    return ranked


def plan_rebalance(positions, targets, cash_available=0.0, signals=None, compounders=None,
                   lots_by_security=None, sell_date=None, tax_config=None) -> dict:
    """Build an advisory rebalancing plan (cash-first, tax-aware, compounder-protecting)."""
    positions = allocation.normalize_positions(positions)
    lots_by_security = lots_by_security or {}
    signals = _signals_map(signals)
    compounders = {str(c).strip().upper() for c in (compounders or set())}
    rb = (targets or {}).get("rebalancing", {})
    ac_targets = (targets or {}).get("asset_classes", {})
    rules = get_tax_rules(sell_date, tax_config or (targets or {}).get("tax"))
    min_trade = float(rb.get("min_trade_size", 0))

    pos_value = allocation.total_value(positions)
    investable = pos_value + _num(cash_available)
    current = allocation.aggregate_by(positions, "asset_class")

    buy_budget: dict[str, float] = {}
    sell_surplus: dict[str, float] = {}
    for cls, cfg in ac_targets.items():
        tgt = investable * float(cfg.get("target", 0.0)) / 100.0
        diff = tgt - current.get(cls, 0.0)
        if diff > 0:
            buy_budget[cls] = round(diff, 2)
            sell_surplus[cls] = 0.0
        else:
            buy_budget[cls] = 0.0
            sell_surplus[cls] = round(-diff, 2)

    actions: list[dict] = []
    warnings: list[str] = []
    protected_skipped: list[str] = []

    # ---- 1) Cash-first into under-weight classes --------------------------
    cash = _num(cash_available)
    cash_invested: dict[str, float] = {}
    if rb.get("prioritize_cash", True):
        for cls in sorted(buy_budget, key=lambda c: -buy_budget[c]):
            if cash <= 0 or buy_budget[cls] <= 0:
                continue
            invest = min(buy_budget[cls], cash)
            if invest < min_trade:
                continue
            cash_invested[cls] = invest
            buy_budget[cls] -= invest
            cash -= invest
    elif cash > 0:
        warnings.append("Cash held but prioritize_cash disabled; cash left unallocated.")

    total_buy_remaining = sum(buy_budget.values())
    total_sell_surplus = sum(sell_surplus.values())
    max_turnover = investable * float(rb.get("max_turnover_pct", 100.0)) / 100.0
    sell_required = min(total_buy_remaining, total_sell_surplus, max_turnover)

    # ---- 2) Tax-aware, signal-aware sells --------------------------------
    overweight = [p for p in positions if sell_surplus.get(p["asset_class"], 0.0) > 0 and _num(p.get("value")) > 0]
    ranked = _rank_sell_candidates(overweight, signals, compounders, lots_by_security, rules, sell_date)
    sells_value = 0.0
    est_tax = 0.0
    missing_cb = False
    for cand in ranked:
        if sells_value >= sell_required - 1e-9:
            break
        pos = cand["pos"]
        room = min(sell_required - sells_value, sell_surplus.get(pos["asset_class"], 0.0), _num(pos.get("value")))
        if room < min_trade:
            continue
        if cand["protected_compounder"]:
            protected_skipped.append(pos["security_id"])
            continue
        tax, missing = _sell_tax(pos, room, lots_by_security, rules, sell_date)
        actions.append({
            "action": "SELL", "security_id": pos["security_id"], "name": pos["name"],
            "asset_class": pos["asset_class"], "amount": round(room, 2),
            "estimated_tax": round(tax, 2), "missing_cost_basis": missing,
            "signal": cand["signal"], "reason": "Trim over-weight class (tax-aware)",
        })
        sells_value += room
        est_tax += tax
        missing_cb = missing_cb or missing
        sell_surplus[pos["asset_class"]] -= room
    return _finalize_plan(positions, targets, actions, cash_invested, buy_budget, sells_value,
                          est_tax, missing_cb, protected_skipped, warnings, investable, min_trade, rb, rules)



def _finalize_plan(positions, targets, actions, cash_invested, buy_budget, sells_value,
                   est_tax, missing_cb, protected_skipped, warnings, investable, min_trade, rb, rules):
    remaining_buy = {c: v for c, v in buy_budget.items() if v > 0}
    total_remaining = sum(remaining_buy.values())
    proceeds = min(sells_value, total_remaining)
    rb_total = total_remaining or 0.0
    for cls, need in remaining_buy.items():
        from_proceeds = proceeds * (need / rb_total) if rb_total > 0 else 0.0
        amount = cash_invested.get(cls, 0.0) + from_proceeds
        if amount >= min_trade:
            actions.append({
                "action": "BUY", "asset_class": cls, "amount": round(amount, 2),
                "reason": ("Invest cash" if cash_invested.get(cls) else "Redeploy sale proceeds") + " into under-weight class",
            })
    still_short = max(0.0, total_remaining - proceeds)
    if still_short > min_trade:
        if rb.get("prioritize_sip", True):
            warnings.append(f"Rs {still_short:,.0f} still under-weight after cash/sells — consider increasing SIP into under-weight classes.")
        else:
            warnings.append(f"Rs {still_short:,.0f} under-weight remains (insufficient cash/sellable surplus).")

    turnover_pct = (sells_value / investable * 100) if investable > 0 else 0.0
    current = allocation.aggregate_by(positions, "asset_class")
    buys_by_class: dict[str, float] = {}
    sells_by_class: dict[str, float] = {}
    for a in actions:
        cls = a.get("asset_class")
        if a["action"] == "BUY":
            buys_by_class[cls] = buys_by_class.get(cls, 0.0) + a["amount"]
        else:
            sells_by_class[cls] = sells_by_class.get(cls, 0.0) + a["amount"]
    drift_after = {}
    for cls, cfg in (targets.get("asset_classes") or {}).items():
        tgt = investable * float(cfg.get("target", 0.0)) / 100.0
        after = current.get(cls, 0.0) + buys_by_class.get(cls, 0.0) - sells_by_class.get(cls, 0.0)
        drift_after[cls] = round((after - tgt) / investable * 100, 2) if investable > 0 else 0.0

    return {
        "actions": actions,
        "investable_total": round(investable, 2),
        "cash_used": round(sum(cash_invested.values()), 2),
        "sells_value": round(sells_value, 2),
        "buys_value": round(sum(a["amount"] for a in actions if a["action"] == "BUY"), 2),
        "estimated_tax": round(est_tax, 2),
        "missing_cost_basis": missing_cb,
        "turnover_pct": round(turnover_pct, 2),
        "protected_compounders_skipped": protected_skipped,
        "warnings": warnings,
        "drift_after": drift_after,
    }

