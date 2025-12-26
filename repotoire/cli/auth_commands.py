"""Authentication CLI commands."""

import click
from rich.console import Console

from repotoire.cli.auth import AuthenticationError, CLIAuth
from repotoire.cli.credentials import mask_api_key
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
    API key is stored securely in system keyring (or ~/.repotoire/credentials).
    """
    cli_auth = CLIAuth()

    # Check if already logged in
    existing_key = cli_auth.get_api_key()
    if existing_key:
        masked = mask_api_key(existing_key)
        console.print(f"[dim]Already logged in with API key: {masked}[/dim]")
        if not click.confirm("Login again to replace existing credentials?"):
            return

    console.print("Opening browser for authentication...")

    try:
        api_key = cli_auth.login()
    except AuthenticationError as e:
        console.print(f"[red]✗[/] Authentication failed: {e}")
        raise click.Abort()

    masked = mask_api_key(api_key)
    source = cli_auth.get_credential_source()

    console.print(f"\n[green]✓[/] Logged in successfully")
    console.print(f"  API Key: {masked}")
    if source:
        console.print(f"  Stored in: {source}")

    console.print("\n[dim]You can now run:[/dim]")
    console.print("  [blue]repotoire ingest /path/to/repo[/]")
    console.print("  [blue]repotoire analyze /path/to/repo[/]")


@auth_group.command()
def logout():
    """Clear stored credentials.

    Removes API key from system keyring or ~/.repotoire/credentials.
    """
    cli_auth = CLIAuth()
    cli_auth.logout()


@auth_group.command()
def whoami():
    """Show current authentication status.

    Displays information about the stored API key and where it's stored.
    """
    cli_auth = CLIAuth()
    cli_auth.whoami()


@auth_group.command()
def status():
    """Show authentication status.

    Same as 'whoami' - displays current authentication state.
    """
    cli_auth = CLIAuth()
    cli_auth.whoami()
