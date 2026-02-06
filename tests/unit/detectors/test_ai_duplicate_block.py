"""Tests for AIDuplicateBlockDetector."""

import pytest
from unittest.mock import Mock

from repotoire.detectors.ai_duplicate_block import AIDuplicateBlockDetector
from repotoire.models import Severity


class TestAIDuplicateBlockDetector:
    """Test suite for AIDuplicateBlockDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return AIDuplicateBlockDetector(mock_client)

    def test_detects_near_duplicate_functions(self, detector, mock_client):
        """Test detection of near-duplicate functions."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module_a.py::process_data",
                "name": "process_data",
                "line_start": 10,
                "line_end": 25,
                "loc": 15,
                "parameters": ["data", "config"],
                "complexity": 5,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "decorators": [],
                "file_path": "module_a.py",
            },
            {
                "qualified_name": "module_b.py::handle_data",
                "name": "handle_data",
                "line_start": 20,
                "line_end": 35,
                "loc": 15,
                "parameters": ["input", "options"],
                "complexity": 5,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "decorators": [],
                "file_path": "module_b.py",
            },
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert "process_data" in findings[0].title
        assert "handle_data" in findings[0].title
        assert "module_a.py" in findings[0].affected_files
        assert "module_b.py" in findings[0].affected_files

    def test_ignores_same_file_duplicates(self, detector, mock_client):
        """Test that functions in the same file are not flagged."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.py::func_a",
                "name": "func_a",
                "line_start": 10,
                "line_end": 25,
                "loc": 15,
                "parameters": ["x", "y"],
                "complexity": 5,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "module.py",
            },
            {
                "qualified_name": "module.py::func_b",
                "name": "func_b",
                "line_start": 30,
                "line_end": 45,
                "loc": 15,
                "parameters": ["a", "b"],
                "complexity": 5,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "module.py",
            },
        ]

        findings = detector.detect()

        # Same file functions should be ignored
        assert len(findings) == 0

    def test_ignores_low_similarity_functions(self, detector, mock_client):
        """Test that functions with low similarity are not flagged."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module_a.py::simple_func",
                "name": "simple_func",
                "line_start": 10,
                "line_end": 15,
                "loc": 5,
                "parameters": [],
                "complexity": 1,
                "is_method": False,
                "is_async": False,
                "has_return": False,
                "has_yield": False,
                "file_path": "module_a.py",
            },
            {
                "qualified_name": "module_b.py::complex_func",
                "name": "complex_func",
                "line_start": 20,
                "line_end": 60,
                "loc": 40,
                "parameters": ["a", "b", "c", "d", "e"],
                "complexity": 15,
                "is_method": True,
                "is_async": True,
                "has_return": True,
                "has_yield": True,
                "file_path": "module_b.py",
            },
        ]

        findings = detector.detect()

        # Very different functions should not be flagged
        assert len(findings) == 0

    def test_empty_codebase_returns_no_findings(self, detector, mock_client):
        """Test that empty codebase returns no findings."""
        mock_client.execute_query.return_value = []

        findings = detector.detect()

        assert len(findings) == 0

    def test_query_error_returns_empty(self, detector, mock_client):
        """Test query error returns empty findings list."""
        mock_client.execute_query.side_effect = Exception("Database error")

        findings = detector.detect()

        assert len(findings) == 0

    def test_config_overrides_thresholds(self, mock_client):
        """Test config can override default thresholds."""
        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={
                "similarity_threshold": 0.90,
                "min_loc": 10,
                "max_findings": 25,
            }
        )

        assert detector.similarity_threshold == 0.90
        assert detector.min_loc == 10
        assert detector.max_findings == 25

    def test_severity_always_high(self, detector, mock_client):
        """Test severity is always HIGH for AI duplicates."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module_a.py::func1",
                "name": "func1",
                "line_start": 10,
                "line_end": 30,
                "loc": 20,
                "parameters": ["x"],
                "complexity": 8,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "module_a.py",
            },
            {
                "qualified_name": "module_b.py::func2",
                "name": "func2",
                "line_start": 10,
                "line_end": 30,
                "loc": 20,
                "parameters": ["y"],
                "complexity": 8,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "module_b.py",
            },
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert detector.severity(findings[0]) == Severity.HIGH

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Test collaboration metadata is added to findings."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module_a.py::process",
                "name": "process",
                "line_start": 10,
                "line_end": 30,
                "loc": 20,
                "parameters": ["data"],
                "complexity": 6,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "module_a.py",
            },
            {
                "qualified_name": "module_b.py::handle",
                "name": "handle",
                "line_start": 10,
                "line_end": 30,
                "loc": 20,
                "parameters": ["info"],
                "complexity": 6,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "module_b.py",
            },
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert len(findings[0].collaboration_metadata) > 0
        metadata = findings[0].collaboration_metadata[0]
        assert metadata.detector == "AIDuplicateBlockDetector"
        assert "ai_duplicate" in metadata.tags
        assert "duplication" in metadata.tags

    def test_graph_context_includes_similarity(self, detector, mock_client):
        """Test graph context includes similarity score."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "a.py::f1",
                "name": "f1",
                "line_start": 1,
                "line_end": 20,
                "loc": 19,
                "parameters": ["x", "y"],
                "complexity": 7,
                "is_method": True,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "a.py",
            },
            {
                "qualified_name": "b.py::f2",
                "name": "f2",
                "line_start": 1,
                "line_end": 20,
                "loc": 19,
                "parameters": ["a", "b"],
                "complexity": 7,
                "is_method": True,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "b.py",
            },
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "similarity" in findings[0].graph_context
        assert findings[0].graph_context["similarity"] >= 0.85

    def test_multiple_duplicate_pairs(self, detector, mock_client):
        """Test detection of multiple duplicate pairs."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "a.py::process_a",
                "name": "process_a",
                "loc": 20,
                "parameters": ["data"],
                "complexity": 5,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "a.py",
            },
            {
                "qualified_name": "b.py::process_b",
                "name": "process_b",
                "loc": 20,
                "parameters": ["input"],
                "complexity": 5,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "b.py",
            },
            {
                "qualified_name": "c.py::handle_c",
                "name": "handle_c",
                "loc": 30,
                "parameters": ["x", "y", "z"],
                "complexity": 10,
                "is_method": True,
                "is_async": True,
                "has_return": True,
                "has_yield": False,
                "file_path": "c.py",
            },
            {
                "qualified_name": "d.py::handle_d",
                "name": "handle_d",
                "loc": 30,
                "parameters": ["a", "b", "c"],
                "complexity": 10,
                "is_method": True,
                "is_async": True,
                "has_return": True,
                "has_yield": False,
                "file_path": "d.py",
            },
        ]

        findings = detector.detect()

        # Should find 2 duplicate pairs
        assert len(findings) == 2


class TestAIDuplicateBlockDetectorNormalization:
    """Test normalization and similarity calculation."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance."""
        return AIDuplicateBlockDetector(mock_client)

    def test_normalize_function_structure(self, detector):
        """Test function normalization creates structural fingerprint."""
        func = {
            "loc": 20,
            "complexity": 5,
            "parameters": ["a", "b"],
            "is_method": True,
            "is_async": False,
            "has_return": True,
            "has_yield": False,
        }

        normalized = detector._normalize_function(func)

        assert "LOC:20" in normalized
        assert "COMPLEXITY:5" in normalized
        assert "PARAMS:2" in normalized
        assert "METHOD" in normalized
        assert "RETURNS" in normalized

    def test_calculate_similarity_identical(self, detector):
        """Test similarity calculation for identical functions."""
        func1 = {
            "loc": 20,
            "complexity": 5,
            "parameters": ["a", "b"],
            "is_method": True,
            "is_async": False,
            "has_return": True,
            "has_yield": False,
        }
        func2 = {
            "loc": 20,
            "complexity": 5,
            "parameters": ["x", "y"],
            "is_method": True,
            "is_async": False,
            "has_return": True,
            "has_yield": False,
        }

        similarity = detector._calculate_similarity(func1, func2)

        assert similarity == 1.0

    def test_calculate_similarity_different(self, detector):
        """Test similarity calculation for different functions."""
        func1 = {
            "loc": 10,
            "complexity": 2,
            "parameters": [],
            "is_method": False,
            "is_async": False,
            "has_return": False,
            "has_yield": False,
        }
        func2 = {
            "loc": 50,
            "complexity": 15,
            "parameters": ["a", "b", "c", "d"],
            "is_method": True,
            "is_async": True,
            "has_return": True,
            "has_yield": True,
        }

        similarity = detector._calculate_similarity(func1, func2)

        assert similarity < 0.5

    def test_calculate_similarity_partial(self, detector):
        """Test similarity calculation for partially similar functions."""
        func1 = {
            "loc": 25,
            "complexity": 8,
            "parameters": ["a", "b"],
            "is_method": True,
            "is_async": False,
            "has_return": True,
            "has_yield": False,
        }
        func2 = {
            "loc": 30,
            "complexity": 10,
            "parameters": ["x", "y", "z"],
            "is_method": True,
            "is_async": False,
            "has_return": True,
            "has_yield": False,
        }

        similarity = detector._calculate_similarity(func1, func2)

        # Should be somewhere in between
        assert 0.7 < similarity < 1.0


