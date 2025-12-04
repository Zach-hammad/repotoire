"""add_fixes_tables

Revision ID: 011
Revises: ce86502e06be
Create Date: 2025-12-04

This migration adds the fixes and fix_comments tables for
persisting AI-generated code fix proposals and reviewer comments.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '011'
down_revision: Union[str, None] = 'ce86502e06be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types
    fix_status = postgresql.ENUM(
        'pending', 'approved', 'rejected', 'applied', 'failed',
        name='fix_status',
        create_type=False,
    )
    fix_status.create(op.get_bind(), checkfirst=True)

    fix_confidence = postgresql.ENUM(
        'high', 'medium', 'low',
        name='fix_confidence',
        create_type=False,
    )
    fix_confidence.create(op.get_bind(), checkfirst=True)

    fix_type = postgresql.ENUM(
        'refactor', 'simplify', 'extract', 'rename', 'remove',
        'security', 'type_hint', 'documentation',
        name='fix_type',
        create_type=False,
    )
    fix_type.create(op.get_bind(), checkfirst=True)

    # Create fixes table
    op.create_table(
        'fixes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('analysis_run_id', sa.Uuid(), nullable=False),
        sa.Column('finding_id', sa.Uuid(), nullable=True),
        sa.Column('file_path', sa.String(length=1024), nullable=False),
        sa.Column('line_start', sa.Integer(), nullable=True),
        sa.Column('line_end', sa.Integer(), nullable=True),
        sa.Column('original_code', sa.Text(), nullable=False),
        sa.Column('fixed_code', sa.Text(), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=False),
        sa.Column(
            'fix_type',
            sa.Enum(
                'refactor', 'simplify', 'extract', 'rename', 'remove',
                'security', 'type_hint', 'documentation',
                name='fix_type',
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            'confidence',
            sa.Enum(
                'high', 'medium', 'low',
                name='fix_confidence',
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column('confidence_score', sa.Float(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'pending', 'approved', 'rejected', 'applied', 'failed',
                name='fix_status',
                create_type=False,
            ),
            nullable=False,
            server_default='pending',
        ),
        sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('validation_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['analysis_run_id'], ['analysis_runs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['finding_id'], ['findings.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_fixes_analysis_run_id', 'fixes', ['analysis_run_id'], unique=False)
    op.create_index('ix_fixes_finding_id', 'fixes', ['finding_id'], unique=False)
    op.create_index('ix_fixes_status', 'fixes', ['status'], unique=False)
    op.create_index('ix_fixes_file_path', 'fixes', ['file_path'], unique=False)
    op.create_index('ix_fixes_created_at', 'fixes', ['created_at'], unique=False)

    # Create fix_comments table
    op.create_table(
        'fix_comments',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('fix_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['fix_id'], ['fixes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_fix_comments_fix_id', 'fix_comments', ['fix_id'], unique=False)
    op.create_index('ix_fix_comments_user_id', 'fix_comments', ['user_id'], unique=False)
    op.create_index('ix_fix_comments_created_at', 'fix_comments', ['created_at'], unique=False)


def downgrade() -> None:
    # Drop fix_comments table
    op.drop_index('ix_fix_comments_created_at', table_name='fix_comments')
    op.drop_index('ix_fix_comments_user_id', table_name='fix_comments')
    op.drop_index('ix_fix_comments_fix_id', table_name='fix_comments')
    op.drop_table('fix_comments')

    # Drop fixes table
    op.drop_index('ix_fixes_created_at', table_name='fixes')
    op.drop_index('ix_fixes_file_path', table_name='fixes')
    op.drop_index('ix_fixes_status', table_name='fixes')
    op.drop_index('ix_fixes_finding_id', table_name='fixes')
    op.drop_index('ix_fixes_analysis_run_id', table_name='fixes')
    op.drop_table('fixes')

    # Drop enum types
    fix_status = postgresql.ENUM(
        'pending', 'approved', 'rejected', 'applied', 'failed',
        name='fix_status',
    )
    fix_status.drop(op.get_bind(), checkfirst=True)

    fix_confidence = postgresql.ENUM(
        'high', 'medium', 'low',
        name='fix_confidence',
    )
    fix_confidence.drop(op.get_bind(), checkfirst=True)

    fix_type = postgresql.ENUM(
        'refactor', 'simplify', 'extract', 'rename', 'remove',
        'security', 'type_hint', 'documentation',
        name='fix_type',
    )
    fix_type.drop(op.get_bind(), checkfirst=True)
