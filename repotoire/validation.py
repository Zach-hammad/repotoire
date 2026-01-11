"""Input validation utilities with helpful error messages."""

import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# Environment Variable Validation
# =============================================================================


class EnvironmentConfigError(Exception):
    """Raised when environment configuration is invalid or missing.

    This exception includes details about what's missing and how to fix it.
    """

    def __init__(self, errors: list[str], warnings: list[str] | None = None):
        self.errors = errors
        self.warnings = warnings or []
        message = "Environment configuration errors:\n"
        message += "\n".join(f"  - {e}" for e in errors)
        if self.warnings:
            message += "\n\nWarnings:\n"
            message += "\n".join(f"  - {w}" for w in self.warnings)
        super().__init__(message)


def validate_environment(
    require_database: bool = True,
    require_clerk: bool = True,
    require_stripe: bool = False,
    require_falkordb: bool = False,
) -> dict[str, Any]:
    """Validate required environment variables at startup.

    This should be called during application startup to fail fast on
    misconfiguration. Different components can be enabled/disabled based
    on what the application needs.

    Args:
        require_database: Check DATABASE_URL is set
        require_clerk: Check CLERK_SECRET_KEY is set
        require_stripe: Check Stripe keys are set
        require_falkordb: Check FalkorDB credentials are set

    Returns:
        Dict with:
            - valid: True if all required vars are set
            - warnings: List of non-critical issues
            - environment: Current environment (development/staging/production)

    Raises:
        EnvironmentConfigError: If any required variables are missing or invalid
    """
    errors: list[str] = []
    warnings: list[str] = []
    environment = os.getenv("ENVIRONMENT", "development")

    # Database configuration
    if require_database:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            errors.append(
                "DATABASE_URL is required. "
                "Set it to your PostgreSQL connection string: "
                "postgresql://user:password@host:5432/dbname"
            )
        elif "password" in database_url and database_url.count(":") >= 2:
            # Basic check - if there's a password segment, ensure it's not a placeholder
            if any(
                placeholder in database_url.lower()
                for placeholder in ["your-password", "changeme", "password123", "xxx"]
            ):
                errors.append(
                    "DATABASE_URL contains a placeholder password. "
                    "Replace it with your actual database password."
                )

    # Clerk authentication
    if require_clerk:
        clerk_secret = os.getenv("CLERK_SECRET_KEY")
        if not clerk_secret:
            errors.append(
                "CLERK_SECRET_KEY is required for authentication. "
                "Get it from: https://dashboard.clerk.com -> Your App -> API Keys"
            )
        else:
            # Check for test vs live key mismatch
            if clerk_secret.startswith("sk_live_") and environment == "development":
                warnings.append(
                    "Using Clerk LIVE key in development environment. "
                    "Consider using a test key (sk_test_*) for development."
                )
            elif clerk_secret.startswith("sk_test_") and environment == "production":
                errors.append(
                    "Using Clerk TEST key in production environment. "
                    "Production requires a live key (sk_live_*)."
                )

    # Stripe billing
    if require_stripe:
        stripe_key = os.getenv("STRIPE_SECRET_KEY")
        if not stripe_key:
            errors.append(
                "STRIPE_SECRET_KEY is required for billing. "
                "Get it from: https://dashboard.stripe.com/apikeys"
            )
        else:
            # Check for test vs live key mismatch
            if stripe_key.startswith("sk_live_") and environment == "development":
                errors.append(
                    "SECURITY ERROR: Using Stripe LIVE key in development environment. "
                    "This could result in real charges. Use a test key (sk_test_*)."
                )
            elif stripe_key.startswith("sk_test_") and environment == "production":
                errors.append(
                    "Using Stripe TEST key in production environment. "
                    "Production requires a live key (sk_live_*)."
                )

        # Check webhook secret
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        if not webhook_secret:
            warnings.append(
                "STRIPE_WEBHOOK_SECRET is not set. "
                "Webhook signature verification will fail."
            )

    # FalkorDB graph database
    if require_falkordb:
        falkordb_password = os.getenv("FALKORDB_PASSWORD")
        if not falkordb_password:
            warnings.append(
                "FALKORDB_PASSWORD is not set. "
                "FalkorDB connections may fail if authentication is required."
            )

    # Optional but recommended settings
    if not os.getenv("SECRET_KEY"):
        warnings.append(
            "SECRET_KEY is not set. "
            "Generate one with: openssl rand -hex 32"
        )

    if not os.getenv("SENTRY_DSN") and environment == "production":
        warnings.append(
            "SENTRY_DSN is not set for production. "
            "Error tracking is disabled."
        )

    # Check for encryption keys that may be needed
    if os.getenv("GITHUB_APP_PRIVATE_KEY") and not os.getenv("GITHUB_TOKEN_ENCRYPTION_KEY"):
        warnings.append(
            "GITHUB_APP_PRIVATE_KEY is set but GITHUB_TOKEN_ENCRYPTION_KEY is not. "
            "GitHub tokens may not be properly encrypted at rest."
        )

    if errors:
        raise EnvironmentConfigError(errors, warnings)

    # Log warnings
    for warning in warnings:
        logger.warning(f"Environment config: {warning}")

    return {
        "valid": True,
        "warnings": warnings,
        "environment": environment,
    }


