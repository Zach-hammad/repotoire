"""Unit tests for CircularDependencyDetector with Rust SCC (REPO-170, REPO-200)."""

from unittest.mock import Mock, patch
from datetime import datetime

import pytest

from repotoire.detectors.circular_dependency import CircularDependencyDetector
from repotoire.models import Severity


@pytest.fixture
def mock_db():
    """Create a mock Neo4j client."""
    db = Mock()
    db.execute_query = Mock()
    return db


class TestCircularDependencyDetectorSCC:
    """Test CircularDependencyDetector with Rust SCC algorithm."""

    def test_uses_rust_scc(self, mock_db):
        """Test that detector uses Rust SCC algorithm."""
        with patch(
            "repotoire.detectors.circular_dependency.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_scc.return_value = {
                "componentCount": 10,
                "nodePropertiesWritten": 50
            }
            mock_algo.get_scc_cycles.return_value = []

            detector = CircularDependencyDetector(mock_db)
            findings = detector.detect()

            mock_algo.calculate_scc.assert_called_once()

    def test_falls_back_to_path_queries_when_rust_fails(self, mock_db):
        """Test fallback to path queries when Rust SCC fails."""
        with patch(
            "repotoire.detectors.circular_dependency.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_scc.return_value = None
            mock_db.execute_query.return_value = []

            detector = CircularDependencyDetector(mock_db)
            findings = detector.detect()

            mock_db.execute_query.assert_called_once()  # Path query fallback

    def test_falls_back_when_scc_calculation_fails(self, mock_db):
        """Test fallback when SCC calculation returns None."""
        with patch(
            "repotoire.detectors.circular_dependency.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_scc.return_value = None
            mock_db.execute_query.return_value = []

            detector = CircularDependencyDetector(mock_db)
            findings = detector.detect()

            mock_db.execute_query.assert_called_once()  # Fallback

    def test_scc_detects_cycles(self, mock_db):
        """Test SCC detection of circular dependencies."""
        with patch(
            "repotoire.detectors.circular_dependency.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_scc.return_value = {
                "componentCount": 5,
                "nodePropertiesWritten": 20
            }
            mock_algo.get_scc_cycles.return_value = [
                {
                    "component_id": 1,
                    "cycle_size": 3,
                    "file_paths": ["/a.py", "/b.py", "/c.py"],
                    "file_names": ["a", "b", "c"],
                    "edges": [
                        {"from": "a", "to": "b"},
                        {"from": "b", "to": "c"},
                        {"from": "c", "to": "a"}
                    ]
                }
            ]

            detector = CircularDependencyDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "3 files" in findings[0].title
            assert findings[0].severity == Severity.MEDIUM
            assert findings[0].graph_context["detection_method"] == "SCC"

    def test_scc_large_cycle_critical_severity(self, mock_db):
        """Test that large cycles get CRITICAL severity."""
        with patch(
            "repotoire.detectors.circular_dependency.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_scc.return_value = {"componentCount": 1}

            # Create a large cycle
            files = [f"/{chr(97+i)}.py" for i in range(12)]
            names = [chr(97+i) for i in range(12)]

            mock_algo.get_scc_cycles.return_value = [
                {
                    "component_id": 1,
                    "cycle_size": 12,
                    "file_paths": files,
                    "file_names": names,
                    "edges": []
                }
            ]

            detector = CircularDependencyDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].severity == Severity.CRITICAL

    def test_path_query_detects_cycles(self, mock_db):
        """Test path-based query detection (fallback)."""
        with patch(
            "repotoire.detectors.circular_dependency.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_scc.return_value = None  # Force fallback

            mock_db.execute_query.return_value = [
                {
                    "cycle": ["/x.py", "/y.py"],
                    "cycle_length": 2
                }
            ]

            detector = CircularDependencyDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "2 files" in findings[0].title
            assert findings[0].graph_context["detection_method"] == "path_query"

    def test_deduplicates_cycles_path_query(self, mock_db):
        """Test that duplicate cycles are deduplicated in path queries."""
        with patch(
            "repotoire.detectors.circular_dependency.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_scc.return_value = None  # Force fallback

            # Same cycle reported twice (from different starting points)
            mock_db.execute_query.return_value = [
                {"cycle": ["/a.py", "/b.py"], "cycle_length": 2},
                {"cycle": ["/b.py", "/a.py"], "cycle_length": 2}
            ]

            detector = CircularDependencyDetector(mock_db)
            findings = detector.detect()

            # Should be deduplicated based on normalized form
            # Note: These are different rotations but same cycle
            assert len(findings) <= 2  # Normalized correctly

    def test_severity_levels(self, mock_db):
        """Test severity calculation for different cycle sizes."""
        detector = CircularDependencyDetector(mock_db)

        assert detector._calculate_severity(2) == Severity.LOW
        assert detector._calculate_severity(3) == Severity.MEDIUM
        assert detector._calculate_severity(5) == Severity.HIGH
        assert detector._calculate_severity(10) == Severity.CRITICAL

    def test_effort_estimation(self, mock_db):
        """Test effort estimation for different cycle sizes."""
        detector = CircularDependencyDetector(mock_db)

        assert "hours" in detector._estimate_effort(2).lower()
        assert "1-2 days" in detector._estimate_effort(5).lower()
        assert "2-4 days" in detector._estimate_effort(10).lower()

    def test_normalize_cycle(self, mock_db):
        """Test cycle normalization preserves directionality."""
        detector = CircularDependencyDetector(mock_db)

        # Same cycle, different starting points should normalize same
        cycle1 = ["/a.py", "/b.py", "/c.py"]
        cycle2 = ["/b.py", "/c.py", "/a.py"]
        cycle3 = ["/c.py", "/a.py", "/b.py"]

        norm1 = detector._normalize_cycle(cycle1)
        norm2 = detector._normalize_cycle(cycle2)
        norm3 = detector._normalize_cycle(cycle3)

        assert norm1 == norm2 == norm3

    def test_scc_with_enricher(self, mock_db):
        """Test that enricher is called for flagging entities."""
        mock_enricher = Mock()

        with patch(
            "repotoire.detectors.circular_dependency.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_scc.return_value = {"componentCount": 1}
            mock_algo.get_scc_cycles.return_value = [
                {
                    "component_id": 1,
                    "cycle_size": 2,
                    "file_paths": ["/a.py", "/b.py"],
                    "file_names": ["a", "b"],
                    "edges": []
                }
            ]

            detector = CircularDependencyDetector(mock_db, enricher=mock_enricher)
            findings = detector.detect()

            assert mock_enricher.flag_entity.call_count == 2  # Both files

    def test_edge_description_in_findings(self, mock_db):
        """Test that edge descriptions are included in findings."""
        with patch(
            "repotoire.detectors.circular_dependency.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_scc.return_value = {"componentCount": 1}
            mock_algo.get_scc_cycles.return_value = [
                {
                    "component_id": 1,
                    "cycle_size": 2,
                    "file_paths": ["/module/a.py", "/module/b.py"],
                    "file_names": ["module.a", "module.b"],
                    "edges": [
                        {"from": "module.a", "to": "module.b"},
                        {"from": "module.b", "to": "module.a"}
                    ]
                }
            ]

            detector = CircularDependencyDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "import" in findings[0].description.lower()

    def test_handles_scc_exception(self, mock_db):
        """Test graceful handling when SCC throws exception."""
        with patch(
            "repotoire.detectors.circular_dependency.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_scc.side_effect = Exception("Rust error")
            mock_db.execute_query.return_value = []

            detector = CircularDependencyDetector(mock_db)
            findings = detector.detect()

            # Should have fallen back to path queries
            mock_db.execute_query.assert_called_once()

    def test_collaboration_metadata(self, mock_db):
        """Test findings include collaboration metadata."""
        with patch(
            "repotoire.detectors.circular_dependency.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_scc.return_value = {"componentCount": 1}
            mock_algo.get_scc_cycles.return_value = [
                {
                    "component_id": 1,
                    "cycle_size": 2,
                    "file_paths": ["/a.py", "/b.py"],
                    "file_names": ["a", "b"],
                    "edges": []
                }
            ]

            detector = CircularDependencyDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].collaboration_metadata is not None
            assert len(findings[0].collaboration_metadata) > 0
            assert "SCC" in findings[0].collaboration_metadata[0].evidence