class TestAIDuplicateBlockDetectorWithEnricher:
    """Test AIDuplicateBlockDetector with GraphEnricher."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        return client

    @pytest.fixture
    def mock_enricher(self):
        """Create a mock enricher."""
        return Mock()

    def test_enricher_flags_entities(self, mock_client, mock_enricher):
        """Test entities are flagged via enricher."""
        detector = AIDuplicateBlockDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "a.py::func_a",
                "name": "func_a",
                "loc": 20,
                "parameters": ["x"],
                "complexity": 5,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "a.py",
            },
            {
                "qualified_name": "b.py::func_b",
                "name": "func_b",
                "loc": 20,
                "parameters": ["y"],
                "complexity": 5,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "b.py",
            },
        ]

        detector.detect()

        # Should flag both entities
        assert mock_enricher.flag_entity.call_count == 2

    def test_enricher_failure_does_not_break_detection(self, mock_client, mock_enricher):
        """Test detection continues even if enricher fails."""
        detector = AIDuplicateBlockDetector(mock_client, enricher=mock_enricher)
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "a.py::f1",
                "name": "f1",
                "loc": 15,
                "parameters": ["x"],
                "complexity": 4,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "a.py",
            },
            {
                "qualified_name": "b.py::f2",
                "name": "f2",
                "loc": 15,
                "parameters": ["y"],
                "complexity": 4,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "b.py",
            },
        ]

        # Should not raise exception
        findings = detector.detect()

        assert len(findings) == 1


class TestAIDuplicateBlockDetectorEdgeCases:
    """Test edge cases for AIDuplicateBlockDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        return Mock()

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance."""
        return AIDuplicateBlockDetector(mock_client)

    def test_handles_missing_parameters(self, detector, mock_client):
        """Test handling of functions with missing parameter data."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "a.py::func",
                "name": "func",
                "loc": 20,
                "parameters": None,
                "complexity": 5,
                "is_method": False,
                "file_path": "a.py",
            },
            {
                "qualified_name": "b.py::func2",
                "name": "func2",
                "loc": 20,
                "parameters": None,
                "complexity": 5,
                "is_method": False,
                "file_path": "b.py",
            },
        ]

        # Should not raise
        findings = detector.detect()
        assert isinstance(findings, list)

    def test_handles_missing_complexity(self, detector, mock_client):
        """Test handling of functions with missing complexity."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "a.py::func",
                "name": "func",
                "loc": 15,
                "parameters": ["x"],
                "complexity": None,
                "is_method": False,
                "file_path": "a.py",
            },
            {
                "qualified_name": "b.py::func2",
                "name": "func2",
                "loc": 15,
                "parameters": ["y"],
                "complexity": None,
                "is_method": False,
                "file_path": "b.py",
            },
        ]

        # Should not raise
        findings = detector.detect()
        assert isinstance(findings, list)

    def test_respects_max_findings_limit(self, mock_client):
        """Test that max_findings limit is respected."""
        detector = AIDuplicateBlockDetector(
            mock_client,
            detector_config={"max_findings": 2}
        )

        # Create many similar functions
        mock_client.execute_query.return_value = [
            {
                "qualified_name": f"module{i}.py::func{i}",
                "name": f"func{i}",
                "loc": 20,
                "parameters": ["x"],
                "complexity": 5,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": f"module{i}.py",
            }
            for i in range(10)
        ]

        findings = detector.detect()

        assert len(findings) <= 2

    def test_finding_includes_affected_nodes(self, detector, mock_client):
        """Test that findings include both affected nodes."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "src/utils.py::helper",
                "name": "helper",
                "loc": 25,
                "parameters": ["data"],
                "complexity": 6,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "src/utils.py",
            },
            {
                "qualified_name": "lib/tools.py::processor",
                "name": "processor",
                "loc": 25,
                "parameters": ["input"],
                "complexity": 6,
                "is_method": False,
                "is_async": False,
                "has_return": True,
                "has_yield": False,
                "file_path": "lib/tools.py",
            },
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "src/utils.py::helper" in findings[0].affected_nodes
        assert "lib/tools.py::processor" in findings[0].affected_nodes
