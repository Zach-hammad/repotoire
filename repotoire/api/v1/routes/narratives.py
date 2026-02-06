"""API routes for AI narrative generation.

This module provides endpoints for:
- Generating executive summaries of health scores
- Streaming narrative generation for real-time UX
- Quick insights for specific metrics
- Weekly changelog narratives
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from repotoire.api.services.narrative import (
    HealthContext,
    WeeklyContext,
    create_narrative_generator,
)
from repotoire.api.shared.auth import ClerkUser, require_org
from repotoire.db.models import (
    AnalysisRun,
    Finding,
    Organization,
    Repository,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/narratives", tags=["narratives"])


# =============================================================================
# Request/Response Models
# =============================================================================


class GenerateSummaryRequest(BaseModel):
    """Request to generate an executive summary."""

    repository_id: UUID = Field(
        ...,
        description="UUID of the repository to generate summary for",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "repository_id": "550e8400-e29b-41d4-a716-446655440000",
            }
        }
    }


class GenerateInsightRequest(BaseModel):
    """Request to generate a metric insight."""

    metric_name: str = Field(
        ...,
        description="Name of the metric (e.g., 'structure_score', 'critical_findings')",
    )
    metric_value: Any = Field(
        ...,
        description="The metric value",
    )
    context: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional additional context for the insight",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "metric_name": "structure_score",
                "metric_value": 75,
                "context": {"previous_value": 68, "trend": "improving"},
            }
        }
    }


class GenerateHoverInsightRequest(BaseModel):
    """Request to generate a hover tooltip insight."""

    element_type: str = Field(
        ...,
        description="Type of element being hovered (e.g., 'severity_badge', 'health_score')",
    )
    element_data: Dict[str, Any] = Field(
        ...,
        description="Data about the element",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "element_type": "severity_badge",
                "element_data": {"severity": "critical", "count": 5},
            }
        }
    }


class NarrativeResponse(BaseModel):
    """Response containing generated narrative text."""

    text: str = Field(
        ...,
        description="The generated narrative text",
    )
    model: str = Field(
        ...,
        description="The model used for generation",
    )
    generated_at: datetime = Field(
        ...,
        description="When the narrative was generated",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": "Your codebase health is good at 85%. The main strength is...",
                "model": "gpt-4o-mini",
                "generated_at": "2024-01-15T10:30:00Z",
            }
        }
    }


class WeeklyNarrativeResponse(BaseModel):
    """Response for weekly narrative."""

    text: str = Field(..., description="The weekly narrative text")
    model: str = Field(..., description="The model used for generation")
    generated_at: datetime = Field(..., description="When the narrative was generated")
    week_start: Optional[datetime] = Field(None, description="Start of the week period")
    week_end: Optional[datetime] = Field(None, description="End of the week period")
    score_change: Optional[int] = Field(None, description="Change in health score")
    new_findings_count: int = Field(0, description="Number of new findings")
    resolved_findings_count: int = Field(0, description="Number of resolved findings")

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": "This week's code health score is 82%...",
                "model": "gpt-4o-mini",
                "generated_at": "2024-01-15T10:30:00Z",
                "week_start": "2024-01-08T00:00:00Z",
                "week_end": "2024-01-15T00:00:00Z",
                "score_change": 3,
                "new_findings_count": 5,
                "resolved_findings_count": 8,
            }
        }
    }


# =============================================================================
# Helper Functions
# =============================================================================


async def _get_user_org(session: AsyncSession, user: ClerkUser) -> Organization | None:
    """Get user's organization by Clerk org ID."""
    if not user.org_id:
        return None
    result = await session.execute(
        select(Organization).where(Organization.clerk_org_id == user.org_id)
    )
    return result.scalar_one_or_none()


async def _get_repository(
    session: AsyncSession, org: Organization, repository_id: UUID
) -> Repository | None:
    """Get repository by ID, ensuring it belongs to the user's org."""
    result = await session.execute(
        select(Repository).where(
            Repository.id == repository_id, Repository.organization_id == org.id
        )
    )
    return result.scalar_one_or_none()


