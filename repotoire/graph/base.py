"""Abstract base class for graph database clients."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from uuid import UUID

from repotoire.models import Entity, Relationship


class DatabaseClient(ABC):
    """Abstract base class for graph database clients.

    Defines the interface that both Neo4jClient and FalkorDBClient implement.
    This allows the codebase to be database-agnostic.

    Multi-Tenancy Support:
        For SaaS deployments, clients can be configured with an org_id to
        isolate data per organization. Use GraphClientFactory for tenant-aware
        client creation.

    Examples:
        # Single-tenant mode (CLI usage)
        client = create_client()

        # Multi-tenant mode (SaaS)
        from repotoire.graph.tenant_factory import get_client_for_org
        client = get_client_for_org(org_id=org.id, org_slug=org.slug)
    """

    # Tenant context for multi-tenant isolation
    _org_id: Optional[UUID] = None

    @property
    def org_id(self) -> Optional[UUID]:
        """Organization ID for tenant isolation.

        Returns None for single-tenant mode (CLI usage).
        Returns UUID for multi-tenant mode (SaaS).
        """
        return self._org_id

    @property
    def is_multi_tenant(self) -> bool:
        """Whether this client enforces tenant isolation.

        When True, all operations are scoped to a specific organization's
        graph/database. When False, operates on a shared graph.
        """
        return self._org_id is not None

    @property
    def is_falkordb(self) -> bool:
        """Check if this is a FalkorDB client.

        Subclasses should override if needed. Default returns False (Neo4j).
        Used for database-specific query adaptations.
        """
        return False

    @property
    def supports_temporal_types(self) -> bool:
        """Check if database supports Neo4j temporal types (datetime, duration).

        FalkorDB doesn't support these - use UNIX timestamps instead.
        """
        return not self.is_falkordb

    @abstractmethod
    def close(self) -> None:
        """Close database connection."""
        pass

    @abstractmethod
    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> List[Dict]:
        """Execute a Cypher query and return results.

        Args:
            query: Cypher query string
            parameters: Query parameters
            timeout: Query timeout in seconds

        Returns:
            List of result records as dictionaries
        """
        pass

    @abstractmethod
    def create_node(self, entity: Entity) -> str:
        """Create a node in the graph.

        Args:
            entity: Entity to create

        Returns:
            Node ID
        """
        pass

    @abstractmethod
    def create_relationship(self, rel: Relationship) -> None:
        """Create a relationship between nodes.

        Args:
            rel: Relationship to create
        """
        pass

    @abstractmethod
    def batch_create_nodes(self, entities: List[Entity]) -> Dict[str, str]:
        """Create multiple nodes.

        Args:
            entities: List of entities to create

        Returns:
            Dict mapping qualified_name to ID
        """
        pass

    @abstractmethod
    def batch_create_relationships(self, relationships: List[Relationship]) -> int:
        """Create multiple relationships.

        Args:
            relationships: List of relationships to create

        Returns:
            Number of relationships created
        """
        pass

    @abstractmethod
    def clear_graph(self) -> None:
        """Delete all nodes and relationships."""
        pass

    def delete_repository(self, repo_id: str) -> int:
        """Delete all nodes for a specific repository.

        This method removes all nodes that have the given repo_id,
        including their relationships. Used for cleaning up repo data
        when a repository is deleted or needs re-ingestion.

        Args:
            repo_id: Repository UUID string to delete

        Returns:
            Number of nodes deleted
        """
        # Default implementation - subclasses can override
        query = """
        MATCH (n {repoId: $repo_id})
        DETACH DELETE n
        RETURN count(n) as deleted
        """
        result = self.execute_query(query, {"repo_id": repo_id})
        if result:
            return result[0].get("deleted", 0)
        return 0

    @abstractmethod
    def create_indexes(self) -> None:
        """Create indexes for better query performance."""
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, int]:
        """Get graph statistics.

        Returns:
            Dictionary with node/relationship counts
        """
        pass

    @abstractmethod
    def get_all_file_paths(self) -> List[str]:
        """Get all file paths currently in the graph."""
        pass

    @abstractmethod
    def get_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get file metadata for incremental ingestion."""
        pass

    @abstractmethod
    def delete_file_entities(self, file_path: str) -> int:
        """Delete a file and all its related entities."""
        pass

    def __enter__(self) -> "DatabaseClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
