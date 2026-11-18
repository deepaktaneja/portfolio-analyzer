"""Stage 11 audit regression tests — independent reference cases + fixed defects."""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pytest

from src.xirr import compute_xirr


class Tx:
    """Minimal ORM-like MF transaction for duck-typed accounting tests."""

    def __init__(self, units, nav, amount, side, d="2024-01-01", code="S1"):
        self.scheme_code = code
        self.trade_date = d
        self.side = side
        self.units = Decimal(str(units))
        self.nav = Decimal(str(nav))
        self.amount = Decimal(str(amount))


# ---------------------------------------------------------------------------
# 1. XIRR reference cases (independent hand calculation)
# ---------------------------------------------------------------------------
def test_xirr_reference_two_year_doubling():
    x = compute_xirr([("2021-01-01", -1000.0), ("2023-01-01", 2000.0)])
    assert x == pytest.approx(math.sqrt(2) - 1, abs=1e-9)


def test_xirr_reference_leap_year_day_count():
    x = compute_xirr([("2020-01-01", -1000.0), ("2022-01-01", 2000.0)])
    assert x == pytest.approx(2 ** (365 / 731) - 1, abs=1e-6)


def test_xirr_requires_opposite_signs():
    assert compute_xirr([("2021-01-01", -1000.0), ("2021-06-01", -500.0)]) is None


# ---------------------------------------------------------------------------
# 2. Equity XIRR: verified dividends are inflows
# ---------------------------------------------------------------------------
def test_equity_xirr_dividends_are_positive_inflows():
    from performance.xirr import calculate_stock_xirr

    txs = [{"isin": "INE1", "buy_sell": "BUY", "trade_date": "2021-01-01",
            "quantity": 10, "net_amount": 1000.0}]
    divs = [{"security_id": "INE1", "gross": 50.0, "payment_date": "2021-06-01",
             "status": "confirmed"}]
    pl = lambda sid, d: 130.0
    with_div = calculate_stock_xirr(txs, dividends=divs, price_lookup=pl,
                                    security_id="INE1", end_date="2023-01-01")
    without = calculate_stock_xirr(txs, price_lookup=pl, security_id="INE1",
                                   end_date="2023-01-01")
    div_flows = [a for _, a, label in with_div["cashflows"] if label == "dividend"]
    assert div_flows and all(a > 0 for a in div_flows)
    assert with_div["xirr"] > without["xirr"]


# ---------------------------------------------------------------------------
# 3. MF unit reconstruction: switches, bonus, IDCW
# ---------------------------------------------------------------------------
def test_mf_units_include_switch_in_and_bonus():
    from mf.accounting import build_folio_units

    txs = [
        Tx(100, 10, 1000, "PURCHASE", code="A"),
        Tx(100, 10, 1000, "SWITCH_OUT", code="A"),
        Tx(50, 10, 500, "SWITCH_IN", code="B"),
        Tx(10, 10, 0, "BONUS", code="C"),
        Tx(5, 10, 50, "IDCW", code="A"),
    ]
    h = build_folio_units(txs)
    assert h["A"] == Decimal(0)
    assert h["B"] == Decimal(50)
    assert h["C"] == Decimal(10)


def test_mf_units_handle_lowercase_and_uppercase_sides():
    from mf.accounting import build_folio_units

    txs = [Tx(10, 10, 100, "purchase"), Tx(5, 10, 50, "bonus"), Tx(3, 10, 30, "switch_in")]
    assert build_folio_units(txs)["S1"] == Decimal(18)


# ---------------------------------------------------------------------------
# 4. MF cashflows: IDCW inflow, switch-in outflow, dict support
# ---------------------------------------------------------------------------
def test_mf_cashflows_idcw_positive_and_switch_semantics():
    from mf.accounting import build_fund_cashflows

    txs = [Tx(100, 10, 1000, "PURCHASE"), Tx(0, 0, 90, "IDCW"),
           Tx(50, 10, 500, "SWITCH_IN", code="S2")]
    flows = build_fund_cashflows(txs, "2024-12-31", Decimal(12))
    assert ("2024-01-01", Decimal(-1000), "buy") in flows
    assert ("2024-01-01", Decimal(90), "dividend") in flows
    assert ("2024-01-01", Decimal(-500), "buy") in flows


def test_mf_cashflows_accept_dict_transactions():
    from mf.accounting import build_fund_cashflows

    flows = build_fund_cashflows(
        [{"scheme_code": "S1", "trade_date": "2024-01-01", "side": "PURCHASE",
          "units": 10, "nav": 10, "amount": 100}], "2024-12-31", Decimal(12))
    assert any(label == "terminal_value" for _, _, label in flows)


# ---------------------------------------------------------------------------
# 5. FIFO realized / unrealized gains
# ---------------------------------------------------------------------------
def test_fifo_realized_gain_full_exit():
    from mf.accounting import calculate_realized_unrealized_gains

    txs = [Tx(100, 10, 1000, "PURCHASE", d="2024-01-01"),
           Tx(100, 15, 1500, "REDEMPTION", d="2024-06-01")]
    g = calculate_realized_unrealized_gains(txs, {"S1": Decimal(15)})["S1"]
    assert g["realized_gain"] == 500.0
    assert g["redeemed"] == 1500.0
    assert g["units"] == 0.0
    assert g["unrealized_gain"] == 0.0