async def _get_latest_analysis(
    session: AsyncSession, repository_id: UUID
) -> AnalysisRun | None:
    """Get the latest completed analysis for a repository."""
    result = await session.execute(
        select(AnalysisRun)
        .where(
            AnalysisRun.repository_id == repository_id,
            AnalysisRun.status == "completed",
        )
        .order_by(AnalysisRun.completed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_previous_analysis(
    session: AsyncSession, repository_id: UUID, before: datetime
) -> AnalysisRun | None:
    """Get the previous completed analysis before a given time."""
    result = await session.execute(
        select(AnalysisRun)
        .where(
            AnalysisRun.repository_id == repository_id,
            AnalysisRun.status == "completed",
            AnalysisRun.completed_at < before,
        )
        .order_by(AnalysisRun.completed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_findings_counts(
    session: AsyncSession, analysis_run_id: UUID
) -> Dict[str, int]:
    """Get findings counts by severity for an analysis run."""
    result = await session.execute(
        select(Finding.severity, func.count(Finding.id).label("count"))
        .where(Finding.analysis_run_id == analysis_run_id)
        .group_by(Finding.severity)
    )
    rows = result.all()

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0, "total": 0}
    for severity, count in rows:
        counts[severity.value] = count
        counts["total"] += count

    return counts


async def _build_health_context(
    session: AsyncSession,
    repository: Repository,
    analysis: AnalysisRun,
    previous_analysis: Optional[AnalysisRun] = None,
) -> HealthContext:
    """Build a HealthContext from database models."""
    findings_counts = await _get_findings_counts(session, analysis.id)

    # Get files analyzed count
    files_result = await session.execute(
        select(func.count(func.distinct(Finding.file_path))).where(
            Finding.analysis_run_id == analysis.id
        )
    )
    files_analyzed = files_result.scalar() or 0

    # Build context
    context = HealthContext(
        score=analysis.health_score or 0,
        grade=_score_to_grade(analysis.health_score or 0),
        structure_score=analysis.structure_score or 0,
        quality_score=analysis.quality_score or 0,
        architecture_score=analysis.architecture_score or 0,
        issues_score=analysis.issues_score,
        findings_count=findings_counts["total"],
        critical_count=findings_counts["critical"],
        high_count=findings_counts["high"],
        medium_count=findings_counts["medium"],
        low_count=findings_counts["low"],
        files_analyzed=files_analyzed,
        repo_name=repository.full_name or repository.name,
        analysis_date=analysis.completed_at,
    )

    if previous_analysis and previous_analysis.health_score is not None:
        context.previous_score = previous_analysis.health_score
        diff = (analysis.health_score or 0) - previous_analysis.health_score
        if diff > 0:
            context.score_trend = "up"
        elif diff < 0:
            context.score_trend = "down"
        else:
            context.score_trend = "stable"

    return context


def _score_to_grade(score: int) -> str:
    """Convert a score to a letter grade."""
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    else:
        return "F"


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/summary", response_model=NarrativeResponse)
async def generate_summary(
    request: GenerateSummaryRequest,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> NarrativeResponse:
    """Generate an executive summary of the repository health.

    Uses AI to create a natural language summary of the health analysis,
    including key insights and actionable recommendations.
    """
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization required",
        )

    repository = await _get_repository(session, org, request.repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    analysis = await _get_latest_analysis(session, request.repository_id)
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No completed analysis found for this repository",
        )

    # Get previous analysis for comparison
    previous_analysis = None
    if analysis.completed_at:
        previous_analysis = await _get_previous_analysis(
            session, request.repository_id, analysis.completed_at
        )

    # Build context
    context = await _build_health_context(session, repository, analysis, previous_analysis)

    # Generate narrative
    generator = create_narrative_generator()
    result = await generator.generate_summary(context)

    return NarrativeResponse(
        text=result.text,
        model=result.model,
        generated_at=result.generated_at,
    )


@router.get("/summary/stream")
async def stream_summary(
    repository_id: UUID = Query(..., description="Repository to generate summary for"),
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    """Stream the summary generation for real-time UX.

    Returns Server-Sent Events (SSE) with incremental text chunks
    as the narrative is generated.
    """
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization required",
        )

    repository = await _get_repository(session, org, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    analysis = await _get_latest_analysis(session, repository_id)
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No completed analysis found for this repository",
        )

    # Get previous analysis for comparison
    previous_analysis = None
    if analysis.completed_at:
        previous_analysis = await _get_previous_analysis(
            session, repository_id, analysis.completed_at
        )

    # Build context
    context = await _build_health_context(session, repository, analysis, previous_analysis)

    async def generate_stream() -> AsyncGenerator[dict, None]:
        """Generate SSE events with narrative chunks."""
        generator = create_narrative_generator()

        try:
            async for chunk in generator.stream_summary(context):
                yield {"event": "chunk", "data": chunk}

            yield {"event": "done", "data": ""}

        except Exception as e:
            logger.error(f"Error streaming narrative: {e}")
            yield {"event": "error", "data": str(e)}

    return EventSourceResponse(generate_stream())


@router.post("/insight", response_model=NarrativeResponse)
async def generate_insight(
    request: GenerateInsightRequest,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> NarrativeResponse:
    """Generate a quick insight for a specific metric.

    Creates a focused, 1-2 sentence insight about a particular metric
    that can be used for tooltips or contextual information.
    """
    # Verify user has org access
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization required",
        )

    # Generate insight
    generator = create_narrative_generator()
    result = await generator.generate_insight(
        metric_name=request.metric_name,
        metric_value=request.metric_value,
        context=request.context,
    )

    return NarrativeResponse(
        text=result.text,
        model=result.model,
        generated_at=result.generated_at,
    )


@router.post("/hover", response_model=NarrativeResponse)
async def generate_hover_insight(
    request: GenerateHoverInsightRequest,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> NarrativeResponse:
    """Generate a hover tooltip insight.

    Creates a brief, contextual explanation for UI elements
    like severity badges, health scores, etc.
    """
    # Verify user has org access
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization required",
        )

    # Generate hover insight
    generator = create_narrative_generator()
    result = await generator.generate_hover_insight(
        element_type=request.element_type,
        element_data=request.element_data,
    )

    return NarrativeResponse(
        text=result.text,
        model=result.model,
        generated_at=result.generated_at,
    )


@router.get("/weekly", response_model=WeeklyNarrativeResponse)
async def get_weekly_narrative(
    repository_id: UUID = Query(..., description="Repository to generate weekly narrative for"),
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> WeeklyNarrativeResponse:
    """Generate a weekly health changelog narrative.

    Creates a narrative summary of the week's code health changes,
    including improvements, regressions, and key findings.
    """
    from datetime import timedelta

    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization required",
        )

    repository = await _get_repository(session, org, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    # Get current analysis
    analysis = await _get_latest_analysis(session, repository_id)
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No completed analysis found for this repository",
        )

    # Calculate week boundaries
    now = datetime.now(timezone.utc)
    week_end = now
    week_start = now - timedelta(days=7)

    # Get analysis from a week ago for comparison
    week_ago_analysis = await _get_previous_analysis(session, repository_id, week_start)

    # Build current health context
    current_context = await _build_health_context(session, repository, analysis)

    # Build previous health context if available
    previous_context = None
    if week_ago_analysis:
        previous_context = await _build_health_context(
            session, repository, week_ago_analysis
        )

    # Build weekly context
    weekly_context = WeeklyContext(
        current_health=current_context,
        previous_health=previous_context,
        week_start=week_start,
        week_end=week_end,
        # TODO: Add actual new/resolved findings tracking
        new_findings=[],
        resolved_findings=[],
        top_hotspots=[],
        files_changed=0,
        commits_count=0,
    )

    # Generate narrative
    generator = create_narrative_generator()
    result = await generator.generate_weekly_narrative(weekly_context)

    # Calculate score change
    score_change = None
    if previous_context and previous_context.score is not None:
        score_change = current_context.score - previous_context.score

    return WeeklyNarrativeResponse(
        text=result.text,
        model=result.model,
        generated_at=result.generated_at,
        week_start=week_start,
        week_end=week_end,
        score_change=score_change,
        new_findings_count=len(weekly_context.new_findings or []),
        resolved_findings_count=len(weekly_context.resolved_findings or []),
    )
