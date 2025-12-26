"""Graph database client and utilities."""

from repotoire.graph.base import DatabaseClient
from repotoire.graph.client import Neo4jClient
from repotoire.graph.falkordb_client import FalkorDBClient
from repotoire.graph.factory import (
    create_client,
    create_cloud_client,
    is_cloud_mode,
    get_cloud_auth_info,
    CloudAuthenticationError,
    CloudConnectionError,
    ConfigurationError,
    CloudAuthInfo,
)
from repotoire.graph.schema import GraphSchema
from repotoire.graph.tenant_factory import (
    GraphClientFactory,
    get_factory,
    get_client_for_org,
    reset_factory,
)
from repotoire.graph.neo4j_multitenant import (
    Neo4jClientMultiTenant,
    Neo4jClientPartitioned,
)

__all__ = [
    # Base classes
    "DatabaseClient",
    # Single-tenant clients
    "Neo4jClient",
    "FalkorDBClient",
    # Multi-tenant clients
    "Neo4jClientMultiTenant",
    "Neo4jClientPartitioned",
    # Factory functions
    "create_client",
    "create_cloud_client",
    "is_cloud_mode",
    "get_cloud_auth_info",
    "GraphClientFactory",
    "get_factory",
    "get_client_for_org",
    "reset_factory",
    # Exceptions
    "CloudAuthenticationError",
    "CloudConnectionError",
    "ConfigurationError",
    # Data classes
    "CloudAuthInfo",
    # Schema
    "GraphSchema",
]
