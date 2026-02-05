"""Remove audit_logs organization FK constraint.

Revision ID: 039
Revises: 038
Create Date: 2026-02-05

Audit logs should not fail when org_id doesn't exist yet (timing issues
with Clerk webhooks) or when org was deleted. Keep the column for queries
but remove FK enforcement.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the FK constraint on organization_id
    op.drop_constraint(
        "audit_logs_organization_id_fkey",
        "audit_logs",
        type_="foreignkey",
    )


def downgrade() -> None:
    # Re-add the FK constraint
    op.create_foreign_key(
        "audit_logs_organization_id_fkey",
        "audit_logs",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="SET NULL",
    )
