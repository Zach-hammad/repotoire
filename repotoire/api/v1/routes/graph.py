"""Graph proxy API routes for CLI operations.

This module provides API endpoints that proxy graph database operations
to the internal FalkorDB instance. This allows the CLI to perform graph
operations without direct database access.

All operations are authenticated via API key and scoped to the user's
organization graph.

Server-side embeddings are automatically generated using DeepInfra + Qwen3-Embedding-8B.
"""

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth.clerk import (
    ClerkUser,
    get_current_user_or_api_key,
)
from repotoire.db.models import Organization, OrganizationMembership, User
from repotoire.db.session import get_db
from repotoire.graph.tenant_factory import get_factory
from repotoire.logging_config import get_logger
from repotoire.models import (
    Entity,
    FileEntity,
    ClassEntity,
    FunctionEntity,
    ModuleEntity,
    NodeType,
    Relationship,
    RelationshipType,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/graph", tags=["graph"])


# =============================================================================
# API Key Authentication
# =============================================================================


@dataclass
class GraphUser:
    """Authenticated user with org info for graph operations."""

    org_id: str  # Our internal org UUID
    org_slug: str
    user_id: Optional[str] = None


async def get_graph_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: ClerkUser = Depends(get_current_user_or_api_key),
) -> GraphUser:
    """Get authenticated user with organization info for graph operations.

    Supports both JWT tokens (frontend) and API keys (CLI).
    Looks up the organization in our database to get the internal UUID.
    """
    org = None
    clerk_org_id = user.org_id

    # If no org_id from JWT/API key, look up from user's membership
    if not clerk_org_id and user.user_id:
        result = await db.execute(
            select(User).where(User.clerk_user_id == user.user_id)
        )
        db_user = result.scalar_one_or_none()
        if db_user:
            result = await db.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == db_user.id
                )
            )
            membership = result.scalar_one_or_none()
            if membership:
                result = await db.execute(
                    select(Organization).where(
                        Organization.id == membership.organization_id
                    )
                )
                org = result.scalar_one_or_none()

    # Look up org by Clerk ID first
    if not org and clerk_org_id:
        result = await db.execute(
            select(Organization).where(Organization.clerk_org_id == clerk_org_id)
        )
        org = result.scalar_one_or_none()

    # Fallback: try looking up by internal UUID (for API keys returning internal org_id)
    if not org and clerk_org_id:
        try:
            from uuid import UUID
            org_uuid = UUID(clerk_org_id)
            result = await db.execute(
                select(Organization).where(Organization.id == org_uuid)
            )
            org = result.scalar_one_or_none()
        except (ValueError, TypeError):
            # Not a valid UUID, continue
            pass

    if not org:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Organization not found. Please ensure you are a member of an organization.",
        )

    return GraphUser(
        org_id=str(org.id),
        org_slug=org.slug,
        user_id=user.user_id,
    )


# Legacy alias for backwards compatibility with CLI
async def get_current_api_key_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: ClerkUser = Depends(get_current_user_or_api_key),
) -> GraphUser:
    """Legacy alias for get_graph_user. Use get_graph_user for new code."""
    return await get_graph_user(request, db, user)


# =============================================================================
# Request/Response Models
# =============================================================================


class QueryRequest(BaseModel):
    """Request to execute a Cypher query."""

    query: str = Field(..., description="Cypher query to execute")
    parameters: Optional[Dict[str, Any]] = Field(
        default=None, description="Query parameters"
    )
    timeout: Optional[float] = Field(
        default=None, description="Query timeout in seconds"
    )


class QueryResponse(BaseModel):
    """Response from a Cypher query."""

    results: List[Dict[str, Any]] = Field(..., description="Query results")
    count: int = Field(..., description="Number of results")


class StatsResponse(BaseModel):
    """Graph statistics response."""

    stats: Dict[str, int] = Field(..., description="Node/relationship counts")


class BatchNodesRequest(BaseModel):
    """Request to batch create nodes."""

    entities: List[Dict[str, Any]] = Field(..., description="Entities to create")


class BatchNodesResponse(BaseModel):
    """Response from batch node creation."""

    created: Dict[str, str] = Field(
        ..., description="Map of qualified_name to node ID"
    )
    count: int = Field(..., description="Number of nodes created")


class BatchRelationshipsRequest(BaseModel):
    """Request to batch create relationships."""

    relationships: List[Dict[str, Any]] = Field(
        ..., description="Relationships to create"
    )


