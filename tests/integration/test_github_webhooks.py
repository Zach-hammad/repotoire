"""Integration tests for GitHub webhook auto-analysis feature.

Tests all gating conditions for auto-analysis on push events:
1. Repository enabled/disabled
2. Auto-analyze flag
3. Organization plan tier (free vs pro/enterprise)
4. Branch filtering (default branch only)
5. Push debouncing
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.v1.routes.github import handle_push_event
from repotoire.db.models import (
    AnalysisRun,
    AnalysisStatus,
    GitHubInstallation,
    GitHubRepository,
    Organization,
    PlanTier,
    Repository,
)


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def org_pro():
    """Create a pro tier organization."""
    org = MagicMock(spec=Organization)
    org.id = uuid4()
    org.plan_tier = PlanTier.PRO
    org.slug = "acme"
    return org


@pytest.fixture
def org_enterprise():
    """Create an enterprise tier organization."""
    org = MagicMock(spec=Organization)
    org.id = uuid4()
    org.plan_tier = PlanTier.ENTERPRISE
    org.slug = "enterprise-co"
    return org


@pytest.fixture
def org_free():
    """Create a free tier organization."""
    org = MagicMock(spec=Organization)
    org.id = uuid4()
    org.plan_tier = PlanTier.FREE
    org.slug = "free-user"
    return org


@pytest.fixture
def github_installation(org_pro):
    """Create a GitHub installation linked to a pro org."""
    installation = MagicMock(spec=GitHubInstallation)
    installation.id = uuid4()
    installation.installation_id = 67890
    installation.organization = org_pro
    installation.account_login = "acme"
    return installation


@pytest.fixture
def github_repo_enabled(github_installation):
    """Create an enabled GitHub repository with auto_analyze=True."""
    repo = MagicMock(spec=GitHubRepository)
    repo.id = uuid4()
    repo.repo_id = 12345
    repo.full_name = "acme/webapp"
    repo.default_branch = "main"
    repo.enabled = True
    repo.auto_analyze = True
    repo.installation_id = github_installation.id
    return repo


@pytest.fixture
def github_repo_disabled(github_installation):
    """Create a disabled GitHub repository."""
    repo = MagicMock(spec=GitHubRepository)
    repo.id = uuid4()
    repo.repo_id = 99999
    repo.full_name = "acme/disabled-repo"
    repo.default_branch = "main"
    repo.enabled = False
    repo.auto_analyze = True
    repo.installation_id = github_installation.id
    return repo


@pytest.fixture
def push_payload_main():
    """Create a push payload for the main branch."""
    return {
        "ref": "refs/heads/main",
        "after": "abc123def456",
        "repository": {
            "id": 12345,
            "full_name": "acme/webapp",
            "default_branch": "main",
        },
        "installation": {"id": 67890},
    }


@pytest.fixture
def push_payload_feature():
    """Create a push payload for a feature branch."""
    return {
        "ref": "refs/heads/feature/new-thing",
        "after": "def456abc123",
        "repository": {
            "id": 12345,
            "full_name": "acme/webapp",
            "default_branch": "main",
        },
        "installation": {"id": 67890},
    }


class TestPushEventGating:
    """Test push event gating conditions."""

    @pytest.mark.asyncio
    async def test_push_to_enabled_repo_pro_tier_triggers_analysis(
        self, mock_db, github_installation, github_repo_enabled, push_payload_main
    ):
        """Push to enabled repo with pro tier org should trigger analysis."""
        # Setup mock database responses
        installation_result = MagicMock()
        installation_result.scalar_one_or_none.return_value = github_installation

        repo_result = MagicMock()
        repo_result.scalar_one_or_none.return_value = github_repo_enabled

        # No existing Repository record
        repository_result = MagicMock()
        repository_result.scalar_one_or_none.return_value = None

        mock_db.execute.side_effect = [
            installation_result,
            repo_result,
            repository_result,
        ]

        with patch(
            "repotoire.workers.debounce.get_push_debouncer"
        ) as mock_debouncer_factory:
            mock_debouncer = MagicMock()
            mock_debouncer.should_analyze.return_value = True
            mock_debouncer_factory.return_value = mock_debouncer

            with patch(
                "repotoire.workers.tasks.analyze_repository"
            ) as mock_analyze:
                mock_analyze.delay = MagicMock()

                await handle_push_event(mock_db, push_payload_main)

                # Verify analysis was triggered
                mock_analyze.delay.assert_called_once()
                call_kwargs = mock_analyze.delay.call_args[1]
                assert call_kwargs["commit_sha"] == "abc123def456"
                assert call_kwargs["incremental"] is True

    @pytest.mark.asyncio
    async def test_push_to_disabled_repo_skips_analysis(
        self, mock_db, github_installation, github_repo_disabled, push_payload_main
    ):
        """Push to disabled repo should skip analysis."""
        push_payload = {
            **push_payload_main,
            "repository": {
                **push_payload_main["repository"],
                "id": 99999,
                "full_name": "acme/disabled-repo",
            },
        }

        installation_result = MagicMock()
        installation_result.scalar_one_or_none.return_value = github_installation

        repo_result = MagicMock()
        repo_result.scalar_one_or_none.return_value = github_repo_disabled

        mock_db.execute.side_effect = [installation_result, repo_result]

        with patch(
            "repotoire.workers.tasks.analyze_repository"
        ) as mock_analyze:
            await handle_push_event(mock_db, push_payload)

            # Verify analysis was NOT triggered
            mock_analyze.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_to_free_tier_org_skips_analysis(
        self, mock_db, org_free, github_repo_enabled, push_payload_main
    ):
        """Push to free tier org should skip analysis."""
        # Create installation linked to free org
        free_installation = MagicMock(spec=GitHubInstallation)
        free_installation.id = uuid4()
        free_installation.installation_id = 11111
        free_installation.organization = org_free

        github_repo_enabled.installation_id = free_installation.id

        # Update payload with free tier installation
        push_payload = {
            **push_payload_main,
            "installation": {"id": 11111},
        }

        installation_result = MagicMock()
        installation_result.scalar_one_or_none.return_value = free_installation

        repo_result = MagicMock()
        repo_result.scalar_one_or_none.return_value = github_repo_enabled

        mock_db.execute.side_effect = [installation_result, repo_result]

        with patch(
            "repotoire.workers.tasks.analyze_repository"
        ) as mock_analyze:
            await handle_push_event(mock_db, push_payload)

            # Verify analysis was NOT triggered
            mock_analyze.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_to_enterprise_tier_triggers_analysis(
        self, mock_db, org_enterprise, github_repo_enabled, push_payload_main
    ):
        """Push to enterprise tier org should trigger analysis."""
        # Create installation linked to enterprise org
        enterprise_installation = MagicMock(spec=GitHubInstallation)
        enterprise_installation.id = uuid4()
        enterprise_installation.installation_id = 67890
        enterprise_installation.organization = org_enterprise

        github_repo_enabled.installation_id = enterprise_installation.id

        installation_result = MagicMock()
        installation_result.scalar_one_or_none.return_value = enterprise_installation

        repo_result = MagicMock()
        repo_result.scalar_one_or_none.return_value = github_repo_enabled

        repository_result = MagicMock()
        repository_result.scalar_one_or_none.return_value = None

        mock_db.execute.side_effect = [
            installation_result,
            repo_result,
            repository_result,
        ]

        with patch(
            "repotoire.workers.debounce.get_push_debouncer"
        ) as mock_debouncer_factory:
            mock_debouncer = MagicMock()
            mock_debouncer.should_analyze.return_value = True
            mock_debouncer_factory.return_value = mock_debouncer

            with patch(
                "repotoire.workers.tasks.analyze_repository"
            ) as mock_analyze:
                mock_analyze.delay = MagicMock()

                await handle_push_event(mock_db, push_payload_main)

                # Verify analysis was triggered
                mock_analyze.delay.assert_called_once()

    @pytest.mark.asyncio
    async def test_push_to_feature_branch_skips_analysis(
        self, mock_db, push_payload_feature
    ):
        """Push to non-default branch should skip analysis."""
        with patch(
            "repotoire.workers.tasks.analyze_repository"
        ) as mock_analyze:
            await handle_push_event(mock_db, push_payload_feature)

            # Verify no database calls for non-default branch
            mock_db.execute.assert_not_called()
            mock_analyze.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_with_auto_analyze_disabled_skips_analysis(
        self, mock_db, github_installation, github_repo_enabled, push_payload_main
    ):
        """Push with auto_analyze=False should skip analysis."""
        # Disable auto_analyze
        github_repo_enabled.auto_analyze = False

        installation_result = MagicMock()
        installation_result.scalar_one_or_none.return_value = github_installation

        repo_result = MagicMock()
        repo_result.scalar_one_or_none.return_value = github_repo_enabled

        mock_db.execute.side_effect = [installation_result, repo_result]

        with patch(
            "repotoire.workers.tasks.analyze_repository"
        ) as mock_analyze:
            await handle_push_event(mock_db, push_payload_main)

            # Verify analysis was NOT triggered
            mock_analyze.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_installation_id_skips_analysis(self, mock_db):
        """Push without installation_id should skip analysis."""
        payload = {
            "ref": "refs/heads/main",
            "after": "abc123",
            "repository": {
                "id": 12345,
                "full_name": "acme/webapp",
                "default_branch": "main",
            },
            # No installation key
        }

        with patch(
            "repotoire.workers.tasks.analyze_repository"
        ) as mock_analyze:
            await handle_push_event(mock_db, payload)

            mock_db.execute.assert_not_called()
            mock_analyze.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_installation_skips_analysis(self, mock_db, push_payload_main):
        """Push for unknown installation should skip analysis."""
        installation_result = MagicMock()
        installation_result.scalar_one_or_none.return_value = None

        mock_db.execute.return_value = installation_result

        with patch(
            "repotoire.workers.tasks.analyze_repository"
        ) as mock_analyze:
            await handle_push_event(mock_db, push_payload_main)

            mock_analyze.delay.assert_not_called()


class TestPushDebouncing:
    """Test push debouncing behavior."""

    @pytest.mark.asyncio
    async def test_debounced_push_skips_analysis(
        self, mock_db, github_installation, github_repo_enabled, push_payload_main
    ):
        """Debounced push should skip analysis."""
        installation_result = MagicMock()
        installation_result.scalar_one_or_none.return_value = github_installation

        repo_result = MagicMock()
        repo_result.scalar_one_or_none.return_value = github_repo_enabled

        mock_db.execute.side_effect = [installation_result, repo_result]

        with patch(
            "repotoire.workers.debounce.get_push_debouncer"
        ) as mock_debouncer_factory:
            mock_debouncer = MagicMock()
            mock_debouncer.should_analyze.return_value = False  # Debounced
            mock_debouncer_factory.return_value = mock_debouncer

            with patch(
                "repotoire.workers.tasks.analyze_repository"
            ) as mock_analyze:
                await handle_push_event(mock_db, push_payload_main)

                # Verify debouncer was checked
                mock_debouncer.should_analyze.assert_called_once_with(12345)
                # Verify analysis was NOT triggered
                mock_analyze.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_push_not_debounced(
        self, mock_db, github_installation, github_repo_enabled, push_payload_main
    ):
        """First push should not be debounced."""
        installation_result = MagicMock()
        installation_result.scalar_one_or_none.return_value = github_installation

        repo_result = MagicMock()
        repo_result.scalar_one_or_none.return_value = github_repo_enabled

        repository_result = MagicMock()
        repository_result.scalar_one_or_none.return_value = None

        mock_db.execute.side_effect = [
            installation_result,
            repo_result,
            repository_result,
        ]

        with patch(
            "repotoire.workers.debounce.get_push_debouncer"
        ) as mock_debouncer_factory:
            mock_debouncer = MagicMock()
            mock_debouncer.should_analyze.return_value = True  # Not debounced
            mock_debouncer_factory.return_value = mock_debouncer

            with patch(
                "repotoire.workers.tasks.analyze_repository"
            ) as mock_analyze:
                mock_analyze.delay = MagicMock()

                await handle_push_event(mock_db, push_payload_main)

                mock_debouncer.should_analyze.assert_called_once_with(12345)
                mock_analyze.delay.assert_called_once()


class TestDebouncerUnit:
    """Unit tests for PushDebouncer."""

    def test_should_analyze_returns_true_without_redis(self):
        """Without Redis, should_analyze always returns True."""
        from repotoire.workers.debounce import PushDebouncer

        debouncer = PushDebouncer(redis=None)
        assert debouncer.should_analyze(repo_id=12345) is True
        assert debouncer.should_analyze(repo_id=12345) is True

    def test_should_analyze_with_redis_first_push(self):
        """First push should return True and set key."""
        from repotoire.workers.debounce import PushDebouncer

        mock_redis = MagicMock()
        mock_redis.set.return_value = True  # Key was set (first push)

        debouncer = PushDebouncer(redis=mock_redis, ttl_seconds=60)
        result = debouncer.should_analyze(repo_id=12345)

        assert result is True
        mock_redis.set.assert_called_once_with(
            "push:debounce:12345", "1", nx=True, ex=60
        )

    def test_should_analyze_with_redis_debounced(self):
        """Second push within TTL should return False."""
        from repotoire.workers.debounce import PushDebouncer

        mock_redis = MagicMock()
        mock_redis.set.return_value = False  # Key already exists

        debouncer = PushDebouncer(redis=mock_redis, ttl_seconds=60)
        result = debouncer.should_analyze(repo_id=12345)

        assert result is False

    def test_should_analyze_redis_error_gracefully_degrades(self):
        """Redis error should gracefully degrade to True."""
        from repotoire.workers.debounce import PushDebouncer

        mock_redis = MagicMock()
        mock_redis.set.side_effect = Exception("Connection refused")

        debouncer = PushDebouncer(redis=mock_redis, ttl_seconds=60)
        result = debouncer.should_analyze(repo_id=12345)

        # Graceful degradation - allow analysis
        assert result is True

    def test_clear_removes_key(self):
        """Clear should remove debounce key."""
        from repotoire.workers.debounce import PushDebouncer

        mock_redis = MagicMock()
        mock_redis.delete.return_value = 1

        debouncer = PushDebouncer(redis=mock_redis)
        result = debouncer.clear(repo_id=12345)

        assert result is True
        mock_redis.delete.assert_called_once_with("push:debounce:12345")

    def test_get_ttl_returns_remaining_time(self):
        """get_ttl should return remaining TTL."""
        from repotoire.workers.debounce import PushDebouncer

        mock_redis = MagicMock()
        mock_redis.ttl.return_value = 45

        debouncer = PushDebouncer(redis=mock_redis)
        result = debouncer.get_ttl(repo_id=12345)

        assert result == 45

    def test_get_ttl_returns_none_for_missing_key(self):
        """get_ttl should return None for non-existent key."""
        from repotoire.workers.debounce import PushDebouncer

        mock_redis = MagicMock()
        mock_redis.ttl.return_value = -2  # Key doesn't exist

        debouncer = PushDebouncer(redis=mock_redis)
        result = debouncer.get_ttl(repo_id=12345)

        assert result is None

    def test_custom_prefix(self):
        """Custom prefix should be used in keys."""
        from repotoire.workers.debounce import PushDebouncer

        mock_redis = MagicMock()
        mock_redis.set.return_value = True

        debouncer = PushDebouncer(
            redis=mock_redis, prefix="webhook:push:", ttl_seconds=120
        )
        debouncer.should_analyze(repo_id=99999)

        mock_redis.set.assert_called_once_with(
            "webhook:push:99999", "1", nx=True, ex=120
        )


class TestGetPushDebouncer:
    """Test get_push_debouncer factory function."""

    def test_returns_debouncer_without_redis_url(self):
        """Without REDIS_URL, returns debouncer with no Redis."""
        from repotoire.workers.debounce import get_push_debouncer

        with patch.dict("os.environ", {}, clear=True):
            debouncer = get_push_debouncer()
            assert debouncer.redis is None

    def test_returns_debouncer_with_redis_url(self):
        """With valid REDIS_URL, returns connected debouncer."""
        from repotoire.workers.debounce import get_push_debouncer

        with patch.dict("os.environ", {"REDIS_URL": "redis://localhost:6379/0"}):
            with patch("redis.from_url") as mock_from_url:
                mock_client = MagicMock()
                mock_from_url.return_value = mock_client

                debouncer = get_push_debouncer()

                mock_from_url.assert_called_once_with("redis://localhost:6379/0")
                mock_client.ping.assert_called_once()
                assert debouncer.redis is mock_client

    def test_handles_redis_connection_failure(self):
        """Connection failure returns debouncer with no Redis."""
        from repotoire.workers.debounce import get_push_debouncer

        with patch.dict("os.environ", {"REDIS_URL": "redis://localhost:6379/0"}):
            with patch("redis.from_url") as mock_from_url:
                mock_from_url.side_effect = Exception("Connection refused")

                debouncer = get_push_debouncer()

                assert debouncer.redis is None
