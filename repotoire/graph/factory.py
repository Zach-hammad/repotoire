"""Factory for creating graph database clients."""

import os
from typing import Optional
from urllib.parse import urlparse

from repotoire.graph.base import DatabaseClient


def create_client(
    uri: Optional[str] = None,
    db_type: Optional[str] = None,
    **kwargs
) -> DatabaseClient:
    """Create a graph database client based on configuration.

    Args:
        uri: Database connection URI. If not provided, uses environment variables.
        db_type: Explicit database type ('neo4j' or 'falkordb').
                 If not provided, auto-detects from URI or env var.
        **kwargs: Additional arguments passed to the client constructor.

    Returns:
        DatabaseClient instance (Neo4jClient or FalkorDBClient)

    Environment Variables:
        REPOTOIRE_DB_TYPE: 'neo4j' or 'falkordb' (default: neo4j)
        REPOTOIRE_NEO4J_URI: Neo4j connection URI (default: bolt://localhost:7687)
        REPOTOIRE_FALKORDB_HOST: FalkorDB host (default: localhost)
        REPOTOIRE_FALKORDB_PORT: FalkorDB port (default: 6379)
        REPOTOIRE_NEO4J_PASSWORD: Database password

    Examples:
        # Auto-detect from environment
        client = create_client()

        # Explicit Neo4j
        client = create_client(uri="bolt://localhost:7687", db_type="neo4j")

        # Explicit FalkorDB
        client = create_client(db_type="falkordb", host="localhost", port=6379)
    """
    # Determine database type
    if db_type is None:
        db_type = os.environ.get("REPOTOIRE_DB_TYPE", "neo4j").lower()

    # Auto-detect from URI scheme if provided
    if uri and db_type == "neo4j":
        parsed = urlparse(uri)
        if parsed.scheme in ("redis", "rediss"):
            db_type = "falkordb"

    if db_type == "falkordb":
        return _create_falkordb_client(uri, **kwargs)
    else:
        return _create_neo4j_client(uri, **kwargs)


def _create_neo4j_client(uri: Optional[str], **kwargs) -> DatabaseClient:
    """Create a Neo4j client."""
    from repotoire.graph.client import Neo4jClient

    if uri is None:
        uri = os.environ.get("REPOTOIRE_NEO4J_URI", "bolt://localhost:7687")

    password = kwargs.pop("password", None)
    if password is None:
        password = os.environ.get("REPOTOIRE_NEO4J_PASSWORD", "password")

    username = kwargs.pop("username", None)
    if username is None:
        username = os.environ.get("REPOTOIRE_NEO4J_USERNAME", "neo4j")

    return Neo4jClient(
        uri=uri,
        username=username,
        password=password,
        **kwargs
    )


def _create_falkordb_client(uri: Optional[str], **kwargs) -> DatabaseClient:
    """Create a FalkorDB client."""
    from repotoire.graph.falkordb_client import FalkorDBClient

    # Parse URI if provided
    if uri:
        parsed = urlparse(uri)
        kwargs.setdefault("host", parsed.hostname or "localhost")
        kwargs.setdefault("port", parsed.port or 6379)
        if parsed.password:
            kwargs.setdefault("password", parsed.password)
    else:
        # Use environment variables
        kwargs.setdefault("host", os.environ.get("REPOTOIRE_FALKORDB_HOST", "localhost"))
        kwargs.setdefault("port", int(os.environ.get("REPOTOIRE_FALKORDB_PORT", "6379")))

    password = os.environ.get("REPOTOIRE_FALKORDB_PASSWORD")
    if password:
        kwargs.setdefault("password", password)

    kwargs.setdefault("graph_name", os.environ.get("REPOTOIRE_FALKORDB_GRAPH", "repotoire"))

    return FalkorDBClient(**kwargs)
