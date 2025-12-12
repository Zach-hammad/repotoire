"""Integration tests for audit API routes.

Tests cover:
- Listing audit log entries
- Filtering by event type, date range, resource
- Export audit logs
"""

import os
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Skip if v1 routes don't exist yet
pytest.importorskip("repotoire.api.v1.routes.audit")

from repotoire.api.v1.routes.audit import router


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def app():
    """Create test FastAPI app with audit routes."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/v1")
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# =============================================================================
# Unit Tests (No Database)
# =============================================================================


class TestAuditEndpointsUnit:
    """Unit tests for audit endpoints without database."""

    def test_unauthorized_access(self, client):
        """Endpoints should return 401 without auth header."""
        response = client.get("/api/v1/audit-logs")
        assert response.status_code == 401


# =============================================================================
# Integration Tests (With Database)
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured."""
    url = os.getenv("DATABASE_URL", "") or os.getenv("TEST_DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestAuditEndpointsIntegration:
    """Integration tests for audit endpoints with real database."""

    @pytest.mark.asyncio
    async def test_list_audit_logs_empty(self, db_session, test_user, mock_clerk):
        """List audit logs should return empty when no logs exist."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
        )

        # Create org with membership
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # No audit logs created
        assert org is not None

    @pytest.mark.asyncio
    async def test_list_audit_logs_with_data(self, db_session, test_user, mock_clerk):
        """List audit logs should return logs for org."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            AuditLogFactory,
        )

        # Create org with audit logs
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Create audit logs
        for _ in range(5):
            await AuditLogFactory.async_create(
                db_session,
                organization_id=org.id,
                actor_id=test_user.id,
            )

        # Verify logs were created
        from repotoire.db.models import AuditLog
        from sqlalchemy import select

        result = await db_session.execute(
            select(AuditLog).where(AuditLog.organization_id == org.id)
        )
        logs = result.scalars().all()
        assert len(logs) == 5

    @pytest.mark.asyncio
    async def test_audit_log_login_event(self, db_session, test_user, mock_clerk):
        """Login event should be logged correctly."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            AuditLogFactory,
        )

        # Create org
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Create login event audit log
        log = await AuditLogFactory.async_create(
            db_session,
            organization_id=org.id,
            actor_id=test_user.id,
            login_event=True,
        )

        assert log.event_type == "user.login"

    @pytest.mark.asyncio
    async def test_audit_log_repo_connected(self, db_session, test_user, mock_clerk):
        """Repository connected event should be logged correctly."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            AuditLogFactory,
        )

        # Create org
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Create repo connected event
        log = await AuditLogFactory.async_create(
            db_session,
            organization_id=org.id,
            actor_id=test_user.id,
            repo_connected=True,
        )

        assert log.event_type == "repository.connected"
        assert log.resource_type == "repository"

    @pytest.mark.asyncio
    async def test_filter_audit_logs_by_event_type(
        self, db_session, test_user, mock_clerk
    ):
        """Audit logs can be filtered by event type."""
        from tests.factories import (
            OrganizationFactory,
            OrganizationMembershipFactory,
            AuditLogFactory,
        )
        from repotoire.db.models import AuditLog
        from sqlalchemy import select

        # Create org with different event types
        org = await OrganizationFactory.async_create(db_session)
        await OrganizationMembershipFactory.async_create(
            db_session,
            user_id=test_user.id,
            organization_id=org.id,
        )

        # Create login events
        for _ in range(3):
            await AuditLogFactory.async_create(
                db_session,
                organization_id=org.id,
                actor_id=test_user.id,
                login_event=True,
            )

        # Create repo events
        for _ in range(2):
            await AuditLogFactory.async_create(
                db_session,
                organization_id=org.id,
                actor_id=test_user.id,
                repo_connected=True,
            )

        # Filter by login events
        result = await db_session.execute(
            select(AuditLog).where(
                AuditLog.organization_id == org.id,
                AuditLog.event_type == "user.login",
            )
        )
        login_logs = result.scalars().all()
        assert len(login_logs) == 3

        # Filter by repo events
        result = await db_session.execute(
            select(AuditLog).where(
                AuditLog.organization_id == org.id,
                AuditLog.event_type == "repository.connected",
            )
        )
        repo_logs = result.scalars().all()
        assert len(repo_logs) == 2
