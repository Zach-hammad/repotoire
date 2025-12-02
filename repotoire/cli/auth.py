"""CLI authentication via Clerk OAuth device flow.

Handles browser-based OAuth authentication and local credential storage.
Tokens are stored in ~/.repotoire/credentials.json and auto-refreshed.
"""

import json
import os
import secrets
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import click
import httpx
from rich.console import Console

from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console()

# Credential storage location
CREDENTIALS_DIR = Path.home() / ".repotoire"
CREDENTIALS_FILE = CREDENTIALS_DIR / "credentials.json"

# OAuth callback server
CALLBACK_PORT = 8787
CALLBACK_PATH = "/callback"

# Timeout for OAuth flow
OAUTH_TIMEOUT_SECONDS = 300  # 5 minutes


class AuthenticationError(Exception):
    """Exception raised for authentication failures."""

    pass


@dataclass
class CLICredentials:
    """Stored CLI credentials."""

    access_token: str
    refresh_token: Optional[str]
    expires_at: datetime
    user_id: str
    user_email: str
    org_id: Optional[str]
    org_slug: Optional[str]
    tier: str  # "free", "pro", "enterprise"

    def is_expired(self) -> bool:
        """Check if token is expired or expiring soon (5 min buffer)."""
        buffer = timedelta(minutes=5)
        return datetime.now(timezone.utc) >= (self.expires_at - buffer)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
            "user_id": self.user_id,
            "user_email": self.user_email,
            "org_id": self.org_id,
            "org_slug": self.org_slug,
            "tier": self.tier,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CLICredentials":
        """Deserialize from dict."""
        expires_at = data["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            user_id=data["user_id"],
            user_email=data["user_email"],
            org_id=data.get("org_id"),
            org_slug=data.get("org_slug"),
            tier=data.get("tier", "free"),
        )


