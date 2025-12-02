"""Unit tests for sandbox metrics and cost tracking."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio

from repotoire.sandbox.metrics import (
    SandboxMetrics,
    SandboxMetricsCollector,
    calculate_cost,
    track_sandbox_operation,
    get_metrics_collector,
    CPU_RATE_PER_SECOND,
    MEMORY_RATE_PER_GB_SECOND,
    MINIMUM_CHARGE,
)


class TestCalculateCost:
    """Tests for cost calculation function."""

    def test_basic_cost_calculation(self):
        """Calculate cost for standard operation."""
        # 60 seconds with 2 CPUs and 2GB RAM
        cost = calculate_cost(60, cpu_count=2, memory_gb=2.0)

        # Expected: (60 * 2 * 0.000014) + (60 * 2 * 0.0000025)
        # = 0.00168 + 0.0003 = 0.00198
        expected = 0.00198
        assert abs(cost - expected) < 0.0001

    def test_minimum_charge_applied(self):
        """Very short operations get minimum charge."""
        # 1 second with 1 CPU and 1GB
        cost = calculate_cost(1, cpu_count=1, memory_gb=1.0)

        # Actual cost would be ~0.0000165, but minimum is 0.001
        assert cost == MINIMUM_CHARGE

    def test_zero_duration(self):
        """Zero duration gets minimum charge."""
        cost = calculate_cost(0, cpu_count=2, memory_gb=2.0)
        assert cost == MINIMUM_CHARGE

    def test_high_resource_cost(self):
        """High resource usage calculates correctly."""
        # 300 seconds (5 min) with 4 CPUs and 4GB RAM
        cost = calculate_cost(300, cpu_count=4, memory_gb=4.0)

        # CPU: 300 * 4 * 0.000014 = 0.0168
        # Memory: 300 * 4 * 0.0000025 = 0.003
        # Total: 0.0198
        expected = 0.0198
        assert abs(cost - expected) < 0.0001

    def test_cost_precision(self):
        """Cost is rounded to 6 decimal places."""
        cost = calculate_cost(100, cpu_count=3, memory_gb=1.5)
        # Ensure we don't have floating point weirdness
        assert len(str(cost).split('.')[-1]) <= 6


class TestSandboxMetrics:
    """Tests for SandboxMetrics dataclass."""

    def test_creation_with_defaults(self):
        """Create metrics with minimal required fields."""
        metrics = SandboxMetrics(
            operation_id="test-123",
            operation_type="test_execution",
        )

        assert metrics.operation_id == "test-123"
        assert metrics.operation_type == "test_execution"
        assert metrics.sandbox_id is None
        assert metrics.success is False
        assert metrics.exit_code == -1
        assert metrics.cost_usd == 0.0
        assert isinstance(metrics.started_at, datetime)

    def test_creation_with_all_fields(self):
        """Create metrics with all fields populated."""
        now = datetime.now(timezone.utc)
        metrics = SandboxMetrics(
            operation_id="test-456",
            operation_type="skill_run",
            sandbox_id="sandbox-abc",
            started_at=now,
            completed_at=now + timedelta(seconds=30),
            duration_ms=30000,
            cpu_seconds=60.0,
            memory_gb_seconds=30.0,
            cost_usd=0.001,
            success=True,
            exit_code=0,
            error_message=None,
            customer_id="cust_123",
            project_id="proj_456",
            repository_id="repo_789",
            tier="PRO",
            template="repotoire-enterprise",
        )

        assert metrics.sandbox_id == "sandbox-abc"
        assert metrics.success is True
        assert metrics.customer_id == "cust_123"
        assert metrics.tier == "PRO"

    def test_to_dict_serialization(self):
        """Convert metrics to dictionary."""
        metrics = SandboxMetrics(
            operation_id="test-789",
            operation_type="code_validation",
            customer_id="cust_abc",
        )

        d = metrics.to_dict()

        assert d["operation_id"] == "test-789"
        assert d["operation_type"] == "code_validation"
        assert d["customer_id"] == "cust_abc"
        assert "started_at" in d
        assert isinstance(d["started_at"], str)  # ISO format

    def test_to_dict_with_none_timestamps(self):
        """Handle None timestamps in serialization."""
        metrics = SandboxMetrics(
            operation_id="test-none",
            operation_type="tool_run",
        )
        metrics.completed_at = None

        d = metrics.to_dict()
        assert d["completed_at"] is None


class TestSandboxMetricsCollector:
    """Tests for SandboxMetricsCollector."""

    def test_initialization_without_connection_string(self):
        """Collector initializes without connection string."""
        with patch.dict("os.environ", {}, clear=True):
            collector = SandboxMetricsCollector()
            assert collector.connection_string is None
            assert collector._connected is False

    def test_initialization_with_connection_string(self):
        """Collector initializes with provided connection string."""
        collector = SandboxMetricsCollector(
            connection_string="postgresql://test:test@localhost/test"
        )
        assert "postgresql" in collector.connection_string

    def test_initialization_from_env(self):
        """Collector reads from environment variable."""
        with patch.dict("os.environ", {"REPOTOIRE_TIMESCALE_URI": "postgresql://env:env@localhost/env"}):
            collector = SandboxMetricsCollector()
            assert "env" in collector.connection_string

    @pytest.mark.asyncio
    async def test_connect_without_connection_string(self):
        """Connect logs warning when no connection string."""
        collector = SandboxMetricsCollector(connection_string=None)
        await collector.connect()
        # Should not raise, just warn
        assert collector._connected is False

    def test_connect_requires_psycopg2(self):
        """Document that psycopg2 is required for connection.

        Note: This is a documentation test - the actual ImportError is raised
        at module import time when psycopg2 is not installed.
        """
        # The SandboxMetricsCollector.connect() method has an import check
        # that raises ImportError with a helpful message
        collector = SandboxMetricsCollector(
            connection_string="postgresql://test:test@localhost/test"
        )
        # Just verify the collector was created - actual psycopg2 test
        # would require uninstalling the package
        assert collector.connection_string is not None

    @pytest.mark.asyncio
    async def test_record_when_not_connected(self):
        """Record is a no-op when not connected."""
        collector = SandboxMetricsCollector(connection_string=None)

        metrics = SandboxMetrics(
            operation_id="test-record",
            operation_type="test_execution",
        )

        # Should not raise
        await collector.record(metrics)

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Collector works as async context manager."""
        collector = SandboxMetricsCollector(connection_string=None)

        async with collector as c:
            assert c is collector

    @pytest.mark.asyncio
    async def test_get_cost_summary_not_connected(self):
        """get_cost_summary returns error when not connected."""
        collector = SandboxMetricsCollector(connection_string=None)

        result = await collector.get_cost_summary()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_cost_by_operation_type_not_connected(self):
        """get_cost_by_operation_type returns empty when not connected."""
        collector = SandboxMetricsCollector(connection_string=None)

        result = await collector.get_cost_by_operation_type()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_cost_by_customer_not_connected(self):
        """get_cost_by_customer returns empty when not connected."""
        collector = SandboxMetricsCollector(connection_string=None)

        result = await collector.get_cost_by_customer()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_slow_operations_not_connected(self):
        """get_slow_operations returns empty when not connected."""
        collector = SandboxMetricsCollector(connection_string=None)

        result = await collector.get_slow_operations()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_recent_failures_not_connected(self):
        """get_recent_failures returns empty when not connected."""
        collector = SandboxMetricsCollector(connection_string=None)

        result = await collector.get_recent_failures()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_failure_rate_not_connected(self):
        """get_failure_rate returns error when not connected."""
        collector = SandboxMetricsCollector(connection_string=None)

        result = await collector.get_failure_rate()
        assert "error" in result


