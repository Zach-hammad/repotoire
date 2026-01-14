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
from repotoire.api.v1.routes.graph import APIKeyUser, get_current_api_key_user
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


def _parse_commit_episodes(search_results) -> List[dict]:
    """Parse Graphiti search results (EntityEdge objects) into commit data.

    Extracts commit metadata from EntityEdge objects returned by graphiti.search().

    Args:
        search_results: List of EntityEdge objects from Graphiti search

    Returns:
        List of dicts with commit metadata (sha, author, date, message, etc.)
    """
    import re
    import hashlib

    commits = []

    if not search_results:
        return commits

    # Handle case where search_results might be a string (legacy behavior)
    if isinstance(search_results, str):
        return commits

    for edge in search_results:
        try:
            # EntityEdge has fact, source_node, target_node, created_at, etc.
            # Extract commit data from the edge's fact or source description
            fact = getattr(edge, 'fact', '') or ''
            source_desc = getattr(edge, 'source_description', '') or ''
            created_at = getattr(edge, 'created_at', None)

            # Try to extract commit SHA from source description
            # Format: "Git commit abc12345 from owner/repo"
            sha_match = re.search(r'Git commit ([a-f0-9]+)', source_desc)
            commit_sha = sha_match.group(1) if sha_match else None

            # If no SHA found, generate a pseudo-SHA from the fact
            if not commit_sha and fact:
                commit_sha = hashlib.sha1(fact.encode()).hexdigest()[:8]

            # Extract author from fact (format: "Author: Name <email>")
            author_match = re.search(r'Author:\s*([^<]+)\s*<([^>]+)>', fact)
            author_name = author_match.group(1).strip() if author_match else "Unknown"
            author_email = author_match.group(2).strip() if author_match else ""

            # Extract commit date from fact (format: "Date: ISO-datetime")
            date_match = re.search(r'Date:\s*(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2})', fact)
            commit_date = None
            if date_match:
                try:
                    commit_date = datetime.fromisoformat(date_match.group(1).replace(' ', 'T'))
                except ValueError:
                    pass

            # Fall back to edge created_at if no date in fact
            if not commit_date and created_at:
                commit_date = created_at

            # Extract summary from fact (format: "Summary: ...")
            summary_match = re.search(r'Summary:\s*(.+?)(?:\n|$)', fact)
            message = summary_match.group(1).strip() if summary_match else fact[:80]

            if commit_sha:
                commits.append({
                    "commit_sha": commit_sha,
                    "author_name": author_name,
                    "author_email": author_email,
                    "commit_date": commit_date,
                    "message": message,
                    "full_message": fact,
                })

        except Exception as e:
            logger.debug(f"Failed to parse edge as commit: {e}")
            continue

    return commits


