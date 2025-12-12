"""Add changelog tables.

Revision ID: 019
Revises: 018
Create Date: 2025-01-16

This migration creates tables for the public changelog system:
- changelog_entries: Release notes and updates
- changelog_subscribers: Email subscribers for notifications
- user_changelog_reads: Tracks "What's New" modal read status
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create changelog tables with indexes."""
    # Create enum types first
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'changelog_category') THEN
                CREATE TYPE changelog_category AS ENUM ('feature', 'improvement', 'fix', 'breaking', 'security', 'deprecation');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'digest_frequency') THEN
                CREATE TYPE digest_frequency AS ENUM ('instant', 'weekly', 'monthly');
            END IF;
        END$$;
    """)

    # Create changelog_entries table
    op.create_table(
        "changelog_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(20), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("is_draft", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_major", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "author_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("image_url", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Convert category column to enum type
    op.execute("""
        ALTER TABLE changelog_entries
        ALTER COLUMN category TYPE changelog_category USING category::changelog_category
    """)

    # Create indexes for changelog_entries
    op.create_index(
        "ix_changelog_entries_slug",
        "changelog_entries",
        ["slug"],
        unique=True,
    )
    op.create_index(
        "ix_changelog_entries_category",
        "changelog_entries",
        ["category"],
    )
    # Partial index for published entries (for efficient public queries)
    op.execute("""
        CREATE INDEX ix_changelog_entries_published
        ON changelog_entries (published_at DESC)
        WHERE is_draft = false
    """)
    # Partial index for scheduled entries (for Celery beat task)
    op.execute("""
        CREATE INDEX ix_changelog_entries_scheduled
        ON changelog_entries (scheduled_for)
        WHERE is_draft = true AND scheduled_for IS NOT NULL
    """)

    # Create changelog_subscribers table
    op.create_table(
        "changelog_subscribers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("is_verified", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("verification_token", sa.String(64), nullable=True),
        sa.Column("unsubscribe_token", sa.String(64), nullable=False),
        sa.Column("digest_frequency", sa.String(20), nullable=False, server_default="instant"),
        sa.Column("subscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Convert digest_frequency column to enum type
    op.execute("""
        ALTER TABLE changelog_subscribers
        ALTER COLUMN digest_frequency DROP DEFAULT,
        ALTER COLUMN digest_frequency TYPE digest_frequency USING digest_frequency::digest_frequency,
        ALTER COLUMN digest_frequency SET DEFAULT 'instant'
    """)

    # Create index for subscribers
    op.create_index(
        "ix_changelog_subscribers_email",
        "changelog_subscribers",
        ["email"],
        unique=True,
    )

    # Create user_changelog_reads table (for "What's New" modal)
    op.create_table(
        "user_changelog_reads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "last_read_entry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("changelog_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Unique constraint on user_id (one read record per user)
    op.create_unique_constraint(
        "uq_user_changelog_reads_user_id",
        "user_changelog_reads",
        ["user_id"],
    )


def downgrade() -> None:
    """Remove changelog tables."""
    # Drop unique constraint
    op.drop_constraint("uq_user_changelog_reads_user_id", "user_changelog_reads", type_="unique")

    # Drop tables
    op.drop_table("user_changelog_reads")

    op.drop_index("ix_changelog_subscribers_email", table_name="changelog_subscribers")
    op.drop_table("changelog_subscribers")

    op.drop_index("ix_changelog_entries_scheduled", table_name="changelog_entries")
    op.drop_index("ix_changelog_entries_published", table_name="changelog_entries")
    op.drop_index("ix_changelog_entries_category", table_name="changelog_entries")
    op.drop_index("ix_changelog_entries_slug", table_name="changelog_entries")
    op.drop_table("changelog_entries")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS digest_frequency")
    op.execute("DROP TYPE IF EXISTS changelog_category")