@dataclass
class OAuthCallbackResult:
    """Result from OAuth callback."""

    code: Optional[str] = None
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

        # Get code and state
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        if not code:
            self.server.callback_result = OAuthCallbackResult(
                error="No authorization code received"
            )  # type: ignore
            self._send_response("Authentication failed: no code received.", error=True)
            return

        self.server.callback_result = OAuthCallbackResult(code=code, state=state)  # type: ignore
        self._send_response("Authentication successful! You can close this window.")

    def _send_response(self, message: str, error: bool = False) -> None:
        """Send HTML response to browser."""
        status = "error" if error else "success"
        color = "#dc2626" if error else "#16a34a"

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
            background-color: #f3f4f6;
        }}
        .container {{
            text-align: center;
            padding: 2rem;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .status {{
            color: {color};
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 1rem;
        }}
        .message {{
            color: #4b5563;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="status">{status.upper()}</div>
        <p class="message">{message}</p>
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

    def __init__(self, api_url: Optional[str] = None):
        """Initialize CLI auth handler.

        Args:
            api_url: Base URL for the Repotoire API (default: from env or https://api.repotoire.dev)
        """
        self.api_url = api_url or os.environ.get("REPOTOIRE_API_URL", "https://api.repotoire.dev")

    def login(self) -> CLICredentials:
        """Initiate browser-based OAuth login.

        1. Start local callback server on port 8787
        2. Open browser to Clerk OAuth URL with redirect to localhost
        3. Receive callback with auth code
        4. Exchange code for tokens via API
        5. Store credentials locally

        Returns:
            CLICredentials on successful login

        Raises:
            AuthenticationError: If login fails
        """
        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)

        # Initialize OAuth flow via API
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{self.api_url}/api/v1/cli/auth/init",
                    json={
                        "state": state,
                        "redirect_uri": f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}",
                    },
                )
                response.raise_for_status()
                init_data = response.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to initialize OAuth: {e}")
            raise AuthenticationError(f"Failed to connect to Repotoire API: {e}")

        auth_url = init_data["auth_url"]
        server_state = init_data["state"]

        # Start callback server
        server = HTTPServer(("localhost", CALLBACK_PORT), OAuthCallbackHandler)
        server.callback_result = OAuthCallbackResult()  # type: ignore
        server.timeout = OAUTH_TIMEOUT_SECONDS

        # Open browser
        console.print("[dim]Opening browser for authentication...[/dim]")
        webbrowser.open(auth_url)
        console.print(
            f"[dim]Waiting for authentication (timeout: {OAUTH_TIMEOUT_SECONDS}s)...[/dim]"
        )
        console.print("[dim]If browser didn't open, visit:[/dim]")
        console.print(f"[blue]{auth_url}[/blue]")

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

        if not result.code:
            raise AuthenticationError("No authorization code received")

        # Verify state
        if result.state != server_state:
            raise AuthenticationError("State mismatch - possible CSRF attack")

        # Exchange code for tokens
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{self.api_url}/api/v1/cli/auth/token",
                    json={"code": result.code, "state": server_state},
                )
                response.raise_for_status()
                token_data = response.json()
        except httpx.HTTPStatusError as e:
            error_detail = "Unknown error"
            try:
                error_detail = e.response.json().get("detail", error_detail)
            except Exception:
                pass
            raise AuthenticationError(f"Failed to exchange code for token: {error_detail}")
        except httpx.HTTPError as e:
            raise AuthenticationError(f"Failed to connect to Repotoire API: {e}")

        # Parse expires_at
        expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00"))

        credentials = CLICredentials(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            user_id=token_data["user_id"],
            user_email=token_data["user_email"],
            org_id=token_data.get("org_id"),
            org_slug=token_data.get("org_slug"),
            tier=token_data.get("tier", "free"),
        )

        # Save credentials
        _save_credentials(credentials)

        logger.info(f"CLI login successful for user {credentials.user_email}")
        return credentials

    def logout(self) -> None:
        """Clear stored credentials."""
        if CREDENTIALS_FILE.exists():
            CREDENTIALS_FILE.unlink()
            logger.info("CLI credentials cleared")
        console.print("[green]✓[/] Logged out successfully")

    def get_current_user(self) -> Optional[CLICredentials]:
        """Load and validate stored credentials.

        Returns:
            CLICredentials if valid credentials exist, None otherwise
        """
        credentials = _load_credentials()
        if credentials is None:
            return None

        # If expired and we have a refresh token, try to refresh
        if credentials.is_expired() and credentials.refresh_token:
            try:
                return self.refresh_token(credentials)
            except AuthenticationError:
                logger.warning("Failed to refresh token, credentials expired")
                return None

        return credentials

    def refresh_token(self, credentials: CLICredentials) -> CLICredentials:
        """Refresh expired access token using refresh token.

        Args:
            credentials: Current credentials with refresh token

        Returns:
            Updated credentials with new access token

        Raises:
            AuthenticationError: If refresh fails
        """
        if not credentials.refresh_token:
            raise AuthenticationError("No refresh token available")

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{self.api_url}/api/v1/cli/auth/refresh",
                    headers={"Authorization": f"Bearer {credentials.access_token}"},
                    json={"refresh_token": credentials.refresh_token},
                )
                response.raise_for_status()
                token_data = response.json()
        except httpx.HTTPStatusError as e:
            error_detail = "Unknown error"
            try:
                error_detail = e.response.json().get("detail", error_detail)
            except Exception:
                pass
            raise AuthenticationError(f"Failed to refresh token: {error_detail}")
        except httpx.HTTPError as e:
            raise AuthenticationError(f"Failed to connect to Repotoire API: {e}")

        # Parse expires_at
        expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00"))

        new_credentials = CLICredentials(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token", credentials.refresh_token),
            expires_at=expires_at,
            user_id=token_data["user_id"],
            user_email=token_data["user_email"],
            org_id=token_data.get("org_id"),
            org_slug=token_data.get("org_slug"),
            tier=token_data.get("tier", "free"),
        )

        # Save updated credentials
        _save_credentials(new_credentials)

        logger.info(f"CLI token refreshed for user {new_credentials.user_email}")
        return new_credentials

    def require_auth(self) -> CLICredentials:
        """Get credentials or prompt for login.

        Returns:
            Valid CLICredentials

        Raises:
            click.Abort: If user declines to login
        """
        creds = self.get_current_user()
        if creds is None:
            console.print("[yellow]⚠[/] Not logged in")
            if click.confirm("Would you like to login now?"):
                return self.login()
            raise click.Abort()

        if creds.is_expired():
            if creds.refresh_token:
                try:
                    return self.refresh_token(creds)
                except AuthenticationError:
                    pass
            console.print("[yellow]⚠[/] Session expired, please login again")
            return self.login()

        return creds

    def switch_org(self, org_slug: str) -> CLICredentials:
        """Switch to a different organization.

        Args:
            org_slug: Slug of the organization to switch to

        Returns:
            Updated credentials for the new organization

        Raises:
            AuthenticationError: If switch fails
        """
        credentials = self.get_current_user()
        if not credentials:
            raise AuthenticationError("Not logged in")

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{self.api_url}/api/v1/cli/auth/switch-org",
                    headers={"Authorization": f"Bearer {credentials.access_token}"},
                    json={"org_slug": org_slug},
                )
                response.raise_for_status()
                token_data = response.json()
        except httpx.HTTPStatusError as e:
            error_detail = "Unknown error"
            try:
                error_detail = e.response.json().get("detail", error_detail)
            except Exception:
                pass
            raise AuthenticationError(f"Failed to switch organization: {error_detail}")
        except httpx.HTTPError as e:
            raise AuthenticationError(f"Failed to connect to Repotoire API: {e}")

        # Parse expires_at
        expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00"))

        new_credentials = CLICredentials(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            user_id=token_data["user_id"],
            user_email=token_data["user_email"],
            org_id=token_data.get("org_id"),
            org_slug=token_data.get("org_slug"),
            tier=token_data.get("tier", "free"),
        )

        # Save updated credentials
        _save_credentials(new_credentials)

        logger.info(f"Switched to organization {new_credentials.org_slug}")
        return new_credentials


def _save_credentials(credentials: CLICredentials) -> None:
    """Save credentials to disk.

    Args:
        credentials: Credentials to save
    """
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(credentials.to_dict(), indent=2))
    # Restrict permissions (owner read/write only)
    CREDENTIALS_FILE.chmod(0o600)
    logger.debug(f"Credentials saved to {CREDENTIALS_FILE}")


def _load_credentials() -> Optional[CLICredentials]:
    """Load credentials from disk.

    Returns:
        CLICredentials if file exists and is valid, None otherwise
    """
    if not CREDENTIALS_FILE.exists():
        return None
    try:
        data = json.loads(CREDENTIALS_FILE.read_text())
        return CLICredentials.from_dict(data)
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"Failed to load credentials: {e}")
        return None


def is_offline_mode() -> bool:
    """Check if running in offline mode.

    Offline mode is enabled by:
    - REPOTOIRE_OFFLINE=true environment variable
    - --offline flag in CLI (checked by caller)

    Returns:
        True if offline mode is enabled
    """
    return os.environ.get("REPOTOIRE_OFFLINE", "").lower() in ("true", "1", "yes")
