# REPO-276: Comprehensive E2E Testing Suite

## Overview

Implement a complete end-to-end testing suite for the Repotoire SaaS platform using Playwright for browser tests and pytest for backend integration tests. This suite must cover **all 19 API route modules**, **12 worker modules**, and provide test factories for **all 19 database models**.

## Testing Stack

- **Playwright** - E2E browser testing (TypeScript)
- **pytest** - Backend integration tests (Python)
- **Factory Boy** - Test data factories
- **Docker Compose** - Test infrastructure (PostgreSQL, Redis, FalkorDB)
- **pytest-asyncio** - Async test support
- **httpx** - Async HTTP client for API tests

---

## Part 1: Test Infrastructure

### 1.1 Docker Compose Test Environment

Create `docker-compose.test.yml`:

```yaml
services:
  test-postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: repotoire_test
      POSTGRES_USER: test
      POSTGRES_PASSWORD: test
    ports:
      - "5433:5432"
    tmpfs:
      - /var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U test -d repotoire_test"]
      interval: 5s
      timeout: 5s
      retries: 5

  test-redis:
    image: redis:7-alpine
    ports:
      - "6380:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  test-falkordb:
    image: falkordb/falkordb:latest
    ports:
      - "6381:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "-p", "6379", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  test-celery-worker:
    build: .
    command: celery -A repotoire.workers.celery_app worker --loglevel=info
    environment:
      DATABASE_URL: postgresql://test:test@test-postgres:5432/repotoire_test
      REDIS_URL: redis://test-redis:6379/0
      REPOTOIRE_NEO4J_URI: bolt://test-falkordb:6379
    depends_on:
      test-postgres:
        condition: service_healthy
      test-redis:
        condition: service_healthy
```

### 1.2 pytest Configuration

Create/update `tests/conftest.py`:

```python
"""Global pytest configuration and fixtures."""

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest import mock
from uuid import uuid4

import factory
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from repotoire.api.app import app
from repotoire.db.models import Base
from repotoire.db.session import get_async_session

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5433/repotoire_test"
)


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_db() -> AsyncGenerator[AsyncSession, None]:
    """Create isolated test database session with automatic cleanup."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        yield session
        await session.rollback()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def test_client(test_db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create test HTTP client with dependency overrides."""
    async def override_get_session():
        yield test_db

    app.dependency_overrides[get_async_session] = override_get_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()


# =============================================================================
# Mock Fixtures for External Services
# =============================================================================

@pytest.fixture
def mock_clerk() -> Generator[mock.MagicMock, None, None]:
    """Mock Clerk authentication for all tests."""
    with mock.patch("repotoire.api.dependencies.auth.verify_clerk_token") as m:
        m.return_value = {
            "user_id": "clerk_test_user_123",
            "email": "test@example.com",
            "org_id": None,
        }
        yield m


@pytest.fixture
def mock_clerk_admin() -> Generator[mock.MagicMock, None, None]:
    """Mock Clerk admin authentication."""
    with mock.patch("repotoire.api.dependencies.auth.verify_clerk_token") as m:
        m.return_value = {
            "user_id": "clerk_admin_user",
            "email": "admin@example.com",
            "org_id": "org_123",
            "org_role": "admin",
        }
        yield m


@pytest.fixture
def mock_stripe() -> Generator[dict[str, mock.MagicMock], None, None]:
    """Mock Stripe API for billing tests."""
    mocks = {}

    with mock.patch("stripe.checkout.Session.create") as checkout_mock, \
         mock.patch("stripe.Subscription.retrieve") as sub_mock, \
         mock.patch("stripe.Customer.create") as customer_mock, \
         mock.patch("stripe.billing_portal.Session.create") as portal_mock:

        checkout_mock.return_value = mock.Mock(id="cs_test_123", url="https://checkout.stripe.com/test")
        sub_mock.return_value = mock.Mock(id="sub_test_123", status="active", current_period_end=9999999999)
        customer_mock.return_value = mock.Mock(id="cus_test_123", email="test@example.com")
        portal_mock.return_value = mock.Mock(url="https://billing.stripe.com/test")

        mocks["checkout"] = checkout_mock
        mocks["subscription"] = sub_mock
        mocks["customer"] = customer_mock
        mocks["portal"] = portal_mock
        yield mocks


@pytest.fixture
def mock_github() -> Generator[dict[str, mock.MagicMock], None, None]:
    """Mock GitHub API for integration tests."""
    mocks = {}

    with mock.patch("httpx.AsyncClient.get") as get_mock, \
         mock.patch("httpx.AsyncClient.post") as post_mock, \
         mock.patch("repotoire.github.pr_commenter.post_or_update_pr_comment") as comment_mock:

        get_mock.return_value = mock.Mock(
            status_code=200,
            json=lambda: [{"id": 123, "full_name": "test-org/test-repo", "private": False}]
        )
        comment_mock.return_value = {"comment_id": "123456", "action": "created", "url": "https://github.com/test/repo/pull/1#issuecomment-123456"}

        mocks["get"] = get_mock
        mocks["post"] = post_mock
        mocks["comment"] = comment_mock
        yield mocks


@pytest.fixture
def mock_e2b_sandbox() -> Generator[mock.MagicMock, None, None]:
    """Mock E2B sandbox for analysis tests."""
    with mock.patch("e2b.Sandbox") as sandbox_mock:
        sandbox_instance = mock.MagicMock()
        sandbox_instance.run_code.return_value = mock.Mock(stdout="Analysis complete", stderr="", exit_code=0)
        sandbox_mock.return_value.__enter__.return_value = sandbox_instance
        yield sandbox_mock


@pytest.fixture
def mock_celery() -> Generator[mock.MagicMock, None, None]:
    """Mock Celery task execution for synchronous testing."""
    with mock.patch("repotoire.workers.celery_app.celery_app.send_task") as m:
        m.return_value = mock.Mock(id=str(uuid4()))
        yield m


def auth_headers(user_id: str = "test_user") -> dict[str, str]:
    """Generate auth headers for test requests."""
    return {"Authorization": f"Bearer test_token_{user_id}", "X-Clerk-User-Id": user_id}


def api_key_headers(api_key: str = "test_api_key") -> dict[str, str]:
    """Generate API key headers for test requests."""
    return {"X-API-Key": api_key}
```

