"""Factory for creating graph database clients.

This module provides the main entry point for creating database clients.
Users connect via API key to Repotoire Cloud.

Usage:
    export REPOTOIRE_API_KEY=ak_your_key
    client = create_client()  # Auto-connects to Repotoire Cloud

Multi-tenant mode (SaaS):
    client = create_client(org_id=org.id, org_slug=org.slug)
"""

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import UUID

if TYPE_CHECKING:
    from repotoire.config import RepotoireConfig
    from repotoire.graph.falkordb_client import FalkorDBClient

import httpx
from rich.console import Console

from repotoire.graph.base import DatabaseClient
from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console()

# Config location
REPOTOIRE_DIR = Path.home() / ".repotoire"
CREDENTIALS_FILE = REPOTOIRE_DIR / "credentials"
CLOUD_CACHE_FILE = REPOTOIRE_DIR / "cloud_auth_cache.json"
CLOUD_CACHE_TTL = 900  # 15 minutes

# Default API URL (Fly.io API server)
DEFAULT_API_URL = "https://repotoire-api.fly.dev"

# Thread-safe lock for cloud auth cache file I/O
_cloud_cache_lock = threading.Lock()


def get_api_key() -> Optional[str]:
    """Get API key from environment or stored credentials.

    Checks in order:
    1. REPOTOIRE_API_KEY environment variable
    2. System keyring (if available)
    3. ~/.repotoire/credentials file

    Returns:
        API key if found, None otherwise
    """
    from repotoire.cli.credentials import CredentialStore

    store = CredentialStore()
    return store.get_api_key()


def save_api_key(api_key: str) -> str:
    """Save API key to secure storage.

    Uses system keyring if available, falls back to file.

    Args:
        api_key: The API key to store

    Returns:
        Description of where the key was saved
    """
    from repotoire.cli.credentials import CredentialStore, StorageBackend

    store = CredentialStore()
    backend = store.save_api_key(api_key)

    if backend == StorageBackend.KEYRING:
        return "system keyring"
    return "~/.repotoire/credentials"


def remove_api_key() -> bool:
    """Remove stored API key from all backends.

    Returns:
        True if credentials were removed, False if none existed
    """
    from repotoire.cli.credentials import CredentialStore

    store = CredentialStore()
    return store.clear()


def get_credential_source() -> Optional[str]:
    """Get a description of where the current API key is coming from.

    Returns:
        Description string or None if no credentials
    """
    from repotoire.cli.credentials import CredentialStore

    store = CredentialStore()
    return store.get_source()


# =============================================================================
# Cloud Mode Exceptions
# =============================================================================


class CloudAuthenticationError(Exception):
    """Raised when cloud authentication fails.

    Includes user-friendly messages with suggestions for fixing the issue.
    """

    def __init__(
        self,
        message: str,
        suggestion: Optional[str] = None,
        retry_after: Optional[int] = None,
    ):
        self.message = message
        self.suggestion = suggestion
        self.retry_after = retry_after
        super().__init__(message)


class CloudConnectionError(Exception):
    """Raised when unable to connect to Repotoire Cloud."""

    def __init__(self, message: str, cause: Optional[Exception] = None):
        self.message = message
        self.cause = cause
        super().__init__(message)


class ConfigurationError(Exception):
    """Raised when database configuration is missing or invalid."""

    def __init__(self, message: str):
        super().__init__(message)


# =============================================================================
# Cloud Auth Cache
# =============================================================================


@dataclass
class UserInfo:
    """User information."""

    email: str
    name: Optional[str] = None


