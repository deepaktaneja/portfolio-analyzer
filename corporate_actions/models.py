"""Corporate action event models (Stage 03)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class CorporateAction:
    security_id: str
    event_type: str
    ex_date: date | None = None
    record_date: date | None = None
    effective_date: date | None = None
    ratio: float | None = None
    subscription_price: float | None = None
    successor_id: str | None = None
    cost_allocation: float | None = None
    subscribed_quantity: float = 0.0
    sold_entitlement_quantity: float = 0.0
    expired: bool = False
    event_id: str | None = None
    verified: bool = False
    source: str = "manual"
    verification_status: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def credit_date(self) -> date:
        return self.effective_date or self.record_date or self.ex_date or date.min