def mask_secret(secret: str, prefix_len: int = 8, suffix_len: int = 4) -> str:
    """Mask a secret value, showing only prefix and suffix.

    Args:
        secret: The secret string to mask
        prefix_len: Number of characters to show at start
        suffix_len: Number of characters to show at end

    Returns:
        Masked string like "sk_test_abc...xyz"

    Examples:
        >>> mask_secret("sk_test_abcdefghijklmnop")
        'sk_test_...mnop'
        >>> mask_secret("short")
        '****'
    """
    if not secret:
        return ""

    total_visible = prefix_len + suffix_len

    # If secret is too short, just mask it entirely
    if len(secret) <= total_visible:
        return "*" * len(secret)

    prefix = secret[:prefix_len]
    suffix = secret[-suffix_len:]
    return f"{prefix}...{suffix}"


class ValidationError(Exception):
    """Raised when input validation fails.

    This exception includes helpful error messages and suggestions for fixing the issue.
    """
    def __init__(self, message: str, suggestion: Optional[str] = None):
        self.message = message
        self.suggestion = suggestion
        full_message = message
        if suggestion:
            full_message += f"\n\n💡 Suggestion: {suggestion}"
        super().__init__(full_message)


def validate_repository_path(repo_path: str) -> Path:
    """Validate repository path exists and is accessible.

    Args:
        repo_path: Path to repository

    Returns:
        Resolved Path object

    Raises:
        ValidationError: If path is invalid or inaccessible
    """
    if not repo_path or not repo_path.strip():
        raise ValidationError(
            "Repository path cannot be empty",
            "Provide a valid path to your codebase directory"
        )

    path = Path(repo_path).expanduser()

    if not path.exists():
        raise ValidationError(
            f"Repository path does not exist: {repo_path}",
            f"Check the path and try again. Did you mean one of these?\n"
            f"  - {Path.cwd()} (current directory)\n"
            f"  - {Path.home()} (home directory)"
        )

    if not path.is_dir():
        raise ValidationError(
            f"Repository path must be a directory, not a file: {repo_path}",
            "Provide the path to the repository root directory, not a specific file"
        )

    # Check if path is readable
    if not os.access(path, os.R_OK):
        raise ValidationError(
            f"Repository path is not readable: {repo_path}",
            f"Check file permissions. Try: chmod +r {repo_path}"
        )

    # Check if directory is empty
    try:
        if not any(path.iterdir()):
            raise ValidationError(
                f"Repository directory is empty: {repo_path}",
                "Make sure you're pointing to a directory with source code files"
            )
    except PermissionError:
        raise ValidationError(
            f"Cannot list directory contents: {repo_path}",
            f"Check directory permissions. Try: chmod +rx {repo_path}"
        )

    return path


def validate_falkordb_host(host: str) -> str:
    """Validate FalkorDB host format.

    Args:
        host: FalkorDB hostname or IP address

    Returns:
        Validated host string

    Raises:
        ValidationError: If host format is invalid
    """
    if not host or not host.strip():
        raise ValidationError(
            "FalkorDB host cannot be empty",
            "Provide a valid hostname, e.g., localhost or falkordb.internal"
        )

    host = host.strip()

    # Check for common mistakes - bolt:// URI instead of plain host
    if host.startswith(("bolt://", "neo4j://", "redis://")):
        raise ValidationError(
            f"Host should be a hostname, not a URI: {host}",
            "Use just the hostname without scheme, e.g., 'localhost' not 'bolt://localhost'"
        )

    # Basic hostname validation
    if not re.match(r'^[a-zA-Z0-9._-]+$', host):
        raise ValidationError(
            f"Invalid hostname format: {host}",
            "Use a valid hostname like 'localhost', '192.168.1.1', or 'falkordb.internal'"
        )

    return host


def validate_falkordb_port(port: int) -> int:
    """Validate FalkorDB port number.

    Args:
        port: FalkorDB port number

    Returns:
        Validated port number

    Raises:
        ValidationError: If port is invalid
    """
    if port <= 0 or port > 65535:
        raise ValidationError(
            f"Port must be between 1 and 65535: {port}",
            "Use a valid port number. FalkorDB default is 6379"
        )

    return port


