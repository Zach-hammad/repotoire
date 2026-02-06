"""Graph database client and utilities.

Lazy-loading module to avoid importing heavy dependencies at import time.
All public APIs are loaded on first access.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repotoire.graph.base import DatabaseClient
    from repotoire.graph.factory import (
        CloudAuthenticationError,
        CloudAuthInfo,
        CloudConnectionError,
        ConfigurationError,
    )
    from repotoire.graph.falkordb_client import FalkorDBClient
    from repotoire.graph.schema import GraphSchema
    from repotoire.graph.tenant_factory import GraphClientFactory

# Backward compatibility alias
Neo4jClient = None  # Set on first access


def __getattr__(name: str):
    """Lazy imports for graph module."""
    global Neo4jClient

    # Base classes
    if name == "DatabaseClient":
        from repotoire.graph.base import DatabaseClient
        return DatabaseClient

    # Graph clients
    if name == "FalkorDBClient":
        from repotoire.graph.falkordb_client import FalkorDBClient
        return FalkorDBClient
    if name == "Neo4jClient":
        from repotoire.graph.falkordb_client import FalkorDBClient
        Neo4jClient = FalkorDBClient
        return FalkorDBClient

    # Factory functions
    if name == "create_client":
        from repotoire.graph.factory import create_client
        return create_client
    if name == "create_cloud_client":
        from repotoire.graph.factory import create_cloud_client
        return create_cloud_client
    if name == "create_falkordb_client":
        from repotoire.graph.factory import create_falkordb_client
        return create_falkordb_client
    if name == "is_cloud_mode":
        from repotoire.graph.factory import is_cloud_mode
        return is_cloud_mode
    if name == "get_cloud_auth_info":
        from repotoire.graph.factory import get_cloud_auth_info
        return get_cloud_auth_info

    # Tenant factory
    if name == "GraphClientFactory":
        from repotoire.graph.tenant_factory import GraphClientFactory
        return GraphClientFactory
    if name == "get_factory":
        from repotoire.graph.tenant_factory import get_factory
        return get_factory
    if name == "get_client_for_org":
        from repotoire.graph.tenant_factory import get_client_for_org
        return get_client_for_org
    if name == "reset_factory":
        from repotoire.graph.tenant_factory import reset_factory
        return reset_factory

    # Exceptions
    if name == "CloudAuthenticationError":
        from repotoire.graph.factory import CloudAuthenticationError
        return CloudAuthenticationError
    if name == "CloudConnectionError":
        from repotoire.graph.factory import CloudConnectionError
        return CloudConnectionError
    if name == "ConfigurationError":
        from repotoire.graph.factory import ConfigurationError
        return ConfigurationError

    # Data classes
    if name == "CloudAuthInfo":
        from repotoire.graph.factory import CloudAuthInfo
        return CloudAuthInfo

    # Schema
    if name == "GraphSchema":
        from repotoire.graph.schema import GraphSchema
        return GraphSchema

    raise AttributeError(f"module 'repotoire.graph' has no attribute {name!r}")


__all__ = [
    # Base classes
    "DatabaseClient",
    # Graph clients
    "FalkorDBClient",
    "Neo4jClient",
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