---

## Part 2: Test Data Factories

Create `tests/factories.py` with factories for ALL 19 models:

```python
"""Test data factories for all database models."""

import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import factory
from slugify import slugify

from repotoire.db.models import (
    User, Organization, OrganizationMember, Repository, AnalysisRun, Finding, Fix,
    Subscription, UsageRecord, QuotaOverride,
    Webhook, WebhookDelivery, GitHubInstallation,
    AuditLogEntry, DataExportRequest, DataDeletionRequest, EmailPreference,
    StatusComponent, Incident, IncidentUpdate, ScheduledMaintenance, StatusSubscriber,
    ChangelogEntry, ChangelogSubscriber,
    CustomDomain, SSOConfiguration,
    PlanTier, MemberRole, AnalysisStatus, FindingSeverity, FixStatus, DeliveryStatus,
    AuditAction, ComponentStatus, IncidentStatus, IncidentSeverity, DomainStatus,
)


# =============================================================================
# Core Factories
# =============================================================================

class UserFactory(factory.Factory):
    class Meta:
        model = User

    id = factory.LazyFunction(uuid4)
    email = factory.Faker("email")
    name = factory.Faker("name")
    clerk_user_id = factory.LazyFunction(lambda: f"user_{uuid4().hex[:12]}")
    avatar_url = factory.Faker("image_url")
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class OrganizationFactory(factory.Factory):
    class Meta:
        model = Organization

    id = factory.LazyFunction(uuid4)
    name = factory.Faker("company")
    slug = factory.LazyAttribute(lambda o: slugify(o.name)[:50])
    plan_tier = PlanTier.FREE
    stripe_customer_id = factory.LazyFunction(lambda: f"cus_{uuid4().hex[:14]}")
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class OrganizationMemberFactory(factory.Factory):
    class Meta:
        model = OrganizationMember

    id = factory.LazyFunction(uuid4)
    user_id = factory.LazyFunction(uuid4)
    organization_id = factory.LazyFunction(uuid4)
    role = MemberRole.MEMBER
    joined_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class RepositoryFactory(factory.Factory):
    class Meta:
        model = Repository

    id = factory.LazyFunction(uuid4)
    name = factory.Faker("word")
    full_name = factory.LazyAttribute(lambda r: f"test-org/{r.name}")
    organization_id = factory.LazyFunction(uuid4)
    github_installation_id = factory.Faker("random_int", min=100000, max=999999)
    is_private = False
    is_active = True
    auto_analyze_enabled = True
    pr_analysis_enabled = True
    default_branch = "main"
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class AnalysisRunFactory(factory.Factory):
    class Meta:
        model = AnalysisRun

    id = factory.LazyFunction(uuid4)
    repository_id = factory.LazyFunction(uuid4)
    commit_sha = factory.LazyFunction(lambda: secrets.token_hex(20))
    branch = "main"
    status = AnalysisStatus.COMPLETED
    health_score = factory.Faker("random_int", min=50, max=100)
    structure_score = factory.Faker("random_int", min=50, max=100)
    quality_score = factory.Faker("random_int", min=50, max=100)
    architecture_score = factory.Faker("random_int", min=50, max=100)
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    completed_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class FindingFactory(factory.Factory):
    class Meta:
        model = Finding

    id = factory.LazyFunction(uuid4)
    analysis_run_id = factory.LazyFunction(uuid4)
    repository_id = factory.LazyFunction(uuid4)
    detector = factory.Faker("random_element", elements=["ruff", "mypy", "bandit", "graph"])
    title = factory.Faker("sentence", nb_words=6)
    description = factory.Faker("paragraph")
    severity = factory.Faker("random_element", elements=list(FindingSeverity))
    category = factory.Faker("random_element", elements=["code_smell", "security", "complexity"])
    affected_files = factory.LazyFunction(lambda: [f"src/{secrets.token_hex(4)}.py"])
    line_start = factory.Faker("random_int", min=1, max=100)
    is_resolved = False
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class FixFactory(factory.Factory):
    class Meta:
        model = Fix

    id = factory.LazyFunction(uuid4)
    finding_id = factory.LazyFunction(uuid4)
    status = FixStatus.PENDING
    diff = factory.Faker("text", max_nb_chars=500)
    justification = factory.Faker("paragraph")
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


# =============================================================================
# Billing Factories
# =============================================================================

class SubscriptionFactory(factory.Factory):
    class Meta:
        model = Subscription

    id = factory.LazyFunction(uuid4)
    organization_id = factory.LazyFunction(uuid4)
    stripe_subscription_id = factory.LazyFunction(lambda: f"sub_{uuid4().hex[:14]}")
    stripe_price_id = "price_pro_monthly"
    status = "active"
    current_period_start = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    current_period_end = factory.LazyFunction(lambda: datetime.now(timezone.utc) + timedelta(days=30))
    cancel_at_period_end = False


class UsageRecordFactory(factory.Factory):
    class Meta:
        model = UsageRecord

    id = factory.LazyFunction(uuid4)
    organization_id = factory.LazyFunction(uuid4)
    metric_type = factory.Faker("random_element", elements=["analysis", "api_call", "storage"])
    quantity = factory.Faker("random_int", min=1, max=100)
    recorded_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class QuotaOverrideFactory(factory.Factory):
    class Meta:
        model = QuotaOverride

    id = factory.LazyFunction(uuid4)
    organization_id = factory.LazyFunction(uuid4)
    quota_type = "analyses_per_month"
    override_value = 1000
    reason = "Enterprise customer"
    expires_at = factory.LazyFunction(lambda: datetime.now(timezone.utc) + timedelta(days=365))


# =============================================================================
# Webhook Factories
# =============================================================================

class WebhookFactory(factory.Factory):
    class Meta:
        model = Webhook

    id = factory.LazyFunction(uuid4)
    organization_id = factory.LazyFunction(uuid4)
    name = factory.Faker("sentence", nb_words=3)
    url = factory.Faker("url")
    secret = factory.LazyFunction(lambda: secrets.token_hex(32))
    events = ["analysis.completed", "analysis.failed"]
    is_active = True


class WebhookDeliveryFactory(factory.Factory):
    class Meta:
        model = WebhookDelivery

    id = factory.LazyFunction(uuid4)
    webhook_id = factory.LazyFunction(uuid4)
    event_type = "analysis.completed"
    payload = factory.LazyFunction(lambda: {"event": "test", "data": {}})
    status = DeliveryStatus.PENDING
    attempt_count = 0
    max_attempts = 5


class GitHubInstallationFactory(factory.Factory):
    class Meta:
        model = GitHubInstallation

    id = factory.LazyFunction(uuid4)
    installation_id = factory.Faker("random_int", min=10000000, max=99999999)
    organization_id = factory.LazyFunction(uuid4)
    account_login = factory.Faker("user_name")
    account_type = "Organization"
    access_token_encrypted = factory.LazyFunction(lambda: secrets.token_hex(32))
    token_expires_at = factory.LazyFunction(lambda: datetime.now(timezone.utc) + timedelta(hours=1))


# =============================================================================
# Audit & Compliance Factories
# =============================================================================

class AuditLogEntryFactory(factory.Factory):
    class Meta:
        model = AuditLogEntry

    id = factory.LazyFunction(uuid4)
    organization_id = factory.LazyFunction(uuid4)
    user_id = factory.LazyFunction(uuid4)
    action = factory.Faker("random_element", elements=list(AuditAction))
    resource_type = factory.Faker("random_element", elements=["repository", "user", "webhook"])
    resource_id = factory.LazyFunction(lambda: str(uuid4()))
    ip_address = factory.Faker("ipv4")
    user_agent = factory.Faker("user_agent")
    metadata = factory.LazyFunction(lambda: {})
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class DataExportRequestFactory(factory.Factory):
    class Meta:
        model = DataExportRequest

    id = factory.LazyFunction(uuid4)
    user_id = factory.LazyFunction(uuid4)
    status = "pending"
    requested_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class DataDeletionRequestFactory(factory.Factory):
    class Meta:
        model = DataDeletionRequest

    id = factory.LazyFunction(uuid4)
    user_id = factory.LazyFunction(uuid4)
    status = "pending"
    reason = factory.Faker("sentence")
    requested_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    scheduled_for = factory.LazyFunction(lambda: datetime.now(timezone.utc) + timedelta(days=30))


class EmailPreferenceFactory(factory.Factory):
    class Meta:
        model = EmailPreference

    id = factory.LazyFunction(uuid4)
    user_id = factory.LazyFunction(uuid4)
    analysis_completed = True
    analysis_failed = True
    weekly_digest = True
    security_alerts = True
    product_updates = False
    marketing = False


# =============================================================================
# Status Page Factories
# =============================================================================

class StatusComponentFactory(factory.Factory):
    class Meta:
        model = StatusComponent

    id = factory.LazyFunction(uuid4)
    name = factory.Faker("random_element", elements=["API", "Dashboard", "Analysis Engine", "Webhooks"])
    description = factory.Faker("sentence")
    status = ComponentStatus.OPERATIONAL
    display_order = factory.Sequence(lambda n: n)
    is_visible = True


class IncidentFactory(factory.Factory):
    class Meta:
        model = Incident

    id = factory.LazyFunction(uuid4)
    title = factory.Faker("sentence", nb_words=5)
    status = IncidentStatus.INVESTIGATING
    severity = IncidentSeverity.MINOR
    message = factory.Faker("paragraph")
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class IncidentUpdateFactory(factory.Factory):
    class Meta:
        model = IncidentUpdate

    id = factory.LazyFunction(uuid4)
    incident_id = factory.LazyFunction(uuid4)
    status = IncidentStatus.IDENTIFIED
    message = factory.Faker("paragraph")
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class ScheduledMaintenanceFactory(factory.Factory):
    class Meta:
        model = ScheduledMaintenance

    id = factory.LazyFunction(uuid4)
    title = factory.Faker("sentence", nb_words=4)
    description = factory.Faker("paragraph")
    scheduled_start = factory.LazyFunction(lambda: datetime.now(timezone.utc) + timedelta(days=7))
    scheduled_end = factory.LazyFunction(lambda: datetime.now(timezone.utc) + timedelta(days=7, hours=2))
    status = "scheduled"


class StatusSubscriberFactory(factory.Factory):
    class Meta:
        model = StatusSubscriber

    id = factory.LazyFunction(uuid4)
    email = factory.Faker("email")
    is_verified = True
    verification_token = factory.LazyFunction(lambda: secrets.token_urlsafe(32))
    subscribed_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


# =============================================================================
# Changelog Factories
# =============================================================================

class ChangelogEntryFactory(factory.Factory):
    class Meta:
        model = ChangelogEntry

    id = factory.LazyFunction(uuid4)
    title = factory.Faker("sentence", nb_words=6)
    slug = factory.LazyAttribute(lambda e: slugify(e.title)[:100])
    content = factory.Faker("paragraphs", nb=3)
    summary = factory.Faker("sentence")
    category = factory.Faker("random_element", elements=["feature", "improvement", "fix", "security"])
    is_published = True
    is_major = False
    published_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class ChangelogSubscriberFactory(factory.Factory):
    class Meta:
        model = ChangelogSubscriber

    id = factory.LazyFunction(uuid4)
    email = factory.Faker("email")
    is_verified = True
    verification_token = factory.LazyFunction(lambda: secrets.token_urlsafe(32))
    subscribed_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


# =============================================================================
# Enterprise Factories
# =============================================================================

class CustomDomainFactory(factory.Factory):
    class Meta:
        model = CustomDomain

    id = factory.LazyFunction(uuid4)
    organization_id = factory.LazyFunction(uuid4)
    domain = factory.Faker("domain_name")
    status = DomainStatus.PENDING
    verification_token = factory.LazyFunction(lambda: f"repotoire-verify={secrets.token_hex(16)}")
    ssl_status = "pending"
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class SSOConfigurationFactory(factory.Factory):
    class Meta:
        model = SSOConfiguration

    id = factory.LazyFunction(uuid4)
    organization_id = factory.LazyFunction(uuid4)
    provider = "saml"
    is_enabled = True
    metadata_url = factory.Faker("url")
    entity_id = factory.LazyFunction(lambda: f"https://idp.example.com/{uuid4().hex[:8]}")
    sso_url = factory.Faker("url")
    certificate = factory.LazyFunction(lambda: f"-----BEGIN CERTIFICATE-----\n{secrets.token_hex(100)}\n-----END CERTIFICATE-----")
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


# =============================================================================
# Async Factory Helpers
# =============================================================================

async def create_test_user(session, **kwargs) -> User:
    """Create and persist a test user."""
    user = UserFactory(**kwargs)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def create_test_org(session, owner: User = None, **kwargs) -> Organization:
    """Create and persist a test organization with optional owner."""
    org = OrganizationFactory(**kwargs)
    session.add(org)
    await session.flush()

    if owner:
        member = OrganizationMemberFactory(organization_id=org.id, user_id=owner.id, role=MemberRole.OWNER)
        session.add(member)

    await session.commit()
    await session.refresh(org)
    return org


async def create_test_repo(session, org: Organization, **kwargs) -> Repository:
    """Create and persist a test repository."""
    repo = RepositoryFactory(organization_id=org.id, **kwargs)
    session.add(repo)
    await session.commit()
    await session.refresh(repo)
    return repo


async def create_test_analysis(session, repo: Repository, with_findings: int = 0, **kwargs) -> AnalysisRun:
    """Create and persist a test analysis with optional findings."""
    analysis = AnalysisRunFactory(repository_id=repo.id, **kwargs)
    session.add(analysis)
    await session.flush()

    if with_findings > 0:
        for _ in range(with_findings):
            finding = FindingFactory(analysis_run_id=analysis.id, repository_id=repo.id)
            session.add(finding)

    await session.commit()
    await session.refresh(analysis)
    return analysis
```

