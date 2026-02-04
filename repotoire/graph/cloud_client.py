"""Cloud proxy client for graph database operations.

This client proxies all graph operations through the Repotoire API,
allowing the CLI to work without direct database access.
"""

import os
import threading
from typing import Any, Dict, List, Optional

import httpx

from repotoire.graph.base import DatabaseClient
from repotoire.logging_config import get_logger
from repotoire.models import Entity, Relationship

logger = get_logger(__name__)

DEFAULT_API_URL = "https://repotoire-api.fly.dev"


class CloudProxyClient(DatabaseClient):
    """Graph database client that proxies through the Repotoire API.

    All operations are sent to the API which executes them on the
    internal FalkorDB instance. This allows the CLI to work without
    direct database connectivity.
    """

    def __init__(
        self,
        api_key: str,
        api_url: Optional[str] = None,
        timeout: float = 60.0,
    ):
        """Initialize the cloud proxy client.

        Args:
            api_key: Repotoire API key for authentication
            api_url: API base URL (defaults to production)
            timeout: Request timeout in seconds
        """
        self.api_key = api_key
        self.api_url = api_url or os.environ.get("REPOTOIRE_API_URL", DEFAULT_API_URL)
        self.timeout = timeout
        # Thread-local storage for httpx client to avoid sharing across threads
        self._local = threading.local()

    @property
    def _client(self) -> httpx.Client:
        """Get thread-local httpx client.

        Creates a new client for each thread to avoid thread-safety issues
        with httpx.Client which is not thread-safe.
        """
        if not hasattr(self._local, 'client') or self._local.client is None:
            self._local.client = httpx.Client(
                base_url=f"{self.api_url}/api/v1/graph",
                headers={"X-API-Key": self.api_key},
                timeout=self.timeout,
            )
        return self._local.client

    @property
    def is_falkordb(self) -> bool:
        """Cloud backend uses FalkorDB."""
        return True

    def close(self) -> None:
        """Close the HTTP client for the current thread."""
        if hasattr(self._local, 'client') and self._local.client is not None:
            self._local.client.close()
            self._local.client = None

    def _request(
        self,
        method: str,
        endpoint: str,
        json: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict:
        """Make an API request.

        Args:
            method: HTTP method
            endpoint: API endpoint (relative to /api/v1/graph)
            json: JSON body
            params: Query parameters

        Returns:
            Response JSON

        Raises:
            Exception: On API error
        """
        response = self._client.request(
            method=method,
            url=endpoint,
            json=json,
            params=params,
        )

        if response.status_code >= 400:
            try:
                error = response.json()
                detail = error.get("detail", str(error))
            except Exception:
                detail = response.text
            raise Exception(f"API error ({response.status_code}): {detail}")

        return response.json()

    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> List[Dict]:
        """Execute a Cypher query via the API."""
        response = self._request(
            "POST",
            "/query",
            json={
                "query": query,
                "parameters": parameters,
                "timeout": timeout,
            },
        )
        return response.get("results", [])

    def create_node(self, entity: Entity) -> str:
        """Create a single node."""
        result = self.batch_create_nodes([entity])
        return result.get(entity.qualified_name, "")

    def create_relationship(self, rel: Relationship) -> None:
        """Create a single relationship."""
        self.batch_create_relationships([rel])

    def batch_create_nodes(self, entities: List[Entity]) -> Dict[str, str]:
        """Create multiple nodes via the API."""
        entity_dicts = []
        for e in entities:
            entity_dict = {
                "entity_type": e.node_type.value if e.node_type else "Unknown",
                "name": e.name,
                "qualified_name": e.qualified_name,
                "file_path": e.file_path,
                "line_start": e.line_start,
                "line_end": e.line_end,
                "docstring": e.docstring,
            }

            # Add repo_id and repo_slug for multi-tenant isolation
            if e.repo_id:
                entity_dict["repo_id"] = e.repo_id
            if e.repo_slug:
                entity_dict["repo_slug"] = e.repo_slug

            # Add type-specific fields (matching FalkorDB client)
            for attr in ["is_external", "package", "loc", "hash", "language",
                         "exports", "is_abstract", "complexity", "parameters",
                         "return_type", "is_async", "decorators", "is_method",
                         "is_static", "is_classmethod", "is_property"]:
                if hasattr(e, attr):
                    val = getattr(e, attr)
                    if val is not None:
                        entity_dict[attr] = val

            entity_dicts.append(entity_dict)

        response = self._request(
            "POST",
            "/batch/nodes",
            json={"entities": entity_dicts},
        )
        return response.get("created", {})

    def batch_create_relationships(self, relationships: List[Relationship]) -> int:
        """Create multiple relationships via the API."""
        rel_dicts = []
        for r in relationships:
            rel_dict = {
                "source_id": r.source_id,
                "target_id": r.target_id,
                "rel_type": r.rel_type.value if hasattr(r.rel_type, 'value') else str(r.rel_type),
                "properties": r.properties or {},
            }
            rel_dicts.append(rel_dict)

        response = self._request(
            "POST",
            "/batch/relationships",
            json={"relationships": rel_dicts},
        )
        return response.get("count", 0)

    def clear_graph(self) -> None:
        """Clear all nodes and relationships."""
        self._request("DELETE", "/clear")

    def create_indexes(self) -> None:
        """Create indexes for better performance."""
        self._request("POST", "/indexes")

    def get_stats(self) -> Dict[str, int]:
        """Get graph statistics."""
        response = self._request("GET", "/stats")
        return response.get("stats", {})

    def get_all_file_paths(self) -> List[str]:
        """Get all file paths in the graph."""
        response = self._request("GET", "/files")
        return response.get("paths", [])

    def get_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific file."""
        try:
            response = self._request(
                "GET",
                f"/files/{file_path}/metadata",
            )
            return response.get("metadata")
        except Exception as e:
            logger.debug(f"Could not get metadata for file '{file_path}': {e}")
            return None

    def batch_get_file_metadata(self, file_paths: List[str]) -> Dict[str, Dict[str, Any]]:
        """Get file metadata for multiple files in a single query.

        Performance: Uses UNWIND to fetch all metadata in O(1) query instead of O(N).

        Args:
            file_paths: List of file paths to fetch metadata for

        Returns:
            Dict mapping file_path to metadata dict (hash, lastModified).
            Files not found in database are not included in result.
        """
        if not file_paths:
            return {}

        query = """
        UNWIND $paths AS path
        MATCH (f:File {filePath: path})
        RETURN f.filePath as filePath, f.hash as hash, f.lastModified as lastModified
        """
        result = self.execute_query(query, {"paths": file_paths})

        return {
            record["filePath"]: {
                "hash": record["hash"],
                "lastModified": record["lastModified"]
            }
            for record in result
            if record.get("filePath")
        }

    def delete_file_entities(self, file_path: str) -> int:
        """Delete a file and its related entities."""
        response = self._request("DELETE", f"/files/{file_path}")
        return response.get("deleted", 0)

    def batch_delete_file_entities(self, file_paths: List[str]) -> int:
        """Delete multiple files and their related entities in a single query.

        Performance: Uses UNWIND to delete all files in O(1) query instead of O(N).

        Args:
            file_paths: List of file paths to delete

        Returns:
            Total count of deleted nodes
        """
        if not file_paths:
            return 0

        query = """
        UNWIND $paths AS path
        MATCH (f:File {filePath: path})
        OPTIONAL MATCH (f)-[:CONTAINS*]->(entity)
        WITH f, collect(entity) as entities
        DETACH DELETE f
        WITH entities
        UNWIND entities as entity
        DETACH DELETE entity
        RETURN count(*) as deletedCount
        """
        try:
            result = self.execute_query(query, {"paths": file_paths})
            deleted_count = result[0]["deletedCount"] if result else 0
            logger.info(f"Batch deleted {deleted_count} nodes for {len(file_paths)} files")
            return deleted_count
        except Exception as e:
            logger.error(f"Failed batch delete: {e}")
            return 0

    def get_node_label_counts(self) -> Dict[str, int]:
        """Get counts for each node label."""
        query = """
        MATCH (n)
        RETURN labels(n)[0] as label, count(n) as count
        ORDER BY count DESC
        """
        result = self.execute_query(query)
        return {record["label"]: record["count"] for record in result if record.get("label")}

    def get_relationship_type_counts(self) -> Dict[str, int]:
        """Get counts for each relationship type."""
        query = """
        MATCH ()-[r]->()
        RETURN type(r) as rel_type, count(r) as count
        ORDER BY count DESC
        """
        result = self.execute_query(query)
        return {record["rel_type"]: record["count"] for record in result}

    def validate_schema_integrity(self) -> Dict[str, Any]:
        """Validate graph schema integrity.

        Returns:
            Dictionary with 'valid' boolean and 'issues' dict with counts
        """
        issues = {}

        # Check for orphaned nodes (no relationships)
        # Note: FalkorDB uses labels() function for label checks instead of inline syntax
        query = """
        MATCH (n)
        WHERE NOT (n)--()
        AND NOT 'File' IN labels(n) AND NOT 'Module' IN labels(n)
        RETURN count(n) as count
        """
        result = self.execute_query(query)
        orphaned = result[0]["count"] if result else 0
        if orphaned > 0:
            issues["orphaned_nodes"] = orphaned

        # Check for nodes missing qualified_name
        query = """
        MATCH (n)
        WHERE n.qualifiedName IS NULL
        RETURN count(n) as count
        """
        result = self.execute_query(query)
        missing_qn = result[0]["count"] if result else 0
        if missing_qn > 0:
            issues["missing_qualified_name"] = missing_qn

        return {
            "valid": len(issues) == 0,
            "issues": issues,
        }

    def sample_nodes(self, label: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get sample nodes of a specific label.

        Args:
            label: Node label to sample
            limit: Maximum number of nodes to return (default 5, max 100)

        Returns:
            List of node property dictionaries
        """
        # Basic validation to prevent injection
        if not label.replace("_", "").isalnum():
            raise ValueError(f"Invalid label: {label}")
        # Validate and clamp limit to prevent abuse (max 100)
        safe_limit = max(1, min(int(limit), 100))
        query = f"""
        MATCH (n:{label})
        RETURN n
        LIMIT $limit
        """
        result = self.execute_query(query, parameters={"limit": safe_limit})
        return [dict(record["n"]) for record in result if "n" in record]
