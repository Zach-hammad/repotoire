"""Add organization_invites table for team invitations.

Revision ID: 008
Revises: 007
Create Date: 2024-12-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Define enums as PostgreSQL ENUM with create_type=False since they may already exist
invite_status_enum = postgresql.ENUM(
    "pending", "accepted", "expired", "revoked",
    name="invite_status",
    create_type=False,
)

member_role_enum = postgresql.ENUM(
    "owner", "admin", "member",
    name="member_role",
    create_type=False,
)


def upgrade() -> None:
    """Create organization_invites table."""
    # Create invite_status enum if it doesn't exist
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = 'invite_status'")
    )
    if not result.fetchone():
        conn.execute(
            sa.text("CREATE TYPE invite_status AS ENUM ('pending', 'accepted', 'expired', 'revoked')")
        )

    op.create_table(
        "organization_invites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("invited_by_id", sa.UUID(), nullable=True),
        sa.Column("role", member_role_enum, nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("status", invite_status_enum, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
    )

    # Create indexes
    op.create_index(
        "ix_organization_invites_token",
        "organization_invites",
        ["token"],
        unique=True,
    )
    op.create_index(
        "ix_organization_invites_email",
        "organization_invites",
        ["email"],
    )
    op.create_index(
        "ix_organization_invites_organization_id",
        "organization_invites",
        ["organization_id"],
    )


def downgrade() -> None:
    """Drop organization_invites table."""
    op.drop_index("ix_organization_invites_organization_id", table_name="organization_invites")
    op.drop_index("ix_organization_invites_email", table_name="organization_invites")
    op.drop_index("ix_organization_invites_token", table_name="organization_invites")
    op.drop_table("organization_invites")

    # Drop invite_status enum
    conn = op.get_bind()
    conn.execute(sa.text("DROP TYPE IF EXISTS invite_status"))
