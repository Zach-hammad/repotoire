"""Tests for TypeScript Compiler (tsc) detector."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from repotoire.detectors.tsc_detector import TscDetector
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

        # Create a TypeScript file with type errors
        ts_file = repo_path / "src" / "utils.ts"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text('''
function formatDate(date: string): number {
    return date;  // Type error: string not assignable to number
}

const unused: any = "test";
const x: unknown = {};
x.property;  // Error: Object is of type 'unknown'
''')

        yield repo_path


class TestTscDetector:
    """Test TscDetector functionality."""

    def test_detector_initialization(self, mock_graph_client, temp_repo):
        """Test detector can be initialized."""
        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        assert detector.repository_path == temp_repo
        assert detector.max_findings == 100
        assert detector.strict is True

    def test_detector_invalid_path(self, mock_graph_client):
        """Test detector raises error for invalid path."""
        with pytest.raises(ValueError, match="does not exist"):
            TscDetector(
                graph_client=mock_graph_client,
                detector_config={"repository_path": "/nonexistent/path"},
            )

    def test_severity_mapping_high(self, mock_graph_client, temp_repo):
        """Test high severity error codes."""
        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # Cannot find name, module errors
        assert detector._get_severity("TS2304") == Severity.HIGH
        assert detector._get_severity("TS2305") == Severity.HIGH
        assert detector._get_severity("TS2307") == Severity.HIGH

    def test_severity_mapping_medium(self, mock_graph_client, temp_repo):
        """Test medium severity error codes."""
        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # Type assignability errors
        assert detector._get_severity("TS2322") == Severity.MEDIUM
        assert detector._get_severity("TS2339") == Severity.MEDIUM
        assert detector._get_severity("TS2345") == Severity.MEDIUM
        assert detector._get_severity("TS2531") == Severity.MEDIUM
        assert detector._get_severity("TS2532") == Severity.MEDIUM

    def test_severity_mapping_low(self, mock_graph_client, temp_repo):
        """Test low severity error codes."""
        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # Unused and implicit any errors
        assert detector._get_severity("TS6133") == Severity.LOW
        assert detector._get_severity("TS7006") == Severity.LOW
        assert detector._get_severity("TS7016") == Severity.LOW

    def test_severity_mapping_unknown(self, mock_graph_client, temp_repo):
        """Test unknown error code defaults to medium."""
        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        assert detector._get_severity("TS9999") == Severity.MEDIUM

    def test_get_tag_from_code(self, mock_graph_client, temp_repo):
        """Test error code to tag mapping."""
        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        assert detector._get_tag_from_code("TS1005") == "syntax"
        assert detector._get_tag_from_code("TS2322") == "type_error"
        assert detector._get_tag_from_code("TS4001") == "semantic"
        assert detector._get_tag_from_code("TS6133") == "declaration"
        assert detector._get_tag_from_code("TS7006") == "suggestion"

    def test_suggest_fix(self, mock_graph_client, temp_repo):
        """Test fix suggestions for common errors."""
        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # Known error codes should have specific fixes
        assert "Import or declare" in detector._suggest_fix("TS2304", "Cannot find name")
        assert "module" in detector._suggest_fix("TS2307", "Cannot find module")
        assert "null check" in detector._suggest_fix("TS2531", "Object is possibly null")
        assert "undefined check" in detector._suggest_fix("TS2532", "Object is possibly undefined")
        assert "type annotation" in detector._suggest_fix("TS7006", "Parameter has any type")

        # Unknown error codes should return generic fix
        assert "Review TypeScript error" in detector._suggest_fix("TS9999", "Unknown error")

    @patch("repotoire.detectors.tsc_detector.run_js_tool")
    def test_detect_with_findings(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test detection with tsc errors."""
        # Mock tsc output
        tsc_output = f"""{temp_repo}/src/utils.ts(2,5): error TS2322: Type 'string' is not assignable to type 'number'.
{temp_repo}/src/utils.ts(7,1): error TS2571: Object is of type 'unknown'.
"""

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = tsc_output
        mock_result.stderr = ""
        mock_run_tool.return_value = mock_result

        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()

        assert len(findings) == 2

        # Check first finding (type assignment error)
        type_finding = findings[0]
        assert type_finding.detector == "TscDetector"
        assert "TS2322" in type_finding.title
        assert type_finding.severity == Severity.MEDIUM
        assert type_finding.language == "typescript"

        # Check second finding (unknown type)
        unknown_finding = findings[1]
        assert "TS2571" in unknown_finding.title
        assert unknown_finding.severity == Severity.MEDIUM

    @patch("repotoire.detectors.tsc_detector.run_js_tool")
    def test_detect_no_findings(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test detection with no tsc errors."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_run_tool.return_value = mock_result

        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()
        assert len(findings) == 0

    @patch("repotoire.detectors.tsc_detector.run_js_tool")
    def test_detect_timeout(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test detection handles timeout gracefully."""
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.timed_out = True
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_run_tool.return_value = mock_result

        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()
        assert len(findings) == 0

    @patch("repotoire.detectors.tsc_detector.run_js_tool")
    def test_incremental_analysis(self, mock_run_tool, mock_graph_client, temp_repo):
        """Test incremental analysis with changed_files."""
        # Only errors in changed files should be reported
        tsc_output = f"""{temp_repo}/src/utils.ts(2,5): error TS2322: Type 'string' is not assignable to type 'number'.
{temp_repo}/src/other.ts(5,1): error TS2304: Cannot find name 'foo'.
"""

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.timed_out = False
        mock_result.stdout = tsc_output
        mock_result.stderr = ""
        mock_run_tool.return_value = mock_result

        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "changed_files": ["src/utils.ts"],
            },
        )

        findings = detector.detect()

        # Only the error in the changed file should be reported
        assert len(findings) == 1
        assert "src/utils.ts" in findings[0].affected_files[0]

    def test_max_findings_limit(self, mock_graph_client, temp_repo):
        """Test max_findings configuration."""
        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "max_findings": 5,
            },
        )

        assert detector.max_findings == 5

    def test_strict_mode_disabled(self, mock_graph_client, temp_repo):
        """Test strict mode can be disabled."""
        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "strict": False,
            },
        )

        assert detector.strict is False

    def test_build_description(self, mock_graph_client, temp_repo):
        """Test description building."""
        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        error = {
            "file": "src/utils.ts",
            "line": 10,
            "column": 5,
            "code": "TS2322",
            "message": "Type 'string' is not assignable to type 'number'.",
        }

        graph_data = {
            "file_loc": 100,
            "complexity": 5,
            "nodes": ["utils.ts::formatDate"],
        }

        desc = detector._build_description(error, graph_data)

        assert "Type 'string' is not assignable to type 'number'" in desc
        assert "src/utils.ts:10:5" in desc
        assert "TS2322" in desc
        assert "100 LOC" in desc
        assert "**Complexity**: 5" in desc

    def test_no_ts_files_skips(self, mock_graph_client):
        """Test detector skips when no TypeScript files exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / "file.py").write_text("print('hello')")

            detector = TscDetector(
                graph_client=mock_graph_client,
                detector_config={"repository_path": str(repo_path)},
            )

            findings = detector.detect()
            assert len(findings) == 0

    def test_tsconfig_detection(self, mock_graph_client, temp_repo):
        """Test tsconfig.json detection."""
        # Create a tsconfig.json
        (temp_repo / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}}')

        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # tsconfig_path should not be explicitly set (uses auto-detection)
        assert detector.tsconfig_path is None

    def test_custom_tsconfig_path(self, mock_graph_client, temp_repo):
        """Test custom tsconfig path configuration."""
        tsconfig_path = str(temp_repo / "custom-tsconfig.json")
        (temp_repo / "custom-tsconfig.json").write_text('{"compilerOptions": {}}')

        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "tsconfig_path": tsconfig_path,
            },
        )

        assert detector.tsconfig_path == tsconfig_path

    def test_severity_method(self, mock_graph_client, temp_repo):
        """Test severity method returns finding's severity."""
        from repotoire.models import Finding

        detector = TscDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        finding = Finding(
            id="test-id",
            detector="TscDetector",
            severity=Severity.HIGH,
            title="Test",
            description="Test",
            affected_nodes=[],
            affected_files=["test.ts"],
        )

        assert detector.severity(finding) == Severity.HIGH