@dataclass
class CloudAuthInfo:
    """Cached cloud authentication info."""

    org_id: str
    org_slug: str
    plan: str
    features: List[str]
    db_config: Dict[str, Any]
    cached_at: float
    user: Optional[UserInfo] = None

    def is_expired(self) -> bool:
        """Check if cache has expired."""
        return time.time() > (self.cached_at + CLOUD_CACHE_TTL)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON storage."""
        result = {
            "org_id": self.org_id,
            "org_slug": self.org_slug,
            "plan": self.plan,
            "features": self.features,
            "db_config": self.db_config,
            "cached_at": self.cached_at,
        }
        if self.user:
            result["user"] = {"email": self.user.email, "name": self.user.name}
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CloudAuthInfo":
        """Deserialize from dict."""
        user = None
        if "user" in data and data["user"]:
            user = UserInfo(
                email=data["user"]["email"],
                name=data["user"].get("name"),
            )
        return cls(
            org_id=data["org_id"],
            org_slug=data["org_slug"],
            plan=data["plan"],
            features=data["features"],
            db_config=data["db_config"],
            cached_at=data["cached_at"],
            user=user,
        )


def _get_cache_key(api_key: str) -> str:
    """Generate a cache key from API key (hashed for security)."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _get_cached_auth(api_key: str) -> Optional[CloudAuthInfo]:
    """Get cached auth info if valid.

    Thread-safe: Uses lock for file I/O.

    Args:
        api_key: The API key to look up cache for

    Returns:
        CloudAuthInfo if cache exists and is valid, None otherwise
    """
    with _cloud_cache_lock:
        if not CLOUD_CACHE_FILE.exists():
            return None

        try:
            data = json.loads(CLOUD_CACHE_FILE.read_text())
            cache_key = _get_cache_key(api_key)

            if cache_key not in data:
                return None

            auth_info = CloudAuthInfo.from_dict(data[cache_key])

            if auth_info.is_expired():
                logger.debug("Cloud auth cache expired")
                return None

            logger.debug(f"Using cached cloud auth for org {auth_info.org_slug}")
            return auth_info

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Failed to read cloud auth cache: {e}")
            return None


def _cache_auth(api_key: str, auth_info: CloudAuthInfo) -> None:
    """Cache auth info with TTL.

    Thread-safe: Uses lock for file I/O.

    Args:
        api_key: The API key to cache auth for
        auth_info: The auth info to cache
    """
    with _cloud_cache_lock:
        try:
            REPOTOIRE_DIR.mkdir(parents=True, exist_ok=True)

            # Load existing cache or create new
            if CLOUD_CACHE_FILE.exists():
                try:
                    data = json.loads(CLOUD_CACHE_FILE.read_text())
                except json.JSONDecodeError:
                    data = {}
            else:
                data = {}

            # Add/update this key's cache
            cache_key = _get_cache_key(api_key)
            data[cache_key] = auth_info.to_dict()

            # Write cache with restricted permissions
            CLOUD_CACHE_FILE.write_text(json.dumps(data, indent=2))
            CLOUD_CACHE_FILE.chmod(0o600)

            logger.debug(f"Cached cloud auth for org {auth_info.org_slug}")

        except Exception as e:
            logger.debug(f"Failed to cache cloud auth: {e}")


def _invalidate_cache(api_key: str) -> None:
    """Invalidate cache for an API key (e.g., on 401 response).

    Thread-safe: Uses lock for file I/O.

    Args:
        api_key: The API key to invalidate cache for
    """
    with _cloud_cache_lock:
        if not CLOUD_CACHE_FILE.exists():
            return

        try:
            data = json.loads(CLOUD_CACHE_FILE.read_text())
            cache_key = _get_cache_key(api_key)

            if cache_key in data:
                del data[cache_key]
                CLOUD_CACHE_FILE.write_text(json.dumps(data, indent=2))
                logger.debug("Invalidated cloud auth cache")

        except Exception as e:
            logger.debug(f"Failed to invalidate cache: {e}")


def create_client(
    uri: Optional[str] = None,
    db_type: Optional[str] = None,
    org_id: Optional[UUID] = None,
    org_slug: Optional[str] = None,
    show_cloud_indicator: bool = True,
    **kwargs,
) -> DatabaseClient:
    """Create a graph database client.

    Requires REPOTOIRE_API_KEY to connect to Repotoire Cloud.

    Args:
        uri: Database connection URI (internal use only).
        db_type: Database type (internal use only).
        org_id: Organization UUID for multi-tenant isolation (SaaS).
        org_slug: Organization slug for naming (used with org_id).
        show_cloud_indicator: Whether to print cloud mode indicator (default: True).
        **kwargs: Additional arguments passed to the client constructor.

    Returns:
        DatabaseClient instance

    Environment Variables:
        REPOTOIRE_API_KEY: API key (required)

    Raises:
        CloudAuthenticationError: If API key is invalid
        CloudConnectionError: If cannot connect to Repotoire Cloud
        ConfigurationError: If API key is not set

    Examples:
        export REPOTOIRE_API_KEY=ak_your_key
        client = create_client()
    """
    # Multi-tenant mode: delegate to GraphClientFactory
    if org_id is not None:
        from repotoire.graph.tenant_factory import get_factory
        return get_factory().get_client(org_id, org_slug)

    # API key required
    api_key = get_api_key()
    if not api_key:
        raise ConfigurationError(
            "API key required.\n\n"
            "  1. Get your API key at: https://repotoire.com/settings/api-keys\n"
            "  2. Run: repotoire login ak_your_key\n"
            "  3. Run: repotoire analyze ./your-repo"
        )

    return create_cloud_client(api_key, show_indicator=show_cloud_indicator)


