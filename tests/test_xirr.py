import pytest

from src.xirr import annualize_pct, compute_xirr, to_date


def test_to_date():
    assert str(to_date("02-04-2025")) == "2025-04-02"


def test_xirr_simple():
    flows = [("2024-01-01", -100), ("2025-01-01", 110)]
    x = compute_xirr(flows)
    assert x is not None
    assert annualize_pct(x) == pytest.approx(10.0, abs=0.1)


def test_xirr_no_solution_single_flow():
    assert compute_xirr([("2024-01-01", -100)]) is None


def test_xirr_all_positive():
    assert compute_xirr([("2024-01-01", 100), ("2025-01-01", 110)]) is None