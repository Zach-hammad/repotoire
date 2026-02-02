"""API routes for code architecture and analysis."""

import asyncio
import os
import time
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from repotoire.api.models import (
    ArchitectureResponse,
    ErrorResponse,
    ModuleStats,
)
from repotoire.api.shared.auth import ClerkUser, get_current_user_or_api_key
from repotoire.api.shared.middleware.usage import enforce_feature_for_api
from repotoire.db.models import Organization
from repotoire.graph.base import DatabaseClient
from repotoire.graph.tenant_factory import get_factory
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Rate limiter for code AI endpoints (expensive operations)
code_ai_limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("REDIS_URL", "memory://"),
)

router = APIRouter(prefix="/code", tags=["code"])


def get_graph_client_for_org(org: Organization) -> DatabaseClient:
    """Get tenant-isolated graph client for the organization.

    Uses the tenant factory to connect to the correct FalkorDB instance
    with proper multi-tenant isolation.
    """
    factory = get_factory()
    return factory.get_client(org_id=org.id, org_slug=org.slug)


@router.get(
    "/architecture",
    response_model=ArchitectureResponse,
    summary="Get codebase architecture",
    description="Get an overview of the codebase architecture including modules, dependencies, and patterns. Requires Pro or Enterprise subscription.",
    responses={
        200: {"description": "Architecture overview retrieved successfully"},
        403: {"model": ErrorResponse, "description": "Feature not available on current plan"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
async def get_architecture(
    depth: int = 2,
    org: Organization = Depends(enforce_feature_for_api("api_access")),
) -> ArchitectureResponse:
    """
    Get codebase architecture overview.

    Returns module statistics, detected patterns, and dependencies.
    The depth parameter controls how deep into the directory structure to analyze.
    """
    # Get org-isolated graph client
    client = get_graph_client_for_org(org)

    try:
        logger.info("Fetching architecture overview", extra={"org_id": str(org.id), "depth": depth})

        # Query module/directory statistics from the graph
        # Group by directory path at the specified depth
        module_query = """
        MATCH (f:File)
        WITH f,
             split(f.path, '/') as parts
        WITH f,
             CASE WHEN size(parts) > $depth THEN
                 reduce(s='', i IN range(0, $depth - 1) | s + '/' + parts[i])
             ELSE
                 '/' + reduce(s='', p IN parts[0..-1] | s + '/' + p)
             END as module_path
        WITH module_path,
             count(f) as file_count,
             sum(CASE WHEN f.function_count IS NOT NULL THEN f.function_count ELSE 0 END) as total_functions,
             sum(CASE WHEN f.class_count IS NOT NULL THEN f.class_count ELSE 0 END) as total_classes
        WHERE module_path <> ''
        RETURN module_path, file_count, total_functions, total_classes
        ORDER BY file_count DESC
        LIMIT 50
        """

        module_results = client.execute_query(module_query, {"depth": depth})

        # Build modules dict
        modules: dict[str, ModuleStats] = {}
        for row in module_results:
            path = row.get("module_path", "unknown").lstrip("/")
            if path:
                modules[path] = ModuleStats(
                    file_count=row.get("file_count", 0),
                    functions=row.get("total_functions", 0),
                    classes=row.get("total_classes", 0),
                )

        # Query top-level dependencies (IMPORTS relationships)
        dep_query = """
        MATCH (f:File)-[:IMPORTS]->(m:Module)
        WHERE NOT m.name STARTS WITH '.'
        RETURN DISTINCT m.name as dependency
        ORDER BY m.name
        LIMIT 30
        """

        dep_results = client.execute_query(dep_query)
        dependencies = [row["dependency"] for row in dep_results if row.get("dependency")]

        # Detect patterns based on graph structure
        patterns: list[str] = []

        # Check for common patterns
        pattern_checks = [
            ("MATCH (c:Class)-[:INHERITS]->(:Class {name: 'BaseModel'}) RETURN count(c) as cnt",
             "Pydantic Models", 3),
            ("MATCH (f:File) WHERE f.path CONTAINS '/routes/' OR f.path CONTAINS '/api/' RETURN count(f) as cnt",
             "REST API", 5),
            ("MATCH (c:Class) WHERE c.name CONTAINS 'Repository' OR c.name CONTAINS 'DAO' RETURN count(c) as cnt",
             "Repository Pattern", 2),
            ("MATCH (f:File) WHERE f.path CONTAINS '/tests/' RETURN count(f) as cnt",
             "Test Suite", 5),
        ]

        for query, pattern_name, threshold in pattern_checks:
            try:
                result = client.execute_query(query)
                if result and result[0].get("cnt", 0) >= threshold:
                    patterns.append(pattern_name)
            except Exception:
                pass  # Skip pattern detection on query errors

        return ArchitectureResponse(
            modules=modules,
            patterns=patterns if patterns else None,
            dependencies=dependencies if dependencies else None,
        )

    except Exception as e:
        logger.error(f"Architecture retrieval error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve architecture overview."
        )
    finally:
        client.close()