def create_cloud_client(
    api_key: str,
    show_indicator: bool = True,
    command: Optional[str] = None,
) -> DatabaseClient:
    """Create a cloud proxy client.

    Returns a client that proxies all graph operations through the
    Repotoire API. This allows the CLI to work without direct database
    connectivity.

    Automatically sets TenantContext from the validated API key,
    enabling tenant isolation throughout the request lifecycle.

    Args:
        api_key: Repotoire API key (starts with 'ak_' or 'rp_')
        show_indicator: Whether to print cloud mode indicator
        command: CLI command being executed (for audit logging)

    Returns:
        CloudProxyClient that proxies operations through the API

    Raises:
        CloudAuthenticationError: If API key is invalid or expired
        CloudConnectionError: If cannot connect to Repotoire Cloud
    """
    from repotoire.graph.cloud_client import CloudProxyClient
    from repotoire.tenant.resolver import set_tenant_from_auth_info

    # Check cache first
    auth_info = _get_cached_auth(api_key)
    used_cache = auth_info is not None

    if auth_info is None:
        # Validate API key against cloud API
        auth_info = _validate_api_key(api_key)

        # Cache the result
        _cache_auth(api_key, auth_info)

    # REPO-600: Automatically set TenantContext from API key validation
    # This enables tenant isolation throughout the CLI command lifecycle
    set_tenant_from_auth_info(auth_info)

    # Show cloud mode indicator
    if show_indicator:
        _print_cloud_indicator(auth_info)

    # Log connection to Neon (fire-and-forget, non-blocking)
    _log_cloud_connection(api_key, auth_info, cached=used_cache, command=command)

    # Create cloud proxy client
    return CloudProxyClient(api_key=api_key)


def _validate_api_key(api_key: str) -> CloudAuthInfo:
    """Validate API key against the Repotoire Cloud API.

    Args:
        api_key: API key to validate

    Returns:
        CloudAuthInfo with org details and db config

    Raises:
        CloudAuthenticationError: If key is invalid
        CloudConnectionError: If cannot connect to cloud
    """
    api_url = os.environ.get("REPOTOIRE_API_URL", DEFAULT_API_URL)
    endpoint = f"{api_url}/api/v1/cli/auth/validate-key"

    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
            )

            if response.status_code == 401:
                # Invalid or expired key
                try:
                    error_data = response.json()
                    error_msg = error_data.get("detail", {}).get("error", "Invalid API key")
                except Exception:
                    error_msg = "Invalid or expired API key"

                _invalidate_cache(api_key)

                raise CloudAuthenticationError(
                    f"❌ Authentication failed: {error_msg}",
                    suggestion=(
                        "To fix this:\n"
                        "  1. Check your API key at https://repotoire.com/settings/api-keys\n"
                        "  2. Generate a new key if needed\n"
                        "  3. Set: export REPOTOIRE_API_KEY=ak_your_new_key"
                    ),
                )

            if response.status_code == 429:
                # Rate limited
                try:
                    error_data = response.json()
                    retry_after = error_data.get("retry_after", 60)
                except Exception:
                    retry_after = 60

                raise CloudAuthenticationError(
                    f"⏳ Too many requests. Try again in {retry_after} seconds.",
                    retry_after=retry_after,
                )

            response.raise_for_status()

            data = response.json()

            # Parse user info if available
            user = None
            if "user" in data and data["user"]:
                user = UserInfo(
                    email=data["user"]["email"],
                    name=data["user"].get("name"),
                )

            return CloudAuthInfo(
                org_id=data["org_id"],
                org_slug=data["org_slug"],
                plan=data["plan"],
                features=data.get("features", []),
                db_config=data["db_config"],
                cached_at=time.time(),
                user=user,
            )

    except httpx.ConnectError as e:
        raise CloudConnectionError(
            "Could not connect to Repotoire Cloud.\n"
            "Check your internet connection.",
            cause=e,
        )
    except httpx.TimeoutException as e:
        raise CloudConnectionError(
            "Connection to Repotoire Cloud timed out.\n"
            "The service may be temporarily unavailable. Please try again.",
            cause=e,
        )
    except (CloudAuthenticationError, CloudConnectionError):
        raise
    except httpx.HTTPStatusError as e:
        raise CloudConnectionError(
            f"Repotoire Cloud returned an error: {e.response.status_code}",
            cause=e,
        )
    except Exception as e:
        logger.error(f"Unexpected error validating API key: {e}")
        raise CloudConnectionError(
            f"Unexpected error connecting to Repotoire Cloud: {e}",
            cause=e,
        )


