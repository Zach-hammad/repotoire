"""Organization management CLI commands.

Allows users to list, switch, and view their current organization context.
This ensures CLI and web dashboard stay in sync.
"""

import json
from pathlib import Path
from typing import Optional

import click
import httpx
from rich.console import Console
from rich.table import Table

from repotoire.cli.auth import CLIAuth
from repotoire.cli.credentials import CredentialStore
from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console()

# Local storage for active org preference
ACTIVE_ORG_FILE = Path.home() / ".repotoire" / "active_org.json"


def _get_api_url() -> str:
    """Get the API URL."""
    import os
    return os.environ.get("REPOTOIRE_API_URL", "https://repotoire-api.fly.dev")


def _get_active_org() -> Optional[dict]:
    """Get the locally stored active organization."""
    if ACTIVE_ORG_FILE.exists():
        try:
            with open(ACTIVE_ORG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def _save_active_org(org_id: str, org_slug: str, org_name: str, plan: str) -> None:
    """Save the active organization locally."""
    ACTIVE_ORG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIVE_ORG_FILE, "w") as f:
        json.dump({
            "org_id": org_id,
            "org_slug": org_slug,
            "org_name": org_name,
            "plan": plan,
        }, f, indent=2)
    # Secure the file
    ACTIVE_ORG_FILE.chmod(0o600)


def _clear_active_org() -> None:
    """Clear the locally stored active organization."""
    if ACTIVE_ORG_FILE.exists():
        ACTIVE_ORG_FILE.unlink()


@click.group(name="org")
def org_group():
    """Organization management commands.

    \b
    COMMANDS:
      list     List all organizations you belong to
      switch   Switch to a different organization
      current  Show the current active organization

    \b
    EXAMPLES:
      $ repotoire org list              # List all your orgs
      $ repotoire org switch acme-corp  # Switch to acme-corp
      $ repotoire org current           # Show current org
    """
    pass


@org_group.command(name="list")
def list_orgs():
    """List all organizations you belong to.

    \b
    Shows all organizations where you have membership,
    along with your role and the organization's plan tier.
    """
    cli_auth = CLIAuth()
    api_key = cli_auth.get_api_key()

    if not api_key:
        console.print("[red]✗[/] Not logged in. Run [blue]repotoire login[/] first.")
        raise click.Abort()

    api_url = _get_api_url()
    active_org = _get_active_org()

    try:
        # Get current org from API key validation (this tells us which org the key is for)
        with httpx.Client(timeout=30.0) as client:
            # First validate the API key to get user info
            validate_resp = client.post(
                f"{api_url}/api/v1/cli/auth/validate-key",
                headers={"Authorization": f"Bearer {api_key}"},
            )

            if validate_resp.status_code != 200:
                console.print(f"[red]✗[/] Failed to validate API key: {validate_resp.text}")
                raise click.Abort()

            key_info = validate_resp.json()
            current_org_slug = key_info.get("org_slug")

            # Now list all organizations
            orgs_resp = client.get(
                f"{api_url}/api/v1/organizations",
                headers={"Authorization": f"Bearer {api_key}"},
            )

            if orgs_resp.status_code == 200:
                orgs = orgs_resp.json()
            else:
                # API key might not have org list permission, show just current org
                orgs = [{
                    "slug": key_info.get("org_slug"),
                    "name": key_info.get("org_slug", "").replace("-", " ").title(),
                    "plan_tier": key_info.get("plan", "free"),
                    "member_count": None,
                }]

        if not orgs:
            console.print("[yellow]You don't belong to any organizations.[/]")
            console.print("Create one at [blue]https://repotoire.com/dashboard[/]")
            return

        table = Table(title="Your Organizations")
        table.add_column("", style="green", width=3)  # Active indicator
        table.add_column("Slug", style="cyan")
        table.add_column("Name")
        table.add_column("Plan", style="magenta")
        table.add_column("Members", justify="right")

        for org in orgs:
            slug = org.get("slug", "")
            is_active = slug == current_org_slug
            active_marker = "●" if is_active else ""

            member_count = org.get("member_count")
            members_str = str(member_count) if member_count is not None else "-"

            table.add_row(
                active_marker,
                slug,
                org.get("name", ""),
                org.get("plan_tier", "free"),
                members_str,
            )

        console.print(table)
        console.print()
        console.print(f"[dim]● = current organization (from API key)[/]")
        console.print(f"[dim]Switch with: repotoire org switch <slug>[/]")

    except httpx.RequestError as e:
        console.print(f"[red]✗[/] Failed to connect to API: {e}")
        raise click.Abort()


