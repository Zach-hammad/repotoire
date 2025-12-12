"""Add api_deprecations table for tracking deprecated endpoints.

Revision ID: 017
Revises: 016
Create Date: 2024-12-11

This migration creates the api_deprecations table for tracking deprecated
API endpoints and their sunset timelines. Enables proactive customer
communication and usage monitoring for deprecated endpoints.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create api_deprecations table with indexes."""

    # Create api_deprecations table
    op.create_table(
        "api_deprecations",
        # Primary key
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # Endpoint identification
        sa.Column(
            "endpoint",
            sa.String(500),
            nullable=False,
            comment="Deprecated endpoint path (e.g., /repos, /analysis/{id})",
        ),
        sa.Column(
            "method",
            sa.String(10),
            nullable=False,
            server_default="GET",
            comment="HTTP method (GET, POST, PUT, DELETE, etc.)",
        ),
        sa.Column(
            "version",
            sa.String(10),
            nullable=False,
            server_default="v1",
            comment="API version (v1, v2, etc.)",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="announced",
            comment="Current deprecation lifecycle status",
        ),
        # Deprecation details
        sa.Column(
            "message",
            sa.Text,
            nullable=False,
            comment="Human-readable deprecation message for headers and notifications",
        ),
        sa.Column(
            "replacement_endpoint",
            sa.String(500),
            nullable=True,
            comment="URL of the successor endpoint (if available)",
        ),
        # Timeline
        sa.Column(
            "announced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="When the deprecation was publicly announced",
        ),
        sa.Column(
            "deprecation_date",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When deprecation headers started appearing in responses",
        ),
        sa.Column(
            "sunset_date",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the endpoint will/did start returning 410 Gone",
        ),
        sa.Column(
            "removed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the endpoint code was deleted from the codebase",
        ),
        # Usage tracking
        sa.Column(
            "last_called_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Last time this deprecated endpoint was called",
        ),
        sa.Column(
            "call_count_since_deprecation",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="Number of calls since deprecation headers started appearing",
        ),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # Create indexes for common queries
    op.create_index(
        "ix_api_deprecations_endpoint",
        "api_deprecations",
        ["endpoint"],
    )
    op.create_index(
        "ix_api_deprecations_version",
        "api_deprecations",
        ["version"],
    )
    op.create_index(
        "ix_api_deprecations_status",
        "api_deprecations",
        ["status"],
    )
    op.create_index(
        "ix_api_deprecations_sunset_date",
        "api_deprecations",
        ["sunset_date"],
    )
    # Unique constraint on endpoint + method + version combination
    op.create_unique_constraint(
        "uq_api_deprecations_endpoint_method_version",
        "api_deprecations",
        ["endpoint", "method", "version"],
    )


def downgrade() -> None:
    """Drop api_deprecations table."""
    op.drop_table("api_deprecations")
