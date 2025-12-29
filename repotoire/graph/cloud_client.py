"""Cloud proxy client for graph database operations.

This client proxies all graph operations through the Repotoire API,
allowing the CLI to work without direct database access.
"""

import os
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
        self._client = httpx.Client(
            base_url=f"{self.api_url}/api/v1/graph",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    @property
    def is_falkordb(self) -> bool:
        """Cloud backend uses FalkorDB."""
        return True

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

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
                "entity_type": e.entity_type,
                "name": e.name,
                "qualified_name": e.qualified_name,
                "file_path": e.file_path,
                "line_number": e.line_number,
                "end_line_number": e.end_line_number,
                "properties": e.properties or {},
            }
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
                "source": r.source,
                "target": r.target,
                "relationship_type": r.relationship_type,
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
        except Exception:
            return None

    def delete_file_entities(self, file_path: str) -> int:
        """Delete a file and its related entities."""
        response = self._request("DELETE", f"/files/{file_path}")
        return response.get("deleted", 0)
