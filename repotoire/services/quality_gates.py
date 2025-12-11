"""Quality gates evaluation service.

This module provides functions to evaluate analysis results against
configurable quality gates. Quality gates can block PR merges when
integrated with GitHub's required status checks.

Gate Types:
- block_on_critical: Fail if any critical severity findings exist
- block_on_high: Fail if any high severity findings exist
- min_health_score: Fail if health score is below threshold
- max_new_issues: Fail if total new issues exceed limit
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from repotoire.db.models.finding import Finding, FindingSeverity
from repotoire.logging_config import get_logger
from repotoire.services.github_status import CommitState

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from uuid import UUID

    from repotoire.db.models import AnalysisRun

logger = get_logger(__name__)


# Default quality gate configuration
DEFAULT_QUALITY_GATES: dict[str, Any] = {
    "enabled": True,
    "block_on_critical": True,
    "block_on_high": False,
    "min_health_score": None,
    "max_new_issues": None,
}


@dataclass
class QualityGateResult:
    """Result of quality gate evaluation.

    Attributes:
        passed: Whether all quality gates passed.
        state: GitHub commit status state to set.
        description: Human-readable description for the status.
        details: Additional details about the evaluation.
    """

    passed: bool
    state: CommitState
    description: str
    details: dict = field(default_factory=dict)


def get_finding_counts(
    session: "Session",
    analysis_run_id: "UUID",
) -> dict[str, int]:
    """Get count of findings by severity for an analysis run.

    Args:
        session: SQLAlchemy session.
        analysis_run_id: UUID of the analysis run.

    Returns:
        Dict mapping severity names to counts.
    """
    result = session.execute(
        select(Finding.severity, func.count(Finding.id))
        .where(Finding.analysis_run_id == analysis_run_id)
        .group_by(Finding.severity)
    )

    counts = {}
    for row in result.all():
        severity, count = row
        counts[severity.value] = count

    return counts


def evaluate_quality_gates(
    session: "Session",
    quality_gates: dict[str, Any] | None,
    analysis_run: "AnalysisRun",
    new_findings_only: bool = False,
    base_analysis_id: "UUID | None" = None,
) -> QualityGateResult:
    """Evaluate analysis results against quality gates.

    Args:
        session: SQLAlchemy session.
        quality_gates: Quality gate configuration dict.
        analysis_run: AnalysisRun model instance.
        new_findings_only: If True, only count new findings (vs base).
        base_analysis_id: UUID of base analysis for comparison.

    Returns:
        QualityGateResult with pass/fail status and description.
    """
    gates = quality_gates or DEFAULT_QUALITY_GATES

    # Check if gates are disabled
    if not gates.get("enabled", True):
        return QualityGateResult(
            passed=True,
            state=CommitState.SUCCESS,
            description="Quality gates disabled",
            details={"gates_enabled": False},
        )

    # Get finding counts
    if new_findings_only and base_analysis_id:
        # For PRs, only count NEW findings
        from repotoire.github.pr_commenter import get_new_findings

        new_findings = get_new_findings(
            session=session,
            head_analysis_id=analysis_run.id,
            base_analysis_id=base_analysis_id,
        )
        counts = {}
        for finding in new_findings:
            severity = finding.severity.value
            counts[severity] = counts.get(severity, 0) + 1
    else:
        # For full analysis, count all findings
        counts = get_finding_counts(session, analysis_run.id)

    critical_count = counts.get("critical", 0)
    high_count = counts.get("high", 0)
    medium_count = counts.get("medium", 0)
    low_count = counts.get("low", 0)
    total_count = critical_count + high_count + medium_count + low_count

    # Track failures
    failures: list[str] = []

    # Gate 1: Block on critical issues
    if gates.get("block_on_critical") and critical_count > 0:
        failures.append(f"{critical_count} critical issue{'s' if critical_count > 1 else ''}")

    # Gate 2: Block on high severity issues
    if gates.get("block_on_high") and high_count > 0:
        failures.append(f"{high_count} high severity issue{'s' if high_count > 1 else ''}")

    # Gate 3: Minimum health score
    min_score = gates.get("min_health_score")
    if min_score is not None and analysis_run.health_score is not None:
        if analysis_run.health_score < min_score:
            failures.append(f"Score {analysis_run.health_score} < {min_score}")

    # Gate 4: Maximum new issues
    max_issues = gates.get("max_new_issues")
    if max_issues is not None and total_count > max_issues:
        failures.append(f"{total_count} issues > limit of {max_issues}")

    # Build result
    details = {
        "gates_enabled": True,
        "counts": counts,
        "health_score": analysis_run.health_score,
        "gates_config": {
            "block_on_critical": gates.get("block_on_critical"),
            "block_on_high": gates.get("block_on_high"),
            "min_health_score": min_score,
            "max_new_issues": max_issues,
        },
    }

    if failures:
        # Limit description to fit GitHub's 140 char limit
        description = f"Failed: {'; '.join(failures[:2])}"
        if len(failures) > 2:
            description += f" (+{len(failures) - 2} more)"
        description = description[:140]

        return QualityGateResult(
            passed=False,
            state=CommitState.FAILURE,
            description=description,
            details={**details, "failures": failures},
        )

    # Build success description
    desc_parts = []
    if analysis_run.health_score is not None:
        desc_parts.append(f"Score: {analysis_run.health_score}")
    if total_count > 0:
        desc_parts.append(f"{total_count} issue{'s' if total_count > 1 else ''}")
    else:
        desc_parts.append("No issues")

    return QualityGateResult(
        passed=True,
        state=CommitState.SUCCESS,
        description=" | ".join(desc_parts),
        details=details,
    )


def format_gates_for_response(gates: dict[str, Any] | None) -> dict[str, Any]:
    """Format quality gates for API response.

    Ensures all expected fields are present with defaults.

    Args:
        gates: Raw quality gates dict from database.

    Returns:
        Complete quality gates dict with all fields.
    """
    result = dict(DEFAULT_QUALITY_GATES)
    if gates:
        result.update(gates)
    return result
