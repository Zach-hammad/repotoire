"""External vector store abstraction for memory-efficient embedding storage.

Provides disk-backed vector storage to reduce RAM usage from FalkorDB.
Primary implementation: LanceDB (zero-copy via Apache Arrow).

Memory Savings:
- Moves embeddings from Redis/FalkorDB RAM to disk
- 10K entities @ 4096 dims: ~164MB RAM → ~0 RAM (disk-backed)
- 3-5ms query latency at 95% recall

Usage:
    >>> from repotoire.ai.vector_store import create_vector_store
    >>> store = create_vector_store("lancedb", path="./vectors")
    >>> store.bulk_index(entity_ids, embeddings, metadata)
    >>> results = store.search(query_embedding, top_k=10)
"""

import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Type alias for vector store backends
VectorStoreBackend = Literal["lancedb", "graph", "none"]


@dataclass
class VectorSearchResult:
    """Result from vector similarity search.

    Attributes:
        entity_id: Qualified name or unique ID of the entity
        score: Similarity score (higher = more similar)
        entity_type: Type of entity (Function, Class, File)
        metadata: Additional metadata stored with the embedding
    """
    entity_id: str
    score: float
    entity_type: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VectorStoreConfig:
    """Configuration for vector store.

    Attributes:
        backend: Vector store backend to use
        path: Path for disk-backed stores (LanceDB)
        dimensions: Embedding dimensions (auto-detected if not set)
        metric: Distance metric for similarity (cosine, euclidean, dot)
        cache_size: LRU cache size for frequent queries
        table_name: Name of the vector table/index
    """
    backend: VectorStoreBackend = "lancedb"
    path: str = "./.repotoire/vectors"
    dimensions: int = 0  # Auto-detect from first embedding
    metric: str = "cosine"
    cache_size: int = 1000
    table_name: str = "code_embeddings"