---

## Part 3: Backend Integration Tests

Create integration tests in `tests/integration/` covering all 19 API route modules.

### 3.1 test_auth.py - Authentication (CLI auth, API keys)

```python
"""Integration tests for authentication flows."""

import pytest
from httpx import AsyncClient
from tests.conftest import auth_headers, api_key_headers
from tests.factories import create_test_user, create_test_org


@pytest.mark.integration
class TestCLIAuthentication:
    async def test_request_device_code(self, test_client: AsyncClient, test_db):
        response = await test_client.post("/api/v1/cli/auth/device")
        assert response.status_code == 200
        assert "device_code" in response.json()
        assert "user_code" in response.json()

    async def test_poll_token_pending(self, test_client: AsyncClient, test_db):
        response = await test_client.post("/api/v1/cli/auth/device")
        device_code = response.json()["device_code"]

        response = await test_client.post("/api/v1/cli/auth/token", json={"device_code": device_code})
        assert response.status_code == 200
        assert response.json()["status"] == "pending"


@pytest.mark.integration
class TestAPIKeyAuthentication:
    async def test_create_api_key(self, test_client: AsyncClient, test_db, mock_clerk):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)

        response = await test_client.post(
            f"/api/v1/organizations/{org.id}/api-keys",
            headers=auth_headers(user.clerk_user_id),
            json={"name": "CI/CD Key", "scopes": ["read", "analyze"]}
        )
        assert response.status_code == 201
        assert "key" in response.json()

    async def test_revoke_api_key(self, test_client: AsyncClient, test_db, mock_clerk):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)

        create_resp = await test_client.post(
            f"/api/v1/organizations/{org.id}/api-keys",
            headers=auth_headers(user.clerk_user_id),
            json={"name": "To Revoke", "scopes": ["read"]}
        )
        key_id = create_resp.json()["id"]

        response = await test_client.delete(
            f"/api/v1/organizations/{org.id}/api-keys/{key_id}",
            headers=auth_headers(user.clerk_user_id),
        )
        assert response.status_code == 204
```

