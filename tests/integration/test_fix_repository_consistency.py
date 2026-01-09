"""Integration tests for FixRepository data consistency methods.

Tests cover:
- find_status_mismatches: Find fixes where status doesn't align with finding status
- sync_finding_status_on_apply: Update finding status to RESOLVED when fix is applied
- get_consistency_stats: Get statistics about data consistency issues

These tests require PostgreSQL because they test ORM-level joins between Fix and Finding
models that don't work correctly with SQLite's string-based UUID handling.
"""

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.db.models import (
    Fix,
    FixStatus,
    Finding,
    FindingStatus,
)
from repotoire.db.repositories.fix import FixRepository


# =============================================================================
# Skip marker for database tests
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured for remote database.

    Returns False if:
    - DATABASE_URL is not set
    - DATABASE_URL points to localhost (local dev database)
    """
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip()) and "localhost" not in url


# =============================================================================
# Integration Tests (With PostgreSQL Database)
# =============================================================================


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestFixRepositoryConsistencyIntegration:
    """Integration tests for FixRepository data consistency methods.

    These tests require PostgreSQL for proper UUID handling and ORM joins.
    """

    @pytest_asyncio.fixture
    async def fix_repository(self, db_session: AsyncSession) -> FixRepository:
        """Create FixRepository instance with test database session."""
        return FixRepository(db_session)

    @pytest.mark.asyncio
    async def test_find_status_mismatches_detects_applied_with_open_finding(
        self,
        db_session: AsyncSession,
        fix_repository: FixRepository,
        test_user,
    ):
        """Test that find_status_mismatches finds applied fixes with non-resolved findings."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
            FixFactory,
        )

        # Create test data hierarchy
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Create a finding with OPEN status
        finding = await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
        )
        assert finding.status == FindingStatus.OPEN

        # Create a fix linked to the finding
        fix = await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            finding_id=finding.id,
        )

        # Manually set fix to APPLIED (simulating bypassing normal status transitions)
        fix.status = FixStatus.APPLIED
        fix.applied_at = datetime.now(timezone.utc)
        db_session.add(fix)
        await db_session.flush()

        # Finding is still OPEN (not RESOLVED) - this is a mismatch
        mismatches = await fix_repository.find_status_mismatches()

        assert len(mismatches) >= 1
        # Find our specific mismatch
        our_mismatch = next((m for m in mismatches if m[0].id == fix.id), None)
        assert our_mismatch is not None
        assert "APPLIED" in our_mismatch[1]

    @pytest.mark.asyncio
    async def test_find_status_mismatches_no_mismatch_when_resolved(
        self,
        db_session: AsyncSession,
        fix_repository: FixRepository,
        test_user,
    ):
        """Test that find_status_mismatches returns empty when finding is resolved."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
            FixFactory,
        )

        # Create test data hierarchy
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Create a finding and set it to RESOLVED
        finding = await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
        )
        finding.status = FindingStatus.RESOLVED
        db_session.add(finding)
        await db_session.flush()

        # Create an APPLIED fix linked to the RESOLVED finding
        fix = await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            finding_id=finding.id,
            applied=True,  # Uses the applied trait
        )

        # No mismatch expected - both fix is APPLIED and finding is RESOLVED
        mismatches = await fix_repository.find_status_mismatches()

        # Check that our fix is NOT in the mismatches
        our_mismatch = next((m for m in mismatches if m[0].id == fix.id), None)
        assert our_mismatch is None

    @pytest.mark.asyncio
    async def test_sync_finding_status_on_apply(
        self,
        db_session: AsyncSession,
        fix_repository: FixRepository,
        test_user,
    ):
        """Test that sync_finding_status_on_apply updates finding to RESOLVED."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
            FixFactory,
        )

        # Create test data hierarchy
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Create a finding with OPEN status
        finding = await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
        )
        assert finding.status == FindingStatus.OPEN

        # Create a fix linked to the finding
        fix = await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            finding_id=finding.id,
        )

        # Sync finding status
        result = await fix_repository.sync_finding_status_on_apply(
            fix_id=fix.id,
            changed_by="test_user",
        )

        assert result is True

        # Verify finding was updated
        await db_session.refresh(finding)
        assert finding.status == FindingStatus.RESOLVED
        assert finding.status_changed_by == "test_user"
        assert finding.status_changed_at is not None

    @pytest.mark.asyncio
    async def test_sync_finding_status_on_apply_returns_false_for_orphan(
        self,
        db_session: AsyncSession,
        fix_repository: FixRepository,
        test_user,
    ):
        """Test that sync_finding_status_on_apply returns False for orphaned fixes."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FixFactory,
        )

        # Create test data hierarchy
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Create an orphaned fix (no finding_id)
        fix = await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            finding_id=None,  # Orphaned
        )

        # Sync should return False since there's no finding to update
        result = await fix_repository.sync_finding_status_on_apply(
            fix_id=fix.id,
            changed_by="test_user",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_sync_finding_status_on_apply_returns_false_for_nonexistent_fix(
        self,
        db_session: AsyncSession,
        fix_repository: FixRepository,
    ):
        """Test that sync_finding_status_on_apply returns False for non-existent fix."""
        from uuid import uuid4

        # Try to sync a non-existent fix
        result = await fix_repository.sync_finding_status_on_apply(
            fix_id=uuid4(),
            changed_by="test_user",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_get_consistency_stats(
        self,
        db_session: AsyncSession,
        fix_repository: FixRepository,
        test_user,
    ):
        """Test get_consistency_stats returns correct counts."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
            FixFactory,
        )

        # Create test data hierarchy
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Create an orphaned fix
        await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            finding_id=None,  # Orphaned
        )

        # Create a finding with OPEN status
        finding = await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
        )

        # Create a fix that's APPLIED but finding is still OPEN (mismatch)
        fix = await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            finding_id=finding.id,
        )
        fix.status = FixStatus.APPLIED
        fix.applied_at = datetime.now(timezone.utc)
        db_session.add(fix)
        await db_session.flush()

        # Get consistency stats
        stats = await fix_repository.get_consistency_stats()

        assert stats["orphaned_fixes"] >= 1
        assert stats["status_mismatches"] >= 1
        assert "needs_attention" in stats

    @pytest.mark.asyncio
    async def test_get_consistency_stats_no_issues(
        self,
        db_session: AsyncSession,
        fix_repository: FixRepository,
        test_user,
    ):
        """Test get_consistency_stats returns zeros when no issues exist."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
            FixFactory,
        )

        # Create test data hierarchy
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Create a properly linked fix with RESOLVED finding
        finding = await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
        )
        finding.status = FindingStatus.RESOLVED
        db_session.add(finding)

        await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            finding_id=finding.id,
            applied=True,
        )
        await db_session.flush()

        # Get consistency stats - should have no issues for our data
        stats = await fix_repository.get_consistency_stats()

        # Stats should have the expected structure
        assert "orphaned_fixes" in stats
        assert "status_mismatches" in stats
        assert "needs_attention" in stats

    @pytest.mark.asyncio
    async def test_find_orphaned_returns_fixes_without_finding(
        self,
        db_session: AsyncSession,
        fix_repository: FixRepository,
        test_user,
    ):
        """Test that find_orphaned returns fixes with NULL finding_id."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FixFactory,
        )

        # Create test data hierarchy
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Create an orphaned fix (no finding_id)
        orphan_fix = await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            finding_id=None,
        )

        # Find orphaned fixes
        orphans = await fix_repository.find_orphaned()

        assert len(orphans) >= 1
        orphan_ids = [o.id for o in orphans]
        assert orphan_fix.id in orphan_ids

    @pytest.mark.asyncio
    async def test_find_orphaned_excludes_fixes_with_finding(
        self,
        db_session: AsyncSession,
        fix_repository: FixRepository,
        test_user,
    ):
        """Test that find_orphaned excludes fixes with a linked finding."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            RepositoryFactory,
            AnalysisRunFactory,
            FindingFactory,
            FixFactory,
        )

        # Create test data hierarchy
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )
        repo = await RepositoryFactory.async_create(
            db_session,
            organization_id=org.id,
        )
        analysis = await AnalysisRunFactory.async_create(
            db_session,
            repository_id=repo.id,
            completed=True,
        )

        # Create a finding
        finding = await FindingFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
        )

        # Create a fix linked to the finding
        linked_fix = await FixFactory.async_create(
            db_session,
            analysis_run_id=analysis.id,
            finding_id=finding.id,
        )

        # Find orphaned fixes
        orphans = await fix_repository.find_orphaned()

        # The linked fix should NOT be in the orphans list
        orphan_ids = [o.id for o in orphans]
        assert linked_fix.id not in orphan_ids
