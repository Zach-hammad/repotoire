"""Add BYOK API key columns to organizations.

Revision ID: 037_add_byok_api_keys
Revises: 036_add_detector_settings
Create Date: 2026-02-04

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '037_add_byok_api_keys'
down_revision = '036_add_detector_settings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add encrypted API key columns for BYOK."""
    op.add_column(
        'organizations',
        sa.Column(
            'anthropic_api_key_encrypted',
            sa.Text(),
            nullable=True,
            comment='Encrypted Anthropic API key for AI fixes (BYOK)',
        ),
    )
    op.add_column(
        'organizations',
        sa.Column(
            'openai_api_key_encrypted',
            sa.Text(),
            nullable=True,
            comment='Encrypted OpenAI API key for embeddings (BYOK)',
        ),
    )


def downgrade() -> None:
    """Remove BYOK API key columns."""
    op.drop_column('organizations', 'openai_api_key_encrypted')
    op.drop_column('organizations', 'anthropic_api_key_encrypted')