### 3.2 test_organizations.py - Organization CRUD, Team Management

```python
"""Integration tests for organization management."""

import pytest
from httpx import AsyncClient
from tests.conftest import auth_headers
from tests.factories import create_test_user, create_test_org, OrganizationMemberFactory
from repotoire.db.models import MemberRole


@pytest.mark.integration
class TestOrganizationCRUD:
    async def test_create_organization(self, test_client: AsyncClient, test_db, mock_clerk, mock_stripe):
        user = await create_test_user(test_db)

        response = await test_client.post(
            "/api/v1/organizations",
            headers=auth_headers(user.clerk_user_id),
            json={"name": "Test Organization", "slug": "test-org"}
        )
        assert response.status_code == 201
        assert response.json()["plan_tier"] == "free"

    async def test_create_organization_duplicate_slug(self, test_client: AsyncClient, test_db, mock_clerk, mock_stripe):
        user = await create_test_user(test_db)
        await create_test_org(test_db, owner=user, slug="existing-org")

        response = await test_client.post(
            "/api/v1/organizations",
            headers=auth_headers(user.clerk_user_id),
            json={"name": "Another Org", "slug": "existing-org"}
        )
        assert response.status_code == 409


@pytest.mark.integration
class TestTeamManagement:
    async def test_invite_member(self, test_client: AsyncClient, test_db, mock_clerk_admin):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)

        response = await test_client.post(
            f"/api/v1/organizations/{org.id}/members/invite",
            headers=auth_headers(user.clerk_user_id),
            json={"email": "newmember@example.com", "role": "member"}
        )
        assert response.status_code == 201

    async def test_remove_member(self, test_client: AsyncClient, test_db, mock_clerk_admin):
        owner = await create_test_user(test_db)
        member = await create_test_user(test_db, email="member@example.com")
        org = await create_test_org(test_db, owner=owner)

        membership = OrganizationMemberFactory(organization_id=org.id, user_id=member.id, role=MemberRole.MEMBER)
        test_db.add(membership)
        await test_db.commit()

        response = await test_client.delete(
            f"/api/v1/organizations/{org.id}/members/{member.id}",
            headers=auth_headers(owner.clerk_user_id),
        )
        assert response.status_code == 204
```

