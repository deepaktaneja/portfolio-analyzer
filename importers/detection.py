"""File-type detection helpers for statement imports."""
from __future__ import annotations

import os
from pathlib import Path

from importers.base import SourceDocument, sha256_file


def detect_pdf(path: str) -> str:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        text_chars = 0
        for page in pdf.pages:
            text_chars += len(page.extract_text() or "")
        if text_chars < 50:
            return "pdf_scanned"
        return "pdf_text"


def detect_tabular(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(2048)
    low = head.lower()
    if low.startswith(b"<") and (b"<html" in low or b"<table" in low or b"<!doctype" in low):
        return "html"
    if head.startswith(b"PK"):
        return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls_ole2"
    # TSV/CSV sniff
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            sample = f.read(2048)
    except Exception:  # noqa: BLE001
        return "binary"
    if "\t" in sample and sample.count("\t") >= 3:
        return "tsv"
    if "," in sample:
        return "csv"
    return "text"


def detect_source(path: str) -> SourceDocument:
    path = str(path)
    name = Path(path).name
    size = os.path.getsize(path)
    sha = sha256_file(path)
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        dtype = detect_pdf(path)
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            pages = len(pdf.pages)
    else:
        dtype = detect_tabular(path)
        pages = 0
    return SourceDocument(path=path, name=name, size=size, sha256=sha, detected_type=dtype, page_count=pages)