"""GitHub App client for API interactions.

This module provides a client for GitHub App API calls, including
JWT generation for app authentication and installation token management.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import jwt

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# GitHub API base URL
GITHUB_API_BASE = "https://api.github.com"


class GitHubAppClient:
    """Client for GitHub App API calls.

    Handles JWT generation for app-level authentication and
    installation access token management for repository access.

    Usage:
        client = GitHubAppClient()
        token, expires_at = await client.get_installation_token(12345)
        repos = await client.list_installation_repos(token)
    """

    def __init__(
        self,
        app_id: Optional[str] = None,
        private_key: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ):
        """Initialize the GitHub App client.

        Args:
            app_id: GitHub App ID. Defaults to GITHUB_APP_ID env var.
            private_key: RSA private key for JWT signing.
                Defaults to GITHUB_APP_PRIVATE_KEY env var.
            webhook_secret: Webhook secret for signature verification.
                Defaults to GITHUB_WEBHOOK_SECRET env var.

        Raises:
            ValueError: If required credentials are not provided.
        """
        self.app_id = app_id or os.getenv("GITHUB_APP_ID")
        self.private_key = private_key or os.getenv("GITHUB_APP_PRIVATE_KEY")
        self.webhook_secret = webhook_secret or os.getenv("GITHUB_WEBHOOK_SECRET")

        if not self.app_id:
            raise ValueError("GITHUB_APP_ID environment variable not set")
        if not self.private_key:
            raise ValueError("GITHUB_APP_PRIVATE_KEY environment variable not set")

        # Handle escaped newlines in private key
        if "\\n" in self.private_key:
            self.private_key = self.private_key.replace("\\n", "\n")

    def generate_jwt(self) -> str:
        """Generate a JWT for GitHub App authentication.

        The JWT is used for app-level API calls and for obtaining
        installation access tokens. JWTs are valid for up to 10 minutes.

        Returns:
            A signed JWT string.
        """
        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued 60 seconds ago (clock skew tolerance)
            "exp": now + 600,  # Expires in 10 minutes
            "iss": self.app_id,
        }

        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def get_installation_token(
        self, installation_id: int
    ) -> tuple[str, datetime]:
        """Get an access token for a GitHub App installation.

        Installation tokens provide access to repositories and are
        valid for 1 hour.

        Args:
            installation_id: The GitHub App installation ID.

        Returns:
            Tuple of (access_token, expires_at datetime).

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        jwt_token = self.generate_jwt()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()

            data = response.json()
            token = data["token"]
            # Parse ISO 8601 datetime
            expires_at = datetime.fromisoformat(
                data["expires_at"].replace("Z", "+00:00")
            )

            logger.info(f"Obtained installation token for {installation_id}")
            return token, expires_at

    async def get_installation(self, installation_id: int) -> dict[str, Any]:
        """Get information about a GitHub App installation.

        Args:
            installation_id: The GitHub App installation ID.

        Returns:
            Installation data including account info.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        jwt_token = self.generate_jwt()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/app/installations/{installation_id}",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            return response.json()

    async def list_installation_repos(
        self,
        access_token: str,
        per_page: int = 100,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """List repositories accessible to an installation.

        Args:
            access_token: Installation access token.
            per_page: Number of results per page (max 100).
            page: Page number for pagination.

        Returns:
            List of repository data dictionaries.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/installation/repositories",
                params={"per_page": per_page, "page": page},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()

            data = response.json()
            return data.get("repositories", [])

    async def list_all_installation_repos(
        self, access_token: str
    ) -> list[dict[str, Any]]:
        """List all repositories accessible to an installation.

        Handles pagination automatically to fetch all repositories.

        Args:
            access_token: Installation access token.

        Returns:
            List of all repository data dictionaries.
        """
        all_repos: list[dict[str, Any]] = []
        page = 1

        while True:
            repos = await self.list_installation_repos(
                access_token, per_page=100, page=page
            )
            if not repos:
                break
            all_repos.extend(repos)
            if len(repos) < 100:
                break
            page += 1

        logger.info(f"Listed {len(all_repos)} repositories for installation")
        return all_repos

    async def get_repo_contents(
        self,
        access_token: str,
        owner: str,
        repo: str,
        path: str = "",
    ) -> dict[str, Any]:
        """Get repository file or directory contents.

        Args:
            access_token: Installation access token.
            owner: Repository owner (user or organization).
            repo: Repository name.
            path: Path to file or directory (empty for root).

        Returns:
            File or directory contents data.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            return response.json()

    async def get_repo(
        self,
        access_token: str,
        owner: str,
        repo: str,
    ) -> dict[str, Any]:
        """Get repository information.

        Args:
            access_token: Installation access token.
            owner: Repository owner (user or organization).
            repo: Repository name.

        Returns:
            Repository data.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            return response.json()

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify a GitHub webhook signature.

        Args:
            payload: Raw request body bytes.
            signature: X-Hub-Signature-256 header value.

        Returns:
            True if signature is valid, False otherwise.
        """
        import hmac
        import hashlib

        if not self.webhook_secret:
            logger.warning("GITHUB_WEBHOOK_SECRET not set, skipping verification")
            return False

        if not signature.startswith("sha256="):
            return False

        expected_signature = hmac.new(
            self.webhook_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(
            signature.removeprefix("sha256="),
            expected_signature,
        )

    def is_token_expiring_soon(
        self,
        expires_at: datetime,
        threshold_minutes: int = 5,
    ) -> bool:
        """Check if a token is expiring soon.

        Args:
            expires_at: Token expiration datetime (must be timezone-aware).
            threshold_minutes: Minutes before expiry to consider "soon".

        Returns:
            True if token expires within threshold, False otherwise.
        """
        now = datetime.now(timezone.utc)
        threshold = timedelta(minutes=threshold_minutes)
        return expires_at - now < threshold


def get_github_client() -> GitHubAppClient:
    """FastAPI dependency that provides GitHub App client.

    Usage:
        @router.get("/repos")
        async def list_repos(
            github: GitHubAppClient = Depends(get_github_client)
        ):
            ...

    Returns:
        GitHubAppClient: A configured GitHub client.
    """
    return GitHubAppClient()
