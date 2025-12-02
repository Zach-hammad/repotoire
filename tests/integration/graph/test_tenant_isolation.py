"""Integration tests for multi-tenant graph isolation.

These tests require a running FalkorDB instance to verify that tenant data
is properly isolated across different organization graphs.

Run with: pytest tests/integration/graph/test_tenant_isolation.py -v

Environment:
    REPOTOIRE_FALKORDB_HOST: FalkorDB host (default: localhost)
    REPOTOIRE_FALKORDB_PORT: FalkorDB port (default: 6379)
"""

import os
import pytest
from uuid import uuid4

# Skip all tests if FalkorDB is not available
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def falkordb_available():
    """Check if FalkorDB is available for testing."""
    try:
        import falkordb
        host = os.environ.get("REPOTOIRE_FALKORDB_HOST", "localhost")
        port = int(os.environ.get("REPOTOIRE_FALKORDB_PORT", "6379"))
        db = falkordb.FalkorDB(host=host, port=port)
        # Try a simple operation
        db.list_graphs()
        return True
    except Exception:
        return False


@pytest.fixture
def factory():
    """Create a GraphClientFactory for testing."""
    from repotoire.graph.tenant_factory import GraphClientFactory

    factory = GraphClientFactory(backend="falkordb")
    yield factory
    factory.close_all()


@pytest.fixture
def test_orgs():
    """Create test organization IDs."""
    return {
        "org1": {"id": uuid4(), "slug": f"test-org-1-{uuid4().hex[:8]}"},
        "org2": {"id": uuid4(), "slug": f"test-org-2-{uuid4().hex[:8]}"},
    }


