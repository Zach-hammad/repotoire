"""Unit tests for sandbox CLI commands."""

import pytest
from click.testing import CliRunner
from unittest.mock import patch, AsyncMock, MagicMock

from repotoire.cli.sandbox import (
    sandbox_stats,
    format_cost,
    format_duration,
    format_percentage,
)


class TestFormatHelpers:
    """Tests for CLI formatting helper functions."""

    def test_format_cost_small(self):
        """Format very small costs."""
        assert format_cost(0.001234) == "$0.001234"
        assert format_cost(0.000001) == "$0.000001"

    def test_format_cost_medium(self):
        """Format medium costs."""
        assert format_cost(0.0123) == "$0.0123"
        assert format_cost(0.5) == "$0.5000"

    def test_format_cost_large(self):
        """Format larger costs."""
        assert format_cost(1.23) == "$1.23"
        assert format_cost(99.99) == "$99.99"
        assert format_cost(1234.56) == "$1234.56"

    def test_format_duration_milliseconds(self):
        """Format durations under 1 second."""
        assert format_duration(500) == "500ms"
        assert format_duration(999) == "999ms"

    def test_format_duration_seconds(self):
        """Format durations under 1 minute."""
        assert format_duration(1000) == "1.0s"
        assert format_duration(2500) == "2.5s"
        assert format_duration(59999) == "60.0s"

    def test_format_duration_minutes(self):
        """Format durations over 1 minute."""
        assert format_duration(60000) == "1.0m"
        assert format_duration(90000) == "1.5m"
        assert format_duration(300000) == "5.0m"

    def test_format_percentage(self):
        """Format percentages."""
        assert format_percentage(95.5) == "95.5%"
        assert format_percentage(100.0) == "100.0%"
        assert format_percentage(0.0) == "0.0%"


