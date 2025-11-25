"""Unit tests for CoreUtilityDetector."""

from unittest.mock import Mock, patch

import pytest

from repotoire.detectors.core_utility import CoreUtilityDetector
from repotoire.models import Severity


@pytest.fixture
def mock_db():
    """Create a mock Neo4j client."""
    db = Mock()
    db.execute_query = Mock()
    return db


class TestCoreUtilityDetector:
    """Test CoreUtilityDetector."""

    def test_no_issues_when_gds_not_available(self, mock_db):
        """Test that detector returns empty when GDS is not available."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = False

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0
            mock_algo.check_gds_available.assert_called_once()

    def test_no_issues_when_projection_fails(self, mock_db):
        """Test that detector returns empty when projection fails."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = False

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0

    def test_no_issues_when_harmonic_fails(self, mock_db):
        """Test that detector handles harmonic centrality failure gracefully."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = None

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0
            mock_algo.cleanup_projection.assert_called()

    def test_no_issues_when_stats_fail(self, mock_db):
        """Test that detector handles statistics failure gracefully."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = None

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0
            mock_algo.cleanup_projection.assert_called()

    def test_central_coordinator_detection(self, mock_db):
        """Test detection of central coordinator functions."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = {
                "total_functions": 100,
                "p95_harmonic": 0.8,
                "p10_harmonic": 0.2,
                "avg_harmonic": 0.5,
                "max_harmonic": 1.0,
            }
            mock_algo.get_high_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/core.py::orchestrate",
                    "name": "orchestrate",
                    "harmonic_score": 0.92,
                    "complexity": 15,  # Below high threshold
                    "loc": 50,
                    "file_path": "/src/core.py",
                    "line_number": 10,
                    "caller_count": 8,
                    "callee_count": 12,
                }
            ]
            mock_algo.get_low_harmonic_functions.return_value = []

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].detector == "CoreUtilityDetector"
            assert "Central coordinator" in findings[0].title
            assert findings[0].severity == Severity.MEDIUM
            assert findings[0].graph_context["harmonic_score"] == 0.92

    def test_central_coordinator_high_complexity(self, mock_db):
        """Test that high complexity central coordinators get HIGH severity."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = {
                "total_functions": 100,
                "p95_harmonic": 0.8,
                "p10_harmonic": 0.2,
                "avg_harmonic": 0.5,
                "max_harmonic": 1.0,
            }
            mock_algo.get_high_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/core.py::complex_orchestrate",
                    "name": "complex_orchestrate",
                    "harmonic_score": 0.95,
                    "complexity": 35,  # High complexity (> 20)
                    "loc": 150,
                    "file_path": "/src/core.py",
                    "line_number": 50,
                    "caller_count": 15,
                    "callee_count": 20,
                }
            ]
            mock_algo.get_low_harmonic_functions.return_value = []

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].severity == Severity.HIGH
            assert "high complexity" in findings[0].title.lower()
            assert "Warning" in findings[0].description

    def test_isolated_code_detection(self, mock_db):
        """Test detection of isolated/dead code."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = {
                "total_functions": 100,
                "p95_harmonic": 0.8,
                "p10_harmonic": 0.2,
                "avg_harmonic": 0.5,
                "max_harmonic": 1.0,
            }
            mock_algo.get_high_harmonic_functions.return_value = []
            mock_algo.get_low_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/unused.py::orphan_func",
                    "name": "orphan_func",
                    "harmonic_score": 0.05,
                    "complexity": 8,
                    "loc": 20,  # > 5 so won't be filtered
                    "file_path": "/src/unused.py",
                    "line_number": 1,
                    "caller_count": 0,  # Never called
                    "callee_count": 2,
                }
            ]

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "Isolated code" in findings[0].title
            assert "never called" in findings[0].title.lower()
            assert findings[0].severity == Severity.LOW

    def test_completely_isolated_code(self, mock_db):
        """Test detection of completely isolated code (no callers and no callees)."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = {
                "total_functions": 100,
                "p95_harmonic": 0.8,
                "p10_harmonic": 0.2,
                "avg_harmonic": 0.5,
                "max_harmonic": 1.0,
            }
            mock_algo.get_high_harmonic_functions.return_value = []
            mock_algo.get_low_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/orphan.py::dead_func",
                    "name": "dead_func",
                    "harmonic_score": 0.0,
                    "complexity": 5,
                    "loc": 15,
                    "file_path": "/src/orphan.py",
                    "line_number": 1,
                    "caller_count": 0,  # No callers
                    "callee_count": 0,  # No callees
                }
            ]

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "completely isolated" in findings[0].title.lower()
            assert findings[0].severity == Severity.MEDIUM

    def test_small_function_filtered(self, mock_db):
        """Test that small functions (< 5 LOC) are filtered out."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = {
                "total_functions": 100,
                "p95_harmonic": 0.8,
                "p10_harmonic": 0.2,
                "avg_harmonic": 0.5,
                "max_harmonic": 1.0,
            }
            mock_algo.get_high_harmonic_functions.return_value = []
            mock_algo.get_low_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/utils.py::tiny_helper",
                    "name": "tiny_helper",
                    "harmonic_score": 0.1,
                    "complexity": 1,
                    "loc": 3,  # < 5 LOC, should be filtered
                    "file_path": "/src/utils.py",
                    "line_number": 1,
                    "caller_count": 0,
                    "callee_count": 0,
                }
            ]

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            # Small function should be filtered out
            assert len(findings) == 0

    def test_function_with_enough_callers_not_isolated(self, mock_db):
        """Test that functions with enough callers are not flagged as isolated."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = {
                "total_functions": 100,
                "p95_harmonic": 0.8,
                "p10_harmonic": 0.2,
                "avg_harmonic": 0.5,
                "max_harmonic": 1.0,
            }
            mock_algo.get_high_harmonic_functions.return_value = []
            mock_algo.get_low_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/utils.py::helper",
                    "name": "helper",
                    "harmonic_score": 0.15,
                    "complexity": 5,
                    "loc": 20,
                    "file_path": "/src/utils.py",
                    "line_number": 1,
                    "caller_count": 3,  # >= MIN_CALLERS_THRESHOLD (2)
                    "callee_count": 1,
                }
            ]

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            # Function has enough callers, not isolated
            assert len(findings) == 0

    def test_multiple_findings(self, mock_db):
        """Test detection of multiple central and isolated functions."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = {
                "total_functions": 100,
                "p95_harmonic": 0.8,
                "p10_harmonic": 0.2,
                "avg_harmonic": 0.5,
                "max_harmonic": 1.0,
            }
            mock_algo.get_high_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/core.py::main_orchestrate",
                    "name": "main_orchestrate",
                    "harmonic_score": 0.95,
                    "complexity": 25,  # High
                    "loc": 100,
                    "file_path": "/src/core.py",
                    "line_number": 1,
                    "caller_count": 10,
                    "callee_count": 15,
                },
                {
                    "qualified_name": "/src/api.py::handle_request",
                    "name": "handle_request",
                    "harmonic_score": 0.88,
                    "complexity": 12,  # Normal
                    "loc": 40,
                    "file_path": "/src/api.py",
                    "line_number": 50,
                    "caller_count": 8,
                    "callee_count": 10,
                },
            ]
            mock_algo.get_low_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/dead.py::unused_func",
                    "name": "unused_func",
                    "harmonic_score": 0.02,
                    "complexity": 10,
                    "loc": 30,
                    "file_path": "/src/dead.py",
                    "line_number": 1,
                    "caller_count": 0,
                    "callee_count": 0,
                }
            ]

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            # 2 central coordinators + 1 isolated
            assert len(findings) == 3

            # Check severities
            severities = {f.severity for f in findings}
            assert Severity.HIGH in severities  # High complexity central
            assert Severity.MEDIUM in severities  # Normal central or isolated

    def test_cleanup_on_error(self, mock_db):
        """Test that cleanup is called even on error."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.side_effect = Exception(
                "Query failed"
            )
            mock_algo.cleanup_projection = Mock()

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0
            mock_algo.cleanup_projection.assert_called()

    def test_collaboration_metadata_central(self, mock_db):
        """Test that collaboration metadata is added to central coordinator findings."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = {
                "total_functions": 100,
                "p95_harmonic": 0.8,
                "p10_harmonic": 0.2,
                "avg_harmonic": 0.5,
                "max_harmonic": 1.0,
            }
            mock_algo.get_high_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/core.py::main",
                    "name": "main",
                    "harmonic_score": 0.9,
                    "complexity": 10,
                    "loc": 30,
                    "file_path": "/src/core.py",
                    "line_number": 1,
                    "caller_count": 5,
                    "callee_count": 8,
                }
            ]
            mock_algo.get_low_harmonic_functions.return_value = []

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert len(findings[0].collaboration_metadata) == 1
            metadata = findings[0].collaboration_metadata[0]
            assert metadata.detector == "CoreUtilityDetector"
            assert "high_harmonic_centrality" in metadata.evidence
            assert "coordinator" in metadata.tags

    def test_collaboration_metadata_isolated(self, mock_db):
        """Test that collaboration metadata is added to isolated code findings."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = {
                "total_functions": 100,
                "p95_harmonic": 0.8,
                "p10_harmonic": 0.2,
                "avg_harmonic": 0.5,
                "max_harmonic": 1.0,
            }
            mock_algo.get_high_harmonic_functions.return_value = []
            mock_algo.get_low_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/dead.py::orphan",
                    "name": "orphan",
                    "harmonic_score": 0.05,
                    "complexity": 5,
                    "loc": 15,
                    "file_path": "/src/dead.py",
                    "line_number": 1,
                    "caller_count": 0,
                    "callee_count": 1,
                }
            ]

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert len(findings[0].collaboration_metadata) == 1
            metadata = findings[0].collaboration_metadata[0]
            assert "low_harmonic_centrality" in metadata.evidence
            assert "dead_code" in metadata.tags

    def test_percentile_calculation(self, mock_db):
        """Test that percentile is calculated correctly in findings."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = {
                "total_functions": 100,
                "p95_harmonic": 0.8,
                "p10_harmonic": 0.2,
                "avg_harmonic": 0.5,
                "max_harmonic": 1.0,  # Max is 1.0
            }
            mock_algo.get_high_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/core.py::central",
                    "name": "central",
                    "harmonic_score": 0.9,  # 90% of max
                    "complexity": 10,
                    "loc": 30,
                    "file_path": "/src/core.py",
                    "line_number": 1,
                    "caller_count": 5,
                    "callee_count": 8,
                }
            ]
            mock_algo.get_low_harmonic_functions.return_value = []

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            # Percentile should be 90 (0.9 / 1.0 * 100)
            assert findings[0].graph_context["percentile"] == 90.0

    def test_severity_method(self, mock_db):
        """Test the severity method returns finding's severity."""
        with patch(
            "repotoire.detectors.core_utility.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_call_graph_projection.return_value = True
            mock_algo.calculate_harmonic_centrality.return_value = {
                "nodePropertiesWritten": 100
            }
            mock_algo.get_harmonic_statistics.return_value = {
                "total_functions": 100,
                "p95_harmonic": 0.8,
                "p10_harmonic": 0.2,
                "avg_harmonic": 0.5,
                "max_harmonic": 1.0,
            }
            mock_algo.get_high_harmonic_functions.return_value = [
                {
                    "qualified_name": "/src/core.py::func",
                    "name": "func",
                    "harmonic_score": 0.9,
                    "complexity": 25,  # High - triggers HIGH severity
                    "loc": 80,
                    "file_path": "/src/core.py",
                    "line_number": 1,
                    "caller_count": 10,
                    "callee_count": 12,
                }
            ]
            mock_algo.get_low_harmonic_functions.return_value = []

            detector = CoreUtilityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert detector.severity(findings[0]) == Severity.HIGH


class TestCoreUtilityConstants:
    """Test CoreUtilityDetector constants."""

    def test_high_complexity_threshold(self, mock_db):
        """Test HIGH_COMPLEXITY_THRESHOLD constant."""
        detector = CoreUtilityDetector(mock_db)
        assert detector.HIGH_COMPLEXITY_THRESHOLD == 20

    def test_min_callers_threshold(self, mock_db):
        """Test MIN_CALLERS_THRESHOLD constant."""
        detector = CoreUtilityDetector(mock_db)
        assert detector.MIN_CALLERS_THRESHOLD == 2
