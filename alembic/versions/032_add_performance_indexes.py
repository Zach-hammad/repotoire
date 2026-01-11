"""Add performance indexes for findings and analysis queries.

Revision ID: 032
Revises: 031
Create Date: 2026-01-10

These indexes optimize the most common query patterns identified in production:
- ix_findings_run_severity: Filter findings by analysis_run_id and severity
- ix_findings_run_detector: Filter findings by analysis_run_id and detector
- ix_analysis_runs_repo_status: Find completed runs by repository
- ix_analysis_runs_repo_completed: Find latest completed run per repo
- ix_repositories_org_created: List repos by org sorted by created_at
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add performance indexes for common query patterns."""
    # Findings table indexes for list_findings endpoint
    op.create_index(
        "ix_findings_run_severity",
        "findings",
        ["analysis_run_id", "severity"],
        unique=False,
        if_not_exists=True,
    )

    op.create_index(
        "ix_findings_run_detector",
        "findings",
        ["analysis_run_id", "detector"],
        unique=False,
        if_not_exists=True,
    )

    op.create_index(
        "ix_findings_run_status",
        "findings",
        ["analysis_run_id", "status"],
        unique=False,
        if_not_exists=True,
    )

    # Analysis runs table indexes for _get_latest_analysis_run_ids
    op.create_index(
        "ix_analysis_runs_repo_status",
        "analysis_runs",
        ["repository_id", "status"],
        unique=False,
        if_not_exists=True,
    )

    # Composite index with DESC ordering for finding latest completed run
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_analysis_runs_repo_completed
        ON analysis_runs (repository_id, completed_at DESC)
        WHERE status = 'completed'
    """)

    # Repositories table index for listing by org
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_repositories_org_created
        ON repositories (organization_id, created_at DESC)
    """)

    # Webhook events table index for idempotency checks (only if table exists)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'webhook_events') THEN
                CREATE INDEX IF NOT EXISTS ix_webhook_events_event_id_source
                ON webhook_events (event_id, source);
            END IF;
        END $$;
    """)

    # Audit logs table index for querying by org and time (only if columns exist)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'audit_logs'
                AND column_name = 'created_at'
            ) THEN
                CREATE INDEX IF NOT EXISTS ix_audit_logs_org_created
                ON audit_logs (organization_id, created_at DESC);
            END IF;
        END $$;
    """)


def downgrade() -> None:
    """Remove performance indexes."""
    # Drop audit logs index
    op.execute("DROP INDEX IF EXISTS ix_audit_logs_org_created")

    # Drop webhook events index (only if table exists)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'webhook_events') THEN
                DROP INDEX IF EXISTS ix_webhook_events_event_id_source;
            END IF;
        END $$;
    """)

    # Drop repositories index
    op.execute("DROP INDEX IF EXISTS ix_repositories_org_created")

    # Drop analysis runs indexes
    op.execute("DROP INDEX IF EXISTS ix_analysis_runs_repo_completed")
    op.execute("DROP INDEX IF EXISTS ix_analysis_runs_repo_status")

    # Drop findings indexes
    op.execute("DROP INDEX IF EXISTS ix_findings_run_status")
    op.execute("DROP INDEX IF EXISTS ix_findings_run_detector")
    op.execute("DROP INDEX IF EXISTS ix_findings_run_severity")
