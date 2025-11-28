"""Abstract base class for graph database clients."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from repotoire.models import Entity, Relationship


class DatabaseClient(ABC):
    """Abstract base class for graph database clients.

    Defines the interface that both Neo4jClient and FalkorDBClient implement.
    This allows the codebase to be database-agnostic.
    """

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
