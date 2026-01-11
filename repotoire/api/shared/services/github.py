"""GitHub App client for API interactions.

This module provides a client for GitHub App API calls, including
JWT generation for app authentication and installation token management.
Includes Redis-based token caching for performance.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import jwt

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Token cache using Redis (lazy initialized)
_redis_client: Optional[Any] = None


def _get_redis_client() -> Optional[Any]:
    """Get Redis client for token caching (lazy initialization)."""
    global _redis_client
    if _redis_client is None:
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return None
        try:
            import redis
            _redis_client = redis.from_url(
                redis_url,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
                decode_responses=True,
            )
            # Test connection
            _redis_client.ping()
        except Exception as e:
            logger.warning(f"Redis not available for token caching: {e}")
            return None
    return _redis_client


def _cache_key(installation_id: int) -> str:
    """Generate cache key for installation token."""
    return f"github:token:{installation_id}"

# GitHub API base URL
GITHUB_API_BASE = "https://api.github.com"

# Default timeouts for GitHub API calls (in seconds)
# Connect timeout: 10s, Read timeout: 30s
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


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
        self, installation_id: int,
        use_cache: bool = True,
    ) -> tuple[str, datetime]:
        """Get an access token for a GitHub App installation.

        Installation tokens provide access to repositories and are
        valid for 1 hour. Tokens are cached in Redis to avoid unnecessary
        API calls.

        Args:
            installation_id: The GitHub App installation ID.
            use_cache: Whether to use Redis caching (default: True).

        Returns:
            Tuple of (access_token, expires_at datetime).

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        # Try cache first
        if use_cache:
            redis = _get_redis_client()
            if redis:
                cache_key = _cache_key(installation_id)
                try:
                    cached = redis.hgetall(cache_key)
                    if cached and cached.get("token") and cached.get("expires_at"):
                        expires_at = datetime.fromisoformat(cached["expires_at"])
                        # Return cached token if it's still valid (with 5 min buffer)
                        if not self.is_token_expiring_soon(expires_at, threshold_minutes=5):
                            logger.debug(f"Using cached token for installation {installation_id}")
                            return cached["token"], expires_at
                except Exception as e:
                    logger.warning(f"Cache read failed: {e}")

        # Fetch new token from GitHub
        jwt_token = self.generate_jwt()

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
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

            # Cache the token
            if use_cache:
                redis = _get_redis_client()
                if redis:
                    cache_key = _cache_key(installation_id)
                    try:
                        # Calculate TTL (token valid for ~1 hour, we cache for 55 min)
                        ttl_seconds = int((expires_at - datetime.now(timezone.utc)).total_seconds()) - 300
                        if ttl_seconds > 0:
                            redis.hset(cache_key, mapping={
                                "token": token,
                                "expires_at": expires_at.isoformat(),
                            })
                            redis.expire(cache_key, ttl_seconds)
                            logger.debug(f"Cached token for installation {installation_id} (TTL: {ttl_seconds}s)")
                    except Exception as e:
                        logger.warning(f"Cache write failed: {e}")

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

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
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
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
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
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
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
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
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

    # =========================================================================
    # PR Creation Methods
    # =========================================================================

    async def get_ref(
        self,
        access_token: str,
        owner: str,
        repo: str,
        ref: str,
    ) -> dict[str, Any]:
        """Get a git reference (branch or tag).

        Args:
            access_token: Installation access token.
            owner: Repository owner.
            repo: Repository name.
            ref: Reference name (e.g., "heads/main" for main branch).

        Returns:
            Reference data including SHA.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/ref/{ref}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            return response.json()

    async def create_ref(
        self,
        access_token: str,
        owner: str,
        repo: str,
        ref: str,
        sha: str,
    ) -> dict[str, Any]:
        """Create a new git reference (branch).

        Args:
            access_token: Installation access token.
            owner: Repository owner.
            repo: Repository name.
            ref: Full reference name (e.g., "refs/heads/new-branch").
            sha: SHA to point the reference to.

        Returns:
            Created reference data.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "ref": ref,
                    "sha": sha,
                },
            )
            response.raise_for_status()
            return response.json()

    async def get_file_sha(
        self,
        access_token: str,
        owner: str,
        repo: str,
        path: str,
        ref: Optional[str] = None,
    ) -> Optional[str]:
        """Get the SHA of a file (needed for updates).

        Args:
            access_token: Installation access token.
            owner: Repository owner.
            repo: Repository name.
            path: File path in repository.
            ref: Branch or commit reference.

        Returns:
            File SHA if exists, None if file doesn't exist.
        """
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                params = {"ref": ref} if ref else {}
                response = await client.get(
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json().get("sha")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_file_content(
        self,
        access_token: str,
        owner: str,
        repo: str,
        path: str,
        ref: Optional[str] = None,
    ) -> Optional[str]:
        """Get the content of a file from GitHub.

        Args:
            access_token: Installation access token.
            owner: Repository owner.
            repo: Repository name.
            path: File path in repository.
            ref: Branch or commit reference.

        Returns:
            File content as string if exists, None if file doesn't exist.
        """
        import base64

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                params = {"ref": ref} if ref else {}
                response = await client.get(
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                data = response.json()
                content_b64 = data.get("content", "")
                # GitHub returns content with newlines, remove them before decoding
                content_b64 = content_b64.replace("\n", "")
                return base64.b64decode(content_b64).decode("utf-8")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def create_or_update_file(
        self,
        access_token: str,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        file_sha: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create or update a file in a repository.

        Args:
            access_token: Installation access token.
            owner: Repository owner.
            repo: Repository name.
            path: File path in repository.
            content: File content (will be base64 encoded).
            message: Commit message.
            branch: Branch to commit to.
            file_sha: SHA of existing file (required for updates).

        Returns:
            Commit data.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        import base64

        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if file_sha:
            payload["sha"] = file_sha

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.put(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def create_pull_request(
        self,
        access_token: str,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = False,
    ) -> dict[str, Any]:
        """Create a pull request.

        Args:
            access_token: Installation access token.
            owner: Repository owner.
            repo: Repository name.
            title: PR title.
            body: PR description (Markdown supported).
            head: Branch containing changes.
            base: Branch to merge into.
            draft: Create as draft PR.

        Returns:
            Created PR data including number and URL.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "title": title,
                    "body": body,
                    "head": head,
                    "base": base,
                    "draft": draft,
                },
            )
            response.raise_for_status()
            return response.json()

    async def create_fix_pr(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        base_branch: str,
        fix_branch: str,
        file_path: str,
        fixed_code: str,
        title: str,
        description: str,
    ) -> dict[str, Any]:
        """Create a PR for an auto-fix change.

        This is a high-level method that handles the full workflow:
        1. Get installation token
        2. Get base branch SHA
        3. Create fix branch
        4. Update file with fix
        5. Create PR

        Args:
            installation_id: GitHub App installation ID.
            owner: Repository owner.
            repo: Repository name.
            base_branch: Branch to base changes on (e.g., "main").
            fix_branch: Branch name for the fix.
            file_path: Path to the file being fixed.
            fixed_code: The fixed code content.
            title: PR title.
            description: PR description.

        Returns:
            Dict with PR URL and number.

        Raises:
            httpx.HTTPStatusError: If any API request fails.
        """
        # Get installation token
        token, _ = await self.get_installation_token(installation_id)

        # Get base branch SHA
        base_ref = await self.get_ref(token, owner, repo, f"heads/{base_branch}")
        base_sha = base_ref["object"]["sha"]

        # Create fix branch
        try:
            await self.create_ref(
                token, owner, repo, f"refs/heads/{fix_branch}", base_sha
            )
            logger.info(f"Created branch {fix_branch} from {base_branch}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                # Branch already exists, continue
                logger.info(f"Branch {fix_branch} already exists, reusing")
            else:
                raise

        # Get current file SHA (needed for update)
        file_sha = await self.get_file_sha(
            token, owner, repo, file_path, ref=fix_branch
        )

        # Update file with fix
        commit_message = f"fix: {title}\n\n{description}\n\nGenerated by Repotoire Auto-Fix"
        await self.create_or_update_file(
            token,
            owner,
            repo,
            file_path,
            fixed_code,
            commit_message,
            fix_branch,
            file_sha,
        )
        logger.info(f"Updated {file_path} on branch {fix_branch}")

        # Create PR
        pr_body = f"""## Auto-Fix: {title}

{description}

---

### Changes
- **File**: `{file_path}`
- **Type**: Auto-generated fix

### Review Checklist
- [ ] Code changes look correct
- [ ] Tests pass
- [ ] No unintended side effects

---
*Generated by [Repotoire](https://repotoire.com) Auto-Fix*
"""
        pr_data = await self.create_pull_request(
            token,
            owner,
            repo,
            title=f"fix: {title}",
            body=pr_body,
            head=fix_branch,
            base=base_branch,
        )

        logger.info(f"Created PR #{pr_data['number']}: {pr_data['html_url']}")

        return {
            "pr_number": pr_data["number"],
            "pr_url": pr_data["html_url"],
            "branch": fix_branch,
        }


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