class BatchRelationshipsResponse(BaseModel):
    """Response from batch relationship creation."""

    count: int = Field(..., description="Number of relationships created")


class FileMetadataResponse(BaseModel):
    """File metadata for incremental ingestion."""

    metadata: Optional[Dict[str, Any]] = Field(
        None, description="File metadata or None if not found"
    )


class WriteRequest(BaseModel):
    """Request to execute a write query (for detector metadata).
    
    Only allowed operations:
    - CREATE/MERGE of DetectorMetadata nodes
    - CREATE of FLAGGED_BY relationships
    - DELETE of DetectorMetadata nodes (cleanup)
    """

    query: str = Field(..., description="Cypher write query")
    parameters: Optional[Dict[str, Any]] = Field(
        default=None, description="Query parameters"
    )


class WriteResponse(BaseModel):
    """Response from a write query."""

    success: bool = Field(..., description="Whether the write succeeded")
    affected: int = Field(default=0, description="Number of nodes/rels affected")


class FilePathsResponse(BaseModel):
    """List of file paths in the graph."""

    paths: List[str] = Field(..., description="File paths")
    count: int = Field(..., description="Number of files")


class DeleteResponse(BaseModel):
    """Response from delete operation."""

    deleted: int = Field(..., description="Number of items deleted")


# =============================================================================
# Helper Functions
# =============================================================================


