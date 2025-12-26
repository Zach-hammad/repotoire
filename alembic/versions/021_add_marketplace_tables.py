"""Add marketplace tables for AI Skills, Commands & Styles Registry.

Revision ID: 021
Revises: 020
Create Date: 2025-12-19

Creates the database schema for the Repotoire Marketplace:
- marketplace_publishers: Users/orgs who publish assets
- marketplace_assets: The main asset entity (skills, commands, styles, etc.)
- marketplace_asset_versions: Immutable versioned content
- marketplace_installs: User installations
- marketplace_reviews: Ratings and reviews
- org_private_assets: Org-only private assets (not in public marketplace)

Design decisions:
- Uses CHECK constraints instead of PostgreSQL ENUMs for flexibility
  (easier to add new values without migrations)
- Uses GIN indexes for tags array for fast containment queries
- Full-text search uses tsvector with generated column (added separately)
- Denormalized stats (install_count, rating_avg) updated via app logic
- Uses JSONB for flexible content and metadata fields
- Uses Clerk IDs (user_id, org_id) as strings for external auth integration
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================================
    # marketplace_publishers - Users/orgs who publish assets
    # =========================================================================
    op.create_table(
        "marketplace_publishers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("clerk_user_id", sa.String(255), nullable=True),
        sa.Column("clerk_org_id", sa.String(255), nullable=True),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.String(2048), nullable=True),
        sa.Column("website_url", sa.String(2048), nullable=True),
        sa.Column("github_url", sa.String(2048), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_marketplace_publishers_slug"),
        sa.CheckConstraint(
            "type IN ('user', 'organization')",
            name="ck_marketplace_publishers_type",
        ),
        sa.CheckConstraint(
            "(type = 'user' AND clerk_user_id IS NOT NULL AND clerk_org_id IS NULL) OR "
            "(type = 'organization' AND clerk_org_id IS NOT NULL AND clerk_user_id IS NULL)",
            name="ck_marketplace_publishers_clerk_id",
        ),
    )
    op.create_index(
        "ix_marketplace_publishers_slug",
        "marketplace_publishers",
        ["slug"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_publishers_clerk_user_id",
        "marketplace_publishers",
        ["clerk_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_publishers_clerk_org_id",
        "marketplace_publishers",
        ["clerk_org_id"],
        unique=False,
    )

    # =========================================================================
    # marketplace_assets - The main asset entity
    # =========================================================================
    op.create_table(
        "marketplace_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("publisher_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("readme", sa.Text(), nullable=True),
        sa.Column("icon_url", sa.String(2048), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String(50)), nullable=True),
        sa.Column(
            "pricing_type",
            sa.String(20),
            nullable=False,
            server_default="free",
        ),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column(
            "visibility",
            sa.String(20),
            nullable=False,
            server_default="public",
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("featured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "install_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("rating_avg", sa.Numeric(3, 2), nullable=True),
        sa.Column(
            "rating_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["publisher_id"],
            ["marketplace_publishers.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "publisher_id",
            "slug",
            name="uq_marketplace_assets_publisher_slug",
        ),
        sa.CheckConstraint(
            "type IN ('skill', 'command', 'style', 'hook', 'prompt')",
            name="ck_marketplace_assets_type",
        ),
        sa.CheckConstraint(
            "pricing_type IN ('free', 'pro', 'paid')",
            name="ck_marketplace_assets_pricing_type",
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'private', 'unlisted')",
            name="ck_marketplace_assets_visibility",
        ),
        sa.CheckConstraint(
            "(pricing_type != 'paid') OR (price_cents IS NOT NULL AND price_cents > 0)",
            name="ck_marketplace_assets_paid_price",
        ),
        sa.CheckConstraint(
            "rating_avg IS NULL OR (rating_avg >= 0 AND rating_avg <= 5)",
            name="ck_marketplace_assets_rating_range",
        ),
    )
    op.create_index(
        "ix_marketplace_assets_publisher_id",
        "marketplace_assets",
        ["publisher_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_assets_type",
        "marketplace_assets",
        ["type"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_assets_visibility",
        "marketplace_assets",
        ["visibility"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_assets_published_at",
        "marketplace_assets",
        ["published_at"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_assets_featured_at",
        "marketplace_assets",
        ["featured_at"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_assets_install_count",
        "marketplace_assets",
        ["install_count"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_assets_rating_avg",
        "marketplace_assets",
        ["rating_avg"],
        unique=False,
    )
    # GIN index for tags array for fast containment queries
    op.create_index(
        "ix_marketplace_assets_tags",
        "marketplace_assets",
        ["tags"],
        unique=False,
        postgresql_using="gin",
    )

    # Full-text search: Create a generated tsvector column and GIN index
    # This allows efficient text search on name + description
    op.execute("""
        ALTER TABLE marketplace_assets
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english', coalesce(name, '') || ' ' || coalesce(description, ''))
        ) STORED;
    """)
    op.create_index(
        "ix_marketplace_assets_search_vector",
        "marketplace_assets",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )

    # =========================================================================
    # marketplace_asset_versions - Immutable versioned content
    # =========================================================================
    op.create_table(
        "marketplace_asset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("min_repotoire_version", sa.String(20), nullable=True),
        sa.Column("max_repotoire_version", sa.String(20), nullable=True),
        sa.Column(
            "download_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("yanked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("yank_reason", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["marketplace_assets.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "asset_id",
            "version",
            name="uq_marketplace_asset_versions_asset_version",
        ),
    )
    op.create_index(
        "ix_marketplace_asset_versions_asset_id",
        "marketplace_asset_versions",
        ["asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_asset_versions_published_at",
        "marketplace_asset_versions",
        ["published_at"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_asset_versions_yanked_at",
        "marketplace_asset_versions",
        ["yanked_at"],
        unique=False,
    )

    # =========================================================================
    # marketplace_installs - User installations
    # =========================================================================
    op.create_table(
        "marketplace_installs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "auto_update",
            sa.Boolean(),
            nullable=False,
            server_default="true",
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["marketplace_assets.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["marketplace_asset_versions.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "user_id",
            "asset_id",
            name="uq_marketplace_installs_user_asset",
        ),
    )
    op.create_index(
        "ix_marketplace_installs_user_id",
        "marketplace_installs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_installs_asset_id",
        "marketplace_installs",
        ["asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_installs_version_id",
        "marketplace_installs",
        ["version_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_installs_created_at",
        "marketplace_installs",
        ["created_at"],
        unique=False,
    )

    # =========================================================================
    # marketplace_reviews - Ratings and reviews
    # =========================================================================
    op.create_table(
        "marketplace_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "helpful_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["marketplace_assets.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id",
            "asset_id",
            name="uq_marketplace_reviews_user_asset",
        ),
        sa.CheckConstraint(
            "rating >= 1 AND rating <= 5",
            name="ck_marketplace_reviews_rating_range",
        ),
    )
    op.create_index(
        "ix_marketplace_reviews_user_id",
        "marketplace_reviews",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_reviews_asset_id",
        "marketplace_reviews",
        ["asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_reviews_rating",
        "marketplace_reviews",
        ["rating"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_reviews_created_at",
        "marketplace_reviews",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_reviews_hidden_at",
        "marketplace_reviews",
        ["hidden_at"],
        unique=False,
    )

    # =========================================================================
    # org_private_assets - Org-only private assets
    # =========================================================================
    op.create_table(
        "org_private_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.String(255), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by_user_id", sa.String(255), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "slug",
            name="uq_org_private_assets_org_slug",
        ),
        sa.CheckConstraint(
            "type IN ('skill', 'command', 'style', 'hook', 'prompt')",
            name="ck_org_private_assets_type",
        ),
    )
    op.create_index(
        "ix_org_private_assets_org_id",
        "org_private_assets",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_org_private_assets_type",
        "org_private_assets",
        ["type"],
        unique=False,
    )
    op.create_index(
        "ix_org_private_assets_created_by_user_id",
        "org_private_assets",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_org_private_assets_enabled",
        "org_private_assets",
        ["enabled"],
        unique=False,
    )


def downgrade() -> None:
    # Drop tables in reverse order (respecting foreign keys)

    # org_private_assets
    op.drop_index("ix_org_private_assets_enabled", table_name="org_private_assets")
    op.drop_index("ix_org_private_assets_created_by_user_id", table_name="org_private_assets")
    op.drop_index("ix_org_private_assets_type", table_name="org_private_assets")
    op.drop_index("ix_org_private_assets_org_id", table_name="org_private_assets")
    op.drop_table("org_private_assets")

    # marketplace_reviews
    op.drop_index("ix_marketplace_reviews_hidden_at", table_name="marketplace_reviews")
    op.drop_index("ix_marketplace_reviews_created_at", table_name="marketplace_reviews")
    op.drop_index("ix_marketplace_reviews_rating", table_name="marketplace_reviews")
    op.drop_index("ix_marketplace_reviews_asset_id", table_name="marketplace_reviews")
    op.drop_index("ix_marketplace_reviews_user_id", table_name="marketplace_reviews")
    op.drop_table("marketplace_reviews")

    # marketplace_installs
    op.drop_index("ix_marketplace_installs_created_at", table_name="marketplace_installs")
    op.drop_index("ix_marketplace_installs_version_id", table_name="marketplace_installs")
    op.drop_index("ix_marketplace_installs_asset_id", table_name="marketplace_installs")
    op.drop_index("ix_marketplace_installs_user_id", table_name="marketplace_installs")
    op.drop_table("marketplace_installs")

    # marketplace_asset_versions
    op.drop_index("ix_marketplace_asset_versions_yanked_at", table_name="marketplace_asset_versions")
    op.drop_index("ix_marketplace_asset_versions_published_at", table_name="marketplace_asset_versions")
    op.drop_index("ix_marketplace_asset_versions_asset_id", table_name="marketplace_asset_versions")
    op.drop_table("marketplace_asset_versions")

    # marketplace_assets (including generated column)
    op.drop_index("ix_marketplace_assets_search_vector", table_name="marketplace_assets")
    op.drop_index("ix_marketplace_assets_tags", table_name="marketplace_assets")
    op.drop_index("ix_marketplace_assets_rating_avg", table_name="marketplace_assets")
    op.drop_index("ix_marketplace_assets_install_count", table_name="marketplace_assets")
    op.drop_index("ix_marketplace_assets_featured_at", table_name="marketplace_assets")
    op.drop_index("ix_marketplace_assets_published_at", table_name="marketplace_assets")
    op.drop_index("ix_marketplace_assets_visibility", table_name="marketplace_assets")
    op.drop_index("ix_marketplace_assets_type", table_name="marketplace_assets")
    op.drop_index("ix_marketplace_assets_publisher_id", table_name="marketplace_assets")
    op.drop_table("marketplace_assets")

    # marketplace_publishers
    op.drop_index("ix_marketplace_publishers_clerk_org_id", table_name="marketplace_publishers")
    op.drop_index("ix_marketplace_publishers_clerk_user_id", table_name="marketplace_publishers")
    op.drop_index("ix_marketplace_publishers_slug", table_name="marketplace_publishers")
    op.drop_table("marketplace_publishers")
