
from src.store import SQLiteStore


def test_store_migrate_and_roundtrip(tmp_path):
    db = tmp_path / "t.db"
    store = SQLiteStore(db)
    assert store.known_files() == {}
    rows = [{"settlement_date": "2025-04-02", "isin": "INE001", "security": "X",
             "buy_sell": "BUY", "quantity": 1, "net_amount": 100.0}]
    store.replace_file_transactions("a.pdf", rows)
    store.upsert_file("a.pdf", 123.0, 1)
    assert store.known_files() == {"a.pdf": 123.0}
    assert len(store.all_transactions()) == 1
    # dedup via replace
    store.replace_file_transactions("a.pdf", rows + rows)
    store.upsert_file("a.pdf", 123.0, 2)
    assert len(store.all_transactions()) == 2
    # clear
    store.clear()
    assert store.all_transactions() == []
    assert store.known_files() == {}


def test_mtime_skip_logic(tmp_path):
    db = tmp_path / "t.db"
    store = SQLiteStore(db)
    store.upsert_file("a.pdf", 100.0, 0)
    known = store.known_files()
    assert "a.pdf" in known
    assert abs(known["a.pdf"] - 100.0) < 1e-9