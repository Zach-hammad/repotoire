"""Unit tests for tenant resolver automatic tenant resolution.

Tests the resolution chain: API key → env var → config → default.

REPO-600: Multi-tenant data isolation implementation.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4, UUID

from repotoire.tenant.context import (
    clear_tenant_context,
    get_tenant_context,
    set_tenant_context,
    reset_tenant_context,
)
from repotoire.tenant.resolver import (
    resolve_tenant_identity,
    resolve_and_set_tenant,
    set_tenant_from_auth_info,
    get_tenant_graph_name,
    is_default_tenant,
    DEFAULT_TENANT_ID,
    DEFAULT_TENANT_SLUG,
)


class TestResolveTenantIdentity:
    """Tests for resolve_tenant_identity function."""

    def setup_method(self):
        """Clear context and env vars before each test."""
        clear_tenant_context()
        # Remove env vars if set
        for var in ["REPOTOIRE_TENANT_ID", "REPOTOIRE_TENANT_SLUG"]:
            if var in os.environ:
                del os.environ[var]

    def teardown_method(self):
        """Clean up after each test."""
        clear_tenant_context()
        for var in ["REPOTOIRE_TENANT_ID", "REPOTOIRE_TENANT_SLUG"]:
            if var in os.environ:
                del os.environ[var]

    def test_returns_existing_context_if_set(self):
        """Test that existing context is returned if already set."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id, org_slug="existing-org")

        try:
            resolved_id, resolved_slug = resolve_tenant_identity()

            assert resolved_id == org_id
            assert resolved_slug == "existing-org"
        finally:
            reset_tenant_context(token)

    def test_resolves_from_env_var(self):
        """Test resolution from REPOTOIRE_TENANT_ID env var."""
        env_org_id = uuid4()
        os.environ["REPOTOIRE_TENANT_ID"] = str(env_org_id)
        os.environ["REPOTOIRE_TENANT_SLUG"] = "env-org"

        resolved_id, resolved_slug = resolve_tenant_identity()

        assert resolved_id == env_org_id
        assert resolved_slug == "env-org"

    def test_resolves_env_var_without_slug(self):
        """Test resolution from env var without slug."""
        env_org_id = uuid4()
        os.environ["REPOTOIRE_TENANT_ID"] = str(env_org_id)

        resolved_id, resolved_slug = resolve_tenant_identity()

        assert resolved_id == env_org_id
        assert resolved_slug is None

    def test_invalid_env_var_uuid_falls_through(self):
        """Test that invalid UUID in env var falls through to next source."""
        os.environ["REPOTOIRE_TENANT_ID"] = "not-a-valid-uuid"

        # Should fall through to default
        resolved_id, resolved_slug = resolve_tenant_identity()

        assert resolved_id == DEFAULT_TENANT_ID
        assert resolved_slug == DEFAULT_TENANT_SLUG

    @patch("repotoire.config.load_config")
    def test_resolves_from_config(self, mock_load_config):
        """Test resolution from config file."""
        config_org_id = uuid4()
        mock_config = MagicMock()
        mock_config.tenant.id = str(config_org_id)
        mock_config.tenant.slug = "config-org"
        mock_load_config.return_value = mock_config

        resolved_id, resolved_slug = resolve_tenant_identity()

        assert resolved_id == config_org_id
        assert resolved_slug == "config-org"

    @patch("repotoire.config.load_config")
    def test_resolves_to_default_when_no_source(self, mock_load_config):
        """Test resolution falls back to default tenant."""
        mock_config = MagicMock()
        mock_config.tenant.id = None
        mock_config.tenant.slug = None
        mock_load_config.return_value = mock_config

        resolved_id, resolved_slug = resolve_tenant_identity()

        assert resolved_id == DEFAULT_TENANT_ID
        assert resolved_slug == DEFAULT_TENANT_SLUG

    def test_resolution_priority_existing_over_env(self):
        """Test existing context takes priority over env var."""
        existing_id = uuid4()
        env_id = uuid4()

        token = set_tenant_context(org_id=existing_id, org_slug="existing")
        os.environ["REPOTOIRE_TENANT_ID"] = str(env_id)

        try:
            resolved_id, _ = resolve_tenant_identity()
            assert resolved_id == existing_id
        finally:
            reset_tenant_context(token)


class TestResolveAndSetTenant:
    """Tests for resolve_and_set_tenant function."""

    def setup_method(self):
        """Clear context before each test."""
        clear_tenant_context()
        for var in ["REPOTOIRE_TENANT_ID", "REPOTOIRE_TENANT_SLUG"]:
            if var in os.environ:
                del os.environ[var]

    def teardown_method(self):
        """Clean up after each test."""
        clear_tenant_context()
        for var in ["REPOTOIRE_TENANT_ID", "REPOTOIRE_TENANT_SLUG"]:
            if var in os.environ:
                del os.environ[var]

    def test_sets_context_from_env(self):
        """Test that context is set from resolved identity."""
        env_org_id = uuid4()
        os.environ["REPOTOIRE_TENANT_ID"] = str(env_org_id)
        os.environ["REPOTOIRE_TENANT_SLUG"] = "env-org"

        ctx = resolve_and_set_tenant()

        assert ctx.org_id == env_org_id
        assert ctx.org_slug == "env-org"

        # Verify context was actually set
        current = get_tenant_context()
        assert current is not None
        assert current.org_id == env_org_id

    def test_returns_existing_context(self):
        """Test that existing context is returned without modification."""
        existing_id = uuid4()
        token = set_tenant_context(org_id=existing_id, org_slug="existing")

        try:
            ctx = resolve_and_set_tenant()

            assert ctx.org_id == existing_id
            assert ctx.org_slug == "existing"
        finally:
            reset_tenant_context(token)


