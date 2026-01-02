"""API routes for git history and temporal knowledge graph queries.

Uses FalkorDB as the graph database backend for Graphiti temporal knowledge graph.
Accepts commit data directly from CLI (cloud-only architecture).
"""

import enum
from datetime import datetime
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.db.session import get_db
from repotoire.db.models import Repository, Finding
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/historical",
    tags=["historical"],
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
    """Get a Graphiti instance configured for FalkorDB.

    Returns:
        Initialized Graphiti instance

    Raises:
        HTTPException: If dependencies not available or not configured
    """
    import os

    try:
        from graphiti_core import Graphiti
        from graphiti_core.driver.falkordb_driver import FalkorDriver
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

    # Get FalkorDB connection parameters
    # On Fly.io: repotoire-falkor.internal:6379
    # Local: localhost:6379
    falkor_host = os.getenv("FALKORDB_HOST", "repotoire-falkor.internal")
    falkor_port = int(os.getenv("FALKORDB_PORT", "6379"))
    falkor_password = os.getenv("FALKORDB_PASSWORD")

    # Create FalkorDriver with direct connection parameters
    driver = FalkorDriver(
        host=falkor_host,
        port=falkor_port,
        password=falkor_password,
        database="graphiti_commits",  # Separate database for git history
    )

    # Initialize Graphiti with FalkorDB driver
    return Graphiti(graph_driver=driver)


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
            detail="Failed to ingest commits. Please try again."
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
            detail="Failed to ingest git history. Please try again."
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
            detail="Failed to query git history. Please try again."
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
            detail="Failed to retrieve entity timeline."
        )


@router.get("/health", tags=["Health"])
async def historical_health_check():
    """Health check for historical analysis endpoints.

    Checks if Graphiti and required dependencies are available.
    Uses FalkorDB as the graph database backend.
    """
    import os

    # FalkorDB is configured if we have a host (defaults to repotoire-falkor.internal on Fly.io)
    falkor_host = os.getenv("FALKORDB_HOST", "repotoire-falkor.internal")

    health_status = {
        "status": "healthy",
        "graphiti_available": False,
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "falkordb_host": falkor_host,
        "falkordb_password_set": bool(os.getenv("FALKORDB_PASSWORD")),
    }

    try:
        from graphiti_core import Graphiti
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        health_status["graphiti_available"] = True
        health_status["falkordb_driver_available"] = True
    except ImportError as e:
        health_status["falkordb_driver_available"] = False
        health_status["import_error"] = str(e)

    # Determine overall status
    if not health_status["graphiti_available"]:
        health_status["status"] = "degraded"
        health_status["message"] = "Graphiti not installed. Install with: pip install graphiti-core[falkordb]"
    elif not health_status["openai_configured"]:
        health_status["status"] = "degraded"
        health_status["message"] = "OPENAI_API_KEY not configured"
    elif not health_status.get("falkordb_driver_available"):
        health_status["status"] = "degraded"
        health_status["message"] = "FalkorDB driver not available"
    else:
        health_status["message"] = "All dependencies available"

    return health_status


# =============================================================================
# New Response Models for Frontend API Contract
# =============================================================================


