"""Add status page tables.

Revision ID: 018
Revises: 017
Create Date: 2025-01-15

This migration creates tables for the public status page system:
- status_components: Service components being monitored
- incidents: Service incident tracking
- incident_updates: Timeline of incident updates
- scheduled_maintenances: Planned maintenance windows
- status_subscribers: Email subscribers for notifications
- uptime_records: Historical health check data
- Junction tables for many-to-many relationships
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create status page tables with indexes."""
    # Create enum types first
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'component_status') THEN
                CREATE TYPE component_status AS ENUM ('operational', 'degraded', 'partial_outage', 'major_outage', 'maintenance');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'incident_status') THEN
                CREATE TYPE incident_status AS ENUM ('investigating', 'identified', 'monitoring', 'resolved');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'incident_severity') THEN
                CREATE TYPE incident_severity AS ENUM ('minor', 'major', 'critical');
            END IF;
        END$$;
    """)

    # Create status_components table with VARCHAR, then alter to use enum
    op.create_table(
        "status_components",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="operational"),
        sa.Column("health_check_url", sa.Text, nullable=True),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_critical", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_time_ms", sa.Integer, nullable=True),
        sa.Column("uptime_percentage", sa.Numeric(5, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("""
        ALTER TABLE status_components
        ALTER COLUMN status DROP DEFAULT,
        ALTER COLUMN status TYPE component_status USING status::component_status,
        ALTER COLUMN status SET DEFAULT 'operational'
    """)
    op.create_index("ix_status_components_display_order", "status_components", ["display_order"])

    # Create incidents table
    op.create_table(
        "incidents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(50), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("postmortem_url", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE incidents ALTER COLUMN status TYPE incident_status USING status::incident_status")
    op.execute("ALTER TABLE incidents ALTER COLUMN severity TYPE incident_severity USING severity::incident_severity")
    op.create_index("ix_incidents_status_started", "incidents", ["status", "started_at"], postgresql_using="btree")
    op.create_index("ix_incidents_resolved_at", "incidents", ["resolved_at"])

    # Create incident_updates table
    op.create_table(
        "incident_updates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("incident_id", UUID(as_uuid=True), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE incident_updates ALTER COLUMN status TYPE incident_status USING status::incident_status")

    # Create scheduled_maintenances table
    op.create_table(
        "scheduled_maintenances",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_cancelled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_scheduled_maintenance_dates", "scheduled_maintenances", ["scheduled_start", "scheduled_end"])

    # Create status_subscribers table
    op.create_table(
        "status_subscribers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("is_verified", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("verification_token", sa.String(64), nullable=True),
        sa.Column("unsubscribe_token", sa.String(64), nullable=False),
        sa.Column("subscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_status_subscribers_email", "status_subscribers", ["email"])

    # Create uptime_records table
    op.create_table(
        "uptime_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("component_id", UUID(as_uuid=True), sa.ForeignKey("status_components.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("response_time_ms", sa.Integer, nullable=True),
        sa.Column("checked_by", sa.String(50), nullable=True),
    )
    op.execute("ALTER TABLE uptime_records ALTER COLUMN status TYPE component_status USING status::component_status")
    op.create_index("ix_uptime_records_component_timestamp", "uptime_records", ["component_id", "timestamp"], postgresql_using="btree")
    op.create_index("ix_uptime_records_timestamp", "uptime_records", ["timestamp"], postgresql_using="btree")

    # Create junction tables
    op.create_table(
        "incident_components",
        sa.Column("incident_id", UUID(as_uuid=True), sa.ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("component_id", UUID(as_uuid=True), sa.ForeignKey("status_components.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "maintenance_components",
        sa.Column("maintenance_id", UUID(as_uuid=True), sa.ForeignKey("scheduled_maintenances.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("component_id", UUID(as_uuid=True), sa.ForeignKey("status_components.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    """Remove status page tables."""
    # Drop junction tables
    op.drop_table("maintenance_components")
    op.drop_table("incident_components")

    # Drop indexes and tables
    op.drop_index("ix_uptime_records_timestamp", table_name="uptime_records")
    op.drop_index("ix_uptime_records_component_timestamp", table_name="uptime_records")
    op.drop_table("uptime_records")

    op.drop_index("ix_status_subscribers_email", table_name="status_subscribers")
    op.drop_table("status_subscribers")

    op.drop_index("ix_scheduled_maintenance_dates", table_name="scheduled_maintenances")
    op.drop_table("scheduled_maintenances")

    op.drop_table("incident_updates")

    op.drop_index("ix_incidents_resolved_at", table_name="incidents")
    op.drop_index("ix_incidents_status_started", table_name="incidents")
    op.drop_table("incidents")

    op.drop_index("ix_status_components_display_order", table_name="status_components")
    op.drop_table("status_components")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS incident_severity")
    op.execute("DROP TYPE IF EXISTS incident_status")
    op.execute("DROP TYPE IF EXISTS component_status")
