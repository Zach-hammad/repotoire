"""Sandbox alerting system for cost and operational monitoring.

This module provides alerting capabilities for sandbox operations:
- Cost threshold alerts (per customer)
- High failure rate alerts
- Slow operation alerts

Supports multiple notification channels:
- Slack webhooks
- Email (via SMTP)
- Custom webhook callbacks

Usage:
    ```python
    from repotoire.sandbox.alerts import (
        AlertManager,
        CostThresholdAlert,
        FailureRateAlert,
        SlowOperationAlert,
    )

    # Create alert manager
    manager = AlertManager()
    manager.add_channel(SlackChannel(webhook_url="https://..."))

    # Register alerts
    manager.register(CostThresholdAlert(threshold_usd=10.0, period_hours=24))
    manager.register(FailureRateAlert(threshold_percent=10.0, period_hours=1))
    manager.register(SlowOperationAlert(threshold_ms=30000))

    # Check alerts (typically called on a schedule)
    await manager.check_all()
    ```
"""

from __future__ import annotations

import asyncio
import json
import os
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Callable, Optional

import httpx

from repotoire.logging_config import get_logger
from repotoire.sandbox.metrics import SandboxMetricsCollector

logger = get_logger(__name__)


# =============================================================================
# Alert Models
# =============================================================================


@dataclass
class AlertEvent:
    """An alert event to be sent through notification channels.

    Attributes:
        alert_type: Type of alert ('cost_threshold', 'failure_rate', 'slow_operation')
        severity: Alert severity ('info', 'warning', 'critical')
        title: Short title for the alert
        message: Detailed alert message
        data: Additional structured data
        customer_id: Customer the alert relates to (if applicable)
        timestamp: When the alert was triggered
    """

    alert_type: str
    severity: str  # 'info', 'warning', 'critical'
    title: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    customer_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "alert_type": self.alert_type,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "data": self.data,
            "customer_id": self.customer_id,
            "timestamp": self.timestamp.isoformat(),
        }

    def to_slack_block(self) -> dict[str, Any]:
        """Convert to Slack block format."""
        severity_emoji = {
            "info": ":information_source:",
            "warning": ":warning:",
            "critical": ":rotating_light:",
        }
        emoji = severity_emoji.get(self.severity, ":bell:")

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {self.title}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": self.message,
                },
            },
        ]

        # Add data fields
        if self.data:
            fields = []
            for key, value in self.data.items():
                fields.append({
                    "type": "mrkdwn",
                    "text": f"*{key}:* {value}",
                })
            blocks.append({
                "type": "section",
                "fields": fields[:10],  # Slack limit
            })

        # Add timestamp
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Triggered at {self.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                }
            ],
        })

        return {"blocks": blocks}


# =============================================================================
# Notification Channels
# =============================================================================


class NotificationChannel(ABC):
    """Abstract base class for notification channels."""

    @abstractmethod
    async def send(self, event: AlertEvent) -> bool:
        """Send an alert through this channel.

        Args:
            event: The alert event to send

        Returns:
            True if sent successfully, False otherwise
        """
        pass


