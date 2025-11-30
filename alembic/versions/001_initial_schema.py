"""Initial schema for multi-tenant SaaS

Revision ID: 001
Revises:
Create Date: 2024-11-30

Creates the initial database schema for the Repotoire SaaS platform:
- users: Clerk-authenticated users
- organizations: Multi-tenant organizations with Stripe billing
- organization_memberships: User-to-organization role assignments
- repositories: GitHub repositories connected for analysis
- analysis_runs: Code health analysis job tracking
- github_installations: GitHub App installation management
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types first
    plan_tier_enum = postgresql.ENUM(
        "free", "pro", "enterprise",
        name="plan_tier",
        create_type=False,
    )
    plan_tier_enum.create(op.get_bind(), checkfirst=True)

    member_role_enum = postgresql.ENUM(
        "owner", "admin", "member",
        name="member_role",
        create_type=False,
    )
    member_role_enum.create(op.get_bind(), checkfirst=True)

    analysis_status_enum = postgresql.ENUM(
        "queued", "running", "completed", "failed",
        name="analysis_status",
        create_type=False,
    )
    analysis_status_enum.create(op.get_bind(), checkfirst=True)

    # Create users table
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("clerk_user_id", sa.String(255), unique=True, nullable=False),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("avatar_url", sa.String(2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_clerk_user_id", "users", ["clerk_user_id"])
    op.create_index("ix_users_email", "users", ["email"])

    # Create organizations table
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("stripe_customer_id", sa.String(255), unique=True, nullable=True),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
        sa.Column(
            "plan_tier",
            postgresql.ENUM("free", "pro", "enterprise", name="plan_tier", create_type=False),
            server_default="free",
            nullable=False,
        ),
        sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])
    op.create_index("ix_organizations_stripe_customer_id", "organizations", ["stripe_customer_id"])

    # Create organization_memberships table
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM("owner", "admin", "member", name="member_role", create_type=False),
            server_default="member",
            nullable=False,
        ),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint("uq_membership_user_org", "organization_memberships", ["user_id", "organization_id"])
    op.create_index("ix_organization_memberships_user_id", "organization_memberships", ["user_id"])
    op.create_index("ix_organization_memberships_organization_id", "organization_memberships", ["organization_id"])

    # Create github_installations table
    op.create_table(
        "github_installations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("installation_id", sa.Integer(), unique=True, nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_github_installations_organization_id", "github_installations", ["organization_id"])
    op.create_index("ix_github_installations_installation_id", "github_installations", ["installation_id"])

    # Create repositories table
    op.create_table(
        "repositories",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("github_repo_id", sa.Integer(), nullable=False),
        sa.Column("github_installation_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("default_branch", sa.String(255), server_default="main", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("health_score", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_repositories_organization_id", "repositories", ["organization_id"])
    op.create_index("ix_repositories_github_repo_id", "repositories", ["github_repo_id"])
    op.create_index("ix_repositories_full_name", "repositories", ["full_name"])
    op.create_index("ix_repositories_github_installation_id", "repositories", ["github_installation_id"])

    # Create analysis_runs table
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("repository_id", sa.UUID(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM("queued", "running", "completed", "failed", name="analysis_status", create_type=False),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("health_score", sa.Integer(), nullable=True),
        sa.Column("findings_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_analysis_runs_repository_id", "analysis_runs", ["repository_id"])
    op.create_index("ix_analysis_runs_commit_sha", "analysis_runs", ["commit_sha"])
    op.create_index("ix_analysis_runs_status", "analysis_runs", ["status"])
    op.create_index("ix_analysis_runs_created_at", "analysis_runs", ["created_at"])


def downgrade() -> None:
    # Drop tables in reverse order of creation (respecting foreign keys)
    op.drop_table("analysis_runs")
    op.drop_table("repositories")
    op.drop_table("github_installations")
    op.drop_table("organization_memberships")
    op.drop_table("organizations")
    op.drop_table("users")

    # Drop enum types
    postgresql.ENUM(name="analysis_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="member_role").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="plan_tier").drop(op.get_bind(), checkfirst=True)