@pytest.fixture
def cleanup_graphs(factory, test_orgs):
    """Cleanup test graphs after tests."""
    yield
    # Cleanup: deprovision test graphs
    import asyncio
    for org in test_orgs.values():
        try:
            asyncio.run(factory.deprovision_tenant(org["id"], org["slug"]))
        except Exception:
            pass


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Skip in CI - requires FalkorDB"
)
class TestTenantDataIsolation:
    """Tests for verifying tenant data isolation."""

    def test_tenant_graphs_are_isolated(
        self, falkordb_available, factory, test_orgs, cleanup_graphs
    ):
        """Test that tenant data is completely isolated between graphs."""
        if not falkordb_available:
            pytest.skip("FalkorDB not available")

        org1 = test_orgs["org1"]
        org2 = test_orgs["org2"]

        # Get clients for both orgs
        client1 = factory.get_client(org1["id"], org1["slug"])
        client2 = factory.get_client(org2["id"], org2["slug"])

        # Create data in org1
        client1.execute_query(
            "CREATE (n:TestNode {name: $name})",
            {"name": "org1-data"}
        )

        # Verify org1 can see its data
        result1 = client1.execute_query("MATCH (n:TestNode) RETURN n.name as name")
        assert len(result1) == 1
        assert result1[0]["name"] == "org1-data"

        # Verify org2 cannot see org1's data
        result2 = client2.execute_query("MATCH (n:TestNode) RETURN n.name as name")
        assert len(result2) == 0

        # Create data in org2
        client2.execute_query(
            "CREATE (n:TestNode {name: $name})",
            {"name": "org2-data"}
        )

        # Verify org2 can see its data
        result2 = client2.execute_query("MATCH (n:TestNode) RETURN n.name as name")
        assert len(result2) == 1
        assert result2[0]["name"] == "org2-data"

        # Verify org1 still only sees its data
        result1 = client1.execute_query("MATCH (n:TestNode) RETURN n.name as name")
        assert len(result1) == 1
        assert result1[0]["name"] == "org1-data"

    def test_clear_graph_only_affects_tenant(
        self, falkordb_available, factory, test_orgs, cleanup_graphs
    ):
        """Test that clearing one tenant's graph doesn't affect others."""
        if not falkordb_available:
            pytest.skip("FalkorDB not available")

        org1 = test_orgs["org1"]
        org2 = test_orgs["org2"]

        client1 = factory.get_client(org1["id"], org1["slug"])
        client2 = factory.get_client(org2["id"], org2["slug"])

        # Create data in both orgs
        client1.execute_query("CREATE (n:TestNode {name: 'org1-data'})")
        client2.execute_query("CREATE (n:TestNode {name: 'org2-data'})")

        # Clear org1's graph
        client1.clear_graph()

        # Verify org1's data is gone
        result1 = client1.execute_query("MATCH (n:TestNode) RETURN n")
        assert len(result1) == 0

        # Verify org2's data is still there
        result2 = client2.execute_query("MATCH (n:TestNode) RETURN n.name as name")
        assert len(result2) == 1
        assert result2[0]["name"] == "org2-data"

    def test_get_stats_is_tenant_specific(
        self, falkordb_available, factory, test_orgs, cleanup_graphs
    ):
        """Test that stats are specific to each tenant."""
        if not falkordb_available:
            pytest.skip("FalkorDB not available")

        org1 = test_orgs["org1"]
        org2 = test_orgs["org2"]

        client1 = factory.get_client(org1["id"], org1["slug"])
        client2 = factory.get_client(org2["id"], org2["slug"])

        # Clear to start fresh
        client1.clear_graph()
        client2.clear_graph()

        # Create different amounts of data
        client1.execute_query("CREATE (n:TestNode {name: 'node1'})")
        client1.execute_query("CREATE (n:TestNode {name: 'node2'})")
        client1.execute_query("CREATE (n:TestNode {name: 'node3'})")

        client2.execute_query("CREATE (n:TestNode {name: 'node1'})")

        # Get stats
        stats1 = client1.get_stats()
        stats2 = client2.get_stats()

        # Verify stats are different
        assert stats1.get("nodes", 0) == 3
        assert stats2.get("nodes", 0) == 1


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Skip in CI - requires FalkorDB"
)
class TestTenantProvisioning:
    """Tests for tenant provisioning and deprovisioning."""

    def test_provision_creates_accessible_graph(
        self, falkordb_available, factory, test_orgs, cleanup_graphs
    ):
        """Test that provisioning makes graph accessible."""
        if not falkordb_available:
            pytest.skip("FalkorDB not available")

        import asyncio

        org = test_orgs["org1"]

        # Provision the tenant
        graph_name = asyncio.run(factory.provision_tenant(org["id"], org["slug"]))
        assert graph_name.startswith("org_")

        # Get client and create data
        client = factory.get_client(org["id"], org["slug"])
        client.execute_query("CREATE (n:TestNode {name: 'test'})")

        # Verify data exists
        result = client.execute_query("MATCH (n:TestNode) RETURN n.name as name")
        assert len(result) == 1

    def test_deprovision_removes_graph(
        self, falkordb_available, factory, test_orgs
    ):
        """Test that deprovisioning removes all graph data."""
        if not falkordb_available:
            pytest.skip("FalkorDB not available")

        import asyncio

        org = test_orgs["org1"]

        # Provision and create data
        asyncio.run(factory.provision_tenant(org["id"], org["slug"]))
        client = factory.get_client(org["id"], org["slug"])
        client.execute_query("CREATE (n:TestNode {name: 'test'})")

        # Deprovision
        asyncio.run(factory.deprovision_tenant(org["id"], org["slug"]))

        # Get a new client (old one was closed)
        factory.close_client(org["id"])

        # Try to get data - should be empty (graph was deleted)
        new_client = factory.get_client(org["id"], org["slug"])
        result = new_client.execute_query("MATCH (n:TestNode) RETURN n")
        assert len(result) == 0

        # Cleanup
        asyncio.run(factory.deprovision_tenant(org["id"], org["slug"]))


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Skip in CI - requires FalkorDB"
)
class TestMultiTenantClientProperties:
    """Tests for multi-tenant client properties."""

    def test_client_is_multi_tenant(
        self, falkordb_available, factory, test_orgs, cleanup_graphs
    ):
        """Test that tenant clients have is_multi_tenant=True."""
        if not falkordb_available:
            pytest.skip("FalkorDB not available")

        org = test_orgs["org1"]
        client = factory.get_client(org["id"], org["slug"])

        assert client.is_multi_tenant is True
        assert client.org_id == org["id"]

    def test_client_graph_name_matches(
        self, falkordb_available, factory, test_orgs, cleanup_graphs
    ):
        """Test that client graph name matches expected name."""
        if not falkordb_available:
            pytest.skip("FalkorDB not available")

        org = test_orgs["org1"]
        client = factory.get_client(org["id"], org["slug"])

        expected_name = factory._generate_graph_name(org["id"], org["slug"])

        # FalkorDBClient exposes graph_name
        if hasattr(client, "graph_name"):
            assert client.graph_name == expected_name


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Skip in CI - requires FalkorDB"
)
class TestClientCaching:
    """Integration tests for client caching."""

    def test_cached_client_maintains_connection(
        self, falkordb_available, factory, test_orgs, cleanup_graphs
    ):
        """Test that cached clients maintain their connections."""
        if not falkordb_available:
            pytest.skip("FalkorDB not available")

        org = test_orgs["org1"]

        # Get client multiple times
        client1 = factory.get_client(org["id"], org["slug"])
        client2 = factory.get_client(org["id"], org["slug"])

        # Same instance
        assert client1 is client2

        # Create data
        client1.execute_query("CREATE (n:TestNode {name: 'test'})")

        # Query with second reference
        result = client2.execute_query("MATCH (n:TestNode) RETURN n.name as name")
        assert len(result) == 1

    def test_close_client_allows_reconnection(
        self, falkordb_available, factory, test_orgs, cleanup_graphs
    ):
        """Test that closing a client allows getting a new one."""
        if not falkordb_available:
            pytest.skip("FalkorDB not available")

        org = test_orgs["org1"]

        # Get initial client
        client1 = factory.get_client(org["id"], org["slug"])
        client1.execute_query("CREATE (n:TestNode {name: 'test'})")

        # Close it
        factory.close_client(org["id"])

        # Get new client
        client2 = factory.get_client(org["id"], org["slug"])

        # Different instance
        assert client1 is not client2

        # Data still accessible
        result = client2.execute_query("MATCH (n:TestNode) RETURN n.name as name")
        assert len(result) == 1
