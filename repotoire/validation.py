"""Input validation utilities with helpful error messages."""

import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)


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


def validate_neo4j_uri(uri: str) -> str:
    """Validate Neo4j URI format.

    Args:
        uri: Neo4j connection URI

    Returns:
        Validated URI string

    Raises:
        ValidationError: If URI format is invalid
    """
    if not uri or not uri.strip():
        raise ValidationError(
            "Neo4j URI cannot be empty",
            "Provide a valid Neo4j connection URI, e.g., bolt://localhost:7687"
        )

    # Common valid schemes for Neo4j
    valid_schemes = {"bolt", "neo4j", "bolt+s", "neo4j+s", "bolt+ssc", "neo4j+ssc"}

    try:
        parsed = urlparse(uri)
    except Exception as e:
        raise ValidationError(
            f"Invalid URI format: {uri}",
            "Use format: bolt://host:port or neo4j://host:port"
        )

    if not parsed.scheme:
        raise ValidationError(
            f"Missing URI scheme in: {uri}",
            "URI must start with a scheme like bolt:// or neo4j://\n"
            "Example: bolt://localhost:7687"
        )

    if parsed.scheme not in valid_schemes:
        raise ValidationError(
            f"Invalid URI scheme '{parsed.scheme}' in: {uri}",
            f"Use one of: {', '.join(sorted(valid_schemes))}\n"
            f"Most common: bolt://localhost:7687"
        )

    if not parsed.netloc:
        raise ValidationError(
            f"Missing host in URI: {uri}",
            "URI must include a host, e.g., bolt://localhost:7687"
        )

    # Check for common mistakes
    if "7474" in uri:
        raise ValidationError(
            f"Port 7474 is for HTTP, not Bolt: {uri}",
            "Use port 7687 for Bolt protocol: bolt://localhost:7687"
        )

    return uri


def validate_neo4j_credentials(user: str, password: str) -> tuple[str, str]:
    """Validate Neo4j username and password.

    Args:
        user: Neo4j username
        password: Neo4j password

    Returns:
        Tuple of (validated_user, validated_password)

    Raises:
        ValidationError: If credentials are invalid
    """
    if not user or not user.strip():
        raise ValidationError(
            "Neo4j username cannot be empty",
            "Provide a valid Neo4j username (default is 'neo4j')"
        )

    if not password or not password.strip():
        raise ValidationError(
            "Neo4j password cannot be empty",
            "Provide your Neo4j password:\n"
            "  - Set FALKOR_NEO4J_PASSWORD environment variable\n"
            "  - Add 'neo4j.password' to .reporc config file\n"
            "  - Use --neo4j-password flag\n"
            "  - Let Falkor prompt you interactively"
        )

    # Warn about default password
    if user == "neo4j" and password == "neo4j":
        # This is just a warning, not an error
        pass

    return user, password


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


def validate_neo4j_connection(uri: str, username: str, password: str) -> None:
    """Test Neo4j connection is actually reachable.

    Args:
        uri: Neo4j connection URI
        username: Neo4j username
        password: Neo4j password

    Raises:
        ValidationError: If connection cannot be established
    """
    # Validate parameters first
    uri = validate_neo4j_uri(uri)
    username, password = validate_neo4j_credentials(username, password)

    try:
        from neo4j import GraphDatabase
        from neo4j.exceptions import ServiceUnavailable, AuthError

        # Try to connect with a short timeout
        driver = GraphDatabase.driver(uri, auth=(username, password))

        try:
            # Verify connectivity
            driver.verify_connectivity()
            logger.debug(f"Successfully validated Neo4j connection to {uri}")
        except AuthError as e:
            raise ValidationError(
                f"Neo4j authentication failed for user '{username}'",
                "Check your Neo4j credentials:\n"
                "  - Verify the username is correct (default: neo4j)\n"
                "  - Verify the password is correct\n"
                "  - Check FALKOR_NEO4J_PASSWORD environment variable\n"
                "  - Or set password in config file"
            ) from e
        except ServiceUnavailable as e:
            raise ValidationError(
                f"Cannot connect to Neo4j at {uri}",
                "Ensure Neo4j is running and accessible:\n"
                "  - Start Neo4j: docker run -p 7687:7687 neo4j:latest\n"
                "  - Check firewall settings\n"
                "  - Verify the URI is correct"
            ) from e
        finally:
            driver.close()

    except ImportError:
        raise ValidationError(
            "Neo4j driver not installed",
            "Install the neo4j package: pip install neo4j"
        )
    except Exception as e:
        if isinstance(e, ValidationError):
            raise
        raise ValidationError(
            f"Failed to connect to Neo4j: {e}",
            "Check your Neo4j configuration and ensure the database is accessible"
        ) from e


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
