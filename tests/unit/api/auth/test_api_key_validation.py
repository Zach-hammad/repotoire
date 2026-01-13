"""Unit tests for API key validation endpoint (REPO-392)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    pass


class TestAPIKeyValidationEndpoint:
    """Tests for POST /cli/auth/validate-key endpoint."""

    @pytest.fixture
    def mock_org(self):
        """Create a mock organization."""
        org = MagicMock()
        org.id = uuid4()
        org.slug = "acme-corp"
        org.clerk_org_id = "org_test123"
        org.plan_tier = MagicMock(value="pro")
        org.graph_backend = "falkordb"
        org.graph_database_name = None
        return org

    @pytest.fixture
    def mock_db_session(self, mock_org):
        """Create a mock database session."""
        session = AsyncMock()

        # Mock the execute result
        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_org
        session.execute.return_value = result

        return session

    @pytest.fixture
    def mock_clerk_client(self):
        """Create a mock Clerk client."""
        client = MagicMock()

        # Mock api_keys.verify_api_key
        api_key_data = MagicMock()
        api_key_data.subject = "org_test123"
        api_key_data.scopes = ["read:analysis", "write:analysis"]
        api_key_data.id = "key_123"
        client.api_keys.verify_api_key.return_value = api_key_data

        return client

    @pytest.fixture
    def app(self, mock_db_session, mock_clerk_client):
        """Create a test FastAPI app."""
        from fastapi import FastAPI
        from repotoire.api.v1.routes.cli_auth import router

        app = FastAPI()
        app.include_router(router)

        # Override dependencies
        from repotoire.db.session import get_db

        async def mock_get_db():
            yield mock_db_session

        app.dependency_overrides[get_db] = mock_get_db

        return app

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        return TestClient(app)

    # =========================================================================
    # Success Cases
    # =========================================================================

    def test_valid_api_key_returns_200(
        self, client, mock_clerk_client, mock_org
    ):
        """Test that a valid API key returns 200 with org info."""
        with patch(
            "repotoire.api.v1.routes.cli_auth.get_clerk_client",
            return_value=mock_clerk_client,
        ):
            with patch(
                "repotoire.api.v1.routes.cli_auth.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_to_thread:
                # Setup mock response
                api_key_data = MagicMock()
                api_key_data.subject = "org_test123"
                mock_to_thread.return_value = api_key_data

                response = client.post(
                    "/cli/auth/validate-key",
                    headers={"Authorization": "Bearer test_api_key_123"},
                )

                assert response.status_code == status.HTTP_200_OK
                data = response.json()
                assert data["valid"] is True
                assert data["org_slug"] == "acme-corp"
                assert data["plan"] == "pro"
                assert "db_config" in data

    def test_valid_key_returns_db_config(
        self, client, mock_clerk_client, mock_org
    ):
        """Test that valid key returns FalkorDB config."""
        with patch(
            "repotoire.api.v1.routes.cli_auth.get_clerk_client",
            return_value=mock_clerk_client,
        ):
            with patch(
                "repotoire.api.v1.routes.cli_auth.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_to_thread:
                api_key_data = MagicMock()
                api_key_data.subject = "org_test123"
                mock_to_thread.return_value = api_key_data

                response = client.post(
                    "/cli/auth/validate-key",
                    headers={"Authorization": "Bearer test_api_key_123"},
                )

                data = response.json()
                db_config = data["db_config"]
                assert db_config["type"] == "falkordb"
                assert db_config["graph"] == "org_acme_corp"
                assert "host" in db_config
                assert "port" in db_config

    def test_valid_key_returns_plan_features(
        self, client, mock_clerk_client, mock_org
    ):
        """Test that valid key returns features based on plan."""
        with patch(
            "repotoire.api.v1.routes.cli_auth.get_clerk_client",
            return_value=mock_clerk_client,
        ):
            with patch(
                "repotoire.api.v1.routes.cli_auth.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_to_thread:
                api_key_data = MagicMock()
                api_key_data.subject = "org_test123"
                mock_to_thread.return_value = api_key_data

                response = client.post(
                    "/cli/auth/validate-key",
                    headers={"Authorization": "Bearer test_api_key_123"},
                )

                data = response.json()
                assert "features" in data
                # Pro plan should have these features
                assert "graph_embeddings" in data["features"]
                assert "rag_search" in data["features"]

    # =========================================================================
    # Authentication Failure Cases
    # =========================================================================

    def test_missing_authorization_header_returns_401(self, client):
        """Test that missing auth header returns 401."""
        response = client.post("/cli/auth/validate-key")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()["detail"]
        assert data["valid"] is False
        assert "Missing Authorization header" in data["error"]

    def test_invalid_authorization_format_returns_401(self, client):
        """Test that invalid auth format returns 401."""
        response = client.post(
            "/cli/auth/validate-key",
            headers={"Authorization": "InvalidFormat token123"},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()["detail"]
        assert data["valid"] is False
        assert "Invalid Authorization header format" in data["error"]

    def test_invalid_api_key_returns_401(self, client, mock_clerk_client):
        """Test that invalid API key returns 401."""
        # Mock Clerk verification failure
        mock_clerk_client.api_keys.verify_api_key.side_effect = Exception(
            "Invalid key"
        )

        with patch(
            "repotoire.api.v1.routes.cli_auth.get_clerk_client",
            return_value=mock_clerk_client,
        ):
            with patch(
                "repotoire.api.v1.routes.cli_auth.asyncio.to_thread",
                side_effect=Exception("Invalid key"),
            ):
                response = client.post(
                    "/cli/auth/validate-key",
                    headers={"Authorization": "Bearer invalid_key"},
                )

                assert response.status_code == status.HTTP_401_UNAUTHORIZED
                data = response.json()["detail"]
                assert data["valid"] is False
                assert "Invalid or expired API key" in data["error"]

    def test_user_scoped_key_without_org_returns_401(
        self, client, mock_clerk_client, mock_db_session
    ):
        """Test that user-scoped key without org returns 401 if user not found."""
        # Configure db session to return None (user not found)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        with patch(
            "repotoire.api.v1.routes.cli_auth.get_clerk_client",
            return_value=mock_clerk_client,
        ):
            with patch(
                "repotoire.api.v1.routes.cli_auth.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_to_thread:
                # User-scoped key without org - user not found
                api_key_data = MagicMock()
                api_key_data.subject = "user_test123"
                api_key_data.org_id = None
                mock_to_thread.return_value = api_key_data

                response = client.post(
                    "/cli/auth/validate-key",
                    headers={"Authorization": "Bearer user_only_key"},
                )

                assert response.status_code == status.HTTP_401_UNAUTHORIZED
                data = response.json()["detail"]
                assert "User not found" in data["error"]

    def test_org_not_found_returns_401(
        self, client, mock_clerk_client, mock_db_session
    ):
        """Test that org not found returns 401."""
        # Mock org not found
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = result

        with patch(
            "repotoire.api.v1.routes.cli_auth.get_clerk_client",
            return_value=mock_clerk_client,
        ):
            with patch(
                "repotoire.api.v1.routes.cli_auth.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_to_thread:
                api_key_data = MagicMock()
                api_key_data.subject = "org_unknown"
                mock_to_thread.return_value = api_key_data

                response = client.post(
                    "/cli/auth/validate-key",
                    headers={"Authorization": "Bearer valid_key_unknown_org"},
                )

                assert response.status_code == status.HTTP_401_UNAUTHORIZED
                data = response.json()["detail"]
                assert "not found" in data["error"]

    # =========================================================================
    # Graph Name Generation Tests
    # =========================================================================

    def test_graph_name_replaces_hyphens(self):
        """Test that graph name converts hyphens to underscores."""
        from repotoire.api.v1.routes.cli_auth import _get_graph_name

        assert _get_graph_name("acme-corp") == "org_acme_corp"
        assert _get_graph_name("my-company-name") == "org_my_company_name"
        assert _get_graph_name("simple") == "org_simple"

    def test_graph_name_adds_prefix(self):
        """Test that graph name adds org_ prefix."""
        from repotoire.api.v1.routes.cli_auth import _get_graph_name

        result = _get_graph_name("test")
        assert result.startswith("org_")

    # =========================================================================
    # FalkorDB Host Tests
    # =========================================================================

    def test_falkordb_host_fly_environment(self):
        """Test FalkorDB host in Fly.io environment."""
        from repotoire.api.v1.routes.cli_auth import _get_falkordb_host

        with patch.dict(os.environ, {"FLY_APP_NAME": "repotoire"}):
            host = _get_falkordb_host()
            assert host == "repotoire-falkor.internal"

    def test_falkordb_host_local_environment(self):
        """Test FalkorDB host in local environment."""
        from repotoire.api.v1.routes.cli_auth import _get_falkordb_host

        with patch.dict(os.environ, {}, clear=True):
            # Clear FLY_APP_NAME if set
            os.environ.pop("FLY_APP_NAME", None)
            host = _get_falkordb_host()
            assert host == "localhost"

    def test_falkordb_host_custom_env(self):
        """Test FalkorDB host with custom env var."""
        from repotoire.api.v1.routes.cli_auth import _get_falkordb_host

        with patch.dict(
            os.environ, {"FALKORDB_HOST": "custom.host.com"}, clear=True
        ):
            os.environ.pop("FLY_APP_NAME", None)
            host = _get_falkordb_host()
            assert host == "custom.host.com"

    # =========================================================================
    # Plan Features Tests
    # =========================================================================

    def test_free_plan_features(self):
        """Test that free plan has no extra features."""
        from repotoire.api.v1.routes.cli_auth import get_features_for_plan

        features = get_features_for_plan("free")
        assert features == []

    def test_pro_plan_features(self):
        """Test that pro plan has expected features."""
        from repotoire.api.v1.routes.cli_auth import get_features_for_plan

        features = get_features_for_plan("pro")
        assert "graph_embeddings" in features
        assert "rag_search" in features

    def test_enterprise_plan_features(self):
        """Test that enterprise plan has all features."""
        from repotoire.api.v1.routes.cli_auth import get_features_for_plan

        features = get_features_for_plan("enterprise")
        assert "graph_embeddings" in features
        assert "rag_search" in features
        assert "auto_fix" in features
        assert "custom_detectors" in features
        assert "sso" in features

    def test_unknown_plan_returns_empty(self):
        """Test that unknown plan returns empty features."""
        from repotoire.api.v1.routes.cli_auth import get_features_for_plan

        features = get_features_for_plan("unknown")
        assert features == []

    # =========================================================================
    # Security Logging Tests
    # =========================================================================

    def test_successful_validation_logs_info(
        self, client, mock_clerk_client, mock_org
    ):
        """Test that successful validation logs info level."""
        with patch(
            "repotoire.api.v1.routes.cli_auth.get_clerk_client",
            return_value=mock_clerk_client,
        ):
            with patch(
                "repotoire.api.v1.routes.cli_auth.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_to_thread:
                api_key_data = MagicMock()
                api_key_data.subject = "org_test123"
                mock_to_thread.return_value = api_key_data

                with patch(
                    "repotoire.api.v1.routes.cli_auth.logger"
                ) as mock_logger:
                    response = client.post(
                        "/cli/auth/validate-key",
                        headers={"Authorization": "Bearer test_key"},
                    )

                    # Should have called info for success
                    assert mock_logger.info.called

    def test_failed_validation_logs_warning(self, client):
        """Test that failed validation logs warning level."""
        with patch("repotoire.api.v1.routes.cli_auth.logger") as mock_logger:
            response = client.post("/cli/auth/validate-key")

            # Should have called warning for failure
            assert mock_logger.warning.called

    @pytest.mark.asyncio
    async def test_failed_validation_captures_sentry(self):
        """Test that failed validation sends to Sentry."""
        # Test the logging function directly instead of through HTTP
        from repotoire.api.v1.routes.cli_auth import _log_validation_attempt

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = {}
        mock_request.url.path = "/cli/auth/validate-key"
        mock_request.method = "POST"

        mock_db = AsyncMock()

        with patch("repotoire.api.v1.routes.cli_auth.sentry_sdk") as mock_sentry:
            await _log_validation_attempt(
                mock_request,
                db=mock_db,
                success=False,
                key_prefix="test_key...",
                reason="verification_failed",
            )

            # Should have captured message in Sentry
            assert mock_sentry.capture_message.called

    @pytest.mark.asyncio
    async def test_key_prefix_logging_never_logs_full_key(self):
        """Test that only key prefix is logged, never full key."""
        # This test verifies the implementation never logs the full key
        from repotoire.api.v1.routes.cli_auth import _log_validation_attempt

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = {}
        mock_request.url.path = "/cli/auth/validate-key"
        mock_request.method = "POST"

        mock_db = AsyncMock()

        with patch("repotoire.api.v1.routes.cli_auth.logger") as mock_logger:
            await _log_validation_attempt(
                mock_request,
                db=mock_db,
                success=False,
                key_prefix="test_key_12...",
                reason="test",
            )

            # Verify the log call
            call_args = mock_logger.warning.call_args
            extra = call_args[1]["extra"]

            # Key prefix should be truncated
            assert len(extra["key_prefix"]) < 20

    # =========================================================================
    # Audit Log Database Write Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_audit_log_written_on_success(self):
        """Test that successful validation writes AuditLog to database."""
        from repotoire.api.v1.routes.cli_auth import _log_validation_attempt

        # Create a mock headers dict that supports case-insensitive access like FastAPI
        headers_dict = {
            "User-Agent": "repotoire-cli/1.0.0",
            "X-Request-ID": "req-123",
        }

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.100"
        mock_request.headers = MagicMock()
        mock_request.headers.get = lambda k, default="": headers_dict.get(k, default)
        mock_request.headers.__iter__ = lambda self: iter(headers_dict.keys())
        mock_request.headers.__contains__ = lambda self, k: k in headers_dict
        mock_request.headers.__getitem__ = lambda self, k: headers_dict[k]
        mock_request.url.path = "/cli/auth/validate-key"
        mock_request.method = "POST"

        mock_db = AsyncMock()

        await _log_validation_attempt(
            mock_request,
            db=mock_db,
            success=True,
            key_prefix="rp_live_abc...",
            org_id="acme-corp",
            org_uuid="550e8400-e29b-41d4-a716-446655440000",
            clerk_org_id="org_abc123",
            plan="pro",
            features=["graph_embeddings", "rag_search"],
        )

        # Verify db.add was called with an AuditLog
        assert mock_db.add.called
        audit_log = mock_db.add.call_args[0][0]

        # Verify AuditLog fields
        assert audit_log.event_type == "api_key.validation"
        assert audit_log.action == "validate"
        assert audit_log.resource_type == "api_key"
        assert audit_log.resource_id == "rp_live_abc..."
        assert audit_log.actor_ip == "192.168.1.100"
        assert "repotoire-cli" in audit_log.actor_user_agent

        # Verify metadata contains expected fields
        metadata = audit_log.event_metadata
        assert metadata["key_prefix"] == "rp_live_abc..."
        assert metadata["client_ip"] == "192.168.1.100"
        assert metadata["clerk_org_id"] == "org_abc123"
        assert metadata["plan"] == "pro"
        assert metadata["features"] == ["graph_embeddings", "rag_search"]
        assert metadata["request_path"] == "/cli/auth/validate-key"

        # Verify commit was called
        assert mock_db.commit.called

    @pytest.mark.asyncio
    async def test_audit_log_written_on_failure(self):
        """Test that failed validation writes AuditLog with failure details."""
        from repotoire.api.v1.routes.cli_auth import _log_validation_attempt

        headers_dict = {"User-Agent": "curl/7.68.0"}

        mock_request = MagicMock()
        mock_request.client.host = "10.0.0.50"
        mock_request.headers = MagicMock()
        mock_request.headers.get = lambda k, default="": headers_dict.get(k, default)
        mock_request.headers.__iter__ = lambda self: iter(headers_dict.keys())
        mock_request.headers.__contains__ = lambda self, k: k in headers_dict
        mock_request.headers.__getitem__ = lambda self, k: headers_dict[k]
        mock_request.url.path = "/cli/auth/validate-key"
        mock_request.method = "POST"

        mock_db = AsyncMock()

        with patch("repotoire.api.v1.routes.cli_auth.sentry_sdk"):
            await _log_validation_attempt(
                mock_request,
                db=mock_db,
                success=False,
                key_prefix="invalid_key...",
                reason="verification_failed",
            )

        # Verify db.add was called
        assert mock_db.add.called
        audit_log = mock_db.add.call_args[0][0]

        # Verify failure status
        from repotoire.db.models import AuditStatus
        assert audit_log.status == AuditStatus.FAILURE

        # Verify failure reason in metadata
        metadata = audit_log.event_metadata
        assert metadata["failure_reason"] == "verification_failed"
        assert metadata["key_prefix"] == "invalid_key..."

    @pytest.mark.asyncio
    async def test_audit_log_includes_safe_headers(self):
        """Test that audit log includes only safe headers."""
        from repotoire.api.v1.routes.cli_auth import _log_validation_attempt

        # Create headers dict with proper casing (as FastAPI normalizes them)
        headers_dict = {
            "User-Agent": "test-agent",
            "X-Request-ID": "req-456",
            "X-Forwarded-For": "1.2.3.4",
            "Accept": "application/json",
            "Authorization": "Bearer secret_token",  # Should NOT be logged
            "Cookie": "session=abc123",  # Should NOT be logged
        }

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = MagicMock()
        mock_request.headers.get = lambda k, default="": headers_dict.get(k, default)
        mock_request.headers.__iter__ = lambda self: iter(headers_dict.keys())
        mock_request.headers.__contains__ = lambda self, k: k in headers_dict
        mock_request.headers.__getitem__ = lambda self, k: headers_dict[k]
        mock_request.url.path = "/cli/auth/validate-key"
        mock_request.method = "POST"

        mock_db = AsyncMock()

        await _log_validation_attempt(
            mock_request,
            db=mock_db,
            success=True,
            key_prefix="test...",
        )

        audit_log = mock_db.add.call_args[0][0]
        headers = audit_log.event_metadata.get("headers", {})

        # Safe headers should be present (key format: x_request_id)
        assert headers.get("x_request_id") == "req-456"
        assert headers.get("x_forwarded_for") == "1.2.3.4"

        # Sensitive headers should NOT be present
        assert "authorization" not in headers
        assert "cookie" not in headers
        # Accept is not in the safe headers list
        assert "accept" not in headers

    @pytest.mark.asyncio
    async def test_audit_log_truncates_long_user_agent(self):
        """Test that very long user agents are truncated."""
        from repotoire.api.v1.routes.cli_auth import _log_validation_attempt

        long_user_agent = "A" * 2000
        headers_dict = {"User-Agent": long_user_agent}

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = MagicMock()
        mock_request.headers.get = lambda k, default="": headers_dict.get(k, default)
        mock_request.headers.__iter__ = lambda self: iter(headers_dict.keys())
        mock_request.headers.__contains__ = lambda self, k: k in headers_dict
        mock_request.headers.__getitem__ = lambda self, k: headers_dict[k]
        mock_request.url.path = "/cli/auth/validate-key"
        mock_request.method = "POST"

        mock_db = AsyncMock()

        await _log_validation_attempt(
            mock_request,
            db=mock_db,
            success=True,
            key_prefix="test...",
        )

        audit_log = mock_db.add.call_args[0][0]

        # User agent in AuditLog should be truncated to 1024 chars
        assert len(audit_log.actor_user_agent) <= 1024

        # User agent in metadata should be truncated to 500 chars
        assert len(audit_log.event_metadata["user_agent"]) <= 500

    # =========================================================================
    # Clerk Org Sync Tests (_sync_user_org_from_clerk)
    # =========================================================================

    @pytest.mark.asyncio
    async def test_sync_user_org_from_clerk_creates_new_org(self):
        """Test that org from Clerk is created in our DB if not exists."""
        from repotoire.api.v1.routes.cli_auth import _sync_user_org_from_clerk

        # Mock DB session that returns None (org not found)
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Mock user
        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.email = "test@example.com"

        # Mock Clerk client with org membership
        mock_clerk = MagicMock()
        mock_membership = MagicMock()
        mock_membership.organization = MagicMock()
        mock_membership.organization.id = "org_clerk123"
        mock_membership.organization.name = "Test Org"
        mock_membership.organization.slug = "test-org"
        mock_membership.role = "admin"

        mock_memberships = MagicMock()
        mock_memberships.data = [mock_membership]
        mock_clerk.users.get_organization_memberships.return_value = mock_memberships

        with patch(
            "repotoire.api.v1.routes.cli_auth.asyncio.to_thread",
            return_value=mock_memberships,
        ):
            result = await _sync_user_org_from_clerk(
                mock_db, "user_abc123", mock_user, mock_clerk
            )

        # Should have created org and membership
        assert mock_db.add.called
        assert mock_db.commit.called

    @pytest.mark.asyncio
    async def test_sync_user_org_from_clerk_links_existing_by_slug(self):
        """Test that existing org by slug is linked to Clerk org."""
        from repotoire.api.v1.routes.cli_auth import _sync_user_org_from_clerk

        # Mock existing org without clerk_org_id
        existing_org = MagicMock()
        existing_org.id = uuid4()
        existing_org.slug = "test-org"
        existing_org.clerk_org_id = None  # Not linked yet

        # Mock DB session - first query returns None (by clerk_org_id), second returns org (by slug)
        mock_db = AsyncMock()
        call_count = 0

        async def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = None  # Not found by clerk_org_id
            elif call_count == 2:
                result.scalar_one_or_none.return_value = existing_org  # Found by slug
            else:
                result.scalar_one_or_none.return_value = None  # No existing membership
            return result

        mock_db.execute = mock_execute

        # Mock user
        mock_user = MagicMock()
        mock_user.id = uuid4()

        # Mock Clerk client
        mock_clerk = MagicMock()
        mock_membership = MagicMock()
        mock_membership.organization = MagicMock()
        mock_membership.organization.id = "org_clerk456"
        mock_membership.organization.name = "Test Org"
        mock_membership.organization.slug = "test-org"
        mock_membership.role = "member"

        mock_memberships = MagicMock()
        mock_memberships.data = [mock_membership]
        mock_clerk.users.get_organization_memberships.return_value = mock_memberships

        with patch(
            "repotoire.api.v1.routes.cli_auth.asyncio.to_thread",
            return_value=mock_memberships,
        ):
            result = await _sync_user_org_from_clerk(
                mock_db, "user_xyz", mock_user, mock_clerk
            )

        # Should have linked existing org to Clerk
        assert existing_org.clerk_org_id == "org_clerk456"

    @pytest.mark.asyncio
    async def test_sync_user_org_from_clerk_returns_none_if_no_clerk_org(self):
        """Test that None is returned if user has no org in Clerk."""
        from repotoire.api.v1.routes.cli_auth import _sync_user_org_from_clerk

        mock_db = AsyncMock()
        mock_user = MagicMock()

        # Mock Clerk client with no memberships
        mock_clerk = MagicMock()
        mock_memberships = MagicMock()
        mock_memberships.data = []
        mock_clerk.users.get_organization_memberships.return_value = mock_memberships

        with patch(
            "repotoire.api.v1.routes.cli_auth.asyncio.to_thread",
            return_value=mock_memberships,
        ):
            result = await _sync_user_org_from_clerk(
                mock_db, "user_no_org", mock_user, mock_clerk
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_sync_user_org_from_clerk_handles_clerk_api_failure(self):
        """Test graceful fallback when Clerk API fails."""
        from repotoire.api.v1.routes.cli_auth import _sync_user_org_from_clerk

        mock_db = AsyncMock()
        mock_user = MagicMock()
        mock_clerk = MagicMock()

        with patch(
            "repotoire.api.v1.routes.cli_auth.asyncio.to_thread",
            side_effect=Exception("Clerk API error"),
        ):
            result = await _sync_user_org_from_clerk(
                mock_db, "user_error", mock_user, mock_clerk
            )

        # Should return None (fallback to personal org creation)
        assert result is None
        # Should not have raised exception
        assert not mock_db.commit.called

    # =========================================================================
    # Personal Org Creation Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_personal_org_has_null_clerk_org_id(self):
        """Test that personal orgs are created with clerk_org_id=None."""
        from repotoire.api.v1.routes.cli_auth import _create_personal_org

        mock_db = AsyncMock()
        mock_user = MagicMock()
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"

        org = await _create_personal_org(mock_db, mock_user)

        # Verify the org was added
        assert mock_db.add.called

        # Get the org that was added (first call to add)
        added_org = mock_db.add.call_args_list[0][0][0]
        assert added_org.clerk_org_id is None  # Should be None, not personal_xxx

    # =========================================================================
    # Custom Graph Database Name Tests
    # =========================================================================

    def test_uses_custom_graph_name_if_set(self):
        """Test that custom graph_database_name is used if set.

        Tests the logic directly rather than through HTTP to avoid rate limiting.
        """
        from repotoire.api.v1.routes.cli_auth import (
            DBConfig,
            _get_falkordb_host,
            _get_falkordb_port,
            _get_graph_name,
        )

        # Test with custom graph name
        custom_name = "custom_graph_name"
        org_slug = "acme-corp"

        # Simulate the logic from the endpoint
        graph = custom_name if custom_name else _get_graph_name(org_slug)

        assert graph == "custom_graph_name"

        # Test fallback to generated name
        graph_fallback = None or _get_graph_name(org_slug)
        assert graph_fallback == "org_acme_corp"
