"""Unit tests for InfluentialCodeDetector (REPO-169, REPO-200)."""

from unittest.mock import Mock, patch

import pytest

from repotoire.detectors.influential_code import InfluentialCodeDetector
from repotoire.models import Severity


@pytest.fixture
def mock_db():
    """Create a mock Neo4j client."""
    db = Mock()
    db.execute_query = Mock()
    return db


class TestInfluentialCodeDetector:
    """Test InfluentialCodeDetector with Rust PageRank."""

    def test_no_issues_when_pagerank_fails(self, mock_db):
        """Test that detector handles PageRank failure gracefully."""
        with patch(
            "repotoire.detectors.influential_code.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_pagerank.return_value = None

            detector = InfluentialCodeDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0

    def test_influential_code_detection_high_pagerank(self, mock_db):
        """Test detection of high PageRank influential code."""
        with patch(
            "repotoire.detectors.influential_code.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_pagerank.return_value = {
                "nodePropertiesWritten": 100
            }

            # Mock high PageRank query
            mock_db.execute_query.side_effect = [
                # High PageRank functions
                [{
                    "qualified_name": "core.utils.helper",
                    "name": "helper",
                    "file_path": "/src/core/utils.py",
                    "line_number": 10,
                    "pagerank": 0.85,
                    "pagerank_threshold": 0.5,
                    "complexity": 5,
                    "loc": 50,
                    "caller_count": 20,
                    "callee_count": 3
                }],
                # Bloated code (empty)
                []
            ]

            detector = InfluentialCodeDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "helper" in findings[0].title
            assert findings[0].severity == Severity.MEDIUM  # Low complexity
            assert "pagerank" in findings[0].graph_context

    def test_critical_bottleneck_high_pagerank_high_complexity(self, mock_db):
        """Test that high PageRank + high complexity = HIGH severity."""
        with patch(
            "repotoire.detectors.influential_code.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_pagerank.return_value = {
                "nodePropertiesWritten": 100
            }

            mock_db.execute_query.side_effect = [
                # High PageRank + high complexity
                [{
                    "qualified_name": "core.engine.process",
                    "name": "process",
                    "file_path": "/src/core/engine.py",
                    "line_number": 50,
                    "pagerank": 0.95,
                    "pagerank_threshold": 0.5,
                    "complexity": 25,  # Above threshold
                    "loc": 300,
                    "caller_count": 50,
                    "callee_count": 10
                }],
                # Bloated code (empty)
                []
            ]

            detector = InfluentialCodeDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "bottleneck" in findings[0].title.lower()
            assert findings[0].severity == Severity.HIGH

    def test_bloated_code_detection(self, mock_db):
        """Test detection of bloated code (low PageRank + high complexity)."""
        with patch(
            "repotoire.detectors.influential_code.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_pagerank.return_value = {
                "nodePropertiesWritten": 100
            }

            mock_db.execute_query.side_effect = [
                # High PageRank (empty)
                [],
                # Bloated code
                [{
                    "qualified_name": "legacy.old_module.complex_func",
                    "name": "complex_func",
                    "file_path": "/src/legacy/old_module.py",
                    "line_number": 100,
                    "pagerank": 0.05,
                    "median_pagerank": 0.3,
                    "complexity": 30,  # Very high
                    "loc": 400,
                    "caller_count": 1
                }]
            ]

            detector = InfluentialCodeDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "bloated" in findings[0].title.lower()
            assert findings[0].severity == Severity.HIGH  # Very complex

    def test_bloated_code_medium_severity(self, mock_db):
        """Test bloated code with moderate complexity gets MEDIUM severity."""
        with patch(
            "repotoire.detectors.influential_code.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_pagerank.return_value = {
                "nodePropertiesWritten": 100
            }

            mock_db.execute_query.side_effect = [
                [],
                [{
                    "qualified_name": "utils.helper.do_stuff",
                    "name": "do_stuff",
                    "file_path": "/src/utils/helper.py",
                    "line_number": 20,
                    "pagerank": 0.1,
                    "median_pagerank": 0.3,
                    "complexity": 18,  # Above threshold but not extreme
                    "loc": 150,
                    "caller_count": 2
                }]
            ]

            detector = InfluentialCodeDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].severity == Severity.MEDIUM

    def test_handles_exception(self, mock_db):
        """Test that exceptions are handled gracefully."""
        with patch(
            "repotoire.detectors.influential_code.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_pagerank.return_value = {"nodePropertiesWritten": 100}
            mock_db.execute_query.side_effect = Exception("DB error")

            detector = InfluentialCodeDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0

    def test_collaboration_metadata(self, mock_db):
        """Test that findings include collaboration metadata."""
        with patch(
            "repotoire.detectors.influential_code.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_pagerank.return_value = {"nodePropertiesWritten": 100}

            mock_db.execute_query.side_effect = [
                [{
                    "qualified_name": "test.func",
                    "name": "func",
                    "file_path": "/test.py",
                    "line_number": 1,
                    "pagerank": 0.9,
                    "pagerank_threshold": 0.5,
                    "complexity": 5,
                    "loc": 20,
                    "caller_count": 10,
                    "callee_count": 2
                }],
                []
            ]

            detector = InfluentialCodeDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].collaboration_metadata is not None
            assert len(findings[0].collaboration_metadata) > 0
            assert findings[0].collaboration_metadata[0].detector == "InfluentialCodeDetector"
            assert "high_pagerank" in findings[0].collaboration_metadata[0].evidence

    def test_severity_method(self, mock_db):
        """Test the severity method returns finding severity."""
        with patch(
            "repotoire.detectors.influential_code.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.calculate_pagerank.return_value = {"nodePropertiesWritten": 100}

            mock_db.execute_query.side_effect = [
                [{
                    "qualified_name": "test.func",
                    "name": "func",
                    "file_path": "/test.py",
                    "line_number": 1,
                    "pagerank": 0.9,
                    "pagerank_threshold": 0.5,
                    "complexity": 5,
                    "loc": 20,
                    "caller_count": 10,
                    "callee_count": 2
                }],
                []
            ]

            detector = InfluentialCodeDetector(mock_db)
            findings = detector.detect()

            assert detector.severity(findings[0]) == findings[0].severity