class SlackChannel(NotificationChannel):
    """Slack webhook notification channel."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        channel: Optional[str] = None,
        username: str = "Repotoire Alerts",
    ):
        """Initialize Slack channel.

        Args:
            webhook_url: Slack webhook URL (or SLACK_WEBHOOK_URL env var)
            channel: Override channel (optional)
            username: Bot username to display
        """
        self.webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
        self.channel = channel
        self.username = username

    async def send(self, event: AlertEvent) -> bool:
        """Send alert to Slack."""
        if not self.webhook_url:
            logger.warning("Slack webhook URL not configured")
            return False

        payload = event.to_slack_block()
        payload["username"] = self.username

        if self.channel:
            payload["channel"] = self.channel

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    timeout=10.0,
                )
                response.raise_for_status()
                logger.info(f"Sent Slack alert: {event.title}")
                return True
        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")
            return False


class EmailChannel(NotificationChannel):
    """Email notification channel via SMTP."""

    def __init__(
        self,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        from_email: Optional[str] = None,
        to_emails: Optional[list[str]] = None,
        use_tls: bool = True,
    ):
        """Initialize email channel.

        Args:
            smtp_host: SMTP server host (or SMTP_HOST env var)
            smtp_port: SMTP server port (or SMTP_PORT env var)
            smtp_user: SMTP username (or SMTP_USER env var)
            smtp_password: SMTP password (or SMTP_PASSWORD env var)
            from_email: Sender email address (or ALERT_FROM_EMAIL env var)
            to_emails: Recipient email addresses (or ALERT_TO_EMAILS env var, comma-separated)
            use_tls: Whether to use TLS
        """
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST", "localhost")
        self.smtp_port = smtp_port or int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = smtp_user or os.getenv("SMTP_USER")
        self.smtp_password = smtp_password or os.getenv("SMTP_PASSWORD")
        self.from_email = from_email or os.getenv("ALERT_FROM_EMAIL", "alerts@repotoire.dev")
        self.use_tls = use_tls

        to_env = os.getenv("ALERT_TO_EMAILS", "")
        self.to_emails = to_emails or [e.strip() for e in to_env.split(",") if e.strip()]

    async def send(self, event: AlertEvent) -> bool:
        """Send alert via email."""
        if not self.to_emails:
            logger.warning("No email recipients configured")
            return False

        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[{event.severity.upper()}] {event.title}"
            msg["From"] = self.from_email
            msg["To"] = ", ".join(self.to_emails)

            # Plain text version
            text_body = f"""
{event.title}
{'=' * len(event.title)}

{event.message}

Details:
{json.dumps(event.data, indent=2)}

Triggered: {event.timestamp.isoformat()}
"""

            # HTML version
            html_body = f"""
