"""CLI authentication via Clerk OAuth device flow.

Handles browser-based OAuth authentication and local credential storage.
API keys are stored securely in system keyring (or file fallback).
"""

import os
import secrets
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse

import click
from rich.console import Console

from repotoire.cli.credentials import CredentialStore, mask_api_key
from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console()

# OAuth callback server
CALLBACK_PORT = 8787
CALLBACK_PATH = "/callback"

# Timeout for OAuth flow
OAUTH_TIMEOUT_SECONDS = 300  # 5 minutes

# Default web app URL
DEFAULT_WEB_URL = "https://repotoire.com"


class AuthenticationError(Exception):
    """Exception raised for authentication failures."""

    pass


@dataclass
class OAuthCallbackResult:
    """Result from OAuth callback."""

    api_key: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""

    def log_message(self, format: str, *args) -> None:
        """Suppress HTTP server logs."""
        pass

    def do_GET(self) -> None:
        """Handle GET request (OAuth callback)."""
        parsed = urlparse(self.path)

        if parsed.path != CALLBACK_PATH:
            self.send_error(404, "Not Found")
            return

        # Parse query parameters
        params = parse_qs(parsed.query)

        # Check for error
        if "error" in params:
            error = params["error"][0]
            error_desc = params.get("error_description", ["Unknown error"])[0]
            self.server.callback_result = OAuthCallbackResult(error=f"{error}: {error_desc}")  # type: ignore
            self._send_response("Authentication failed. You can close this window.", error=True)
            return

        # Get api_key and state
        api_key = params.get("api_key", [None])[0]
        state = params.get("state", [None])[0]

        if not api_key:
            self.server.callback_result = OAuthCallbackResult(
                error="No API key received"
            )  # type: ignore
            self._send_response("Authentication failed: no API key received.", error=True)
            return

        self.server.callback_result = OAuthCallbackResult(api_key=api_key, state=state)  # type: ignore
        self._send_response("Authentication successful! You can close this window.")

    def _send_response(self, message: str, error: bool = False) -> None:
        """Send HTML response to browser."""
        status = "error" if error else "success"
        color = "#dc2626" if error else "#16a34a"
        icon = "✗" if error else "✓"

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Repotoire CLI - Authentication</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background-color: #0a0a0a;
            color: #fafafa;
        }}
        .container {{
            text-align: center;
            padding: 2rem;
            background: #171717;
            border-radius: 12px;
            border: 1px solid #262626;
            max-width: 400px;
        }}
        .icon {{
            font-size: 3rem;
            margin-bottom: 1rem;
        }}
        .status {{
            color: {color};
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
        }}
        .message {{
            color: #a3a3a3;
            margin-bottom: 1rem;
        }}
        .hint {{
            color: #525252;
            font-size: 0.875rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">{icon}</div>
        <div class="status">{status.upper()}</div>
        <p class="message">{message}</p>
        <p class="hint">Return to your terminal to continue.</p>
    </div>
</body>
</html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html.encode())


class CLIAuth:
    """Handle CLI authentication flow."""

    def __init__(self, web_url: Optional[str] = None):
        """Initialize CLI auth handler.

        Args:
            web_url: Base URL for the Repotoire web app (default: from env or https://repotoire.com)
        """
        self.web_url = web_url or os.environ.get("REPOTOIRE_WEB_URL", DEFAULT_WEB_URL)
        self.credential_store = CredentialStore()

    def login(self) -> str:
        """Initiate browser-based OAuth login.

        Flow:
        1. Start local callback server on port 8787
        2. Open browser to Clerk sign-in with redirect to /cli/callback
        3. User signs in, page creates API key
        4. Page redirects to localhost:8787/callback with API key
        5. Store API key in keyring

        Returns:
            API key on successful login

        Raises:
            AuthenticationError: If login fails
        """
        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)

        # Build auth URL - redirect through sign-in to CLI callback
        # The redirect_url must be fully URL-encoded so Clerk preserves the nested params
        callback_params = urlencode({
            "state": state,
            "port": str(CALLBACK_PORT),
        })
        redirect_url = f"/cli/callback?{callback_params}"
        # URL-encode the entire redirect_url so &port isn't interpreted as a top-level param
        auth_url = f"{self.web_url}/sign-in?redirect_url={quote(redirect_url, safe='')}"

        # Start callback server
        try:
            server = HTTPServer(("localhost", CALLBACK_PORT), OAuthCallbackHandler)
        except OSError as e:
            if "Address already in use" in str(e):
                raise AuthenticationError(
                    f"Port {CALLBACK_PORT} is already in use. "
                    "Close any other CLI login processes and try again."
                )
            raise AuthenticationError(f"Failed to start callback server: {e}")

        server.callback_result = OAuthCallbackResult()  # type: ignore
        server.timeout = OAUTH_TIMEOUT_SECONDS

        # Open browser
        console.print("[dim]Opening browser for authentication...[/dim]")
        webbrowser.open(auth_url)
        console.print(
            f"[dim]Waiting for authentication (timeout: {OAUTH_TIMEOUT_SECONDS}s)...[/dim]"
        )
        console.print("[dim]If browser didn't open, visit:[/dim]")
        console.print(f"[link={auth_url}]{auth_url}[/link]")

        # Handle single request (blocking)
        try:
            server.handle_request()
        except Exception as e:
            logger.error(f"Error handling OAuth callback: {e}")
            raise AuthenticationError(f"Error during authentication: {e}")
        finally:
            server.server_close()

        result: OAuthCallbackResult = server.callback_result  # type: ignore

        if result.error:
            raise AuthenticationError(result.error)

        if not result.api_key:
            raise AuthenticationError("No API key received")

        # Verify state
        if result.state != state:
            raise AuthenticationError("State mismatch - possible CSRF attack")

        # Store the API key
        backend = self.credential_store.save_api_key(result.api_key)
        logger.info(f"CLI login successful, API key stored in {backend.value}")

        return result.api_key

    def logout(self) -> bool:
        """Clear stored credentials.

        Returns:
            True if credentials were cleared, False if none existed
        """
        cleared = self.credential_store.clear()
        if cleared:
            logger.info("CLI credentials cleared")
            console.print("[green]✓[/] Logged out successfully")
        else:
            console.print("[dim]No credentials to clear[/dim]")
        return cleared

    def get_api_key(self) -> Optional[str]:
        """Get stored API key.

        Returns:
            API key if stored, None otherwise
        """
        return self.credential_store.get_api_key()

    def get_credential_source(self) -> Optional[str]:
        """Get description of where credentials are stored.

        Returns:
            Description like "system keyring" or file path, None if not stored
        """
        return self.credential_store.get_source()

    def require_auth(self) -> str:
        """Get API key or prompt for login.

        Returns:
            Valid API key

        Raises:
            click.Abort: If user declines to login
        """
        api_key = self.get_api_key()
        if api_key is None:
            console.print("[yellow]⚠[/] Not logged in")
            if click.confirm("Would you like to login now?"):
                return self.login()
            raise click.Abort()

        return api_key

    def whoami(self) -> None:
        """Display current authentication status."""
        api_key = self.get_api_key()

        if not api_key:
            console.print("[yellow]Not logged in[/]")
            console.print("Run [blue]repotoire login[/] to authenticate")
            return

        source = self.get_credential_source()
        masked = mask_api_key(api_key)

        console.print("[green]●[/] Authenticated")
        console.print(f"[bold]API Key:[/] {masked}")
        if source:
            console.print(f"[bold]Stored in:[/] {source}")


def is_offline_mode() -> bool:
    """Check if running in offline mode.

    Offline mode is enabled by:
    - REPOTOIRE_OFFLINE=true environment variable
    - --offline flag in CLI (checked by caller)

    Returns:
        True if offline mode is enabled
    """
    return os.environ.get("REPOTOIRE_OFFLINE", "").lower() in ("true", "1", "yes")
