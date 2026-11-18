"""MF data integration (Stage 7): NAV providers, cache, and disclosure/metadata loaders.

- NAV sources: AMFI/MFAPI.in (free, primary) or mftool (optional if installed).
- Caching: persisted to ``mf_nav_cache`` (isolated analytics DB) with source +
  provider + fetched_at, and freshness helpers.
- Disclosures (scheme stock holdings) and fund metadata are loaded from
  user-provided AMFI/AMC CSVs with explicit source tracking — never invented.

Network access is best-effort and always failure-tolerant; callers receive an
empty result plus a warning rather than an exception.
"""
from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path

from models.mf_analytics import (
    MFFundMeta,
    MFNavCache,
    MFSchemeHolding,
    get_mf_analytics_session,
)

MFAPI_BASE = "https://api.mfapi.in/mf"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_mfapi_date(d: str) -> str | None:
    """MFAPI returns DD-MM-YYYY; normalise to ISO YYYY-MM-DD."""
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(d).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


class MFAPINavProvider:
    """Free AMFI-nav provider via api.mfapi.in (scheme_code = AMFI code)."""

    name = "mfapi"

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def _get(self, scheme_code: str) -> dict | None:
        try:
            import httpx
            resp = httpx.get(f"{MFAPI_BASE}/{scheme_code}", timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception:  # noqa: BLE001
            return None

    def get_nav_history(self, scheme_code: str) -> list[tuple[str, float]]:
        payload = self._get(scheme_code)
        if not payload:
            return []
        out: list[tuple[str, float]] = []
        for row in payload.get("data", []) or []:
            iso = _parse_mfapi_date(row.get("date", ""))
            try:
                nav = float(row.get("nav"))
            except (TypeError, ValueError):
                continue
            if iso and nav > 0:
                out.append((iso, nav))
        out.sort(key=lambda x: x[0])
        return out

    def get_scheme_details(self, scheme_code: str) -> dict:
        payload = self._get(scheme_code) or {}
        meta = payload.get("meta", {}) or {}
        return {
            "scheme_code": scheme_code,
            "scheme_name": meta.get("scheme_name"),
            "fund_house": meta.get("fund_house"),
            "category": meta.get("scheme_category"),
            "source": "mfapi",
        }


class MftoolNavProvider:
    """Optional provider using the ``mftool`` package if installed."""

    name = "mftool"

    def get_nav_history(self, scheme_code: str) -> list[tuple[str, float]]:
        try:
            from mftool import Mftool  # type: ignore
        except ImportError:
            return []
        try:
            mf = Mftool()
            hist = mf.get_scheme_historical_nav(scheme_code) or {}
            rows = hist.get("data", []) if isinstance(hist, dict) else []
            out = []
            for row in rows:
                iso = _parse_mfapi_date(row.get("date", ""))
                try:
                    nav = float(row.get("nav"))
                except (TypeError, ValueError):
                    continue
                if iso and nav > 0:
                    out.append((iso, nav))
            out.sort(key=lambda x: x[0])
            return out
        except Exception:  # noqa: BLE001
            return []

    def get_scheme_details(self, scheme_code: str) -> dict:
        try:
            from mftool import Mftool  # type: ignore
            mf = Mftool()
            return {"scheme_code": scheme_code, "scheme_name": mf.get_scheme_quote(scheme_code) or None, "source": "mftool"}
        except Exception:  # noqa: BLE001
            return {"scheme_code": scheme_code, "source": "mftool"}


PROVIDERS = {"mfapi": MFAPINavProvider, "mftool": MftoolNavProvider}


class MFDataService:
    """Coordinates NAV providers + the analytics cache with freshness tracking."""

    def __init__(self, provider: str = "mfapi", session=None):
        self.provider_name = provider
        self._provider = PROVIDERS.get(provider, MFAPINavProvider)()
        self._session = session

    def _ctx(self):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            if self._session is not None:
                yield self._session
            else:
                s = get_mf_analytics_session()
                try:
                    yield s
                finally:
                    s.close()

        return _cm()

    def cached_nav_history(self, scheme_code: str) -> list[tuple[str, float]]:
        """Read NAV history from the analytics cache (no network)."""
        with self._ctx() as s:
            rows = (
                s.query(MFNavCache)
                .filter_by(scheme_code=scheme_code)
                .order_by(MFNavCache.as_of_date)
                .all()
            )
            return [(r.as_of_date, float(r.nav)) for r in rows]

    def cached_scheme_codes(self) -> list[str]:
        """Distinct scheme codes that have cached NAV observations."""
        with self._ctx() as s:
            rows = s.query(MFNavCache.scheme_code).distinct().all()
            return [r[0] for r in rows if r[0]]

    def _store_nav(self, scheme_code: str, records: list[tuple[str, float]]) -> None:
        if not records:
            return
        with self._ctx() as s:
            for iso, nav in records:
                row = (
                    s.query(MFNavCache)
                    .filter_by(scheme_code=scheme_code, as_of_date=iso)
                    .first()
                )
                if row is None:
                    row = MFNavCache(
                        scheme_code=scheme_code, as_of_date=iso, nav=nav,
                        source="amfi", provider=self.provider_name, fetched_at=_now_iso(),
                    )
                    s.add(row)
                else:
                    row.nav = nav
                    row.provider = self.provider_name
                    row.fetched_at = _now_iso()
            s.commit()

    def store_nav_records(self, scheme_code: str, records, source: str = "amfi_disclosure") -> int:
        """Upsert explicit NAV observations (e.g. from a current-valuation statement).

        ``records`` is an iterable of ``(iso_date, nav)``. Returns the number of
        rows written. Empty/invalid observations are ignored.
        """
        clean: list[tuple[str, float]] = []
        for d, v in records or []:
            try:
                iso = str(d)[:10]
                nav = float(v)
            except (TypeError, ValueError):
                continue
            if iso and len(iso) == 10 and nav > 0:
                clean.append((iso, nav))
        if not clean:
            return 0
        with self._ctx() as s:
            n = 0
            for iso, nav in clean:
                row = (
                    s.query(MFNavCache)
                    .filter_by(scheme_code=scheme_code, as_of_date=iso)
                    .first()
                )
                if row is None:
                    row = MFNavCache(
                        scheme_code=scheme_code, as_of_date=iso, nav=nav,
                        source=source, provider=self.provider_name, fetched_at=_now_iso(),
                    )
                    s.add(row)
                else:
                    row.nav = nav
                    row.source = source
                    row.provider = self.provider_name
                    row.fetched_at = _now_iso()
                n += 1
            s.commit()
            return n

    def get_nav_history(self, scheme_code: str, use_cache: bool = True) -> tuple[list[tuple[str, float]], str]:
        """Return (nav_records, status). Status: cache|fetched|empty."""
        cached = self.cached_nav_history(scheme_code) if use_cache else []
        if cached:
            return cached, "cache"
        fetched = self._provider.get_nav_history(scheme_code)
        if fetched:
            self._store_nav(scheme_code, fetched)
            return fetched, "fetched"
        return [], "empty"

    def get_latest_nav(self, scheme_code: str, use_cache: bool = True) -> float | None:
        records, _ = self.get_nav_history(scheme_code, use_cache=use_cache)
        return records[-1][1] if records else None

    def nav_freshness(self, scheme_code: str) -> dict:
        """Return latest cached NAV date + age in days."""
        records = self.cached_nav_history(scheme_code)
        if not records:
            return {"scheme_code": scheme_code, "latest_date": None, "age_days": None, "status": "missing"}
        latest = records[-1][0]
        try:
            age = (date.today() - date.fromisoformat(latest)).days
        except ValueError:
            age = None
        return {"scheme_code": scheme_code, "latest_date": latest, "age_days": age,
                "status": "ok" if (age is not None and age <= 7) else "stale"}

    def get_scheme_details(self, scheme_code: str) -> dict:
        details = self._provider.get_scheme_details(scheme_code)
        self._store_metadata(details)
        return details

    def _store_metadata(self, details: dict) -> None:
        if not details:
            return
        with self._ctx() as s:
            row = s.query(MFFundMeta).filter_by(scheme_code=details["scheme_code"]).first()
            if row is None:
                row = MFFundMeta(scheme_code=details["scheme_code"])
                s.add(row)
            row.scheme_name = details.get("scheme_name") or row.scheme_name
            row.fund_house = details.get("fund_house") or row.fund_house
            row.category = details.get("category") or row.category
            row.source = details.get("source") or "mfapi"
            row.fetched_at = _now_iso()
            s.commit()


def is_fresh(as_of_date: str | None, max_days: int = 45) -> bool:
    """True if ``as_of_date`` is within ``max_days`` of today."""
    if not as_of_date:
        return False
    try:
        return (date.today() - date.fromisoformat(str(as_of_date)[:10])).days <= max_days
    except (ValueError, TypeError):
        return False


def _norm_holding_row(row: dict) -> dict | None:
    isin = str(row.get("stock_isin") or row.get("isin") or "").strip().upper()
    scheme = str(row.get("scheme_code") or row.get("scheme") or "").strip()
    try:
        weight = float(row.get("weight_pct") or row.get("weight") or 0)
    except (TypeError, ValueError):
        return None
    as_of = str(row.get("as_of_date") or row.get("as_of") or "").strip()[:10]
    if not isin or not scheme or weight <= 0:
        return None
    return {
        "scheme_code": scheme,
        "as_of_date": as_of,
        "stock_isin": isin,
        "stock_name": row.get("stock_name") or row.get("name"),
        "weight_pct": weight,
        "sector": row.get("sector"),
        "market_cap": row.get("market_cap") or row.get("cap"),
        "source": row.get("source") or "amfi_disclosure",
    }


def load_scheme_holdings_csv(path: str | Path) -> list[dict]:
    """Load scheme holdings disclosures from a CSV (AMFI/AMC factsheet export)."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            norm = _norm_holding_row(row)
            if norm:
                out.append(norm)
    return out


def save_scheme_holdings(records: list[dict], session=None) -> int:
    """Persist normalised holding records into the analytics DB. Returns count saved."""
    if not records:
        return 0

    def _do(s):
        n = 0
        for rec in records:
            row = (
                s.query(MFSchemeHolding)
                .filter_by(
                    scheme_code=rec["scheme_code"],
                    as_of_date=rec["as_of_date"],
                    stock_isin=rec["stock_isin"],
                )
                .first()
            )
            if row is None:
                row = MFSchemeHolding(
                    scheme_code=rec["scheme_code"], as_of_date=rec["as_of_date"],
                    stock_isin=rec["stock_isin"],
                )
                s.add(row)
            row.stock_name = rec.get("stock_name")
            row.weight_pct = rec["weight_pct"]
            row.sector = rec.get("sector")
            row.market_cap = rec.get("market_cap")
            row.source = rec.get("source")
            row.fetched_at = _now_iso()
            n += 1
        s.commit()
        return n

    if session is not None:
        return _do(session)
    s = get_mf_analytics_session()
    try:
        return _do(s)
    finally:
        s.close()


def load_scheme_holdings_from_store(session=None) -> tuple[dict[str, list[dict]], dict[str, str | None]]:
    """Return latest disclosed holdings per scheme + their as_of_date map."""
    def _do(s):
        rows = s.query(MFSchemeHolding).all()
        latest: dict[str, str] = {}
        for r in rows:
            if r.scheme_code not in latest or (r.as_of_date and r.as_of_date > latest[r.scheme_code]):
                latest[r.scheme_code] = r.as_of_date
        holdings: dict[str, list[dict]] = {sc: [] for sc in latest}
        for r in rows:
            if r.as_of_date == latest.get(r.scheme_code):
                holdings[r.scheme_code].append({
                    "stock_isin": r.stock_isin,
                    "stock_name": r.stock_name,
                    "weight_pct": float(r.weight_pct),
                    "sector": r.sector,
                    "market_cap": r.market_cap,
                })
        return holdings, latest

    if session is not None:
        return _do(session)
    s = get_mf_analytics_session()
    try:
        return _do(s)
    finally:
        s.close()


def _to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def load_fund_metadata_csv(path: str | Path) -> list[dict]:
    """Load fund metadata (category, benchmark, expense ratio, AUM, manager) from CSV."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = str(row.get("scheme_code") or "").strip()
            if not code:
                continue
            out.append({
                "scheme_code": code,
                "scheme_name": row.get("scheme_name"),
                "fund_house": row.get("fund_house"),
                "category": row.get("category"),
                "benchmark": row.get("benchmark"),
                "expense_ratio_regular": _to_float(row.get("expense_ratio_regular")),
                "expense_ratio_direct": _to_float(row.get("expense_ratio_direct")),
                "aum_crore": _to_float(row.get("aum_crore")),
                "fund_manager": row.get("fund_manager"),
                "as_of_date": str(row.get("as_of_date") or "")[:10] or None,
                "source": row.get("source") or "amfi_disclosure",
            })
    return out


def save_fund_metadata(records: list[dict], session=None) -> int:
    """Persist fund metadata records into the analytics DB. Returns count saved."""
    if not records:
        return 0

    def _do(s):
        n = 0
        for rec in records:
            row = s.query(MFFundMeta).filter_by(scheme_code=rec["scheme_code"]).first()
            if row is None:
                row = MFFundMeta(scheme_code=rec["scheme_code"])
                s.add(row)
            for field in ("scheme_name", "fund_house", "category", "benchmark",
                          "expense_ratio_regular", "expense_ratio_direct", "aum_crore",
                          "fund_manager", "as_of_date", "source"):
                if rec.get(field) is not None:
                    setattr(row, field, rec[field])
            row.fetched_at = _now_iso()
            n += 1
        s.commit()
        return n

    if session is not None:
        return _do(session)
    s = get_mf_analytics_session()
    try:
        return _do(s)
    finally:
        s.close()


def get_fund_metadata_map(session=None) -> dict[str, dict]:
    """Return {scheme_code: metadata-dict} from the analytics DB."""
    def _do(s):
        out: dict[str, dict] = {}
        for r in s.query(MFFundMeta).all():
            out[r.scheme_code] = {
                "scheme_code": r.scheme_code, "scheme_name": r.scheme_name,
                "fund_house": r.fund_house, "category": r.category, "benchmark": r.benchmark,
                "expense_ratio_regular": float(r.expense_ratio_regular) if r.expense_ratio_regular is not None else None,
                "expense_ratio_direct": float(r.expense_ratio_direct) if r.expense_ratio_direct is not None else None,
                "aum_crore": float(r.aum_crore) if r.aum_crore is not None else None,
                "fund_manager": r.fund_manager, "as_of_date": r.as_of_date, "source": r.source,
            }
        return out

    if session is not None:
        return _do(session)
    s = get_mf_analytics_session()
    try:
        return _do(s)
    finally:
        s.close()

        s.close()