"""Unit tests for Redis cache layer.

Uses fakeredis for testing without a real Redis server.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List, Optional

import pytest
from pydantic import BaseModel, Field

# Try to import fakeredis
try:
    import fakeredis.aioredis

    FAKEREDIS_AVAILABLE = True
except ImportError:
    FAKEREDIS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not FAKEREDIS_AVAILABLE,
    reason="fakeredis not installed",
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def fake_redis():
    """Create a fake Redis client for testing."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def preview_cache(fake_redis):
    """Create a PreviewCache with fake Redis."""
    from repotoire.cache import PreviewCache

    return PreviewCache(fake_redis, ttl_seconds=60)


@pytest.fixture
def scan_cache(fake_redis):
    """Create a ScanCache with fake Redis."""
    from repotoire.cache import ScanCache

    return ScanCache(fake_redis, ttl_seconds=60)


@pytest.fixture
def skill_cache(fake_redis):
    """Create a TwoTierSkillCache with fake Redis."""
    from repotoire.cache import TwoTierSkillCache

    return TwoTierSkillCache(fake_redis, ttl_seconds=60)


# =============================================================================
# Helper Models
# =============================================================================


class MockPreviewCheck(BaseModel):
    """Mock model for preview check."""

    name: str
    passed: bool
    message: str
    duration_ms: int = 0


# =============================================================================
# Base Cache Tests
# =============================================================================


class TestCacheMetrics:
    """Tests for CacheMetrics class."""

    def test_metrics_initialization(self):
        """Test metrics are initialized to zero."""
        from repotoire.cache import CacheMetrics

        metrics = CacheMetrics("test")
        assert metrics.cache_name == "test"
        assert metrics.total_hits == 0
        assert metrics.misses == 0
        assert metrics.total_errors == 0

    def test_record_hit(self):
        """Test recording cache hits."""
        from repotoire.cache import CacheMetrics

        metrics = CacheMetrics("test")
        metrics.record_hit("redis")
        metrics.record_hit("local")
        metrics.record_hit("local")

        assert metrics.hits["redis"] == 1
        assert metrics.hits["local"] == 2
        assert metrics.total_hits == 3

    def test_record_miss(self):
        """Test recording cache misses."""
        from repotoire.cache import CacheMetrics

        metrics = CacheMetrics("test")
        metrics.record_miss()
        metrics.record_miss()

        assert metrics.misses == 2

    def test_hit_rate_calculation(self):
        """Test hit rate calculation."""
        from repotoire.cache import CacheMetrics

        metrics = CacheMetrics("test")

        # No operations - 0 hit rate
        assert metrics.hit_rate == 0.0

        # All hits
        metrics.record_hit()
        metrics.record_hit()
        assert metrics.hit_rate == 1.0

        # 2 hits, 2 misses = 50%
        metrics.record_miss()
        metrics.record_miss()
        assert metrics.hit_rate == 0.5

    def test_record_error(self):
        """Test recording errors by type."""
        from repotoire.cache import CacheMetrics

        metrics = CacheMetrics("test")
        metrics.record_error("connection", Exception("test error"))
        metrics.record_error("serialization", Exception("another error"))
        metrics.record_error("connection", Exception("third error"))

        assert metrics.errors["connection"] == 2
        assert metrics.errors["serialization"] == 1
        assert metrics.total_errors == 3

    def test_to_dict(self):
        """Test exporting metrics as dictionary."""
        from repotoire.cache import CacheMetrics

        metrics = CacheMetrics("test")
        metrics.record_hit("redis")
        metrics.record_miss()

        result = metrics.to_dict()
        assert result["cache"] == "test"
        assert result["total_hits"] == 1
        assert result["misses"] == 1
        assert result["hit_rate"] == 0.5


# =============================================================================
# Preview Cache Tests
# =============================================================================


