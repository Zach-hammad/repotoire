"""Integration tests for GitHub PR analyzer."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from repotoire.github.pr_analyzer import (
    parse_severity,
    format_pr_comment,
    main,
)
from repotoire.models import Severity, Finding, CodebaseHealth, MetricsBreakdown, FindingsSummary


class TestParseSeverity:
    """Test parse_severity function."""

    def test_parse_severity_all_levels(self):
        """Test parsing all severity levels."""
        assert parse_severity("critical") == Severity.CRITICAL
        assert parse_severity("high") == Severity.HIGH
        assert parse_severity("medium") == Severity.MEDIUM
        assert parse_severity("low") == Severity.LOW
        assert parse_severity("info") == Severity.INFO

    def test_parse_severity_case_insensitive(self):
        """Test case insensitive parsing."""
        assert parse_severity("CRITICAL") == Severity.CRITICAL
        assert parse_severity("HiGh") == Severity.HIGH

    def test_parse_severity_unknown(self):
        """Test unknown severity defaults to critical."""
        assert parse_severity("unknown") == Severity.CRITICAL


class TestFormatPRComment:
    """Test format_pr_comment function."""

    def test_format_comment_no_findings(self):
        """Test comment with no findings."""
        metrics = MetricsBreakdown(
            modularity=0.5,
            avg_coupling=2.0,
            circular_dependencies=0,
            bottleneck_count=0,
            dead_code_percentage=0.0,
            duplication_percentage=0.0,
            god_class_count=0,
            layer_violations=0,
            boundary_violations=0,
            abstraction_ratio=0.3,
            total_files=10,
            total_classes=20,
            total_functions=50,
            total_loc=1000,
        )
        summary = FindingsSummary(critical=0, high=0, medium=0, low=0, info=0)

        health = CodebaseHealth(
            grade="A",
            overall_score=95,
            structure_score=95,
            quality_score=95,
            architecture_score=95,
            metrics=metrics,
            findings_summary=summary,
            findings=[],
        )

        comment = format_pr_comment(health, Severity.CRITICAL, [])

        assert "No Issues Found" in comment
        assert "Health Score**: 95/100" in comment

    def test_format_comment_with_findings(self):
        """Test comment with findings."""
        findings = [
            Finding(
                id="test-1",
                detector="TestDetector",
                title="Critical Issue",
                description="This is critical",
                severity=Severity.CRITICAL,
                affected_nodes=["test.py::func"],
                affected_files=["test.py"],
                suggested_fix="Fix it now",
            ),
            Finding(
                id="test-2",
                detector="TestDetector",
                title="Medium Issue",
                description="This is medium",
                severity=Severity.MEDIUM,
                affected_nodes=["test.py::other"],
                affected_files=["test.py"],
            ),
        ]

        metrics = MetricsBreakdown(
            modularity=0.5,
            avg_coupling=2.0,
            circular_dependencies=0,
            bottleneck_count=0,
            dead_code_percentage=0.0,
            duplication_percentage=0.0,
            god_class_count=0,
            layer_violations=0,
            boundary_violations=0,
            abstraction_ratio=0.3,
            total_files=10,
            total_classes=20,
            total_functions=50,
            total_loc=1000,
        )
        summary = FindingsSummary(critical=1, high=0, medium=1, low=0, info=0)

        health = CodebaseHealth(
            grade="C",
            overall_score=70,
            structure_score=70,
            quality_score=70,
            architecture_score=70,
            metrics=metrics,
            findings_summary=summary,
            findings=findings,
        )

        comment = format_pr_comment(health, Severity.CRITICAL, ["test.py"])

        assert "Found 2 issue(s)" in comment
        assert "Critical Issue" in comment
        # Medium issues are counted but not shown in detail (only critical/high)
        assert "🟡 **Medium**: 1" in comment
        assert "Fix it now" in comment
        assert "🔴" in comment  # Critical icon

    def test_format_comment_check_failed(self):
        """Test comment when check fails."""
        findings = [
            Finding(
                id="test-1",
                detector="TestDetector",
                title="Critical Issue",
                description="This is critical",
                severity=Severity.CRITICAL,
                affected_nodes=["test.py::func"],
                affected_files=["test.py"],
            ),
        ]

        metrics = MetricsBreakdown(
            modularity=0.3,
            avg_coupling=5.0,
            circular_dependencies=3,
            bottleneck_count=2,
            dead_code_percentage=0.2,
            duplication_percentage=0.1,
            god_class_count=2,
            layer_violations=5,
            boundary_violations=3,
            abstraction_ratio=0.1,
            total_files=10,
            total_classes=20,
            total_functions=50,
            total_loc=1000,
        )
        summary = FindingsSummary(critical=1, high=0, medium=0, low=0, info=0)

        health = CodebaseHealth(
            grade="F",
            overall_score=50,
            structure_score=50,
            quality_score=50,
            architecture_score=50,
            metrics=metrics,
            findings_summary=summary,
            findings=findings,
        )

        comment = format_pr_comment(health, Severity.CRITICAL, ["test.py"])

        assert "❌ Check Failed" in comment
        assert "Please fix these issues before merging" in comment

    def test_format_comment_check_passed(self):
        """Test comment when check passes."""
        findings = [
            Finding(
                id="test-1",
                detector="TestDetector",
                title="Low Issue",
                description="This is low",
                severity=Severity.LOW,
                affected_nodes=["test.py::func"],
                affected_files=["test.py"],
            ),
        ]

        metrics = MetricsBreakdown(
            modularity=0.6,
            avg_coupling=2.5,
            circular_dependencies=0,
            bottleneck_count=1,
            dead_code_percentage=0.05,
            duplication_percentage=0.03,
            god_class_count=0,
            layer_violations=1,
            boundary_violations=0,
            abstraction_ratio=0.4,
            total_files=10,
            total_classes=20,
            total_functions=50,
            total_loc=1000,
        )
        summary = FindingsSummary(critical=0, high=0, medium=0, low=1, info=0)

        health = CodebaseHealth(
            grade="B",
            overall_score=85,
            structure_score=85,
            quality_score=85,
            architecture_score=85,
            metrics=metrics,
            findings_summary=summary,
            findings=findings,
        )

        comment = format_pr_comment(health, Severity.CRITICAL, ["test.py"])

        assert "✅ Check Passed" in comment
        assert "below the" in comment

    def test_format_comment_filters_files(self):
        """Test that comment filters for specific files."""
        findings = [
            Finding(
                id="test-1",
                detector="TestDetector",
                title="Issue in file A",
                description="This is in A",
                severity=Severity.HIGH,
                affected_nodes=["a.py::func"],
                affected_files=["a.py"],
            ),
            Finding(
                id="test-2",
                detector="TestDetector",
                title="Issue in file B",
                description="This is in B",
                severity=Severity.HIGH,
                affected_nodes=["b.py::func"],
                affected_files=["b.py"],
            ),
        ]

        metrics = MetricsBreakdown(
            modularity=0.5,
            avg_coupling=3.0,
            circular_dependencies=1,
            bottleneck_count=1,
            dead_code_percentage=0.1,
            duplication_percentage=0.05,
            god_class_count=1,
            layer_violations=2,
            boundary_violations=1,
            abstraction_ratio=0.3,
            total_files=10,
            total_classes=20,
            total_functions=50,
            total_loc=1000,
        )
        summary = FindingsSummary(critical=0, high=2, medium=0, low=0, info=0)

        health = CodebaseHealth(
            grade="C",
            overall_score=75,
            structure_score=75,
            quality_score=75,
            architecture_score=75,
            metrics=metrics,
            findings_summary=summary,
            findings=findings,
        )

        # Only include a.py
        comment = format_pr_comment(health, Severity.CRITICAL, ["a.py"])

        assert "Issue in file A" in comment
        assert "Issue in file B" not in comment


class TestMainFunction:
    """Test main function."""

    def test_main_missing_neo4j_password(self):
        """Test that main fails without Neo4j password."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("sys.argv", [
                "pr_analyzer",
                "--repo-path", tmpdir,
                "--output", f"{tmpdir}/output.json",
                "--pr-comment", f"{tmpdir}/comment.md",
            ]):
                with patch.dict("os.environ", {}, clear=True):
                    result = main()
                    assert result == 1

    def test_main_with_env_password(self):
        """Test that main accepts password from environment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("sys.argv", [
                "pr_analyzer",
                "--repo-path", tmpdir,
                "--output", f"{tmpdir}/output.json",
                "--pr-comment", f"{tmpdir}/comment.md",
            ]):
                with patch.dict("os.environ", {"REPOTOIRE_NEO4J_PASSWORD": "test"}):
                    with patch("repotoire.github.pr_analyzer.Neo4jClient"):
                        # This will fail at pipeline step, but password check passes
                        result = main()