def validate_falkordb_password(password: Optional[str]) -> Optional[str]:
    """Validate FalkorDB password.

    Args:
        password: FalkorDB password (can be None for unauthenticated connections)

    Returns:
        Validated password string or None

    Raises:
        ValidationError: If password format is invalid
    """
    if password is None:
        return None

    if not password.strip():
        raise ValidationError(
            "FalkorDB password cannot be empty string",
            "Provide a valid password or omit for unauthenticated connections:\n"
            "  - Set FALKORDB_PASSWORD environment variable\n"
            "  - Add 'database.password' to .repotoirerc config file"
        )

    return password.strip()


def validate_output_path(output_path: str) -> Path:
    """Validate output file path is writable.

    Args:
        output_path: Path to output file

    Returns:
        Validated Path object

    Raises:
        ValidationError: If path is not writable
    """
    if not output_path or not output_path.strip():
        raise ValidationError(
            "Output path cannot be empty",
            "Provide a valid output file path, e.g., report.json"
        )

    path = Path(output_path).expanduser()

    # Check parent directory exists and is writable
    parent = path.parent

    if not parent.exists():
        raise ValidationError(
            f"Output directory does not exist: {parent}",
            f"Create the directory first: mkdir -p {parent}"
        )

    if not parent.is_dir():
        raise ValidationError(
            f"Output parent path is not a directory: {parent}",
            "Provide a path where the parent is a directory"
        )

    if not os.access(parent, os.W_OK):
        raise ValidationError(
            f"Output directory is not writable: {parent}",
            f"Check permissions. Try: chmod +w {parent}"
        )

    # Check if file already exists and is writable
    if path.exists():
        if path.is_dir():
            raise ValidationError(
                f"Output path is a directory, not a file: {output_path}",
                "Provide a file path, not a directory"
            )

        if not os.access(path, os.W_OK):
            raise ValidationError(
                f"Output file exists but is not writable: {output_path}",
                f"Check permissions. Try: chmod +w {output_path}"
            )

    return path


def validate_file_size_limit(max_size_mb: float) -> float:
    """Validate file size limit is reasonable.

    Args:
        max_size_mb: Maximum file size in megabytes

    Returns:
        Validated file size

    Raises:
        ValidationError: If size is invalid
    """
    if max_size_mb <= 0:
        raise ValidationError(
            f"File size limit must be positive: {max_size_mb}MB",
            "Use a positive value, e.g., 10.0 (MB)"
        )

    if max_size_mb > 1000:
        raise ValidationError(
            f"File size limit is unusually large: {max_size_mb}MB",
            "Consider using a smaller limit to avoid memory issues.\n"
            "Typical values: 10MB (default), 50MB (large files), 100MB (very large)"
        )

    return max_size_mb


def validate_batch_size(batch_size: int) -> int:
    """Validate batch size is reasonable.

    Args:
        batch_size: Number of entities per batch

    Returns:
        Validated batch size

    Raises:
        ValidationError: If batch size is invalid
    """
    if batch_size <= 0:
        raise ValidationError(
            f"Batch size must be positive: {batch_size}",
            "Use a positive integer, e.g., 100 (default)"
        )

    if batch_size < 10:
        raise ValidationError(
            f"Batch size is too small: {batch_size}",
            "Use at least 10 for reasonable performance.\n"
            "Recommended: 100 (default), 50 (small), 500 (large)"
        )

    if batch_size > 10000:
        raise ValidationError(
            f"Batch size is too large: {batch_size}",
            "Use a smaller batch size to avoid memory issues.\n"
            "Recommended: 100 (default), 500 (large), 1000 (very large)"
        )

    return batch_size


