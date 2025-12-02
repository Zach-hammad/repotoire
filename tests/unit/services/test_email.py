"""Unit tests for EmailService.

Tests cover:
- Email sending functionality
- Template rendering
- Score to grade conversion
- All email types (welcome, analysis, team invite, etc.)
"""

import os
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def email_service():
    """Create EmailService with mocked API key."""
    with patch.dict(os.environ, {
        "RESEND_API_KEY": "test_key",
        "EMAIL_FROM_ADDRESS": "Test <test@repotoire.io>",
        "APP_BASE_URL": "https://test.repotoire.io",
    }):
        from repotoire.services.email import EmailService
        return EmailService()


class TestScoreToGrade:
    """Tests for score to grade conversion."""

    def test_score_90_is_grade_a(self, email_service):
        """Score 90+ should be grade A."""
        assert email_service._score_to_grade(90) == "A"
        assert email_service._score_to_grade(95) == "A"
        assert email_service._score_to_grade(100) == "A"

    def test_score_80_to_89_is_grade_b(self, email_service):
        """Score 80-89 should be grade B."""
        assert email_service._score_to_grade(80) == "B"
        assert email_service._score_to_grade(85) == "B"
        assert email_service._score_to_grade(89) == "B"

    def test_score_70_to_79_is_grade_c(self, email_service):
        """Score 70-79 should be grade C."""
        assert email_service._score_to_grade(70) == "C"
        assert email_service._score_to_grade(75) == "C"
        assert email_service._score_to_grade(79) == "C"

    def test_score_60_to_69_is_grade_d(self, email_service):
        """Score 60-69 should be grade D."""
        assert email_service._score_to_grade(60) == "D"
        assert email_service._score_to_grade(65) == "D"
        assert email_service._score_to_grade(69) == "D"

    def test_score_below_60_is_grade_f(self, email_service):
        """Score below 60 should be grade F."""
        assert email_service._score_to_grade(59) == "F"
        assert email_service._score_to_grade(50) == "F"
        assert email_service._score_to_grade(0) == "F"


class TestSendWelcome:
    """Tests for welcome email."""

    @pytest.mark.asyncio
    async def test_send_welcome_email(self, email_service):
        """Welcome email should be sent with correct content."""
        with patch("resend.Emails.send") as mock_send:
            mock_send.return_value = {"id": "email_123"}

            result = await email_service.send_welcome(
                user_email="test@example.com",
                user_name="John",
            )

            assert result == "email_123"
            mock_send.assert_called_once()
            call_args = mock_send.call_args[0][0]
            assert call_args["to"] == "test@example.com"
            assert "Welcome" in call_args["subject"]
            assert "John" in call_args["html"]

    @pytest.mark.asyncio
    async def test_send_welcome_email_no_name(self, email_service):
        """Welcome email should handle missing name gracefully."""
        with patch("resend.Emails.send") as mock_send:
            mock_send.return_value = {"id": "email_456"}

            result = await email_service.send_welcome(
                user_email="test@example.com",
                user_name=None,
            )

            assert result == "email_456"
            call_args = mock_send.call_args[0][0]
            # Should use "there" as fallback
            assert "there" in call_args["html"]


class TestSendAnalysisComplete:
    """Tests for analysis complete email."""

    @pytest.mark.asyncio
    async def test_send_analysis_complete_email(self, email_service):
        """Analysis complete email should include score and grade."""
        with patch("resend.Emails.send") as mock_send:
            mock_send.return_value = {"id": "email_789"}

            result = await email_service.send_analysis_complete(
                user_email="test@example.com",
                repo_name="my-repo",
                health_score=85,
                dashboard_url="https://app.repotoire.io/repos/123",
            )

            assert result == "email_789"
            call_args = mock_send.call_args[0][0]
            assert "85" in call_args["subject"]
            assert "my-repo" in call_args["subject"]
            assert "B" in call_args["html"]  # Grade B for score 85

    @pytest.mark.asyncio
    async def test_send_analysis_complete_grade_a(self, email_service):
        """Analysis complete with high score should show grade A."""
        with patch("resend.Emails.send") as mock_send:
            mock_send.return_value = {"id": "email_aaa"}

            await email_service.send_analysis_complete(
                user_email="test@example.com",
                repo_name="excellent-repo",
                health_score=95,
                dashboard_url="https://app.repotoire.io/repos/456",
            )

            call_args = mock_send.call_args[0][0]
            assert "A" in call_args["html"]


class TestSendAnalysisFailed:
    """Tests for analysis failed email."""

    @pytest.mark.asyncio
    async def test_send_analysis_failed_email(self, email_service):
        """Analysis failed email should include error message."""
        with patch("resend.Emails.send") as mock_send:
            mock_send.return_value = {"id": "email_fail"}

            result = await email_service.send_analysis_failed(
                user_email="test@example.com",
                repo_name="broken-repo",
                error_message="Repository not accessible",
            )

            assert result == "email_fail"
            call_args = mock_send.call_args[0][0]
            assert "Failed" in call_args["subject"]
            assert "broken-repo" in call_args["subject"]
            assert "Repository not accessible" in call_args["html"]


