"""Validation rules for imported transactions."""
from __future__ import annotations

from importers.base import ImportPreview, NormalizedTransaction


def validate_transaction(tx: NormalizedTransaction) -> list[str]:
    issues = list(tx.issues)
    if not tx.isin or len(tx.isin) != 12:
        issues.append("invalid_isin")
    if tx.quantity is None or tx.quantity <= 0:
        issues.append("invalid_quantity")
    if tx.net_amount is None:
        issues.append("missing_net_amount")
    if not tx.settlement_date and not tx.trade_date:
        issues.append("missing_date")
    if tx.side not in ("BUY", "SELL"):
        issues.append("invalid_side")
    return issues


def build_preview(source_file: str, detected_type: str, transactions: list[NormalizedTransaction]) -> ImportPreview:
    preview = ImportPreview(source_file=source_file, detected_type=detected_type)
    seen: set[str] = set()
    duplicates = 0
    unresolved = set()
    rejected_rows = []
    accepted = 0
    for tx in transactions:
        issues = validate_transaction(tx)
        tx.issues = sorted(set(issues))
        if issues:
            preview.rejected += 1
            rejected_rows.append({"row_index": tx.row_index, "page": tx.page_number, "issues": issues, "isin": tx.isin})
        else:
            if tx.fingerprint in seen:
                duplicates += 1
                preview.duplicates += 1
            else:
                seen.add(tx.fingerprint)
                accepted += 1
        if (tx.isin is None or (tx.isin and len(tx.isin) != 12)) and tx.isin:
            unresolved.add(tx.isin)
        if tx.security is None:
            unresolved.add(tx.isin or "?")
    preview.extracted_rows = len(transactions)
    preview.accepted = accepted
    preview.rejected = len(rejected_rows)
    preview.rejected_rows = rejected_rows
    preview.unresolved_identifiers = sorted(unresolved)
    preview.transactions = transactions
    if duplicates:
        preview.warnings.append(f"{duplicates} duplicate transactions detected")
    if preview.rejected:
        preview.warnings.append(f"{preview.rejected} rows rejected")
    return preview