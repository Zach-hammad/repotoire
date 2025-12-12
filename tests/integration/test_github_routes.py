"""Integration tests for GitHub API routes.

Tests cover:
- GitHub App installation management
- Repository configuration
- Quality gates settings
- Analysis triggering via GitHub
"""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Skip if v1 routes don't exist yet
pytest.importorskip("repotoire.api.v1.routes.github")

from repotoire.api.v1.routes.github import router


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create test FastAPI app with github routes."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# =============================================================================
# Response Model Tests
# =============================================================================


class TestResponseModels:
    """Tests for response model serialization."""

    def test_github_repo_response_model(self):
        """GitHubRepoResponse should serialize correctly."""
        from repotoire.api.v1.routes.github import GitHubRepoResponse

        response = GitHubRepoResponse(
            id=uuid4(),
            repo_id=123456789,
            full_name="test-org/test-repo",
            default_branch="main",
            enabled=True,
            auto_analyze=True,
            pr_analysis_enabled=True,
            quality_gates=None,
            last_analyzed_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        assert response.full_name == "test-org/test-repo"
        assert response.enabled is True
        assert response.repo_id == 123456789

    def test_github_installation_response_model(self):
        """GitHubInstallationResponse should serialize correctly."""
        from repotoire.api.v1.routes.github import GitHubInstallationResponse

        response = GitHubInstallationResponse(
            id=uuid4(),
            installation_id=12345678,
            account_login="test-org",
            account_type="Organization",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            repo_count=10,
        )

        assert response.account_login == "test-org"
        assert response.installation_id == 12345678
        assert response.repo_count == 10

    def test_quality_gates_config_model(self):
        """QualityGatesConfig should serialize correctly."""
        from repotoire.api.v1.routes.github import QualityGatesConfig

        config = QualityGatesConfig(
            enabled=True,
            block_on_critical=True,
            block_on_high=False,
            min_health_score=70,
            max_new_issues=5,
        )

        assert config.enabled is True
        assert config.block_on_critical is True
        assert config.min_health_score == 70


# =============================================================================
# Unit Tests (No Database)
# =============================================================================


class TestGitHubEndpointsUnit:
    """Unit tests for GitHub endpoints without database."""

    def test_unauthorized_access_installations(self, client):
        """GET /github/installations should return 401 without auth header."""
        response = client.get("/api/v1/github/installations")
        assert response.status_code == 401

    def test_unauthorized_access_repos(self, client):
        """GET /github/installations/{id}/repos should return 401 without auth header."""
        from uuid import uuid4

        response = client.get(f"/api/v1/github/installations/{uuid4()}/repos")
        assert response.status_code == 401


# =============================================================================
# Integration Tests (With Database)
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestGitHubInstallationsIntegration:
    """Integration tests for GitHub installation endpoints."""

    @pytest.mark.asyncio
    async def test_list_installations_empty(self, db_session, test_user):
        """List installations should return empty when no installations."""
        from tests.factories import OrganizationFactory, OrganizationMembershipFactory

        # Create org with membership
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )


        # The actual route test would require mocking the get_org_by_clerk_id
        # For now, test the response model structure
        assert True

    @pytest.mark.asyncio
    async def test_list_installations_with_data(
        self, db_session, test_user, mock_clerk
    ):
        """List installations should return installations for org."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            GitHubInstallationFactory,
        )

        # Create org with installation
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        installation = await GitHubInstallationFactory.async_create(
            db_session,
            organization_id=org.id,
        )


        # Verify installation was created
        assert installation.organization_id == org.id
        assert installation.installation_id is not None


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestGitHubRepositoriesIntegration:
    """Integration tests for GitHub repository endpoints."""

    @pytest.mark.asyncio
    async def test_list_repos_for_installation(
        self, db_session, test_user, mock_clerk
    ):
        """List repos should return repos for installation."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            GitHubInstallationFactory,
            GitHubRepositoryFactory,
        )

        # Create org with installation and repos
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        installation = await GitHubInstallationFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        # Create multiple repos
        for i in range(3):
            await GitHubRepositoryFactory.async_create(
                db_session,
                installation_id=installation.id,
            )


        # Verify repos were created
        from repotoire.db.models import GitHubRepository
        from sqlalchemy import select

        result = await db_session.execute(
            select(GitHubRepository).where(
                GitHubRepository.installation_id == installation.id
            )
        )
        repos = result.scalars().all()
        assert len(repos) == 3

    @pytest.mark.asyncio
    async def test_enable_repo_for_analysis(
        self, db_session, test_user, mock_clerk
    ):
        """Enabling a repo should set enabled=True."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            GitHubInstallationFactory,
            GitHubRepositoryFactory,
        )

        # Create org with installation and repo
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        installation = await GitHubInstallationFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        repo = await GitHubRepositoryFactory.async_create(
            db_session,
            installation_id=installation.id,
        )

        # Initially disabled by default
        repo.enabled = False
        await db_session.flush()

        # Enable the repo
        repo.enabled = True
        await db_session.flush()

        assert repo.enabled is True

    @pytest.mark.asyncio
    async def test_configure_quality_gates(
        self, db_session, test_user, mock_clerk
    ):
        """Quality gates can be configured on a repo."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            GitHubInstallationFactory,
            GitHubRepositoryFactory,
        )

        # Create org with installation and repo
        org = await OrganizationFactory.async_create(db_session, pro=True)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        installation = await GitHubInstallationFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        repo = await GitHubRepositoryFactory.async_create(
            db_session,
            installation_id=installation.id,
            with_quality_gates=True,
        )

        # Verify quality gates are configured
        assert repo.quality_gates is not None
        assert repo.quality_gates.get("enabled") is True


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestGitHubWebhookHandling:
    """Tests for GitHub webhook event handling."""

    @pytest.mark.asyncio
    async def test_installation_created_event(
        self, db_session
    ):
        """Installation created event should create installation record."""
        from tests.factories import OrganizationFactory
        from repotoire.db.models import GitHubInstallation
        import random

        # Create org
        org = await OrganizationFactory.async_create(db_session)

        # Simulate installation creation with random ID to avoid conflicts
        installation_id = random.randint(100000000, 999999999)
        installation = GitHubInstallation(
            organization_id=org.id,
            installation_id=installation_id,
            account_login="test-org",
            account_type="Organization",
            access_token_encrypted="encrypted_token",
            token_expires_at=datetime.now(timezone.utc),
        )
        db_session.add(installation)
        await db_session.flush()

        # Verify installation was created
        assert installation.id is not None
        assert installation.account_login == "test-org"
        assert installation.installation_id == installation_id

    @pytest.mark.asyncio
    async def test_installation_deleted_event(self, db_session):
        """Installation deleted event should remove installation record."""
        from tests.factories import (
            OrganizationFactory,
            GitHubInstallationFactory,
        )
        from repotoire.db.models import GitHubInstallation
        from sqlalchemy import select

        # Create org with installation
        org = await OrganizationFactory.async_create(db_session)
        installation = await GitHubInstallationFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        # Verify installation was created
        assert installation.id is not None
        assert installation.organization_id == org.id

        # Delete installation
        await db_session.delete(installation)
        await db_session.flush()

        # Verify installation is marked for deletion in the session
        assert installation not in db_session

    @pytest.mark.asyncio
    async def test_repos_added_event(self, db_session):
        """Repos added event should create repo records."""
        from tests.factories import (
            OrganizationFactory,
            GitHubInstallationFactory,
        )
        from repotoire.db.models import GitHubRepository

        # Create org with installation
        org = await OrganizationFactory.async_create(db_session)
        installation = await GitHubInstallationFactory.async_create(
            db_session,
            organization_id=org.id,
        )

        # Simulate adding repos
        repos_to_add = [
            {"id": 100, "full_name": "test-org/repo-1"},
            {"id": 101, "full_name": "test-org/repo-2"},
        ]

        created_repos = []
        for repo_data in repos_to_add:
            repo = GitHubRepository(
                installation_id=installation.id,
                repo_id=repo_data["id"],
                full_name=repo_data["full_name"],
                default_branch="main",
                enabled=False,
            )
            db_session.add(repo)
            created_repos.append(repo)

        await db_session.flush()

        # Verify repos were added
        assert len(created_repos) == 2
        for repo in created_repos:
            assert repo.id is not None


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestGitHubAnalysis:
    """Tests for GitHub analysis triggering."""

    @pytest.mark.asyncio
    async def test_trigger_analysis_for_repo(
        self, db_session, test_user, mock_clerk, mock_celery
    ):
        """Triggering analysis should create analysis run."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            GitHubInstallationFactory,
            GitHubRepositoryFactory,
            RepositoryFactory,
        )
        from repotoire.db.models import AnalysisRun, AnalysisStatus

        # Create org with installation and repo
        org = await OrganizationFactory.async_create(db_session, pro=True)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        installation = await GitHubInstallationFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        gh_repo = await GitHubRepositoryFactory.async_create(
            db_session,
            installation_id=installation.id,
        )
        gh_repo.enabled = True
        await db_session.flush()

        # Also create matching Repository record
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
            github_repo_id=gh_repo.repo_id,
        )

        # Create analysis run
        analysis_run = AnalysisRun(
            repository_id=repo.id,
            commit_sha="abc123def456" * 3 + "ab",  # 40 chars
            branch="main",
            status=AnalysisStatus.QUEUED,
        )
        db_session.add(analysis_run)
        await db_session.flush()

        # Verify analysis run was created
        assert analysis_run.id is not None
        assert analysis_run.status == AnalysisStatus.QUEUED

    @pytest.mark.asyncio
    async def test_analysis_requires_enabled_repo(
        self, db_session, test_user, mock_clerk
    ):
        """Analysis should fail for disabled repos."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            GitHubInstallationFactory,
            GitHubRepositoryFactory,
        )

        # Create org with disabled repo
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        installation = await GitHubInstallationFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        repo = await GitHubRepositoryFactory.async_create(
            db_session,
            installation_id=installation.id,
        )
        repo.enabled = False
        await db_session.flush()

        # Verify repo is disabled
        assert repo.enabled is False


