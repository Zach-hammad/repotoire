"""Tests for ESLint hybrid detector."""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from repotoire.detectors.eslint_detector import ESLintDetector
from repotoire.models import Severity


@pytest.fixture
def mock_graph_client():
    """Create a mock graph client."""
    client = MagicMock()
    client.execute_query.return_value = [
        {
            "file_loc": 100,
            "language": "typescript",
            "affected_nodes": ["src/utils.ts::formatDate"],
            "complexities": [5],
        }
    ]
    return client


@pytest.fixture
def temp_repo():
    """Create a temporary repository with TypeScript files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create a simple TypeScript file
        ts_file = repo_path / "src" / "utils.ts"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text('''
function formatDate(date: any): string {
    var result = date.toString();
    console.log(result);
    return result;
}

const unused = "test";
''')

        yield repo_path


class TestESLintDetector:
    """Test ESLintDetector functionality."""

    def test_detector_initialization(self, mock_graph_client, temp_repo):
        """Test detector can be initialized."""
        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        assert detector.repository_path == temp_repo
        assert detector.max_findings == 100
        assert ".ts" in detector.extensions
        assert ".tsx" in detector.extensions

    def test_detector_invalid_path(self, mock_graph_client):
        """Test detector raises error for invalid path."""
        with pytest.raises(ValueError, match="does not exist"):
            ESLintDetector(
                graph_client=mock_graph_client,
                detector_config={"repository_path": "/nonexistent/path"},
            )

    def test_severity_mapping_critical(self, mock_graph_client, temp_repo):
        """Test critical severity rules are mapped correctly."""
        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # Security-critical rules should be critical
        assert detector._get_severity("no-eval", 2) == Severity.CRITICAL
        assert detector._get_severity("security/detect-eval-with-expression", 2) == Severity.CRITICAL

    def test_severity_mapping_high(self, mock_graph_client, temp_repo):
        """Test high severity rules are mapped correctly."""
        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # Error rules should be high
        assert detector._get_severity("no-undef", 2) == Severity.HIGH
        assert detector._get_severity("security/detect-object-injection", 2) == Severity.HIGH

    def test_severity_mapping_medium(self, mock_graph_client, temp_repo):
        """Test medium severity rules are mapped correctly."""
        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # TypeScript and best practice rules
        assert detector._get_severity("@typescript-eslint/no-explicit-any", 2) == Severity.MEDIUM
        assert detector._get_severity("eqeqeq", 2) == Severity.MEDIUM

    def test_severity_mapping_low(self, mock_graph_client, temp_repo):
        """Test low severity rules are mapped correctly."""
        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # Unused vars and style rules
        assert detector._get_severity("no-unused-vars", 2) == Severity.LOW
        assert detector._get_severity("prefer-const", 2) == Severity.INFO

    def test_get_tag_from_rule(self, mock_graph_client, temp_repo):
        """Test rule-to-tag mapping."""
        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        assert detector._get_tag_from_rule("security/detect-eval") == "security"
        assert detector._get_tag_from_rule("@typescript-eslint/no-unused-vars") == "unused_code"
        assert detector._get_tag_from_rule("@typescript-eslint/no-explicit-any") == "type_safety"
        assert detector._get_tag_from_rule("import/order") == "imports"
        assert detector._get_tag_from_rule("react/jsx-key") == "react"
        assert detector._get_tag_from_rule("semi") == "style"

    def test_suggest_fix_with_autofix(self, mock_graph_client, temp_repo):
        """Test fix suggestion when ESLint can auto-fix."""
        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        fix = {"range": [0, 10], "text": "fixed"}
        suggestion = detector._suggest_fix("semi", "Missing semicolon", fix)
        assert "npx eslint --fix" in suggestion

    def test_suggest_fix_manual(self, mock_graph_client, temp_repo):
        """Test fix suggestion for manual fixes."""
        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        suggestion = detector._suggest_fix("no-eval", "eval is dangerous", None)
        assert "Replace eval()" in suggestion

    @patch("repotoire.detectors.eslint_detector.run_js_tool")
    def test_detect_with_findings(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test detection with ESLint findings."""
        # Mock ESLint output
        eslint_output = [
            {
                "filePath": str(temp_repo / "src/utils.ts"),
                "messages": [
                    {
                        "ruleId": "@typescript-eslint/no-explicit-any",
                        "severity": 2,
                        "message": "Unexpected any. Specify a different type.",
                        "line": 1,
                        "column": 27,
                        "endLine": 1,
                        "endColumn": 30,
                    },
                    {
                        "ruleId": "no-var",
                        "severity": 1,
                        "message": "Unexpected var, use let or const instead.",
                        "line": 2,
                        "column": 5,
                        "endLine": 2,
                        "endColumn": 8,
                    },
                ],
                "errorCount": 1,
                "warningCount": 1,
            }
        ]

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = json.dumps(eslint_output)
        mock_run_tool.return_value = mock_result

        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()

        assert len(findings) == 2

        # Check first finding
        any_finding = findings[0]
        assert any_finding.detector == "ESLintDetector"
        assert "@typescript-eslint/no-explicit-any" in any_finding.title
        assert any_finding.severity == Severity.MEDIUM
        assert any_finding.language == "typescript"

        # Check second finding
        var_finding = findings[1]
        assert "no-var" in var_finding.title
        assert var_finding.severity == Severity.LOW

    @patch("repotoire.detectors.eslint_detector.run_js_tool")
    def test_detect_no_findings(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test detection with no ESLint findings."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = "[]"
        mock_run_tool.return_value = mock_result

        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()
        assert len(findings) == 0

    @patch("repotoire.detectors.eslint_detector.run_js_tool")
    def test_detect_timeout(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test detection handles timeout gracefully."""
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.timed_out = True
        mock_result.stdout = ""
        mock_run_tool.return_value = mock_result

        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()
        assert len(findings) == 0

    @patch("repotoire.detectors.eslint_detector.run_js_tool")
    def test_incremental_analysis(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test incremental analysis with changed_files."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = "[]"
        mock_run_tool.return_value = mock_result

        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "changed_files": ["src/utils.ts"],
            },
        )

        detector.detect()

        # Verify run_js_tool was called with the changed file
        call_args = mock_run_tool.call_args
        args = call_args.kwargs["args"]
        assert "src/utils.ts" in args

    def test_max_findings_limit(self, mock_graph_client, temp_repo):
        """Test max_findings configuration."""
        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "max_findings": 5,
            },
        )

        assert detector.max_findings == 5

    def test_custom_extensions(self, mock_graph_client, temp_repo):
        """Test custom extensions configuration."""
        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "extensions": [".ts", ".vue"],
            },
        )

        assert ".ts" in detector.extensions
        assert ".vue" in detector.extensions
        assert ".jsx" not in detector.extensions

    def test_build_description(self, mock_graph_client, temp_repo):
        """Test description building."""
        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        message = {
            "ruleId": "@typescript-eslint/no-explicit-any",
            "message": "Unexpected any. Specify a different type.",
            "line": 10,
            "column": 5,
        }

        graph_data = {
            "file_loc": 100,
            "complexity": 5,
            "nodes": ["utils.ts::formatDate"],
        }

        desc = detector._build_description("src/utils.ts", message, graph_data)

        assert "Unexpected any" in desc
        assert "src/utils.ts:10:5" in desc
        assert "@typescript-eslint/no-explicit-any" in desc
        assert "typescript-eslint.io" in desc  # TS-ESLint doc link
        assert "100 LOC" in desc
        assert "**Complexity**: 5" in desc

    def test_severity_method(self, mock_graph_client, temp_repo):
        """Test severity method returns finding's severity."""
        from repotoire.models import Finding

        detector = ESLintDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        finding = Finding(
            id="test-id",
            detector="ESLintDetector",
            severity=Severity.HIGH,
            title="Test",
            description="Test",
            affected_nodes=[],
            affected_files=["test.ts"],
        )

        assert detector.severity(finding) == Severity.HIGH