class TestSendTeamInvite:
    """Tests for team invite email."""

    @pytest.mark.asyncio
    async def test_send_team_invite_email(self, email_service):
        """Team invite email should include inviter and org names."""
        with patch("resend.Emails.send") as mock_send:
            mock_send.return_value = {"id": "email_invite"}

            result = await email_service.send_team_invite(
                to_email="newmember@example.com",
                inviter_name="Alice",
                org_name="Acme Corp",
                invite_url="https://app.repotoire.io/invite/abc123",
            )

            assert result == "email_invite"
            call_args = mock_send.call_args[0][0]
            assert "Alice" in call_args["subject"]
            assert "Acme Corp" in call_args["subject"]
            assert "https://app.repotoire.io/invite/abc123" in call_args["html"]


class TestSendPaymentFailed:
    """Tests for payment failed email."""

    @pytest.mark.asyncio
    async def test_send_payment_failed_email(self, email_service):
        """Payment failed email should include org name and retry URL."""
        with patch("resend.Emails.send") as mock_send:
            mock_send.return_value = {"id": "email_payment"}

            result = await email_service.send_payment_failed(
                user_email="billing@example.com",
                org_name="Startup Inc",
                retry_url="https://app.repotoire.io/billing/retry",
            )

            assert result == "email_payment"
            call_args = mock_send.call_args[0][0]
            assert "Payment failed" in call_args["subject"]
            assert "Startup Inc" in call_args["subject"]
            assert "https://app.repotoire.io/billing/retry" in call_args["html"]


class TestSendHealthRegressionAlert:
    """Tests for health regression alert email."""

    @pytest.mark.asyncio
    async def test_send_health_regression_alert(self, email_service):
        """Health regression alert should include score drop details."""
        with patch("resend.Emails.send") as mock_send:
            mock_send.return_value = {"id": "email_regression"}

            result = await email_service.send_health_regression_alert(
                user_email="dev@example.com",
                repo_name="declining-repo",
                old_score=80,
                new_score=65,
                dashboard_url="https://app.repotoire.io/repos/789",
            )

            assert result == "email_regression"
            call_args = mock_send.call_args[0][0]
            assert "Health Score Dropped" in call_args["subject"]
            assert "-15" in call_args["subject"]
            assert "declining-repo" in call_args["subject"]
            assert "80" in call_args["html"]
            assert "65" in call_args["html"]


class TestSendDeletionConfirmation:
    """Tests for deletion confirmation email (GDPR)."""

    @pytest.mark.asyncio
    async def test_send_deletion_confirmation_email(self, email_service):
        """Deletion confirmation should include date and cancel URL."""
        with patch("resend.Emails.send") as mock_send:
            mock_send.return_value = {"id": "email_deletion"}

            result = await email_service.send_deletion_confirmation(
                user_email="leaving@example.com",
                deletion_date="January 15, 2025",
                cancel_url="https://app.repotoire.io/settings/privacy?cancel=true",
            )

            assert result == "email_deletion"
            call_args = mock_send.call_args[0][0]
            assert "Account Deletion Scheduled" in call_args["subject"]
            assert "January 15, 2025" in call_args["html"]
            assert "https://app.repotoire.io/settings/privacy?cancel=true" in call_args["html"]


class TestSendDeletionCancelled:
    """Tests for deletion cancelled email."""

    @pytest.mark.asyncio
    async def test_send_deletion_cancelled_email(self, email_service):
        """Deletion cancelled email should be sent correctly."""
        with patch("resend.Emails.send") as mock_send:
            mock_send.return_value = {"id": "email_cancelled"}

            result = await email_service.send_deletion_cancelled(
                user_email="staying@example.com",
            )

            assert result == "email_cancelled"
            call_args = mock_send.call_args[0][0]
            assert "Account Deletion Cancelled" in call_args["subject"]


class TestEmailServiceConfiguration:
    """Tests for EmailService configuration."""

    def test_default_from_address(self):
        """EmailService should use default from address if not configured."""
        with patch.dict(os.environ, {"RESEND_API_KEY": "test_key"}, clear=False):
            # Remove EMAIL_FROM_ADDRESS if it exists
            env_copy = os.environ.copy()
            env_copy.pop("EMAIL_FROM_ADDRESS", None)
            with patch.dict(os.environ, env_copy, clear=True):
                with patch.dict(os.environ, {"RESEND_API_KEY": "test_key"}):
                    from repotoire.services.email import EmailService
                    service = EmailService()
                    assert "repotoire.io" in service.from_address

    def test_custom_from_address(self):
        """EmailService should use custom from address when configured."""
        with patch.dict(os.environ, {
            "RESEND_API_KEY": "test_key",
            "EMAIL_FROM_ADDRESS": "Custom <custom@example.com>",
        }):
            from repotoire.services.email import EmailService
            service = EmailService()
            assert service.from_address == "Custom <custom@example.com>"


class TestGetEmailService:
    """Tests for singleton EmailService getter."""

    def test_get_email_service_returns_same_instance(self):
        """get_email_service should return the same instance."""
        with patch.dict(os.environ, {"RESEND_API_KEY": "test_key"}):
            from repotoire.services.email import get_email_service, _email_service
            import repotoire.services.email as email_module

            # Reset singleton
            email_module._email_service = None

            service1 = get_email_service()
            service2 = get_email_service()

            assert service1 is service2
