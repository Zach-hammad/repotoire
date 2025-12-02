"""Global pytest configuration and fixtures.

This module provides shared fixtures and configuration for all tests.
"""

import os
import sys
from pathlib import Path

import pytest


# =============================================================================
# Path Setup
# =============================================================================

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =============================================================================
# Environment Detection
# =============================================================================


def _has_e2b_key() -> bool:
    """Check if E2B API key is available."""
    key = os.getenv("E2B_API_KEY", "")
    return bool(key.strip())


def _has_neo4j_connection() -> bool:
    """Check if Neo4j is available."""
    uri = os.getenv("REPOTOIRE_NEO4J_URI", "")
    return bool(uri.strip())


def _has_falkordb_connection() -> bool:
    """Check if FalkorDB is available."""
    uri = os.getenv("REPOTOIRE_NEO4J_URI", "")
    # FalkorDB typically runs on port 6379
    return "6379" in uri


# =============================================================================
# Skip Markers
# =============================================================================


def pytest_configure(config):
    """Configure pytest with custom markers."""
    # Register markers
    config.addinivalue_line(
        "markers", "unit: Unit tests (fast, no external dependencies)"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests (may require external services)"
    )
    config.addinivalue_line(
        "markers", "e2b: Tests requiring E2B sandbox (requires E2B_API_KEY)"
    )
    config.addinivalue_line(
        "markers", "slow: Slow tests (>30 seconds)"
    )
    config.addinivalue_line(
        "markers", "benchmark: Performance benchmark tests"
    )
    config.addinivalue_line(
        "markers", "neo4j: Tests requiring Neo4j connection"
    )
    config.addinivalue_line(
        "markers", "falkordb: Tests requiring FalkorDB connection"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add skip markers based on environment."""
    # Check for available services
    has_e2b = _has_e2b_key()
    has_neo4j = _has_neo4j_connection()
    has_falkordb = _has_falkordb_connection()

    skip_e2b = pytest.mark.skip(reason="E2B_API_KEY not set")
    skip_neo4j = pytest.mark.skip(reason="REPOTOIRE_NEO4J_URI not set")
    skip_falkordb = pytest.mark.skip(reason="FalkorDB not available")

    for item in items:
        # Skip E2B tests if no API key
        if "e2b" in item.keywords and not has_e2b:
            item.add_marker(skip_e2b)

        # Skip Neo4j tests if no connection
        if "neo4j" in item.keywords and not has_neo4j:
            item.add_marker(skip_neo4j)

        # Skip FalkorDB tests if not available
        if "falkordb" in item.keywords and not has_falkordb:
            item.add_marker(skip_falkordb)


# =============================================================================
# Shared Fixtures
# =============================================================================


@pytest.fixture
def project_root() -> Path:
    """Get the project root directory.

    Returns:
        Path to project root.
    """
    return PROJECT_ROOT


@pytest.fixture
def fixtures_dir() -> Path:
    """Get the test fixtures directory.

    Returns:
        Path to test fixtures directory.
    """
    return PROJECT_ROOT / "tests" / "fixtures"


@pytest.fixture
def sample_repos_dir(fixtures_dir: Path) -> Path:
    """Get the sample repositories directory.

    Returns:
        Path to sample repositories.
    """
    return fixtures_dir / "sample_repos"


@pytest.fixture
def simple_python_repo(sample_repos_dir: Path) -> Path:
    """Get the simple Python sample repository.

    Returns:
        Path to simple_python sample repo.
    """
    return sample_repos_dir / "simple_python"


@pytest.fixture
def with_tests_repo(sample_repos_dir: Path) -> Path:
    """Get the sample repository with tests.

    Returns:
        Path to with_tests sample repo.
    """
    return sample_repos_dir / "with_tests"


@pytest.fixture
def with_errors_repo(sample_repos_dir: Path) -> Path:
    """Get the sample repository with errors.

    Returns:
        Path to with_errors sample repo.
    """
    return sample_repos_dir / "with_errors"


@pytest.fixture
def large_project_repo(sample_repos_dir: Path) -> Path:
    """Get the large sample project repository.

    Returns:
        Path to large_project sample repo.
    """
    return sample_repos_dir / "large_project"


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a minimal temporary repository for testing.

    Returns:
        Path to temporary repository.
    """
    src = tmp_path / "src"
    src.mkdir()

    (src / "__init__.py").write_text("")
    (src / "main.py").write_text('''
"""Main module."""

def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"
''')

    (tmp_path / "pyproject.toml").write_text('''
[project]
name = "temp-repo"
version = "0.1.0"
''')

    return tmp_path


# =============================================================================
# E2B Fixtures
# =============================================================================


@pytest.fixture
def e2b_api_key() -> str | None:
    """Get E2B API key from environment.

    Returns:
        E2B API key or None if not set.
    """
    return os.getenv("E2B_API_KEY")


@pytest.fixture
def sandbox_config_from_env():
    """Get sandbox configuration from environment.

    Returns:
        SandboxConfig if E2B is available, None otherwise.
    """
    if not _has_e2b_key():
        return None

    from repotoire.sandbox import SandboxConfig
    return SandboxConfig.from_env()


# =============================================================================
# Neo4j Fixtures
# =============================================================================


@pytest.fixture
def neo4j_uri() -> str | None:
    """Get Neo4j URI from environment.

    Returns:
        Neo4j URI or None if not set.
    """
    return os.getenv("REPOTOIRE_NEO4J_URI")


@pytest.fixture
def neo4j_password() -> str | None:
    """Get Neo4j password from environment.

    Returns:
        Neo4j password or None if not set.
    """
    return os.getenv("REPOTOIRE_NEO4J_PASSWORD")
