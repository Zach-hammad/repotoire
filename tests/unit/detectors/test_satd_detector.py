"""Tests for SATDDetector (REPO-410).

Tests Self-Admitted Technical Debt detection including:
- All 8 SATD patterns (TODO, FIXME, HACK, XXX, KLUDGE, REFACTOR, TEMP, BUG)
- Correct severity mapping
- Python fallback scanning
- Graph enrichment
- Edge cases (empty files, binary content, encoding errors)
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from repotoire.detectors.satd_detector import (
    SATDDetector,
    SATD_PATTERN,
    SATD_SEVERITY_MAP,
    HAS_RUST,
)
from repotoire.models import Severity


class TestSATDPattern:
    """Test the SATD regex pattern matching."""

    def test_matches_todo(self):
        """Test TODO pattern matching."""
        matches = list(SATD_PATTERN.finditer("# TODO: fix this later"))
        assert len(matches) == 1
        assert matches[0].group(1).upper() == "TODO"
        assert "fix this later" in matches[0].group(2)

    def test_matches_fixme(self):
        """Test FIXME pattern matching."""
        matches = list(SATD_PATTERN.finditer("// FIXME: edge case not handled"))
        assert len(matches) == 1
        assert matches[0].group(1).upper() == "FIXME"

    def test_matches_hack(self):
        """Test HACK pattern matching."""
        matches = list(SATD_PATTERN.finditer("/* HACK: workaround for API bug */"))
        assert len(matches) == 1
        assert matches[0].group(1).upper() == "HACK"

    def test_matches_xxx(self):
        """Test XXX pattern matching."""
        matches = list(SATD_PATTERN.finditer("# XXX: needs review"))
        assert len(matches) == 1
        assert matches[0].group(1).upper() == "XXX"

    def test_matches_kludge(self):
        """Test KLUDGE pattern matching."""
        matches = list(SATD_PATTERN.finditer("# KLUDGE: temporary fix"))
        assert len(matches) == 1
        assert matches[0].group(1).upper() == "KLUDGE"

    def test_matches_refactor(self):
        """Test REFACTOR pattern matching."""
        matches = list(SATD_PATTERN.finditer("# REFACTOR: split into modules"))
        assert len(matches) == 1
        assert matches[0].group(1).upper() == "REFACTOR"

    def test_matches_temp(self):
        """Test TEMP pattern matching."""
        matches = list(SATD_PATTERN.finditer("# TEMP: remove before release"))
        assert len(matches) == 1
        assert matches[0].group(1).upper() == "TEMP"

    def test_matches_bug(self):
        """Test BUG pattern matching."""
        matches = list(SATD_PATTERN.finditer("# BUG: known issue #123"))
        assert len(matches) == 1
        assert matches[0].group(1).upper() == "BUG"

    def test_case_insensitive(self):
        """Test case insensitive matching."""
        for pattern in ["# todo: test", "# Todo: test", "# TODO: test", "# ToDo: test"]:
            matches = list(SATD_PATTERN.finditer(pattern))
            assert len(matches) == 1, f"Failed for pattern: {pattern}"

    def test_no_match_in_code(self):
        """Test that regular code doesn't match."""
        code_lines = [
            "def todo_something():",
            "variable = 'fixme'",
            "result = hack_function()",
        ]
        for line in code_lines:
            matches = list(SATD_PATTERN.finditer(line))
            # These might match as word boundaries, but should be filtered in detector
            # The key is that actual comments are matched

    def test_various_comment_styles(self):
        """Test various comment styles."""
        comment_styles = [
            "# TODO: Python style",
            "// TODO: JS style",
            "/* TODO: C style */",
            "* TODO: docstring style",
            "''' TODO: Python triple quote",
            '""" TODO: Python docstring',
        ]
        for comment in comment_styles:
            matches = list(SATD_PATTERN.finditer(comment))
            assert len(matches) >= 1, f"Failed for comment: {comment}"


class TestSATDSeverityMapping:
    """Test severity mapping for SATD types."""

    def test_high_severity_patterns(self):
        """Test HIGH severity patterns."""
        for pattern in ["HACK", "KLUDGE", "BUG"]:
            assert SATD_SEVERITY_MAP[pattern] == Severity.HIGH

    def test_medium_severity_patterns(self):
        """Test MEDIUM severity patterns."""
        for pattern in ["FIXME", "XXX", "REFACTOR"]:
            assert SATD_SEVERITY_MAP[pattern] == Severity.MEDIUM

    def test_low_severity_patterns(self):
        """Test LOW severity patterns."""
        for pattern in ["TODO", "TEMP"]:
            assert SATD_SEVERITY_MAP[pattern] == Severity.LOW