<html>
<body>
<h2>{event.title}</h2>
<p>{event.message}</p>
<h3>Details</h3>
<pre>{json.dumps(event.data, indent=2)}</pre>
<p><small>Triggered: {event.timestamp.isoformat()}</small></p>
</body>
</html>
"""

            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            # Send email in thread pool
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._send_smtp, msg)

            logger.info(f"Sent email alert: {event.title}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
            return False

    def _send_smtp(self, msg: MIMEMultipart) -> None:
        """Send message via SMTP (sync, called in executor)."""
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            if self.use_tls:
                server.starttls()
            if self.smtp_user and self.smtp_password:
                server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)


class WebhookChannel(NotificationChannel):
    """Generic webhook notification channel."""

    def __init__(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
        transform: Optional[Callable[[AlertEvent], dict]] = None,
    ):
        """Initialize webhook channel.

        Args:
            url: Webhook URL to POST to
            headers: Custom headers to include
            transform: Optional function to transform AlertEvent to payload
        """
        self.url = url
        self.headers = headers or {}
        self.transform = transform or (lambda e: e.to_dict())

    async def send(self, event: AlertEvent) -> bool:
        """Send alert to webhook."""
        try:
            payload = self.transform(event)

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.url,
                    json=payload,
                    headers=self.headers,
                    timeout=10.0,
                )
                response.raise_for_status()
                logger.info(f"Sent webhook alert: {event.title}")
                return True
        except Exception as e:
            logger.error(f"Failed to send webhook alert: {e}")
            return False


# =============================================================================
# Alert Definitions
# =============================================================================


class AlertDefinition(ABC):
    """Abstract base class for alert definitions."""

    @abstractmethod
    async def check(self, collector: SandboxMetricsCollector) -> list[AlertEvent]:
        """Check if alert condition is met.

        Args:
            collector: Connected metrics collector

        Returns:
            List of alert events to send (empty if no alert)
        """
        pass


class CostThresholdAlert(AlertDefinition):
    """Alert when customer cost exceeds threshold."""

    def __init__(
        self,
        threshold_usd: float = 10.0,
        period_hours: int = 24,
        customer_ids: Optional[list[str]] = None,
    ):
        """Initialize cost threshold alert.

        Args:
            threshold_usd: Cost threshold in USD
            period_hours: Period to measure cost over
            customer_ids: Specific customers to monitor (None for all)
        """
        self.threshold_usd = threshold_usd
        self.period_hours = period_hours
        self.customer_ids = customer_ids

    async def check(self, collector: SandboxMetricsCollector) -> list[AlertEvent]:
        """Check if any customer exceeds cost threshold."""
        events = []

        # Get top customers by cost
        from datetime import timedelta
        start_date = datetime.now(timezone.utc) - timedelta(hours=self.period_hours)

        customers = await collector.get_cost_by_customer(
            start_date=start_date,
            limit=100,
        )

        for customer in customers:
            customer_id = customer.get("customer_id")

            # Skip if monitoring specific customers and this isn't one
            if self.customer_ids and customer_id not in self.customer_ids:
                continue

            cost = customer.get("total_cost_usd", 0)

            if cost > self.threshold_usd:
                severity = "critical" if cost > self.threshold_usd * 2 else "warning"

                events.append(AlertEvent(
                    alert_type="cost_threshold",
                    severity=severity,
                    title=f"Cost Threshold Exceeded: {customer_id}",
                    message=f"Customer {customer_id} has exceeded the ${self.threshold_usd:.2f} "
                            f"cost threshold in the last {self.period_hours} hours.",
                    data={
                        "Customer ID": customer_id,
                        "Current Cost": f"${cost:.4f}",
                        "Threshold": f"${self.threshold_usd:.2f}",
                        "Period": f"{self.period_hours} hours",
                        "Operations": customer.get("total_operations", 0),
                    },
                    customer_id=customer_id,
                ))

        return events


class FailureRateAlert(AlertDefinition):
    """Alert when failure rate exceeds threshold."""

    def __init__(
        self,
        threshold_percent: float = 10.0,
        period_hours: int = 1,
        min_operations: int = 10,
    ):
        """Initialize failure rate alert.

        Args:
            threshold_percent: Failure rate threshold percentage
            period_hours: Period to measure over
            min_operations: Minimum operations to trigger alert
        """
        self.threshold_percent = threshold_percent
        self.period_hours = period_hours
        self.min_operations = min_operations

    async def check(self, collector: SandboxMetricsCollector) -> list[AlertEvent]:
        """Check if failure rate exceeds threshold."""
        events = []

        rate = await collector.get_failure_rate(hours=self.period_hours)

        total = rate.get("total_operations", 0)
        failures = rate.get("failures", 0)
        failure_rate = rate.get("failure_rate", 0)

        if total >= self.min_operations and failure_rate > self.threshold_percent:
            severity = "critical" if failure_rate > self.threshold_percent * 2 else "warning"

            events.append(AlertEvent(
                alert_type="failure_rate",
                severity=severity,
                title="High Failure Rate Detected",
                message=f"Sandbox failure rate ({failure_rate:.1f}%) exceeds threshold "
                        f"({self.threshold_percent:.1f}%) in the last {self.period_hours} hour(s).",
                data={
                    "Failure Rate": f"{failure_rate:.1f}%",
                    "Threshold": f"{self.threshold_percent:.1f}%",
                    "Total Operations": total,
                    "Failures": failures,
                    "Period": f"{self.period_hours} hour(s)",
                },
            ))

        return events


class SlowOperationAlert(AlertDefinition):
    """Alert when operations exceed duration threshold."""

    def __init__(
        self,
        threshold_ms: int = 30000,
        check_count: int = 5,
    ):
        """Initialize slow operation alert.

        Args:
            threshold_ms: Duration threshold in milliseconds
            check_count: Number of recent slow operations to check
        """
        self.threshold_ms = threshold_ms
        self.check_count = check_count

    async def check(self, collector: SandboxMetricsCollector) -> list[AlertEvent]:
        """Check for slow operations."""
        events = []

        slow_ops = await collector.get_slow_operations(
            threshold_ms=self.threshold_ms,
            limit=self.check_count,
        )

        if slow_ops:
            # Group by operation type
            by_type: dict[str, list] = {}
            for op in slow_ops:
                op_type = op.get("operation_type", "unknown")
                by_type.setdefault(op_type, []).append(op)

            for op_type, ops in by_type.items():
                avg_duration = sum(op.get("duration_ms", 0) for op in ops) / len(ops)

                events.append(AlertEvent(
                    alert_type="slow_operation",
                    severity="warning",
                    title=f"Slow Operations Detected: {op_type}",
                    message=f"Found {len(ops)} {op_type} operation(s) exceeding "
                            f"{self.threshold_ms/1000:.0f}s threshold.",
                    data={
                        "Operation Type": op_type,
                        "Count": len(ops),
                        "Avg Duration": f"{avg_duration/1000:.1f}s",
                        "Threshold": f"{self.threshold_ms/1000:.0f}s",
                    },
                ))

        return events


# =============================================================================
# Alert Manager
# =============================================================================


class AlertManager:
    """Manages alert definitions and notification channels.

    Usage:
        ```python
        manager = AlertManager()

        # Add notification channels
        manager.add_channel(SlackChannel(webhook_url="https://..."))
        manager.add_channel(EmailChannel(to_emails=["ops@example.com"]))

        # Register alert definitions
        manager.register(CostThresholdAlert(threshold_usd=10.0))
        manager.register(FailureRateAlert(threshold_percent=10.0))

        # Check all alerts (call periodically)
        await manager.check_all()
        ```
    """

    def __init__(self, collector: Optional[SandboxMetricsCollector] = None):
        """Initialize alert manager.

        Args:
            collector: Metrics collector to use (created if not provided)
        """
        self._collector = collector
        self._alerts: list[AlertDefinition] = []
        self._channels: list[NotificationChannel] = []

    def add_channel(self, channel: NotificationChannel) -> None:
        """Add a notification channel."""
        self._channels.append(channel)

    def register(self, alert: AlertDefinition) -> None:
        """Register an alert definition."""
        self._alerts.append(alert)

    async def check_all(self) -> list[AlertEvent]:
        """Check all registered alerts and send notifications.

        Returns:
            List of all alert events that were triggered
        """
        all_events: list[AlertEvent] = []

        # Get or create collector
        collector = self._collector or SandboxMetricsCollector()
        should_close = self._collector is None

        try:
            if not collector._connected:
                await collector.connect()

            # Check all alerts
            for alert in self._alerts:
                try:
                    events = await alert.check(collector)
                    all_events.extend(events)
                except Exception as e:
                    logger.error(f"Failed to check alert {type(alert).__name__}: {e}")

            # Send all events through all channels
            for event in all_events:
                for channel in self._channels:
                    try:
                        await channel.send(event)
                    except Exception as e:
                        logger.error(f"Failed to send alert via {type(channel).__name__}: {e}")

        finally:
            if should_close:
                await collector.close()

        return all_events

    @classmethod
    def from_env(cls) -> "AlertManager":
        """Create alert manager with default configuration from environment.

        Environment Variables:
            SLACK_WEBHOOK_URL: Slack webhook for alerts
            ALERT_COST_THRESHOLD: Cost threshold in USD (default: 10.0)
            ALERT_FAILURE_RATE_THRESHOLD: Failure rate % (default: 10.0)
            ALERT_SLOW_OPERATION_MS: Slow operation threshold (default: 30000)
        """
        manager = cls()

        # Add Slack channel if configured
        slack_url = os.getenv("SLACK_WEBHOOK_URL")
        if slack_url:
            manager.add_channel(SlackChannel(webhook_url=slack_url))

        # Add email channel if configured
        to_emails = os.getenv("ALERT_TO_EMAILS")
        if to_emails:
            manager.add_channel(EmailChannel())

        # Register default alerts
        cost_threshold = float(os.getenv("ALERT_COST_THRESHOLD", "10.0"))
        manager.register(CostThresholdAlert(threshold_usd=cost_threshold))

        failure_threshold = float(os.getenv("ALERT_FAILURE_RATE_THRESHOLD", "10.0"))
        manager.register(FailureRateAlert(threshold_percent=failure_threshold))

        slow_threshold = int(os.getenv("ALERT_SLOW_OPERATION_MS", "30000"))
        manager.register(SlowOperationAlert(threshold_ms=slow_threshold))

        return manager


async def run_alert_check() -> list[AlertEvent]:
    """Run a single alert check using default configuration.

    This is a convenience function for scheduled tasks.

    Returns:
        List of triggered alert events
    """
    manager = AlertManager.from_env()
    return await manager.check_all()
