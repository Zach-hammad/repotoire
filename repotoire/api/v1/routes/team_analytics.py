"""API routes for team analytics (cloud-only features).

This module provides endpoints for:
- Code ownership analysis
- Collaboration graph
- Developer profiles
- Team insights (bus factor, knowledge silos)

All endpoints require organization membership and are cloud-only.
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, require_org
from repotoire.db.models import Repository
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.services.team_analytics import TeamAnalyticsService
from sqlalchemy import select

logger = get_logger(__name__)

router = APIRouter(prefix="/team-analytics", tags=["team-analytics"])


# =============================================================================
# Request/Response Models
# =============================================================================


class OwnershipEntry(BaseModel):
    """Code ownership entry."""
    
    path: str
    developer_name: str
    developer_email: str
    ownership_score: float
    commit_count: int


class OwnershipAnalysisResponse(BaseModel):
    """Response for ownership analysis."""
    
    files_analyzed: int
    developers_found: int
    ownership: List[OwnershipEntry]


class CollaboratorEntry(BaseModel):
    """Collaborator in the graph."""
    
    developer_id: str
    name: str
    email: str
    shared_files: int
    collaboration_score: float


class CollaborationGraphResponse(BaseModel):
    """Response for collaboration graph."""
    
    total_developers: int
    total_collaborations: int
    top_pairs: List[dict]


class BusFactorResponse(BaseModel):
    """Response for bus factor analysis."""
    
    bus_factor: int = Field(description="Minimum developers to lose 50% knowledge")
    at_risk_files: List[dict] = Field(description="Files with concentrated ownership")
    top_owners: List[dict] = Field(description="Top code owners")


class DeveloperProfileResponse(BaseModel):
    """Developer profile response."""
    
    id: str
    name: str
    email: str
    total_commits: int
    total_lines_added: int
    total_lines_removed: int
    first_commit_at: Optional[str]
    last_commit_at: Optional[str]
    expertise_areas: dict
    top_owned_files: List[dict]
    top_collaborators: List[dict]


class TeamOverviewResponse(BaseModel):
    """Team overview dashboard response."""
    
    developer_count: int
    total_commits: int
    avg_commits_per_developer: float
    top_contributors: List[dict]
    recent_insights: List[dict]


# =============================================================================
# Helper Functions
# =============================================================================


async def get_user_org_id(session: AsyncSession, user: ClerkUser) -> Optional[UUID]:
    """Get user's organization ID from Clerk org ID."""
    if not user.org_id:
        return None
    
    from repotoire.db.models import Organization
    result = await session.execute(
        select(Organization.id).where(Organization.clerk_org_id == user.org_id)
    )
    org = result.scalar_one_or_none()
    return org


async def verify_repo_access(
    session: AsyncSession,
    repo_id: UUID,
    org_id: UUID,
) -> Repository:
    """Verify repository belongs to organization."""
    result = await session.execute(
        select(Repository).where(
            Repository.id == repo_id,
            Repository.organization_id == org_id,
        )
    )
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found or not accessible",
        )
    return repo


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/overview", response_model=TeamOverviewResponse)
async def get_team_overview(
    repository_id: Optional[UUID] = None,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> TeamOverviewResponse:
    """Get team overview dashboard.
    
    Returns aggregated team statistics including:
    - Developer count
    - Total commits
    - Top contributors
    - Recent insights
    
    **Cloud-only feature** - requires organization membership.
    """
    org_id = await get_user_org_id(session, user)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required for team analytics",
        )
    
    if repository_id:
        await verify_repo_access(session, repository_id, org_id)
    
    service = TeamAnalyticsService(session, org_id)
    overview = await service.get_team_overview(repository_id)
    
    return TeamOverviewResponse(**overview)


