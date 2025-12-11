"""Unit tests for GitHub PR commenter service."""

import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from repotoire.db.models.analysis import AnalysisRun, AnalysisStatus
from repotoire.db.models.finding import Finding, FindingSeverity
from repotoire.github.pr_commenter import (
    COMMENT_MARKER,
    format_pr_comment,
    get_new_findings,
)


@pytest.fixture
def mock_analysis():
    """Create a mock AnalysisRun."""
    analysis = MagicMock(spec=AnalysisRun)
    analysis.id = uuid4()
    analysis.health_score = 75
    analysis.structure_score = 80
    analysis.quality_score = 70
    analysis.architecture_score = 75
    analysis.score_delta = -5
    analysis.findings_count = 3
    analysis.files_analyzed = 10
    analysis.status = AnalysisStatus.COMPLETED
    return analysis


@pytest.fixture
def mock_findings():
    """Create mock findings."""
    findings = []
    for i, (severity, title, file) in enumerate([
        (FindingSeverity.CRITICAL, "SQL injection vulnerability", "src/auth/login.py"),
        (FindingSeverity.HIGH, "High complexity function", "src/utils/parser.py"),
        (FindingSeverity.MEDIUM, "Missing type hints", "src/api/handlers.py"),
    ]):
        finding = MagicMock(spec=Finding)
        finding.id = uuid4()
        finding.severity = severity
        finding.title = title
        finding.affected_files = [file]
        finding.line_start = 10 + i * 20
        finding.detector = "test_detector"
        findings.append(finding)
    return findings


class TestFormatPrComment:
    """Tests for format_pr_comment function."""

    def test_comment_includes_marker(self, mock_analysis, mock_findings):
        """Comment should include unique marker for updates."""
        comment = format_pr_comment(
            analysis=mock_analysis,
            new_findings=mock_findings,
            base_score=80,
            dashboard_url="https://app.repotoire.io/repos/123",
        )

        assert COMMENT_MARKER in comment

    def test_comment_shows_health_score(self, mock_analysis, mock_findings):
        """Comment should display health score."""
        comment = format_pr_comment(
            analysis=mock_analysis,
            new_findings=mock_findings,
            base_score=80,
            dashboard_url="https://app.repotoire.io/repos/123",
        )

        assert "75/100" in comment
        assert "▼ -5" in comment  # Score dropped from 80 to 75

    def test_comment_shows_score_improvement(self, mock_analysis, mock_findings):
        """Comment should show positive trend when score improves."""
        mock_analysis.health_score = 85
        comment = format_pr_comment(
            analysis=mock_analysis,
            new_findings=mock_findings,
            base_score=80,
            dashboard_url="https://app.repotoire.io/repos/123",
        )

        assert "▲ +5" in comment

    def test_comment_groups_by_severity(self, mock_analysis, mock_findings):
        """Comment should group findings by severity."""
        comment = format_pr_comment(
            analysis=mock_analysis,
            new_findings=mock_findings,
            base_score=80,
            dashboard_url="https://app.repotoire.io/repos/123",
        )

        assert "🔴 Critical" in comment
        assert "🟠 High" in comment
        assert "🟡 Medium" in comment

    def test_comment_shows_file_and_issue(self, mock_analysis, mock_findings):
        """Comment should show file path and issue title."""
        comment = format_pr_comment(
            analysis=mock_analysis,
            new_findings=mock_findings,
            base_score=80,
            dashboard_url="https://app.repotoire.io/repos/123",
        )

        assert "src/auth/login.py" in comment
        assert "SQL injection vulnerability" in comment

    def test_no_findings_shows_success_message(self, mock_analysis):
        """Comment should show success when no new issues."""
        comment = format_pr_comment(
            analysis=mock_analysis,
            new_findings=[],
            base_score=70,
            dashboard_url="https://app.repotoire.io/repos/123",
        )

        assert "No new issues found" in comment
        assert "Great job" in comment

    def test_comment_limits_to_10_findings(self, mock_analysis):
        """Comment should limit displayed findings to 10."""
        many_findings = []
        for i in range(15):
            finding = MagicMock(spec=Finding)
            finding.id = uuid4()
            finding.severity = FindingSeverity.MEDIUM
            finding.title = f"Issue {i}"
            finding.affected_files = [f"src/file{i}.py"]
            finding.line_start = i * 10
            many_findings.append(finding)

        comment = format_pr_comment(
            analysis=mock_analysis,
            new_findings=many_findings,
            base_score=80,
            dashboard_url="https://app.repotoire.io/repos/123",
        )

        # Should show "and 5 more issues"
        assert "and 5 more issues" in comment

    def test_comment_includes_dashboard_link(self, mock_analysis, mock_findings):
        """Comment should link to full dashboard report."""
        dashboard_url = "https://app.repotoire.io/repos/abc123/analysis/def456"
        comment = format_pr_comment(
            analysis=mock_analysis,
            new_findings=mock_findings,
            base_score=80,
            dashboard_url=dashboard_url,
        )

        assert dashboard_url in comment
        assert "View full report" in comment

    def test_no_base_score_shows_score_without_delta(self, mock_analysis, mock_findings):
        """When no base score, show score without trend indicator."""
        comment = format_pr_comment(
            analysis=mock_analysis,
            new_findings=mock_findings,
            base_score=None,
            dashboard_url="https://app.repotoire.io/repos/123",
        )

        assert "75/100" in comment
        # Should not show trend indicator
        assert "▲" not in comment
        assert "▼" not in comment
        assert "from base" not in comment


class TestGetNewFindings:
    """Tests for get_new_findings function."""

    def test_all_findings_new_when_no_base(self):
        """All findings should be returned when no base analysis."""
        mock_session = MagicMock()
        head_analysis_id = uuid4()

        # Create mock findings
        findings = []
        for i in range(3):
            finding = MagicMock()
            finding.severity = FindingSeverity.MEDIUM
            finding.detector = "test"
            finding.title = f"Issue {i}"
            finding.affected_files = [f"file{i}.py"]
            findings.append(finding)

        # Mock session.execute to return findings
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = findings
        mock_session.execute.return_value = mock_result

        result = get_new_findings(
            session=mock_session,
            head_analysis_id=head_analysis_id,
            base_analysis_id=None,
        )

        assert len(result) == 3

    def test_filters_existing_findings(self):
        """Findings present in base should be filtered out."""
        mock_session = MagicMock()
        head_analysis_id = uuid4()
        base_analysis_id = uuid4()

        # Head findings - 3 total
        head_findings = []
        for i in range(3):
            finding = MagicMock()
            finding.detector = "test"
            finding.title = f"Issue {i}"
            finding.affected_files = [f"file{i}.py"]
            head_findings.append(finding)

        # Base findings - only has Issue 0 and Issue 1
        base_findings = []
        for i in range(2):
            finding = MagicMock()
            finding.detector = "test"
            finding.title = f"Issue {i}"
            finding.affected_files = [f"file{i}.py"]
            base_findings.append(finding)

        # Mock session.execute - first call returns head, second returns base
        def mock_execute(query):
            result = MagicMock()
            # Check if query is for head or base by examining the call
            if mock_session.execute.call_count == 1:
                result.scalars.return_value.all.return_value = head_findings
            else:
                result.scalars.return_value.all.return_value = base_findings
            return result

        mock_session.execute.side_effect = mock_execute

        result = get_new_findings(
            session=mock_session,
            head_analysis_id=head_analysis_id,
            base_analysis_id=base_analysis_id,
        )

        # Only Issue 2 should be new
        assert len(result) == 1
        assert result[0].title == "Issue 2"