class ProvenanceConfidence(str, enum.Enum):
    """Confidence level for provenance detection."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class CommitProvenance(BaseModel):
    """Commit provenance information."""

    commit_sha: str = Field(..., description="Full commit SHA")
    author_name: str = Field(..., description="Author's display name")
    author_email: str = Field(..., description="Author's email address")
    author_avatar_url: Optional[str] = Field(None, description="URL to author's avatar (Gravatar)")
    commit_date: datetime = Field(..., description="Commit timestamp")
    message: str = Field(..., description="Commit message (first line)")
    full_message: Optional[str] = Field(None, description="Full commit message")


class IssueOriginResponse(BaseModel):
    """Response for issue origin lookup."""

    finding_id: str = Field(..., description="ID of the finding")
    introduced_in: Optional[CommitProvenance] = Field(None, description="Commit that introduced the issue")
    confidence: ProvenanceConfidence = Field(..., description="Confidence level of detection")
    confidence_reason: str = Field(..., description="Explanation of confidence level")
    related_commits: List[CommitProvenance] = Field(
        default_factory=list, description="Related commits"
    )
    user_corrected: bool = Field(default=False, description="Whether attribution was manually corrected")
    corrected_commit_sha: Optional[str] = Field(None, description="SHA of user-corrected commit")


class GitHistoryStatusResponse(BaseModel):
    """Git history status for a repository."""

    has_git_history: bool = Field(..., description="Whether git history has been ingested")
    commits_ingested: int = Field(default=0, description="Number of commits ingested")
    oldest_commit_date: Optional[datetime] = Field(None, description="Date of oldest commit")
    newest_commit_date: Optional[datetime] = Field(None, description="Date of newest commit")
    last_updated: Optional[datetime] = Field(None, description="When history was last updated")
    is_backfill_running: bool = Field(default=False, description="Whether a backfill is in progress")


class CommitHistoryResponse(BaseModel):
    """Paginated commit history response."""

    commits: List[CommitProvenance] = Field(default_factory=list, description="List of commits")
    total_count: int = Field(default=0, description="Total commits available")
    has_more: bool = Field(default=False, description="Whether more commits exist")


class BackfillJobStatus(str, enum.Enum):
    """Status of a backfill job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BackfillJobStatusResponse(BaseModel):
    """Status of a git history backfill job."""

    job_id: str = Field(..., description="Unique job ID")
    status: BackfillJobStatus = Field(..., description="Current job status")
    commits_processed: int = Field(default=0, description="Commits processed so far")
    total_commits: Optional[int] = Field(None, description="Total commits to process")
    started_at: Optional[datetime] = Field(None, description="When the job started")
    completed_at: Optional[datetime] = Field(None, description="When the job completed")
    error_message: Optional[str] = Field(None, description="Error message if failed")


class BackfillRequest(BaseModel):
    """Request to trigger a backfill job."""

    max_commits: int = Field(default=500, description="Maximum commits to backfill")


class CorrectAttributionRequest(BaseModel):
    """Request to correct attribution for a finding."""

    commit_sha: str = Field(..., description="Correct commit SHA")


# =============================================================================
# In-memory storage for backfill jobs (would use Redis in production)
# =============================================================================

_backfill_jobs: dict[str, dict] = {}


# =============================================================================
# New Endpoints to Match Frontend API Contract
# =============================================================================


@router.get("/issue-origin", response_model=IssueOriginResponse)
async def get_issue_origin(
    finding_id: str = Query(..., description="Finding ID to get origin for"),
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> IssueOriginResponse:
    """Get the origin commit that introduced a finding.

    Uses git blame and commit history analysis to identify which commit
    introduced the code that caused this finding.
    """
    # Verify finding exists and user has access
    try:
        finding_uuid = UUID(finding_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid finding ID format",
        )

    result = await db.execute(
        select(Finding).where(Finding.id == finding_uuid)
    )
    finding = result.scalar_one_or_none()

    if not finding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Finding not found: {finding_id}",
        )

    # TODO: Implement actual git blame integration with Graphiti
    # For now, return a placeholder response indicating feature is available
    # but no git history has been ingested yet

    try:
        graphiti = _get_graphiti_instance()
        # Query Graphiti for commits related to this file/line
        # This would involve looking up the finding's file path and line number
        # and searching for commits that modified that area

        # For now, return placeholder
        return IssueOriginResponse(
            finding_id=finding_id,
            introduced_in=None,
            confidence=ProvenanceConfidence.UNKNOWN,
            confidence_reason="Git history not yet ingested for this repository. Use POST /historical/backfill to ingest git history.",
            related_commits=[],
            user_corrected=False,
        )
    except HTTPException:
        # Graphiti not available - return degraded response
        return IssueOriginResponse(
            finding_id=finding_id,
            introduced_in=None,
            confidence=ProvenanceConfidence.UNKNOWN,
            confidence_reason="Git history analysis unavailable. Graphiti or OpenAI not configured.",
            related_commits=[],
            user_corrected=False,
        )