### 3.3 test_analysis.py - Analysis Triggers, Status, Findings

```python
"""Integration tests for analysis workflow."""

import pytest
from httpx import AsyncClient
from tests.conftest import auth_headers
from tests.factories import create_test_user, create_test_org, create_test_repo, create_test_analysis, FindingFactory
from repotoire.db.models import AnalysisStatus, FindingSeverity


@pytest.mark.integration
class TestAnalysisTrigger:
    async def test_trigger_analysis(self, test_client: AsyncClient, test_db, mock_clerk, mock_celery):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)
        repo = await create_test_repo(test_db, org)

        response = await test_client.post(
            f"/api/v1/repositories/{repo.id}/analyze",
            headers=auth_headers(user.clerk_user_id),
        )
        assert response.status_code == 202
        assert "analysis_id" in response.json()
        mock_celery.assert_called_once()


@pytest.mark.integration
class TestAnalysisStatus:
    async def test_get_analysis_completed(self, test_client: AsyncClient, test_db, mock_clerk):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)
        repo = await create_test_repo(test_db, org)
        analysis = await create_test_analysis(test_db, repo, status=AnalysisStatus.COMPLETED, health_score=85)

        response = await test_client.get(f"/api/v1/analysis/{analysis.id}", headers=auth_headers(user.clerk_user_id))
        assert response.status_code == 200
        assert response.json()["health_score"] == 85


@pytest.mark.integration
class TestFindings:
    async def test_get_analysis_findings(self, test_client: AsyncClient, test_db, mock_clerk):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)
        repo = await create_test_repo(test_db, org)
        analysis = await create_test_analysis(test_db, repo, with_findings=10)

        response = await test_client.get(f"/api/v1/analysis/{analysis.id}/findings", headers=auth_headers(user.clerk_user_id))
        assert response.status_code == 200
        assert len(response.json()["findings"]) == 10

    async def test_filter_findings_by_severity(self, test_client: AsyncClient, test_db, mock_clerk):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)
        repo = await create_test_repo(test_db, org)
        analysis = await create_test_analysis(test_db, repo)

        response = await test_client.get(
            f"/api/v1/analysis/{analysis.id}/findings",
            headers=auth_headers(user.clerk_user_id),
            params={"severity": "critical"}
        )
        assert response.status_code == 200
```

### 3.4 test_billing.py - Checkout, Subscriptions, Usage

```python
"""Integration tests for billing flows."""

import pytest
from httpx import AsyncClient
from tests.conftest import auth_headers
from tests.factories import create_test_user, create_test_org, SubscriptionFactory
from repotoire.db.models import PlanTier


@pytest.mark.integration
class TestCheckout:
    async def test_create_checkout_session(self, test_client: AsyncClient, test_db, mock_clerk, mock_stripe):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user, plan_tier=PlanTier.FREE)

        response = await test_client.post(
            f"/api/v1/billing/{org.id}/checkout",
            headers=auth_headers(user.clerk_user_id),
            json={"price_id": "price_pro_monthly"}
        )
        assert response.status_code == 200
        assert "checkout_url" in response.json()


@pytest.mark.integration
class TestSubscription:
    async def test_get_subscription(self, test_client: AsyncClient, test_db, mock_clerk):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user, plan_tier=PlanTier.PRO)

        sub = SubscriptionFactory(organization_id=org.id)
        test_db.add(sub)
        await test_db.commit()

        response = await test_client.get(f"/api/v1/billing/{org.id}/subscription", headers=auth_headers(user.clerk_user_id))
        assert response.status_code == 200
        assert response.json()["status"] == "active"

    async def test_cancel_subscription(self, test_client: AsyncClient, test_db, mock_clerk, mock_stripe):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user, plan_tier=PlanTier.PRO)

        sub = SubscriptionFactory(organization_id=org.id)
        test_db.add(sub)
        await test_db.commit()

        response = await test_client.post(f"/api/v1/billing/{org.id}/subscription/cancel", headers=auth_headers(user.clerk_user_id))
        assert response.status_code == 200
        assert response.json()["cancel_at_period_end"] is True
```

