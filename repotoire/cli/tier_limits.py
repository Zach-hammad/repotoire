"""Tier-based limits enforcement for CLI commands.

Checks organization's usage against plan limits before allowing
expensive operations like analysis or repository connections.
"""

from dataclasses import dataclass
from typing import Optional

import httpx
from rich.console import Console
from rich.table import Table

from repotoire.cli.auth import AuthenticationError, CLIAuth, CLICredentials
from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console()


@dataclass
class UsageInfo:
    """Current usage stats for an organization."""

    tier: str
    repos_used: int
    repos_limit: int  # -1 = unlimited
    analyses_this_month: int
    analyses_limit: int  # -1 = unlimited
    seats: int = 1

    @property
    def repos_remaining(self) -> float:
        """Get remaining repositories."""
        if self.repos_limit == -1:
            return float("inf")
        return max(0, self.repos_limit - self.repos_used)

    @property
    def analyses_remaining(self) -> float:
        """Get remaining analyses this month."""
        if self.analyses_limit == -1:
            return float("inf")
        return max(0, self.analyses_limit - self.analyses_this_month)

    @classmethod
    def from_api_response(cls, data: dict) -> "UsageInfo":
        """Create UsageInfo from API response.

        Args:
            data: API response data

        Returns:
            UsageInfo instance
        """
        return cls(
            tier=data["tier"],
            repos_used=data["repos_used"],
            repos_limit=data["repos_limit"],
            analyses_this_month=data["analyses_this_month"],
            analyses_limit=data["analyses_limit"],
            seats=data.get("seats", 1),
        )


class TierLimitError(Exception):
    """Exception raised when a tier limit is exceeded."""

    def __init__(self, message: str, upgrade_url: Optional[str] = None):
        """Initialize TierLimitError.

        Args:
            message: Error message
            upgrade_url: URL to upgrade plan
        """
        super().__init__(message)
        self.upgrade_url = upgrade_url