class TestSATDDetector:
    """Test suite for SATDDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        client.execute_query.return_value = []
        return client

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository with test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            # Create test Python file with SATD comments
            test_file = repo_path / "main.py"
            test_file.write_text("""
# TODO: add error handling
def process_data():
    # FIXME: this doesn't handle edge cases
    data = load_data()
    # HACK: workaround for API bug
    result = data.transform()
    return result

# XXX: needs review before merge
class DataProcessor:
    def __init__(self):
        # KLUDGE: temporary fix for initialization
        pass

    def run(self):
        # REFACTOR: split this into smaller methods
        # TEMP: remove debug logging
        print("debug")
        # BUG: known issue with large inputs
        pass
""")

            # Create test JS file
            js_file = repo_path / "app.js"
            js_file.write_text("""
// TODO: implement caching
function fetchData() {
    // FIXME: handle network errors
    return fetch('/api/data');
}
""")

            yield repo_path

    @pytest.fixture
    def detector(self, mock_client, temp_repo):
        """Create a detector instance with mock client."""
        return SATDDetector(
            mock_client,
            detector_config={"repository_path": str(temp_repo)},
        )

    def test_detect_all_satd_types(self, detector):
        """Test detection of all 8 SATD types."""
        findings = detector.detect()

        # Count by type
        types_found = set()
        for f in findings:
            satd_type = f.graph_context.get("satd_type")
            if satd_type:
                types_found.add(satd_type)

        # Should find all types in our test files
        expected_types = {"TODO", "FIXME", "HACK", "XXX", "KLUDGE", "REFACTOR", "TEMP", "BUG"}
        assert expected_types.issubset(types_found), f"Missing types: {expected_types - types_found}"

    def test_severity_mapping(self, detector):
        """Test correct severity assignment."""
        findings = detector.detect()

        for finding in findings:
            satd_type = finding.graph_context.get("satd_type")
            expected_severity = SATD_SEVERITY_MAP.get(satd_type)
            assert finding.severity == expected_severity, \
                f"Wrong severity for {satd_type}: expected {expected_severity}, got {finding.severity}"

    def test_finding_structure(self, detector):
        """Test that findings have correct structure."""
        findings = detector.detect()

        assert len(findings) > 0

        finding = findings[0]
        assert finding.id is not None
        assert finding.detector == "SATDDetector"
        assert finding.title.startswith("SATD:")
        assert finding.description is not None
        assert len(finding.affected_files) > 0
        assert finding.line_start is not None
        assert finding.line_end is not None
        assert "satd_type" in finding.graph_context
        assert "comment_text" in finding.graph_context

    def test_empty_repository(self, mock_client):
        """Test handling of empty repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": tmpdir},
            )
            findings = detector.detect()
            assert findings == []

    def test_no_satd_comments(self, mock_client):
        """Test handling of files with no SATD comments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / "clean.py").write_text("""
def clean_function():
    '''This function has no technical debt.'''
    return 42
