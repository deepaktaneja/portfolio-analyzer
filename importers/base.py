"""Shared data contracts for statement imports (Stage 01)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SourceDocument:
    path: str
    name: str
    size: int
    sha256: str
    detected_type: str
    page_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawRow:
    source_file: str
    page_number: int
    table_index: int
    row_index: int
    cells: list[Any]
    extraction_method: str = "pdfplumber"


@dataclass
class NormalizedTransaction:
    source_file: str
    page_number: int
    row_index: int
    trade_date: str | None = None
    settlement_date: str | None = None
    exchange: str | None = None
    order_no: str | None = None
    trade_no: str | None = None
    isin: str | None = None
    security: str | None = None
    side: str | None = None  # BUY / SELL
    quantity: float | None = None
    gross_rate: float | None = None
    gross_amount: float | None = None
    brokerage: float | None = None
    gst: float | None = None
    charges: float | None = None
    net_amount: float | None = None
    fingerprint: str = ""
    confidence: float = 1.0
    issues: list[str] = field(default_factory=list)
    raw_cells: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["raw_cells"] = json.dumps(d["raw_cells"], default=str)
        d["issues"] = json.dumps(d["issues"])
        return d


@dataclass
class ImportPreview:
    source_file: str
    detected_type: str
    extracted_rows: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    warnings: list[str] = field(default_factory=list)
    unresolved_identifiers: list[str] = field(default_factory=list)
    rejected_rows: list[dict[str, Any]] = field(default_factory=list)
    transactions: list[NormalizedTransaction] = field(default_factory=list)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint_transaction(t: NormalizedTransaction) -> str:
    canon = "|".join(
        str(x)
        for x in (
            t.settlement_date or t.trade_date,
            t.isin,
            t.side,
            t.quantity,
            t.net_amount,
            t.trade_no,
            t.order_no,
        )
    )
    return hashlib.sha256(canon.encode()).hexdigest()