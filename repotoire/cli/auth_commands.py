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
    """Authentication and account management commands.

    \b
    COMMANDS:
      login    Authenticate via browser OAuth or API key
      logout   Remove stored credentials
      whoami   Show current authentication status

    \b
    EXAMPLES:
      $ repotoire auth login           # Browser OAuth
      $ repotoire auth whoami          # Check status

    \b
    Note: You can also use top-level shortcuts:
      $ repotoire login
      $ repotoire whoami
    """
    pass


@auth_group.command()
def login():
    """Login to Repotoire via browser OAuth.

    \b
    Opens your default browser for secure OAuth authentication.
    The authentication URL will be displayed if your browser doesn't open.

    \b
    Credentials are stored securely in:
      - System keyring (macOS Keychain, Windows Credential Manager, etc.)
      - Fallback: ~/.repotoire/credentials (chmod 600)

    \b
    For CI/CD or headless environments, use:
      $ repotoire login <api-key>

    \b
    Get your API key at: https://repotoire.com/settings/api-keys
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

    console.print("\n[green]✓[/] Logged in successfully")
    console.print(f"  API Key: {masked}")
    if source:
        console.print(f"  Stored in: {source}")

    console.print("\n[dim]You can now run:[/dim]")
    console.print("  [blue]repotoire ingest /path/to/repo[/]")
    console.print("  [blue]repotoire analyze /path/to/repo[/]")


@auth_group.command()
def logout():
    """Remove stored credentials and log out.

    \b
    Clears credentials from:
      - System keyring (if used)
      - ~/.repotoire/credentials (if used)

    \b
    This does NOT revoke the API key on the server. To revoke API keys:
      https://repotoire.com/settings/api-keys
    """
    cli_auth = CLIAuth()
    cli_auth.logout()


@auth_group.command()
def whoami():
    """Display current authentication status.

    \b
    Shows:
      - Whether you're logged in
      - Your organization and plan
      - Where credentials are stored
      - Masked API key

    \b
    EXAMPLE OUTPUT:
      Logged in as John Doe (john@example.com)
        Organization: my-org (Pro plan)
        API Key: rp_live_abc...xyz
        Credentials stored in: system keyring
    """
    cli_auth = CLIAuth()
    cli_auth.whoami()


# Note: 'status' command removed - it was a duplicate of 'whoami'.
# Use 'repotoire whoami' or 'repotoire auth whoami' instead.
