"""Normalization interface for statement parsers."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from importers.base import NormalizedTransaction, RawRow, SourceDocument


@runtime_checkable
class TransactionParser(Protocol):
    name: str

    def detect(self, doc: SourceDocument) -> bool: ...

    def parse(self, doc: SourceDocument) -> list[RawRow]: ...

    def normalize(self, raw: RawRow) -> list[NormalizedTransaction]: ...


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: list[TransactionParser] = []

    def register(self, parser: TransactionParser) -> None:
        self._parsers.append(parser)

    def find(self, doc: SourceDocument) -> TransactionParser | None:
        for p in self._parsers:
            if p.detect(doc):
                return p
        return None