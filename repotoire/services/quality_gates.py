"""Quality gate evaluation service.

This module provides functionality to evaluate code health against
quality gate conditions, determining pass/fail status for CI/CD pipelines.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from repotoire.config import QualityGateAction, QualityGateConditions, QualityGateConfig
from repotoire.logging_config import get_logger
from repotoire.models import CodebaseHealth, Severity

logger = get_logger(__name__)


# Grade ordering for comparison (lower index = better grade)
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}


class GateStatus(str, Enum):
    """Result of quality gate evaluation."""
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"  # Gate failed but on_fail=warn
    SKIPPED = "skipped"  # Gate on_fail=ignore


@dataclass
class ConditionResult:
    """Result of evaluating a single condition."""
    condition_name: str
    passed: bool
    actual_value: Optional[float | int | str]
    threshold_value: Optional[float | int | str]
    message: str


@dataclass
class QualityGateResult:
    """Result of evaluating a quality gate."""
    gate_name: str
    status: GateStatus
    passed: bool
    action: QualityGateAction
    conditions_evaluated: int
    conditions_passed: int
    condition_results: List[ConditionResult]
    summary: str

    @property
    def exit_code(self) -> int:
        """Get exit code based on gate result.

        Returns:
            0 = passed
            1 = failed (blocking)
            0 = warning (non-blocking)
            0 = skipped
        """
        if self.status == GateStatus.FAILED:
            return 1
        return 0


def evaluate_quality_gate(
    health: CodebaseHealth,
    gate: QualityGateConfig,
    baseline_health: Optional[CodebaseHealth] = None,
) -> QualityGateResult:
    """Evaluate a quality gate against codebase health.

    Args:
        health: Current codebase health to evaluate
        gate: Quality gate configuration
        baseline_health: Optional baseline for comparison (for max_new_issues)

    Returns:
        QualityGateResult with pass/fail status and details
    """
    conditions = gate.conditions
    results: List[ConditionResult] = []

    # Get finding counts by severity
    critical_count = health.findings_summary.critical
    high_count = health.findings_summary.high
    medium_count = health.findings_summary.medium
    low_count = health.findings_summary.low
    total_count = health.findings_summary.total

    # Evaluate max_critical
    if conditions.max_critical is not None:
        passed = critical_count <= conditions.max_critical
        results.append(ConditionResult(
            condition_name="max_critical",
            passed=passed,
            actual_value=critical_count,
            threshold_value=conditions.max_critical,
            message=f"Critical findings: {critical_count} (max: {conditions.max_critical})"
        ))

    # Evaluate max_high
    if conditions.max_high is not None:
        passed = high_count <= conditions.max_high
        results.append(ConditionResult(
            condition_name="max_high",
            passed=passed,
            actual_value=high_count,
            threshold_value=conditions.max_high,
            message=f"High findings: {high_count} (max: {conditions.max_high})"
        ))

    # Evaluate max_medium
    if conditions.max_medium is not None:
        passed = medium_count <= conditions.max_medium
        results.append(ConditionResult(
            condition_name="max_medium",
            passed=passed,
            actual_value=medium_count,
            threshold_value=conditions.max_medium,
            message=f"Medium findings: {medium_count} (max: {conditions.max_medium})"
        ))

    # Evaluate max_low
    if conditions.max_low is not None:
        passed = low_count <= conditions.max_low
        results.append(ConditionResult(
            condition_name="max_low",
            passed=passed,
            actual_value=low_count,
            threshold_value=conditions.max_low,
            message=f"Low findings: {low_count} (max: {conditions.max_low})"
        ))

    # Evaluate max_total
    if conditions.max_total is not None:
        passed = total_count <= conditions.max_total
        results.append(ConditionResult(
            condition_name="max_total",
            passed=passed,
            actual_value=total_count,
            threshold_value=conditions.max_total,
            message=f"Total findings: {total_count} (max: {conditions.max_total})"
        ))

    # Evaluate min_grade
    if conditions.min_grade is not None:
        min_grade_upper = conditions.min_grade.upper()
        current_grade = health.grade.upper()
        # Compare grades (A > B > C > D > F)
        current_grade_rank = GRADE_ORDER.get(current_grade, 4)
        min_grade_rank = GRADE_ORDER.get(min_grade_upper, 4)
        passed = current_grade_rank <= min_grade_rank
        results.append(ConditionResult(
            condition_name="min_grade",
            passed=passed,
            actual_value=current_grade,
            threshold_value=min_grade_upper,
            message=f"Grade: {current_grade} (minimum: {min_grade_upper})"
        ))

    # Evaluate min_score
    if conditions.min_score is not None:
        passed = health.overall_score >= conditions.min_score
        results.append(ConditionResult(
            condition_name="min_score",
            passed=passed,
            actual_value=round(health.overall_score, 1),
            threshold_value=conditions.min_score,
            message=f"Score: {health.overall_score:.1f} (minimum: {conditions.min_score})"
        ))

    # Evaluate max_new_issues (requires baseline)
    if conditions.max_new_issues is not None:
        if baseline_health is not None:
            new_issues = total_count - baseline_health.findings_summary.total
            new_issues = max(0, new_issues)  # Don't count as negative
            passed = new_issues <= conditions.max_new_issues
            results.append(ConditionResult(
                condition_name="max_new_issues",
                passed=passed,
                actual_value=new_issues,
                threshold_value=conditions.max_new_issues,
                message=f"New issues: {new_issues} (max: {conditions.max_new_issues})"
            ))
        else:
            # No baseline available, skip this condition
            results.append(ConditionResult(
                condition_name="max_new_issues",
                passed=True,  # Don't fail if no baseline
                actual_value=None,
                threshold_value=conditions.max_new_issues,
                message=f"New issues: skipped (no baseline available)"
            ))

    # Calculate overall result
    conditions_evaluated = len(results)
    conditions_passed = sum(1 for r in results if r.passed)
    all_passed = all(r.passed for r in results) if results else True

    # Determine status based on on_fail action
    action = QualityGateAction(gate.on_fail.lower())

    if all_passed:
        status = GateStatus.PASSED
    elif action == QualityGateAction.IGNORE:
        status = GateStatus.SKIPPED
    elif action == QualityGateAction.WARN:
        status = GateStatus.WARNING
    else:  # BLOCK
        status = GateStatus.FAILED

    # Generate summary
    failed_conditions = [r for r in results if not r.passed]
    if all_passed:
        summary = f"Quality gate '{gate.name}' passed ({conditions_passed}/{conditions_evaluated} conditions met)"
    else:
        failed_names = [r.condition_name for r in failed_conditions]
        summary = f"Quality gate '{gate.name}' failed: {', '.join(failed_names)}"

    return QualityGateResult(
        gate_name=gate.name,
        status=status,
        passed=all_passed,
        action=action,
        conditions_evaluated=conditions_evaluated,
        conditions_passed=conditions_passed,
        condition_results=results,
        summary=summary,
    )


def create_inline_gate(
    fail_on: Optional[str] = None,
    max_issues: Optional[int] = None,
    max_critical: Optional[int] = None,
    max_high: Optional[int] = None,
    min_grade: Optional[str] = None,
    min_score: Optional[float] = None,
) -> QualityGateConfig:
    """Create an inline quality gate from CLI parameters.

    Args:
        fail_on: Severity threshold (maps to max_critical/max_high)
        max_issues: Maximum total issues allowed
        max_critical: Maximum critical issues
        max_high: Maximum high issues
        min_grade: Minimum grade required
        min_score: Minimum score required

    Returns:
        QualityGateConfig for inline evaluation
    """
    # Map fail_on to severity thresholds
    if fail_on:
        fail_on_lower = fail_on.lower()
        if fail_on_lower == "critical":
            max_critical = 0
        elif fail_on_lower == "high":
            max_critical = 0
            max_high = 0
        elif fail_on_lower == "medium":
            max_critical = 0
            max_high = 0
            # Don't set max_medium - original behavior was fail_on >= severity
        elif fail_on_lower == "low":
            max_critical = 0
            max_high = 0
        elif fail_on_lower == "info":
            # Fail on any finding
            max_issues = 0

    conditions = QualityGateConditions(
        max_critical=max_critical,
        max_high=max_high,
        max_total=max_issues,
        min_grade=min_grade,
        min_score=min_score,
    )

    return QualityGateConfig(
        name="inline",
        description="Inline quality gate from CLI parameters",
        conditions=conditions,
        on_fail="block",
    )


def evaluate_inline_gate(
    health: CodebaseHealth,
    fail_on: Optional[str] = None,
    max_issues: Optional[int] = None,
    max_critical: Optional[int] = None,
    max_high: Optional[int] = None,
    min_grade: Optional[str] = None,
    min_score: Optional[float] = None,
    baseline_health: Optional[CodebaseHealth] = None,
) -> QualityGateResult:
    """Evaluate an inline quality gate.

    Args:
        health: Current codebase health
        fail_on: Severity threshold (critical, high, medium, low, info)
        max_issues: Maximum total issues
        max_critical: Maximum critical issues
        max_high: Maximum high issues
        min_grade: Minimum grade
        min_score: Minimum score
        baseline_health: Optional baseline for comparison

    Returns:
        QualityGateResult
    """
    gate = create_inline_gate(
        fail_on=fail_on,
        max_issues=max_issues,
        max_critical=max_critical,
        max_high=max_high,
        min_grade=min_grade,
        min_score=min_score,
    )
    return evaluate_quality_gate(health, gate, baseline_health)
