"""Add detector_settings table for organization-level detector configuration.

This table stores detector threshold configurations at the organization level,
allowing teams to customize sensitivity levels (strict/balanced/permissive)
or define custom thresholds.

Revision ID: 036
Revises: 035
Create Date: 2026-01-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Default balanced thresholds
DEFAULT_THRESHOLDS = {
    # God Class - default thresholds
    "god_class_high_method_count": 20,
    "god_class_medium_method_count": 15,
    "god_class_high_complexity": 100,
    "god_class_medium_complexity": 50,
    "god_class_high_loc": 500,
    "god_class_medium_loc": 300,
    "god_class_high_lcom": 0.8,
    "god_class_medium_lcom": 0.6,
    # Feature Envy - default thresholds
    "feature_envy_threshold_ratio": 3.0,
    "feature_envy_min_external_uses": 15,
    # Radon - default thresholds
    "radon_complexity_threshold": 10,
    "radon_maintainability_threshold": 65,
    # Global settings
    "max_findings_per_detector": 100,
    "confidence_threshold": 0.7,
}


def upgrade() -> None:
    """Create detector_settings table for organization detector configuration."""
    op.create_table(
        "detector_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "preset",
            sa.String(20),
            nullable=False,
            server_default="balanced",
            comment="Preset profile: strict, balanced, permissive, custom",
        ),
        sa.Column(
            "thresholds",
            JSONB(),
            nullable=False,
            server_default=sa.text(f"'{str(DEFAULT_THRESHOLDS).replace(chr(39), chr(34))}'::jsonb"),
            comment="Detector threshold configuration values",
        ),
        sa.Column(
            "enabled_detectors",
            JSONB(),
            nullable=True,
            comment="List of enabled detector names. Null means all enabled.",
        ),
        sa.Column(
            "disabled_detectors",
            JSONB(),
            nullable=True,
            server_default="[]",
            comment="List of disabled detector names.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id"),
    )
    op.create_index(
        "ix_detector_settings_organization_id",
        "detector_settings",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop detector_settings table."""
    op.drop_index("ix_detector_settings_organization_id", table_name="detector_settings")
    op.drop_table("detector_settings")