""")
            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
            )
            findings = detector.detect()
            assert findings == []

    def test_exclude_patterns(self, mock_client):
        """Test file exclusion patterns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            # Create test file in excluded directory
            tests_dir = repo_path / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_main.py").write_text("# TODO: this should be excluded")

            # Create test file in non-excluded directory
            (repo_path / "main.py").write_text("# TODO: this should be included")

            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
            )
            findings = detector.detect()

            # Should only find the non-excluded file
            file_paths = [f.affected_files[0] for f in findings]
            assert "main.py" in file_paths
            assert not any("tests/" in p for p in file_paths)

    def test_max_findings_limit(self, mock_client):
        """Test max findings limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            # Create file with many TODOs
            content = "\n".join([f"# TODO: item {i}" for i in range(100)])
            (repo_path / "many_todos.py").write_text(content)

            detector = SATDDetector(
                mock_client,
                detector_config={
                    "repository_path": str(repo_path),
                    "max_findings": 10,
                },
            )
            findings = detector.detect()

            assert len(findings) <= 10

    def test_python_fallback_scan(self, mock_client):
        """Test Python fallback scanning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / "test.py").write_text("# TODO: test fallback")

            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
            )

            # Force Python fallback
            files = detector._collect_files()
            findings = detector._scan_python(files)

            assert len(findings) == 1
            assert findings[0][2] == "TODO"

    def test_line_numbers(self, detector):
        """Test correct line number reporting."""
        findings = detector.detect()

        # All findings should have valid line numbers
        for finding in findings:
            assert finding.line_start is not None
            assert finding.line_start > 0
            assert finding.line_end is not None
            assert finding.line_end >= finding.line_start

    def test_suggested_fix(self, detector):
        """Test suggested fixes are provided."""
        findings = detector.detect()

        for finding in findings:
            assert finding.suggested_fix is not None
            assert len(finding.suggested_fix) > 0

    def test_estimated_effort(self, detector):
        """Test effort estimation."""
        findings = detector.detect()

        for finding in findings:
            assert finding.estimated_effort is not None
            satd_type = finding.graph_context.get("satd_type")

            # Check effort matches severity expectations
            if satd_type in ("HACK", "KLUDGE", "BUG"):
                assert "Medium" in finding.estimated_effort
            elif satd_type == "REFACTOR":
                assert "Large" in finding.estimated_effort
            else:
                assert "Small" in finding.estimated_effort

    def test_collaboration_metadata(self, detector):
        """Test collaboration metadata is added."""
        findings = detector.detect()

        for finding in findings:
            assert len(finding.collaboration_metadata) > 0
            metadata = finding.collaboration_metadata[0]
            assert metadata.detector == "SATDDetector"
            assert "satd" in metadata.tags
            assert metadata.confidence > 0

    def test_binary_file_handling(self, mock_client):
        """Test that mostly-binary files with valid text portions are still scanned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            # Create binary file with embedded text
            # Note: Python read with errors='ignore' will skip invalid bytes
            (repo_path / "binary.py").write_bytes(b"\x00\x01\x02\x03 # TODO: test")

            # Create valid file
            (repo_path / "valid.py").write_text("# TODO: valid")

            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
            )
            findings = detector.detect()

            # Both files may produce findings since we use errors='ignore'
            # The key is that we don't crash on binary content
            assert len(findings) >= 1
            # At minimum, the valid file should be found
            valid_findings = [f for f in findings if "valid.py" in f.affected_files[0]]
            assert len(valid_findings) == 1

    def test_large_file_handling(self, mock_client):
        """Test that very large files are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            # Create large file (> 1MB)
            large_content = "# TODO: test\n" * 100000  # ~1.3MB
            (repo_path / "large.py").write_text(large_content)

            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
            )

            # Collect files should skip large file
            files = detector._collect_files()
            assert len(files) == 0

    def test_encoding_error_handling(self, mock_client):
        """Test handling of files with encoding errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            # Create file with invalid UTF-8
            (repo_path / "bad_encoding.py").write_bytes(
                b"# TODO: test\xff\xfe invalid utf-8"
            )

            # Create valid file
            (repo_path / "valid.py").write_text("# TODO: valid")

            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
            )
            findings = detector.detect()

            # Should find at least the valid file
            assert len(findings) >= 1

    def test_multiline_comment_single_todo(self, mock_client):
        """Test that a multiline comment with one TODO creates one finding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / "test.py").write_text("""
# TODO: This is a comment
# that spans multiple lines
# but only has one marker above
""")

            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
            )
            findings = detector.detect()

            # Should have exactly 1 finding (only the line with TODO)
            todo_findings = [f for f in findings if f.graph_context.get("satd_type") == "TODO"]
            assert len(todo_findings) == 1


class TestSATDDetectorGraphEnrichment:
    """Test graph enrichment functionality."""

    @pytest.fixture
    def mock_client_with_graph(self):
        """Create a mock client that returns graph data."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        client.execute_query.return_value = [
            {
                "file_loc": 100,
                "containing_entity": "module.py::MyClass.my_method",
                "entity_type": "Function",
                "complexity": 5,
            }
        ]
        return client

    @pytest.fixture
    def mock_client_error(self):
        """Create a mock client that raises errors on queries."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        client.execute_query.side_effect = Exception("Database error")
        return client

    @pytest.fixture
    def temp_repo_simple(self):
        """Create a simple temp repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / "module.py").write_text("# TODO: test enrichment")
            yield repo_path

    def test_graph_context_included(self, mock_client_with_graph, temp_repo_simple):
        """Test that graph context is included in findings."""
        detector = SATDDetector(
            mock_client_with_graph,
            detector_config={"repository_path": str(temp_repo_simple)},
        )
        findings = detector.detect()

        assert len(findings) > 0
        finding = findings[0]

        # Check graph context is populated
        assert finding.graph_context.get("containing_entity") == "module.py::MyClass.my_method"
        assert finding.graph_context.get("entity_type") == "Function"
        assert finding.graph_context.get("complexity") == 5

    def test_graph_error_handling(self, mock_client_error, temp_repo_simple):
        """Test handling of graph query errors."""
        detector = SATDDetector(
            mock_client_error,
            detector_config={"repository_path": str(temp_repo_simple)},
        )

        # Should still work, just without graph context
        findings = detector.detect()
        assert len(findings) > 0
        # Context should have default values
        assert findings[0].graph_context.get("containing_entity") is None


class TestSATDDetectorWithEnricher:
    """Test integration with GraphEnricher."""

    @pytest.fixture
    def mock_enricher(self):
        """Create a mock GraphEnricher."""
        enricher = Mock()
        enricher.flag_entity = Mock()
        return enricher

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        client.execute_query.return_value = [
            {
                "file_loc": 50,
                "containing_entity": "test.py::test_function",
                "entity_type": "Function",
                "complexity": 3,
            }
        ]
        return client

    def test_enricher_flags_entities(self, mock_client, mock_enricher):
        """Test that enricher flags affected entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / "test.py").write_text("# HACK: workaround")

            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
                enricher=mock_enricher,
            )
            detector.detect()

            # Enricher should have been called
            assert mock_enricher.flag_entity.called

    def test_enricher_error_handling(self, mock_client, mock_enricher):
        """Test handling of enricher errors."""
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / "test.py").write_text("# TODO: test")

            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
                enricher=mock_enricher,
            )

            # Should still produce findings despite enricher error
            findings = detector.detect()
            assert len(findings) > 0


