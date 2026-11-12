"""Mutual fund tables: mf_schemes, mf_plans, mf_nav, mf_transactions, mf_portfolios."""

from alembic import op
import sqlalchemy as sa

revision = '0003_mf_tables'
down_revision = '0002_equity_tables'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'mf_portfolios',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('equity_portfolio_id', sa.Integer(), nullable=False),
        sa.Column('scheme_code', sa.String(length=20), nullable=False),
        sa.Column('plan_type', sa.String(length=20), nullable=True),
        sa.Column('direct_regular', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['equity_portfolio_id'], ['portfolios.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['scheme_code'], ['mf_schemes.scheme_code'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('equity_portfolio_id', 'scheme_code', name='uq_mf_portfolio_equity'),
    )
    op.create_index(
        'idx_mf_portfolio_equity', 'mf_portfolios', ['equity_portfolio_id']
)
    op.create_table(
        'mf_schemes',
        sa.Column('scheme_code', sa.String(length=20), nullable=False),
        sa.Column('scheme_name', sa.String(length=255), nullable=False),
        sa.Column('provider', sa.String(length=100), nullable=True),
        sa.Column('plan_type', sa.String(length=20), nullable=True),
        sa.Column('direct_regular', sa.Boolean(), nullable=True),
        sa.Column('growth_idcw', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('scheme_code'),
        sa.UniqueConstraint('scheme_name', 'plan_type', 'direct_regular', name='uq_scheme_name_plan'),
    )
    op.create_table(
        'mf_plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scheme_code', sa.String(length=20), nullable=False),
        sa.Column('plan_name', sa.String(length=255), nullable=False),
        sa.Column('plan_type', sa.String(length=20), nullable=True),
        sa.Column('direct_regular', sa.Boolean(), nullable=True),
        sa.Column('growth_idcw', sa.Boolean(), nullable=True),
        sa.Column('nav_type', sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(['scheme_code'], ['mf_schemes.scheme_code'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scheme_code', 'plan_name', name='uq_plan_scheme_name'),
    )
    op.create_table(
        'mf_nav',
        sa.Column('scheme_code', sa.String(length=20), nullable=False),
        sa.Column('plan_type', sa.String(length=20), nullable=True),
        sa.Column('as_of_date', sa.Text(), nullable=False, index=True),
        sa.Column('nav', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('source', sa.String(length=100), nullable=True),
        sa.Column('provider', sa.String(length=100), nullable=True),
        sa.Column('fetched_at', sa.Text(), nullable=True),
        sa.Column('compressed_nav', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.ForeignKeyConstraint(['scheme_code'], ['mf_schemes.scheme_code'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('scheme_code', 'plan_type', 'as_of_date'),
        sa.UniqueConstraint('scheme_code', 'plan_type', 'as_of_date', name='uq_nav_scheme_date'),
    )
    op.create_table(
        'mf_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('portfolio_id', sa.Integer(), nullable=False),
        sa.Column('scheme_code', sa.String(length=20), nullable=True),
        sa.Column('plan_type', sa.String(length=20), nullable=True),
        sa.Column('direct_regular', sa.Boolean(), nullable=True),
        sa.Column('growth_idcw', sa.Boolean(), nullable=True),
        sa.Column('trade_date', sa.Text(), nullable=False, index=True),
        sa.Column('side', sa.String(length=20), nullable=False),
        sa.Column('units', sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column('nav', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('source_file', sa.Text(), nullable=True),
        sa.Column('source_priority', sa.String(length=30), nullable=False, server_default='mf_statement'),
        sa.Column('fingerprint', sa.String(length=64), nullable=False, index=True),
        sa.Column('imported_at', sa.Text(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['portfolio_id'], ['mf_portfolios.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['scheme_code'], ['mf_schemes.scheme_code'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('idx_mf_portfolio_scheme_date', 'portfolio_id', 'scheme_code', 'trade_date'),
    )
    op.create_index(
        'idx_mf_scheme_code', 'mf_transactions', ['scheme_code']
)

def downgrade() -> None:
    op.drop_index(
        'idx_mf_scheme_code', table_name='mf_transactions'
)
    op.drop_index(
        'idx_mf_portfolio_scheme_date', table_name='mf_transactions'
)
    op.drop_table(
        'mf_transactions'
    )
    op.drop_table(
        'mf_nav'
    )
    op.drop_table(
        'mf_plans'
    )
    op.drop_table(
        'mf_schemes'
    )
    op.drop_index(
        'idx_mf_portfolio_equity', table_name='mf_portfolios'
)
    op.drop_table(
        'mf_portfolios'
    )
