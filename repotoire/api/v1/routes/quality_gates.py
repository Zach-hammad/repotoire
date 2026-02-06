"""API routes for quality gate evaluation.

This module provides endpoints for:
- Evaluating quality gates against repository analysis results
- Listing available quality gates
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user_or_api_key, require_org
from repotoire.config import QualityGateAction, QualityGateConditions, QualityGateConfig
from repotoire.db.models import AnalysisRun, AnalysisStatus, Organization, Repository
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.models import CodebaseHealth, FindingsSummary, MetricsBreakdown
from repotoire.services.quality_gates import (
    ConditionResult,
    GateStatus,
    QualityGateResult,
    evaluate_quality_gate,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/repos", tags=["repositories"])


# =============================================================================
# Request/Response Models
# =============================================================================


class QualityGateConditionsRequest(BaseModel):
    """Quality gate conditions for evaluation."""

    max_critical: Optional[int] = Field(None, ge=0, description="Maximum critical findings allowed")
    max_high: Optional[int] = Field(None, ge=0, description="Maximum high severity findings allowed")
    max_medium: Optional[int] = Field(None, ge=0, description="Maximum medium severity findings allowed")
    max_low: Optional[int] = Field(None, ge=0, description="Maximum low severity findings allowed")
    max_total: Optional[int] = Field(None, ge=0, description="Maximum total findings allowed")
    min_grade: Optional[str] = Field(None, pattern="^[A-Fa-f]$", description="Minimum grade (A, B, C, D, F)")
    min_score: Optional[float] = Field(None, ge=0, le=100, description="Minimum health score (0-100)")
    max_new_issues: Optional[int] = Field(None, ge=0, description="Maximum new issues since baseline")


class QualityGateRequest(BaseModel):
    """Request to evaluate a quality gate."""

    conditions: QualityGateConditionsRequest = Field(
        ...,
        description="Quality gate conditions to evaluate",
    )
    on_fail: str = Field(
        "block",
        description="Action on failure: block, warn, or ignore",
        pattern="^(block|warn|ignore)$",
    )
    baseline_analysis_id: Optional[UUID] = Field(
        None,
        description="Optional baseline analysis ID for comparison (for max_new_issues)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "conditions": {
                    "max_critical": 0,
                    "max_high": 5,
                    "min_grade": "C",
                },
                "on_fail": "block",
            }
        }
    }


class ConditionResultResponse(BaseModel):
    """Result of evaluating a single condition."""

    condition_name: str = Field(..., description="Name of the condition evaluated")
    passed: bool = Field(..., description="Whether the condition passed")
    actual_value: Optional[float | int | str] = Field(None, description="Actual value from analysis")
    threshold_value: Optional[float | int | str] = Field(None, description="Threshold value from gate")
    message: str = Field(..., description="Human-readable result message")


class QualityGateResponse(BaseModel):
    """Response from quality gate evaluation."""

    gate_name: str = Field(..., description="Name of the evaluated gate")
    status: str = Field(..., description="Gate status: passed, failed, warning, or skipped")
    passed: bool = Field(..., description="Whether all conditions passed")
    action: str = Field(..., description="Action configured for failures")
    exit_code: int = Field(..., description="Suggested exit code (0=pass, 1=fail)")
    conditions_evaluated: int = Field(..., description="Number of conditions evaluated")
    conditions_passed: int = Field(..., description="Number of conditions that passed")
    condition_results: List[ConditionResultResponse] = Field(
        ..., description="Detailed results for each condition"
    )
    summary: str = Field(..., description="Human-readable summary")

    # Analysis context
    analysis_id: Optional[UUID] = Field(None, description="ID of the analysis evaluated")
    grade: Optional[str] = Field(None, description="Analysis grade")
    score: Optional[float] = Field(None, description="Analysis score")
    findings_summary: Optional[dict] = Field(None, description="Findings count by severity")

    model_config = {
        "json_schema_extra": {
            "example": {
                "gate_name": "API Gate",
                "status": "failed",
                "passed": False,
                "action": "block",
                "exit_code": 1,
                "conditions_evaluated": 3,
                "conditions_passed": 2,
                "condition_results": [
                    {
                        "condition_name": "max_critical",
                        "passed": True,
                        "actual_value": 0,
                        "threshold_value": 0,
                        "message": "Critical findings: 0 (max: 0)",
                    },
                    {
                        "condition_name": "max_high",
                        "passed": False,
                        "actual_value": 7,
                        "threshold_value": 5,
                        "message": "High findings: 7 (max: 5)",
                    },
                    {
                        "condition_name": "min_grade",
                        "passed": True,
                        "actual_value": "B",
                        "threshold_value": "C",
                        "message": "Grade: B (minimum: C)",
                    },
                ],
                "summary": "Quality gate 'API Gate' failed: max_high",
                "analysis_id": "550e8400-e29b-41d4-a716-446655440000",
                "grade": "B",
                "score": 78.5,
                "findings_summary": {
                    "critical": 0,
                    "high": 7,
                    "medium": 12,
                    "low": 25,
                    "total": 44,
                },
            }
        }
    }


# =============================================================================
# Endpoints
# =============================================================================


@router.post(
    "/{repo_id}/gates/evaluate",
    response_model=QualityGateResponse,
    summary="Evaluate quality gate",
    description="""
Evaluate a quality gate against the latest analysis results for a repository.

Quality gates define pass/fail criteria for CI/CD pipelines. This endpoint
allows you to programmatically check if code meets your quality standards.

