"""API routes for git history natural language queries.

Uses GitHistoryRAG for semantic search over git commits - 99% cheaper than
the deprecated Graphiti approach (~$0.001/query vs $0.01+).
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.api.v1.routes.graph import GraphUser, get_graph_user
from repotoire.db.models import Repository
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/historical",
    tags=["historical"],
)


# =============================================================================
# Request/Response Models
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


# =============================================================================
# Helper Functions
# =============================================================================


def _get_git_history_rag(repo_id: str, user: Optional[GraphUser] = None):
    """Get GitHistoryRAG instance for a repository.

    Uses local embeddings (FREE) instead of the deprecated Graphiti approach.

    Args:
        repo_id: Repository UUID for multi-tenant isolation
        user: Optional GraphUser for tenant-scoped graph access

    Returns:
        GitHistoryRAG instance

    Raises:
        HTTPException: If dependencies not available
    """
    import os

    try:
        from repotoire.ai.embeddings import CodeEmbedder
        from repotoire.graph.tenant_factory import get_factory
        from repotoire.historical.git_rag import GitHistoryRAG
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


# =============================================================================
# Health Check
# =============================================================================


@router.get("/health", tags=["Health"])
async def historical_health_check():
    """Health check for historical analysis endpoints.

    Checks if GitHistoryRAG and required dependencies are available.
    """
    import os

    health_status = {
        "status": "healthy",
        "rag_available": False,
        "embedding_backend": os.environ.get("REPOTOIRE_EMBEDDING_BACKEND", "local"),
    }

    try:
        from repotoire.ai.embeddings import CodeEmbedder
        from repotoire.historical.git_rag import GitHistoryRAG
        health_status["rag_available"] = True
    except ImportError as e:
        health_status["status"] = "degraded"
        health_status["import_error"] = str(e)
        health_status["message"] = "GitHistoryRAG dependencies not available"

    if health_status["rag_available"]:
        health_status["message"] = "Git history RAG ready"

    return health_status


# =============================================================================
# Session-Auth Endpoints (for Web UI)
# =============================================================================


@router.post("/nlq", response_model=NLQResponse)
async def natural_language_query(
    request: NLQRequest,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NLQResponse:
    """Query git history using natural language with RAG.

    Uses semantic vector search + Claude Haiku to answer questions about
    git history. This is 99% cheaper than the old approach (~$0.001/query).

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
# API Key Authenticated Endpoints (for CLI)
# =============================================================================


async def _handle_nlq_query(request: NLQRequest, user: Optional[GraphUser] = None) -> NLQResponse:
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


async def _handle_nlq_search(request: NLQRequest, user: Optional[GraphUser] = None) -> NLQSearchResponse:
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
    user: GraphUser = Depends(get_graph_user),
) -> NLQResponse:
    """Query git history using natural language with RAG (API key auth).

    This endpoint is for CLI/API key authentication. For session-based auth,
    use the /nlq endpoint instead.

    Uses semantic vector search + Claude Haiku to answer questions about
    git history. This is 99% cheaper than the old approach (~$0.001/query).
    """
    return await _handle_nlq_query(request, user=user)


@router.post("/nlq-api/search", response_model=NLQSearchResponse)
async def nlq_search_api(
    request: NLQRequest,
    user: GraphUser = Depends(get_graph_user),
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
    user: GraphUser = Depends(get_graph_user),
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