@router.post("/analyze-ownership/{repository_id}")
async def analyze_ownership(
    repository_id: UUID,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger code ownership analysis for a repository.
    
    Analyzes git history to determine code ownership based on:
    - Commit history
    - Lines of code contributed
    - Recency of contributions
    
    **Cloud-only feature** - requires organization membership.
    """
    org_id = await get_user_org_id(session, user)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required for team analytics",
        )
    
    repo = await verify_repo_access(session, repository_id, org_id)
    
    # TODO: Fetch git log from repository
    # For now, return a placeholder
    logger.info(f"Ownership analysis requested for repo {repository_id}")
    
    return {
        "status": "queued",
        "message": "Ownership analysis has been queued",
        "repository_id": str(repository_id),
    }


@router.get("/bus-factor/{repository_id}", response_model=BusFactorResponse)
async def get_bus_factor(
    repository_id: UUID,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> BusFactorResponse:
    """Get bus factor analysis for a repository.
    
    Bus factor = minimum number of developers that would need to leave
    for the project to lose critical knowledge.
    
    Also returns:
    - At-risk files (concentrated ownership)
    - Top code owners
    
    **Cloud-only feature** - requires organization membership.
    """
    org_id = await get_user_org_id(session, user)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required for team analytics",
        )
    
    await verify_repo_access(session, repository_id, org_id)
    
    service = TeamAnalyticsService(session, org_id)
    result = await service.compute_bus_factor(repository_id)
    
    return BusFactorResponse(**result)


@router.get("/developer/{developer_id}", response_model=DeveloperProfileResponse)
async def get_developer_profile(
    developer_id: UUID,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> DeveloperProfileResponse:
    """Get detailed developer profile.
    
    Returns:
    - Contribution statistics
    - Top owned files
    - Top collaborators
    - Expertise areas
    
    **Cloud-only feature** - requires organization membership.
    """
    org_id = await get_user_org_id(session, user)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required for team analytics",
        )
    
    service = TeamAnalyticsService(session, org_id)
    profile = await service.get_developer_profile(developer_id)
    
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Developer not found",
        )
    
    return DeveloperProfileResponse(**profile)


@router.get("/developers")
async def list_developers(
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "commits",
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """List all developers in the organization.
    
    Supports pagination and sorting by:
    - commits (default)
    - lines_added
    - name
    
    **Cloud-only feature** - requires organization membership.
    """
    from repotoire.db.models import Developer
    
    org_id = await get_user_org_id(session, user)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required for team analytics",
        )
    
    # Build query
    query = select(Developer).where(Developer.organization_id == org_id)
    
    # Sort
    if sort_by == "commits":
        query = query.order_by(Developer.total_commits.desc())
    elif sort_by == "lines_added":
        query = query.order_by(Developer.total_lines_added.desc())
    elif sort_by == "name":
        query = query.order_by(Developer.name.asc())
    else:
        query = query.order_by(Developer.total_commits.desc())
    
    # Paginate
    query = query.offset(offset).limit(limit)
    
    result = await session.execute(query)
    developers = result.scalars().all()
    
    # Count total
    count_result = await session.execute(
        select(Developer.id).where(Developer.organization_id == org_id)
    )
    total = len(count_result.all())
    
    return {
        "developers": [
            {
                "id": str(d.id),
                "name": d.name,
                "email": d.email,
                "total_commits": d.total_commits,
                "total_lines_added": d.total_lines_added,
                "last_commit_at": d.last_commit_at.isoformat() if d.last_commit_at else None,
            }
            for d in developers
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/collaboration-graph")
async def compute_collaboration_graph(
    repository_id: Optional[UUID] = None,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Compute or refresh the collaboration graph.
    
    Analyzes shared file ownership to determine which developers
    collaborate frequently.
    
    **Cloud-only feature** - requires organization membership.
    """
    org_id = await get_user_org_id(session, user)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required for team analytics",
        )
    
    if repository_id:
        await verify_repo_access(session, repository_id, org_id)
    
    service = TeamAnalyticsService(session, org_id)
    result = await service.compute_collaboration_graph(repository_id)
    
    return {
        "status": "completed",
        **result,
    }


@router.get("/collaboration-graph")
async def get_collaboration_graph(
    repository_id: Optional[UUID] = None,
    limit: int = 20,
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Get the collaboration graph.
    
    Returns top collaboration pairs with:
    - Developer names
    - Shared file count
    - Collaboration score
    
    **Cloud-only feature** - requires organization membership.
    """
    from repotoire.db.models import Collaboration, Developer
    
    org_id = await get_user_org_id(session, user)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required for team analytics",
        )
    
    # Get top collaborations
    result = await session.execute(
        select(Collaboration).where(
            Collaboration.organization_id == org_id,
        ).order_by(Collaboration.collaboration_score.desc()).limit(limit)
    )
    collaborations = result.scalars().all()
    
    # Enrich with developer names
    pairs = []
    for collab in collaborations:
        dev_a_result = await session.execute(
            select(Developer).where(Developer.id == collab.developer_a_id)
        )
        dev_b_result = await session.execute(
            select(Developer).where(Developer.id == collab.developer_b_id)
        )
        dev_a = dev_a_result.scalar_one_or_none()
        dev_b = dev_b_result.scalar_one_or_none()
        
        if dev_a and dev_b:
            pairs.append({
                "developer_a": {"id": str(dev_a.id), "name": dev_a.name, "email": dev_a.email},
                "developer_b": {"id": str(dev_b.id), "name": dev_b.name, "email": dev_b.email},
                "shared_files": collab.shared_files,
                "collaboration_score": collab.collaboration_score,
            })
    
    return {
        "pairs": pairs,
        "total": len(pairs),
    }
