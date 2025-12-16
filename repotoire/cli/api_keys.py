"""CLI commands for managing Clerk API keys."""

import os
import click
from rich.console import Console
from rich.table import Table

console = Console()


def get_clerk_client():
    """Get Clerk SDK client."""
    from clerk_backend_api import Clerk

    secret_key = os.getenv("CLERK_SECRET_KEY")
    if not secret_key:
        raise click.ClickException(
            "CLERK_SECRET_KEY not set.\n"
            "Get it from: dashboard.clerk.com → Your App → API Keys"
        )
    return Clerk(bearer_auth=secret_key)


@click.group()
def api_keys():
    """Manage Repotoire API keys."""
    pass


@api_keys.command("create")
@click.option("--name", "-n", required=True, help="Name for the API key")
@click.option("--user-id", "-u", help="User ID to scope the key to (user_xxx)")
@click.option("--org-id", "-o", help="Organization ID to scope the key to (org_xxx)")
@click.option(
    "--scopes",
    "-s",
    multiple=True,
    default=["read:code", "write:analysis"],
    help="Scopes for the key (can specify multiple)",
)
@click.option(
    "--expires",
    "-e",
    type=int,
    default=None,
    help="Seconds until expiration (default: never)",
)
def create_key(name: str, user_id: str, org_id: str, scopes: tuple, expires: int):
    """Create a new API key.

    Examples:
        repotoire api-keys create --name "My Key" --user-id user_abc123
        repotoire api-keys create --name "Org Key" --org-id org_xyz789 --scopes read:code
    """
    if not user_id and not org_id:
        raise click.ClickException("Must specify either --user-id or --org-id")

    subject = org_id if org_id else user_id
    clerk = get_clerk_client()

    import json
    import re

    try:
        # Build kwargs, only include expiration if set
        kwargs = {
            "name": name,
            "subject": subject,
            "scopes": list(scopes),
        }
        if expires:
            kwargs["seconds_until_expiration"] = expires

        try:
            api_key = clerk.api_keys.create_api_key(**kwargs)
            # Normal response
            key_data = {
                "name": api_key.name,
                "id": api_key.id,
                "subject": api_key.subject,
                "scopes": api_key.scopes or [],
                "secret": api_key.secret,
            }
        except Exception as sdk_err:
            # Clerk SDK bug: throws error on 201 Created
            # Parse the JSON from the error message
            err_str = str(sdk_err)
            if "Status 201" in err_str and "Body:" in err_str:
                json_match = re.search(r'Body: ({.*})', err_str)
                if json_match:
                    key_data = json.loads(json_match.group(1))
                else:
                    raise sdk_err
            else:
                raise sdk_err

        console.print("\n[green bold]API Key Created Successfully![/]\n")
        console.print(f"[bold]Name:[/] {key_data['name']}")
        console.print(f"[bold]ID:[/] {key_data['id']}")
        console.print(f"[bold]Subject:[/] {key_data['subject']}")
        console.print(f"[bold]Scopes:[/] {', '.join(key_data.get('scopes') or [])}")
        console.print()
        console.print("[yellow bold]Secret (save this - shown only once!):[/]")
        console.print(f"[cyan]{key_data['secret']}[/]")
        console.print()
        console.print("[dim]Set it with: export REPOTOIRE_API_KEY=<secret>[/]")

    except Exception as e:
        raise click.ClickException(f"Failed to create API key: {e}")


@api_keys.command("list")
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option("--org-id", "-o", help="Filter by organization ID")
@click.option("--include-invalid", is_flag=True, help="Include revoked/expired keys")
def list_keys(user_id: str, org_id: str, include_invalid: bool):
    """List API keys.

    Examples:
        repotoire api-keys list --user-id user_abc123
        repotoire api-keys list --org-id org_xyz789 --include-invalid
    """
    if not user_id and not org_id:
        raise click.ClickException("Must specify either --user-id or --org-id")

    subject = org_id if org_id else user_id
    clerk = get_clerk_client()

    try:
        result = clerk.api_keys.get_api_keys(
            subject=subject,
            include_invalid=include_invalid,
        )

        if not result.data:
            console.print("[yellow]No API keys found.[/]")
            return

        table = Table(title=f"API Keys for {subject}")
        table.add_column("ID", style="dim")
        table.add_column("Name")
        table.add_column("Scopes")
        table.add_column("Status")
        table.add_column("Created")

        for key in result.data:
            status = "[green]Active[/]"
            if key.revoked:
                status = "[red]Revoked[/]"
            elif key.expired:
                status = "[yellow]Expired[/]"

            table.add_row(
                key.id[:20] + "...",
                key.name,
                ", ".join(key.scopes or []),
                status,
                str(key.created_at)[:10] if key.created_at else "?",
            )

        console.print(table)

    except Exception as e:
        raise click.ClickException(f"Failed to list API keys: {e}")


@api_keys.command("revoke")
@click.argument("key_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def revoke_key(key_id: str, yes: bool):
    """Revoke an API key.

    Examples:
        repotoire api-keys revoke apikey_abc123
    """
    if not yes:
        click.confirm(f"Revoke API key {key_id}?", abort=True)

    clerk = get_clerk_client()

    try:
        clerk.api_keys.revoke_api_key(api_key_id=key_id)
        console.print(f"[green]API key {key_id} revoked.[/]")

    except Exception as e:
        raise click.ClickException(f"Failed to revoke API key: {e}")


@api_keys.command("verify")
@click.argument("secret")
def verify_key(secret: str):
    """Verify an API key is valid.

    Examples:
        repotoire api-keys verify sk_live_xxxxx
    """
    clerk = get_clerk_client()

    try:
        api_key = clerk.api_keys.verify_api_key(secret=secret)

        console.print("\n[green bold]API Key Valid![/]\n")
        console.print(f"[bold]ID:[/] {api_key.id}")
        console.print(f"[bold]Name:[/] {api_key.name}")
        console.print(f"[bold]Subject:[/] {api_key.subject}")
        console.print(f"[bold]Scopes:[/] {', '.join(api_key.scopes or [])}")

    except Exception as e:
        console.print(f"[red bold]Invalid API Key:[/] {e}")
        raise SystemExit(1)