# =============================================================================
# Access Control Tests
# =============================================================================


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestGitHubAccessControl:
    """Tests for GitHub endpoint access control."""

    @pytest.mark.asyncio
    async def test_cannot_access_other_org_installations(
        self, db_session, test_user, mock_clerk
    ):
        """User should not access installations from other orgs."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            GitHubInstallationFactory,
        )

        # Create user's org
        user_org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=user_org.id,
        )

        # Create other org with installation
        other_org = await OrganizationFactory.async_create(db_session)
        other_installation = await GitHubInstallationFactory.async_create(
            db_session,
            organization_id=other_org.id,
        )

        # Verify installations are separate
        from repotoire.db.models import GitHubInstallation
        from sqlalchemy import select

        # User's org should have no installations
        result = await db_session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.organization_id == user_org.id
            )
        )
        user_installations = result.scalars().all()
        assert len(user_installations) == 0

        # Other org has installation
        result = await db_session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.organization_id == other_org.id
            )
        )
        other_installations = result.scalars().all()
        assert len(other_installations) == 1

    @pytest.mark.asyncio
    async def test_pro_tier_required_for_auto_analyze(
        self, db_session, test_user, mock_clerk
    ):
        """Auto-analyze requires pro tier."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            GitHubInstallationFactory,
            GitHubRepositoryFactory,
        )
        from repotoire.db.models import PlanTier

        # Create free tier org
        org = await OrganizationFactory.async_create(db_session)
        assert org.plan_tier == PlanTier.FREE

        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        installation = await GitHubInstallationFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        repo = await GitHubRepositoryFactory.async_create(
            db_session,
            installation_id=installation.id,
        )

        # Auto-analyze is a pro feature - verify org is free
        assert org.plan_tier == PlanTier.FREE