### 3.5 test_github.py - GitHub Webhooks, Installations

```python
"""Integration tests for GitHub integration."""

import pytest
from httpx import AsyncClient
from tests.factories import create_test_user, create_test_org, create_test_repo


@pytest.mark.integration
class TestGitHubWebhooks:
    async def test_push_webhook_triggers_analysis(self, test_client: AsyncClient, test_db, mock_celery):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)
        await create_test_repo(test_db, org, full_name="test-org/test-repo", auto_analyze_enabled=True)

        response = await test_client.post(
            "/api/v1/webhooks/github",
            headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=test"},
            json={
                "ref": "refs/heads/main",
                "after": "a" * 40,
                "repository": {"full_name": "test-org/test-repo", "default_branch": "main"},
            }
        )
        assert response.status_code == 200
        mock_celery.assert_called()

    async def test_pr_webhook_triggers_analysis(self, test_client: AsyncClient, test_db, mock_celery):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)
        await create_test_repo(test_db, org, full_name="test-org/test-repo", pr_analysis_enabled=True)

        response = await test_client.post(
            "/api/v1/webhooks/github",
            headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=test"},
            json={
                "action": "opened",
                "number": 123,
                "repository": {"full_name": "test-org/test-repo"},
                "pull_request": {"number": 123, "head": {"sha": "b" * 40, "ref": "feature"}, "base": {"sha": "a" * 40}},
            }
        )
        assert response.status_code == 200
```

### 3.6 test_customer_webhooks.py - Customer Webhook CRUD, Deliveries

```python
"""Integration tests for customer webhook management."""

import pytest
from httpx import AsyncClient
from tests.conftest import auth_headers
from tests.factories import create_test_user, create_test_org, WebhookFactory, WebhookDeliveryFactory
from repotoire.db.models.webhook import DeliveryStatus


@pytest.mark.integration
class TestWebhookCRUD:
    async def test_create_webhook(self, test_client: AsyncClient, test_db, mock_clerk):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)

        response = await test_client.post(
            f"/api/v1/organizations/{org.id}/webhooks",
            headers=auth_headers(user.clerk_user_id),
            json={"name": "CI/CD Webhook", "url": "https://example.com/webhook", "events": ["analysis.completed"]}
        )
        assert response.status_code == 201
        assert "secret" in response.json()

    async def test_list_deliveries(self, test_client: AsyncClient, test_db, mock_clerk):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)

        webhook = WebhookFactory(organization_id=org.id)
        test_db.add(webhook)
        await test_db.flush()

        for _ in range(5):
            test_db.add(WebhookDeliveryFactory(webhook_id=webhook.id))
        await test_db.commit()

        response = await test_client.get(
            f"/api/v1/organizations/{org.id}/webhooks/{webhook.id}/deliveries",
            headers=auth_headers(user.clerk_user_id),
        )
        assert response.status_code == 200
        assert len(response.json()["deliveries"]) == 5
```

### 3.7 test_audit.py - Audit Logs

```python
"""Integration tests for audit logging."""

import pytest
from httpx import AsyncClient
from tests.conftest import auth_headers
from tests.factories import create_test_user, create_test_org, AuditLogEntryFactory


@pytest.mark.integration
class TestAuditLogs:
    async def test_list_audit_logs(self, test_client: AsyncClient, test_db, mock_clerk_admin):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)

        for _ in range(20):
            test_db.add(AuditLogEntryFactory(organization_id=org.id, user_id=user.id))
        await test_db.commit()

        response = await test_client.get(f"/api/v1/organizations/{org.id}/audit-logs", headers=auth_headers(user.clerk_user_id))
        assert response.status_code == 200

    async def test_export_audit_logs_csv(self, test_client: AsyncClient, test_db, mock_clerk_admin):
        user = await create_test_user(test_db)
        org = await create_test_org(test_db, owner=user)

        response = await test_client.get(
            f"/api/v1/organizations/{org.id}/audit-logs/export",
            headers=auth_headers(user.clerk_user_id),
            params={"format": "csv"}
        )
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
```

### 3.8 test_status.py - Public Status Page, Incidents

```python
"""Integration tests for public status page."""

import pytest
from httpx import AsyncClient
from tests.factories import StatusComponentFactory, IncidentFactory, ScheduledMaintenanceFactory
from repotoire.db.models.status import ComponentStatus, IncidentStatus


@pytest.mark.integration
class TestPublicStatus:
    async def test_get_current_status(self, test_client: AsyncClient, test_db):
        for name in ["API", "Dashboard", "Analysis"]:
            test_db.add(StatusComponentFactory(name=name, status=ComponentStatus.OPERATIONAL))
        await test_db.commit()

        response = await test_client.get("/api/v1/status")
        assert response.status_code == 200
        assert response.json()["overall_status"] == "operational"

    async def test_get_active_incidents(self, test_client: AsyncClient, test_db):
        test_db.add(IncidentFactory(status=IncidentStatus.INVESTIGATING))
        test_db.add(IncidentFactory(status=IncidentStatus.RESOLVED))
        await test_db.commit()

        response = await test_client.get("/api/v1/status/incidents")
        assert response.status_code == 200
        assert len(response.json()["incidents"]) == 1

    async def test_subscribe_to_status_updates(self, test_client: AsyncClient, test_db):
        response = await test_client.post("/api/v1/status/subscribe", json={"email": "subscriber@example.com"})
        assert response.status_code == 201
```

