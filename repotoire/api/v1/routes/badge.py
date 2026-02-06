"""Public badge API routes for shields.io integration.

These endpoints are PUBLIC and do not require authentication.
They provide dynamic badge data for repository code health.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.db.models.analysis import AnalysisRun, AnalysisStatus
from repotoire.db.models.repository import Repository
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/badge", tags=["badge"])


# =============================================================================
# Response Models
# =============================================================================


class ShieldsIOBadgeResponse(BaseModel):
    """Shields.io endpoint badge response format.

    See: https://shields.io/badges/endpoint-badge
    """

    schemaVersion: int = 1
    label: str
    message: str
    color: str
    cacheSeconds: int | None = None


# =============================================================================
# Grade Calculation Helpers
# =============================================================================


def score_to_grade(score: int | None) -> str:
    """Convert a numeric health score (0-100) to a letter grade."""
    if score is None:
        return "unknown"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def grade_to_color(grade: str) -> str:
    """Map a letter grade to a shields.io color."""
    colors = {
        "A": "brightgreen",
        "B": "green",
        "C": "yellow",
        "D": "orange",
        "F": "red",
        "unknown": "lightgrey",
    }
    return colors.get(grade, "lightgrey")


# =============================================================================
# Public Badge Endpoints
# =============================================================================


@router.get(
    "/{repo_id}",
    response_model=ShieldsIOBadgeResponse,
    summary="Get code health badge for a repository",
    description="""
Returns a shields.io-compatible JSON badge response for the repository's
code health grade.

**Usage with shields.io:**

```markdown
![Code Health](https://img.shields.io/endpoint?url=https://api.repotoire.com/api/v1/badge/{repo_id})
```

The badge displays the repository's letter grade (A-F) based on the
latest completed analysis. Grades are calculated from health scores:
- A: 90-100
- B: 80-89
- C: 70-79
- D: 60-69
- F: 0-59

Returns "unknown" if no analysis exists for the repository.
""",
    responses={
        200: {
            "description": "Badge data for the repository",
            "content": {
                "application/json": {
                    "examples": {
                        "healthy": {
                            "summary": "Healthy repository",
                            "value": {
                                "schemaVersion": 1,
                                "label": "code health",
                                "message": "A",
                                "color": "brightgreen",
                                "cacheSeconds": 300,
                            },
                        },
                        "unknown": {
                            "summary": "No analysis data",
                            "value": {
                                "schemaVersion": 1,
                                "label": "code health",
                                "message": "unknown",
                                "color": "lightgrey",
                                "cacheSeconds": 300,
                            },
                        },
                    }
                }
            },
        }
    },
)
async def get_health_badge(
    repo_id: UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> ShieldsIOBadgeResponse:
    """Get shields.io badge JSON for a repository's code health grade.

    This endpoint is PUBLIC and does not require authentication.
    Results are cached for 5 minutes (300 seconds).
    """
    # Set cache headers for 5 minutes
    response.headers["Cache-Control"] = "public, max-age=300"

    # Look up the repository
    repo_result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = repo_result.scalar_one_or_none()

    if repo is None:
        # Return unknown badge for non-existent repos (don't leak repo existence)
        return ShieldsIOBadgeResponse(
            label="code health",
            message="unknown",
            color="lightgrey",
            cacheSeconds=300,
        )

    # Get the latest completed analysis for this repository
    analysis_result = await db.execute(
        select(AnalysisRun)
        .where(
            AnalysisRun.repository_id == repo_id,
            AnalysisRun.status == AnalysisStatus.COMPLETED,
        )
        .order_by(AnalysisRun.completed_at.desc())
        .limit(1)
    )
    latest_analysis = analysis_result.scalar_one_or_none()

    # Determine the health score
    # Prefer analysis run score, fall back to repository cached score
    if latest_analysis and latest_analysis.health_score is not None:
        score = latest_analysis.health_score
    elif repo.health_score is not None:
        score = repo.health_score
    else:
        score = None

    grade = score_to_grade(score)
    color = grade_to_color(grade)

    logger.debug(
        "Badge request for repo %s: score=%s, grade=%s",
        repo_id,
        score,
        grade,
    )

    return ShieldsIOBadgeResponse(
        label="code health",
        message=grade,
        color=color,
        cacheSeconds=300,
    )


@router.get(
    "/name/{owner}/{repo}",
    response_model=ShieldsIOBadgeResponse,
    summary="Get code health badge by repository name",
    description="""
Returns a shields.io-compatible JSON badge response for the repository's
code health grade, looked up by owner/repo name.

**Usage with shields.io:**

```markdown
![Code Health](https://img.shields.io/endpoint?url=https://api.repotoire.com/api/v1/badge/name/{owner}/{repo})
```
""",
)
async def get_health_badge_by_name(
    owner: str,
    repo: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> ShieldsIOBadgeResponse:
    """Get shields.io badge JSON for a repository by owner/repo name.

    This endpoint is PUBLIC and does not require authentication.
    Results are cached for 5 minutes (300 seconds).
    """
    # Set cache headers for 5 minutes
    response.headers["Cache-Control"] = "public, max-age=300"

    full_name = f"{owner}/{repo}"

    # Look up the repository by full name
    repo_result = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    repository = repo_result.scalar_one_or_none()

    if repository is None:
        return ShieldsIOBadgeResponse(
            label="code health",
            message="unknown",
            color="lightgrey",
            cacheSeconds=300,
        )

    # Get the latest completed analysis
    analysis_result = await db.execute(
        select(AnalysisRun)
        .where(
            AnalysisRun.repository_id == repository.id,
            AnalysisRun.status == AnalysisStatus.COMPLETED,
        )
        .order_by(AnalysisRun.completed_at.desc())
        .limit(1)
    )
    latest_analysis = analysis_result.scalar_one_or_none()

    if latest_analysis and latest_analysis.health_score is not None:
        score = latest_analysis.health_score
    elif repository.health_score is not None:
        score = repository.health_score
    else:
        score = None

    grade = score_to_grade(score)
    color = grade_to_color(grade)

    logger.debug(
        "Badge request for repo %s: score=%s, grade=%s",
        full_name,
        score,
        grade,
    )

    return ShieldsIOBadgeResponse(
        label="code health",
        message=grade,
        color=color,
        cacheSeconds=300,
    )