**Conditions:**
- `max_critical`: Maximum critical severity findings (default: unlimited)
- `max_high`: Maximum high severity findings (default: unlimited)
- `max_medium`: Maximum medium severity findings (default: unlimited)
- `max_low`: Maximum low severity findings (default: unlimited)
- `max_total`: Maximum total findings (default: unlimited)
- `min_grade`: Minimum grade required (A, B, C, D, F)
- `min_score`: Minimum health score (0-100)
- `max_new_issues`: Maximum new issues since baseline (requires baseline)

**Actions:**
- `block`: Return exit_code=1 on failure (default)
- `warn`: Return exit_code=0 but status=warning on failure
- `ignore`: Always return exit_code=0

**Use in CI/CD:**
```bash
# Evaluate gate and check exit code
curl -X POST "https://api.repotoire.io/api/v1/repos/{repo_id}/gates/evaluate" \\
  -H "X-API-Key: $API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"conditions": {"max_critical": 0, "min_grade": "C"}, "on_fail": "block"}' \\
  | jq -e '.exit_code == 0'
```
    """,
    responses={
        200: {"description": "Gate evaluation completed successfully"},
        404: {"description": "Repository or analysis not found"},
        422: {"description": "Invalid gate conditions"},
    },
)
async def evaluate_gate(
    repo_id: UUID,
    request: QualityGateRequest,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> QualityGateResponse:
    """Evaluate a quality gate against repository analysis."""
    # Get organization context
    org = await require_org(user, db)

    # Get repository
    result = await db.execute(
        select(Repository).where(
            Repository.id == repo_id,
            Repository.org_id == org.id,
        )
    )
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository {repo_id} not found",
        )

    # Get latest completed analysis
    result = await db.execute(
        select(AnalysisRun)
        .where(
            AnalysisRun.repository_id == repo_id,
            AnalysisRun.status == AnalysisStatus.COMPLETED,
        )
        .order_by(AnalysisRun.completed_at.desc())
        .limit(1)
    )
    analysis = result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No completed analysis found for repository {repo_id}",
        )

    # Get baseline analysis if specified
    baseline_health = None
    if request.baseline_analysis_id:
        result = await db.execute(
            select(AnalysisRun).where(
                AnalysisRun.id == request.baseline_analysis_id,
                AnalysisRun.repository_id == repo_id,
                AnalysisRun.status == AnalysisStatus.COMPLETED,
            )
        )
        baseline = result.scalar_one_or_none()
        if baseline and baseline.results:
            baseline_health = _create_health_from_results(baseline.results)

    # Create CodebaseHealth from analysis results
    health = _create_health_from_results(analysis.results)

    # Create gate config from request
    conditions = QualityGateConditions(
        max_critical=request.conditions.max_critical,
        max_high=request.conditions.max_high,
        max_medium=request.conditions.max_medium,
        max_low=request.conditions.max_low,
        max_total=request.conditions.max_total,
        min_grade=request.conditions.min_grade.upper() if request.conditions.min_grade else None,
        min_score=request.conditions.min_score,
        max_new_issues=request.conditions.max_new_issues,
    )

    gate_config = QualityGateConfig(
        name="API Gate",
        conditions=conditions,
        on_fail=request.on_fail,
    )

    # Evaluate gate
    result = evaluate_quality_gate(health, gate_config, baseline_health)

    # Build response
    return QualityGateResponse(
        gate_name=result.gate_name,
        status=result.status.value,
        passed=result.passed,
        action=result.action.value,
        exit_code=result.exit_code,
        conditions_evaluated=result.conditions_evaluated,
        conditions_passed=result.conditions_passed,
        condition_results=[
            ConditionResultResponse(
                condition_name=cr.condition_name,
                passed=cr.passed,
                actual_value=cr.actual_value,
                threshold_value=cr.threshold_value,
                message=cr.message,
            )
            for cr in result.condition_results
        ],
        summary=result.summary,
        analysis_id=analysis.id,
        grade=health.grade,
        score=round(health.overall_score, 1),
        findings_summary={
            "critical": health.findings_summary.critical,
            "high": health.findings_summary.high,
            "medium": health.findings_summary.medium,
            "low": health.findings_summary.low,
            "total": health.findings_summary.total,
        },
    )


def _create_health_from_results(results: dict) -> CodebaseHealth:
    """Create a CodebaseHealth object from analysis results dict.

    Args:
        results: Analysis results dictionary from database

    Returns:
        CodebaseHealth object for gate evaluation
    """
    # Extract findings summary
    findings_data = results.get("findings_summary", {})
    findings_summary = FindingsSummary(
        critical=findings_data.get("critical", 0),
        high=findings_data.get("high", 0),
        medium=findings_data.get("medium", 0),
        low=findings_data.get("low", 0),
        info=findings_data.get("info", 0),
    )

    # Extract metrics (minimal for gate evaluation)
    metrics = MetricsBreakdown(
        total_files=results.get("total_files", 0),
        total_classes=results.get("total_classes", 0),
        total_functions=results.get("total_functions", 0),
        total_lines=results.get("total_lines", 0),
        avg_complexity=results.get("avg_complexity", 0),
        max_complexity=results.get("max_complexity", 0),
        dependency_count=results.get("dependency_count", 0),
        circular_dependencies=results.get("circular_dependencies", 0),
    )

    return CodebaseHealth(
        grade=results.get("grade", "F"),
        overall_score=results.get("health_score", 0),
        structure_score=results.get("structure_score", 0),
        quality_score=results.get("quality_score", 0),
        architecture_score=results.get("architecture_score", 0),
        issues_score=results.get("issues_score", 0),
        metrics=metrics,
        findings_summary=findings_summary,
        findings=[],  # Don't need full findings for gate evaluation
    )
