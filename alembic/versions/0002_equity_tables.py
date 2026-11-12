"""Equity transaction ledger tables (Stage 1).
Creates portfolios, securities, transactions, holdings, import_audit.
Extensible for mutual funds and corporate actions in later stages.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0002_equity_tables'
down_revision = '0001_initial'
branch_labels = None
depends_on = None

def upgrade() -> None:

    op.create_table(
        "portfolios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "securities",
        sa.Column("isin", sa.String(length=12), nullable=False),
        sa.Column("symbol", sa.String(length=50), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("exchange", sa.String(length=10), nullable=True),
        sa.Column("sector", sa.String(length=100), nullable=True),
        sa.Column("instrument_type", sa.String(length=20), nullable=False, server_default="EQUITY"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("isin"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.String(length=12), nullable=False),
        sa.Column("trade_date", sa.Text(), nullable=True),
        sa.Column("settlement_date", sa.Text(), nullable=True),
        sa.Column("side", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("price", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("gross_amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("brokerage", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
        sa.Column("gst", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
        sa.Column("charges", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
        sa.Column("net_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("source_priority", sa.String(length=30), nullable=False, server_default="historical_statement"),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("imported_at", sa.Text(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("confidence", sa.Numeric(precision=3, scale=2), nullable=False, server_default="1.00"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["security_id"], ["securities.isin"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_tx_portfolio_id", "transactions", ["portfolio_id"], unique=False)

    op.create_index(
        "idx_tx_security_id", "transactions", ["security_id"], unique=False)

    op.create_index(
        "idx_tx_trade_date", "transactions", ["trade_date"], unique=False)

    op.create_index(
        "idx_tx_fingerprint", "transactions", ["fingerprint"], unique=False)

    op.create_index(
        "idx_tx_portfolio_security_date", "transactions",
        ["portfolio_id", "security_id", "trade_date"],
        unique=False)

    op.create_table(
        "holdings",
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.String(length=12), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("cost_basis", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["security_id"], ["securities.isin"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("portfolio_id", "security_id"),
    )

    op.create_index(
        "idx_holdings_portfolio_id", "holdings", ["portfolio_id"], unique=False)

    op.create_index(
        "idx_holdings_security_id", "holdings", ["security_id"], unique=False)

    op.create_table(
        "import_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("detected_type", sa.String(length=50), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="imported"),
        sa.Column("imported_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_import_audit_portfolio_id", "import_audit", ["portfolio_id"], unique=False)

def downgrade() -> None:

    op.drop_index(
        "idx_import_audit_portfolio_id", table_name="import_audit")

    op.drop_table(
        "import_audit")

    op.drop_index(
        "idx_holdings_security_id", table_name="holdings")

    op.drop_index(
        "idx_holdings_portfolio_id", table_name="holdings")

    op.drop_table(
        "holdings")

    op.drop_index(
        "idx_tx_portfolio_security_date", table_name="transactions")

    op.drop_index(
        "idx_tx_fingerprint", table_name="transactions")

    op.drop_index(
        "idx_tx_trade_date", table_name="transactions")

    op.drop_index(
        "idx_tx_security_id", table_name="transactions")

    op.drop_index(
        "idx_tx_portfolio_id", table_name="transactions")

    op.drop_table(
        "transactions")

    op.drop_table(
        "securities")

    op.drop_table(
        "portfolios")

