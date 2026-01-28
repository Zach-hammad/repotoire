"""Unit tests for the multi-tenant GraphClientFactory."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from uuid import UUID, uuid4

from repotoire.graph.tenant_factory import (
    GraphClientFactory,
    get_factory,
    reset_factory,
    get_client_for_org,
)


class TestGraphNameGeneration:
    """Tests for graph/database name generation from org ID and slug.

    REPO-500: Graph names now include org_id suffix for collision prevention.
    Format: org_{sanitized_slug}_{8_char_md5_of_org_id}
    """

    def test_generate_graph_name_from_slug(self):
        """Test graph name generation from org slug includes org_id suffix."""
        import hashlib
        factory = GraphClientFactory()
        org_id = uuid4()
        expected_suffix = hashlib.md5(str(org_id).encode()).hexdigest()[:8]

        name = factory._generate_graph_name(org_id, "acme-corp")
        assert name == f"org_acme_corp_{expected_suffix}"

    def test_generate_graph_name_from_slug_with_dots(self):
        """Test graph name generation handles dots in slug."""
        import hashlib
        factory = GraphClientFactory()
        org_id = uuid4()
        expected_suffix = hashlib.md5(str(org_id).encode()).hexdigest()[:8]

        name = factory._generate_graph_name(org_id, "acme.corp.inc")
        assert name == f"org_acme_corp_inc_{expected_suffix}"

    def test_generate_graph_name_from_slug_with_special_chars(self):
        """Test graph name sanitization of special characters."""
        import hashlib
        factory = GraphClientFactory()
        org_id = uuid4()
        expected_suffix = hashlib.md5(str(org_id).encode()).hexdigest()[:8]

        name = factory._generate_graph_name(org_id, "acme--corp__test")
        assert name == f"org_acme_corp_test_{expected_suffix}"

    def test_generate_graph_name_from_uuid(self):
        """Test graph name fallback to UUID when no slug uses 16 char MD5."""
        import hashlib
        factory = GraphClientFactory()
        org_id = UUID("550e8400-e29b-41d4-a716-446655440000")
        expected_suffix = hashlib.md5(str(org_id).encode()).hexdigest()[:16]

        name = factory._generate_graph_name(org_id, None)
        assert name == f"org_{expected_suffix}"

    def test_generate_graph_name_lowercase(self):
        """Test graph names are lowercased."""
        import hashlib
        factory = GraphClientFactory()
        org_id = uuid4()
        expected_suffix = hashlib.md5(str(org_id).encode()).hexdigest()[:8]

        name = factory._generate_graph_name(org_id, "AcMeCorp")
        assert name == f"org_acmecorp_{expected_suffix}"

    def test_generate_graph_name_strips_underscores(self):
        """Test leading/trailing underscores are stripped."""
        import hashlib
        factory = GraphClientFactory()
        org_id = uuid4()
        expected_suffix = hashlib.md5(str(org_id).encode()).hexdigest()[:8]

        name = factory._generate_graph_name(org_id, "_acme_")
        assert name == f"org_acme_{expected_suffix}"

    def test_different_orgs_same_slug_get_different_names(self):
        """Test REPO-500: Different orgs with colliding slugs get unique names."""
        factory = GraphClientFactory()
        org_id_1 = uuid4()
        org_id_2 = uuid4()

        # Both slugs sanitize to "acme_corp" but should get different names
        name1 = factory._generate_graph_name(org_id_1, "acme-corp")
        name2 = factory._generate_graph_name(org_id_2, "acme_corp")

        assert name1 != name2
        assert name1.startswith("org_acme_corp_")
        assert name2.startswith("org_acme_corp_")


class TestClientCaching:
    """Tests for client caching behavior."""

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_client_caching(self, mock_falkordb_class):
        """Test that clients are cached per org."""
        mock_client = Mock()
        mock_falkordb_class.return_value = mock_client

        factory = GraphClientFactory()
        org_id = uuid4()

        client1 = factory.get_client(org_id, "test-org")
        client2 = factory.get_client(org_id, "test-org")

        assert client1 is client2
        assert mock_falkordb_class.call_count == 1

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_different_orgs_get_different_clients(self, mock_falkordb_class):
        """Test that different orgs get isolated clients."""
        mock_falkordb_class.side_effect = [Mock(), Mock()]

        factory = GraphClientFactory()
        org1 = uuid4()
        org2 = uuid4()

        client1 = factory.get_client(org1, "org-one")
        client2 = factory.get_client(org2, "org-two")

        assert client1 is not client2
        assert mock_falkordb_class.call_count == 2

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_close_client_removes_from_cache(self, mock_falkordb_class):
        """Test closing a client removes it from cache."""
        mock_client = Mock()
        mock_falkordb_class.return_value = mock_client

        factory = GraphClientFactory()
        org_id = uuid4()

        factory.get_client(org_id, "test-org")
        assert org_id in factory._clients

        factory.close_client(org_id)
        assert org_id not in factory._clients
        mock_client.close.assert_called_once()

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_close_all_clears_cache(self, mock_falkordb_class):
        """Test close_all clears all cached clients."""
        mock_falkordb_class.side_effect = [Mock(), Mock()]

        factory = GraphClientFactory()
        org1 = uuid4()
        org2 = uuid4()

        factory.get_client(org1, "org-one")
        factory.get_client(org2, "org-two")

        assert len(factory._clients) == 2

        factory.close_all()
        assert len(factory._clients) == 0


class TestOrgIdIsolation:
    """Tests for org_id property on created clients."""

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_falkordb_client_has_org_id(self, mock_falkordb_class):
        """Test FalkorDB client has org_id set."""
        mock_client = Mock()
        mock_falkordb_class.return_value = mock_client

        factory = GraphClientFactory()
        org_id = uuid4()

        client = factory.get_client(org_id, "test-org")
        assert client._org_id == org_id


class TestFalkorDBClientCreation:
    """Tests for FalkorDB client creation."""

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_falkordb_client_created_with_correct_graph_name(self, mock_falkordb_class):
        """Test FalkorDB client is created with correct graph name including org_id suffix."""
        import hashlib
        mock_client = Mock()
        mock_falkordb_class.return_value = mock_client

        factory = GraphClientFactory()
        org_id = uuid4()
        expected_suffix = hashlib.md5(str(org_id).encode()).hexdigest()[:8]

        factory.get_client(org_id, "test-org")

        mock_falkordb_class.assert_called_once()
        call_kwargs = mock_falkordb_class.call_args[1]
        assert call_kwargs["graph_name"] == f"org_test_org_{expected_suffix}"


class TestEnvironmentVariables:
    """Tests for environment variable configuration."""

    @patch.dict("os.environ", {
        "REPOTOIRE_FALKORDB_HOST": "custom-host",
        "REPOTOIRE_FALKORDB_PORT": "7777",
    }, clear=True)
    def test_repotoire_env_vars_for_falkordb(self):
        """Test factory reads FalkorDB config from REPOTOIRE_* env vars."""
        factory = GraphClientFactory()

        assert factory.falkordb_host == "custom-host"
        assert factory.falkordb_port == 7777


class TestSingletonFactory:
    """Tests for the global singleton factory."""

    def teardown_method(self):
        """Clean up singleton after each test."""
        reset_factory()

    @patch("repotoire.graph.tenant_factory.GraphClientFactory")
    def test_get_factory_creates_singleton(self, mock_factory_class):
        """Test get_factory creates singleton on first call."""
        mock_factory = Mock()
        mock_factory_class.return_value = mock_factory

        factory1 = get_factory()
        factory2 = get_factory()

        assert factory1 is factory2
        mock_factory_class.assert_called_once()

    @patch("repotoire.graph.tenant_factory.GraphClientFactory")
    def test_reset_factory_clears_singleton(self, mock_factory_class):
        """Test reset_factory clears the singleton."""
        mock_factory = Mock()
        mock_factory_class.return_value = mock_factory

        get_factory()
        reset_factory()

        mock_factory.close_all.assert_called_once()

    @patch("repotoire.graph.tenant_factory.get_factory")
    def test_get_client_for_org_convenience(self, mock_get_factory):
        """Test get_client_for_org convenience function."""
        mock_factory = Mock()
        mock_client = Mock()
        mock_factory.get_client.return_value = mock_client
        mock_get_factory.return_value = mock_factory

        org_id = uuid4()
        client = get_client_for_org(org_id, "test-org")

        assert client is mock_client
        mock_factory.get_client.assert_called_once_with(org_id, "test-org")


class TestContextManager:
    """Tests for context manager support."""

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_context_manager(self, mock_falkordb_class):
        """Test factory can be used as context manager."""
        mock_client = Mock()
        mock_falkordb_class.return_value = mock_client

        with GraphClientFactory() as factory:
            org_id = uuid4()
            factory.get_client(org_id, "test-org")
            assert len(factory._clients) == 1

        # After exiting, all clients should be closed
        mock_client.close.assert_called()


class TestProvisioning:
    """Tests for tenant provisioning/deprovisioning."""

    @pytest.mark.asyncio
    async def test_provision_falkordb_is_noop(self):
        """Test FalkorDB provisioning is a no-op (graphs auto-create)."""
        import hashlib
        factory = GraphClientFactory()
        org_id = uuid4()
        expected_suffix = hashlib.md5(str(org_id).encode()).hexdigest()[:8]

        # Should not raise
        graph_name = await factory.provision_tenant(org_id, "test-org")
        assert graph_name == f"org_test_org_{expected_suffix}"

    @pytest.mark.asyncio
    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    async def test_deprovision_falkordb(self, mock_falkordb_class):
        """Test FalkorDB deprovisioning deletes graph."""
        mock_client = Mock()
        mock_graph = Mock()
        mock_client.graph = mock_graph
        mock_falkordb_class.return_value = mock_client

        factory = GraphClientFactory()
        org_id = uuid4()

        await factory.deprovision_tenant(org_id, "test-org")

        mock_graph.delete.assert_called_once()
        mock_client.close.assert_called_once()


class TestGetCachedOrgIds:
    """Tests for getting list of cached org IDs."""

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_get_cached_org_ids(self, mock_falkordb_class):
        """Test get_cached_org_ids returns cached orgs."""
        mock_falkordb_class.side_effect = [Mock(), Mock()]

        factory = GraphClientFactory()
        org1 = uuid4()
        org2 = uuid4()

        factory.get_client(org1, "org-one")
        factory.get_client(org2, "org-two")

        cached = factory.get_cached_org_ids()

        assert len(cached) == 2
        assert org1 in cached
        assert org2 in cached

    def test_get_cached_org_ids_empty(self):
        """Test get_cached_org_ids when no clients cached."""
        factory = GraphClientFactory()
        cached = factory.get_cached_org_ids()
        assert cached == []


class TestFlyIoDefaults:
    """Tests for Fly.io environment detection and defaults."""

    @patch.dict("os.environ", {"FLY_APP_NAME": "repotoire-worker"}, clear=False)
    def test_fly_environment_detection(self):
        """Test that Fly.io environment is detected."""
        from repotoire.graph.tenant_factory import _is_fly_environment

        assert _is_fly_environment() is True

    @patch.dict("os.environ", {}, clear=True)
    def test_non_fly_environment_detection(self):
        """Test that non-Fly.io environment is detected."""
        from repotoire.graph.tenant_factory import _is_fly_environment

        assert _is_fly_environment() is False

    @patch.dict("os.environ", {"FLY_APP_NAME": "repotoire-worker"}, clear=True)
    def test_fly_falkordb_host_default(self):
        """Test FalkorDB host defaults to internal DNS on Fly.io."""
        factory = GraphClientFactory()
        assert factory.falkordb_host == "repotoire-falkor.internal"

    @patch.dict("os.environ", {}, clear=True)
    def test_local_falkordb_host_default(self):
        """Test FalkorDB host defaults to localhost when not on Fly.io."""
        factory = GraphClientFactory()
        assert factory.falkordb_host == "localhost"

    @patch.dict("os.environ", {
        "FALKORDB_HOST": "custom-host.local",
        "FLY_APP_NAME": "repotoire-worker",
    }, clear=True)
    def test_explicit_host_overrides_fly_default(self):
        """Test explicit FALKORDB_HOST overrides Fly.io default."""
        factory = GraphClientFactory()
        assert factory.falkordb_host == "custom-host.local"


class TestFalkorDBEnvVars:
    """Tests for FALKORDB_* environment variable support."""

    @patch.dict("os.environ", {
        "FALKORDB_HOST": "falkor.example.com",
        "FALKORDB_PORT": "16379",
        "FALKORDB_PASSWORD": "secret123",
    }, clear=True)
    def test_falkordb_env_vars(self):
        """Test factory reads FALKORDB_* env vars."""
        factory = GraphClientFactory()

        assert factory.falkordb_host == "falkor.example.com"
        assert factory.falkordb_port == 16379
        assert factory.falkordb_password == "secret123"

    @patch.dict("os.environ", {
        "FALKORDB_HOST": "from-falkordb",
        "REPOTOIRE_FALKORDB_HOST": "from-repotoire",
    }, clear=True)
    def test_falkordb_env_takes_precedence(self):
        """Test FALKORDB_* takes precedence over REPOTOIRE_FALKORDB_*."""
        factory = GraphClientFactory()
        assert factory.falkordb_host == "from-falkordb"


class TestTenantContextValidation:
    """Tests for tenant context validation."""

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_validate_tenant_context_success(self, mock_falkordb_class):
        """Test successful tenant context validation."""
        mock_client = Mock()
        mock_falkordb_class.return_value = mock_client

        factory = GraphClientFactory()
        org_id = uuid4()

        client = factory.get_client(org_id, "test-org")

        # Validation should pass
        result = factory.validate_tenant_context(client, org_id)
        assert result is True

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_validate_tenant_context_mismatch(self, mock_falkordb_class):
        """Test tenant context validation fails on mismatch."""
        mock_client = Mock()
        mock_falkordb_class.return_value = mock_client

        factory = GraphClientFactory()
        org1 = uuid4()
        org2 = uuid4()

        # Create client for org1
        client = factory.get_client(org1, "org-one")

        # Validation should fail when checking against org2
        with pytest.raises(ValueError, match="Tenant context mismatch"):
            factory.validate_tenant_context(client, org2)

    def test_validate_tenant_context_non_multitenant(self):
        """Test validation fails for non-multi-tenant clients."""
        factory = GraphClientFactory()

        # Create a mock client without _org_id
        mock_client = Mock(spec=[])

        with pytest.raises(ValueError, match="not multi-tenant"):
            factory.validate_tenant_context(mock_client, uuid4())


class TestSecurityLogging:
    """Tests for security audit logging."""

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_tenant_access_logged(self, mock_falkordb_class, caplog):
        """Test that tenant access is logged for security auditing."""
        import logging

        mock_client = Mock()
        mock_falkordb_class.return_value = mock_client

        with caplog.at_level(logging.INFO):
            factory = GraphClientFactory()
            org_id = uuid4()
            factory.get_client(org_id, "test-org")

        # Check that tenant access was logged
        assert any("Tenant graph access" in record.message for record in caplog.records)

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_context_mismatch_logged_as_warning(self, mock_falkordb_class, caplog):
        """Test that context mismatch is logged as a warning."""
        import logging

        mock_client = Mock()
        mock_falkordb_class.return_value = mock_client

        factory = GraphClientFactory()
        org1 = uuid4()
        org2 = uuid4()

        client = factory.get_client(org1, "org-one")

        with caplog.at_level(logging.WARNING):
            try:
                factory.validate_tenant_context(client, org2)
            except ValueError:
                pass

        # Check that mismatch was logged as warning
        assert any(
            "Tenant context mismatch detected" in record.message
            for record in caplog.records
        )
