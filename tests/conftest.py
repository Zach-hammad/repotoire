"""Global pytest configuration and fixtures.

This module provides shared fixtures and configuration for all tests,
including mock fixtures for external services (Clerk, Stripe, GitHub, E2B, Celery).
"""

import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest import mock
from uuid import uuid4

import pytest

# Try to import async test support
try:
    import pytest_asyncio
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    HAS_ASYNC_SUPPORT = True
except ImportError:
    HAS_ASYNC_SUPPORT = False


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


def _has_graph_connection() -> bool:
    """Check if FalkorDB is available."""
    uri = os.getenv("FALKORDB_HOST", "")
    return bool(uri.strip())


def _has_falkordb_connection() -> bool:
    """Check if FalkorDB is available."""
    uri = os.getenv("FALKORDB_HOST", "")
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
        "markers", "falkordb: Tests requiring FalkorDB connection"
    )
    config.addinivalue_line(
        "markers", "falkordb: Tests requiring FalkorDB connection"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add skip markers based on environment."""
    # Check for available services
    has_e2b = _has_e2b_key()
    has_graph_db = _has_graph_connection()
    has_falkordb = _has_falkordb_connection()

    skip_e2b = pytest.mark.skip(reason="E2B_API_KEY not set")
    skip_graph_db = pytest.mark.skip(reason="FALKORDB_HOST not set")
    skip_falkordb = pytest.mark.skip(reason="FalkorDB not available")

    for item in items:
        # Skip E2B tests if no API key
        if "e2b" in item.keywords and not has_e2b:
            item.add_marker(skip_e2b)

        # Skip FalkorDB tests if no connection
        if "falkordb" in item.keywords and not has_graph_db:
            item.add_marker(skip_graph_db)

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
# Graph DB Fixtures
# =============================================================================


@pytest.fixture
def falkordb_host() -> str | None:
    """Get FalkorDB host from environment.

    Returns:
        FalkorDB host or None if not set.
    """
    return os.getenv("FALKORDB_HOST")


@pytest.fixture
def falkordb_password() -> str | None:
    """Get FalkorDB password from environment.

    Returns:
        FalkorDB password or None if not set.
    """
    return os.getenv("FALKORDB_PASSWORD")


# =============================================================================
# Test Database Configuration
# =============================================================================

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5433/repotoire_test"
)


def _has_test_database() -> bool:
    """Check if test database is available."""
    url = os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip()) or "localhost:5433" in TEST_DATABASE_URL


# =============================================================================
# Mock Fixtures for External Services
# =============================================================================


@pytest.fixture
def mock_clerk() -> Generator[mock.MagicMock, None, None]:
    """Mock Clerk authentication for all tests.

    Returns a mock that simulates successful Clerk token verification.
    Use this fixture when testing authenticated API endpoints.

    Example:
        def test_protected_endpoint(mock_clerk, test_client):
            response = test_client.get("/api/v1/account")
            assert response.status_code == 200
    """
    from repotoire.api.auth import ClerkUser

    mock_user = ClerkUser(
        user_id="clerk_test_user_123",
        session_id="sess_test_123",
        org_id=None,
        org_role=None,
    )

    async def mock_get_current_user(*args, **kwargs):
        return mock_user

    with mock.patch(
        "repotoire.api.auth.get_current_user", side_effect=mock_get_current_user
    ) as m:
        m.return_value = mock_user
        yield m


@pytest.fixture
def mock_clerk_with_org() -> Generator[mock.MagicMock, None, None]:
    """Mock Clerk authentication with organization context.

    Returns a mock that simulates a user authenticated within an organization.
    """
    from repotoire.api.auth import ClerkUser

    mock_user = ClerkUser(
        user_id="clerk_test_user_123",
        session_id="sess_test_123",
        org_id="org_test_123",
        org_role="member",
    )

    async def mock_get_current_user(*args, **kwargs):
        return mock_user

    with mock.patch(
        "repotoire.api.auth.get_current_user", side_effect=mock_get_current_user
    ) as m:
        m.return_value = mock_user
        yield m


@pytest.fixture
def mock_clerk_admin() -> Generator[mock.MagicMock, None, None]:
    """Mock Clerk admin authentication.

    Returns a mock that simulates an admin user within an organization.
    """
    from repotoire.api.auth import ClerkUser

    mock_user = ClerkUser(
        user_id="clerk_admin_user_123",
        session_id="sess_admin_123",
        org_id="org_test_123",
        org_role="admin",
    )

    async def mock_get_current_user(*args, **kwargs):
        return mock_user

    with mock.patch(
        "repotoire.api.auth.get_current_user", side_effect=mock_get_current_user
    ) as m:
        m.return_value = mock_user
        yield m


@pytest.fixture
def mock_stripe() -> Generator[dict[str, mock.MagicMock], None, None]:
    """Mock Stripe API for billing tests.

    Returns a dictionary of mocked Stripe API objects:
    - checkout: stripe.checkout.Session.create
    - subscription: stripe.Subscription.retrieve
    - customer: stripe.Customer.create
    - portal: stripe.billing_portal.Session.create

    Example:
        def test_create_checkout(mock_stripe):
            mock_stripe["checkout"].return_value.url = "https://custom-url.com"
            # ... test code
    """
    mocks = {}

    with (
        mock.patch("stripe.checkout.Session.create") as checkout_mock,
        mock.patch("stripe.checkout.Session.retrieve") as checkout_retrieve_mock,
        mock.patch("stripe.Subscription.retrieve") as sub_mock,
        mock.patch("stripe.Subscription.modify") as sub_modify_mock,
        mock.patch("stripe.Customer.create") as customer_mock,
        mock.patch("stripe.Customer.retrieve") as customer_retrieve_mock,
        mock.patch("stripe.billing_portal.Session.create") as portal_mock,
        mock.patch("stripe.Webhook.construct_event") as webhook_mock,
    ):
        # Checkout session
        checkout_mock.return_value = mock.Mock(
            id="cs_test_123",
            url="https://checkout.stripe.com/test",
            status="open",
        )
        checkout_retrieve_mock.return_value = mock.Mock(
            id="cs_test_123",
            status="complete",
            customer="cus_test_123",
            subscription="sub_test_123",
        )

        # Subscription
        sub_mock.return_value = mock.Mock(
            id="sub_test_123",
            status="active",
            current_period_start=1700000000,
            current_period_end=1702592000,
            cancel_at_period_end=False,
            items=mock.Mock(data=[mock.Mock(price=mock.Mock(id="price_test_123"))]),
        )
        sub_modify_mock.return_value = sub_mock.return_value

        # Customer
        customer_mock.return_value = mock.Mock(
            id="cus_test_123",
            email="test@example.com",
        )
        customer_retrieve_mock.return_value = customer_mock.return_value

        # Billing portal
        portal_mock.return_value = mock.Mock(
            url="https://billing.stripe.com/test"
        )

        # Webhook verification
        webhook_mock.return_value = {
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_test_123", "status": "active"}},
        }

        mocks["checkout"] = checkout_mock
        mocks["checkout_retrieve"] = checkout_retrieve_mock
        mocks["subscription"] = sub_mock
        mocks["subscription_modify"] = sub_modify_mock
        mocks["customer"] = customer_mock
        mocks["customer_retrieve"] = customer_retrieve_mock
        mocks["portal"] = portal_mock
        mocks["webhook"] = webhook_mock

        yield mocks


@pytest.fixture
def mock_github() -> Generator[dict[str, mock.MagicMock], None, None]:
    """Mock GitHub API for integration tests.

    Returns a dictionary of mocked GitHub API objects:
    - get: httpx.AsyncClient.get
    - post: httpx.AsyncClient.post
    - comment: post_or_update_pr_comment function
    - installation_token: get_installation_token function

    Example:
        def test_list_repos(mock_github):
            mock_github["get"].return_value.json.return_value = [...]
    """
    mocks = {}

    with (
        mock.patch("httpx.AsyncClient.get") as get_mock,
        mock.patch("httpx.AsyncClient.post") as post_mock,
    ):
        # GET requests (list repos, etc.)
        get_mock.return_value = mock.Mock(
            status_code=200,
            json=lambda: [
                {
                    "id": 123456789,
                    "full_name": "test-org/test-repo",
                    "private": False,
                    "default_branch": "main",
                },
            ],
            raise_for_status=lambda: None,
        )

        # POST requests (create comment, etc.)
        post_mock.return_value = mock.Mock(
            status_code=201,
            json=lambda: {
                "id": 123456,
                "html_url": "https://github.com/test/repo/pull/1#issuecomment-123456",
            },
            raise_for_status=lambda: None,
        )

        mocks["get"] = get_mock
        mocks["post"] = post_mock

        yield mocks


@pytest.fixture
def mock_github_installation() -> Generator[mock.MagicMock, None, None]:
    """Mock GitHub App installation token generation.

    Use this when testing code that needs to authenticate as a GitHub App.
    """
    with mock.patch("repotoire.github.auth.get_installation_token") as m:
        m.return_value = "ghs_test_installation_token_123"
        yield m


@pytest.fixture
def mock_github_pr_comment() -> Generator[mock.MagicMock, None, None]:
    """Mock GitHub PR comment posting.

    Use this when testing the PR commenter functionality.
    """
    with mock.patch("repotoire.github.pr_commenter.post_or_update_pr_comment") as m:
        m.return_value = {
            "comment_id": "123456",
            "action": "created",
            "url": "https://github.com/test/repo/pull/1#issuecomment-123456",
        }
        yield m


@pytest.fixture
def mock_e2b_sandbox() -> Generator[mock.MagicMock, None, None]:
    """Mock E2B sandbox for analysis tests.

    Returns a mock sandbox that simulates successful code execution.
    Use this when testing code that runs in E2B sandboxes without
    requiring an actual E2B_API_KEY.

    Example:
        def test_sandbox_execution(mock_e2b_sandbox):
            # Customize the mock if needed
            mock_e2b_sandbox.return_value.run_code.return_value.stdout = "custom output"
    """
    with mock.patch("e2b.Sandbox") as sandbox_class_mock:
        # Create mock sandbox instance
        sandbox_instance = mock.MagicMock()

        # Mock run_code
        sandbox_instance.run_code.return_value = mock.Mock(
            stdout="Analysis complete\nHealth score: 85",
            stderr="",
            exit_code=0,
        )

        # Mock process.start (for running commands)
        process_mock = mock.MagicMock()
        process_mock.wait.return_value = mock.Mock(
            stdout="Success",
            stderr="",
            exit_code=0,
        )
        sandbox_instance.process.start.return_value = process_mock

        # Mock filesystem operations
        sandbox_instance.filesystem.read.return_value = "file contents"
        sandbox_instance.filesystem.write.return_value = None
        sandbox_instance.filesystem.list.return_value = [
            mock.Mock(name="file1.py", is_dir=False),
            mock.Mock(name="dir1", is_dir=True),
        ]

        # Mock upload/download
        sandbox_instance.upload_file.return_value = "/sandbox/uploaded_file"
        sandbox_instance.download_file.return_value = b"file contents"

        # Context manager support
        sandbox_class_mock.return_value.__enter__.return_value = sandbox_instance
        sandbox_class_mock.return_value.__aenter__.return_value = sandbox_instance

        yield sandbox_class_mock


@pytest.fixture
def mock_celery() -> Generator[mock.MagicMock, None, None]:
    """Mock Celery task execution for synchronous testing.

    Returns a mock that captures task calls without actually executing them.
    Use this when testing code that dispatches Celery tasks.

    Example:
        def test_trigger_analysis(mock_celery):
            response = client.post("/api/v1/analysis/trigger")
            mock_celery.assert_called_once()
    """
    # Import the celery app and patch its send_task method
    from repotoire.workers.celery_app import celery_app
    with mock.patch.object(celery_app, "send_task") as m:
        # Return a mock AsyncResult
        m.return_value = mock.Mock(
            id=str(uuid4()),
            status="PENDING",
            result=None,
        )
        yield m


@pytest.fixture
def mock_celery_tasks() -> Generator[dict[str, mock.MagicMock], None, None]:
    """Mock individual Celery tasks for more granular testing.

    Returns a dictionary of mocked tasks:
    - analyze: run_analysis task
    - webhook: deliver_webhook task
    - email: send_email task

    Example:
        def test_analysis_task(mock_celery_tasks):
            mock_celery_tasks["analyze"].delay.return_value.id = "custom-task-id"
    """
    mocks = {}

    with (
        mock.patch("repotoire.workers.tasks.run_analysis") as analyze_mock,
        mock.patch("repotoire.workers.webhook_delivery.deliver_webhook") as webhook_mock,
    ):
        # Mock task.delay() and task.apply_async()
        for task_mock in [analyze_mock, webhook_mock]:
            task_mock.delay.return_value = mock.Mock(
                id=str(uuid4()),
                status="PENDING",
            )
            task_mock.apply_async.return_value = mock.Mock(
                id=str(uuid4()),
                status="PENDING",
            )

        mocks["analyze"] = analyze_mock
        mocks["webhook"] = webhook_mock

        yield mocks


@pytest.fixture
def mock_resend() -> Generator[mock.MagicMock, None, None]:
    """Mock Resend email service.

    Use this when testing code that sends emails via Resend.
    """
    with mock.patch("resend.Emails.send") as m:
        m.return_value = {"id": f"email_{uuid4().hex[:12]}"}
        yield m


# =============================================================================
# Auth Header Helpers
# =============================================================================


def auth_headers(user_id: str = "test_user") -> dict[str, str]:
    """Generate auth headers for test requests.

    Args:
        user_id: User ID to include in headers

    Returns:
        Dictionary of auth headers

    Example:
        response = client.get("/api/v1/account", headers=auth_headers())
    """
    return {
        "Authorization": f"Bearer test_token_{user_id}",
        "X-Clerk-User-Id": user_id,
    }


def api_key_headers(api_key: str = "test_api_key") -> dict[str, str]:
    """Generate API key headers for test requests.

    Args:
        api_key: API key to include in headers

    Returns:
        Dictionary of API key headers

    Example:
        response = client.get("/api/v1/analysis", headers=api_key_headers())
    """
    return {"X-API-Key": api_key}


def org_headers(user_id: str = "test_user", org_id: str = "test_org") -> dict[str, str]:
    """Generate headers for organization-scoped requests.

    Args:
        user_id: User ID
        org_id: Organization ID

    Returns:
        Dictionary of headers with org context
    """
    return {
        "Authorization": f"Bearer test_token_{user_id}",
        "X-Clerk-User-Id": user_id,
        "X-Clerk-Org-Id": org_id,
    }
