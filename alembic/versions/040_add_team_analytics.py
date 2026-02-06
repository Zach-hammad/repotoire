"""Add team analytics tables.

Revision ID: 040_team_analytics
Revises: 039_remove_audit_logs_org_fk
Create Date: 2026-02-05

This migration adds cloud-only team analytics features:
- Developer: Individual contributor profiles
- CodeOwnership: File/function ownership tracking
- Collaboration: Cross-developer collaboration metrics
- TeamInsight: Pre-computed team insights
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "040_team_analytics"
down_revision = "039_remove_audit_logs_org_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create ownership_type enum
    ownership_type = postgresql.ENUM(
        "file", "function", "class", "module",
        name="ownership_type",
        create_type=False,
    )
    ownership_type.create(op.get_bind(), checkfirst=True)
    
    # Create developers table
    op.create_table(
        "developers",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True), 
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=True),
        sa.Column("first_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_commits", sa.Integer(), default=0, nullable=False),
        sa.Column("total_lines_added", sa.Integer(), default=0, nullable=False),
        sa.Column("total_lines_removed", sa.Integer(), default=0, nullable=False),
        sa.Column("expertise_areas", postgresql.JSONB(), nullable=True),
        sa.Column("linked_user_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), 
                  onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "email", name="uq_developer_org_email"),
    )
    op.create_index("ix_developer_org_id", "developers", ["organization_id"])
    op.create_index("ix_developer_org_commits", "developers", ["organization_id", "total_commits"])
    
    # Create code_ownership table
    op.create_table(
        "code_ownership",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("repository_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("developer_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("developers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ownership_type", ownership_type, nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("ownership_score", sa.Float(), default=0.0, nullable=False),
        sa.Column("lines_owned", sa.Integer(), default=0, nullable=False),
        sa.Column("last_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("commit_count", sa.Integer(), default=0, nullable=False),
        sa.Column("extra_data", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("repository_id", "developer_id", "ownership_type", "path",
                          name="uq_ownership_repo_dev_type_path"),
    )
    op.create_index("ix_ownership_repo_id", "code_ownership", ["repository_id"])
    op.create_index("ix_ownership_dev_id", "code_ownership", ["developer_id"])
    op.create_index("ix_ownership_repo_path", "code_ownership", ["repository_id", "path"])
    op.create_index("ix_ownership_dev_score", "code_ownership", ["developer_id", "ownership_score"])
    
    # Create collaborations table
    op.create_table(
        "collaborations",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("developer_a_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("developers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("developer_b_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("developers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("collaboration_score", sa.Float(), default=0.0, nullable=False),
        sa.Column("shared_files", sa.Integer(), default=0, nullable=False),
        sa.Column("co_commits", sa.Integer(), default=0, nullable=False),
        sa.Column("reviews_given", sa.Integer(), default=0, nullable=False),
        sa.Column("reviews_received", sa.Integer(), default=0, nullable=False),
        sa.Column("handoff_count", sa.Integer(), default=0, nullable=False),
        sa.Column("last_interaction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "developer_a_id", "developer_b_id",
                          name="uq_collaboration_org_devs"),
    )
    op.create_index("ix_collaboration_org_id", "collaborations", ["organization_id"])
    op.create_index("ix_collaboration_dev_a", "collaborations", ["developer_a_id"])
    op.create_index("ix_collaboration_dev_b", "collaborations", ["developer_b_id"])
    
    # Create team_insights table
    op.create_table(
        "team_insights",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repository_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=True),
        sa.Column("insight_type", sa.String(100), nullable=False),
        sa.Column("insight_data", postgresql.JSONB(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_team_insight_org_id", "team_insights", ["organization_id"])
    op.create_index("ix_team_insight_repo_id", "team_insights", ["repository_id"])
    op.create_index("ix_team_insight_org_type", "team_insights", ["organization_id", "insight_type"])


def downgrade() -> None:
    op.drop_table("team_insights")
    op.drop_table("collaborations")
    op.drop_table("code_ownership")
    op.drop_table("developers")
    
    # Drop enum type
    op.execute("DROP TYPE IF EXISTS ownership_type")