def _print_cloud_indicator(auth_info: CloudAuthInfo) -> None:
    """Print cloud mode indicator with org and plan info."""
    plan_colors = {
        "free": "dim",
        "pro": "blue",
        "enterprise": "magenta",
    }
    plan_color = plan_colors.get(auth_info.plan, "dim")

    console.print(
        f"☁️  [green]Connected to Repotoire Cloud[/green] "
        f"(org: [cyan]{auth_info.org_slug}[/cyan], "
        f"plan: [{plan_color}]{auth_info.plan}[/{plan_color}])"
    )


def _log_cloud_connection(
    api_key: str,
    auth_info: CloudAuthInfo,
    cached: bool = False,
    command: Optional[str] = None,
) -> None:
    """Log cloud connection to Neon audit log (fire-and-forget).

    This is a non-blocking call that logs the connection asynchronously.
    Failures are silently ignored to not affect CLI operation.

    Args:
        api_key: API key used for authentication
        auth_info: Validated auth info
        cached: Whether cached auth was used
        command: CLI command being executed
    """
    import threading

    def _do_log():
        try:
            api_url = os.environ.get("REPOTOIRE_API_URL", DEFAULT_API_URL)
            endpoint = f"{api_url}/api/v1/cli/auth/log-connection"

            # Get CLI version if available
            cli_version = None
            try:
                from repotoire import __version__
                cli_version = __version__
            except ImportError:
                pass

            with httpx.Client(timeout=10.0) as client:
                client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "org_id": auth_info.org_id,
                        "org_slug": auth_info.org_slug,
                        "plan": auth_info.plan,
                        "cached": cached,
                        "cli_version": cli_version,
                        "command": command,
                    },
                )
                # Fire-and-forget: don't check response
        except Exception as e:
            # Silently ignore failures - this is non-critical
            logger.debug(f"Failed to log cloud connection: {e}")

    # Run in background thread to not block CLI
    thread = threading.Thread(target=_do_log, daemon=True)
    thread.start()


def create_kuzu_client(
    db_path: Optional[str] = None,
    repository_path: Optional[str] = None,
) -> DatabaseClient:
    """Create a Kùzu embedded graph database client for local-first analysis.

    Kùzu is a lightweight, embedded graph database that:
    - Requires no Docker or external server
    - Uses Cypher queries (same as FalkorDB)
    - Disk-based storage (low RAM usage)
    - Runs in-process (fast, no network latency)

    Args:
        db_path: Explicit path for Kùzu database directory
        repository_path: Repository path (used to auto-detect db_path)

    Returns:
        KuzuClient instance

    Examples:
        # Auto-detect from repository
        client = create_kuzu_client(repository_path="./my-repo")

        # Explicit path
        client = create_kuzu_client(db_path="/path/to/kuzu_db")
    """
    try:
        from repotoire.graph.kuzu_client import KuzuClient
        from repotoire.graph.kuzu_client import create_kuzu_client as _create
        return _create(db_path=db_path, repository_path=repository_path)
    except ImportError:
        raise ConfigurationError(
            "Kùzu is not installed.\n\n"
            "Install with: pip install kuzu\n\n"
            "Then run: repotoire analyze ./your-repo --local"
        )