class TestSandboxStatsCLI:
    """Tests for sandbox-stats CLI command."""

    def test_help_option(self):
        """--help displays usage information."""
        runner = CliRunner()
        result = runner.invoke(sandbox_stats, ["--help"])

        assert result.exit_code == 0
        assert "sandbox-stats" in result.output or "sandbox_stats" in result.output
        assert "--period" in result.output
        assert "--by-type" in result.output
        assert "--slow" in result.output
        assert "--failures" in result.output

    def test_default_invocation_no_db(self):
        """Default invocation without database shows error."""
        runner = CliRunner()

        with patch("repotoire.cli.sandbox.SandboxMetricsCollector") as MockCollector:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock(side_effect=Exception("Connection failed"))
            MockCollector.return_value = mock_instance

            result = runner.invoke(sandbox_stats, [])

            assert "Failed to connect" in result.output or result.exit_code != 0

    def test_with_period_option(self):
        """--period option sets the lookback period."""
        runner = CliRunner()

        with patch("repotoire.cli.sandbox.SandboxMetricsCollector") as MockCollector:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock()
            mock_instance.close = AsyncMock()
            mock_instance.get_cost_summary = AsyncMock(return_value={
                "total_operations": 100,
                "successful_operations": 95,
                "success_rate": 95.0,
                "total_cost_usd": 1.50,
                "avg_duration_ms": 2000,
                "total_cpu_seconds": 100,
                "total_memory_gb_seconds": 50,
            })
            MockCollector.return_value = mock_instance

            result = runner.invoke(sandbox_stats, ["--period", "7"])

            assert result.exit_code == 0
            # Check that summary was displayed
            assert "100" in result.output or "operations" in result.output.lower()

    def test_with_customer_id_option(self):
        """--customer-id option filters by customer."""
        runner = CliRunner()

        with patch("repotoire.cli.sandbox.SandboxMetricsCollector") as MockCollector:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock()
            mock_instance.close = AsyncMock()
            mock_instance.get_cost_summary = AsyncMock(return_value={
                "total_operations": 50,
                "successful_operations": 48,
                "success_rate": 96.0,
                "total_cost_usd": 0.75,
                "avg_duration_ms": 1500,
                "total_cpu_seconds": 50,
                "total_memory_gb_seconds": 25,
            })
            MockCollector.return_value = mock_instance

            result = runner.invoke(sandbox_stats, ["--customer-id", "cust_123"])

            assert result.exit_code == 0
            mock_instance.get_cost_summary.assert_called_once()
            # Verify customer_id was passed
            call_kwargs = mock_instance.get_cost_summary.call_args[1]
            assert call_kwargs.get("customer_id") == "cust_123"

    def test_with_by_type_flag(self):
        """--by-type flag shows breakdown by operation type."""
        runner = CliRunner()

        with patch("repotoire.cli.sandbox.SandboxMetricsCollector") as MockCollector:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock()
            mock_instance.close = AsyncMock()
            mock_instance.get_cost_summary = AsyncMock(return_value={
                "total_operations": 100,
                "successful_operations": 95,
                "success_rate": 95.0,
                "total_cost_usd": 1.50,
                "avg_duration_ms": 2000,
                "total_cpu_seconds": 100,
                "total_memory_gb_seconds": 50,
            })
            mock_instance.get_cost_by_operation_type = AsyncMock(return_value=[
                {
                    "operation_type": "test_execution",
                    "count": 60,
                    "total_cost_usd": 1.00,
                    "percentage": 66.7,
                    "avg_duration_ms": 2500,
                    "success_rate": 95.0,
                },
                {
                    "operation_type": "skill_run",
                    "count": 40,
                    "total_cost_usd": 0.50,
                    "percentage": 33.3,
                    "avg_duration_ms": 1500,
                    "success_rate": 97.5,
                },
            ])
            MockCollector.return_value = mock_instance

            result = runner.invoke(sandbox_stats, ["--by-type"])

            assert result.exit_code == 0
            mock_instance.get_cost_by_operation_type.assert_called_once()
            # Output should mention operation types
            assert "test_execution" in result.output or "Operation Type" in result.output

    def test_with_slow_flag(self):
        """--slow flag shows slow operations."""
        runner = CliRunner()

        with patch("repotoire.cli.sandbox.SandboxMetricsCollector") as MockCollector:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock()
            mock_instance.close = AsyncMock()
            mock_instance.get_cost_summary = AsyncMock(return_value={
                "total_operations": 100,
                "successful_operations": 95,
                "success_rate": 95.0,
                "total_cost_usd": 1.50,
                "avg_duration_ms": 2000,
                "total_cpu_seconds": 100,
                "total_memory_gb_seconds": 50,
            })
            mock_instance.get_slow_operations = AsyncMock(return_value=[
                {
                    "time": "2024-01-15T10:30:00",
                    "operation_id": "op-123",
                    "operation_type": "test_execution",
                    "duration_ms": 45000,
                    "cost_usd": 0.05,
                    "success": True,
                    "customer_id": "cust_abc",
                    "sandbox_id": "sandbox-xyz",
                },
            ])
            MockCollector.return_value = mock_instance

            result = runner.invoke(sandbox_stats, ["--slow"])

            assert result.exit_code == 0
            mock_instance.get_slow_operations.assert_called_once()

    def test_with_failures_flag(self):
        """--failures flag shows recent failures."""
        runner = CliRunner()

        with patch("repotoire.cli.sandbox.SandboxMetricsCollector") as MockCollector:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock()
            mock_instance.close = AsyncMock()
            mock_instance.get_cost_summary = AsyncMock(return_value={
                "total_operations": 100,
                "successful_operations": 95,
                "success_rate": 95.0,
                "total_cost_usd": 1.50,
                "avg_duration_ms": 2000,
                "total_cpu_seconds": 100,
                "total_memory_gb_seconds": 50,
            })
            mock_instance.get_recent_failures = AsyncMock(return_value=[
                {
                    "time": "2024-01-15T10:30:00",
                    "operation_id": "op-456",
                    "operation_type": "skill_run",
                    "error_message": "Timeout after 300s",
                    "duration_ms": 300000,
                    "customer_id": "cust_def",
                    "sandbox_id": "sandbox-uvw",
                },
            ])
            MockCollector.return_value = mock_instance

            result = runner.invoke(sandbox_stats, ["--failures"])

            assert result.exit_code == 0
            mock_instance.get_recent_failures.assert_called_once()

    def test_with_top_customers_option(self):
        """--top-customers option shows top N customers."""
        runner = CliRunner()

        with patch("repotoire.cli.sandbox.SandboxMetricsCollector") as MockCollector:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock()
            mock_instance.close = AsyncMock()
            mock_instance.get_cost_summary = AsyncMock(return_value={
                "total_operations": 100,
                "successful_operations": 95,
                "success_rate": 95.0,
                "total_cost_usd": 1.50,
                "avg_duration_ms": 2000,
                "total_cpu_seconds": 100,
                "total_memory_gb_seconds": 50,
            })
            mock_instance.get_cost_by_customer = AsyncMock(return_value=[
                {
                    "customer_id": "cust_top",
                    "total_operations": 500,
                    "total_cost_usd": 5.00,
                    "avg_duration_ms": 3000,
                    "success_rate": 94.0,
                },
            ])
            MockCollector.return_value = mock_instance

            result = runner.invoke(sandbox_stats, ["--top-customers", "5"])

            assert result.exit_code == 0
            mock_instance.get_cost_by_customer.assert_called_once()
            call_kwargs = mock_instance.get_cost_by_customer.call_args[1]
            assert call_kwargs.get("limit") == 5

    def test_json_output_flag(self):
        """--json-output flag outputs JSON format."""
        runner = CliRunner()

        with patch("repotoire.cli.sandbox.SandboxMetricsCollector") as MockCollector:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock()
            mock_instance.close = AsyncMock()
            mock_instance.get_cost_summary = AsyncMock(return_value={
                "total_operations": 100,
                "successful_operations": 95,
                "success_rate": 95.0,
                "total_cost_usd": 1.50,
                "avg_duration_ms": 2000,
                "total_cpu_seconds": 100,
                "total_memory_gb_seconds": 50,
            })
            MockCollector.return_value = mock_instance

            result = runner.invoke(sandbox_stats, ["--json-output"])

            assert result.exit_code == 0
            # Output should be valid JSON
            import json
            try:
                parsed = json.loads(result.output.strip())
                assert "total_operations" in parsed
            except json.JSONDecodeError:
                pytest.fail("Output is not valid JSON")

    def test_empty_results_handling(self):
        """Handle empty results gracefully."""
        runner = CliRunner()

        with patch("repotoire.cli.sandbox.SandboxMetricsCollector") as MockCollector:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock()
            mock_instance.close = AsyncMock()
            mock_instance.get_cost_summary = AsyncMock(return_value={
                "total_operations": 0,
                "successful_operations": 0,
                "success_rate": 0.0,
                "total_cost_usd": 0.0,
                "avg_duration_ms": 0.0,
                "total_cpu_seconds": 0.0,
                "total_memory_gb_seconds": 0.0,
            })
            mock_instance.get_cost_by_operation_type = AsyncMock(return_value=[])
            MockCollector.return_value = mock_instance

            result = runner.invoke(sandbox_stats, ["--by-type"])

            assert result.exit_code == 0
            # Should show "No operations" or similar message
            assert "0" in result.output or "No" in result.output


