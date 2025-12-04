"""add_findings_table

Revision ID: ce86502e06be
Revises: 010
Create Date: 2025-12-04 12:25:19.695230
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ce86502e06be'
down_revision: Union[str, None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create findings table (enum finding_severity assumed to exist or created automatically)
    op.create_table('findings',
        sa.Column('analysis_run_id', sa.Uuid(), nullable=False),
        sa.Column('detector', sa.String(length=100), nullable=False),
        sa.Column('severity', sa.Enum('critical', 'high', 'medium', 'low', 'info', name='finding_severity', create_type=False), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('affected_files', postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column('affected_nodes', postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column('line_start', sa.Integer(), nullable=True),
        sa.Column('line_end', sa.Integer(), nullable=True),
        sa.Column('suggested_fix', sa.Text(), nullable=True),
        sa.Column('estimated_effort', sa.String(length=100), nullable=True),
        sa.Column('graph_context', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['analysis_run_id'], ['analysis_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_findings_analysis_run_id', 'findings', ['analysis_run_id'], unique=False)
    op.create_index('ix_findings_detector', 'findings', ['detector'], unique=False)
    op.create_index('ix_findings_severity', 'findings', ['severity'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_findings_severity', table_name='findings')
    op.drop_index('ix_findings_detector', table_name='findings')
    op.drop_index('ix_findings_analysis_run_id', table_name='findings')
    op.drop_table('findings')

    # Drop the enum type
    finding_severity = postgresql.ENUM(
        'critical', 'high', 'medium', 'low', 'info',
        name='finding_severity'
    )
    finding_severity.drop(op.get_bind(), checkfirst=True)
