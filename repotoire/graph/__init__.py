"""Graph database client and utilities."""

from repotoire.graph.base import DatabaseClient
from repotoire.graph.falkordb_client import FalkorDBClient
from repotoire.graph.factory import (
    create_client,
    create_cloud_client,
    create_falkordb_client,
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

# Backward compatibility alias - all code using Neo4jClient will use FalkorDBClient
Neo4jClient = FalkorDBClient

__all__ = [
    # Base classes
    "DatabaseClient",
    # Graph clients
    "FalkorDBClient",
    "Neo4jClient",  # Alias for backward compatibility
    # Factory functions
    "create_client",
    "create_cloud_client",
    "create_falkordb_client",
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
