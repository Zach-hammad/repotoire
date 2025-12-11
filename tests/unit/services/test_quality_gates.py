"""Unit tests for quality gates service."""

import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from repotoire.db.models.analysis import AnalysisRun, AnalysisStatus
from repotoire.db.models.finding import Finding, FindingSeverity
from repotoire.services.github_status import CommitState
from repotoire.services.quality_gates import (
    DEFAULT_QUALITY_GATES,
    QualityGateResult,
    evaluate_quality_gates,
    format_gates_for_response,
    get_finding_counts,
)


@pytest.fixture
def mock_analysis():
    """Create a mock AnalysisRun."""
    analysis = MagicMock(spec=AnalysisRun)
    analysis.id = uuid4()
    analysis.health_score = 75
    analysis.status = AnalysisStatus.COMPLETED
    return analysis


@pytest.fixture
def mock_session():
    """Create a mock SQLAlchemy session."""
    return MagicMock()


class TestEvaluateQualityGates:
    """Tests for evaluate_quality_gates function."""

    def test_disabled_gates_always_pass(self, mock_session, mock_analysis):
        """When gates are disabled, always return success."""
        gates = {"enabled": False}

        result = evaluate_quality_gates(
            session=mock_session,
            quality_gates=gates,
            analysis_run=mock_analysis,
        )

        assert result.passed is True
        assert result.state == CommitState.SUCCESS
        assert "disabled" in result.description.lower()

    def test_no_findings_passes(self, mock_session, mock_analysis):
        """When no findings exist, should pass."""
        # Mock session to return empty counts
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result

        result = evaluate_quality_gates(
            session=mock_session,
            quality_gates=None,  # Uses defaults
            analysis_run=mock_analysis,
        )

        assert result.passed is True
        assert result.state == CommitState.SUCCESS
        assert "No issues" in result.description

    def test_critical_findings_fail_by_default(self, mock_session, mock_analysis):
        """Critical findings should fail with default config."""
        # Mock session to return 2 critical findings
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (FindingSeverity.CRITICAL, 2),
        ]
        mock_session.execute.return_value = mock_result

        result = evaluate_quality_gates(
            session=mock_session,
            quality_gates=None,  # Uses defaults (block_on_critical=True)
            analysis_run=mock_analysis,
        )

        assert result.passed is False
        assert result.state == CommitState.FAILURE
        assert "critical" in result.description.lower()
        assert "2" in result.description

    def test_high_findings_pass_by_default(self, mock_session, mock_analysis):
        """High severity findings should pass with default config."""
        # Mock session to return 5 high findings
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (FindingSeverity.HIGH, 5),
        ]
        mock_session.execute.return_value = mock_result

        result = evaluate_quality_gates(
            session=mock_session,
            quality_gates=None,  # Uses defaults (block_on_high=False)
            analysis_run=mock_analysis,
        )

        assert result.passed is True
        assert result.state == CommitState.SUCCESS

    def test_high_findings_fail_when_configured(self, mock_session, mock_analysis):
        """High severity findings should fail when block_on_high=True."""
        gates = {
            "enabled": True,
            "block_on_critical": True,
            "block_on_high": True,
        }

        # Mock session to return 3 high findings
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (FindingSeverity.HIGH, 3),
        ]
        mock_session.execute.return_value = mock_result

        result = evaluate_quality_gates(
            session=mock_session,
            quality_gates=gates,
            analysis_run=mock_analysis,
        )

        assert result.passed is False
        assert result.state == CommitState.FAILURE
        assert "high" in result.description.lower()

    def test_min_health_score_pass(self, mock_session, mock_analysis):
        """Should pass when health score meets minimum."""
        gates = {
            "enabled": True,
            "block_on_critical": True,
            "min_health_score": 70,
        }

        mock_analysis.health_score = 75  # Above minimum

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result

        result = evaluate_quality_gates(
            session=mock_session,
            quality_gates=gates,
            analysis_run=mock_analysis,
        )

        assert result.passed is True
        assert "75" in result.description

    def test_min_health_score_fail(self, mock_session, mock_analysis):
        """Should fail when health score below minimum."""
        gates = {
            "enabled": True,
            "block_on_critical": True,
            "min_health_score": 80,
        }

        mock_analysis.health_score = 75  # Below minimum

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result

        result = evaluate_quality_gates(
            session=mock_session,
            quality_gates=gates,
            analysis_run=mock_analysis,
        )

        assert result.passed is False
        assert result.state == CommitState.FAILURE
        assert "75" in result.description
        assert "80" in result.description

    def test_max_new_issues_pass(self, mock_session, mock_analysis):
        """Should pass when issues within limit."""
        gates = {
            "enabled": True,
            "block_on_critical": False,
            "max_new_issues": 10,
        }

        mock_result = MagicMock()
        mock_result.all.return_value = [
            (FindingSeverity.MEDIUM, 5),
            (FindingSeverity.LOW, 3),
        ]
        mock_session.execute.return_value = mock_result

        result = evaluate_quality_gates(
            session=mock_session,
            quality_gates=gates,
            analysis_run=mock_analysis,
        )

        assert result.passed is True
        assert "8 issue" in result.description

    def test_max_new_issues_fail(self, mock_session, mock_analysis):
        """Should fail when issues exceed limit."""
        gates = {
            "enabled": True,
            "block_on_critical": False,
            "max_new_issues": 5,
        }

        mock_result = MagicMock()
        mock_result.all.return_value = [
            (FindingSeverity.MEDIUM, 5),
            (FindingSeverity.LOW, 3),
        ]
        mock_session.execute.return_value = mock_result

        result = evaluate_quality_gates(
            session=mock_session,
            quality_gates=gates,
            analysis_run=mock_analysis,
        )

        assert result.passed is False
        assert result.state == CommitState.FAILURE
        assert "8 issues" in result.description
        assert "limit of 5" in result.description

    def test_multiple_failures_combined(self, mock_session, mock_analysis):
        """Multiple failures should be combined in description."""
        gates = {
            "enabled": True,
            "block_on_critical": True,
            "block_on_high": True,
            "min_health_score": 80,
        }

        mock_analysis.health_score = 60

        mock_result = MagicMock()
        mock_result.all.return_value = [
            (FindingSeverity.CRITICAL, 1),
            (FindingSeverity.HIGH, 2),
        ]
        mock_session.execute.return_value = mock_result

        result = evaluate_quality_gates(
            session=mock_session,
            quality_gates=gates,
            analysis_run=mock_analysis,
        )

        assert result.passed is False
        assert result.state == CommitState.FAILURE
        # Should mention at least 2 failures
        assert len(result.details.get("failures", [])) >= 3

    def test_description_truncated_to_140_chars(self, mock_session, mock_analysis):
        """Description should be truncated to GitHub's 140 char limit."""
        gates = {
            "enabled": True,
            "block_on_critical": True,
            "block_on_high": True,
            "min_health_score": 80,
            "max_new_issues": 5,
        }

        mock_analysis.health_score = 60

        mock_result = MagicMock()
        mock_result.all.return_value = [
            (FindingSeverity.CRITICAL, 100),
            (FindingSeverity.HIGH, 200),
            (FindingSeverity.MEDIUM, 300),
        ]
        mock_session.execute.return_value = mock_result

        result = evaluate_quality_gates(
            session=mock_session,
            quality_gates=gates,
            analysis_run=mock_analysis,
        )

        assert len(result.description) <= 140


