"""Tests for PersonStore multi-person tagging."""
from __future__ import annotations

from src.people import PersonStore


def _store(tmp_path):
    return PersonStore(str(tmp_path / "p.sqlite3"))


def test_create_and_list_people(tmp_path):
    s = _store(tmp_path)
    a = s.create_person("Anmol")
    b = s.create_person("Father")
    assert a != b
    names = [p["name"] for p in s.list_people()]
    assert "Anmol" in names and "Father" in names


def test_create_person_dedupes_case_insensitive(tmp_path):
    s = _store(tmp_path)
    a = s.create_person("Anmol")
    again = s.create_person("anmol")
    assert a == again


def test_rename_and_archive(tmp_path):
    s = _store(tmp_path)
    a = s.create_person("Anmol")
    s.rename_person(a, "Anmol K")
    assert s.get_person_by_id(a)["name"] == "Anmol K"
    s.set_person_active(a, False)
    assert s.get_person_by_id(a)["active"] == 0
    assert s.list_people() == []


def test_transactions_tagged_and_filtered(tmp_path):
    s = _store(tmp_path)
    a = s.create_person("A")
    b = s.create_person("B")
    s.replace_file_transactions("fa", [{"trade_date": "2024-01-01", "isin": "I1", "buy_sell": "BUY", "quantity": 1, "net_amount": 100}], person_id=a)
    s.replace_file_transactions("fb", [{"trade_date": "2024-01-02", "isin": "I2", "buy_sell": "BUY", "quantity": 2, "net_amount": 200}], person_id=b)
    assert len(s.all_transactions()) == 2
    assert len(s.all_transactions(a)) == 1
    assert len(s.all_transactions(b)) == 1
    assert s.person_transaction_counts() == {a: 1, b: 1}


def test_unassigned_filter(tmp_path):
    s = _store(tmp_path)
    s.replace_file_transactions("fx", [{"trade_date": "2024-01-01", "isin": "I1", "buy_sell": "BUY", "quantity": 1, "net_amount": 100}])
    assert len(s.all_transactions(-1)) == 1


def test_holdings_snapshot_per_person(tmp_path):
    s = _store(tmp_path)
    a = s.create_person("A")
    s.add_portfolio_snapshot("h.xls", "2024-12-31", "equity", [{"isin": "I1", "symbol": "X", "quantity": 10, "avg_price": 100, "current_price": 150, "market_value": 1500}], person_id=a)
    rows = s.get_latest_holdings(a)
    assert len(rows) == 1 and rows[0]["isin"] == "I1"