class TestPreviewCache:
    """Tests for PreviewCache."""

    @pytest.mark.asyncio
    async def test_set_and_get_preview(self, preview_cache):
        """Test basic set and get operations."""
        from repotoire.api.models import PreviewCheck, PreviewResult

        result = PreviewResult(
            success=True,
            stdout="Test output",
            stderr="",
            duration_ms=100,
            checks=[
                PreviewCheck(
                    name="syntax",
                    passed=True,
                    message="Syntax valid",
                    duration_ms=5,
                )
            ],
        )

        # Set preview
        success = await preview_cache.set_preview("fix-123", result)
        assert success is True

        # Get preview
        cached = await preview_cache.get_preview("fix-123")
        assert cached is not None
        assert cached.success is True
        assert cached.stdout == "Test output"
        assert len(cached.checks) == 1
        assert cached.checks[0].name == "syntax"

    @pytest.mark.asyncio
    async def test_get_nonexistent_preview(self, preview_cache):
        """Test getting a preview that doesn't exist."""
        cached = await preview_cache.get_preview("nonexistent")
        assert cached is None

    @pytest.mark.asyncio
    async def test_invalidate_preview(self, preview_cache):
        """Test invalidating a cached preview."""
        from repotoire.api.models import PreviewResult

        result = PreviewResult(success=True, duration_ms=100)
        await preview_cache.set_preview("fix-123", result)

        # Verify it's cached
        assert await preview_cache.get_preview("fix-123") is not None

        # Invalidate
        success = await preview_cache.invalidate("fix-123")
        assert success is True

        # Verify it's gone
        assert await preview_cache.get_preview("fix-123") is None

    @pytest.mark.asyncio
    async def test_get_with_hash_check_valid(self, preview_cache):
        """Test hash validation passes when hash matches."""
        from repotoire.api.models import PreviewResult

        fix_hash = "abc123def456"  # Needs to be longer to use rsplit properly
        result = PreviewResult(
            success=True,
            duration_ms=100,
            # Use ISO timestamp format with hash at end after final colon
            cached_at=f"2025-01-01T00:00:00Z:{fix_hash}",
        )
        await preview_cache.set_preview("fix-123", result)

        # Same hash should return cached result
        cached = await preview_cache.get_with_hash_check("fix-123", fix_hash)
        assert cached is not None
        assert cached.success is True

    @pytest.mark.asyncio
    async def test_get_with_hash_check_invalid(self, preview_cache):
        """Test hash validation fails when hash changes."""
        from repotoire.api.models import PreviewResult

        old_hash = "abc123def456"
        new_hash = "xyz789abc123"
        result = PreviewResult(
            success=True,
            duration_ms=100,
            cached_at=f"2025-01-01T00:00:00Z:{old_hash}",
        )
        await preview_cache.set_preview("fix-123", result)

        # Different hash should return None and invalidate
        cached = await preview_cache.get_with_hash_check("fix-123", new_hash)
        assert cached is None

        # Cache should be invalidated
        assert await preview_cache.get_preview("fix-123") is None

    @pytest.mark.asyncio
    async def test_metrics_tracking(self, preview_cache):
        """Test that metrics are tracked correctly."""
        from repotoire.api.models import PreviewResult

        result = PreviewResult(success=True, duration_ms=100)

        # Miss
        await preview_cache.get_preview("nonexistent")
        assert preview_cache.metrics.misses == 1

        # Set and hit
        await preview_cache.set_preview("fix-123", result)
        await preview_cache.get_preview("fix-123")
        assert preview_cache.metrics.total_hits == 1


# =============================================================================
# Scan Cache Tests
# =============================================================================


class TestScanCache:
    """Tests for ScanCache."""

    @pytest.mark.asyncio
    async def test_hash_content(self, scan_cache):
        """Test content hashing is deterministic."""
        content = "def secret(): pass"
        hash1 = scan_cache.hash_content(content)
        hash2 = scan_cache.hash_content(content)
        assert hash1 == hash2
        assert len(hash1) == 32  # MD5 hex digest

    @pytest.mark.asyncio
    async def test_set_and_get_by_content(self, scan_cache):
        """Test caching scan results by content."""
        content = "API_KEY = 'sk-secret123'"

        # Cache scan result
        success = await scan_cache.set_by_content(
            content=content,
            has_secrets=True,
            total_secrets=1,
            by_risk_level={"high": 1},
            by_type={"OpenAI API Key": 1},
        )
        assert success is True

        # Get by same content
        cached = await scan_cache.get_by_content(content)
        assert cached is not None
        assert cached.has_secrets is True
        assert cached.total_secrets == 1
        assert cached.by_risk_level == {"high": 1}

    @pytest.mark.asyncio
    async def test_different_content_different_keys(self, scan_cache):
        """Test that different content gets different cache entries."""
        content1 = "def foo(): pass"
        content2 = "def bar(): pass"

        await scan_cache.set_by_content(
            content=content1,
            has_secrets=False,
            total_secrets=0,
        )
        await scan_cache.set_by_content(
            content=content2,
            has_secrets=True,
            total_secrets=1,
        )

        cached1 = await scan_cache.get_by_content(content1)
        cached2 = await scan_cache.get_by_content(content2)

        assert cached1.has_secrets is False
        assert cached2.has_secrets is True

    @pytest.mark.asyncio
    async def test_content_change_invalidates(self, scan_cache):
        """Test that modified content doesn't hit cache."""
        content_v1 = "def foo(): pass"
        content_v2 = "def foo(): pass  # modified"

        await scan_cache.set_by_content(
            content=content_v1,
            has_secrets=False,
            total_secrets=0,
        )

        # Same content hits cache
        assert await scan_cache.get_by_content(content_v1) is not None

        # Modified content is a miss
        assert await scan_cache.get_by_content(content_v2) is None

    @pytest.mark.asyncio
    async def test_set_with_cached_secret_matches(self, scan_cache):
        """Test caching with secret match details."""
        from repotoire.cache import CachedSecretMatch

        content = "token = 'secret'"
        matches = [
            CachedSecretMatch(
                secret_type="High Entropy String",
                line_number=1,
                risk_level="low",
                remediation="Review this string",
            )
        ]

        success = await scan_cache.set_by_content(
            content=content,
            has_secrets=True,
            total_secrets=1,
            matches=matches,
        )
        assert success is True

        cached = await scan_cache.get_by_content(content)
        assert len(cached.matches) == 1
        assert cached.matches[0].secret_type == "High Entropy String"


