"""API routes for git history and temporal knowledge graph queries."""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from repotoire.api.auth import ClerkUser, get_current_user
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/historical",
    tags=["Historical Analysis"],
)


# Request/Response Models
class IngestGitRequest(BaseModel):
    """Request to ingest git commit history."""

    repository_path: str = Field(..., description="Path to git repository")
    since: Optional[datetime] = Field(None, description="Only ingest commits after this date")
    until: Optional[datetime] = Field(None, description="Only ingest commits before this date")
    branch: str = Field(default="main", description="Git branch to analyze")
    max_commits: int = Field(default=1000, description="Maximum commits to process")
    batch_size: int = Field(default=10, description="Commits to process in parallel")


class IngestGitResponse(BaseModel):
    """Response from git history ingestion."""

    status: str
    commits_processed: int
    commits_skipped: int
    errors: int
    oldest_commit: Optional[datetime] = None
    newest_commit: Optional[datetime] = None
    message: str


class QueryHistoryRequest(BaseModel):
    """Request to query git history using natural language."""

    query: str = Field(..., description="Natural language question about code history")
    repository_path: str = Field(..., description="Path to git repository")
    start_time: Optional[datetime] = Field(None, description="Filter episodes after this time")
    end_time: Optional[datetime] = Field(None, description="Filter episodes before this time")


class QueryHistoryResponse(BaseModel):
    """Response from git history query."""

    query: str
    results: str
    execution_time_ms: float


class TimelineRequest(BaseModel):
    """Request for entity timeline."""

    entity_name: str = Field(..., description="Name of the function/class/module")
    entity_type: str = Field(default="function", description="Type of entity (function, class, module)")
    repository_path: str = Field(..., description="Path to git repository")


class TimelineResponse(BaseModel):
    """Response with entity timeline."""

    entity_name: str
    entity_type: str
    timeline: str
    execution_time_ms: float


@router.post("/ingest-git", response_model=IngestGitResponse)
async def ingest_git_history(request: IngestGitRequest, user: ClerkUser = Depends(get_current_user)):
    """Ingest git commit history into Graphiti temporal knowledge graph.

    Analyzes git repository and creates Graphiti episodes for each commit,
    enabling natural language queries about code evolution over time.

    Requires:
    - Graphiti installed (`pip install graphiti-core`)
    - OPENAI_API_KEY environment variable set
    - Neo4j connection configured

    Returns statistics about the ingestion process including:
    - Number of commits processed
    - Date range of commits
    - Any errors encountered
    """
    import time
    import os

    start_time = time.time()

    try:
        # Check for Graphiti
        try:
            from graphiti_core import Graphiti
            from repotoire.historical import GitGraphitiIntegration
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="Graphiti not installed. Install with: pip install graphiti-core"
            )

        # Check for OpenAI API key
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(
                status_code=400,
                detail="OPENAI_API_KEY environment variable not set"
            )

        # Get Neo4j credentials
        neo4j_uri = os.getenv("REPOTOIRE_NEO4J_URI", "bolt://localhost:7687")
        neo4j_password = os.getenv("REPOTOIRE_NEO4J_PASSWORD")

        if not neo4j_password:
            raise HTTPException(
                status_code=400,
                detail="REPOTOIRE_NEO4J_PASSWORD environment variable not set"
            )

        # Initialize Graphiti
        graphiti = Graphiti(neo4j_uri, neo4j_password, "neo4j")

        # Initialize integration
        integration = GitGraphitiIntegration(request.repository_path, graphiti)

        # Ingest git history
        stats = await integration.ingest_git_history(
            since=request.since,
            until=request.until,
            branch=request.branch,
            max_commits=request.max_commits,
            batch_size=request.batch_size,
        )

        execution_time = (time.time() - start_time) * 1000

        return IngestGitResponse(
            status="success",
            commits_processed=stats["commits_processed"],
            commits_skipped=stats["commits_skipped"],
            errors=stats["errors"],
            oldest_commit=stats.get("oldest_commit"),
            newest_commit=stats.get("newest_commit"),
            message=f"Successfully ingested {stats['commits_processed']} commits in {execution_time:.0f}ms"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to ingest git history: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to ingest git history: {str(e)}"
        )