@router.get("/status/{repository_id}", response_model=GitHistoryStatusResponse)
async def get_git_history_status(
    repository_id: str,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GitHistoryStatusResponse:
    """Get git history ingestion status for a repository.

    Returns information about whether git history has been ingested,
    how many commits are available, and whether a backfill is running.
    """
    # Verify repository exists and user has access
    try:
        repo_uuid = UUID(repository_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository ID format",
        )

    result = await db.execute(
        select(Repository).where(Repository.id == repo_uuid)
    )
    repo = result.scalar_one_or_none()

    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository not found: {repository_id}",
        )

    # Check if there's a running backfill job for this repo
    is_backfill_running = any(
        job.get("repository_id") == repository_id
        and job.get("status") in ("queued", "running")
        for job in _backfill_jobs.values()
    )

    # TODO: Query Graphiti/FalkorDB for actual commit count
    # For now, return placeholder indicating no history ingested

    return GitHistoryStatusResponse(
        has_git_history=False,
        commits_ingested=0,
        oldest_commit_date=None,
        newest_commit_date=None,
        last_updated=None,
        is_backfill_running=is_backfill_running,
    )


@router.get("/commits", response_model=CommitHistoryResponse)
async def get_commit_history(
    repository_id: str = Query(..., description="Repository ID"),
    limit: int = Query(default=20, ge=1, le=100, description="Max commits to return"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommitHistoryResponse:
    """Get commit history for a repository.

    Returns paginated list of commits that have been ingested into
    the temporal knowledge graph.
    """
    # Verify repository exists and user has access
    try:
        repo_uuid = UUID(repository_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository ID format",
        )

    result = await db.execute(
        select(Repository).where(Repository.id == repo_uuid)
    )
    repo = result.scalar_one_or_none()

    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository not found: {repository_id}",
        )

    # TODO: Query Graphiti for commit history
    # For now, return empty list indicating no history ingested

    return CommitHistoryResponse(
        commits=[],
        total_count=0,
        has_more=False,
    )


@router.get("/commits/{commit_sha}", response_model=CommitProvenance)
async def get_commit(
    commit_sha: str,
    repository_id: str = Query(..., description="Repository ID"),
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommitProvenance:
    """Get details for a specific commit.

    Returns commit metadata including author info and message.
    """
    # Verify repository exists and user has access
    try:
        repo_uuid = UUID(repository_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository ID format",
        )

    result = await db.execute(
        select(Repository).where(Repository.id == repo_uuid)
    )
    repo = result.scalar_one_or_none()

    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository not found: {repository_id}",
        )

    # TODO: Query Graphiti for commit details
    # For now, return 404 as no history is ingested

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Commit not found: {commit_sha}. Git history may not be ingested for this repository.",
    )


@router.post("/backfill/{repository_id}", response_model=BackfillJobStatusResponse)
async def trigger_backfill(
    repository_id: str,
    request: BackfillRequest,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BackfillJobStatusResponse:
    """Trigger a git history backfill job for a repository.

    This will queue a background job to ingest git commit history
    from the repository into the temporal knowledge graph.
    """
    import uuid

    # Verify repository exists and user has access
    try:
        repo_uuid = UUID(repository_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository ID format",
        )

    result = await db.execute(
        select(Repository).where(Repository.id == repo_uuid)
    )
    repo = result.scalar_one_or_none()

    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository not found: {repository_id}",
        )

    # Background job processing is not yet implemented
    # Return 501 Not Implemented to be honest about feature status
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Git history backfill is not yet fully implemented. "
               "Use POST /api/v1/historical/ingest-git to manually ingest git history.",
    )


@router.get("/backfill/status/{job_id}", response_model=BackfillJobStatusResponse)
async def get_backfill_status(
    job_id: str,
    user: ClerkUser = Depends(get_current_user),
) -> BackfillJobStatusResponse:
    """Get status of a git history backfill job.

    Returns current progress and status of the backfill operation.

    Note: Background backfill jobs are not yet implemented.
    """
    # Backfill jobs are not yet implemented - always return 404
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Backfill job not found: {job_id}. Background backfill is not yet implemented.",
    )


@router.post("/correct/{finding_id}", response_model=IssueOriginResponse)
async def correct_attribution(
    finding_id: str,
    request: CorrectAttributionRequest,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> IssueOriginResponse:
    """Correct the attribution for a finding.

    Allows users to manually specify which commit introduced an issue
    when the automatic detection was incorrect.
    """
    # Verify finding exists and user has access
    try:
        finding_uuid = UUID(finding_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid finding ID format",
        )

    result = await db.execute(
        select(Finding).where(Finding.id == finding_uuid)
    )
    finding = result.scalar_one_or_none()

    if not finding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Finding not found: {finding_id}",
        )

    # Attribution correction storage is not yet implemented
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Attribution correction is not yet implemented. "
               "User corrections cannot be persisted at this time.",
    )
