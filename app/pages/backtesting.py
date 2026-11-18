"""Page 4 — Backtesting.

Historical stock-replacement simulator: actual vs hypothetical portfolio returns,
scenario comparison, assumptions and export. Wraps the existing Stage-5 engine in
``mf/backtesting.py`` (no new analytics engine introduced).
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app.components import ui
from app.context import AppContext
from app.shared import fmt_pct


def _run_backtest(ctx: AppContext, original: str, replacement: str, replacement_date: str,
                  full: bool, end_date) -> dict:
    from mf.backtesting import calculate_backtest_xirr, simulate_stock_replacement

    sim = simulate_stock_replacement(
        transactions=ctx.txs,
        security_id=original,
        replacement_security_id=replacement,
        replacement_date=replacement_date,
        is_full_replacement=full,
    )
    hypothetical = list(sim.preserved_transactions) + list(sim.replaced_transactions)
    actual_res = calculate_backtest_xirr(ctx.txs, str(end_date), ctx.pl)
    hyp_res = calculate_backtest_xirr(hypothetical, str(end_date), ctx.pl)
    return {
        "replacement_info": sim.replacement_info,
        "actual": actual_res,
        "hypothetical": hyp_res,
        "comparison": {
            "actual_xirr": actual_res.get("xirr"),
            "hypothetical_xirr": hyp_res.get("xirr"),
            "xirr_difference": (
                (hyp_res.get("xirr") or 0) - (actual_res.get("xirr") or 0)
                if actual_res.get("xirr") is not None and hyp_res.get("xirr") is not None else None
            ),
            "actual_terminal": actual_res.get("total_terminal"),
            "hypothetical_terminal": hyp_res.get("total_terminal"),
            "actual_invested": actual_res.get("total_invested"),
            "hypothetical_invested": hyp_res.get("total_invested"),
        },
        "hypothetical_transactions": len(hypothetical),
    }


def _render_rebalancing_simulator(ctx: AppContext) -> None:
    ui.section("Rebalancing Simulator")
    st.caption("Tax-aware, advisory rebalancing: cash-first, signal-aware, compounder-protected. "
               "Compare do-nothing vs rebalance. Nothing is executed.")
    from portfolio.optimizer import plan_rebalance
    from portfolio.scenarios import compare_scenarios
    from portfolio.ui import get_rebalance_context
    rb = get_rebalance_context(ctx)
    positions = rb.get("positions", [])
    if not positions:
        st.info("No positions available. Import equity holdings / MF data in Data & Settings.")
        return
    targets = rb["targets"]
    c1, c2 = st.columns(2)
    with c1:
        cash = st.number_input("Cash available to invest (Rs)", min_value=0.0, value=0.0,
                               step=10000.0, key="rb_cash")
    with c2:
        strategies = ["threshold", "annual", "on_demand"]
        idx = strategies.index(targets["rebalancing"].get("strategy", "threshold")) if targets["rebalancing"].get("strategy") in strategies else 0
        strat = st.selectbox("Strategy", strategies, index=idx, key="rb_strategy")

    if st.button("Simulate rebalance", key="rb_run"):
        try:
            t = dict(targets)
            t["rebalancing"] = dict(targets["rebalancing"])
            t["rebalancing"]["strategy"] = strat
            plan = plan_rebalance(
                positions, t, cash_available=cash, signals=rb.get("signals"),
                compounders=rb.get("compounders"), lots_by_security=rb.get("lots"),
                sell_date=str(ctx.filters.end_date),
            )
            st.session_state["rb_plan"] = plan
        except Exception as e:  # noqa: BLE001
            st.error(f"Rebalance simulation failed: {e}")

    plan = st.session_state.get("rb_plan")
    if not plan:
        return
    import pandas as pd
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Cash Used", f"Rs {plan.get('cash_used', 0):,.0f}")
    m2.metric("Sells", f"Rs {plan.get('sells_value', 0):,.0f}")
    m3.metric("Buys", f"Rs {plan.get('buys_value', 0):,.0f}")
    m4.metric("Est. Tax", f"Rs {plan.get('estimated_tax', 0):,.0f}")
    m5.metric("Turnover", f"{plan.get('turnover_pct', 0):.1f}%")

    acts = plan.get("actions", [])
    if acts:
        adf = pd.DataFrame([
            {"action": a.get("action"), "asset_class": a.get("asset_class"),
             "security": a.get("security_id"), "amount": a.get("amount"),
             "estimated_tax": a.get("estimated_tax"), "signal": a.get("signal")}
            for a in acts
        ])
        ui.safe_dataframe(adf)
    else:
        st.success("No trades required to reach targets within tolerance.")

    scen = compare_scenarios(positions, targets, plan)
    st.markdown("##### Current vs Proposed allocation")
    comp = pd.DataFrame([
        {"asset_class": cls,
         "current_%": scen["current"]["weights_pct"].get(cls, 0.0),
         "proposed_%": scen["proposed"]["weights_pct"].get(cls, 0.0),
         "drift_before_pp": scen["current"]["drift_pct"].get(cls, 0.0),
         "drift_after_pp": scen["proposed"]["drift_pct"].get(cls, 0.0)}
        for cls in scen["proposed"]["weights_pct"].keys() | scen["current"]["weights_pct"].keys()
    ])
    ui.safe_dataframe(comp)
    st.caption(f"Max |drift| before: {scen['max_abs_drift_before']:.1f}pp → after: {scen['max_abs_drift_after']:.1f}pp. "
               f"Concentration (HHI) before: {scen['current']['concentration']['hhi']} → after: {scen['proposed']['concentration']['hhi']}.")

    if plan.get("missing_cost_basis"):
        st.warning("Some assets lack cost basis / tax lots — tax is flagged as an estimate and may be understated.")
    if plan.get("protected_compounders_skipped"):
        st.info("Protected compounders not sold: " + ", ".join(plan["protected_compounders_skipped"]))
    for w in plan.get("warnings", []):
        st.warning(w)

    st.download_button(
        "Export plan (CSV)",
        data=pd.DataFrame(acts).to_csv(index=False).encode("utf-8") if acts else b"",
        file_name="rebalance_plan.csv", mime="text/csv", key="rb_export",
    )


def render(ctx: AppContext) -> None:
    f = ctx.filters
    st.markdown("### Backtesting — Rebalancing & Stock Replacement")

    _render_rebalancing_simulator(ctx)
    st.divider()
    st.markdown("#### Stock Replacement Simulator")
    st.caption(
        "Replace stock A with stock B using the same investment amounts and purchase dates, "
        "then compare actual vs hypothetical portfolio XIRR. Engine: mf/backtesting.py."
    )

    if not ctx.txs:
        ui.missing_data_warning("No equity transactions available. Import statements in Data & Settings.")
        return

    isins = sorted({str(t.get("isin")).strip().upper() for t in ctx.txs if t.get("isin")})
    if len(isins) < 1:
        st.info("Need at least one security with transactions to simulate replacement.")
        return

    c1, c2 = st.columns(2)
    with c1:
        original = st.selectbox("Original stock (A)", isins, key="bt_original")
    with c2:
        replacement = st.selectbox(
            "Replacement stock (B)", isins, index=min(1, len(isins) - 1), key="bt_replacement"
        )

    c3, c4 = st.columns(2)
    with c3:
        full = st.checkbox("Full replacement (all transactions)", value=True, key="bt_full")
    with c4:
        st.caption(f"End date (from sidebar): {f.end_date}")

    if st.button("Run simulation", key="bt_run"):
        if original == replacement:
            st.error("Original and replacement stocks must differ.")
            return
        with st.spinner("Running backtest..."):
            try:
                res = _run_backtest(ctx, original, replacement, str(f.end_date), full, f.end_date)
                st.session_state["bt_result"] = res
            except Exception as e:  # noqa: BLE001
                st.error(f"Backtest failed: {e}")

    res = st.session_state.get("bt_result")
    if res:
        ui.section("Scenario Comparison")
        comp = res["comparison"]
        m1, m2, m3 = st.columns(3)
        m1.metric("Actual XIRR", fmt_pct(comp["actual_xirr"]))
        m2.metric("Hypothetical XIRR", fmt_pct(comp["hypothetical_xirr"]))
        diff = comp["xirr_difference"]
        m3.metric("XIRR Difference", fmt_pct(diff) if diff is not None else "N/A")

        st.markdown("##### Assumptions")
        st.json(res["replacement_info"])

        st.markdown("##### Actual vs Hypothetical")
        cmp_df = pd.DataFrame(
            [
                {"Metric": "Invested", "Actual": comp["actual_invested"], "Hypothetical": comp["hypothetical_invested"]},
                {"Metric": "Terminal Value", "Actual": comp["actual_terminal"], "Hypothetical": comp["hypothetical_terminal"]},
                {"Metric": "XIRR", "Actual": comp["actual_xirr"], "Hypothetical": comp["hypothetical_xirr"]},
            ]
        )
        ui.safe_dataframe(cmp_df)

        if res["actual"].get("missing_data") or res["hypothetical"].get("missing_data"):
            st.warning("Missing price data for some positions; backtest values may be provisional.")

        # Exports
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "Export comparison (CSV)",
                data=cmp_df.to_csv(index=False).encode("utf-8"),
                file_name="backtest_comparison.csv",
                mime="text/csv",
                key="bt_export_csv",
            )
        with col2:
            st.download_button(
                "Export full result (JSON)",
                data=json.dumps(res, indent=2, default=str).encode("utf-8"),
                file_name="backtest_result.json",
                mime="application/json",
                key="bt_export_json",
            )

    # ---- AI Copilot (Ask My Portfolio) ------------------------------------
    try:
        from copilot.ui import render_copilot
        render_copilot(ctx, "backtesting")
    except Exception:  # noqa: BLE001, S110
        pass
