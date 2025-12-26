"""Add marketplace analytics tables.

Revision ID: 025
Revises: 024
Create Date: 2025-01-16

REPO-386: Marketplace Analytics & Metrics Dashboard

This migration adds:
- asset_events: Track download/install/uninstall/update events
- asset_ratings: Store 1-5 star ratings with review text (uses existing reviews table)
- asset_stats: Aggregated totals per asset
- asset_stats_daily: Daily snapshots for trend charts
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers, used by Alembic.
revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ==========================================================================
    # asset_events - Track individual events (install, uninstall, update, download)
    # ==========================================================================
    op.create_table(
        "asset_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("marketplace_asset_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("user_id", sa.String(255), nullable=True),  # Clerk user ID (null for anonymous)
        sa.Column(
            "event_type",
            sa.String(20),
            nullable=False,
            comment="download, install, uninstall, update",
        ),
        sa.Column("cli_version", sa.String(50), nullable=True),
        sa.Column("os_platform", sa.String(50), nullable=True),  # darwin, linux, win32
        sa.Column("source", sa.String(50), nullable=True),  # cli, web, api
        sa.Column("metadata", JSONB, nullable=True),  # Additional context
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Indexes for asset_events - optimized for analytics queries
    op.create_index("ix_asset_events_asset_id", "asset_events", ["asset_id"])
    op.create_index("ix_asset_events_user_id", "asset_events", ["user_id"])
    op.create_index("ix_asset_events_event_type", "asset_events", ["event_type"])
    op.create_index("ix_asset_events_created_at", "asset_events", ["created_at"])
    op.create_index(
        "ix_asset_events_asset_event_created",
        "asset_events",
        ["asset_id", "event_type", "created_at"],
    )
    # BRIN index for time-series queries (efficient for append-only tables)
    op.execute(
        "CREATE INDEX ix_asset_events_created_at_brin ON asset_events USING BRIN (created_at)"
    )

    # ==========================================================================
    # asset_stats - Aggregated totals per asset (denormalized for fast queries)
    # ==========================================================================
    op.create_table(
        "asset_stats",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,  # One stats row per asset
        ),
        # Lifetime totals
        sa.Column(
            "total_downloads",
            sa.BigInteger,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_installs",
            sa.BigInteger,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_uninstalls",
            sa.BigInteger,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_updates",
            sa.BigInteger,
            nullable=False,
            server_default="0",
        ),
        # Active installs = installs - uninstalls
        sa.Column(
            "active_installs",
            sa.BigInteger,
            nullable=False,
            server_default="0",
        ),
        # Rating stats (mirrored from asset for consistency)
        sa.Column("rating_avg", sa.Numeric(3, 2), nullable=True),
        sa.Column("rating_count", sa.Integer, nullable=False, server_default="0"),
        # Revenue stats (for paid assets)
        sa.Column(
            "total_revenue_cents",
            sa.BigInteger,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_purchases",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        # Rolling windows (updated by background job)
        sa.Column("downloads_7d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("downloads_30d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("installs_7d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("installs_30d", sa.Integer, nullable=False, server_default="0"),
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

    op.create_index("ix_asset_stats_asset_id", "asset_stats", ["asset_id"])
    op.create_index("ix_asset_stats_active_installs", "asset_stats", ["active_installs"])
    op.create_index("ix_asset_stats_total_downloads", "asset_stats", ["total_downloads"])

    # ==========================================================================
    # asset_stats_daily - Daily snapshots for trend charts
    # ==========================================================================
    op.create_table(
        "asset_stats_daily",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date, nullable=False),
        # Daily counts
        sa.Column("downloads", sa.Integer, nullable=False, server_default="0"),
        sa.Column("installs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("uninstalls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updates", sa.Integer, nullable=False, server_default="0"),
        # Running totals at end of day (for cumulative charts)
        sa.Column("cumulative_downloads", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("cumulative_installs", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("active_installs", sa.BigInteger, nullable=False, server_default="0"),
        # Revenue for the day
        sa.Column("revenue_cents", sa.Integer, nullable=False, server_default="0"),
        sa.Column("purchases", sa.Integer, nullable=False, server_default="0"),
        # Unique users who interacted
        sa.Column("unique_users", sa.Integer, nullable=False, server_default="0"),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Unique constraint: one row per asset per day
    op.create_unique_constraint(
        "uq_asset_stats_daily_asset_date",
        "asset_stats_daily",
        ["asset_id", "date"],
    )

    op.create_index("ix_asset_stats_daily_asset_id", "asset_stats_daily", ["asset_id"])
    op.create_index("ix_asset_stats_daily_date", "asset_stats_daily", ["date"])
    op.create_index(
        "ix_asset_stats_daily_asset_date",
        "asset_stats_daily",
        ["asset_id", "date"],
    )

    # ==========================================================================
    # publisher_stats - Aggregated stats per publisher
    # ==========================================================================
    op.create_table(
        "publisher_stats",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "publisher_id",
            UUID(as_uuid=True),
            sa.ForeignKey("marketplace_publishers.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        # Totals across all assets
        sa.Column("total_assets", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_downloads", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("total_installs", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("total_active_installs", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("total_revenue_cents", sa.BigInteger, nullable=False, server_default="0"),
        # Average rating across all assets
        sa.Column("avg_rating", sa.Numeric(3, 2), nullable=True),
        sa.Column("total_reviews", sa.Integer, nullable=False, server_default="0"),
        # Rolling windows
        sa.Column("downloads_7d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("downloads_30d", sa.Integer, nullable=False, server_default="0"),
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

    op.create_index("ix_publisher_stats_publisher_id", "publisher_stats", ["publisher_id"])

    # ==========================================================================
    # Add verified_install flag to marketplace_reviews
    # ==========================================================================
    op.add_column(
        "marketplace_reviews",
        sa.Column(
            "verified_install",
            sa.Boolean,
            nullable=False,
            server_default="false",
            comment="True if user had the asset installed when reviewing",
        ),
    )

    # ==========================================================================
    # Create function and trigger to auto-create asset_stats on asset creation
    # ==========================================================================
    op.execute("""
        CREATE OR REPLACE FUNCTION create_asset_stats()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO asset_stats (id, asset_id)
            VALUES (gen_random_uuid(), NEW.id)
            ON CONFLICT (asset_id) DO NOTHING;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trigger_create_asset_stats
        AFTER INSERT ON marketplace_assets
        FOR EACH ROW
        EXECUTE FUNCTION create_asset_stats();
    """)

    # ==========================================================================
    # Create function and trigger to auto-create publisher_stats
    # ==========================================================================
    op.execute("""
        CREATE OR REPLACE FUNCTION create_publisher_stats()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO publisher_stats (id, publisher_id)
            VALUES (gen_random_uuid(), NEW.id)
            ON CONFLICT (publisher_id) DO NOTHING;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trigger_create_publisher_stats
        AFTER INSERT ON marketplace_publishers
        FOR EACH ROW
        EXECUTE FUNCTION create_publisher_stats();
    """)

    # ==========================================================================
    # Backfill stats for existing assets and publishers
    # ==========================================================================
    op.execute("""
        INSERT INTO asset_stats (id, asset_id, rating_avg, rating_count, active_installs, total_installs)
        SELECT
            gen_random_uuid(),
            id,
            rating_avg,
            rating_count,
            install_count,
            install_count
        FROM marketplace_assets
        ON CONFLICT (asset_id) DO NOTHING;
    """)

    op.execute("""
        INSERT INTO publisher_stats (id, publisher_id, total_assets)
        SELECT
            gen_random_uuid(),
            p.id,
            COUNT(a.id)::integer
        FROM marketplace_publishers p
        LEFT JOIN marketplace_assets a ON a.publisher_id = p.id
        GROUP BY p.id
        ON CONFLICT (publisher_id) DO NOTHING;
    """)


def downgrade() -> None:
    # Drop triggers first
    op.execute("DROP TRIGGER IF EXISTS trigger_create_publisher_stats ON marketplace_publishers")
    op.execute("DROP TRIGGER IF EXISTS trigger_create_asset_stats ON marketplace_assets")

    # Drop functions
    op.execute("DROP FUNCTION IF EXISTS create_publisher_stats()")
    op.execute("DROP FUNCTION IF EXISTS create_asset_stats()")

    # Remove verified_install column from reviews
    op.drop_column("marketplace_reviews", "verified_install")

    # Drop tables in reverse order
    op.drop_table("publisher_stats")
    op.drop_table("asset_stats_daily")
    op.drop_table("asset_stats")
    op.drop_table("asset_events")