class TestTrackSandboxOperation:
    """Tests for track_sandbox_operation context manager."""

    @pytest.mark.asyncio
    async def test_basic_tracking(self):
        """Track a simple successful operation."""
        collector = SandboxMetricsCollector(connection_string=None)

        async with track_sandbox_operation(
            operation_type="test_execution",
            collector=collector,
        ) as metrics:
            # Simulate work
            await asyncio.sleep(0.01)
            metrics.exit_code = 0
            metrics.success = True

        assert metrics.operation_type == "test_execution"
        assert metrics.success is True
        assert metrics.exit_code == 0
        assert metrics.duration_ms >= 10
        assert metrics.cost_usd >= MINIMUM_CHARGE
        assert metrics.completed_at is not None

    @pytest.mark.asyncio
    async def test_tracking_with_context(self):
        """Track operation with full context."""
        collector = SandboxMetricsCollector(connection_string=None)

        async with track_sandbox_operation(
            operation_type="skill_run",
            sandbox_id="sandbox-test",
            cpu_count=2,
            memory_mb=2048,
            customer_id="cust_123",
            project_id="proj_456",
            repository_id="repo_789",
            tier="PRO",
            template="repotoire-enterprise",
            collector=collector,
        ) as metrics:
            pass

        assert metrics.sandbox_id == "sandbox-test"
        assert metrics.customer_id == "cust_123"
        assert metrics.project_id == "proj_456"
        assert metrics.repository_id == "repo_789"
        assert metrics.tier == "PRO"
        assert metrics.template == "repotoire-enterprise"

    @pytest.mark.asyncio
    async def test_tracking_calculates_resources(self):
        """Track operation calculates CPU and memory usage."""
        collector = SandboxMetricsCollector(connection_string=None)

        async with track_sandbox_operation(
            operation_type="test_execution",
            cpu_count=2,
            memory_mb=2048,
            collector=collector,
        ) as metrics:
            await asyncio.sleep(0.05)  # 50ms

        # Should have calculated CPU seconds and memory GB-seconds
        assert metrics.cpu_seconds > 0
        assert metrics.memory_gb_seconds > 0
        # 2 CPUs for ~50ms = ~0.1 CPU-seconds
        assert metrics.cpu_seconds >= 0.05

    @pytest.mark.asyncio
    async def test_tracking_handles_exception(self):
        """Track operation handles exceptions gracefully."""
        collector = SandboxMetricsCollector(connection_string=None)

        with pytest.raises(ValueError):
            async with track_sandbox_operation(
                operation_type="test_execution",
                collector=collector,
            ) as metrics:
                raise ValueError("Test error")

        assert metrics.success is False
        assert metrics.error_message is not None
        assert "Test error" in metrics.error_message
        assert metrics.completed_at is not None

    @pytest.mark.asyncio
    async def test_tracking_truncates_long_errors(self):
        """Track operation truncates very long error messages."""
        collector = SandboxMetricsCollector(connection_string=None)

        long_error = "x" * 1000  # 1000 char error

        with pytest.raises(ValueError):
            async with track_sandbox_operation(
                operation_type="test_execution",
                collector=collector,
            ) as metrics:
                raise ValueError(long_error)

        # Should be truncated to 500 chars
        assert len(metrics.error_message) <= 500

    @pytest.mark.asyncio
    async def test_tracking_default_success(self):
        """Track operation defaults to success if no exit code set."""
        collector = SandboxMetricsCollector(connection_string=None)

        async with track_sandbox_operation(
            operation_type="test_execution",
            collector=collector,
        ) as metrics:
            # Don't set exit_code or success
            pass

        # Should default to success
        assert metrics.success is True
        assert metrics.exit_code == 0

    @pytest.mark.asyncio
    async def test_tracking_generates_operation_id(self):
        """Track operation generates unique operation ID."""
        collector = SandboxMetricsCollector(connection_string=None)

        ids = []
        for _ in range(3):
            async with track_sandbox_operation(
                operation_type="test_execution",
                collector=collector,
            ) as metrics:
                ids.append(metrics.operation_id)

        # All IDs should be unique
        assert len(set(ids)) == 3


