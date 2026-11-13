"""MF NAV adapter + local cache (Stage 3/4)."""
from __future__ import annotations
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable

@runtime_checkable
class MFNavAdapter(Protocol):
    """Abstract NAV adapter protocol."""
    name: str

    def get_latest_nav(self, scheme_code: str, plan_type: Optional[str] = None) -> Optional[Decimal]:
        ...
    def get_historical_nav(self, scheme_code: str, as_of_date: date, plan_type: Optional[str] = None) -> Optional[Decimal]:
        ...
    def refresh_cache(self, scheme_codes: list[str]) -> dict[str, str]:
        ...

class ManualCsvNavAdapter:
    """Manual CSV-based NAV provider."""
    def __init__(self, path: str | None = None):
        self.path = path
        self.name = "manual_csv"

    def get_latest_nav(self, scheme_code: str, plan_type: Optional[str] = None) -> Optional[Decimal]:
        return None

    def get_historical_nav(self, scheme_code: str, as_of_date: date, plan_type: Optional[str] = None) -> Optional[Decimal]:
        return None

    def refresh_cache(self, scheme_codes: list[str]) -> dict[str, str]:
        return {sc: "missing" for sc in scheme_codes}

def create_nav_adapter(provider: str = "manual", **kwargs) -> MFNavAdapter:
    """Factory for NAV adapters."""
    if provider == "manual":
        return ManualCsvNavAdapter(**kwargs)
    raise ValueError(f"Unknown NAV provider: {provider}")
print("mf/nav.py created")