def test_fifo_partial_exit_cost_basis():
    from mf.accounting import calculate_realized_unrealized_gains

    txs = [Tx(100, 10, 1000, "PURCHASE", d="2024-01-01"),
           Tx(100, 20, 2000, "PURCHASE", d="2024-02-01"),
           Tx(50, 20, 1000, "REDEMPTION", d="2024-03-01")]
    g = calculate_realized_unrealized_gains(txs, {"S1": Decimal(20)})["S1"]
    assert g["realized_gain"] == 500.0
    assert g["unrealized_gain"] == 500.0


def test_mf_backtesting_gains_delegate_matches_accounting():
    from mf.accounting import calculate_realized_unrealized_gains as acct
    from mf.backtesting import calculate_realized_unrealized_gains as bt

    txs = [{"scheme_code": "S1", "trade_date": "2024-01-01", "side": "PURCHASE",
            "units": 10, "amount": 100},
           {"scheme_code": "S1", "trade_date": "2024-06-01", "side": "REDEMPTION",
            "units": 10, "amount": 150}]
    nav = {"S1": Decimal(15)}
    assert bt(txs, nav)["S1"]["realized_gain"] == acct(txs, nav)["S1"]["realized_gain"] == 50.0


# ---------------------------------------------------------------------------
# 6. Portfolio MF XIRR: terminal value is net of redemptions
# ---------------------------------------------------------------------------
def test_portfolio_mf_xirr_net_of_redemptions():
    from mf.accounting import calculate_portfolio_mf_xirr

    txs = [Tx(100, 10, 1000, "PURCHASE", d="2024-01-01"),
           Tx(50, 12, 600, "REDEMPTION", d="2024-06-01")]
    res = calculate_portfolio_mf_xirr(txs, "2024-12-31", {"S1": Decimal(12)})
    terminal = [a for _, a, label in res["cashflows"] if label == "terminal_value"]
    assert len(terminal) == 1 and float(terminal[0]) == 600.0
    assert res["xirr"] is not None


# ---------------------------------------------------------------------------
# 7. Backtesting forward returns: horizon selection + no look-ahead
# ---------------------------------------------------------------------------
def test_forward_return_uses_nearest_observation_after_horizon():
    from decision.backtest import forward_return

    series = [(date(2024, 1, 1), 100.0), (date(2024, 4, 1), 110.0)]
    assert forward_return(series, date(2024, 1, 1), 90) == pytest.approx(0.10)


def test_backtest_point_in_time_never_uses_future_bar():
    from decision.backtest import price_as_of

    series = [(date(2024, 1, 1), 100.0), (date(2024, 2, 1), 110.0)]
    assert price_as_of(series, date(2024, 1, 15)) == 100.0


# ---------------------------------------------------------------------------
# 8. Corporate-action de-duplication is idempotent
# ---------------------------------------------------------------------------
def test_corporate_action_dedupe_idempotent():
    from corporate_actions.engine import reconstruct_with_actions
    from corporate_actions.models import CorporateAction

    tx = [{"isin": "INE1", "buy_sell": "BUY", "trade_date": "2023-01-01",
           "quantity": 10, "net_amount": 1000}]
    split = CorporateAction(security_id="INE1", event_type="split",
                            ex_date="2023-06-01", ratio=2.0)
    once = reconstruct_with_actions(tx, [split], "2024-01-01")[0]
    twice = reconstruct_with_actions(tx, [split, split], "2024-01-01")[0]
    assert once == twice == {"INE1": 20.0}


# ---------------------------------------------------------------------------
# 9. Opening holdings included in reconstruction
# ---------------------------------------------------------------------------
def test_opening_positions_merged_in_reconstruction():
    from ledger.positions import reconstruct

    txs = [{"isin": "INE1", "buy_sell": "BUY", "trade_date": "2024-06-01", "quantity": 5}]
    ops = [{"security_id": "INE1", "opening_qty": 10, "ledger_opening_date": "2024-01-01"}]
    assert reconstruct(txs, "2024-12-31", opening_positions=ops).get("INE1") == 15


# ---------------------------------------------------------------------------
# 10. Documented limitation: XIRR layer does not apply split/bonus quantities
# ---------------------------------------------------------------------------
def test_documented_limitation_xirr_ignores_ca_quantities():
    """Locks the known gap documented in docs/financial_audit.md (High, needs approval)."""
    from corporate_actions.engine import reconstruct_with_actions
    from corporate_actions.models import CorporateAction
    from performance.xirr import _holdings_as_of

    tx = [{"isin": "INE1", "buy_sell": "BUY", "trade_date": "2023-01-01",
           "quantity": 10, "net_amount": 1000}]
    split = CorporateAction(security_id="INE1", event_type="split",
                            ex_date="2023-06-01", ratio=2.0)
    ca_qty = reconstruct_with_actions(tx, [split], "2024-01-01")[0]["INE1"]
    xirr_qty = _holdings_as_of(tx, "2024-01-01")["INE1"]
    assert ca_qty == 20.0 and xirr_qty == 10.0