def validate_retry_config(max_retries: int, backoff_factor: float, base_delay: float) -> tuple[int, float, float]:
    """Validate retry configuration parameters.

    Args:
        max_retries: Maximum number of retry attempts
        backoff_factor: Exponential backoff multiplier
        base_delay: Base delay in seconds

    Returns:
        Tuple of validated parameters

    Raises:
        ValidationError: If parameters are invalid
    """
    if max_retries < 0:
        raise ValidationError(
            f"Max retries cannot be negative: {max_retries}",
            "Use 0 to disable retries, or a positive number (recommended: 3)"
        )

    if max_retries > 10:
        raise ValidationError(
            f"Max retries is unusually high: {max_retries}",
            "Consider using fewer retries to fail faster.\n"
            "Recommended: 3 (default), 5 (patient), 10 (very patient)"
        )

    if backoff_factor < 1.0:
        raise ValidationError(
            f"Backoff factor must be >= 1.0: {backoff_factor}",
            "Use at least 1.0 for linear backoff, 2.0 for exponential (recommended)"
        )

    if backoff_factor > 10.0:
        raise ValidationError(
            f"Backoff factor is unusually large: {backoff_factor}",
            "Consider using a smaller factor to avoid very long delays.\n"
            "Recommended: 2.0 (default), 1.5 (gentle), 3.0 (aggressive)"
        )

    if base_delay <= 0:
        raise ValidationError(
            f"Base delay must be positive: {base_delay}",
            "Use a positive value in seconds, e.g., 1.0 (default)"
        )

    if base_delay > 60:
        raise ValidationError(
            f"Base delay is unusually long: {base_delay}s",
            "Consider using a shorter delay.\n"
            "Recommended: 1.0s (default), 0.5s (fast), 2.0s (patient)"
        )

    return max_retries, backoff_factor, base_delay


def validate_falkordb_connection(
    host: str = "localhost",
    port: int = 6379,
    password: Optional[str] = None
) -> None:
    """Test FalkorDB connection is actually reachable.

    Args:
        host: FalkorDB hostname
        port: FalkorDB port
        password: FalkorDB password (optional)

    Raises:
        ValidationError: If connection cannot be established
    """
    # Validate parameters first
    host = validate_falkordb_host(host)
    port = validate_falkordb_port(port)
    password = validate_falkordb_password(password)

    try:
        import redis
        from falkordb import FalkorDB

        # Try to connect with a short timeout
        client = FalkorDB(
            host=host,
            port=port,
            password=password,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )

        try:
            # Verify connectivity by pinging
            client.connection.ping()
            logger.debug(f"Successfully validated FalkorDB connection to {host}:{port}")
        except redis.exceptions.AuthenticationError as e:
            raise ValidationError(
                "FalkorDB authentication failed",
                "Check your FalkorDB password:\n"
                "  - Verify the password is correct\n"
                "  - Check FALKORDB_PASSWORD environment variable\n"
                "  - Or set password in config file"
            ) from e
        except redis.exceptions.ConnectionError as e:
            raise ValidationError(
                f"Cannot connect to FalkorDB at {host}:{port}",
                "Ensure FalkorDB is running and accessible:\n"
                f"  - Start FalkorDB: docker run -p {port}:6379 falkordb/falkordb:latest\n"
                "  - Check firewall settings\n"
                "  - Verify the host and port are correct"
            ) from e
        finally:
            client.connection.close()

    except ImportError:
        raise ValidationError(
            "FalkorDB driver not installed",
            "Install the falkordb package: pip install falkordb"
        )
    except Exception as e:
        if isinstance(e, ValidationError):
            raise
        raise ValidationError(
            f"Failed to connect to FalkorDB: {e}",
            "Check your FalkorDB configuration and ensure the database is accessible"
        ) from e


# Backward compatibility aliases
validate_neo4j_credentials = validate_falkordb_password
validate_neo4j_connection = validate_falkordb_connection


def validate_identifier(name: str, context: str = "identifier") -> str:
    """Validate identifier is safe for use in Cypher queries.

    Prevents Cypher injection by ensuring identifiers only contain
    alphanumeric characters, underscores, and hyphens.

    Args:
        name: Identifier to validate (e.g., projection name, property name)
        context: Description of what this identifier is used for (for error messages)

    Returns:
        Validated identifier string

    Raises:
        ValidationError: If identifier contains invalid characters

    Examples:
        >>> validate_identifier("my-projection", "projection name")
        'my-projection'
        >>> validate_identifier("test123_data", "graph name")
        'test123_data'
        >>> validate_identifier("bad'; DROP TABLE", "name")
        ValidationError: Invalid name: bad'; DROP TABLE
    """
    if not name or not name.strip():
        raise ValidationError(
            f"{context.capitalize()} cannot be empty",
            f"Provide a valid {context}"
        )

    # Allow alphanumeric, underscores, and hyphens only
    # This prevents Cypher injection attacks
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        raise ValidationError(
            f"Invalid {context}: {name}",
            f"{context.capitalize()} must contain only letters, numbers, underscores, and hyphens.\n"
            f"This restriction prevents Cypher injection attacks.\n"
            f"Examples of valid {context}s: 'my-projection', 'data_graph', 'test123'"
        )

    # Check length is reasonable (prevent DoS via extremely long names)
    if len(name) > 100:
        raise ValidationError(
            f"{context.capitalize()} is too long: {len(name)} characters",
            f"Use a shorter {context} (max 100 characters)"
        )

    return name
