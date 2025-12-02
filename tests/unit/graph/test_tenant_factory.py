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
    """Tests for graph/database name generation from org ID and slug."""

    def test_generate_graph_name_from_slug(self):
        """Test graph name generation from org slug."""
        factory = GraphClientFactory(backend="falkordb")
        org_id = uuid4()

        name = factory._generate_graph_name(org_id, "acme-corp")
        assert name == "org_acme_corp"

    def test_generate_graph_name_from_slug_with_dots(self):
        """Test graph name generation handles dots in slug."""
        factory = GraphClientFactory(backend="falkordb")
        org_id = uuid4()

        name = factory._generate_graph_name(org_id, "acme.corp.inc")
        assert name == "org_acme_corp_inc"

    def test_generate_graph_name_from_slug_with_special_chars(self):
        """Test graph name sanitization of special characters."""
        factory = GraphClientFactory(backend="falkordb")
        org_id = uuid4()

        name = factory._generate_graph_name(org_id, "acme--corp__test")
        assert name == "org_acme_corp_test"

    def test_generate_graph_name_from_uuid(self):
        """Test graph name fallback to UUID when no slug."""
        factory = GraphClientFactory(backend="falkordb")
        org_id = UUID("550e8400-e29b-41d4-a716-446655440000")

        name = factory._generate_graph_name(org_id, None)
        assert name == "org_550e8400"

    def test_generate_graph_name_lowercase(self):
        """Test graph names are lowercased."""
        factory = GraphClientFactory(backend="falkordb")
        org_id = uuid4()

        name = factory._generate_graph_name(org_id, "AcMeCorp")
        assert name == "org_acmecorp"

    def test_generate_graph_name_strips_underscores(self):
        """Test leading/trailing underscores are stripped."""
        factory = GraphClientFactory(backend="falkordb")
        org_id = uuid4()

        name = factory._generate_graph_name(org_id, "_acme_")
        assert name == "org_acme"


class TestClientCaching:
    """Tests for client caching behavior."""

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_client_caching(self, mock_falkordb_class):
        """Test that clients are cached per org."""
        mock_client = Mock()
        mock_falkordb_class.return_value = mock_client

        factory = GraphClientFactory(backend="falkordb")
        org_id = uuid4()

        client1 = factory.get_client(org_id, "test-org")
        client2 = factory.get_client(org_id, "test-org")

        assert client1 is client2
        assert mock_falkordb_class.call_count == 1

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_different_orgs_get_different_clients(self, mock_falkordb_class):
        """Test that different orgs get isolated clients."""
        mock_falkordb_class.side_effect = [Mock(), Mock()]

        factory = GraphClientFactory(backend="falkordb")
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

        factory = GraphClientFactory(backend="falkordb")
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

        factory = GraphClientFactory(backend="falkordb")
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

        factory = GraphClientFactory(backend="falkordb")
        org_id = uuid4()

        client = factory.get_client(org_id, "test-org")
        assert client._org_id == org_id

    @patch("repotoire.graph.neo4j_multitenant.Neo4jClientMultiTenant")
    def test_neo4j_client_has_org_id(self, mock_neo4j_class):
        """Test Neo4j client has org_id set."""
        mock_client = Mock()
        mock_neo4j_class.return_value = mock_client

        factory = GraphClientFactory(backend="neo4j", strategy="database_per_tenant")
        org_id = uuid4()

        client = factory.get_client(org_id, "test-org")

        # Verify org_id was passed to constructor
        call_kwargs = mock_neo4j_class.call_args[1]
        assert call_kwargs["org_id"] == org_id


class TestBackendSelection:
    """Tests for backend selection logic."""

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_falkordb_backend(self, mock_falkordb_class):
        """Test FalkorDB backend creates FalkorDBClient."""
        mock_client = Mock()
        mock_falkordb_class.return_value = mock_client

        factory = GraphClientFactory(backend="falkordb")
        org_id = uuid4()

        factory.get_client(org_id, "test-org")

        mock_falkordb_class.assert_called_once()
        call_kwargs = mock_falkordb_class.call_args[1]
        assert call_kwargs["graph_name"] == "org_test_org"

    @patch("repotoire.graph.neo4j_multitenant.Neo4jClientMultiTenant")
    def test_neo4j_database_per_tenant_strategy(self, mock_neo4j_class):
        """Test Neo4j with database_per_tenant strategy."""
        mock_client = Mock()
        mock_neo4j_class.return_value = mock_client

        factory = GraphClientFactory(backend="neo4j", strategy="database_per_tenant")
        org_id = uuid4()

        factory.get_client(org_id, "test-org")

        mock_neo4j_class.assert_called_once()
        call_kwargs = mock_neo4j_class.call_args[1]
        assert call_kwargs["database"] == "org_test_org"

    @patch("repotoire.graph.neo4j_multitenant.Neo4jClientPartitioned")
    def test_neo4j_partition_strategy(self, mock_neo4j_class):
        """Test Neo4j with partition strategy."""
        mock_client = Mock()
        mock_neo4j_class.return_value = mock_client

        factory = GraphClientFactory(backend="neo4j", strategy="partition")
        org_id = uuid4()

        factory.get_client(org_id, "test-org")

        mock_neo4j_class.assert_called_once()


