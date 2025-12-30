"""API routes for git history and temporal knowledge graph queries.

Uses FalkorDB as the graph database backend for Graphiti temporal knowledge graph.
Accepts commit data directly from CLI (cloud-only architecture).
"""

from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/historical",
    tags=["Historical Analysis"],
)


# Request/Response Models
class CommitData(BaseModel):
    """Git commit data extracted from client repository."""

    sha: str = Field(..., description="Full commit SHA")
    author_name: str = Field(..., description="Author name")
    author_email: str = Field(..., description="Author email")
    committed_date: datetime = Field(..., description="Commit timestamp")
    message: str = Field(..., description="Full commit message")
    changed_files: List[str] = Field(default_factory=list, description="List of changed file paths")
    insertions: int = Field(default=0, description="Lines inserted")
    deletions: int = Field(default=0, description="Lines deleted")
    code_changes: List[str] = Field(default_factory=list, description="Function/class changes detected")


class IngestCommitsRequest(BaseModel):
    """Request to ingest pre-extracted commit data (cloud-only architecture)."""

    repo_id: str = Field(..., description="Repository UUID for multi-tenant isolation")
    repo_slug: str = Field(..., description="Repository slug (e.g., 'owner/repo')")
    commits: List[CommitData] = Field(..., description="List of commits to ingest")
    branch: str = Field(default="main", description="Branch these commits are from")


class IngestGitRequest(BaseModel):
    """Request to ingest git commit history (legacy - for server-accessible repos)."""

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


def _get_graphiti_instance():
    """Get a Graphiti instance configured for Neo4j/FalkorDB.

    Returns:
        Initialized Graphiti instance

    Raises:
        HTTPException: If dependencies not available or not configured
    """
    import os

    try:
        from graphiti_core import Graphiti
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Graphiti not installed. Install with: pip install graphiti-core[falkordb]"
        )

    # Check for OpenAI API key (required for LLM processing)
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=400,
            detail="OPENAI_API_KEY environment variable not set"
        )

    # Get database connection - use REPOTOIRE_FALKOR_URI if set, else REPOTOIRE_NEO4J_URI
    # Both FalkorDB and Neo4j use bolt/neo4j protocol
    db_uri = os.getenv("REPOTOIRE_FALKOR_URI") or os.getenv("REPOTOIRE_NEO4J_URI")
    db_password = os.getenv("REPOTOIRE_FALKOR_PASSWORD") or os.getenv("REPOTOIRE_NEO4J_PASSWORD")

    if not db_uri:
        raise HTTPException(
            status_code=500,
            detail="No graph database URI configured (REPOTOIRE_FALKOR_URI or REPOTOIRE_NEO4J_URI)"
        )

    # Initialize Graphiti with Neo4j-compatible backend
    return Graphiti(db_uri, db_password)


@router.post("/ingest-commits", response_model=IngestGitResponse)
async def ingest_commits(request: IngestCommitsRequest, user: ClerkUser = Depends(get_current_user)):
    """Ingest pre-extracted commit data into Graphiti (cloud-only architecture).

    This endpoint accepts commit data extracted by the CLI from the user's local
    git repository. The CLI extracts commit metadata and sends it here for
    server-side Graphiti processing with FalkorDB storage.

    This is the preferred endpoint for cloud-only operation where the CLI
    doesn't have direct database access.

    Returns statistics about the ingestion process including:
    - Number of commits processed
    - Date range of commits
    - Any errors encountered
    """
    import time

    start_time = time.time()

    try:
        from graphiti_core.nodes import EpisodeType

        graphiti = _get_graphiti_instance()

        stats = {
            "commits_processed": 0,
            "commits_skipped": 0,
            "errors": 0,
            "oldest_commit": None,
            "newest_commit": None,
        }

        for commit in request.commits:
            try:
                # Format commit data as episode text
                episode_body = _format_commit_data(commit)

                await graphiti.add_episode(
                    name=commit.message.split("\n")[0][:80],  # First line, max 80 chars
                    episode_body=episode_body,
                    source_description=f"Git commit {commit.sha[:8]} from {request.repo_slug}",
                    reference_time=commit.committed_date,
                    source=EpisodeType.text,
                )

                stats["commits_processed"] += 1

                # Track date range
                if stats["oldest_commit"] is None or commit.committed_date < stats["oldest_commit"]:
                    stats["oldest_commit"] = commit.committed_date
                if stats["newest_commit"] is None or commit.committed_date > stats["newest_commit"]:
                    stats["newest_commit"] = commit.committed_date

            except Exception as e:
                logger.error(f"Error processing commit {commit.sha[:8]}: {e}")
                stats["errors"] += 1

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
        logger.error(f"Failed to ingest commits: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to ingest commits: {str(e)}"
        )


