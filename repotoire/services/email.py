"""Transactional email service using Resend.

This module provides email functionality for:
- Welcome emails on signup
- Analysis completion/failure notifications
- Team invitations
- Payment failure alerts
- Health regression alerts
- Account deletion confirmation (GDPR)
"""

import os
from typing import Any

import resend
from jinja2 import Environment, PackageLoader, select_autoescape


class EmailService:
    """Transactional email service using Resend.

    Attributes:
        from_address: The sender email address.
        templates: Jinja2 template environment for email templates.
    """

    def __init__(self) -> None:
        """Initialize the email service with Resend API key."""
        resend.api_key = os.environ.get("RESEND_API_KEY")
        self.from_address = os.environ.get(
            "EMAIL_FROM_ADDRESS", "Repotoire <hello@repotoire.io>"
        )
        self.templates = Environment(
            loader=PackageLoader("repotoire", "templates/emails"),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self.base_url = os.environ.get("APP_BASE_URL", "https://app.repotoire.io")

    async def send(
        self,
        to: str,
        subject: str,
        template_name: str,
        context: dict[str, Any],
    ) -> str:
        """Send a templated email.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            template_name: Name of the template file (without .html extension).
            context: Template context variables.

        Returns:
            Email ID from Resend.
        """
        # Add common context
        context.setdefault("base_url", self.base_url)
        context.setdefault(
            "unsubscribe_url", f"{self.base_url}/settings/notifications"
        )

        template = self.templates.get_template(f"{template_name}.html")
        html_content = template.render(**context)

        result = resend.Emails.send(
            {
                "from": self.from_address,
                "to": to,
                "subject": subject,
                "html": html_content,
            }
        )
        return result["id"]

    async def send_welcome(self, user_email: str, user_name: str | None) -> str:
        """Send welcome email to new user.

        Args:
            user_email: User's email address.
            user_name: User's display name.

        Returns:
            Email ID.
        """
        return await self.send(
            to=user_email,
            subject="Welcome to Repotoire!",
            template_name="welcome",
            context={"name": user_name or "there"},
        )

    async def send_analysis_complete(
        self,
        user_email: str,
        repo_name: str,
        health_score: int,
        dashboard_url: str,
    ) -> str:
        """Send analysis completion notification.

        Args:
            user_email: User's email address.
            repo_name: Repository name.
            health_score: Analysis health score (0-100).
            dashboard_url: URL to view the analysis.

        Returns:
            Email ID.
        """
        grade = self._score_to_grade(health_score)
        return await self.send(
            to=user_email,
            subject=f"Analysis Complete: {repo_name} scored {health_score}/100",
            template_name="analysis_complete",
            context={
                "repo_name": repo_name,
                "health_score": health_score,
                "grade": grade,
                "dashboard_url": dashboard_url,
            },
        )

    async def send_analysis_failed(
        self,
        user_email: str,
        repo_name: str,
        error_message: str,
    ) -> str:
        """Send analysis failure notification.

        Args:
            user_email: User's email address.
            repo_name: Repository name.
            error_message: Description of the error.

        Returns:
            Email ID.
        """
        return await self.send(
            to=user_email,
            subject=f"Analysis Failed: {repo_name}",
            template_name="analysis_failed",
            context={"repo_name": repo_name, "error_message": error_message},
        )

    async def send_team_invite(
        self,
        to_email: str,
        inviter_name: str,
        org_name: str,
        invite_url: str,
    ) -> str:
        """Send team invitation email.

        Args:
            to_email: Invitee's email address.
            inviter_name: Name of the person sending the invite.
            org_name: Organization name.
            invite_url: URL to accept the invitation.

        Returns:
            Email ID.
        """
        return await self.send(
            to=to_email,
            subject=f"{inviter_name} invited you to join {org_name} on Repotoire",
            template_name="team_invite",
            context={
                "inviter_name": inviter_name,
                "org_name": org_name,
                "invite_url": invite_url,
            },
        )

    async def send_payment_failed(
        self,
        user_email: str,
        org_name: str,
        retry_url: str,
    ) -> str:
        """Send payment failure notification.

        Args:
            user_email: User's email address.
            org_name: Organization name.
            retry_url: URL to update payment method.

        Returns:
            Email ID.
        """
        return await self.send(
            to=user_email,
            subject=f"Payment failed for {org_name}",
            template_name="payment_failed",
            context={"org_name": org_name, "retry_url": retry_url},
        )

    async def send_health_regression_alert(
        self,
        user_email: str,
        repo_name: str,
        old_score: int,
        new_score: int,
        dashboard_url: str,
    ) -> str:
        """Send health score regression alert.

        Args:
            user_email: User's email address.
            repo_name: Repository name.
            old_score: Previous health score.
            new_score: New (lower) health score.
            dashboard_url: URL to view the analysis.

        Returns:
            Email ID.
        """
        drop = old_score - new_score
        return await self.send(
            to=user_email,
            subject=f"Health Score Dropped: {repo_name} (-{drop} points)",
            template_name="health_regression",
            context={
                "repo_name": repo_name,
                "old_score": old_score,
                "new_score": new_score,
                "drop": drop,
                "dashboard_url": dashboard_url,
            },
        )

    async def send_deletion_confirmation(
        self,
        user_email: str,
        deletion_date: str,
        cancel_url: str,
    ) -> str:
        """Send account deletion confirmation (GDPR).

        Args:
            user_email: User's email address.
            deletion_date: Formatted date when deletion will occur.
            cancel_url: URL to cancel the deletion.

        Returns:
            Email ID.
        """
        return await self.send(
            to=user_email,
            subject="Account Deletion Scheduled",
            template_name="deletion_confirmation",
            context={
                "deletion_date": deletion_date,
                "cancel_url": cancel_url,
            },
        )

    async def send_deletion_cancelled(
        self,
        user_email: str,
    ) -> str:
        """Send account deletion cancellation confirmation.

        Args:
            user_email: User's email address.

        Returns:
            Email ID.
        """
        return await self.send(
            to=user_email,
            subject="Account Deletion Cancelled",
            template_name="deletion_cancelled",
            context={},
        )

    @staticmethod
    def _score_to_grade(score: int) -> str:
        """Convert health score to letter grade.

        Args:
            score: Health score (0-100).

        Returns:
            Letter grade (A, B, C, D, or F).
        """
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"


# Singleton instance for easy access
_email_service: EmailService | None = None


def get_email_service() -> EmailService:
    """Get the singleton EmailService instance.

    Returns:
        EmailService instance.
    """
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