# =============================================================================
# Skill Cache Tests
# =============================================================================


class TestTwoTierSkillCache:
    """Tests for TwoTierSkillCache."""

    @pytest.mark.asyncio
    async def test_set_and_get_skill(self, skill_cache):
        """Test basic set and get operations."""
        from repotoire.cache import CachedSkill

        skill = CachedSkill(
            skill_id="test_skill",
            skill_name="Test Skill",
            skill_code="def test(): pass",
            code_hash="abc123",
            loaded_at="2025-01-01T00:00:00Z",
        )

        # Set skill
        success = await skill_cache.set("test_skill", skill)
        assert success is True

        # Get skill - should hit L1 (local)
        cached = await skill_cache.get("test_skill")
        assert cached is not None
        assert cached.skill_name == "Test Skill"
        assert skill_cache.metrics.hits["local"] == 1

    @pytest.mark.asyncio
    async def test_l1_to_l2_promotion(self, skill_cache):
        """Test that L2 hits get promoted to L1."""
        from repotoire.cache import CachedSkill

        skill = CachedSkill(
            skill_id="test_skill",
            skill_name="Test Skill",
            skill_code="def test(): pass",
            code_hash="abc123",
            loaded_at="2025-01-01T00:00:00Z",
        )

        # Set skill (populates both L1 and L2)
        await skill_cache.set("test_skill", skill)

        # Clear L1 only
        skill_cache.invalidate_local("test_skill")
        assert skill_cache.get_local("test_skill") is None

        # Get should hit L2 and promote to L1
        cached = await skill_cache.get("test_skill")
        assert cached is not None
        assert skill_cache.metrics.hits["redis"] == 1

        # Now L1 should have it
        assert skill_cache.get_local("test_skill") is not None

    @pytest.mark.asyncio
    async def test_invalidate_local(self, skill_cache):
        """Test L1 cache invalidation."""
        from repotoire.cache import CachedSkill

        skill = CachedSkill(
            skill_id="test_skill",
            skill_name="Test Skill",
            skill_code="def test(): pass",
            code_hash="abc123",
            loaded_at="2025-01-01T00:00:00Z",
        )

        await skill_cache.set("test_skill", skill)

        # Invalidate L1 only
        count = skill_cache.invalidate_local("test_skill")
        assert count == 1
        assert skill_cache.get_local("test_skill") is None

        # L2 should still have it
        cached = await skill_cache.get("test_skill")
        assert cached is not None

    @pytest.mark.asyncio
    async def test_invalidate_both_tiers(self, skill_cache):
        """Test full invalidation of both L1 and L2."""
        from repotoire.cache import CachedSkill

        skill = CachedSkill(
            skill_id="test_skill",
            skill_name="Test Skill",
            skill_code="def test(): pass",
            code_hash="abc123",
            loaded_at="2025-01-01T00:00:00Z",
        )

        await skill_cache.set("test_skill", skill)

        # Invalidate both
        success = await skill_cache.invalidate("test_skill")
        assert success is True

        # Both should be gone
        assert skill_cache.get_local("test_skill") is None
        cached = await skill_cache.get("test_skill")
        assert cached is None

    @pytest.mark.asyncio
    async def test_local_size(self, skill_cache):
        """Test local cache size tracking."""
        from repotoire.cache import CachedSkill

        assert skill_cache.local_size == 0

        for i in range(3):
            skill = CachedSkill(
                skill_id=f"skill_{i}",
                skill_name=f"Skill {i}",
                skill_code=f"def skill_{i}(): pass",
                code_hash=f"hash{i}",
                loaded_at="2025-01-01T00:00:00Z",
            )
            await skill_cache.set(f"skill_{i}", skill)

        assert skill_cache.local_size == 3

    @pytest.mark.asyncio
    async def test_get_stats(self, skill_cache):
        """Test stats reporting."""
        from repotoire.cache import CachedSkill

        skill = CachedSkill(
            skill_id="test_skill",
            skill_name="Test Skill",
            skill_code="def test(): pass",
            code_hash="abc123",
            loaded_at="2025-01-01T00:00:00Z",
        )

        await skill_cache.set("test_skill", skill)
        await skill_cache.get("test_skill")
        await skill_cache.get("nonexistent")

        stats = skill_cache.get_stats()
        assert stats["local_size"] == 1
        assert stats["has_redis"] is True
        assert stats["total_hits"] == 1
        assert stats["misses"] == 1


