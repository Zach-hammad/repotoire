"""Multi-tenant graph client factory.

This module provides tenant-isolated graph database clients for SaaS deployments.
Each organization gets its own isolated graph/database to ensure complete data
separation.

Supports two strategies:
- database_per_tenant: Each org gets a dedicated database/graph (recommended)
- partition: Single database with org_id filtering on all queries

Examples:
    # Create factory with FalkorDB backend
    factory = GraphClientFactory(backend="falkordb")

    # Get client for specific organization
    client = factory.get_client(org_id=org.id, org_slug=org.slug)

    # Client is now isolated to that org's graph
    client.execute_query("MATCH (n) RETURN n LIMIT 10")

    # Convenience function
    from repotoire.graph.tenant_factory import get_client_for_org
    client = get_client_for_org(org.id, org.slug)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional
from uuid import UUID

from repotoire.graph.base import DatabaseClient

logger = logging.getLogger(__name__)


def _is_fly_environment() -> bool:
    """Check if running on Fly.io."""
    return bool(os.environ.get("FLY_APP_NAME"))


def _get_fly_falkordb_host() -> str:
    """Get FalkorDB internal host for Fly.io.

    Returns the internal DNS name for the FalkorDB service.
    """
    return "repotoire-falkor.internal"


class GraphClientFactory:
    """Factory for creating tenant-isolated graph database clients.

    Supports two multi-tenancy strategies:
    - database_per_tenant: Each org gets a dedicated database/graph (recommended)
    - partition: Single database with org_id filtering on all queries

    The factory caches clients per organization to avoid creating duplicate
    connections. Use close_client() or close_all() to release resources.

    Attributes:
        backend: Database backend ("neo4j" or "falkordb")
        strategy: Multi-tenancy strategy ("database_per_tenant" or "partition")

    Examples:
        >>> factory = GraphClientFactory(backend="falkordb")
        >>> client = factory.get_client(org_id=UUID("..."), org_slug="acme-corp")
        >>> # Client operates on graph "org_acme_corp"
    """

    # Cache of active clients per org
    _clients: Dict[UUID, DatabaseClient]

    def __init__(
        self,
        backend: Optional[str] = None,
        strategy: str = "database_per_tenant",
        neo4j_uri: Optional[str] = None,
        neo4j_username: Optional[str] = None,
        neo4j_password: Optional[str] = None,
        falkordb_host: Optional[str] = None,
        falkordb_port: Optional[int] = None,
        falkordb_password: Optional[str] = None,
    ):
        """Initialize the factory.

        Args:
            backend: Database backend ("neo4j" or "falkordb").
                     Defaults to REPOTOIRE_DB_TYPE env var or "neo4j".
            strategy: Multi-tenancy strategy:
                     - "database_per_tenant": Separate database/graph per org (default)
                     - "partition": Shared database with org_id filtering
            neo4j_uri: Neo4j connection URI.
                       Defaults to REPOTOIRE_NEO4J_URI or bolt://localhost:7687.
            neo4j_username: Neo4j username.
                           Defaults to REPOTOIRE_NEO4J_USERNAME or "neo4j".
            neo4j_password: Neo4j password.
                           Defaults to REPOTOIRE_NEO4J_PASSWORD.
            falkordb_host: FalkorDB host.
                          Defaults to REPOTOIRE_FALKORDB_HOST or "localhost".
            falkordb_port: FalkorDB port.
                          Defaults to REPOTOIRE_FALKORDB_PORT or 6379.
            falkordb_password: FalkorDB password.
                              Defaults to REPOTOIRE_FALKORDB_PASSWORD.
        """
        self._clients = {}
        self.backend = backend or os.environ.get("REPOTOIRE_DB_TYPE", "neo4j").lower()
        self.strategy = strategy

        # Neo4j connection config
        self.neo4j_uri = neo4j_uri or os.environ.get(
            "REPOTOIRE_NEO4J_URI", "bolt://localhost:7687"
        )
        self.neo4j_username = neo4j_username or os.environ.get(
            "REPOTOIRE_NEO4J_USERNAME", "neo4j"
        )
        self.neo4j_password = neo4j_password or os.environ.get(
            "REPOTOIRE_NEO4J_PASSWORD", "password"
        )

        # FalkorDB connection config
        # Support both FALKORDB_* and REPOTOIRE_FALKORDB_* env vars for flexibility
        # On Fly.io, use internal DNS for FalkorDB by default
        default_host = _get_fly_falkordb_host() if _is_fly_environment() else "localhost"
        self.falkordb_host = falkordb_host or os.environ.get(
            "FALKORDB_HOST",
            os.environ.get("REPOTOIRE_FALKORDB_HOST", default_host)
        )
        self.falkordb_port = falkordb_port or int(
            os.environ.get(
                "FALKORDB_PORT",
                os.environ.get("REPOTOIRE_FALKORDB_PORT", "6379")
            )
        )
        self.falkordb_password = falkordb_password or os.environ.get(
            "FALKORDB_PASSWORD",
            os.environ.get("REPOTOIRE_FALKORDB_PASSWORD")
        )

        logger.info(
            f"GraphClientFactory initialized: backend={self.backend}, "
            f"strategy={self.strategy}"
        )

    def get_client(
        self, org_id: UUID, org_slug: Optional[str] = None
    ) -> DatabaseClient:
        """Get a tenant-isolated graph client for an organization.

        Clients are cached per organization. Subsequent calls with the same
        org_id return the cached client.

        Args:
            org_id: Organization UUID for isolation
            org_slug: Organization slug (used for graph/database naming).
                     If not provided, uses first 8 chars of org_id hex.

        Returns:
            DatabaseClient isolated to the organization's graph/database

        Examples:
            >>> client = factory.get_client(
            ...     org_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            ...     org_slug="acme-corp"
            ... )
            >>> # Client operates on graph "org_acme_corp"
        """
        # Check cache first
        if org_id in self._clients:
            return self._clients[org_id]

        # Generate graph/database name from org
        graph_name = self._generate_graph_name(org_id, org_slug)

        if self.backend == "falkordb":
            client = self._create_falkordb_client(org_id, graph_name)
        else:
            client = self._create_neo4j_client(org_id, graph_name)

        # Cache the client
        self._clients[org_id] = client

        # Log tenant access for security auditing
        self._log_tenant_access(org_id, org_slug, graph_name, "client_created")

        logger.info(f"Created tenant client for org {org_id}: {graph_name}")
        return client

    def _log_tenant_access(
        self,
        org_id: UUID,
        org_slug: Optional[str],
        graph_name: str,
        action: str,
    ) -> None:
        """Log tenant access for security auditing.

        Args:
            org_id: Organization UUID
            org_slug: Organization slug
            graph_name: Graph/database name
            action: Action being performed (e.g., "client_created", "query", "provisioned")
        """
        logger.info(
            "Tenant graph access",
            extra={
                "tenant_id": str(org_id),
                "tenant_slug": org_slug,
                "graph_name": graph_name,
                "action": action,
                "backend": self.backend,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    def validate_tenant_context(
        self,
        client: DatabaseClient,
        expected_org_id: UUID,
    ) -> bool:
        """Validate that a client belongs to the expected organization.

        Use this to verify tenant context before executing sensitive operations.
        Raises an error if there's a mismatch, preventing cross-tenant access.

        Args:
            client: DatabaseClient to validate
            expected_org_id: Expected organization UUID

        Returns:
            True if validation passes

        Raises:
            ValueError: If client's org_id doesn't match expected_org_id
        """
        if not hasattr(client, "_org_id") or client._org_id is None:
            raise ValueError(
                "Client is not multi-tenant. Use get_client() to create tenant-isolated clients."
            )

        if client._org_id != expected_org_id:
            # Log security event
            logger.warning(
                "Tenant context mismatch detected",
                extra={
                    "expected_org_id": str(expected_org_id),
                    "client_org_id": str(client._org_id),
                    "action": "context_mismatch",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise ValueError(
                f"Tenant context mismatch: client belongs to org {client._org_id}, "
                f"but expected org {expected_org_id}"
            )

        # Log successful validation
        logger.debug(
            "Tenant context validated",
            extra={
                "org_id": str(expected_org_id),
                "action": "context_validated",
            },
        )
        return True

    def _generate_graph_name(self, org_id: UUID, org_slug: Optional[str]) -> str:
        """Generate a unique graph/database name for an organization.

        Uses slug if available (human-readable), falls back to UUID.
        Sanitizes to be valid graph name (alphanumeric + underscore).

        Args:
            org_id: Organization UUID
            org_slug: Optional organization slug

        Returns:
            Sanitized graph/database name (e.g., "org_acme_corp" or "org_550e8400")
        """
        if org_slug:
            # Sanitize slug for graph name: replace non-alphanumeric with underscore
            safe_name = "".join(
                c if c.isalnum() else "_" for c in org_slug.lower()
            )
            # Remove consecutive underscores and leading/trailing underscores
            while "__" in safe_name:
                safe_name = safe_name.replace("__", "_")
            safe_name = safe_name.strip("_")
            return f"org_{safe_name}"
        else:
            # Use first 8 chars of UUID hex
            return f"org_{org_id.hex[:8]}"

    def _create_falkordb_client(
        self, org_id: UUID, graph_name: str
    ) -> DatabaseClient:
        """Create a FalkorDB client for a tenant.

        Each tenant gets a separate graph within the FalkorDB instance.

        Args:
            org_id: Organization UUID
            graph_name: Graph name for this tenant

        Returns:
            FalkorDBClient configured for the tenant's graph
        """
        from repotoire.graph.falkordb_client import FalkorDBClient

        client = FalkorDBClient(
            host=self.falkordb_host,
            port=self.falkordb_port,
            password=self.falkordb_password,
            graph_name=graph_name,
        )
        client._org_id = org_id

        return client

    def _create_neo4j_client(
        self, org_id: UUID, database_name: str
    ) -> DatabaseClient:
        """Create a Neo4j client for a tenant.

        For Neo4j Enterprise: Uses database-per-tenant.
        For Neo4j Community: Uses partition strategy with org_id filter.

        Args:
            org_id: Organization UUID
            database_name: Database name for this tenant

        Returns:
            Neo4j client configured for the tenant
        """
        if self.strategy == "database_per_tenant":
            # Neo4j Enterprise: Create client pointing to org-specific database
            from repotoire.graph.neo4j_multitenant import Neo4jClientMultiTenant

            client = Neo4jClientMultiTenant(
                uri=self.neo4j_uri,
                username=self.neo4j_username,
                password=self.neo4j_password,
                database=database_name,
                org_id=org_id,
            )
        else:
            # Neo4j Community: Use partition strategy
            from repotoire.graph.neo4j_multitenant import Neo4jClientPartitioned

            client = Neo4jClientPartitioned(
                uri=self.neo4j_uri,
                username=self.neo4j_username,
                password=self.neo4j_password,
                org_id=org_id,
            )

        return client

    def close_client(self, org_id: UUID) -> None:
        """Close and remove a cached client.

        Args:
            org_id: Organization UUID whose client should be closed
        """
        if org_id in self._clients:
            try:
                self._clients[org_id].close()
            except Exception as e:
                logger.warning(f"Error closing client for org {org_id}: {e}")
            del self._clients[org_id]
            logger.debug(f"Closed client for org {org_id}")

    def close_all(self) -> None:
        """Close all cached clients.

        Should be called during application shutdown.
        """
        for org_id in list(self._clients.keys()):
            self.close_client(org_id)
        logger.info("Closed all tenant clients")

    async def provision_tenant(self, org_id: UUID, org_slug: str) -> str:
        """Provision graph storage for a new organization.

        For FalkorDB: Graph is created automatically on first query.
        For Neo4j Enterprise: Creates the database if it doesn't exist.

        Args:
            org_id: Organization UUID
            org_slug: Organization slug for naming

        Returns:
            Graph/database name that was provisioned

        Note:
            This is idempotent - calling multiple times is safe.
        """
        graph_name = self._generate_graph_name(org_id, org_slug)

        if self.backend == "neo4j" and self.strategy == "database_per_tenant":
            # Create Neo4j database
            from repotoire.graph.client import Neo4jClient

            admin_client = Neo4jClient(
                uri=self.neo4j_uri,
                username=self.neo4j_username,
                password=self.neo4j_password,
            )
            try:
                admin_client.execute_query(
                    f"CREATE DATABASE `{graph_name}` IF NOT EXISTS"
                )
                logger.info(f"Created Neo4j database: {graph_name}")
            except Exception as e:
                # Database might already exist or we're on Community Edition
                logger.warning(
                    f"Could not create database {graph_name}: {e}. "
                    "This is expected on Neo4j Community Edition."
                )
            finally:
                admin_client.close()
        else:
            # FalkorDB creates graphs automatically - no provisioning needed
            logger.info(
                f"FalkorDB graph {graph_name} will be created on first query"
            )

        return graph_name

    async def deprovision_tenant(self, org_id: UUID, org_slug: str) -> None:
        """Remove graph storage for a deleted organization.

        WARNING: This permanently deletes all data for the organization!

        Args:
            org_id: Organization UUID
            org_slug: Organization slug for naming
        """
        graph_name = self._generate_graph_name(org_id, org_slug)

        # Close any cached client first
        self.close_client(org_id)

        if self.backend == "falkordb":
            from repotoire.graph.falkordb_client import FalkorDBClient

            temp_client = FalkorDBClient(
                host=self.falkordb_host,
                port=self.falkordb_port,
                password=self.falkordb_password,
                graph_name=graph_name,
            )
            try:
                temp_client.graph.delete()
                logger.info(f"Deleted FalkorDB graph: {graph_name}")
            except Exception as e:
                logger.warning(f"Could not delete graph {graph_name}: {e}")
            finally:
                temp_client.close()

        elif self.backend == "neo4j" and self.strategy == "database_per_tenant":
            from repotoire.graph.client import Neo4jClient

            admin_client = Neo4jClient(
                uri=self.neo4j_uri,
                username=self.neo4j_username,
                password=self.neo4j_password,
            )
            try:
                admin_client.execute_query(
                    f"DROP DATABASE `{graph_name}` IF EXISTS"
                )
                logger.info(f"Dropped Neo4j database: {graph_name}")
            except Exception as e:
                logger.warning(f"Could not drop database {graph_name}: {e}")
            finally:
                admin_client.close()

    def get_cached_org_ids(self) -> list[UUID]:
        """Get list of organization IDs with cached clients.

        Returns:
            List of org UUIDs currently in the cache
        """
        return list(self._clients.keys())

    def __enter__(self) -> "GraphClientFactory":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - closes all clients."""
        self.close_all()


