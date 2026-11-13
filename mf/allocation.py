"""MF asset allocation & risk metrics."""
from __future__ import annotations
from decimal import Decimal
from typing import Optional

def classify_scheme(direct_regular, growth_idcw, scheme_name=None):
    name = (scheme_name or "").lower()
    if any(k in name for k in ("gold",)):
        return "Gold"
    if any(k in name for k in ("debt", "liquid", "gilt")):
        return "Debt"
    if any(k in name for k in ("hybrid", "balanced")):
        return "Hybrid"
    return "Equity"

def allocation_report(holdings_data, scheme_classifications=None):
    from collections import defaultdict
    categories = defaultdict(Decimal)
    total_value = Decimal("0")
    for sid, data in holdings_data.items():
        cv = Decimal(str(data.get("current_value", 0)))
        if cv <= 0: continue
        cat = scheme_classifications.get(sid, "Equity") if scheme_classifications else "Equity"
        categories[cat] += cv; total_value += cv
    if total_value <= 0: return dict(total_value=0, allocation={}, missing_data=[], pct_sum=0)
    allocation = {}
    for cat, val in categories.items():
        allocation[cat] = {"value": float(val), "percentage": round(float(val / total_value * 100), 2)}
    pct_sum = round(sum(v["percentage"] for v in allocation.values()), 2)
    return {"total_value": float(total_value), "allocation": allocation, "percentage_sum": pct_sum, "rounding_gap": round(100.0 - pct_sum, 2)}

def concentration_report(holdings_data, top_n=5):
    sorted_schemes = sorted(holdings_data.items(), key=lambda x: x[1].get("current_value", 0), reverse=True)
    total = sum(d.get("current_value", 0) for d in holdings_data.values())
    top_holdings = []
    for sid, data in sorted_schemes[:top_n]:
        cv = data.get("current_value", 0)
        pct = round(cv / total * 100, 2) if total > 0 else 0.0
        top_holdings.append({"scheme_code": sid, "current_value": cv, "percentage": pct})
    top_conc = round(sum(h["percentage"] for h in top_holdings), 2)
    return {"total_value": total, "top_holdings": top_holdings, "top_concentration_percentage": top_conc}
print("allocation.py done")
