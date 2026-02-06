"""API routes for CLI sync functionality.

This module provides endpoints for syncing local CLI analysis to the cloud:
- Upload local analysis results
- Register local repositories
- Sync findings and health scores
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, require_org
from repotoire.db.models import (
    AnalysisRun,
    AnalysisStatus,
    Organization,
    Repository,
)
from repotoire.db.models.finding import Finding as DBFinding
from repotoire.db.models.finding import FindingSeverity
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/cli-sync", tags=["cli-sync"])


# =============================================================================
# Request/Response Models
# =============================================================================


class FindingUpload(BaseModel):
    """A finding to upload from CLI."""

    detector_id: str
    title: str
    description: str
    severity: str  # "critical", "high", "medium", "low", "info"
    file_path: str
    line_start: int
    line_end: Optional[int] = None
    category: Optional[str] = None
    cwe_id: Optional[str] = None
    why_it_matters: Optional[str] = None
    suggested_fix: Optional[str] = None
    code_snippet: Optional[str] = None
    metadata: Optional[dict] = None


class HealthScoreUpload(BaseModel):
    """Health score data from CLI."""

    health_score: float = Field(ge=0, le=100)
    structure_score: float = Field(ge=0, le=100)
    quality_score: float = Field(ge=0, le=100)
    architecture_score: Optional[float] = Field(None, ge=0, le=100)


class CLISyncRequest(BaseModel):
    """Request to sync CLI analysis to cloud."""

    # Repository info
    repo_name: str = Field(..., description="Repository name (e.g., 'my-project')")
    repo_url: Optional[str] = Field(None, description="Git remote URL")
    commit_sha: Optional[str] = Field(None, description="Analyzed commit SHA")
    branch: Optional[str] = Field(None, description="Branch name")

    # Analysis results
    health: HealthScoreUpload
    findings: List[FindingUpload] = Field(default_factory=list)

    # Metadata
    cli_version: str = Field(..., description="CLI version used")
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_files: Optional[int] = None
    total_functions: Optional[int] = None
    total_classes: Optional[int] = None


class CLISyncResponse(BaseModel):
    """Response from CLI sync."""

    status: str
    repository_id: str
    analysis_id: str
    findings_synced: int
    dashboard_url: str


# =============================================================================
# Helper Functions
# =============================================================================


async def get_org_from_user(session: AsyncSession, user: ClerkUser) -> Organization:
    """Get organization from Clerk user."""
    if not user.org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization context. Create or join an organization first.",
        )

    result = await session.execute(
        select(Organization).where(Organization.clerk_org_id == user.org_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return org


def map_severity(severity_str: str) -> FindingSeverity:
    """Map severity string to FindingSeverity enum."""
    mapping = {
        "critical": FindingSeverity.CRITICAL,
        "high": FindingSeverity.HIGH,
        "medium": FindingSeverity.MEDIUM,
        "low": FindingSeverity.LOW,
        "info": FindingSeverity.INFO,
    }
    return mapping.get(severity_str.lower(), FindingSeverity.INFO)


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/upload", response_model=CLISyncResponse)
async def upload_cli_analysis(
    sync_data: CLISyncRequest,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> CLISyncResponse:
    """Upload local CLI analysis results to cloud.
    
    This endpoint allows the CLI to sync local analysis to the cloud dashboard,
    enabling team visibility into individually-run analyses.
    
    The repository will be auto-created if it doesn't exist in the organization.
    
    **Requires authentication** - run `repotoire login` first.
    """
    org = await get_org_from_user(session, user)

    # Find or create repository
    full_name = f"{org.slug}/{sync_data.repo_name}"

    result = await session.execute(
        select(Repository).where(
            Repository.organization_id == org.id,
            Repository.full_name == full_name,
        )
    )
    repo = result.scalar_one_or_none()

    if not repo:
        # Create new repository record for CLI-synced repo
        # Use 0 for github_repo_id to indicate a local/CLI repo
        repo = Repository(
            organization_id=org.id,
            github_repo_id=0,  # CLI-synced, no GitHub ID
            github_installation_id=0,  # CLI-synced, no installation
            full_name=full_name,
            default_branch=sync_data.branch or "main",
            is_active=True,
        )
        session.add(repo)
        await session.flush()
        logger.info(f"Created CLI repository {full_name} for org {org.slug}")

    # Create analysis run record
    analysis = AnalysisRun(
        repository_id=repo.id,
        commit_sha=sync_data.commit_sha or "cli-local",
        branch=sync_data.branch or "unknown",
        status=AnalysisStatus.COMPLETED,
        health_score=int(sync_data.health.health_score),
        structure_score=int(sync_data.health.structure_score),
        quality_score=int(sync_data.health.quality_score),
        architecture_score=int(sync_data.health.architecture_score) if sync_data.health.architecture_score else None,
        findings_count=len(sync_data.findings),
        files_analyzed=sync_data.total_files or 0,
        started_at=sync_data.analyzed_at,
        completed_at=datetime.now(timezone.utc),
    )
    session.add(analysis)
    await session.flush()

    # Create finding records
    findings_created = 0
    for finding_data in sync_data.findings:
        finding = DBFinding(
            analysis_run_id=analysis.id,
            detector=finding_data.detector_id,
            title=finding_data.title,
            description=finding_data.description or "",
            severity=map_severity(finding_data.severity),
            affected_files=[finding_data.file_path] if finding_data.file_path else [],
            affected_nodes=[],
            line_start=finding_data.line_start,
            line_end=finding_data.line_end or finding_data.line_start,
            suggested_fix=finding_data.suggested_fix,
            graph_context={
                "category": finding_data.category,
                "cwe_id": finding_data.cwe_id,
                "why_it_matters": finding_data.why_it_matters,
                "code_snippet": finding_data.code_snippet,
                **(finding_data.metadata or {}),
            } if any([finding_data.category, finding_data.cwe_id, finding_data.why_it_matters, finding_data.code_snippet, finding_data.metadata]) else None,
        )
        session.add(finding)
        findings_created += 1

    await session.commit()

    # Build dashboard URL
    base_url = "https://app.repotoire.io"
    repo_slug = sync_data.repo_name
    dashboard_url = f"{base_url}/dashboard/{org.slug}/{repo_slug}/analysis/{analysis.id}"

    logger.info(
        f"CLI sync completed: repo={repo.full_name}, analysis={analysis.id}, "
        f"findings={findings_created}, user={user.user_id}"
    )

    return CLISyncResponse(
        status="synced",
        repository_id=str(repo.id),
        analysis_id=str(analysis.id),
        findings_synced=findings_created,
        dashboard_url=dashboard_url,
    )


@router.get("/repositories")
async def list_synced_repositories(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """List repositories synced from CLI.
    
    Returns all repositories in the organization that have CLI-synced analyses.
    """
    org = await get_org_from_user(session, user)

    # Get repositories with CLI analyses
    result = await session.execute(
        select(Repository).where(
            Repository.organization_id == org.id,
            Repository.is_active == True,
        ).order_by(Repository.updated_at.desc())
    )
    repos = result.scalars().all()

    return {
        "repositories": [
            {
                "id": str(r.id),
                "name": r.full_name.split("/")[-1] if "/" in r.full_name else r.full_name,
                "full_name": r.full_name,
                "default_branch": r.default_branch,
                "health_score": r.health_score,
                "last_analyzed_at": r.last_analyzed_at.isoformat() if r.last_analyzed_at else None,
            }
            for r in repos
        ]
    }