# Singleton factory instance
_factory: Optional[GraphClientFactory] = None


def get_factory(**kwargs) -> GraphClientFactory:
    """Get or create the global factory instance.

    Args:
        **kwargs: Arguments passed to GraphClientFactory on first creation

    Returns:
        The global GraphClientFactory instance

    Note:
        Factory is created lazily on first call. Subsequent calls return
        the same instance, ignoring any kwargs.
    """
    global _factory
    if _factory is None:
        _factory = GraphClientFactory(**kwargs)
    return _factory


def reset_factory() -> None:
    """Reset the global factory instance.

    Closes all clients and removes the singleton. Useful for testing.
    """
    global _factory
    if _factory is not None:
        _factory.close_all()
        _factory = None


def get_client_for_org(
    org_id: UUID, org_slug: Optional[str] = None
) -> DatabaseClient:
    """Convenience function to get a client for an organization.

    Uses the global factory instance.

    Args:
        org_id: Organization UUID
        org_slug: Organization slug (optional, for readable graph names)

    Returns:
        DatabaseClient isolated to the organization's graph

    Examples:
        >>> from repotoire.graph.tenant_factory import get_client_for_org
        >>> client = get_client_for_org(org.id, org.slug)
        >>> stats = client.get_stats()
    """
    return get_factory().get_client(org_id, org_slug)