class TestSetTenantFromAuthInfo:
    """Tests for set_tenant_from_auth_info function."""

    def setup_method(self):
        """Clear context before each test."""
        clear_tenant_context()

    def teardown_method(self):
        """Clean up after each test."""
        clear_tenant_context()

    def test_sets_context_from_auth_info(self):
        """Test setting context from CloudAuthInfo."""
        import time
        from repotoire.graph.factory import CloudAuthInfo

        org_id = uuid4()
        # Create real CloudAuthInfo instance
        auth_info = CloudAuthInfo(
            org_id=str(org_id),
            org_slug="auth-org",
            plan="pro",
            features=["search", "analytics"],
            db_config={},
            cached_at=time.time(),
            user=None,
        )

        ctx = set_tenant_from_auth_info(auth_info)

        assert ctx.org_id == org_id
        assert ctx.org_slug == "auth-org"
        assert ctx.metadata["plan"] == "pro"
        assert ctx.metadata["source"] == "api_key"

    def test_context_is_set_globally(self):
        """Test that context is available via get_tenant_context."""
        import time
        from repotoire.graph.factory import CloudAuthInfo

        org_id = uuid4()
        auth_info = CloudAuthInfo(
            org_id=str(org_id),
            org_slug="auth-org",
            plan="free",
            features=[],
            db_config={},
            cached_at=time.time(),
            user=None,
        )

        set_tenant_from_auth_info(auth_info)

        current = get_tenant_context()
        assert current is not None
        assert current.org_id == org_id


class TestGetTenantGraphName:
    """Tests for get_tenant_graph_name function."""

    def setup_method(self):
        """Clear context before each test."""
        clear_tenant_context()

    def teardown_method(self):
        """Clean up after each test."""
        clear_tenant_context()

    def test_generates_graph_name_from_slug(self):
        """Test graph name generation with slug."""
        org_id = UUID("550e8400-e29b-41d4-a716-446655440000")
        token = set_tenant_context(org_id=org_id, org_slug="acme-corp")

        try:
            name = get_tenant_graph_name()
            assert name == "org_acme_corp"
        finally:
            reset_tenant_context(token)

    def test_generates_graph_name_without_slug(self):
        """Test graph name generation without slug uses org_id hash."""
        org_id = UUID("550e8400-e29b-41d4-a716-446655440000")
        token = set_tenant_context(org_id=org_id)

        try:
            name = get_tenant_graph_name()
            # Should start with org_ and contain hash
            assert name.startswith("org_")
            assert len(name) > 4  # org_ + some hash
        finally:
            reset_tenant_context(token)

    def test_sanitizes_slug(self):
        """Test that slug is sanitized for graph name."""
        org_id = UUID("550e8400-e29b-41d4-a716-446655440000")
        token = set_tenant_context(org_id=org_id, org_slug="Acme Corp Inc.")

        try:
            name = get_tenant_graph_name()
            # Should be lowercase and special chars replaced
            assert "acme" in name.lower()
            assert " " not in name
            assert "." not in name
        finally:
            reset_tenant_context(token)

    def test_raises_without_context(self):
        """Test that RuntimeError is raised without context."""
        with pytest.raises(RuntimeError) as exc_info:
            get_tenant_graph_name()

        assert "No tenant context set" in str(exc_info.value)


class TestIsDefaultTenant:
    """Tests for is_default_tenant function."""

    def setup_method(self):
        """Clear context before each test."""
        clear_tenant_context()

    def teardown_method(self):
        """Clean up after each test."""
        clear_tenant_context()

    def test_default_tenant_returns_true(self):
        """Test that default tenant ID is recognized."""
        token = set_tenant_context(org_id=DEFAULT_TENANT_ID, org_slug=DEFAULT_TENANT_SLUG)

        try:
            assert is_default_tenant() is True
        finally:
            reset_tenant_context(token)

    def test_non_default_tenant_returns_false(self):
        """Test that non-default tenant ID returns False."""
        other_id = uuid4()
        token = set_tenant_context(org_id=other_id, org_slug="other-org")

        try:
            assert is_default_tenant() is False
        finally:
            reset_tenant_context(token)

    def test_no_context_returns_true(self):
        """Test that no context is treated as default."""
        # No context set
        assert is_default_tenant() is True

    def test_default_tenant_uuid_value(self):
        """Test that DEFAULT_TENANT_ID is the expected nil UUID."""
        assert DEFAULT_TENANT_ID == UUID("00000000-0000-0000-0000-000000000000")

    def test_default_tenant_slug_value(self):
        """Test that DEFAULT_TENANT_SLUG is the expected value."""
        assert DEFAULT_TENANT_SLUG == "default"
