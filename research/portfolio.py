"""Portfolio-aware research: integrates existing portfolio data (weight, invested
capital, XIRR, unrealized gains) with company research to identify concentration,
valuation, and fundamental risks.

Does NOT generate sell/trim signals — those are reserved for Stage 8.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.holdings import load_holdings
from src.store import SQLiteStore
from src.xirr import annualize_pct, compute_xirr


@dataclass
class HoldingContext:
    """Portfolio context for a single holding."""
    symbol: str
    isin: str | None
    quantity: Decimal
    avg_price: Decimal
    invested_capital: Decimal
    current_price: Decimal | None
    current_value: Decimal | None
    unrealized_gain: Decimal | None
    unrealized_gain_pct: Decimal | None
    weight_pct: Decimal | None
    xirr: Decimal | None
    xirr_annualized: float | None


@dataclass
class PortfolioResearchContext:
    """Aggregate portfolio context for research."""
    holdings: list[HoldingContext] = field(default_factory=list)
    total_invested: Decimal = Decimal(0)
    total_current_value: Decimal = Decimal(0)
    total_unrealized_gain: Decimal = Decimal(0)
    portfolio_xirr: float | None = None
    concentration_risk: list[dict[str, Any]] = field(default_factory=list)
    valuation_risk: list[dict[str, Any]] = field(default_factory=list)
    fundamental_risk: list[dict[str, Any]] = field(default_factory=list)


class PortfolioResearchIntegrator:
    """Integrates portfolio accounting data with research context."""

    def __init__(self, db_path: str = "data/xirr.sqlite3", portfolio_folder: str = "data/portfolio"):
        self.db_path = db_path
        self.portfolio_folder = portfolio_folder

    def load_portfolio_holdings(self) -> list[dict[str, Any]]:
        """Load current portfolio holdings from the portfolio XLS/TSV folder."""
        try:
            df = load_holdings(self.portfolio_folder)
            if df is None or df.empty:
                return []
            return df.to_dict("records")
        except Exception:  # noqa: BLE001
            return []

    def load_transactions(self, db_path: str | None = None) -> list[dict[str, Any]]:
        """Load all transactions from the SQLite store."""
        path = db_path or self.db_path
        try:
            store = SQLiteStore(path)
            return store.all_transactions()
        except Exception:  # noqa: BLE001
            return []

    def build_holding_context(
        self,
        symbol: str,
        isin: str | None,
        holdings_data: list[dict[str, Any]],
        transactions: list[dict[str, Any]],
        current_price_lookup=None,
    ) -> HoldingContext | None:
        """Build a HoldingContext for a single security from portfolio data."""
        holding = None
        for h in holdings_data:
            h_isin = str(h.get("isin", "")).strip().upper()
            h_symbol = str(h.get("symbol", "")).strip().upper()
            if (isin and h_isin == str(isin).strip().upper()) or h_symbol == str(symbol).strip().upper():
                holding = h
                break

        if holding is None:
            return None

        quantity = Decimal(str(holding.get("quantity") or 0))
        avg_price = Decimal(str(holding.get("avg_price") or 0))
        invested_capital = quantity * avg_price

        cp = None
        if current_price_lookup is not None:
            try:
                cp = current_price_lookup(symbol, None)
            except Exception:  # noqa: BLE001
                cp = None
        if cp is None:
            cp = holding.get("current_price")
        current_price = Decimal(str(cp)) if cp is not None else None

        current_value = None
        if current_price is not None:
            current_value = quantity * current_price
        else:
            current_value = holding.get("current_value")
            if current_value is not None:
                current_value = Decimal(str(current_value))

        unrealized_gain = None
        unrealized_gain_pct = None
        if current_value is not None and invested_capital > 0:
            unrealized_gain = current_value - invested_capital
            unrealized_gain_pct = (unrealized_gain / invested_capital * Decimal(100)).quantize(Decimal("0.01"))

        xirr = None
        xirr_annualized = None
        security_tx = [
            t for t in transactions
            if str(t.get("isin", "")).strip().upper() == str(isin or "").strip().upper()
            or str(t.get("security", "")).strip().upper() == str(symbol).strip().upper()
        ]
        if security_tx and current_price is not None:
            cashflows = []
            for t in security_tx:
                td = t.get("trade_date") or t.get("settlement_date")
                amt = t.get("net_amount") or 0
                side = str(t.get("buy_sell", "")).upper()
                if side == "BUY":
                    cashflows.append((td, -abs(float(amt))))
                elif side == "SELL":
                    cashflows.append((td, abs(float(amt))))
            if cashflows:
                last_date = max(cf[0] for cf in cashflows)
                cashflows.append((last_date, float(current_value or 0)))
                try:
                    xirr_val = compute_xirr(cashflows)
                    if xirr_val is not None:
                        xirr = Decimal(str(xirr_val))
                        xirr_annualized = annualize_pct(xirr_val)
                except Exception:  # noqa: BLE001, S110
                    pass

        return HoldingContext(
            symbol=symbol,
            isin=isin,
            quantity=quantity,
            avg_price=avg_price,
            invested_capital=invested_capital,
            current_price=current_price,
            current_value=current_value,
            unrealized_gain=unrealized_gain,
            unrealized_gain_pct=unrealized_gain_pct,
            weight_pct=None,
            xirr=xirr,
            xirr_annualized=xirr_annualized,
        )

    def build_portfolio_context(
        self,
        symbols: list[str],
        isins: dict[str, str | None],
        current_price_lookup=None,
    ) -> PortfolioResearchContext:
        """Build aggregate portfolio context for a list of symbols."""
        holdings_data = self.load_portfolio_holdings()
        transactions = self.load_transactions()

        ctx = PortfolioResearchContext()
        total_invested = Decimal(0)
        total_current_value = Decimal(0)

        holding_contexts: list[HoldingContext] = []
        for symbol in symbols:
            isin = isins.get(symbol)
            hc = self.build_holding_context(
                symbol=symbol,
                isin=isin,
                holdings_data=holdings_data,
                transactions=transactions,
                current_price_lookup=current_price_lookup,
            )
            if hc is not None:
                total_invested += hc.invested_capital
                if hc.current_value is not None:
                    total_current_value += hc.current_value
                holding_contexts.append(hc)

        for hc in holding_contexts:
            if total_current_value > 0 and hc.current_value is not None:
                hc.weight_pct = _r2(hc.current_value / total_current_value * Decimal(100))

        ctx.holdings = holding_contexts
        ctx.total_invested = total_invested
        ctx.total_current_value = total_current_value

        if total_invested > 0:
            ctx.total_unrealized_gain = total_current_value - total_invested

        if transactions:
            try:
                all_cashflows = []
                for t in transactions:
                    td = t.get("trade_date") or t.get("settlement_date")
                    amt = t.get("net_amount") or 0
                    side = str(t.get("buy_sell", "")).upper()
                    if side == "BUY":
                        all_cashflows.append((td, -abs(float(amt))))
                    elif side == "SELL":
                        all_cashflows.append((td, abs(float(amt))))
                if all_cashflows:
                    px = compute_xirr(all_cashflows)
                    if px is not None:
                        ctx.portfolio_xirr = annualize_pct(px)
            except Exception:  # noqa: BLE001, S110
                pass

        for hc in holding_contexts:
            if hc.weight_pct is not None and hc.weight_pct > Decimal(25):
                ctx.concentration_risk.append({
                    "symbol": hc.symbol,
                    "weight_pct": float(hc.weight_pct),
                    "reason": f"Single holding weight {hc.weight_pct}% exceeds 25% concentration threshold",
                })

        for hc in holding_contexts:
            if hc.unrealized_gain_pct is not None and hc.unrealized_gain_pct > Decimal(100):
                ctx.valuation_risk.append({
                    "symbol": hc.symbol,
                    "unrealized_gain_pct": float(hc.unrealized_gain_pct),
                    "reason": f"Unrealized gain of {hc.unrealized_gain_pct}% may indicate extended valuation — monitor",
                })

        for hc in holding_contexts:
            if hc.xirr_annualized is not None and hc.xirr_annualized < -0.20:
                ctx.fundamental_risk.append({
                    "symbol": hc.symbol,
                    "xirr_annualized": hc.xirr_annualized,
                    "reason": f"XIRR of {hc.xirr_annualized:.1%} indicates sustained capital erosion",
                })
            if hc.unrealized_gain_pct is not None and hc.unrealized_gain_pct < Decimal(-30):
                ctx.fundamental_risk.append({
                    "symbol": hc.symbol,
                    "unrealized_gain_pct": float(hc.unrealized_gain_pct),
                    "reason": f"Unrealised loss of {abs(hc.unrealized_gain_pct)}% — fundamental review warranted",
                })

        return ctx


def _r2(value: Decimal) -> Decimal:
    from decimal import ROUND_HALF_UP
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)