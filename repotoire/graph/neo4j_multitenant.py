"""Multi-tenant Neo4j clients for SaaS deployment.

This module provides two multi-tenancy strategies for Neo4j:

1. Neo4jClientMultiTenant: Uses database-per-tenant (Neo4j Enterprise)
   - Each organization gets its own Neo4j database
   - Complete isolation, no query modifications needed
   - Requires Neo4j Enterprise Edition

2. Neo4jClientPartitioned: Uses partition strategy (Neo4j Community)
   - All organizations share a single database
   - Every node gets an org_id property
   - Queries must filter by org_id

Usage:
    # Database-per-tenant (Enterprise)
    client = Neo4jClientMultiTenant(
        uri="bolt://localhost:7687",
        username="neo4j",
        password="password",
        database="org_acme_corp",
        org_id=UUID("..."),
    )

    # Partition strategy (Community)
    client = Neo4jClientPartitioned(
        uri="bolt://localhost:7687",
        username="neo4j",
        password="password",
        org_id=UUID("..."),
    )
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from neo4j import Result

from repotoire.graph.client import Neo4jClient
from repotoire.models import Entity

logger = logging.getLogger(__name__)


class Neo4jClientMultiTenant(Neo4jClient):
    """Neo4j client that connects to a specific database (Enterprise feature).

    Each organization gets its own Neo4j database for complete isolation.
    This requires Neo4j Enterprise Edition which supports multiple databases.

    All queries are automatically routed to the tenant's database without
    any query modifications.

    Attributes:
        _database: Database name for this tenant
        _org_id: Organization UUID

    Example:
        >>> client = Neo4jClientMultiTenant(
        ...     uri="bolt://localhost:7687",
        ...     username="neo4j",
        ...     password="password",
        ...     database="org_acme_corp",
        ...     org_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
        ... )
        >>> # All queries go to the org_acme_corp database
        >>> client.execute_query("MATCH (n) RETURN n LIMIT 10")
    """

    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        database: str,
        org_id: UUID,
        **kwargs: Any,
    ):
        """Initialize with a specific database.

        Args:
            uri: Neo4j connection URI
            username: Neo4j username
            password: Neo4j password
            database: Database name for this tenant
            org_id: Organization UUID
            **kwargs: Additional arguments passed to Neo4jClient
        """
        self._database = database
        self._org_id = org_id

        # Call parent init
        super().__init__(uri=uri, username=username, password=password, **kwargs)

        logger.info(
            f"Multi-tenant Neo4j client initialized: database={database}, "
            f"org_id={org_id}"
        )

    @property
    def database(self) -> str:
        """Get the database name for this tenant."""
        return self._database

    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> List[Dict]:
        """Execute query in the tenant's database.

        All queries are routed to the org-specific database, ensuring
        complete data isolation without query modifications.

        Args:
            query: Cypher query string
            parameters: Query parameters
            timeout: Query timeout in seconds

        Returns:
            List of result records as dictionaries
        """
        timeout_ms = int((timeout or self.query_timeout) * 1000)

        def _execute():
            # Specify database in session
            with self.driver.session(database=self._database) as session:
                result: Result = session.run(
                    query, parameters or {}, timeout=timeout_ms
                )
                return [dict(record) for record in result]

        return self._retry_operation(_execute, operation_name="execute_query")

    def clear_graph(self) -> None:
        """Clear all nodes and relationships in the tenant's database."""
        query = "MATCH (n) DETACH DELETE n"
        self.execute_query(query)
        logger.info(f"Cleared graph in database {self._database}")