# =============================================================================
# Graceful Degradation Tests
# =============================================================================


class TestGracefulDegradation:
    """Tests for graceful degradation without Redis."""

    @pytest.mark.asyncio
    async def test_preview_cache_without_redis(self):
        """Test PreviewCache works without Redis."""
        from repotoire.cache import PreviewCache
        from repotoire.api.models import PreviewResult

        cache = PreviewCache(None)  # No Redis

        result = PreviewResult(success=True, duration_ms=100)

        # Set returns False but doesn't crash
        success = await cache.set_preview("fix-123", result)
        assert success is False

        # Get returns None
        cached = await cache.get_preview("fix-123")
        assert cached is None

    @pytest.mark.asyncio
    async def test_scan_cache_without_redis(self):
        """Test ScanCache works without Redis."""
        from repotoire.cache import ScanCache

        cache = ScanCache(None)  # No Redis

        success = await cache.set_by_content(
            content="test",
            has_secrets=False,
            total_secrets=0,
        )
        assert success is False

        cached = await cache.get_by_content("test")
        assert cached is None

    @pytest.mark.asyncio
    async def test_skill_cache_l1_only(self):
        """Test TwoTierSkillCache works with L1 only."""
        from repotoire.cache import TwoTierSkillCache, CachedSkill

        cache = TwoTierSkillCache(None)  # No Redis

        skill = CachedSkill(
            skill_id="test_skill",
            skill_name="Test Skill",
            skill_code="def test(): pass",
            code_hash="abc123",
            loaded_at="2025-01-01T00:00:00Z",
        )

        # Should work with L1 only - set returns True since L1 succeeds
        # (behavior when no Redis is that L1 is used)
        success = await cache.set("test_skill", skill)
        # When redis is None, set returns True (L1 success is what matters)
        assert success is True  # L1 succeeded (no L2 to fail)

        # L1 should work
        cached = cache.get_local("test_skill")
        assert cached is not None
        assert cached.skill_name == "Test Skill"


# =============================================================================
# Cache Manager Tests
# =============================================================================


class TestCacheManager:
    """Tests for CacheManager."""

    def test_cache_manager_initialization(self, fake_redis):
        """Test CacheManager initializes all caches."""
        from repotoire.cache import CacheManager

        manager = CacheManager(fake_redis)

        assert manager.preview is not None
        assert manager.scan is not None
        assert manager.skill is not None

    def test_get_metrics(self, fake_redis):
        """Test getting metrics from all caches."""
        from repotoire.cache import CacheManager

        manager = CacheManager(fake_redis)

        # Record some activity
        manager.preview.metrics.record_hit()
        manager.scan.metrics.record_miss()
        manager.skill.metrics.record_error("connection", Exception("test"))

        metrics = manager.get_metrics()

        assert "preview" in metrics
        assert "scan" in metrics
        assert "skill" in metrics
        assert metrics["preview"]["total_hits"] == 1
        assert metrics["scan"]["misses"] == 1
        assert metrics["skill"]["total_errors"] == 1

    def test_get_summary(self, fake_redis):
        """Test getting summary of cache health."""
        from repotoire.cache import CacheManager

        manager = CacheManager(fake_redis)

        manager.preview.metrics.record_hit()
        manager.preview.metrics.record_miss()

        summary = manager.get_summary()

        assert summary["redis_connected"] is True
        assert summary["total_hits"] == 1
        assert summary["total_misses"] == 1
        assert "preview" in summary["caches"]
