# ICICI Direct XIRR Calculator

A Streamlit app that parses ICICI Direct quarterly equity transaction statement PDFs and
a portfolio XLS to compute per-security and portfolio XIRR.

## Setup

```bash
git clone https://github.com/deepaktaneja/icici-xirr.git
cd icici-xirr
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

1. Download quarterly Equity Transaction Statements from ICICI Direct
   (Stocks > Reports > Equity Transaction statement) as PDFs into the
   `data/statements` folder.
2. Export your portfolio summary as XLS/XLSX (or TSV) into the
   `data/portfolio` folder.
3. Run: `streamlit run app.py`
4. The app reads statement PDFs from the statements folder and holdings
   from the portfolio folder. Click "Run analysis".

## Tests

```bash
PYTHONPATH=. pytest -q
```

## Notes

- PDFs are sorted chronologically by date in filename (`TRX-Equity_DD-MM-YYYY_*.pdf`).
- XIRR uses settlement dates; buys are outflows, sells inflows, and current holding value
  (from XLS) is the terminal inflow.
- Mismatches between computed quantities and XLS holdings are flagged.