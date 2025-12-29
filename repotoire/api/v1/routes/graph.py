"""Graph proxy API routes for CLI operations.

This module provides API endpoints that proxy graph database operations
to the internal FalkorDB instance. This allows the CLI to perform graph
operations without direct database access.

All operations are authenticated via API key and scoped to the user's
organization graph.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth.clerk import get_clerk_client
from repotoire.db.models import Organization, OrganizationMembership, User
from repotoire.db.session import get_db
from repotoire.graph.tenant_factory import get_factory
from repotoire.logging_config import get_logger
from repotoire.models import Entity, Relationship

logger = get_logger(__name__)

router = APIRouter(prefix="/graph", tags=["graph"])


# =============================================================================
# API Key Authentication
# =============================================================================


@dataclass
class APIKeyUser:
    """Authenticated user from API key."""

    org_id: str  # Our internal org UUID
    org_slug: str
    user_id: Optional[str] = None


async def get_current_api_key_user(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> APIKeyUser:
    """Validate API key and return authenticated user with org info."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization format. Use: Bearer <api_key>",
        )

    api_key = parts[1]

    try:
        clerk = get_clerk_client()
        api_key_data = await asyncio.to_thread(
            clerk.api_keys.verify_api_key, secret=api_key
        )

        subject = api_key_data.subject
        clerk_org_id = None
        org = None

        if subject.startswith("org_"):
            clerk_org_id = subject
        elif hasattr(api_key_data, "org_id") and api_key_data.org_id:
            clerk_org_id = api_key_data.org_id
        elif subject.startswith("user_"):
            # User-scoped key - look up user's organization
            result = await db.execute(
                select(User).where(User.clerk_user_id == subject)
            )
            db_user = result.scalar_one_or_none()
            if not db_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found",
                )

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
                if org:
                    clerk_org_id = org.clerk_org_id

        # Look up org by Clerk ID
        if not org and clerk_org_id:
            result = await db.execute(
                select(Organization).where(Organization.clerk_org_id == clerk_org_id)
            )
            org = result.scalar_one_or_none()

        if not org:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Organization not found",
            )

        return APIKeyUser(
            org_id=str(org.id),
            org_slug=org.slug,
            user_id=subject if subject.startswith("user_") else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API key validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key",
        )


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


def _get_client_for_user(user: APIKeyUser):
    """Get graph client scoped to user's organization."""
    factory = get_factory()
    return factory.get_client(org_id=UUID(user.org_id), org_slug=user.org_slug)


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/query", response_model=QueryResponse)
async def execute_query(
    request: QueryRequest,
    user: APIKeyUser = Depends(get_current_api_key_user),
) -> QueryResponse:
    """Execute a Cypher query on the organization's graph."""
    client = _get_client_for_user(user)
    try:
        results = client.execute_query(
            request.query,
            parameters=request.parameters,
            timeout=request.timeout,
        )
        return QueryResponse(results=results, count=len(results))
    except Exception as e:
        logger.error(f"Query failed: {e}", org_id=user.org_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Query failed: {str(e)}",
        )
    finally:
        client.close()


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    user: APIKeyUser = Depends(get_current_api_key_user),
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
    user: APIKeyUser = Depends(get_current_api_key_user),
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
    user: APIKeyUser = Depends(get_current_api_key_user),
) -> BatchNodesResponse:
    """Batch create nodes in the graph."""
    client = _get_client_for_user(user)
    try:
        # Convert dicts to Entity objects
        entities = []
        for e in request.entities:
            entity = Entity(
                entity_type=e["entity_type"],
                name=e["name"],
                qualified_name=e["qualified_name"],
                file_path=e.get("file_path"),
                line_number=e.get("line_number"),
                end_line_number=e.get("end_line_number"),
                properties=e.get("properties", {}),
            )
            entities.append(entity)

        created = client.batch_create_nodes(entities)
        return BatchNodesResponse(created=created, count=len(created))
    except Exception as e:
        logger.error(f"Batch create nodes failed: {e}", org_id=user.org_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Batch create failed: {str(e)}",
        )
    finally:
        client.close()


@router.post("/batch/relationships", response_model=BatchRelationshipsResponse)
async def batch_create_relationships(
    request: BatchRelationshipsRequest,
    user: APIKeyUser = Depends(get_current_api_key_user),
) -> BatchRelationshipsResponse:
    """Batch create relationships in the graph."""
    client = _get_client_for_user(user)
    try:
        # Convert dicts to Relationship objects
        relationships = []
        for r in request.relationships:
            rel = Relationship(
                source=r["source"],
                target=r["target"],
                relationship_type=r["relationship_type"],
                properties=r.get("properties", {}),
            )
            relationships.append(rel)

        count = client.batch_create_relationships(relationships)
        return BatchRelationshipsResponse(count=count)
    except Exception as e:
        logger.error(f"Batch create relationships failed: {e}", org_id=user.org_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Batch create failed: {str(e)}",
        )
    finally:
        client.close()


@router.get("/files", response_model=FilePathsResponse)
async def get_file_paths(
    user: APIKeyUser = Depends(get_current_api_key_user),
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
    user: APIKeyUser = Depends(get_current_api_key_user),
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
    user: APIKeyUser = Depends(get_current_api_key_user),
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
    user: APIKeyUser = Depends(get_current_api_key_user),
) -> Dict[str, str]:
    """Create indexes for better query performance."""
    client = _get_client_for_user(user)
    try:
        client.create_indexes()
        return {"status": "ok", "message": "Indexes created"}
    finally:
        client.close()