class TierLimits:
    """Check and enforce tier-based limits."""

    def __init__(self, auth: CLIAuth):
        """Initialize TierLimits.

        Args:
            auth: CLIAuth instance for API communication
        """
        self.auth = auth

    async def get_usage(self, credentials: CLICredentials) -> UsageInfo:
        """Fetch current usage from API.

        Args:
            credentials: Valid CLI credentials

        Returns:
            UsageInfo with current usage stats

        Raises:
            AuthenticationError: If API request fails
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.auth.api_url}/api/v1/usage",
                    headers={"Authorization": f"Bearer {credentials.access_token}"},
                )
                response.raise_for_status()
                data = response.json()
                return UsageInfo.from_api_response(data)
            except httpx.HTTPStatusError as e:
                error_detail = "Unknown error"
                try:
                    error_detail = e.response.json().get("detail", error_detail)
                except Exception:
                    pass
                raise AuthenticationError(f"Failed to get usage: {error_detail}")
            except httpx.HTTPError as e:
                raise AuthenticationError(f"Failed to connect to Repotoire API: {e}")

    def get_usage_sync(self, credentials: CLICredentials) -> UsageInfo:
        """Fetch current usage from API (synchronous version).

        Args:
            credentials: Valid CLI credentials

        Returns:
            UsageInfo with current usage stats

        Raises:
            AuthenticationError: If API request fails
        """
        with httpx.Client(timeout=30.0) as client:
            try:
                response = client.get(
                    f"{self.auth.api_url}/api/v1/usage",
                    headers={"Authorization": f"Bearer {credentials.access_token}"},
                )
                response.raise_for_status()
                data = response.json()
                return UsageInfo.from_api_response(data)
            except httpx.HTTPStatusError as e:
                error_detail = "Unknown error"
                try:
                    error_detail = e.response.json().get("detail", error_detail)
                except Exception:
                    pass
                raise AuthenticationError(f"Failed to get usage: {error_detail}")
            except httpx.HTTPError as e:
                raise AuthenticationError(f"Failed to connect to Repotoire API: {e}")

    async def check_can_analyze(self, credentials: CLICredentials) -> bool:
        """Check if org can run another analysis.

        Args:
            credentials: Valid CLI credentials

        Returns:
            True if analysis allowed, False if limit reached
        """
        try:
            usage = await self.get_usage(credentials)
        except AuthenticationError as e:
            logger.warning(f"Failed to check usage limits: {e}")
            # Allow analysis if we can't check limits (fail open)
            return True

        if usage.analyses_remaining <= 0:
            self._display_analysis_limit_error(usage)
            return False

        return True

    def check_can_analyze_sync(self, credentials: CLICredentials) -> bool:
        """Check if org can run another analysis (synchronous version).

        Args:
            credentials: Valid CLI credentials

        Returns:
            True if analysis allowed, False if limit reached
        """
        try:
            usage = self.get_usage_sync(credentials)
        except AuthenticationError as e:
            logger.warning(f"Failed to check usage limits: {e}")
            # Allow analysis if we can't check limits (fail open)
            return True

        if usage.analyses_remaining <= 0:
            self._display_analysis_limit_error(usage)
            return False

        return True

    async def check_can_add_repo(self, credentials: CLICredentials) -> bool:
        """Check if org can connect another repository.

        Args:
            credentials: Valid CLI credentials

        Returns:
            True if repo connection allowed, False if limit reached
        """
        try:
            usage = await self.get_usage(credentials)
        except AuthenticationError as e:
            logger.warning(f"Failed to check usage limits: {e}")
            # Allow repo add if we can't check limits (fail open)
            return True

        if usage.repos_remaining <= 0:
            self._display_repo_limit_error(usage)
            return False

        return True

    def check_can_add_repo_sync(self, credentials: CLICredentials) -> bool:
        """Check if org can connect another repository (synchronous version).

        Args:
            credentials: Valid CLI credentials

        Returns:
            True if repo connection allowed, False if limit reached
        """
        try:
            usage = self.get_usage_sync(credentials)
        except AuthenticationError as e:
            logger.warning(f"Failed to check usage limits: {e}")
            # Allow repo add if we can't check limits (fail open)
            return True

        if usage.repos_remaining <= 0:
            self._display_repo_limit_error(usage)
            return False

        return True

    def _display_analysis_limit_error(self, usage: UsageInfo) -> None:
        """Display a helpful error message when analysis limit is reached.

        Args:
            usage: Current usage information
        """
        from rich.panel import Panel

        console.print()
        console.print(
            Panel(
                f"[bold red]Analysis Limit Reached[/bold red]\n\n"
                f"You've used [bold]{usage.analyses_this_month}[/bold] of "
                f"[bold]{usage.analyses_limit}[/bold] analyses this month\n"
                f"on the [cyan]{usage.tier.title()}[/cyan] plan.",
                title="Limit Reached",
                border_style="red",
            )
        )
        console.print()
        console.print("[bold]Options:[/bold]")
        console.print("  [dim]1.[/dim] Wait until next month for your limit to reset")
        console.print("  [dim]2.[/dim] Upgrade to Pro for unlimited analyses:")
        console.print("     [link=https://repotoire.com/settings/billing][blue]https://repotoire.com/settings/billing[/blue][/link]")
        console.print("  [dim]3.[/dim] Run locally with [cyan]--offline[/cyan] flag (skips cloud sync)")
        console.print()

    def _display_repo_limit_error(self, usage: UsageInfo) -> None:
        """Display a helpful error message when repository limit is reached.

        Args:
            usage: Current usage information
        """
        from rich.panel import Panel

        console.print()
        console.print(
            Panel(
                f"[bold red]Repository Limit Reached[/bold red]\n\n"
                f"You've connected [bold]{usage.repos_used}[/bold] of "
                f"[bold]{usage.repos_limit}[/bold] repositories\n"
                f"on the [cyan]{usage.tier.title()}[/cyan] plan.",
                title="Limit Reached",
                border_style="red",
            )
        )
        console.print()
        console.print("[bold]Options:[/bold]")
        console.print("  [dim]1.[/dim] Remove an existing repository to free up a slot")
        console.print("  [dim]2.[/dim] Upgrade to Pro for more repositories:")
        console.print("     [link=https://repotoire.com/settings/billing][blue]https://repotoire.com/settings/billing[/blue][/link]")
        console.print()
        console.print("[dim]View connected repos:[/dim] [cyan]repotoire repos list[/cyan]")
        console.print()

    def display_usage(self, usage: UsageInfo) -> None:
        """Display usage stats in a formatted table.

        Args:
            usage: UsageInfo to display
        """
        tier_colors = {
            "free": "white",
            "pro": "cyan",
            "enterprise": "magenta",
        }
        tier_color = tier_colors.get(usage.tier.lower(), "white")

        table = Table(title=f"Plan: [{tier_color}]{usage.tier.title()}[/{tier_color}]")
        table.add_column("Resource", style="cyan")
        table.add_column("Used", justify="right")
        table.add_column("Limit", justify="right")
        table.add_column("Remaining", justify="right")

        # Format limits
        repos_limit = "\u221e" if usage.repos_limit == -1 else str(usage.repos_limit)
        analyses_limit = "\u221e" if usage.analyses_limit == -1 else str(usage.analyses_limit)

        repos_remaining = "\u221e" if usage.repos_limit == -1 else str(int(usage.repos_remaining))
        analyses_remaining = (
            "\u221e" if usage.analyses_limit == -1 else str(int(usage.analyses_remaining))
        )

        # Color remaining based on usage
        repos_remaining_style = _get_usage_style(usage.repos_used, usage.repos_limit)
        analyses_remaining_style = _get_usage_style(usage.analyses_this_month, usage.analyses_limit)

        table.add_row(
            "Repositories",
            str(usage.repos_used),
            repos_limit,
            f"[{repos_remaining_style}]{repos_remaining}[/{repos_remaining_style}]",
        )
        table.add_row(
            "Analyses (month)",
            str(usage.analyses_this_month),
            analyses_limit,
            f"[{analyses_remaining_style}]{analyses_remaining}[/{analyses_remaining_style}]",
        )

        if usage.seats > 1:
            table.add_row("Seats", str(usage.seats), "-", "-")

        console.print(table)

        # Show upgrade prompt for free tier
        if usage.tier.lower() == "free":
            console.print()
            console.print("[dim]Upgrade to Pro for unlimited analyses:[/dim]")
            console.print("[blue]  repotoire auth upgrade[/blue]")


def _get_usage_style(used: int, limit: int) -> str:
    """Get color style based on usage percentage.

    Args:
        used: Amount used
        limit: Maximum limit (-1 for unlimited)

    Returns:
        Color style string
    """
    if limit == -1:
        return "green"

    if limit == 0:
        return "red"

    percentage = (used / limit) * 100

    if percentage >= 100:
        return "red"
    elif percentage >= 80:
        return "yellow"
    else:
        return "green"
