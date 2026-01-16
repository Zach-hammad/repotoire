"""Tests for jscpd duplicate code detector."""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from repotoire.detectors.jscpd_detector import JscpdDetector
from repotoire.models import Severity


@pytest.fixture
def mock_graph_client():
    """Create a mock graph client."""
    client = MagicMock()
    client.execute_query.return_value = [
        {
            "file_loc": 100,
            "entity_count": 5,
        }
    ]
    return client


@pytest.fixture
def temp_repo():
    """Create a temporary repository with duplicate code files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create TypeScript files with duplicate code
        src_dir = repo_path / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        # File 1 with duplicate function
        (src_dir / "duplicate1.ts").write_text('''
function processItems(items: string[]): string[] {
    const result: string[] = [];
    for (const item of items) {
        if (item.length > 0) {
            const processed = item.trim().toLowerCase();
            if (processed.startsWith('a')) {
                result.push(processed + '_a');
            } else {
                result.push(processed + '_other');
            }
        }
    }
    return result;
}
''')

        # File 2 with same duplicate function
        (src_dir / "duplicate2.ts").write_text('''
function processData(items: string[]): string[] {
    const result: string[] = [];
    for (const item of items) {
        if (item.length > 0) {
            const processed = item.trim().toLowerCase();
            if (processed.startsWith('a')) {
                result.push(processed + '_a');
            } else {
                result.push(processed + '_other');
            }
        }
    }
    return result;
}
''')

        # Also create some Python files
        (src_dir / "unique.py").write_text('''
def unique_function():
    return "unique"
''')

        yield repo_path


class TestJscpdDetector:
    """Test JscpdDetector functionality."""

    def test_detector_initialization(self, mock_graph_client, temp_repo):
        """Test detector can be initialized."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        assert detector.repository_path == temp_repo
        assert detector.min_lines == 5
        assert detector.min_tokens == 50
        assert detector.max_findings == 50
        assert detector.threshold == 10.0

    def test_detector_invalid_path(self, mock_graph_client):
        """Test detector raises error for invalid path."""
        with pytest.raises(ValueError, match="does not exist"):
            JscpdDetector(
                graph_client=mock_graph_client,
                detector_config={"repository_path": "/nonexistent/path"},
            )

    def test_custom_configuration(self, mock_graph_client, temp_repo):
        """Test custom configuration values."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "min_lines": 10,
                "min_tokens": 100,
                "max_findings": 25,
                "threshold": 5.0,
            },
        )

        assert detector.min_lines == 10
        assert detector.min_tokens == 100
        assert detector.max_findings == 25
        assert detector.threshold == 5.0

    def test_default_ignore_patterns(self, mock_graph_client, temp_repo):
        """Test default ignore patterns are set."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # Check some expected ignore patterns
        assert any("node_modules" in p for p in detector.ignore)
        assert any(".venv" in p for p in detector.ignore)
        assert any("__pycache__" in p for p in detector.ignore)
        assert any(".git" in p for p in detector.ignore)

    def test_custom_ignore_patterns(self, mock_graph_client, temp_repo):
        """Test custom ignore patterns."""
        custom_ignore = ["**/custom/**", "**/vendor/**"]

        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "ignore": custom_ignore,
            },
        )

        assert detector.ignore == custom_ignore

    def test_severity_based_on_lines_high(self, mock_graph_client, temp_repo):
        """Test high severity for 50+ line duplicates."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # Large duplicate (50+ lines) should be HIGH
        duplicate = {
            "lines": 55,
            "firstFile": {"name": "file1.ts", "startLoc": {"line": 1}, "endLoc": {"line": 55}},
            "secondFile": {"name": "file2.ts", "startLoc": {"line": 1}, "endLoc": {"line": 55}},
        }

        finding = detector._create_finding(duplicate)
        assert finding.severity == Severity.HIGH

    def test_severity_based_on_lines_medium(self, mock_graph_client, temp_repo):
        """Test medium severity for 20-49 line duplicates."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # Medium duplicate (20-49 lines) should be MEDIUM
        duplicate = {
            "lines": 25,
            "firstFile": {"name": "file1.ts", "startLoc": {"line": 1}, "endLoc": {"line": 25}},
            "secondFile": {"name": "file2.ts", "startLoc": {"line": 1}, "endLoc": {"line": 25}},
        }

        finding = detector._create_finding(duplicate)
        assert finding.severity == Severity.MEDIUM

    def test_severity_based_on_lines_low(self, mock_graph_client, temp_repo):
        """Test low severity for <20 line duplicates."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        # Small duplicate (<20 lines) should be LOW
        duplicate = {
            "lines": 10,
            "firstFile": {"name": "file1.ts", "startLoc": {"line": 1}, "endLoc": {"line": 10}},
            "secondFile": {"name": "file2.ts", "startLoc": {"line": 1}, "endLoc": {"line": 10}},
        }

        finding = detector._create_finding(duplicate)
        assert finding.severity == Severity.LOW

    def test_suggest_fix_large_duplicate(self, mock_graph_client, temp_repo):
        """Test fix suggestion for large duplicates."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        fix = detector._suggest_fix(55, "file1.ts", "file2.ts")
        assert "utility function or class" in fix

    def test_suggest_fix_medium_duplicate(self, mock_graph_client, temp_repo):
        """Test fix suggestion for medium duplicates."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        fix = detector._suggest_fix(25, "file1.ts", "file2.ts")
        assert "helper function" in fix

    def test_suggest_fix_small_duplicate(self, mock_graph_client, temp_repo):
        """Test fix suggestion for small duplicates."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        fix = detector._suggest_fix(10, "file1.ts", "file2.ts")
        assert "common logic" in fix

    def test_estimate_effort_large(self, mock_graph_client, temp_repo):
        """Test effort estimate for large duplicates."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        effort = detector._estimate_effort(55)
        assert "half day" in effort

    def test_estimate_effort_medium(self, mock_graph_client, temp_repo):
        """Test effort estimate for medium duplicates."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        effort = detector._estimate_effort(25)
        assert "1-2 hours" in effort

    def test_estimate_effort_small(self, mock_graph_client, temp_repo):
        """Test effort estimate for small duplicates."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        effort = detector._estimate_effort(10)
        assert "30 minutes" in effort

    @patch("subprocess.run")
    def test_detect_with_findings(self, mock_subprocess, mock_graph_client, temp_repo):
        """Test detection with jscpd duplicates."""
        # Create mock jscpd report
        jscpd_report = {
            "duplicates": [
                {
                    "format": "typescript",
                    "lines": 15,
                    "fragment": "const result = [];",
                    "tokens": 0,
                    "firstFile": {
                        "name": "src/duplicate1.ts",
                        "start": 1,
                        "end": 15,
                        "startLoc": {"line": 1, "column": 0, "position": 0},
                        "endLoc": {"line": 15, "column": 0, "position": 150},
                    },
                    "secondFile": {
                        "name": "src/duplicate2.ts",
                        "start": 1,
                        "end": 15,
                        "startLoc": {"line": 1, "column": 0, "position": 0},
                        "endLoc": {"line": 15, "column": 0, "position": 150},
                    },
                }
            ]
        }

        # Mock subprocess to simulate successful jscpd execution
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.stderr = ""

        # We need to mock the file reading too
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(jscpd_report)

            with patch.object(Path, "exists", return_value=True):
                detector = JscpdDetector(
                    graph_client=mock_graph_client,
                    detector_config={"repository_path": str(temp_repo)},
                )

                # Since we're mocking, we need to simulate the _run_jscpd result
                with patch.object(detector, "_run_jscpd", return_value=jscpd_report["duplicates"]):
                    findings = detector.detect()

                    assert len(findings) == 1
                    finding = findings[0]
                    assert finding.detector == "JscpdDetector"
                    assert "15 lines" in finding.title
                    assert finding.severity == Severity.LOW  # <20 lines

    @patch.object(JscpdDetector, "_run_jscpd")
    def test_detect_no_findings(self, mock_run_jscpd, mock_graph_client, temp_repo):
        """Test detection with no duplicates."""
        mock_run_jscpd.return_value = []

        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        findings = detector.detect()
        assert len(findings) == 0

    def test_create_finding_with_graph_enrichment(self, mock_graph_client, temp_repo):
        """Test finding creation includes graph enrichment."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        duplicate = {
            "lines": 20,
            "firstFile": {"name": "src/file1.ts", "startLoc": {"line": 1}, "endLoc": {"line": 20}},
            "secondFile": {"name": "src/file2.ts", "startLoc": {"line": 1}, "endLoc": {"line": 20}},
        }

        finding = detector._create_finding(duplicate)

        # Check graph context is included
        assert "file1_loc" in finding.graph_context
        assert "file2_loc" in finding.graph_context

    def test_build_description(self, mock_graph_client, temp_repo):
        """Test description building."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        duplicate = {
            "lines": 25,
            "firstFile": {"name": "src/utils.ts", "startLoc": {"line": 10}, "endLoc": {"line": 35}},
            "secondFile": {"name": "src/helpers.ts", "startLoc": {"line": 5}, "endLoc": {"line": 30}},
        }

        graph_data1 = {"file_loc": 100, "entity_count": 5}
        graph_data2 = {"file_loc": 150, "entity_count": 8}

        desc = detector._build_description(duplicate, graph_data1, graph_data2)

        assert "25 lines" in desc
        assert "src/utils.ts" in desc
        assert "src/helpers.ts" in desc
        assert "10-35" in desc
        assert "5-30" in desc
        assert "100 LOC" in desc
        assert "150 LOC" in desc

    def test_incremental_analysis_supported_extensions(self, mock_graph_client, temp_repo):
        """Test incremental analysis filters to supported extensions."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "changed_files": ["src/file.ts", "src/file.tsx", "src/file.mts", "src/file.cts"],
            },
        )

        assert detector.changed_files is not None
        # The detector should support all TypeScript variants
        assert "src/file.ts" in detector.changed_files
        assert "src/file.tsx" in detector.changed_files
        assert "src/file.mts" in detector.changed_files
        assert "src/file.cts" in detector.changed_files

    def test_format_filter(self, mock_graph_client, temp_repo):
        """Test format filter configuration."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "formats": ["python", "typescript"],
            },
        )

        assert detector.formats == ["python", "typescript"]

    def test_max_findings_limit(self, mock_graph_client, temp_repo):
        """Test max_findings configuration limits results."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={
                "repository_path": str(temp_repo),
                "max_findings": 3,
            },
        )

        # Create 5 duplicates
        duplicates = [
            {
                "lines": 10 + i,
                "firstFile": {"name": f"file{i}a.ts", "startLoc": {"line": 1}, "endLoc": {"line": 10 + i}},
                "secondFile": {"name": f"file{i}b.ts", "startLoc": {"line": 1}, "endLoc": {"line": 10 + i}},
            }
            for i in range(5)
        ]

        with patch.object(detector, "_run_jscpd", return_value=duplicates):
            findings = detector.detect()
            assert len(findings) == 3  # Limited to max_findings

    def test_collaboration_metadata(self, mock_graph_client, temp_repo):
        """Test findings include collaboration metadata."""
        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        duplicate = {
            "lines": 25,
            "firstFile": {"name": "file1.ts", "startLoc": {"line": 1}, "endLoc": {"line": 25}},
            "secondFile": {"name": "file2.ts", "startLoc": {"line": 1}, "endLoc": {"line": 25}},
        }

        finding = detector._create_finding(duplicate)

        # Check collaboration metadata
        assert finding.collaboration_metadata is not None
        assert len(finding.collaboration_metadata) > 0
        collab = finding.collaboration_metadata[0]
        assert collab.detector == "JscpdDetector"
        assert "jscpd" in collab.tags
        assert "duplication" in collab.tags

    def test_severity_method(self, mock_graph_client, temp_repo):
        """Test severity method returns finding's severity."""
        from repotoire.models import Finding

        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
        )

        finding = Finding(
            id="test-id",
            detector="JscpdDetector",
            severity=Severity.HIGH,
            title="Test",
            description="Test",
            affected_nodes=[],
            affected_files=["test.ts"],
        )

        assert detector.severity(finding) == Severity.HIGH

    def test_enricher_integration(self, mock_graph_client, temp_repo):
        """Test enricher is called when provided."""
        mock_enricher = MagicMock()

        detector = JscpdDetector(
            graph_client=mock_graph_client,
            detector_config={"repository_path": str(temp_repo)},
            enricher=mock_enricher,
        )

        duplicate = {
            "lines": 25,
            "firstFile": {"name": "file1.ts", "startLoc": {"line": 1}, "endLoc": {"line": 25}},
            "secondFile": {"name": "file2.ts", "startLoc": {"line": 1}, "endLoc": {"line": 25}},
        }

        detector._create_finding(duplicate)

        # Enricher should be called for both file locations
        assert mock_enricher.flag_entity.call_count == 2