### 3.9 test_changelog.py - Public Changelog

```python
"""Integration tests for public changelog."""

import pytest
from httpx import AsyncClient
from tests.conftest import auth_headers
from tests.factories import create_test_user, ChangelogEntryFactory


@pytest.mark.integration
class TestPublicChangelog:
    async def test_list_changelog_entries(self, test_client: AsyncClient, test_db):
        for _ in range(5):
            test_db.add(ChangelogEntryFactory(is_published=True))
        test_db.add(ChangelogEntryFactory(is_published=False))  # Draft
        await test_db.commit()

        response = await test_client.get("/api/v1/changelog")
        assert response.status_code == 200
        assert len(response.json()["entries"]) == 5

    async def test_get_changelog_rss(self, test_client: AsyncClient, test_db):
        test_db.add(ChangelogEntryFactory(is_published=True))
        await test_db.commit()

        response = await test_client.get("/api/v1/changelog/rss")
        assert response.status_code == 200
        assert "application/rss+xml" in response.headers["content-type"]

    async def test_mark_changelog_as_read(self, test_client: AsyncClient, test_db, mock_clerk):
        user = await create_test_user(test_db)
        entry = ChangelogEntryFactory(is_published=True)
        test_db.add(entry)
        await test_db.commit()

        response = await test_client.post(f"/api/v1/changelog/{entry.id}/read", headers=auth_headers(user.clerk_user_id))
        assert response.status_code == 200
```

### 3.10 Additional Tests (Analytics, Code Search, Fixes, Account, GDPR)

```python
# tests/integration/test_analytics.py
@pytest.mark.integration
class TestAnalytics:
    async def test_get_organization_analytics(self, test_client, test_db, mock_clerk):
        # Test org-wide analytics endpoint
        pass

    async def test_get_repository_trends(self, test_client, test_db, mock_clerk):
        # Test health score trends over time
        pass


# tests/integration/test_code.py
@pytest.mark.integration
class TestCodeSearch:
    async def test_search_code(self, test_client, test_db, mock_clerk):
        # Test semantic code search
        pass

    async def test_ask_codebase(self, test_client, test_db, mock_clerk):
        # Test RAG-powered Q&A
        pass


# tests/integration/test_fixes.py
@pytest.mark.integration
class TestAutoFix:
    async def test_generate_fix_for_finding(self, test_client, test_db, mock_clerk, mock_celery):
        # Test fix generation
        pass

    async def test_approve_fix(self, test_client, test_db, mock_clerk):
        # Test fix approval flow
        pass


# tests/integration/test_account.py
@pytest.mark.integration
class TestGDPR:
    async def test_request_data_export(self, test_client, test_db, mock_clerk, mock_celery):
        # Test GDPR data export
        pass

    async def test_request_account_deletion(self, test_client, test_db, mock_clerk):
        # Test account deletion with 30-day grace period
        pass
```

---

## Part 4: Playwright E2E Tests

### 4.1 Playwright Configuration

Create `playwright.config.ts`:

```typescript
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["html", { outputFolder: "playwright-report" }], ["json", { outputFile: "playwright-results.json" }]],
  use: {
    baseURL: process.env.TEST_BASE_URL || "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
  },
});
```

### 4.2 Test Helpers

Create `tests/e2e/helpers/auth.ts`:

```typescript
import { Page } from "@playwright/test";

export interface TestUser {
  id: string;
  email: string;
  clerkUserId: string;
}

export async function loginAsUser(page: Page, user: TestUser): Promise<void> {
  await page.context().addCookies([{
    name: "__test_auth",
    value: JSON.stringify({ userId: user.clerkUserId, email: user.email }),
    domain: "localhost",
    path: "/",
  }]);
}

export async function createTestUser(page: Page): Promise<TestUser> {
  const response = await page.request.post("/api/test/users", {
    data: { email: `test-${Date.now()}@example.com`, name: "Test User" },
  });
  return response.json();
}

export async function loginAsNewUser(page: Page): Promise<TestUser> {
  const user = await createTestUser(page);
  await loginAsUser(page, user);
  return user;
}
```

### 4.3 E2E Test Files

#### tests/e2e/auth.spec.ts

```typescript
import { test, expect } from "@playwright/test";
import { loginAsUser, createTestUser } from "./helpers/auth";

test.describe("Authentication", () => {
  test("displays sign-in page", async ({ page }) => {
    await page.goto("/sign-in");
    await expect(page.locator("h1")).toContainText("Sign in");
    await expect(page.getByText("Continue with GitHub")).toBeVisible();
  });

  test("redirects unauthenticated users to sign-in", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test("authenticated user can access dashboard", async ({ page }) => {
    const user = await createTestUser(page);
    await loginAsUser(page, user);
    await page.goto("/dashboard");
    await expect(page).toHaveURL("/dashboard");
  });
});
```

#### tests/e2e/onboarding.spec.ts

```typescript
import { test, expect } from "@playwright/test";
import { loginAsNewUser } from "./helpers/auth";

test.describe("Onboarding", () => {
  test("new user completes full onboarding flow", async ({ page }) => {
    await loginAsNewUser(page);
    await page.goto("/onboarding");

    // Step 1: Welcome
    await expect(page.locator("h1")).toContainText("Welcome");
    await page.click("text=Get Started");

    // Step 2: Create organization
    await page.fill("[name=organizationName]", "Test Org");
    await page.click("text=Continue");

    // Step 3: Connect GitHub (skip for test)
    await page.click("text=Skip for now");

    await expect(page).toHaveURL(/\/dashboard/);
  });
});
```