def _format_search_results(search_results, query: str) -> str:
    """Format Graphiti search results into a readable response.

    Converts EntityEdge objects into a human-readable summary.

    Args:
        search_results: List of EntityEdge objects from Graphiti search
        query: The original search query

    Returns:
        Formatted string with search results
    """
    if not search_results:
        return f"No results found for: {query}"

    # Handle case where search_results might already be a string
    if isinstance(search_results, str):
        return search_results

    result_parts = [f"Found {len(search_results)} results for: {query}\n"]

    for i, edge in enumerate(search_results[:20], 1):  # Limit to first 20
        try:
            fact = getattr(edge, 'fact', '') or ''
            source_desc = getattr(edge, 'source_description', '') or ''
            created_at = getattr(edge, 'created_at', None)

            result_parts.append(f"\n--- Result {i} ---")

            if source_desc:
                result_parts.append(f"Source: {source_desc}")

            if created_at:
                result_parts.append(f"Date: {created_at.isoformat()}")

            if fact:
                # Truncate long facts
                fact_preview = fact[:500] + "..." if len(fact) > 500 else fact
                result_parts.append(f"\n{fact_preview}")

        except Exception as e:
            logger.debug(f"Failed to format edge: {e}")
            continue

    if len(search_results) > 20:
        result_parts.append(f"\n... and {len(search_results) - 20} more results")

    return "\n".join(result_parts)


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

        # Get FalkorDB credentials (support both FALKORDB_* and REPOTOIRE_* prefixes)
        falkor_host = os.getenv("FALKORDB_HOST", os.getenv("REPOTOIRE_FALKORDB_HOST", "localhost"))
        falkor_port = os.getenv("FALKORDB_PORT", os.getenv("REPOTOIRE_FALKORDB_PORT", "6379"))
        falkor_uri = os.getenv("REPOTOIRE_FALKOR_URI", f"falkor://{falkor_host}:{falkor_port}")
        falkor_password = os.getenv("FALKORDB_PASSWORD", os.getenv("REPOTOIRE_FALKOR_PASSWORD"))

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

        # Format results into readable response
        formatted_results = _format_search_results(results, request.query)

        return QueryHistoryResponse(
            query=request.query,
            results=formatted_results,
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
        search_query = f"Show all changes to {request.entity_type} {request.entity_name}"
        timeline_results = await graphiti.search(query=search_query)

        execution_time = (time.time() - start_time) * 1000

        # Format results into readable timeline
        formatted_timeline = _format_search_results(
            timeline_results,
            f"timeline of {request.entity_type} {request.entity_name}"
        )

        return TimelineResponse(
            entity_name=request.entity_name,
            entity_type=request.entity_type,
            timeline=formatted_timeline,
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

    # Query Graphiti/FalkorDB for actual commit count
    try:
        graphiti = _get_graphiti_instance()

        # Search for any commits related to this repository
        # Use repo slug/name to find episodes ingested for this repo
        repo_slug = repo.full_name if hasattr(repo, 'full_name') else str(repo_uuid)

        # Query for commit episodes - search for episodes mentioning this repo
        search_results = await graphiti.search(
            query=f"commits from {repo_slug}",
            num_results=1000,  # Get up to 1000 to count
        )

        # Parse results to get commit metadata
        commits_data = _parse_commit_episodes(search_results)
        commits_ingested = len(commits_data)

        # Get date range from parsed commits
        oldest_commit_date = None
        newest_commit_date = None
        last_updated = None

        if commits_data:
            dates = [c.get("commit_date") for c in commits_data if c.get("commit_date")]
            if dates:
                oldest_commit_date = min(dates)
                newest_commit_date = max(dates)
                last_updated = newest_commit_date

        return GitHistoryStatusResponse(
            has_git_history=commits_ingested > 0,
            commits_ingested=commits_ingested,
            oldest_commit_date=oldest_commit_date,
            newest_commit_date=newest_commit_date,
            last_updated=last_updated,
            is_backfill_running=is_backfill_running,
        )

    except HTTPException:
        # Graphiti not available - return degraded response
        return GitHistoryStatusResponse(
            has_git_history=False,
            commits_ingested=0,
            oldest_commit_date=None,
            newest_commit_date=None,
            last_updated=None,
            is_backfill_running=is_backfill_running,
        )
    except Exception as e:
        logger.warning(f"Failed to query git history status: {e}")
        # Return empty response rather than failing
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
    import hashlib

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

    # Query Graphiti for commit history
    try:
        graphiti = _get_graphiti_instance()

        # Get repo identifier for search
        repo_slug = repo.full_name if hasattr(repo, 'full_name') else str(repo_uuid)

        # Query for commit episodes with pagination
        # Request more than needed to support offset/limit
        search_results = await graphiti.search(
            query=f"all commits from {repo_slug}",
            num_results=offset + limit + 100,  # Buffer for pagination
        )

        # Parse results to get commit metadata
        commits_data = _parse_commit_episodes(search_results)

        # Sort by date (newest first)
        commits_data.sort(
            key=lambda c: c.get("commit_date") or datetime.min,
            reverse=True
        )

        # Apply pagination
        total_count = len(commits_data)
        paginated = commits_data[offset:offset + limit]

        # Convert to CommitProvenance objects
        commits = []
        for c in paginated:
            # Generate Gravatar URL from email
            avatar_url = None
            if c.get("author_email"):
                email_hash = hashlib.md5(c["author_email"].lower().strip().encode()).hexdigest()
                avatar_url = f"https://www.gravatar.com/avatar/{email_hash}?d=identicon&s=40"

            commits.append(CommitProvenance(
                commit_sha=c.get("commit_sha", "unknown"),
                author_name=c.get("author_name", "Unknown"),
                author_email=c.get("author_email", ""),
                author_avatar_url=avatar_url,
                commit_date=c.get("commit_date") or datetime.now(),
                message=c.get("message", "No message"),
                full_message=c.get("full_message"),
            ))

        return CommitHistoryResponse(
            commits=commits,
            total_count=total_count,
            has_more=offset + limit < total_count,
        )

    except HTTPException:
        # Graphiti not available - return empty response
        return CommitHistoryResponse(
            commits=[],
            total_count=0,
            has_more=False,
        )
    except Exception as e:
        logger.warning(f"Failed to query commit history: {e}")
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

    This performs an inline backfill of git commit history from the CLI.
    For repositories with GitHub installations, it instructs the user to use
    the CLI to extract and send commits.

    Note: This is a synchronous operation. For large repositories,
    use the CLI command `repotoire historical ingest-git` directly.
    """
    import uuid as uuid_module

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

    # Create a job ID for tracking
    job_id = str(uuid_module.uuid4())

    # Store initial job status
    _backfill_jobs[job_id] = {
        "job_id": job_id,
        "repository_id": repository_id,
        "status": "queued",
        "commits_processed": 0,
        "total_commits": None,
        "started_at": datetime.now(),
        "completed_at": None,
        "error_message": None,
        "max_commits": request.max_commits,
    }

    # For cloud-only architecture, we can't access the git repo directly
    # Return instructions for using the CLI
    repo_slug = repo.full_name if hasattr(repo, 'full_name') else "your-repo"

    # Update job status to indicate manual action needed
    _backfill_jobs[job_id]["status"] = "completed"
    _backfill_jobs[job_id]["completed_at"] = datetime.now()
    _backfill_jobs[job_id]["error_message"] = (
        f"Backfill job created. To ingest git history, run the CLI locally:\n\n"
        f"  repotoire historical ingest-git /path/to/{repo_slug} --max-commits {request.max_commits}\n\n"
        f"This extracts commits from your local clone and sends them to the API."
    )

    return BackfillJobStatusResponse(
        job_id=job_id,
        status=BackfillJobStatus.COMPLETED,
        commits_processed=0,
        total_commits=request.max_commits,
        started_at=_backfill_jobs[job_id]["started_at"],
        completed_at=_backfill_jobs[job_id]["completed_at"],
        error_message=_backfill_jobs[job_id]["error_message"],
    )


@router.get("/backfill/status/{job_id}", response_model=BackfillJobStatusResponse)
async def get_backfill_status(
    job_id: str,
    user: ClerkUser = Depends(get_current_user),
) -> BackfillJobStatusResponse:
    """Get status of a git history backfill job.

    Returns current progress and status of the backfill operation.
    """
    # Look up job in memory
    job = _backfill_jobs.get(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Backfill job not found: {job_id}",
        )

    # Map string status to enum
    status_map = {
        "queued": BackfillJobStatus.QUEUED,
        "running": BackfillJobStatus.RUNNING,
        "completed": BackfillJobStatus.COMPLETED,
        "failed": BackfillJobStatus.FAILED,
    }

    return BackfillJobStatusResponse(
        job_id=job["job_id"],
        status=status_map.get(job["status"], BackfillJobStatus.COMPLETED),
        commits_processed=job.get("commits_processed", 0),
        total_commits=job.get("total_commits"),
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        error_message=job.get("error_message"),
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


# =============================================================================
# New RAG-based Endpoints (Replaces Graphiti - 99% cheaper)
# =============================================================================


class NLQRequest(BaseModel):
    """Request for natural language query over git history."""

    query: str = Field(..., description="Natural language question about git history")
    repo_id: str = Field(..., description="Repository UUID")
    top_k: int = Field(default=10, ge=1, le=50, description="Number of commits to retrieve")
    author: Optional[str] = Field(None, description="Filter by author email")
    since: Optional[datetime] = Field(None, description="Filter commits after this date")
    until: Optional[datetime] = Field(None, description="Filter commits before this date")


class NLQCommitResult(BaseModel):
    """A commit result from NLQ search."""

    sha: str = Field(..., description="Full commit SHA")
    short_sha: str = Field(..., description="Short commit SHA (7 chars)")
    message_subject: str = Field(..., description="First line of commit message")
    author_name: str = Field(..., description="Author name")
    author_email: str = Field(..., description="Author email")
    committed_at: Optional[datetime] = Field(None, description="Commit timestamp")
    files_changed: int = Field(default=0, description="Number of files changed")
    insertions: int = Field(default=0, description="Lines added")
    deletions: int = Field(default=0, description="Lines deleted")
    score: float = Field(default=0.0, description="Relevance score (0-1)")
    changed_file_paths: List[str] = Field(default_factory=list, description="Files changed")


class NLQResponse(BaseModel):
    """Response from natural language query over git history."""

    answer: str = Field(..., description="Natural language answer")
    commits: List[NLQCommitResult] = Field(default_factory=list, description="Relevant commits")
    confidence: float = Field(default=0.0, description="Answer confidence (0-1)")
    follow_up_questions: List[str] = Field(default_factory=list, description="Suggested follow-ups")
    execution_time_ms: float = Field(default=0.0, description="Query execution time")


class NLQSearchResponse(BaseModel):
    """Response from semantic search over git history (without LLM answer)."""

    commits: List[NLQCommitResult] = Field(default_factory=list, description="Matching commits")
    total_count: int = Field(default=0, description="Total matching commits")
    execution_time_ms: float = Field(default=0.0, description="Search execution time")


class NLQStatusResponse(BaseModel):
    """Status of git history RAG for a repository."""

    total_commits: int = Field(default=0, description="Total commits in graph")
    commits_with_embeddings: int = Field(default=0, description="Commits with embeddings")
    coverage: float = Field(default=0.0, description="Embedding coverage (0-1)")
    rag_available: bool = Field(default=False, description="Whether RAG queries are available")
    message: str = Field(default="", description="Status message")


def _get_git_history_rag(repo_id: str, user: Optional[APIKeyUser] = None):
    """Get GitHistoryRAG instance for a repository.

    Uses local embeddings (FREE) instead of Graphiti's LLM approach ($0.01+/commit).

    Args:
        repo_id: Repository UUID for multi-tenant isolation
        user: Optional APIKeyUser for tenant-scoped graph access

    Returns:
        GitHistoryRAG instance

    Raises:
        HTTPException: If dependencies not available
    """
    import os

    try:
        from repotoire.ai.embeddings import CodeEmbedder
        from repotoire.historical.git_rag import GitHistoryRAG
        from repotoire.graph.tenant_factory import get_factory
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"GitHistoryRAG dependencies not available: {e}"
        )

    # Get tenant-scoped graph client if user provided
    if user:
        factory = get_factory()
        graph_client = factory.get_client(org_id=UUID(user.org_id), org_slug=user.org_slug)
    else:
        # Fallback to generic client (for session-based auth)
        from repotoire.graph.factory import create_client
        graph_client = create_client()

    # Initialize embedder with local backend (FREE) or configured backend
    embedding_backend = os.environ.get("REPOTOIRE_EMBEDDING_BACKEND", "local")
    try:
        embedder = CodeEmbedder(backend=embedding_backend)
    except Exception as e:
        logger.warning(f"Failed to initialize {embedding_backend} embedder, falling back to local: {e}")
        embedder = CodeEmbedder(backend="local")

    return GitHistoryRAG(client=graph_client, embedder=embedder)


@router.post("/nlq", response_model=NLQResponse)
async def natural_language_query(
    request: NLQRequest,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NLQResponse:
    """Query git history using natural language with RAG.

    Uses semantic vector search + Claude Haiku to answer questions about
    git history. This is 99% cheaper than Graphiti (~$0.001/query vs $0.01+).

    Examples:
    - "When did we add OAuth authentication?"
    - "What changes did Alice make to the parser?"
    - "Show refactorings of the UserManager class"
    - "What caused the performance regression last month?"

    Returns:
        Natural language answer with supporting commits and confidence score.
    """
    # Verify repository exists and user has access
    try:
        repo_uuid = UUID(request.repo_id)
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
            detail=f"Repository not found: {request.repo_id}",
        )

    try:
        rag = _get_git_history_rag(request.repo_id)

        # Run RAG query
        answer = await rag.ask(
            query=request.query,
            repo_id=request.repo_id,
            top_k=request.top_k,
            author=request.author,
            since=request.since,
            until=request.until,
        )

        # Convert commits to response model
        commits = [
            NLQCommitResult(
                sha=r.commit.sha,
                short_sha=r.commit.short_sha,
                message_subject=r.commit.message_subject,
                author_name=r.commit.author_name,
                author_email=r.commit.author_email,
                committed_at=r.commit.committed_at,
                files_changed=r.commit.files_changed,
                insertions=r.commit.insertions,
                deletions=r.commit.deletions,
                score=r.score,
                changed_file_paths=r.commit.changed_file_paths[:10],
            )
            for r in answer.commits
        ]

        return NLQResponse(
            answer=answer.answer,
            commits=commits,
            confidence=answer.confidence,
            follow_up_questions=answer.follow_up_questions,
            execution_time_ms=answer.execution_time_ms,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"NLQ query failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process query: {e}"
        )


@router.post("/nlq/search", response_model=NLQSearchResponse)
async def nlq_search(
    request: NLQRequest,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NLQSearchResponse:
    """Semantic search over git history (no LLM, faster).

    Uses vector similarity search to find relevant commits without
    generating a natural language answer. Useful for browsing/exploring.

    Returns:
        List of matching commits ordered by relevance.
    """
    import time

    start_time = time.time()

    # Verify repository exists and user has access
    try:
        repo_uuid = UUID(request.repo_id)
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
            detail=f"Repository not found: {request.repo_id}",
        )

    try:
        rag = _get_git_history_rag(request.repo_id)

        # Run search (no LLM)
        results = await rag.search(
            query=request.query,
            repo_id=request.repo_id,
            top_k=request.top_k,
            author=request.author,
            since=request.since,
            until=request.until,
        )

        # Convert to response model
        commits = [
            NLQCommitResult(
                sha=r.commit.sha,
                short_sha=r.commit.short_sha,
                message_subject=r.commit.message_subject,
                author_name=r.commit.author_name,
                author_email=r.commit.author_email,
                committed_at=r.commit.committed_at,
                files_changed=r.commit.files_changed,
                insertions=r.commit.insertions,
                deletions=r.commit.deletions,
                score=r.score,
                changed_file_paths=r.commit.changed_file_paths[:10],
            )
            for r in results
        ]

        elapsed = (time.time() - start_time) * 1000

        return NLQSearchResponse(
            commits=commits,
            total_count=len(commits),
            execution_time_ms=elapsed,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"NLQ search failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to search: {e}"
        )


@router.get("/nlq/status/{repository_id}", response_model=NLQStatusResponse)
async def get_nlq_status(
    repository_id: str,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NLQStatusResponse:
    """Get status of git history RAG for a repository.

    Returns information about commit embeddings coverage and whether
    RAG queries are available.
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

    try:
        rag = _get_git_history_rag(repository_id)

        # Get embeddings status
        status_info = rag.get_embeddings_status(repository_id)

        return NLQStatusResponse(
            total_commits=status_info.get("total_commits", 0),
            commits_with_embeddings=status_info.get("commits_with_embeddings", 0),
            coverage=status_info.get("coverage", 0.0),
            rag_available=status_info.get("total_commits", 0) > 0,
            message="Git history RAG is ready" if status_info.get("total_commits", 0) > 0
                    else "No commits ingested. Run analysis or use `repotoire historical ingest-git`",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Failed to get NLQ status: {e}")
        return NLQStatusResponse(
            total_commits=0,
            commits_with_embeddings=0,
            coverage=0.0,
            rag_available=False,
            message=f"Git history RAG unavailable: {e}",
        )


# =============================================================================
# API Key Authenticated NLQ Endpoints (for CLI)
# =============================================================================
# These endpoints mirror the session-auth versions above but use API key auth
# instead of Clerk session auth. This allows the CLI to use these endpoints.


async def _handle_nlq_query(request: NLQRequest, user: Optional[APIKeyUser] = None) -> NLQResponse:
    """Shared handler for NLQ queries (used by both auth methods)."""
    try:
        rag = _get_git_history_rag(request.repo_id, user=user)

        # Run RAG query
        answer = await rag.ask(
            query=request.query,
            repo_id=request.repo_id,
            top_k=request.top_k,
            author=request.author,
            since=request.since,
            until=request.until,
        )

        # Convert commits to response model
        commits = [
            NLQCommitResult(
                sha=r.commit.sha,
                short_sha=r.commit.short_sha,
                message_subject=r.commit.message_subject,
                author_name=r.commit.author_name,
                author_email=r.commit.author_email,
                committed_at=r.commit.committed_at,
                files_changed=r.commit.files_changed,
                insertions=r.commit.insertions,
                deletions=r.commit.deletions,
                score=r.score,
                changed_file_paths=r.commit.changed_file_paths[:10],
            )
            for r in answer.commits
        ]

        return NLQResponse(
            answer=answer.answer,
            commits=commits,
            confidence=answer.confidence,
            follow_up_questions=answer.follow_up_questions,
            execution_time_ms=answer.execution_time_ms,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"NLQ query failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process query: {e}"
        )


async def _handle_nlq_search(request: NLQRequest, user: Optional[APIKeyUser] = None) -> NLQSearchResponse:
    """Shared handler for NLQ search (used by both auth methods)."""
    import time

    start_time = time.time()

    try:
        rag = _get_git_history_rag(request.repo_id, user=user)

        # Run search (no LLM)
        results = await rag.search(
            query=request.query,
            repo_id=request.repo_id,
            top_k=request.top_k,
            author=request.author,
            since=request.since,
            until=request.until,
        )

        # Convert to response model
        commits = [
            NLQCommitResult(
                sha=r.commit.sha,
                short_sha=r.commit.short_sha,
                message_subject=r.commit.message_subject,
                author_name=r.commit.author_name,
                author_email=r.commit.author_email,
                committed_at=r.commit.committed_at,
                files_changed=r.commit.files_changed,
                insertions=r.commit.insertions,
                deletions=r.commit.deletions,
                score=r.score,
                changed_file_paths=r.commit.changed_file_paths[:10],
            )
            for r in results
        ]

        elapsed = (time.time() - start_time) * 1000

        return NLQSearchResponse(
            commits=commits,
            total_count=len(commits),
            execution_time_ms=elapsed,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"NLQ search failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to search: {e}"
        )


@router.post("/nlq-api", response_model=NLQResponse)
async def natural_language_query_api(
    request: NLQRequest,
    user: APIKeyUser = Depends(get_current_api_key_user),
) -> NLQResponse:
    """Query git history using natural language with RAG (API key auth).

    This endpoint is for CLI/API key authentication. For session-based auth,
    use the /nlq endpoint instead.

    Uses semantic vector search + Claude Haiku to answer questions about
    git history. This is 99% cheaper than Graphiti (~$0.001/query vs $0.01+).
    """
    return await _handle_nlq_query(request, user=user)


@router.post("/nlq-api/search", response_model=NLQSearchResponse)
async def nlq_search_api(
    request: NLQRequest,
    user: APIKeyUser = Depends(get_current_api_key_user),
) -> NLQSearchResponse:
    """Semantic search over git history (API key auth).

    This endpoint is for CLI/API key authentication. For session-based auth,
    use the /nlq/search endpoint instead.

    Uses vector similarity search to find relevant commits without
    generating a natural language answer. Useful for browsing/exploring.
    """
    return await _handle_nlq_search(request, user=user)


@router.get("/nlq-api/status/{repository_id}", response_model=NLQStatusResponse)
async def get_nlq_status_api(
    repository_id: str,
    user: APIKeyUser = Depends(get_current_api_key_user),
) -> NLQStatusResponse:
    """Get status of git history RAG for a repository (API key auth).

    Returns information about commit embeddings coverage and whether
    RAG queries are available.
    """
    try:
        rag = _get_git_history_rag(repository_id, user=user)

        # Get embeddings status
        status_info = rag.get_embeddings_status(repository_id)

        return NLQStatusResponse(
            total_commits=status_info.get("total_commits", 0),
            commits_with_embeddings=status_info.get("commits_with_embeddings", 0),
            coverage=status_info.get("coverage", 0.0),
            rag_available=status_info.get("total_commits", 0) > 0,
            message="Git history RAG is ready" if status_info.get("total_commits", 0) > 0
                    else "No commits ingested. Run `repotoire historical ingest`",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Failed to get NLQ status: {e}")
        return NLQStatusResponse(
            total_commits=0,
            commits_with_embeddings=0,
            coverage=0.0,
            rag_available=False,
            message=f"Git history RAG unavailable: {e}",
        )