class TestSandboxStatsIntegration:
    """Integration-style tests for sandbox-stats command."""

    def test_all_flags_combined(self):
        """Test with multiple flags at once."""
        runner = CliRunner()

        with patch("repotoire.cli.sandbox.SandboxMetricsCollector") as MockCollector:
            mock_instance = MagicMock()
            mock_instance.connect = AsyncMock()
            mock_instance.close = AsyncMock()
            mock_instance.get_cost_summary = AsyncMock(return_value={
                "total_operations": 1000,
                "successful_operations": 950,
                "success_rate": 95.0,
                "total_cost_usd": 15.00,
                "avg_duration_ms": 2500,
                "total_cpu_seconds": 1000,
                "total_memory_gb_seconds": 500,
            })
            mock_instance.get_cost_by_operation_type = AsyncMock(return_value=[
                {
                    "operation_type": "test_execution",
                    "count": 600,
                    "total_cost_usd": 10.00,
                    "percentage": 66.7,
                    "avg_duration_ms": 3000,
                    "success_rate": 94.0,
                },
            ])
            mock_instance.get_slow_operations = AsyncMock(return_value=[])
            mock_instance.get_recent_failures = AsyncMock(return_value=[])
            MockCollector.return_value = mock_instance

            result = runner.invoke(sandbox_stats, [
                "--period", "7",
                "--by-type",
                "--slow",
                "--failures",
            ])

            assert result.exit_code == 0
            # All methods should have been called
            mock_instance.get_cost_summary.assert_called_once()
            mock_instance.get_cost_by_operation_type.assert_called_once()
            mock_instance.get_slow_operations.assert_called_once()
            mock_instance.get_recent_failures.assert_called_once()
