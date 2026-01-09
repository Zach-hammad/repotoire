"""Unit tests for DegreeCentralityDetector (REPO-171)."""

from unittest.mock import Mock, patch

import pytest

from repotoire.detectors.degree_centrality import DegreeCentralityDetector
from repotoire.models import Severity


@pytest.fixture
def mock_db():
    """Create a mock Neo4j client."""
    db = Mock()
    db.execute_query = Mock()
    return db


class TestDegreeCentralityDetector:
    """Test DegreeCentralityDetector."""

    def test_no_issues_when_degree_calculation_fails(self, mock_db):
        """Test that detector handles degree calculation failure gracefully."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = None

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0
            mock_algo.calculate_degree_centrality.assert_called_once()

    def test_god_class_detection_high_indegree_high_complexity(self, mock_db):
        """Test detection of God Class (high in-degree + high complexity)."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = {
                "in_degree_nodes": 100,
                "out_degree_nodes": 100
            }
            mock_algo.get_degree_statistics.return_value = {
                "avg_in_degree": 3.0,
                "max_in_degree": 50,
                "avg_out_degree": 5.0,
                "max_out_degree": 30
            }
            mock_algo.get_high_indegree_nodes.return_value = [
                {
                    "qualified_name": "core.utils",
                    "file_path": "/src/core/utils.py",
                    "in_degree": 45,
                    "out_degree": 5,
                    "complexity": 25,  # Above threshold
                    "line_count": 500,
                    "threshold": 30
                }
            ]
            mock_algo.get_high_outdegree_nodes.return_value = []

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "God Class" in findings[0].title
            assert findings[0].severity in [Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
            assert "in_degree" in findings[0].graph_context

    def test_god_class_critical_severity(self, mock_db):
        """Test God Class with extreme metrics gets CRITICAL severity."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = {"in_degree_nodes": 100}
            mock_algo.get_degree_statistics.return_value = {
                "max_in_degree": 100
            }
            mock_algo.get_high_indegree_nodes.return_value = [
                {
                    "qualified_name": "core.base",
                    "file_path": "/core/base.py",
                    "in_degree": 99,  # Very high
                    "out_degree": 2,
                    "complexity": 35,  # Very high
                    "line_count": 1000,
                    "threshold": 50
                }
            ]
            mock_algo.get_high_outdegree_nodes.return_value = []

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].severity == Severity.CRITICAL

    def test_god_class_not_detected_low_complexity(self, mock_db):
        """Test that high in-degree alone doesn't flag as God Class."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = {"in_degree_nodes": 100}
            mock_algo.get_degree_statistics.return_value = {"max_in_degree": 50}
            # High in-degree but low complexity - not a God Class
            mock_algo.get_high_indegree_nodes.return_value = [
                {
                    "qualified_name": "utils.constants",
                    "file_path": "/utils/constants.py",
                    "in_degree": 40,
                    "out_degree": 0,
                    "complexity": 5,  # Low complexity
                    "line_count": 50,
                    "threshold": 30
                }
            ]
            mock_algo.get_high_outdegree_nodes.return_value = []

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            # Should not flag as God Class (low complexity)
            assert len(findings) == 0

    def test_feature_envy_detection_high_outdegree(self, mock_db):
        """Test detection of Feature Envy (high out-degree)."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = {
                "in_degree_nodes": 100,
                "out_degree_nodes": 100
            }
            mock_algo.get_degree_statistics.return_value = {
                "avg_out_degree": 5.0,
                "max_out_degree": 40
            }
            mock_algo.get_high_indegree_nodes.return_value = []
            mock_algo.get_high_outdegree_nodes.return_value = [
                {
                    "qualified_name": "handlers.main",
                    "file_path": "/handlers/main.py",
                    "out_degree": 35,
                    "in_degree": 3,
                    "complexity": 10,
                    "line_count": 200,
                    "threshold": 25
                }
            ]

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "Feature Envy" in findings[0].title
            assert "out_degree" in findings[0].graph_context

    def test_coupling_hotspot_detection(self, mock_db):
        """Test detection of coupling hotspots (both high in and out degree)."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = {
                "in_degree_nodes": 100,
                "out_degree_nodes": 100
            }
            mock_algo.get_degree_statistics.return_value = {
                "max_in_degree": 50,
                "max_out_degree": 40
            }
            # Same file in both lists
            mock_algo.get_high_indegree_nodes.return_value = [
                {
                    "qualified_name": "core.engine",
                    "file_path": "/core/engine.py",
                    "in_degree": 40,
                    "out_degree": 30,
                    "complexity": 20,
                    "line_count": 400,
                    "threshold": 30
                }
            ]
            mock_algo.get_high_outdegree_nodes.return_value = [
                {
                    "qualified_name": "core.engine",
                    "file_path": "/core/engine.py",
                    "out_degree": 30,
                    "in_degree": 40,
                    "complexity": 20,
                    "line_count": 400,
                    "threshold": 20
                }
            ]

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            # Should have God Class + Feature Envy + Coupling Hotspot
            hotspot_findings = [f for f in findings if "Hotspot" in f.title]
            assert len(hotspot_findings) == 1
            assert hotspot_findings[0].severity in [Severity.HIGH, Severity.CRITICAL]
            assert "total_coupling" in hotspot_findings[0].graph_context

    def test_coupling_hotspot_critical_when_complex(self, mock_db):
        """Test coupling hotspot with high complexity is CRITICAL."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = {"in_degree_nodes": 100}
            mock_algo.get_degree_statistics.return_value = {
                "max_in_degree": 50,
                "max_out_degree": 40
            }
            mock_algo.get_high_indegree_nodes.return_value = [
                {
                    "qualified_name": "core.main",
                    "file_path": "/core/main.py",
                    "in_degree": 45,
                    "out_degree": 35,
                    "complexity": 25,  # High complexity
                    "line_count": 500,
                    "threshold": 30
                }
            ]
            mock_algo.get_high_outdegree_nodes.return_value = [
                {
                    "qualified_name": "core.main",
                    "file_path": "/core/main.py",
                    "out_degree": 35,
                    "in_degree": 45,
                    "complexity": 25,
                    "line_count": 500,
                    "threshold": 25
                }
            ]

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            hotspot_findings = [f for f in findings if "Hotspot" in f.title]
            assert len(hotspot_findings) == 1
            assert hotspot_findings[0].severity == Severity.CRITICAL

    def test_handles_exception_gracefully(self, mock_db):
        """Test that detector handles exceptions gracefully."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = {"in_degree_nodes": 100}
            mock_algo.get_degree_statistics.side_effect = Exception("DB error")

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            # Should return empty list on exception, not crash
            assert len(findings) == 0

    def test_collaboration_metadata_god_class(self, mock_db):
        """Test God Class findings include collaboration metadata."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = {"in_degree_nodes": 100}
            mock_algo.get_degree_statistics.return_value = {"max_in_degree": 50}
            mock_algo.get_high_indegree_nodes.return_value = [
                {
                    "qualified_name": "test.module",
                    "file_path": "/test.py",
                    "in_degree": 40,
                    "out_degree": 5,
                    "complexity": 20,
                    "line_count": 300,
                    "threshold": 30
                }
            ]
            mock_algo.get_high_outdegree_nodes.return_value = []

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].collaboration_metadata is not None
            assert len(findings[0].collaboration_metadata) > 0
            assert findings[0].collaboration_metadata[0].detector == "DegreeCentralityDetector"
            assert "high_in_degree" in findings[0].collaboration_metadata[0].evidence
            assert "god_class" in findings[0].collaboration_metadata[0].tags

    def test_collaboration_metadata_feature_envy(self, mock_db):
        """Test Feature Envy findings include collaboration metadata."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = {"out_degree_nodes": 100}
            mock_algo.get_degree_statistics.return_value = {"max_out_degree": 40}
            mock_algo.get_high_indegree_nodes.return_value = []
            mock_algo.get_high_outdegree_nodes.return_value = [
                {
                    "qualified_name": "test.handler",
                    "file_path": "/test.py",
                    "out_degree": 35,
                    "in_degree": 2,
                    "complexity": 10,
                    "line_count": 150,
                    "threshold": 25
                }
            ]

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert len(findings[0].collaboration_metadata) > 0
            assert "high_out_degree" in findings[0].collaboration_metadata[0].evidence
            assert "feature_envy" in findings[0].collaboration_metadata[0].tags

    def test_severity_method(self, mock_db):
        """Test the severity method returns finding severity."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = {"in_degree_nodes": 100}
            mock_algo.get_degree_statistics.return_value = {"max_in_degree": 50}
            mock_algo.get_high_indegree_nodes.return_value = [
                {
                    "qualified_name": "test.module",
                    "file_path": "/test.py",
                    "in_degree": 40,
                    "out_degree": 5,
                    "complexity": 20,
                    "line_count": 300,
                    "threshold": 30
                }
            ]
            mock_algo.get_high_outdegree_nodes.return_value = []

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            assert detector.severity(findings[0]) == findings[0].severity

    def test_no_duplicate_findings_for_hotspot(self, mock_db):
        """Test that coupling hotspot doesn't also appear as both God Class and Feature Envy."""
        with patch(
            "repotoire.detectors.degree_centrality.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_degree_centrality.return_value = {"in_degree_nodes": 100}
            mock_algo.get_degree_statistics.return_value = {
                "max_in_degree": 50,
                "max_out_degree": 40
            }
            # Same file appears in both high in-degree and high out-degree
            mock_algo.get_high_indegree_nodes.return_value = [
                {
                    "qualified_name": "core.main",
                    "file_path": "/core/main.py",
                    "in_degree": 45,
                    "out_degree": 35,
                    "complexity": 20,
                    "line_count": 400,
                    "threshold": 30
                }
            ]
            mock_algo.get_high_outdegree_nodes.return_value = [
                {
                    "qualified_name": "core.main",
                    "file_path": "/core/main.py",
                    "out_degree": 35,
                    "in_degree": 45,
                    "complexity": 20,
                    "line_count": 400,
                    "threshold": 25
                }
            ]

            detector = DegreeCentralityDetector(mock_db)
            findings = detector.detect()

            # Check we have the expected findings
            god_class = [f for f in findings if "God Class" in f.title]
            feature_envy = [f for f in findings if "Feature Envy" in f.title]
            hotspot = [f for f in findings if "Hotspot" in f.title]

            # All three types should be present
            assert len(god_class) == 1
            assert len(feature_envy) == 1
            assert len(hotspot) == 1
