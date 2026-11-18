"""Research and MCP evidence tables (Stage 6).

Creates mcp_cache, research_reports, research_snapshots.
No changes to any other application's database.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = '0004_research_tables'
down_revision = '0003_mf_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'mcp_cache',
        sa.Column('server_name', sa.String(length=100), nullable=False),
        sa.Column('tool_name', sa.String(length=100), nullable=False),
        sa.Column('args_hash', sa.String(length=64), nullable=False),
        sa.Column('arguments', sa.Text(), nullable=False),
        sa.Column('response', sa.Text(), nullable=False),
        sa.Column('retrieved_at', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.Text(), nullable=False),
        sa.Column('hit_count', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('server_name', 'tool_name', 'args_hash'),
    )
    op.create_index('idx_mcp_cache_expires', 'mcp_cache', ['expires_at'])

    op.create_table(
        'research_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('portfolio_id', sa.Integer(), nullable=True),
        sa.Column('security_id', sa.String(length=12), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('content_md', sa.Text(), nullable=False),
        sa.Column('evidence_ids', sa.Text(), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('generated_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_research_reports_portfolio', 'research_reports', ['portfolio_id'])
    op.create_index('idx_research_reports_security', 'research_reports', ['security_id'])
    op.create_index('idx_research_reports_generated', 'research_reports', ['generated_at'])

    op.create_table(
        'research_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_id', sa.Integer(), nullable=False),
        sa.Column('security_id', sa.String(length=12), nullable=False),
        sa.Column('snapshot_date', sa.Text(), nullable=False),
        sa.Column('key_metrics_json', sa.Text(), nullable=False),
        sa.Column('outlook', sa.String(length=50), nullable=False),
        sa.Column('generated_at', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['report_id'], ['research_reports.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_research_snapshots_security_date', 'research_snapshots', ['security_id', 'snapshot_date'])
    op.create_index('idx_research_snapshots_report', 'research_snapshots', ['report_id'])


def downgrade() -> None:
    op.drop_index('idx_research_snapshots_report', table_name='research_snapshots')
    op.drop_index('idx_research_snapshots_security_date', table_name='research_snapshots')
    op.drop_table('research_snapshots')
    op.drop_index('idx_research_reports_generated', table_name='research_reports')
    op.drop_index('idx_research_reports_security', table_name='research_reports')
    op.drop_index('idx_research_reports_portfolio', table_name='research_reports')
    op.drop_table('research_reports')
    op.drop_index('idx_mcp_cache_expires', table_name='mcp_cache')
    op.drop_table('mcp_cache')