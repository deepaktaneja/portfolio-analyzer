"""MF statement importer: CSV, Excel, CAS statements."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional
from models.mf import MFScheme, MFPlan, MFNav, MFTransaction, MFPortfolio

SIDE_ALIASES = {
    "sip": "SIP",
    "purchase": "PURCHASE",
    "lump_sum": "PURCHASE",
    "redeem": "REDEMPTION",
    "redemption": "REDEMPTION",
    "withdraw": "REDEMPTION",
    "switch_in": "SWITCH_IN",
    "switch_out": "SWITCH_OUT",
    "stp": "STP",
    "swp": "SWP",
    "idcw": "IDCW",
    "dividend": "IDCW",
    "reinvest": "PURCHASE",
    "buy": "PURCHASE",
    "sell": "REDEMPTION",
    "exit": "REDEMPTION",
    "invest": "PURCHASE",
    "investment": "PURCHASE",
    "subscription": "PURCHASE",
    "allotment": "PURCHASE",
    "bonus": "PURCHASE",
    "growth": "PURCHASE",
    "accumulation": "PURCHASE",
}
