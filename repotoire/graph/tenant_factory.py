"""Multi-tenant graph client factory.

This module provides tenant-isolated graph database clients for SaaS deployments.
Each organization gets its own isolated graph to ensure complete data separation.

Examples:
    # Create factory
    factory = GraphClientFactory()

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
import threading
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

    Each organization gets a dedicated graph within the FalkorDB instance.
    The factory caches clients per organization to avoid creating duplicate
    connections. Use close_client() or close_all() to release resources.

    Examples:
        >>> factory = GraphClientFactory()
        >>> client = factory.get_client(org_id=UUID("..."), org_slug="acme-corp")
        >>> # Client operates on graph "org_acme_corp"
    """

    # Cache of active clients per org
    _clients: Dict[UUID, DatabaseClient]
    # Lock for thread-safe client cache access
    _lock: threading.Lock

    def __init__(
        self,
        falkordb_host: Optional[str] = None,
        falkordb_port: Optional[int] = None,
        falkordb_password: Optional[str] = None,
    ):
        """Initialize the factory.

        Args:
            falkordb_host: FalkorDB host.
                          Defaults to FALKORDB_HOST or "localhost".
            falkordb_port: FalkorDB port.
                          Defaults to FALKORDB_PORT or 6379.
            falkordb_password: FalkorDB password.
                              Defaults to FALKORDB_PASSWORD.
        """
        self._clients = {}
        self._lock = threading.Lock()

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
            f"GraphClientFactory initialized: host={self.falkordb_host}, "
            f"port={self.falkordb_port}"
        )

    def get_client(
        self, org_id: UUID, org_slug: Optional[str] = None
    ) -> DatabaseClient:
        """Get a tenant-isolated graph client for an organization.

        Clients are cached per organization. Subsequent calls with the same
        org_id return the cached client.

        Args:
            org_id: Organization UUID for isolation
            org_slug: Organization slug (used for graph naming).
                     If not provided, uses first 8 chars of org_id hex.

        Returns:
            DatabaseClient isolated to the organization's graph

        Examples:
            >>> client = factory.get_client(
            ...     org_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            ...     org_slug="acme-corp"
            ... )
            >>> # Client operates on graph "org_acme_corp"
        """
        # Check cache first (fast path without lock)
        if org_id in self._clients:
            return self._clients[org_id]

        # Acquire lock for thread-safe client creation
        with self._lock:
            # Double-check after acquiring lock (another thread may have created it)
            if org_id in self._clients:
                return self._clients[org_id]

            # Generate graph name from org
            graph_name = self._generate_graph_name(org_id, org_slug)

            client = self._create_falkordb_client(org_id, graph_name)

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
            graph_name: Graph name
            action: Action being performed (e.g., "client_created", "query", "provisioned")
        """
        logger.info(
            "Tenant graph access",
            extra={
                "tenant_id": str(org_id),
                "tenant_slug": org_slug,
                "graph_name": graph_name,
                "action": action,
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
        """Generate a unique graph name for an organization.

        Uses slug if available (human-readable), falls back to UUID.
        Sanitizes to be valid graph name (alphanumeric + underscore).

        Args:
            org_id: Organization UUID
            org_slug: Optional organization slug

        Returns:
            Sanitized graph name (e.g., "org_acme_corp" or "org_550e8400")
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

    def close_client(self, org_id: UUID) -> None:
        """Close and remove a cached client.

        Args:
            org_id: Organization UUID whose client should be closed
        """
        with self._lock:
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

        FalkorDB graphs are created automatically on first query,
        so this is essentially a no-op that returns the graph name.

        Args:
            org_id: Organization UUID
            org_slug: Organization slug for naming

        Returns:
            Graph name that was provisioned

        Note:
            This is idempotent - calling multiple times is safe.
        """
        graph_name = self._generate_graph_name(org_id, org_slug)

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
_factory_lock = threading.Lock()


def get_factory(**kwargs) -> GraphClientFactory:
    """Get or create the global factory instance.

    Thread-safe singleton using double-checked locking pattern.

    Args:
        **kwargs: Arguments passed to GraphClientFactory on first creation

    Returns:
        The global GraphClientFactory instance

    Note:
        Factory is created lazily on first call. Subsequent calls return
        the same instance, ignoring any kwargs.
    """
    global _factory
    # Fast path without lock
    if _factory is not None:
        return _factory

    # Acquire lock for thread-safe creation
    with _factory_lock:
        # Double-check after acquiring lock
        if _factory is None:
            _factory = GraphClientFactory(**kwargs)
    return _factory


def reset_factory() -> None:
    """Reset the global factory instance.

    Thread-safe reset that closes all clients and removes the singleton.
    Useful for testing.
    """
    global _factory
    with _factory_lock:
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