class TestEnvironmentVariables:
    """Tests for environment variable configuration."""

    @patch.dict("os.environ", {
        "REPOTOIRE_DB_TYPE": "falkordb",
        "REPOTOIRE_FALKORDB_HOST": "custom-host",
        "REPOTOIRE_FALKORDB_PORT": "7777",
    })
    def test_env_vars_for_falkordb(self):
        """Test factory reads FalkorDB config from env vars."""
        factory = GraphClientFactory()

        assert factory.backend == "falkordb"
        assert factory.falkordb_host == "custom-host"
        assert factory.falkordb_port == 7777

    @patch.dict("os.environ", {
        "REPOTOIRE_DB_TYPE": "neo4j",
        "REPOTOIRE_NEO4J_URI": "bolt://custom:7687",
        "REPOTOIRE_NEO4J_USERNAME": "custom-user",
        "REPOTOIRE_NEO4J_PASSWORD": "custom-pass",
    })
    def test_env_vars_for_neo4j(self):
        """Test factory reads Neo4j config from env vars."""
        factory = GraphClientFactory()

        assert factory.backend == "neo4j"
        assert factory.neo4j_uri == "bolt://custom:7687"
        assert factory.neo4j_username == "custom-user"
        assert factory.neo4j_password == "custom-pass"


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

        with GraphClientFactory(backend="falkordb") as factory:
            org_id = uuid4()
            factory.get_client(org_id, "test-org")
            assert len(factory._clients) == 1

        # After exiting, all clients should be closed
        mock_client.close.assert_called()


class TestProvisioning:
    """Tests for tenant provisioning/deprovisioning."""

    @pytest.mark.asyncio
    @patch("repotoire.graph.client.Neo4jClient")
    async def test_provision_neo4j_database(self, mock_neo4j_class):
        """Test provisioning creates Neo4j database."""
        mock_admin = Mock()
        mock_neo4j_class.return_value = mock_admin

        factory = GraphClientFactory(backend="neo4j", strategy="database_per_tenant")
        org_id = uuid4()

        graph_name = await factory.provision_tenant(org_id, "test-org")

        assert graph_name == "org_test_org"
        mock_admin.execute_query.assert_called_once()
        call_args = mock_admin.execute_query.call_args[0][0]
        assert "CREATE DATABASE" in call_args
        assert "org_test_org" in call_args
        mock_admin.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_provision_falkordb_is_noop(self):
        """Test FalkorDB provisioning is a no-op (graphs auto-create)."""
        factory = GraphClientFactory(backend="falkordb")
        org_id = uuid4()

        # Should not raise
        graph_name = await factory.provision_tenant(org_id, "test-org")
        assert graph_name == "org_test_org"

    @pytest.mark.asyncio
    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    async def test_deprovision_falkordb(self, mock_falkordb_class):
        """Test FalkorDB deprovisioning deletes graph."""
        mock_client = Mock()
        mock_graph = Mock()
        mock_client.graph = mock_graph
        mock_falkordb_class.return_value = mock_client

        factory = GraphClientFactory(backend="falkordb")
        org_id = uuid4()

        await factory.deprovision_tenant(org_id, "test-org")

        mock_graph.delete.assert_called_once()
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    @patch("repotoire.graph.client.Neo4jClient")
    async def test_deprovision_neo4j(self, mock_neo4j_class):
        """Test Neo4j deprovisioning drops database."""
        mock_admin = Mock()
        mock_neo4j_class.return_value = mock_admin

        factory = GraphClientFactory(backend="neo4j", strategy="database_per_tenant")
        org_id = uuid4()

        await factory.deprovision_tenant(org_id, "test-org")

        mock_admin.execute_query.assert_called_once()
        call_args = mock_admin.execute_query.call_args[0][0]
        assert "DROP DATABASE" in call_args
        assert "org_test_org" in call_args
        mock_admin.close.assert_called_once()


class TestGetCachedOrgIds:
    """Tests for getting list of cached org IDs."""

    @patch("repotoire.graph.falkordb_client.FalkorDBClient")
    def test_get_cached_org_ids(self, mock_falkordb_class):
        """Test get_cached_org_ids returns cached orgs."""
        mock_falkordb_class.side_effect = [Mock(), Mock()]

        factory = GraphClientFactory(backend="falkordb")
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
        factory = GraphClientFactory(backend="falkordb")
        cached = factory.get_cached_org_ids()
        assert cached == []