@router.post("/query", response_model=QueryHistoryResponse)
async def query_history(request: QueryHistoryRequest, user: ClerkUser = Depends(get_current_user)):
    """Query git history using natural language.

    Ask questions about code evolution, when features were added, who made changes,
    and other historical questions about the codebase.

    Examples:
    - "When did we add OAuth authentication?"
    - "What changes led to the performance regression?"
    - "Show all refactorings of the UserManager class"
    - "Which developer changed this function most?"

    Returns:
    Natural language response from Graphiti with relevant commit information.
    """
    import time
    import os

    start_time = time.time()

    try:
        # Check for Graphiti
        try:
            from graphiti_core import Graphiti
            from repotoire.historical import GitGraphitiIntegration
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="Graphiti not installed. Install with: pip install graphiti-core"
            )

        # Get Neo4j credentials
        neo4j_uri = os.getenv("REPOTOIRE_NEO4J_URI", "bolt://localhost:7687")
        neo4j_password = os.getenv("REPOTOIRE_NEO4J_PASSWORD")

        if not neo4j_password:
            raise HTTPException(
                status_code=400,
                detail="REPOTOIRE_NEO4J_PASSWORD environment variable not set"
            )

        # Initialize Graphiti
        graphiti = Graphiti(neo4j_uri, neo4j_password, "neo4j")

        # Initialize integration
        integration = GitGraphitiIntegration(request.repository_path, graphiti)

        # Query history
        results = await integration.query_history(
            query=request.query,
            start_time=request.start_time,
            end_time=request.end_time,
        )

        execution_time = (time.time() - start_time) * 1000

        return QueryHistoryResponse(
            query=request.query,
            results=str(results),
            execution_time_ms=execution_time
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to query git history: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to query git history: {str(e)}"
        )


@router.post("/timeline", response_model=TimelineResponse)
async def get_entity_timeline(request: TimelineRequest, user: ClerkUser = Depends(get_current_user)):
    """Get timeline of changes for a specific code entity.

    Shows all commits that modified a particular function, class, or module
    over time, helping understand how that code evolved.

    Returns:
    List of commits that modified the specified entity, with dates and descriptions.
    """
    import time
    import os

    start_time = time.time()

    try:
        # Check for Graphiti
        try:
            from graphiti_core import Graphiti
            from repotoire.historical import GitGraphitiIntegration
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="Graphiti not installed. Install with: pip install graphiti-core"
            )

        # Get Neo4j credentials
        neo4j_uri = os.getenv("REPOTOIRE_NEO4J_URI", "bolt://localhost:7687")
        neo4j_password = os.getenv("REPOTOIRE_NEO4J_PASSWORD")

        if not neo4j_password:
            raise HTTPException(
                status_code=400,
                detail="REPOTOIRE_NEO4J_PASSWORD environment variable not set"
            )

        # Initialize Graphiti
        graphiti = Graphiti(neo4j_uri, neo4j_password, "neo4j")

        # Initialize integration
        integration = GitGraphitiIntegration(request.repository_path, graphiti)

        # Get timeline
        timeline = await integration.get_entity_timeline(
            entity_name=request.entity_name,
            entity_type=request.entity_type,
        )

        execution_time = (time.time() - start_time) * 1000

        return TimelineResponse(
            entity_name=request.entity_name,
            entity_type=request.entity_type,
            timeline=str(timeline),
            execution_time_ms=execution_time
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get entity timeline: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get entity timeline: {str(e)}"
        )


@router.get("/health", tags=["Health"])
async def historical_health_check():
    """Health check for historical analysis endpoints.

    Checks if Graphiti and required dependencies are available.
    """
    import os

    status = {
        "status": "healthy",
        "graphiti_available": False,
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "neo4j_configured": bool(os.getenv("REPOTOIRE_NEO4J_PASSWORD")),
    }

    try:
        from graphiti_core import Graphiti
        status["graphiti_available"] = True
    except ImportError:
        pass

    # Determine overall status
    if not status["graphiti_available"]:
        status["status"] = "degraded"
        status["message"] = "Graphiti not installed"
    elif not status["openai_configured"]:
        status["status"] = "degraded"
        status["message"] = "OPENAI_API_KEY not configured"
    elif not status["neo4j_configured"]:
        status["status"] = "degraded"
        status["message"] = "REPOTOIRE_NEO4J_PASSWORD not configured"
    else:
        status["message"] = "All dependencies available"

    return status