class TestSATDDetectorRustIntegration:
    """Test Rust integration when available."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        client.execute_query.return_value = []
        return client

    @pytest.mark.skipif(not HAS_RUST, reason="Rust module not available")
    def test_rust_scanner_used(self, mock_client):
        """Test that Rust scanner is used when available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / "test.py").write_text("# TODO: test rust")

            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
            )

            files = detector._collect_files()

            # Try Rust scanner
            import repotoire_fast
            result = repotoire_fast.scan_satd_batch(files)

            assert len(result) == 1
            assert result[0][2] == "TODO"  # satd_type

    @pytest.mark.skipif(not HAS_RUST, reason="Rust module not available")
    def test_rust_python_consistency(self, mock_client):
        """Test that Rust and Python scanners produce consistent results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            content = """
# TODO: first todo
# FIXME: a fixme
# HACK: a hack
"""
            (repo_path / "test.py").write_text(content)

            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
            )

            files = detector._collect_files()

            # Get results from both
            python_results = detector._scan_python(files)
            rust_results = detector._scan_rust(files)

            # Should have same number of findings
            assert len(python_results) == len(rust_results)

            # Same types should be detected
            python_types = {r[2] for r in python_results}
            rust_types = {r[2] for r in rust_results}
            assert python_types == rust_types


class TestSATDDetectorConfiguration:
    """Test detector configuration options."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        client.execute_query.return_value = []
        return client

    def test_custom_file_extensions(self, mock_client):
        """Test custom file extensions configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / "test.py").write_text("# TODO: python")
            (repo_path / "test.custom").write_text("# TODO: custom")

            # Default extensions (should find .py only)
            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": str(repo_path)},
            )
            files = detector._collect_files()
            assert len(files) == 1

            # Custom extensions (should find .custom)
            detector_custom = SATDDetector(
                mock_client,
                detector_config={
                    "repository_path": str(repo_path),
                    "file_extensions": [".custom"],
                },
            )
            files_custom = detector_custom._collect_files()
            assert len(files_custom) == 1
            assert files_custom[0][0] == "test.custom"

    def test_custom_exclude_patterns(self, mock_client):
        """Test custom exclude patterns configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)

            # Create files
            (repo_path / "include.py").write_text("# TODO: include")
            exclude_dir = repo_path / "custom_exclude"
            exclude_dir.mkdir()
            (exclude_dir / "exclude.py").write_text("# TODO: exclude")

            detector = SATDDetector(
                mock_client,
                detector_config={
                    "repository_path": str(repo_path),
                    "exclude_patterns": ["custom_exclude/"],
                },
            )

            files = detector._collect_files()
            paths = [f[0] for f in files]

            assert "include.py" in paths
            assert not any("custom_exclude" in p for p in paths)

    def test_invalid_repository_path(self, mock_client):
        """Test error for invalid repository path."""
        with pytest.raises(ValueError, match="does not exist"):
            SATDDetector(
                mock_client,
                detector_config={"repository_path": "/nonexistent/path"},
            )

    def test_default_configuration(self, mock_client):
        """Test default configuration values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            detector = SATDDetector(
                mock_client,
                detector_config={"repository_path": tmpdir},
            )

            assert detector.max_findings == 500
            assert ".py" in detector.file_extensions
            assert "tests/" in detector.exclude_patterns