class TestFormatGatesForResponse:
    """Tests for format_gates_for_response function."""

    def test_none_returns_defaults(self):
        """None input should return default config."""
        result = format_gates_for_response(None)

        assert result == DEFAULT_QUALITY_GATES

    def test_partial_config_merged_with_defaults(self):
        """Partial config should be merged with defaults."""
        partial = {"block_on_high": True}

        result = format_gates_for_response(partial)

        assert result["block_on_high"] is True
        assert result["enabled"] is True  # From defaults
        assert result["block_on_critical"] is True  # From defaults

    def test_full_config_preserved(self):
        """Full config should be preserved."""
        full_config = {
            "enabled": False,
            "block_on_critical": False,
            "block_on_high": True,
            "min_health_score": 50,
            "max_new_issues": 20,
        }

        result = format_gates_for_response(full_config)

        assert result == full_config


class TestQualityGateResult:
    """Tests for QualityGateResult dataclass."""

    def test_success_result(self):
        """Test creating a success result."""
        result = QualityGateResult(
            passed=True,
            state=CommitState.SUCCESS,
            description="Score: 85 | No issues",
        )

        assert result.passed is True
        assert result.state == CommitState.SUCCESS
        assert result.details == {}

    def test_failure_result_with_details(self):
        """Test creating a failure result with details."""
        result = QualityGateResult(
            passed=False,
            state=CommitState.FAILURE,
            description="Failed: 3 critical issues",
            details={
                "failures": ["3 critical issues"],
                "counts": {"critical": 3},
            },
        )

        assert result.passed is False
        assert result.state == CommitState.FAILURE
        assert result.details["failures"] == ["3 critical issues"]