#### tests/e2e/billing.spec.ts

```typescript
import { test, expect } from "@playwright/test";
import { loginAsNewUser } from "./helpers/auth";

test.describe("Billing", () => {
  test("free user sees upgrade options", async ({ page }) => {
    await loginAsNewUser(page);
    await page.goto("/billing");

    await expect(page.locator("[data-testid=current-plan]")).toContainText("Free");
    await expect(page.getByText("Upgrade to Pro")).toBeVisible();
  });

  test("user can view usage metrics", async ({ page }) => {
    await loginAsNewUser(page);
    await page.goto("/billing");

    await expect(page.locator("[data-testid=usage-analyses]")).toBeVisible();
  });
});
```

#### tests/e2e/analysis.spec.ts

```typescript
import { test, expect } from "@playwright/test";
import { loginAsNewUser } from "./helpers/auth";

test.describe("Repository Analysis", () => {
  test("user can view repository health score", async ({ page }) => {
    await loginAsNewUser(page);
    await page.goto("/repos/test-repo");

    await expect(page.locator("[data-testid=health-score]")).toBeVisible();
    await expect(page.locator("[data-testid=structure-score]")).toBeVisible();
  });

  test("user can view findings list", async ({ page }) => {
    await loginAsNewUser(page);
    await page.goto("/repos/test-repo/findings");

    await expect(page.locator("[data-testid=findings-table]")).toBeVisible();
  });
});
```

---

## Part 5: CI/CD Integration

### GitHub Actions Workflow

Create `.github/workflows/test.yml`:

```yaml
name: Test Suite

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

env:
  DATABASE_URL: postgresql://test:test@localhost:5433/repotoire_test
  REDIS_URL: redis://localhost:6380/0
  REPOTOIRE_NEO4J_URI: bolt://localhost:6381

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: pytest tests/unit -v --cov=repotoire --cov-report=xml
      - uses: codecov/codecov-action@v4
        with:
          files: coverage.xml

  integration-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: repotoire_test
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        ports:
          - 5433:5432
        options: --health-cmd pg_isready --health-interval 10s --health-timeout 5s --health-retries 5
      redis:
        image: redis:7-alpine
        ports:
          - 6380:6379
      falkordb:
        image: falkordb/falkordb:latest
        ports:
          - 6381:6379

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: alembic upgrade head
      - run: pytest tests/integration -v --cov=repotoire --cov-report=xml
      - uses: codecov/codecov-action@v4

  e2e-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: repotoire_test
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        ports:
          - 5433:5432
      redis:
        image: redis:7-alpine
        ports:
          - 6380:6379
      falkordb:
        image: falkordb/falkordb:latest
        ports:
          - 6381:6379

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: npm ci
      - run: npx playwright install --with-deps
      - run: alembic upgrade head
      - run: uvicorn repotoire.api.app:app --host 0.0.0.0 --port 8000 &
      - run: sleep 5
      - run: npm run build && npm run start &
      - run: sleep 5
      - run: npx playwright test
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: playwright-report
          path: playwright-report/
```

---

## Test Coverage Goals

| Area | Target | Description |
|------|--------|-------------|
| Authentication | 100% | Sign-in, sign-up, API keys, CLI auth |
| Billing | 100% | Checkout, subscriptions, usage |
| Core API | 90% | All 19 route modules |
| GitHub Integration | 90% | Webhooks, PR comments |
| Webhooks | 90% | CRUD, deliveries, retries |
| UI Flows | 80% | Onboarding, dashboard, settings |
| Workers | 80% | Task execution, retries |

---

## File Structure

```
tests/
├── conftest.py                    # Global fixtures & mocks
├── factories.py                   # All 19 model factories
├── integration/
│   ├── test_auth.py              # CLI auth, API keys
│   ├── test_organizations.py     # Org CRUD, team
│   ├── test_repositories.py      # Repository CRUD
│   ├── test_analysis.py          # Analysis, findings
│   ├── test_billing.py           # Checkout, subscriptions
│   ├── test_github.py            # GitHub webhooks
│   ├── test_customer_webhooks.py # Customer webhooks
│   ├── test_audit.py             # Audit logs
│   ├── test_status.py            # Status page
│   ├── test_changelog.py         # Changelog
│   ├── test_analytics.py         # Analytics
│   ├── test_code.py              # Code search/RAG
│   ├── test_fixes.py             # Auto-fix
│   ├── test_account.py           # Account, GDPR
│   ├── test_sandbox.py           # E2B sandbox
│   ├── test_historical.py        # Historical analysis
│   └── test_usage.py             # Rate limiting
└── e2e/
    ├── helpers/
    │   ├── auth.ts
    │   ├── github.ts
    │   └── stripe.ts
    ├── auth.spec.ts
    ├── onboarding.spec.ts
    ├── analysis.spec.ts
    ├── billing.spec.ts
    ├── webhooks.spec.ts
    └── settings.spec.ts

playwright.config.ts
docker-compose.test.yml
.github/workflows/test.yml
```

---

## Acceptance Criteria

- [ ] Playwright setup with browser tests for all critical flows
- [ ] pytest integration tests covering all 19 API route modules
- [ ] Factory Boy factories for all 19 database models
- [ ] Docker Compose test environment with PostgreSQL, Redis, FalkorDB
- [ ] CI/CD pipeline running all test suites
- [ ] Mock implementations for Clerk, Stripe, GitHub, E2B
- [ ] Test coverage reporting with minimum 80% threshold
- [ ] Parallel test execution in CI
- [ ] Test artifacts (screenshots, traces) on failure

**Total: ~140+ tests covering all SaaS features**