@org_group.command(name="switch")
@click.argument("org_slug")
def switch_org(org_slug: str):
    """Switch to a different organization.

    \b
    This changes which organization's data you access via CLI.
    You must be a member of the target organization.

    \b
    ARGUMENTS:
      ORG_SLUG  The slug of the organization to switch to

    \b
    EXAMPLE:
      $ repotoire org switch acme-corp
    """
    cli_auth = CLIAuth()
    api_key = cli_auth.get_api_key()

    if not api_key:
        console.print("[red]✗[/] Not logged in. Run [blue]repotoire login[/] first.")
        raise click.Abort()

    api_url = _get_api_url()

    console.print(f"Switching to organization [cyan]{org_slug}[/]...")

    try:
        with httpx.Client(timeout=30.0) as client:
            # Call the switch org endpoint
            resp = client.post(
                f"{api_url}/api/v1/cli/auth/switch-org",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"org_slug": org_slug},
            )

            if resp.status_code == 200:
                data = resp.json()
                _save_active_org(
                    org_id=data.get("org_id", ""),
                    org_slug=data.get("org_slug", org_slug),
                    org_name=org_slug.replace("-", " ").title(),
                    plan=data.get("tier", "free"),
                )
                console.print(f"[green]✓[/] Switched to [cyan]{org_slug}[/]")
                console.print(f"  Plan: {data.get('tier', 'free')}")
                console.print()
                console.print("[dim]Note: Your API key determines org access.[/]")
                console.print("[dim]To use a different org, create an API key for that org.[/]")
            elif resp.status_code == 403:
                console.print(f"[red]✗[/] You are not a member of organization '{org_slug}'")
                console.print("Run [blue]repotoire org list[/] to see your organizations")
                raise click.Abort()
            elif resp.status_code == 404:
                console.print(f"[red]✗[/] Organization '{org_slug}' not found")
                raise click.Abort()
            else:
                error = resp.json().get("detail", resp.text)
                console.print(f"[red]✗[/] Failed to switch: {error}")
                raise click.Abort()

    except httpx.RequestError as e:
        console.print(f"[red]✗[/] Failed to connect to API: {e}")
        raise click.Abort()


@org_group.command(name="current")
def current_org():
    """Show the current active organization.

    \b
    Displays which organization your CLI commands will operate on.
    This is determined by your API key.
    """
    cli_auth = CLIAuth()
    api_key = cli_auth.get_api_key()

    if not api_key:
        console.print("[red]✗[/] Not logged in. Run [blue]repotoire login[/] first.")
        raise click.Abort()

    api_url = _get_api_url()

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{api_url}/api/v1/cli/auth/validate-key",
                headers={"Authorization": f"Bearer {api_key}"},
            )

            if resp.status_code != 200:
                console.print(f"[red]✗[/] Failed to validate API key")
                raise click.Abort()

            data = resp.json()

            console.print("[green]●[/] Current Organization")
            console.print(f"  [bold]Slug:[/] {data.get('org_slug', 'unknown')}")
            console.print(f"  [bold]Plan:[/] {data.get('plan', 'free')}")

            if data.get("user"):
                user = data["user"]
                console.print(f"  [bold]User:[/] {user.get('email', 'unknown')}")

            console.print()
            console.print("[dim]Your API key is scoped to this organization.[/]")
            console.print("[dim]To access a different org, create an API key for that org[/]")
            console.print("[dim]at: https://repotoire.com/dashboard/settings/api-keys[/]")

    except httpx.RequestError as e:
        console.print(f"[red]✗[/] Failed to connect to API: {e}")
        raise click.Abort()