class VectorStore(ABC):
    """Abstract base class for vector storage backends.

    Implementations provide:
    - Bulk indexing of embeddings with metadata
    - Similarity search with filtering
    - Efficient disk-backed storage
    """

    @abstractmethod
    def bulk_index(
        self,
        entity_ids: List[str],
        embeddings: List[List[float]],
        entity_types: List[str],
        metadata: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Index multiple embeddings in a single batch operation.

        Args:
            entity_ids: Unique identifiers (qualified names)
            embeddings: Embedding vectors
            entity_types: Entity types (Function, Class, File)
            metadata: Optional metadata dicts for each entity

        Returns:
            Number of embeddings indexed
        """
        pass

    @abstractmethod
    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        entity_types: Optional[List[str]] = None,
        filter_metadata: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> List[VectorSearchResult]:
        """Search for similar embeddings.

        REPO-600: Supports tenant_id filtering for multi-tenant data isolation.

        Args:
            query_embedding: Query vector
            top_k: Number of results to return
            entity_types: Filter by entity types
            filter_metadata: Additional metadata filters
            tenant_id: Tenant ID for multi-tenant filtering (REPO-600)

        Returns:
            List of search results ordered by similarity
        """
        pass

    @abstractmethod
    def delete(self, entity_ids: List[str]) -> int:
        """Delete embeddings by entity ID.

        Args:
            entity_ids: IDs to delete

        Returns:
            Number of embeddings deleted
        """
        pass

    @abstractmethod
    def count(self) -> int:
        """Get total number of indexed embeddings."""
        pass

    @abstractmethod
    def exists(self, entity_id: str) -> bool:
        """Check if entity is indexed."""
        pass

    def close(self) -> None:
        """Close any open connections/files."""
        pass


class LanceDBVectorStore(VectorStore):
    """LanceDB implementation for disk-backed vector storage.

    Features:
    - Zero-copy reads via Apache Arrow
    - 3-5ms latency at 95% recall
    - Embedded (no server process)
    - Free and open-source
    - Production-proven at 700M+ vectors

    Memory:
    - Data stored on disk, only loaded during queries
    - ~0 RAM for storage (vs ~164MB for 10K entities in Redis)
    """

    def __init__(self, config: VectorStoreConfig):
        """Initialize LanceDB vector store.

        Args:
            config: Vector store configuration
        """
        self.config = config
        self._db = None
        self._table = None
        self._dimensions = config.dimensions
        # Thread-safe lock for dimension detection and table access
        self._lock = threading.Lock()

        # Lazy import to make LanceDB optional
        try:
            import lancedb
            self._lancedb = lancedb
        except ImportError:
            raise ImportError(
                "lancedb required for LanceDB vector store. "
                "Install with: pip install repotoire[lancedb]"
            )

        self._initialize()

    def _initialize(self) -> None:
        """Initialize LanceDB connection and table."""
        # Create directory if needed
        path = Path(self.config.path)
        path.mkdir(parents=True, exist_ok=True)

        # Connect to LanceDB
        self._db = self._lancedb.connect(str(path))

        # Check if table exists
        try:
            self._table = self._db.open_table(self.config.table_name)
            logger.info(
                f"Opened existing LanceDB table: {self.config.table_name} "
                f"({self._table.count_rows()} rows)"
            )
        except Exception:
            # Table doesn't exist yet, will be created on first insert
            self._table = None
            logger.info(f"LanceDB initialized at {path} (table will be created on first insert)")

    def bulk_index(
        self,
        entity_ids: List[str],
        embeddings: List[List[float]],
        entity_types: List[str],
        metadata: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Index embeddings using LanceDB's efficient bulk operations.

        Args:
            entity_ids: Qualified names for entities
            embeddings: Embedding vectors
            entity_types: Entity types (Function, Class, File)
            metadata: Optional metadata for each entity

        Returns:
            Number of embeddings indexed
        """
        if not entity_ids:
            return 0

        # Thread-safe dimension detection and table access
        with self._lock:
            # Auto-detect dimensions from first embedding
            if self._dimensions == 0:
                self._dimensions = len(embeddings[0])

            # Build data records
            records = []
            for i, (eid, emb, etype) in enumerate(zip(entity_ids, embeddings, entity_types)):
                record = {
                    "entity_id": eid,
                    "vector": emb,
                    "entity_type": etype,
                }
                # Add metadata fields
                if metadata and i < len(metadata):
                    for k, v in metadata[i].items():
                        # LanceDB handles various types well
                        record[k] = v
                records.append(record)

            # Create or update table
            if self._table is None:
                # Create new table
                self._table = self._db.create_table(
                    self.config.table_name,
                    data=records,
                    mode="overwrite"
                )
                logger.info(
                    f"Created LanceDB table: {self.config.table_name} "
                    f"with {len(records)} records"
                )
            else:
                # Append to existing table
                # First, delete existing records with same entity_ids (upsert)
                try:
                    existing_ids = set(entity_ids)
                    # LanceDB delete with filter
                    self._table.delete(f"entity_id IN {tuple(existing_ids)}")
                except Exception as e:
                    logger.debug(f"Delete during upsert: {e}")

                self._table.add(records)
                logger.info(f"Added {len(records)} records to LanceDB table")

            return len(records)

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        entity_types: Optional[List[str]] = None,
        filter_metadata: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> List[VectorSearchResult]:
        """Search for similar embeddings using LanceDB ANN search.

        REPO-600: Supports tenant_id filtering for multi-tenant data isolation.

        Args:
            query_embedding: Query vector
            top_k: Number of results
            entity_types: Optional type filter
            filter_metadata: Optional metadata filters
            tenant_id: Tenant ID for multi-tenant filtering (REPO-600)

        Returns:
            Search results ordered by similarity
        """
        with self._lock:
            if self._table is None:
                logger.warning("LanceDB table not initialized, returning empty results")
                return []

            # Build query
            query = self._table.search(query_embedding)

            # REPO-600: Apply tenant filter first (most restrictive)
            if tenant_id:
                query = query.where(f"tenant_id = '{tenant_id}'")

            # Apply entity type filter
            if entity_types:
                if len(entity_types) == 1:
                    query = query.where(f"entity_type = '{entity_types[0]}'")
                else:
                    type_list = ", ".join(f"'{t}'" for t in entity_types)
                    query = query.where(f"entity_type IN ({type_list})")

            # Apply metadata filters
            if filter_metadata:
                for key, value in filter_metadata.items():
                    if isinstance(value, str):
                        query = query.where(f"{key} = '{value}'")
                    else:
                        query = query.where(f"{key} = {value}")

            # Execute search
            try:
                results = query.limit(top_k).to_list()
            except Exception as e:
                logger.error(f"LanceDB search failed: {e}")
                return []

            # Convert to VectorSearchResult
            search_results = []
            for row in results:
                # LanceDB returns _distance, convert to similarity score
                # For cosine, distance is 1 - similarity
                distance = row.get("_distance", 0)
                score = 1.0 - distance if self.config.metric == "cosine" else 1.0 / (1.0 + distance)

                # Build metadata from remaining fields
                metadata = {
                    k: v for k, v in row.items()
                    if k not in ("entity_id", "vector", "entity_type", "_distance")
                }

                search_results.append(VectorSearchResult(
                    entity_id=row["entity_id"],
                    score=score,
                    entity_type=row.get("entity_type", ""),
                    metadata=metadata,
                ))

            return search_results

    def delete(self, entity_ids: List[str]) -> int:
        """Delete embeddings by entity ID.

        Thread-safe: Uses lock for table access.
        """
        with self._lock:
            if self._table is None or not entity_ids:
                return 0

            try:
                # LanceDB delete with filter
                id_tuple = tuple(entity_ids) if len(entity_ids) > 1 else f"('{entity_ids[0]}')"
                self._table.delete(f"entity_id IN {id_tuple}")
                return len(entity_ids)
            except Exception as e:
                logger.error(f"Delete failed: {e}")
                return 0

    def count(self) -> int:
        """Get total number of indexed embeddings.

        Thread-safe: Uses lock for table access.
        """
        with self._lock:
            if self._table is None:
                return 0
            return self._table.count_rows()

    def exists(self, entity_id: str) -> bool:
        """Check if entity is indexed.

        Thread-safe: Uses lock for table access.
        """
        with self._lock:
            if self._table is None:
                return False

            try:
                result = self._table.search([0] * (self._dimensions or 1024)).where(
                    f"entity_id = '{entity_id}'"
                ).limit(1).to_list()
                return len(result) > 0
            except Exception:
                return False

    def close(self) -> None:
        """Close LanceDB connection.

        Thread-safe: Uses lock for table access.
        """
        with self._lock:
            # LanceDB handles cleanup automatically
            self._table = None
        self._db = None


class GraphVectorStore(VectorStore):
    """Passthrough implementation that uses FalkorDB/Neo4j for vector storage.

    This is the default when no external vector store is configured.
    Vectors are stored as node properties in the graph database.

    Use this when:
    - You want simplicity (no additional infrastructure)
    - Your codebase is small (<10K entities)
    - You have sufficient RAM for in-memory vector storage
    """

    def __init__(self, config: VectorStoreConfig):
        """Initialize graph vector store (no-op, vectors stored in graph)."""
        self.config = config
        logger.info(
            "Using graph-native vector storage (embeddings stored in FalkorDB/Neo4j). "
            "For memory optimization, consider: VectorStoreConfig(backend='lancedb')"
        )

    def bulk_index(
        self,
        entity_ids: List[str],
        embeddings: List[List[float]],
        entity_types: List[str],
        metadata: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """No-op for graph storage (handled by ingestion pipeline)."""
        # Graph stores vectors as node properties during ingestion
        return len(entity_ids)

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        entity_types: Optional[List[str]] = None,
        filter_metadata: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> List[VectorSearchResult]:
        """No-op - search handled by GraphRAGRetriever's _vector_search."""
        # Return empty to signal retriever should use graph search
        # tenant_id filtering is handled in GraphRAGRetriever._vector_search
        return []

    def delete(self, entity_ids: List[str]) -> int:
        """No-op for graph storage."""
        return 0

    def count(self) -> int:
        """Cannot count without graph client."""
        return -1

    def exists(self, entity_id: str) -> bool:
        """Cannot check without graph client."""
        return True


class NoOpVectorStore(VectorStore):
    """No-op implementation when vector storage is disabled."""

    def __init__(self, config: VectorStoreConfig):
        self.config = config
        logger.info("Vector storage disabled")

    def bulk_index(self, *args, **kwargs) -> int:
        return 0

    def search(self, *args, **kwargs) -> List[VectorSearchResult]:
        return []

    def delete(self, entity_ids: List[str]) -> int:
        return 0

    def count(self) -> int:
        return 0

    def exists(self, entity_id: str) -> bool:
        return False


def create_vector_store(
    backend: VectorStoreBackend = "lancedb",
    path: Optional[str] = None,
    config: Optional[VectorStoreConfig] = None,
    **kwargs,
) -> VectorStore:
    """Factory function to create a vector store.

    Args:
        backend: Vector store backend ("lancedb", "graph", "none")
        path: Path for disk-backed stores
        config: Full configuration (overrides other params)
        **kwargs: Additional config parameters

    Returns:
        Configured VectorStore instance

    Examples:
        >>> # LanceDB (disk-backed, memory-efficient)
        >>> store = create_vector_store("lancedb", path="./vectors")

        >>> # Graph-native (vectors in FalkorDB/Neo4j)
        >>> store = create_vector_store("graph")

        >>> # Disabled
        >>> store = create_vector_store("none")
    """
    if config is None:
        config = VectorStoreConfig(
            backend=backend,
            path=path or "./.repotoire/vectors",
            **kwargs,
        )

    if config.backend == "lancedb":
        return LanceDBVectorStore(config)
    elif config.backend == "graph":
        return GraphVectorStore(config)
    else:
        return NoOpVectorStore(config)


def get_default_vector_store_path(repo_path: Optional[str] = None) -> str:
    """Get the default vector store path for a repository.

    Args:
        repo_path: Repository path (uses cwd if not provided)

    Returns:
        Path for vector store data
    """
    base = repo_path or os.getcwd()
    return str(Path(base) / ".repotoire" / "vectors")