def create_local_client(
    repository_path: str = ".",
    backend: str = "kuzu",
) -> DatabaseClient:
    """Create a local graph database client (no cloud required).

    This is the entry point for local-first analysis without API key.

    Args:
        repository_path: Path to repository being analyzed
        backend: Backend to use ('kuzu' or 'falkordb')

    Returns:
        DatabaseClient for local analysis

    Examples:
        client = create_local_client("./my-repo")
    """
    if backend == "kuzu":
        return create_kuzu_client(repository_path=repository_path)
    elif backend == "falkordb":
        return create_falkordb_client()
    else:
        raise ConfigurationError(f"Unknown local backend: {backend}")


def create_falkordb_client(
    config: Optional["RepotoireConfig"] = None,
    graph_name: str = "repotoire",
    **overrides,
) -> "FalkorDBClient":
    """Create a FalkorDBClient from configuration.

    This is the centralized factory function for creating FalkorDBClient instances.
    It uses RepotoireConfig as the single source of truth for connection settings,
    retry behavior, and circuit breaker configuration.

    Args:
        config: RepotoireConfig instance. If None, loads from environment/file.
        graph_name: Name of the graph to use (default: "repotoire")
        **overrides: Override any config value (e.g., max_retries=2 for tests)

    Returns:
        FalkorDBClient instance configured from config

    Examples:
        # Use default config (from env/file)
        client = create_falkordb_client()

        # Use specific config
        config = load_config()
        client = create_falkordb_client(config=config)

        # Override specific settings
        client = create_falkordb_client(max_retries=2, graph_name="test")

        # Override with specific host (e.g., from CLI args)
        client = create_falkordb_client(host="custom-host.example.com", port=6380)
    """
    from repotoire.config import load_config
    from repotoire.graph.falkordb_client import FalkorDBClient

    # Load config if not provided
    if config is None:
        config = load_config()

    db = config.database

    # Build kwargs from config, allowing overrides
    client_kwargs = {
        # Connection settings
        "host": overrides.get("host", db.host),
        "port": overrides.get("port", db.port),
        "graph_name": graph_name,
        "password": overrides.get("password", db.password),
        "ssl": overrides.get("ssl", db.ssl),
        "socket_timeout": overrides.get("socket_timeout", db.socket_timeout),
        "socket_connect_timeout": overrides.get("socket_connect_timeout", db.socket_connect_timeout),
        "max_connections": overrides.get("max_connections", db.max_connections),
        # Retry settings
        "max_retries": overrides.get("max_retries", db.max_retries),
        "retry_base_delay": overrides.get("retry_base_delay", db.retry_base_delay),
        "retry_backoff_factor": overrides.get("retry_backoff_factor", db.retry_backoff_factor),
        "retry_jitter": overrides.get("retry_jitter", db.retry_jitter),
        "retry_max_delay": overrides.get("retry_max_delay", db.retry_max_delay),
        # Query settings
        "default_query_timeout": overrides.get("default_query_timeout", db.default_query_timeout),
        # Circuit breaker settings
        "circuit_breaker_threshold": overrides.get("circuit_breaker_threshold", db.circuit_breaker_threshold),
        "circuit_breaker_timeout": overrides.get("circuit_breaker_timeout", db.circuit_breaker_timeout),
    }

    # Note: health_check_interval is set internally by FalkorDBClient, not as constructor arg

    logger.debug(
        f"Creating FalkorDBClient: host={client_kwargs['host']}, port={client_kwargs['port']}, "
        f"graph={graph_name}, max_retries={client_kwargs['max_retries']}, "
        f"circuit_breaker_threshold={client_kwargs['circuit_breaker_threshold']}"
    )

    return FalkorDBClient(**client_kwargs)


def is_cloud_mode() -> bool:
    """Check if cloud mode is enabled.

    Returns:
        True if API key is available
    """
    return bool(get_api_key())


def get_cloud_auth_info() -> Optional[CloudAuthInfo]:
    """Get cached cloud auth info if available.

    Returns:
        CloudAuthInfo if API key is available and validated, None otherwise
    """
    api_key = get_api_key()
    if not api_key:
        return None
    return _get_cached_auth(api_key)