def _format_commit_data(commit: CommitData) -> str:
    """Format commit data as episode text for Graphiti processing."""
    message_lines = commit.message.strip().split("\n")
    summary = message_lines[0]
    body = "\n".join(message_lines[1:]).strip() if len(message_lines) > 1 else ""

    episode_parts = [
        f"Commit: {commit.sha}",
        f"Author: {commit.author_name} <{commit.author_email}>",
        f"Date: {commit.committed_date.isoformat()}",
        "",
        f"Summary: {summary}",
    ]

    if body:
        episode_parts.append(f"\nDescription:\n{body}")

    episode_parts.extend([
        "",
        f"Files Changed ({len(commit.changed_files)}):",
        *[f"  - {f}" for f in commit.changed_files[:20]],
    ])

    if len(commit.changed_files) > 20:
        episode_parts.append(f"  ... and {len(commit.changed_files) - 20} more files")

    if commit.code_changes:
        episode_parts.extend([
            "",
            "Code Changes:",
            *[f"  - {change}" for change in commit.code_changes[:10]],
        ])

    episode_parts.extend([
        "",
        "Statistics:",
        f"  +{commit.insertions} insertions",
        f"  -{commit.deletions} deletions",
        f"  {len(commit.changed_files)} files changed",
    ])

    return "\n".join(episode_parts)


@router.post("/ingest-git", response_model=IngestGitResponse, deprecated=True)
async def ingest_git_history(request: IngestGitRequest, user: ClerkUser = Depends(get_current_user)):
    """[DEPRECATED] Ingest git history from server-accessible repository.

    This endpoint is deprecated for cloud-only architecture. Use /ingest-commits
    instead, which accepts pre-extracted commit data from the CLI.

    This endpoint remains for backwards compatibility with server-side repositories.
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
                detail="Graphiti not installed. Install with: pip install graphiti-core[falkordb]"
            )

        # Check for OpenAI API key
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(
                status_code=400,
                detail="OPENAI_API_KEY environment variable not set"
            )

        # Get FalkorDB credentials
        falkor_uri = os.getenv("REPOTOIRE_FALKOR_URI", "falkor://localhost:6379")
        falkor_password = os.getenv("REPOTOIRE_FALKOR_PASSWORD")

        # Initialize Graphiti with FalkorDB
        graphiti = Graphiti(falkor_uri, falkor_password)

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

    start_time = time.time()

    try:
        graphiti = _get_graphiti_instance()

        # Query using Graphiti search
        results = await graphiti.search(query=request.query)

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

    start_time = time.time()

    try:
        graphiti = _get_graphiti_instance()

        # Search for episodes mentioning this entity
        timeline = await graphiti.search(
            query=f"Show all changes to {request.entity_type} {request.entity_name}"
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
    Uses FalkorDB as the graph database backend.
    """
    import os

    status = {
        "status": "healthy",
        "graphiti_available": False,
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "falkordb_configured": bool(os.getenv("REPOTOIRE_FALKOR_URI")),
    }

    try:
        from graphiti_core import Graphiti
        status["graphiti_available"] = True
    except ImportError:
        pass

    # Determine overall status
    if not status["graphiti_available"]:
        status["status"] = "degraded"
        status["message"] = "Graphiti not installed. Install with: pip install graphiti-core[falkordb]"
    elif not status["openai_configured"]:
        status["status"] = "degraded"
        status["message"] = "OPENAI_API_KEY not configured"
    elif not status["falkordb_configured"]:
        status["status"] = "degraded"
        status["message"] = "REPOTOIRE_FALKOR_URI not configured"
    else:
        status["message"] = "All dependencies available"

    return status