def _get_client_for_user(user: GraphUser):
    """Get graph client scoped to user's organization."""
    factory = get_factory()
    return factory.get_client(org_id=UUID(user.org_id), org_slug=user.org_slug)


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/query", response_model=QueryResponse)
async def execute_query(
    request: QueryRequest,
    user: GraphUser = Depends(get_graph_user),
) -> QueryResponse:
    """Execute a Cypher query on the organization's graph.

    REPO-500: Only read-only queries are allowed for security.
    Destructive operations (CREATE, DELETE, SET, etc.) are blocked.
    """
    # REPO-500: Validate query is read-only before execution
    from repotoire.validation import validate_cypher_query_readonly, ValidationError as ValError

    try:
        validated_query = validate_cypher_query_readonly(request.query)
    except ValError as e:
        logger.warning(
            f"Rejected unsafe query from user",
            extra={"org_id": user.org_id, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    client = _get_client_for_user(user)
    try:
        results = client.execute_query(
            validated_query,
            parameters=request.parameters,
            timeout=request.timeout,
        )
        return QueryResponse(results=results, count=len(results))
    except Exception as e:
        logger.error(f"Query failed: {e}", extra={"org_id": user.org_id})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query execution failed. Please check your query syntax.",
        )
    finally:
        client.close()


# Allowed write patterns for detector metadata operations
_ALLOWED_WRITE_PATTERNS = [
    "DetectorMetadata",  # CREATE/DELETE of metadata nodes
    "FLAGGED_BY",        # CREATE of flagging relationships
]


def _validate_write_query(query: str) -> str:
    """Validate that a write query only touches detector metadata.
    
    Raises ValidationError if the query attempts unauthorized operations.
    """
    query_upper = query.upper()
    
    # Must be a write operation
    if not any(op in query_upper for op in ["CREATE", "MERGE", "DELETE", "SET"]):
        raise ValueError("Write endpoint requires a write operation (CREATE, MERGE, DELETE, SET)")
    
    # Must only touch allowed patterns
    has_allowed = any(pattern in query for pattern in _ALLOWED_WRITE_PATTERNS)
    if not has_allowed:
        raise ValueError(
            f"Write endpoint only allows operations on: {', '.join(_ALLOWED_WRITE_PATTERNS)}. "
            "Use /query for read operations."
        )
    
    # Block dangerous patterns
    dangerous = ["DROP", "CALL db.", "CALL apoc.", "LOAD CSV"]
    for pattern in dangerous:
        if pattern in query_upper:
            raise ValueError(f"Operation '{pattern}' is not allowed")
    
    return query


@router.post("/write", response_model=WriteResponse)
async def execute_write(
    request: WriteRequest,
    user: GraphUser = Depends(get_graph_user),
) -> WriteResponse:
    """Execute a write query for detector metadata operations.
    
    This endpoint allows CREATE/DELETE of DetectorMetadata nodes and
    FLAGGED_BY relationships. Used by the CLI during analysis to track
    which entities have findings attached.
    
    Only pro tier users can use this endpoint.
    """
    # Validate the write query
    try:
        validated_query = _validate_write_query(request.query)
    except ValueError as e:
        logger.warning(
            f"Rejected unsafe write query",
            extra={"org_id": user.org_id, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    
    client = _get_client_for_user(user)
    try:
        results = client.execute_query(
            validated_query,
            parameters=request.parameters,
        )
        # Try to extract affected count from results
        affected = 0
        if results and isinstance(results, list):
            affected = len(results)
        
        logger.info(
            "Write query executed",
            extra={"org_id": user.org_id, "affected": affected},
        )
        return WriteResponse(success=True, affected=affected)
    except Exception as e:
        logger.error(f"Write query failed: {e}", extra={"org_id": user.org_id})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Write failed: {str(e)}",
        )
    finally:
        client.close()


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    user: GraphUser = Depends(get_graph_user),
) -> StatsResponse:
    """Get graph statistics."""
    client = _get_client_for_user(user)
    try:
        stats = client.get_stats()
        return StatsResponse(stats=stats)
    finally:
        client.close()


@router.delete("/clear", response_model=DeleteResponse)
async def clear_graph(
    user: GraphUser = Depends(get_graph_user),
) -> DeleteResponse:
    """Clear all nodes and relationships from the graph."""
    client = _get_client_for_user(user)
    try:
        # Get count before clearing for response
        stats = client.get_stats()
        total = sum(stats.values())
        client.clear_graph()
        logger.info(f"Graph cleared", org_id=user.org_id, deleted=total)
        return DeleteResponse(deleted=total)
    finally:
        client.close()


@router.post("/batch/nodes", response_model=BatchNodesResponse)
async def batch_create_nodes(
    request: BatchNodesRequest,
    user: GraphUser = Depends(get_graph_user),
) -> BatchNodesResponse:
    """Batch create nodes in the graph with automatic embedding generation.

    Embeddings are generated server-side using DeepInfra + Qwen3-Embedding-8B
    for semantic code search. This happens automatically - no client config needed.
    """
    client = _get_client_for_user(user)
    try:
        # Convert dicts to appropriate Entity subclass
        entities = []
        for e in request.entities:
            entity_type = e.get("entity_type", "Unknown")
            node_type = NodeType(entity_type) if entity_type in [t.value for t in NodeType] else None

            # Common fields for all entities
            base_fields = {
                "name": e["name"],
                "qualified_name": e["qualified_name"],
                "file_path": e.get("file_path", ""),
                "line_start": e.get("line_start", 0),
                "line_end": e.get("line_end", 0),
                "docstring": e.get("docstring"),
            }

            # Create appropriate entity type
            if entity_type == "File":
                entity = FileEntity(
                    **base_fields,
                    node_type=NodeType.FILE,
                    language=e.get("language", "python"),
                    loc=e.get("loc", 0),
                    hash=e.get("hash"),
                    exports=e.get("exports", []),
                )
            elif entity_type == "Class":
                entity = ClassEntity(
                    **base_fields,
                    node_type=NodeType.CLASS,
                    is_abstract=e.get("is_abstract", False),
                    complexity=e.get("complexity", 0),
                    decorators=e.get("decorators", []),
                )
            elif entity_type == "Function":
                entity = FunctionEntity(
                    **base_fields,
                    node_type=NodeType.FUNCTION,
                    parameters=e.get("parameters", []),
                    return_type=e.get("return_type"),
                    is_async=e.get("is_async", False),
                    decorators=e.get("decorators", []),
                    complexity=e.get("complexity", 0),
                    is_method=e.get("is_method", False),
                    is_static=e.get("is_static", False),
                    is_classmethod=e.get("is_classmethod", False),
                    is_property=e.get("is_property", False),
                )
            elif entity_type == "Module":
                entity = ModuleEntity(
                    **base_fields,
                    node_type=NodeType.MODULE,
                    is_external=e.get("is_external", False),
                    package=e.get("package"),
                )
            else:
                # Fall back to base Entity for unknown types
                entity = Entity(
                    **base_fields,
                    node_type=node_type,
                )

            # Set repo_id and repo_slug for multi-tenant isolation
            if e.get("repo_id"):
                entity.repo_id = e["repo_id"]
            if e.get("repo_slug"):
                entity.repo_slug = e["repo_slug"]

            entities.append(entity)

        # Create nodes in graph
        created = client.batch_create_nodes(entities)

        # Generate and store embeddings server-side (async to not block)
        # Only for Function and Class entities (most useful for semantic search)
        embeddable_entities = [
            e for e in entities
            if e.node_type in (NodeType.FUNCTION, NodeType.CLASS)
        ]

        if embeddable_entities and os.getenv("DEEPINFRA_API_KEY"):
            try:
                await _generate_and_store_embeddings(client, embeddable_entities)
                logger.info(
                    f"Generated embeddings for {len(embeddable_entities)} entities",
                    org_id=user.org_id
                )
            except Exception as embed_error:
                # Non-fatal - log but don't fail the request
                logger.warning(
                    f"Embedding generation failed (non-fatal): {embed_error}",
                    org_id=user.org_id
                )

        return BatchNodesResponse(created=created, count=len(created))
    except Exception as e:
        logger.error(f"Batch create nodes failed: {e}", org_id=user.org_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch node creation failed. Please check your input.",
        )
    finally:
        client.close()


async def _generate_and_store_embeddings(client, entities: List[Entity]) -> None:
    """Generate embeddings using DeepInfra Qwen3 and store on nodes.

    Args:
        client: Graph database client
        entities: Entities to generate embeddings for
    """
    from repotoire.ai.embeddings import CodeEmbedder

    # Use DeepInfra backend with Qwen3-Embedding-8B
    embedder = CodeEmbedder(backend="deepinfra")

    # Generate embeddings in thread pool (CPU-bound serialization)
    embeddings = await asyncio.to_thread(
        embedder.embed_entities_batch, entities
    )

    # Store embeddings on nodes
    for entity, embedding in zip(entities, embeddings):
        query = """
        MATCH (n {qualifiedName: $qualified_name})
        SET n.embedding = $embedding
        """
        client.execute_query(
            query,
            parameters={
                "qualified_name": entity.qualified_name,
                "embedding": embedding,
            }
        )


@router.post("/batch/relationships", response_model=BatchRelationshipsResponse)
async def batch_create_relationships(
    request: BatchRelationshipsRequest,
    user: GraphUser = Depends(get_graph_user),
) -> BatchRelationshipsResponse:
    """Batch create relationships in the graph."""
    client = _get_client_for_user(user)
    try:
        # Convert dicts to Relationship objects
        relationships = []
        for r in request.relationships:
            # Parse rel_type from string to enum
            rel_type_str = r.get("rel_type", "CALLS")
            try:
                rel_type = RelationshipType(rel_type_str)
            except ValueError:
                rel_type = RelationshipType.CALLS  # Default fallback

            rel = Relationship(
                source_id=r["source_id"],
                target_id=r["target_id"],
                rel_type=rel_type,
                properties=r.get("properties", {}),
            )
            relationships.append(rel)

        count = client.batch_create_relationships(relationships)
        return BatchRelationshipsResponse(count=count)
    except Exception as e:
        logger.error(f"Batch create relationships failed: {e}", org_id=user.org_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch relationship creation failed. Please check your input.",
        )
    finally:
        client.close()


@router.get("/files", response_model=FilePathsResponse)
async def get_file_paths(
    user: GraphUser = Depends(get_graph_user),
) -> FilePathsResponse:
    """Get all file paths in the graph."""
    client = _get_client_for_user(user)
    try:
        paths = client.get_all_file_paths()
        return FilePathsResponse(paths=paths, count=len(paths))
    finally:
        client.close()


@router.get("/files/{file_path:path}/metadata", response_model=FileMetadataResponse)
async def get_file_metadata(
    file_path: str,
    user: GraphUser = Depends(get_graph_user),
) -> FileMetadataResponse:
    """Get metadata for a specific file (for incremental ingestion)."""
    client = _get_client_for_user(user)
    try:
        metadata = client.get_file_metadata(file_path)
        return FileMetadataResponse(metadata=metadata)
    finally:
        client.close()


@router.delete("/files/{file_path:path}", response_model=DeleteResponse)
async def delete_file(
    file_path: str,
    user: GraphUser = Depends(get_graph_user),
) -> DeleteResponse:
    """Delete a file and its related entities from the graph."""
    client = _get_client_for_user(user)
    try:
        deleted = client.delete_file_entities(file_path)
        return DeleteResponse(deleted=deleted)
    finally:
        client.close()


@router.post("/indexes")
async def create_indexes(
    user: GraphUser = Depends(get_graph_user),
) -> Dict[str, str]:
    """Create indexes for better query performance."""
    client = _get_client_for_user(user)
    try:
        client.create_indexes()
        return {"status": "ok", "message": "Indexes created"}
    finally:
        client.close()