class TestGetMetricsCollector:
    """Tests for global metrics collector."""

    def test_returns_collector(self):
        """get_metrics_collector returns a collector instance."""
        # Reset global state first
        import repotoire.sandbox.metrics as metrics_module
        metrics_module._global_collector = None

        collector = get_metrics_collector()
        # Import fresh to ensure correct type check
        from repotoire.sandbox.metrics import SandboxMetricsCollector as FreshCollector
        assert isinstance(collector, FreshCollector)

    def test_returns_same_instance(self):
        """get_metrics_collector returns same instance (singleton)."""
        # Reset global state
        import repotoire.sandbox.metrics as metrics_module
        metrics_module._global_collector = None

        c1 = get_metrics_collector()
        c2 = get_metrics_collector()

        assert c1 is c2


class TestE2BPricing:
    """Tests for E2B pricing constants."""

    def test_cpu_rate(self):
        """CPU rate is correct."""
        assert CPU_RATE_PER_SECOND == 0.000014

    def test_memory_rate(self):
        """Memory rate is correct."""
        assert MEMORY_RATE_PER_GB_SECOND == 0.0000025

    def test_minimum_charge(self):
        """Minimum charge is correct."""
        assert MINIMUM_CHARGE == 0.001

    def test_example_calculation_from_docs(self):
        """Example from E2B pricing docs calculates correctly."""
        # Example: 60 seconds with 2 CPUs, 2GB RAM
        # CPU: 60 * 2 * $0.000014 = $0.00168
        # Memory: 60 * 2 * $0.0000025 = $0.0003
        # Total: $0.00198

        cpu_cost = 60 * 2 * CPU_RATE_PER_SECOND
        memory_cost = 60 * 2 * MEMORY_RATE_PER_GB_SECOND
        total = cpu_cost + memory_cost

        assert abs(cpu_cost - 0.00168) < 0.00001
        assert abs(memory_cost - 0.0003) < 0.00001
        assert abs(total - 0.00198) < 0.00001
