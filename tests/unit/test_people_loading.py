"""Tests for person-aware loading via app.shared."""
from __future__ import annotations

from src.people import PersonStore


def _db(tmp_path):
    return str(tmp_path / "x.sqlite3")


def test_load_transactions_filters_by_person(tmp_path):
    from app.shared import load_transactions
    db = _db(tmp_path)
    s = PersonStore(db)
    a = s.create_person("A")
    b = s.create_person("B")
    s.replace_file_transactions("fa", [{"trade_date": "2024-01-01", "isin": "I1", "buy_sell": "BUY", "quantity": 1, "net_amount": 100}], person_id=a)
    s.replace_file_transactions("fb", [{"trade_date": "2024-01-02", "isin": "I2", "buy_sell": "BUY", "quantity": 1, "net_amount": 200}], person_id=b)
    assert len(load_transactions(db)) == 2
    assert len(load_transactions(db, a)) == 1
    assert len(load_transactions(db, b)) == 1


def test_load_holdings_df_by_person(tmp_path, monkeypatch):
    from app.shared import load_holdings_df
    db = _db(tmp_path)
    monkeypatch.setenv("ICICI_XIRR_DB_PATH", db)
    s = PersonStore(db)
    a = s.create_person("A")
    s.add_portfolio_snapshot("h.xls", "2024-12-31", "equity", [{"isin": "I1", "symbol": "X", "quantity": 10, "avg_price": 100, "current_price": 150, "market_value": 1500}], person_id=a)
    df = load_holdings_df(person_id=a)
    assert df is not None and len(df) == 1
    assert float(df.iloc[0]["current_value"]) == 1500.0
    assert load_holdings_df(person_id=9999) is None


def test_unassigned_visible(tmp_path):
    from app.shared import load_transactions
    db = _db(tmp_path)
    s = PersonStore(db)
    s.replace_file_transactions("fx", [{"trade_date": "2024-01-01", "isin": "I1", "buy_sell": "BUY", "quantity": 1, "net_amount": 100}])
    assert len(load_transactions(db, -1)) == 1


def test_all_people_excludes_unassigned(tmp_path):
    from app.shared import load_transactions
    db = _db(tmp_path)
    s = PersonStore(db)
    a = s.create_person("A")
    s.replace_file_transactions("fa", [{"trade_date": "2024-01-01", "isin": "I1", "buy_sell": "BUY", "quantity": 1, "net_amount": 100}], person_id=a)
    s.replace_file_transactions("fx", [{"trade_date": "2024-01-02", "isin": "I2", "buy_sell": "BUY", "quantity": 1, "net_amount": 200}])
    assert len(load_transactions(db)) == 1
    assert len(load_transactions(db, -1)) == 1


def test_resolve_person_name_by_id_and_scope(tmp_path):
    from app.shared import resolve_person_name
    db = _db(tmp_path)
    s = PersonStore(db)
    a = s.create_person("A")
    b = s.create_person("B")
    # Explicit name wins.
    assert resolve_person_name(db, a, "A") == "A"
    # Missing name resolves from the store by id (prevents person -> all-people collapse).
    assert resolve_person_name(db, a, None) == "A"
    assert resolve_person_name(db, b, None) == "B"
    # Aggregate scope stays None.
    assert resolve_person_name(db, None, None) is None
    # Unknown id does not accidentally resolve to a name.
    assert resolve_person_name(db, 99999, None) is None


def test_all_people_holdings_aggregate(tmp_path, monkeypatch):
    from app.shared import load_holdings_df
    db = _db(tmp_path)
    monkeypatch.setenv("ICICI_XIRR_DB_PATH", db)
    s = PersonStore(db)
    a = s.create_person("A")
    b = s.create_person("B")
    s.add_portfolio_snapshot("ha", "2024-12-31", "equity", [{"isin": "I1", "symbol": "X", "quantity": 10, "avg_price": 100, "current_price": 150, "market_value": 1500}], person_id=a)
    s.add_portfolio_snapshot("hb", "2024-12-31", "equity", [{"isin": "I1", "symbol": "X", "quantity": 5, "avg_price": 200, "current_price": 150, "market_value": 750}], person_id=b)
    df = load_holdings_df()
    assert df is not None and len(df) == 1
    assert float(df.iloc[0]["quantity"]) == 15.0
    assert float(df.iloc[0]["current_value"]) == 2250.0
    assert float(df.iloc[0]["current_price"]) == 150.0
    one = load_holdings_df(person_id=a)
    assert one is not None and float(one.iloc[0]["quantity"]) == 10.0

def test_opening_positions_filter_by_person(tmp_path):
    from src.store import SQLiteStore
    db = str(tmp_path / "x.sqlite3")
    s = SQLiteStore(db)
    s.add_opening_position("INFY", "2018-01-01", 10, person_id=2)
    s.add_opening_position("TCS", "2018-01-01", 5, person_id=3)
    s.add_opening_position("WIPRO", "2018-01-01", 1)
    assert len(s.get_opening_positions(person_id=2)) == 1
    assert len(s.get_opening_positions(person_id=3)) == 1
    assert len(s.get_opening_positions()) == 3
    assert len(s.get_opening_positions(person_id=-1)) == 1
