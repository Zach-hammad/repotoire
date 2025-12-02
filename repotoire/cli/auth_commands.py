"""Authentication CLI commands."""

import webbrowser

import click
from rich.console import Console

from repotoire.cli.auth import AuthenticationError, CLIAuth
from repotoire.cli.tier_limits import TierLimits
from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console()


@click.group(name="auth")
def auth_group():
    """Authentication and account commands."""
    pass


@auth_group.command()
def login():
    """Login to Repotoire via browser.

    Opens your default browser for authentication.
    Credentials are stored locally in ~/.repotoire/credentials.json.
    """
    cli_auth = CLIAuth()

    console.print("Opening browser for authentication...")

    try:
        credentials = cli_auth.login()
    except AuthenticationError as e:
        console.print(f"[red]✗[/] Authentication failed: {e}")
        raise click.Abort()

    console.print(f"\n[green]✓[/] Logged in as [bold]{credentials.user_email}[/]")
    if credentials.org_slug:
        console.print(f"  Organization: {credentials.org_slug}")
    console.print(f"  Plan: {credentials.tier.title()}")


@auth_group.command()
def logout():
    """Clear stored credentials.

    Removes locally stored credentials from ~/.repotoire/credentials.json.
    """
    cli_auth = CLIAuth()
    cli_auth.logout()


@auth_group.command()
def whoami():
    """Show current user and organization.

    Displays information about the currently authenticated user
    and their organization membership.
    """
    cli_auth = CLIAuth()
    credentials = cli_auth.get_current_user()

    if not credentials:
        console.print("[yellow]Not logged in[/]")
        console.print("Run [blue]repotoire auth login[/] to authenticate")
        return

    console.print(f"[bold]User:[/] {credentials.user_email}")
    console.print(f"[bold]User ID:[/] {credentials.user_id}")

    if credentials.org_slug:
        console.print(f"[bold]Organization:[/] {credentials.org_slug}")
        console.print(f"[bold]Org ID:[/] {credentials.org_id}")

    console.print(f"[bold]Plan:[/] {credentials.tier.title()}")

    if credentials.is_expired():
        console.print("[yellow]⚠ Session expired - run 'repotoire auth login' to refresh[/]")


@auth_group.command()
def usage():
    """Show current usage and limits.

    Displays a table showing:
    - Current plan tier
    - Repository usage vs limits
    - Analysis usage vs limits (this month)
    """
    cli_auth = CLIAuth()
    credentials = cli_auth.get_current_user()

    if not credentials:
        console.print("[yellow]Not logged in[/]")
        console.print("Run [blue]repotoire auth login[/] to authenticate")
        return

    # Check if expired
    if credentials.is_expired():
        console.print("[yellow]Session expired, refreshing...[/]")
        try:
            credentials = cli_auth.require_auth()
        except click.Abort:
            return

    limits = TierLimits(cli_auth)

    try:
        usage_info = limits.get_usage_sync(credentials)
        limits.display_usage(usage_info)
    except AuthenticationError as e:
        console.print(f"[red]✗[/] Failed to get usage info: {e}")
        raise click.Abort()


@auth_group.command()
def upgrade():
    """Open billing portal to upgrade plan.

    Opens your browser to the Repotoire billing portal
    where you can upgrade your subscription.
    """
    cli_auth = CLIAuth()
    credentials = cli_auth.get_current_user()

    if not credentials:
        console.print("[yellow]Login required to upgrade[/]")
        console.print("Run [blue]repotoire auth login[/] first")
        return

    # Construct billing URL
    # Replace 'api.' with 'app.' in the API URL to get the web app URL
    app_url = cli_auth.api_url.replace("api.", "app.")
    if app_url == cli_auth.api_url:
        # No 'api.' prefix found, try a different approach
        app_url = "https://app.repotoire.dev"

    billing_url = f"{app_url}/billing/upgrade"
    console.print(f"Opening billing portal: {billing_url}")
    webbrowser.open(billing_url)


@auth_group.command("switch-org")
@click.argument("org_slug")
def switch_org(org_slug: str):
    """Switch to a different organization.

    Changes the active organization context for all CLI commands.
    You must be a member of the target organization.

    ORG_SLUG is the URL-friendly identifier of the organization.
    """
    cli_auth = CLIAuth()
    credentials = cli_auth.get_current_user()

    if not credentials:
        console.print("[yellow]Not logged in[/]")
        console.print("Run [blue]repotoire auth login[/] to authenticate")
        raise click.Abort()

    try:
        new_credentials = cli_auth.switch_org(org_slug)
        console.print(f"[green]✓[/] Switched to organization: [bold]{new_credentials.org_slug}[/]")
        console.print(f"  Plan: {new_credentials.tier.title()}")
    except AuthenticationError as e:
        console.print(f"[red]✗[/] Failed to switch organization: {e}")
        raise click.Abort()


@auth_group.command()
def status():
    """Show authentication status and token validity.

    Displays detailed information about the current authentication state,
    including token expiration time.
    """
    cli_auth = CLIAuth()
    credentials = cli_auth.get_current_user()

    if not credentials:
        console.print("[red]●[/] Not authenticated")
        console.print()
        console.print("Run [blue]repotoire auth login[/] to authenticate")
        return

    # Token status
    if credentials.is_expired():
        console.print("[red]●[/] Token expired")
        if credentials.refresh_token:
            console.print(
                "  [dim]Refresh token available - will auto-refresh on next command[/dim]"
            )
        else:
            console.print("  [dim]No refresh token - login required[/dim]")
    else:
        console.print("[green]●[/] Authenticated")
        # Calculate time until expiration
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        remaining = credentials.expires_at - now
        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)

        if hours > 0:
            console.print(f"  [dim]Token expires in {hours}h {minutes}m[/dim]")
        else:
            console.print(f"  [dim]Token expires in {minutes}m[/dim]")

    console.print()
    console.print(f"[bold]User:[/] {credentials.user_email}")

    if credentials.org_slug:
        console.print(f"[bold]Organization:[/] {credentials.org_slug}")
        console.print(f"[bold]Plan:[/] {credentials.tier.title()}")
    else:
        console.print("[dim]No organization selected[/dim]")