class Neo4jClientPartitioned(Neo4jClient):
    """Neo4j client that uses graph partitioning for multi-tenancy.

    All organizations share a single database, but data is isolated by
    adding an org_id property to every node. Queries should filter by org_id.

    Use this for Neo4j Community Edition which doesn't support multiple databases.

    WARNING: This strategy requires careful query construction to ensure
    all queries include org_id filtering. Use the provided methods rather
    than raw execute_query when possible.

    Attributes:
        _org_id: Organization UUID for partitioning

    Example:
        >>> client = Neo4jClientPartitioned(
        ...     uri="bolt://localhost:7687",
        ...     username="neo4j",
        ...     password="password",
        ...     org_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
        ... )
        >>> # Node creation automatically includes org_id
        >>> client.batch_create_nodes(entities)
    """

    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        org_id: UUID,
        **kwargs: Any,
    ):
        """Initialize with org_id for query filtering.

        Args:
            uri: Neo4j connection URI
            username: Neo4j username
            password: Neo4j password
            org_id: Organization UUID for partitioning
            **kwargs: Additional arguments passed to Neo4jClient
        """
        self._org_id = org_id

        # Call parent init
        super().__init__(uri=uri, username=username, password=password, **kwargs)

        logger.info(f"Partitioned Neo4j client initialized: org_id={org_id}")

    def _get_org_id_str(self) -> str:
        """Get org_id as string for query parameters."""
        return str(self._org_id)

    def batch_create_nodes(self, entities: List[Entity]) -> Dict[str, str]:
        """Create nodes with org_id property for partitioning.

        Automatically adds org_id to all entities for tenant isolation.

        Args:
            entities: List of entities to create

        Returns:
            Dict mapping qualified_name to elementId
        """
        # Add org_id to all entities via extra_props
        for entity in entities:
            if not hasattr(entity, "extra_props") or entity.extra_props is None:
                entity.extra_props = {}
            entity.extra_props["org_id"] = self._get_org_id_str()

        return super().batch_create_nodes(entities)

    def create_node(self, entity: Entity) -> str:
        """Create a node with org_id property.

        Automatically adds org_id to the entity for tenant isolation.

        Args:
            entity: Entity to create

        Returns:
            Node ID
        """
        if not hasattr(entity, "extra_props") or entity.extra_props is None:
            entity.extra_props = {}
        entity.extra_props["org_id"] = self._get_org_id_str()

        return super().create_node(entity)

    def execute_query_with_org_filter(
        self,
        query: str,
        parameters: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> List[Dict]:
        """Execute query with org_id parameter automatically added.

        Use this method when you need to include $org_id in your query.
        The org_id parameter is automatically added to the parameters dict.

        Args:
            query: Cypher query string (should use $org_id parameter)
            parameters: Query parameters (org_id will be added)
            timeout: Query timeout in seconds

        Returns:
            List of result records as dictionaries

        Example:
            >>> results = client.execute_query_with_org_filter(
            ...     "MATCH (n {org_id: $org_id}) RETURN n LIMIT 10"
            ... )
        """
        params = parameters.copy() if parameters else {}
        params["org_id"] = self._get_org_id_str()
        return self.execute_query(query, params, timeout)

    def clear_graph(self) -> None:
        """Clear only this org's data (not the entire database).

        Only deletes nodes with matching org_id, preserving other tenants' data.
        """
        query = """
        MATCH (n {org_id: $org_id})
        DETACH DELETE n
        """
        self.execute_query(query, {"org_id": self._get_org_id_str()})
        logger.info(f"Cleared graph for org {self._org_id}")

    def get_stats(self) -> Dict[str, int]:
        """Get graph statistics for this tenant only.

        Returns:
            Dictionary with node/relationship counts for this org
        """
        query = """
        MATCH (n {org_id: $org_id})
        WITH count(n) as node_count
        MATCH (n {org_id: $org_id})-[r]->()
        RETURN node_count, count(r) as rel_count
        """
        result = self.execute_query(query, {"org_id": self._get_org_id_str()})

        if result:
            return {
                "nodes": result[0].get("node_count", 0),
                "relationships": result[0].get("rel_count", 0),
            }

        # No data yet
        return {"nodes": 0, "relationships": 0}

    def get_all_file_paths(self) -> List[str]:
        """Get all file paths for this tenant.

        Returns:
            List of file paths in this org's graph
        """
        query = """
        MATCH (f:File {org_id: $org_id})
        RETURN f.path as path
        """
        result = self.execute_query(query, {"org_id": self._get_org_id_str()})
        return [r["path"] for r in result if r.get("path")]

    def get_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get file metadata for this tenant.

        Args:
            file_path: Path to the file

        Returns:
            File metadata dict or None if not found
        """
        query = """
        MATCH (f:File {path: $path, org_id: $org_id})
        RETURN f.hash as hash, f.lastModified as last_modified
        """
        result = self.execute_query(
            query,
            {"path": file_path, "org_id": self._get_org_id_str()},
        )

        if result:
            return {
                "hash": result[0].get("hash"),
                "last_modified": result[0].get("last_modified"),
            }
        return None

    def delete_file_entities(self, file_path: str) -> int:
        """Delete a file and all related entities for this tenant.

        Args:
            file_path: Path to the file to delete

        Returns:
            Number of nodes deleted
        """
        query = """
        MATCH (f:File {path: $path, org_id: $org_id})
        OPTIONAL MATCH (f)-[:CONTAINS*]->(child)
        WITH f, collect(child) as children
        UNWIND children + [f] as node
        DETACH DELETE node
        RETURN count(*) as deleted_count
        """
        result = self.execute_query(
            query,
            {"path": file_path, "org_id": self._get_org_id_str()},
        )

        deleted = result[0].get("deleted_count", 0) if result else 0
        logger.debug(f"Deleted {deleted} nodes for file {file_path} in org {self._org_id}")
        return deleted

    def ensure_org_id_index(self) -> None:
        """Create index on org_id property for efficient partitioned queries.

        Should be called once during database setup. Creating duplicate indexes
        is a no-op, so this is safe to call multiple times.
        """
        # Create composite indexes for common node types
        node_types = ["File", "Module", "Class", "Function", "Variable", "Attribute"]

        for node_type in node_types:
            try:
                # Create index if not exists
                self.execute_query(
                    f"CREATE INDEX IF NOT EXISTS FOR (n:{node_type}) ON (n.org_id)"
                )
                logger.debug(f"Ensured org_id index on {node_type}")
            except Exception as e:
                logger.warning(f"Could not create index on {node_type}.org_id: {e}")

        logger.info("Ensured org_id indexes for partitioned multi-tenancy")
