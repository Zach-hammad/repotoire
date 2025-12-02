"""Unit tests for sandbox alerting system."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock
import json

from repotoire.sandbox.alerts import (
    AlertEvent,
    AlertManager,
    CostThresholdAlert,
    FailureRateAlert,
    SlowOperationAlert,
    SlackChannel,
    EmailChannel,
    WebhookChannel,
    run_alert_check,
)
from repotoire.sandbox.metrics import SandboxMetricsCollector


class TestAlertEvent:
    """Tests for AlertEvent dataclass."""

    def test_creation_with_minimal_fields(self):
        """Create alert event with required fields only."""
        event = AlertEvent(
            alert_type="cost_threshold",
            severity="warning",
            title="Test Alert",
            message="This is a test",
        )

        assert event.alert_type == "cost_threshold"
        assert event.severity == "warning"
        assert event.title == "Test Alert"
        assert event.message == "This is a test"
        assert event.customer_id is None
        assert isinstance(event.timestamp, datetime)

    def test_creation_with_all_fields(self):
        """Create alert event with all fields."""
        now = datetime.now(timezone.utc)
        event = AlertEvent(
            alert_type="failure_rate",
            severity="critical",
            title="High Failure Rate",
            message="Failure rate exceeded threshold",
            data={"rate": "15%", "threshold": "10%"},
            customer_id="cust_123",
            timestamp=now,
        )

        assert event.data["rate"] == "15%"
        assert event.customer_id == "cust_123"
        assert event.timestamp == now

    def test_to_dict_serialization(self):
        """Convert alert event to dictionary."""
        event = AlertEvent(
            alert_type="slow_operation",
            severity="warning",
            title="Slow Operations",
            message="Found slow operations",
            data={"count": 5},
        )

        d = event.to_dict()

        assert d["alert_type"] == "slow_operation"
        assert d["severity"] == "warning"
        assert d["title"] == "Slow Operations"
        assert d["data"]["count"] == 5
        assert "timestamp" in d
        assert isinstance(d["timestamp"], str)

    def test_to_slack_block_format(self):
        """Convert alert event to Slack block format."""
        event = AlertEvent(
            alert_type="cost_threshold",
            severity="warning",
            title="Cost Alert",
            message="Customer exceeded budget",
            data={"Cost": "$15.00", "Threshold": "$10.00"},
        )

        slack = event.to_slack_block()

        assert "blocks" in slack
        blocks = slack["blocks"]

        # Should have header, section with message, fields, and context
        assert len(blocks) >= 3

        # Header should contain title with emoji
        header = blocks[0]
        assert header["type"] == "header"
        assert "Cost Alert" in header["text"]["text"]

    def test_to_slack_block_severity_emojis(self):
        """Slack blocks use correct emoji for severity."""
        severities = {
            "info": ":information_source:",
            "warning": ":warning:",
            "critical": ":rotating_light:",
        }

        for severity, emoji in severities.items():
            event = AlertEvent(
                alert_type="test",
                severity=severity,
                title="Test",
                message="Test message",
            )
            slack = event.to_slack_block()
            header_text = slack["blocks"][0]["text"]["text"]
            assert emoji in header_text


class TestSlackChannel:
    """Tests for SlackChannel."""

    def test_initialization_without_url(self):
        """Initialize without webhook URL."""
        with patch.dict("os.environ", {}, clear=True):
            channel = SlackChannel()
            assert channel.webhook_url is None

    def test_initialization_from_env(self):
        """Initialize from environment variable."""
        with patch.dict("os.environ", {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}):
            channel = SlackChannel()
            assert channel.webhook_url == "https://hooks.slack.com/test"

    def test_initialization_with_explicit_url(self):
        """Initialize with explicit webhook URL."""
        channel = SlackChannel(webhook_url="https://custom.webhook.url")
        assert channel.webhook_url == "https://custom.webhook.url"

    @pytest.mark.asyncio
    async def test_send_without_url_returns_false(self):
        """Send returns False when no webhook URL configured."""
        channel = SlackChannel(webhook_url=None)
        event = AlertEvent(
            alert_type="test",
            severity="info",
            title="Test",
            message="Test",
        )

        result = await channel.send(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_success(self):
        """Send succeeds with valid webhook."""
        channel = SlackChannel(webhook_url="https://hooks.slack.com/test")
        event = AlertEvent(
            alert_type="test",
            severity="warning",
            title="Test Alert",
            message="Test message",
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response

            result = await channel.send(event)

            assert result is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_failure_returns_false(self):
        """Send returns False on HTTP error."""
        channel = SlackChannel(webhook_url="https://hooks.slack.com/test")
        event = AlertEvent(
            alert_type="test",
            severity="warning",
            title="Test",
            message="Test",
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = Exception("Connection failed")

            result = await channel.send(event)
            assert result is False


class TestEmailChannel:
    """Tests for EmailChannel."""

    def test_initialization_defaults(self):
        """Initialize with default values."""
        channel = EmailChannel()
        assert channel.smtp_host == "localhost"
        assert channel.smtp_port == 587
        assert channel.use_tls is True

    def test_initialization_from_env(self):
        """Initialize from environment variables."""
        env = {
            "SMTP_HOST": "smtp.test.com",
            "SMTP_PORT": "465",
            "SMTP_USER": "user@test.com",
            "SMTP_PASSWORD": "secret",
            "ALERT_FROM_EMAIL": "alerts@test.com",
            "ALERT_TO_EMAILS": "admin@test.com,ops@test.com",
        }

        with patch.dict("os.environ", env):
            channel = EmailChannel()
            assert channel.smtp_host == "smtp.test.com"
            assert channel.smtp_port == 465
            assert channel.smtp_user == "user@test.com"
            assert channel.from_email == "alerts@test.com"
            assert "admin@test.com" in channel.to_emails
            assert "ops@test.com" in channel.to_emails

    @pytest.mark.asyncio
    async def test_send_without_recipients_returns_false(self):
        """Send returns False when no recipients configured."""
        channel = EmailChannel(to_emails=[])
        event = AlertEvent(
            alert_type="test",
            severity="info",
            title="Test",
            message="Test",
        )

        result = await channel.send(event)
        assert result is False


class TestWebhookChannel:
    """Tests for WebhookChannel."""

    def test_initialization(self):
        """Initialize with URL."""
        channel = WebhookChannel(url="https://webhook.example.com")
        assert channel.url == "https://webhook.example.com"
        assert channel.headers == {}

    def test_initialization_with_headers(self):
        """Initialize with custom headers."""
        channel = WebhookChannel(
            url="https://webhook.example.com",
            headers={"Authorization": "Bearer token"},
        )
        assert channel.headers["Authorization"] == "Bearer token"

    def test_initialization_with_transform(self):
        """Initialize with custom transform function."""
        transform = lambda e: {"custom": e.title}
        channel = WebhookChannel(
            url="https://webhook.example.com",
            transform=transform,
        )
        assert channel.transform is transform

    @pytest.mark.asyncio
    async def test_send_success(self):
        """Send succeeds with valid webhook."""
        channel = WebhookChannel(url="https://webhook.example.com")
        event = AlertEvent(
            alert_type="test",
            severity="warning",
            title="Test Alert",
            message="Test message",
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response

            result = await channel.send(event)

            assert result is True
            mock_client.post.assert_called_once()


class TestCostThresholdAlert:
    """Tests for CostThresholdAlert."""

    def test_initialization_defaults(self):
        """Initialize with default values."""
        alert = CostThresholdAlert()
        assert alert.threshold_usd == 10.0
        assert alert.period_hours == 24
        assert alert.customer_ids is None

    def test_initialization_with_values(self):
        """Initialize with custom values."""
        alert = CostThresholdAlert(
            threshold_usd=5.0,
            period_hours=12,
            customer_ids=["cust_1", "cust_2"],
        )
        assert alert.threshold_usd == 5.0
        assert alert.period_hours == 12
        assert "cust_1" in alert.customer_ids

    @pytest.mark.asyncio
    async def test_check_no_customers(self):
        """Check returns empty when no customers exceed threshold."""
        alert = CostThresholdAlert(threshold_usd=10.0)

        collector = MagicMock(spec=SandboxMetricsCollector)
        collector.get_cost_by_customer = AsyncMock(return_value=[
            {"customer_id": "cust_1", "total_cost_usd": 5.0, "total_operations": 100},
        ])

        events = await alert.check(collector)
        assert events == []

    @pytest.mark.asyncio
    async def test_check_customer_exceeds_threshold(self):
        """Check returns alert when customer exceeds threshold."""
        alert = CostThresholdAlert(threshold_usd=10.0)

        collector = MagicMock(spec=SandboxMetricsCollector)
        collector.get_cost_by_customer = AsyncMock(return_value=[
            {"customer_id": "cust_1", "total_cost_usd": 15.0, "total_operations": 500},
        ])

        events = await alert.check(collector)

        assert len(events) == 1
        assert events[0].alert_type == "cost_threshold"
        assert events[0].severity == "warning"
        assert "cust_1" in events[0].title

    @pytest.mark.asyncio
    async def test_check_critical_severity_at_2x_threshold(self):
        """Check returns critical severity at 2x threshold."""
        alert = CostThresholdAlert(threshold_usd=10.0)

        collector = MagicMock(spec=SandboxMetricsCollector)
        collector.get_cost_by_customer = AsyncMock(return_value=[
            {"customer_id": "cust_1", "total_cost_usd": 25.0, "total_operations": 1000},
        ])

        events = await alert.check(collector)

        assert len(events) == 1
        assert events[0].severity == "critical"


class TestFailureRateAlert:
    """Tests for FailureRateAlert."""

    def test_initialization_defaults(self):
        """Initialize with default values."""
        alert = FailureRateAlert()
        assert alert.threshold_percent == 10.0
        assert alert.period_hours == 1
        assert alert.min_operations == 10

    @pytest.mark.asyncio
    async def test_check_below_threshold(self):
        """Check returns empty when failure rate below threshold."""
        alert = FailureRateAlert(threshold_percent=10.0, min_operations=10)

        collector = MagicMock(spec=SandboxMetricsCollector)
        collector.get_failure_rate = AsyncMock(return_value={
            "total_operations": 100,
            "failures": 5,
            "failure_rate": 5.0,
        })

        events = await alert.check(collector)
        assert events == []

    @pytest.mark.asyncio
    async def test_check_below_min_operations(self):
        """Check returns empty when below minimum operations."""
        alert = FailureRateAlert(threshold_percent=10.0, min_operations=10)

        collector = MagicMock(spec=SandboxMetricsCollector)
        collector.get_failure_rate = AsyncMock(return_value={
            "total_operations": 5,
            "failures": 3,
            "failure_rate": 60.0,  # High rate but not enough operations
        })

        events = await alert.check(collector)
        assert events == []

    @pytest.mark.asyncio
    async def test_check_exceeds_threshold(self):
        """Check returns alert when failure rate exceeds threshold."""
        alert = FailureRateAlert(threshold_percent=10.0, min_operations=10)

        collector = MagicMock(spec=SandboxMetricsCollector)
        collector.get_failure_rate = AsyncMock(return_value={
            "total_operations": 100,
            "failures": 15,
            "failure_rate": 15.0,
        })

        events = await alert.check(collector)

        assert len(events) == 1
        assert events[0].alert_type == "failure_rate"
        assert events[0].severity == "warning"
        assert "15.0%" in events[0].message


class TestSlowOperationAlert:
    """Tests for SlowOperationAlert."""

    def test_initialization_defaults(self):
        """Initialize with default values."""
        alert = SlowOperationAlert()
        assert alert.threshold_ms == 30000
        assert alert.check_count == 5

    @pytest.mark.asyncio
    async def test_check_no_slow_operations(self):
        """Check returns empty when no slow operations."""
        alert = SlowOperationAlert(threshold_ms=30000)

        collector = MagicMock(spec=SandboxMetricsCollector)
        collector.get_slow_operations = AsyncMock(return_value=[])

        events = await alert.check(collector)
        assert events == []

    @pytest.mark.asyncio
    async def test_check_slow_operations_found(self):
        """Check returns alerts for slow operations."""
        alert = SlowOperationAlert(threshold_ms=30000)

        collector = MagicMock(spec=SandboxMetricsCollector)
        collector.get_slow_operations = AsyncMock(return_value=[
            {"operation_type": "test_execution", "duration_ms": 45000},
            {"operation_type": "test_execution", "duration_ms": 35000},
            {"operation_type": "skill_run", "duration_ms": 50000},
        ])

        events = await alert.check(collector)

        assert len(events) == 2  # Grouped by operation type
        types = {e.data["Operation Type"] for e in events}
        assert "test_execution" in types
        assert "skill_run" in types


class TestAlertManager:
    """Tests for AlertManager."""

    def test_initialization(self):
        """Initialize alert manager."""
        manager = AlertManager()
        assert manager._alerts == []
        assert manager._channels == []

    def test_add_channel(self):
        """Add notification channel."""
        manager = AlertManager()
        channel = SlackChannel(webhook_url="https://test")

        manager.add_channel(channel)

        assert len(manager._channels) == 1
        assert manager._channels[0] is channel

    def test_register_alert(self):
        """Register alert definition."""
        manager = AlertManager()
        alert = CostThresholdAlert()

        manager.register(alert)

        assert len(manager._alerts) == 1
        assert manager._alerts[0] is alert

    @pytest.mark.asyncio
    async def test_check_all_no_alerts(self):
        """check_all returns empty when no alerts registered."""
        manager = AlertManager()

        events = await manager.check_all()
        assert events == []

    @pytest.mark.asyncio
    async def test_check_all_with_alerts(self):
        """check_all processes all registered alerts."""
        manager = AlertManager()

        # Add mock collector
        collector = MagicMock(spec=SandboxMetricsCollector)
        collector._connected = True
        collector.get_cost_by_customer = AsyncMock(return_value=[
            {"customer_id": "test", "total_cost_usd": 15.0, "total_operations": 100},
        ])
        collector.get_failure_rate = AsyncMock(return_value={
            "total_operations": 100, "failures": 5, "failure_rate": 5.0,
        })
        manager._collector = collector

        # Register alerts
        manager.register(CostThresholdAlert(threshold_usd=10.0))
        manager.register(FailureRateAlert(threshold_percent=10.0))

        events = await manager.check_all()

        # Should have cost alert but not failure rate alert
        assert len(events) == 1
        assert events[0].alert_type == "cost_threshold"

    @pytest.mark.asyncio
    async def test_check_all_sends_to_channels(self):
        """check_all sends events through all channels."""
        manager = AlertManager()

        # Add mock collector
        collector = MagicMock(spec=SandboxMetricsCollector)
        collector._connected = True
        collector.get_cost_by_customer = AsyncMock(return_value=[
            {"customer_id": "test", "total_cost_usd": 15.0, "total_operations": 100},
        ])
        manager._collector = collector

        # Add mock channel
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock(return_value=True)
        manager.add_channel(mock_channel)

        # Register alert
        manager.register(CostThresholdAlert(threshold_usd=10.0))

        events = await manager.check_all()

        # Channel should have received the event
        assert mock_channel.send.called
        mock_channel.send.assert_called_once()

    def test_from_env_creates_manager(self):
        """from_env creates manager with environment configuration."""
        env = {
            "SLACK_WEBHOOK_URL": "https://hooks.slack.com/test",
            "ALERT_COST_THRESHOLD": "5.0",
            "ALERT_FAILURE_RATE_THRESHOLD": "15.0",
            "ALERT_SLOW_OPERATION_MS": "20000",
        }

        with patch.dict("os.environ", env, clear=True):
            manager = AlertManager.from_env()

            # Should have Slack channel
            assert len(manager._channels) == 1
            assert isinstance(manager._channels[0], SlackChannel)

            # Should have 3 default alerts
            assert len(manager._alerts) == 3

            # Check alert configurations
            cost_alert = next(a for a in manager._alerts if isinstance(a, CostThresholdAlert))
            assert cost_alert.threshold_usd == 5.0

            failure_alert = next(a for a in manager._alerts if isinstance(a, FailureRateAlert))
            assert failure_alert.threshold_percent == 15.0

            slow_alert = next(a for a in manager._alerts if isinstance(a, SlowOperationAlert))
            assert slow_alert.threshold_ms == 20000


class TestRunAlertCheck:
    """Tests for run_alert_check convenience function."""

    @pytest.mark.asyncio
    async def test_run_alert_check_returns_events(self):
        """run_alert_check returns list of events."""
        with patch.object(AlertManager, "from_env") as mock_from_env:
            mock_manager = MagicMock()
            mock_manager.check_all = AsyncMock(return_value=[])
            mock_from_env.return_value = mock_manager

            events = await run_alert_check()

            assert events == []
            mock_manager.check_all.assert_called_once()
