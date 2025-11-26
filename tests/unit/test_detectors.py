"""Unit tests for code smell detectors."""

from unittest.mock import Mock

import pytest

from repotoire.detectors.circular_dependency import CircularDependencyDetector
from repotoire.detectors.dead_code import DeadCodeDetector
from repotoire.detectors.god_class import GodClassDetector
from repotoire.models import Severity


@pytest.fixture
def mock_db():
    """Create a mock Neo4j client."""
    db = Mock()
    db.execute_query = Mock()
    return db


class TestCircularDependencyDetector:
    """Test CircularDependencyDetector."""

    def test_no_circular_dependencies(self, mock_db):
        """Test when no cycles exist."""
        mock_db.execute_query.return_value = []

        detector = CircularDependencyDetector(mock_db)
        findings = detector.detect()

        assert len(findings) == 0

    def test_finds_simple_cycle(self, mock_db):
        """Test detecting a simple A->B->A cycle."""
        mock_db.execute_query.return_value = [
            {
                "cycle": ["/path/to/a.py", "/path/to/b.py", "/path/to/a.py"],
                "cycle_length": 2
            }
        ]

        detector = CircularDependencyDetector(mock_db)
        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].detector == "CircularDependencyDetector"
        assert "Circular dependency" in findings[0].title
        assert findings[0].severity in [Severity.LOW, Severity.MEDIUM]

    def test_finds_complex_cycle(self, mock_db):
        """Test detecting a complex multi-file cycle."""
        mock_db.execute_query.return_value = [
            {
                "cycle": [f"/path/{i}.py" for i in range(6)],
                "cycle_length": 6
            }
        ]

        detector = CircularDependencyDetector(mock_db)
        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH  # 6-file cycle is HIGH severity

    def test_severity_calculation(self, mock_db):
        """Test severity based on cycle length."""
        detector = CircularDependencyDetector(mock_db)

        # Small cycle (2 files) = LOW
        assert detector._calculate_severity(2) == Severity.LOW

        # Medium cycle (3-4 files) = MEDIUM
        assert detector._calculate_severity(3) == Severity.MEDIUM

        # Large cycle (5-9 files) = HIGH
        assert detector._calculate_severity(5) == Severity.HIGH

        # Very large cycle (10+ files) = CRITICAL
        assert detector._calculate_severity(10) == Severity.CRITICAL

    def test_deduplicates_cycles(self, mock_db):
        """Test that duplicate cycles are filtered out."""
        # Same cycle detected from different starting points
        mock_db.execute_query.return_value = [
            {"cycle": ["/a.py", "/b.py", "/c.py"], "cycle_length": 3},
            {"cycle": ["/b.py", "/c.py", "/a.py"], "cycle_length": 3},  # Same cycle, different start
            {"cycle": ["/c.py", "/a.py", "/b.py"], "cycle_length": 3},  # Same cycle, different start
        ]

        detector = CircularDependencyDetector(mock_db)
        findings = detector.detect()

        # Should deduplicate to single finding
        assert len(findings) == 1

    def test_normalize_cycle(self, mock_db):
        """Test cycle normalization."""
        detector = CircularDependencyDetector(mock_db)

        cycle1 = ["/a.py", "/b.py", "/c.py"]
        cycle2 = ["/b.py", "/c.py", "/a.py"]
        cycle3 = ["/c.py", "/a.py", "/b.py"]

        norm1 = detector._normalize_cycle(cycle1)
        norm2 = detector._normalize_cycle(cycle2)
        norm3 = detector._normalize_cycle(cycle3)

        # All should normalize to same representation
        assert norm1 == norm2 == norm3


class TestDeadCodeDetector:
    """Test DeadCodeDetector."""

    def test_no_dead_code(self, mock_db):
        """Test when no dead code exists."""
        mock_db.execute_query.return_value = []

        detector = DeadCodeDetector(mock_db)
        findings = detector.detect()

        assert len(findings) == 0

    def test_finds_dead_function(self, mock_db):
        """Test detecting unused function."""
        mock_db.execute_query.side_effect = [
            # First call: find dead functions
            [{
                "qualified_name": "/test.py::unused_function",
                "name": "unused_function",
                "file_path": "/test.py",
                "line_start": 10,
                "complexity": 5,
                "containing_file": "/test.py",
                "decorators": []
            }],
            # Second call: find dead classes (return empty)
            []
        ]

        detector = DeadCodeDetector(mock_db)
        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].detector == "DeadCodeDetector"
        assert "unused_function" in findings[0].title
        assert findings[0].graph_context["type"] == "function"

    def test_finds_dead_class(self, mock_db):
        """Test detecting unused class."""
        mock_db.execute_query.side_effect = [
            # First call: find dead functions (return empty)
            [],
            # Second call: find dead classes
            [{
                "qualified_name": "/test.py::UnusedClass",
                "name": "UnusedClass",
                "file_path": "/test.py",
                "complexity": 10,
                "containing_file": "/test.py",
                "method_count": 3
            }]
        ]

        detector = DeadCodeDetector(mock_db)
        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].detector == "DeadCodeDetector"
        assert "UnusedClass" in findings[0].title
        assert findings[0].graph_context["type"] == "class"

    def test_filters_magic_methods(self, mock_db):
        """Test that magic methods are filtered out."""
        mock_db.execute_query.side_effect = [
            [{
                "qualified_name": "/test.py::MyClass.__str__",
                "name": "__str__",
                "file_path": "/test.py",
                "line_start": 10,
                "complexity": 1,
                "containing_file": "/test.py",
                "decorators": []
            }],
            []
        ]

        detector = DeadCodeDetector(mock_db)
        findings = detector.detect()

        # __str__ should be filtered out
        assert len(findings) == 0

    def test_filters_entry_points(self, mock_db):
        """Test that entry points are filtered out."""
        mock_db.execute_query.side_effect = [
            [{
                "qualified_name": "/test.py::main",
                "name": "main",
                "file_path": "/test.py",
                "line_start": 1,
                "complexity": 5,
                "containing_file": "/test.py",
                "decorators": []
            }],
            []
        ]

        detector = DeadCodeDetector(mock_db)
        findings = detector.detect()

        # main should be filtered out
        assert len(findings) == 0

    def test_function_severity_calculation(self, mock_db):
        """Test severity calculation for dead functions."""
        detector = DeadCodeDetector(mock_db)

        # Low complexity = LOW severity
        assert detector._calculate_function_severity(5) == Severity.LOW

        # Medium complexity = MEDIUM severity
        assert detector._calculate_function_severity(12) == Severity.MEDIUM

        # High complexity = HIGH severity
        assert detector._calculate_function_severity(25) == Severity.HIGH

    def test_class_severity_calculation(self, mock_db):
        """Test severity calculation for dead classes."""
        detector = DeadCodeDetector(mock_db)

        # Small class = LOW
        assert detector._calculate_class_severity(3, 10) == Severity.LOW

        # Medium class = MEDIUM
        assert detector._calculate_class_severity(7, 25) == Severity.MEDIUM

        # Large class = HIGH
        assert detector._calculate_class_severity(15, 60) == Severity.HIGH


class TestGodClassDetector:
    """Test GodClassDetector."""

    def test_no_god_classes(self, mock_db):
        """Test when no god classes exist."""
        mock_db.execute_query.return_value = []

        detector = GodClassDetector(mock_db)
        findings = detector.detect()

        assert len(findings) == 0

    def test_finds_god_class_high_method_count(self, mock_db):
        """Test detecting god class with high method count."""
        mock_db.execute_query.side_effect = [
            # Main query
            [{
                "qualified_name": "/test.py::GodClass",
                "name": "GodClass",
                "file_path": "/test.py",
                "line_start": 1,
                "line_end": 500,
                "containing_file": "/test.py",
                "method_count": 25,  # Very high
                "total_complexity": 80,
                "coupling_count": 30,
                "loc": 400,
                "is_abstract": False
            }],
            # LCOM calculation query - return low cohesion data
            [{"method_field_pairs": [
                {"method": "m1", "fields": ["a"]},
                {"method": "m2", "fields": ["b"]},
                {"method": "m3", "fields": ["c"]},
            ], "method_count": 25}]
        ]

        # Disable community analysis to avoid additional queries
        detector = GodClassDetector(mock_db, detector_config={
            "use_community_analysis": False,
            "use_semantic_analysis": False,
        })
        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].detector == "GodClassDetector"
        assert "GodClass" in findings[0].title
        assert findings[0].graph_context["method_count"] == 25

    def test_finds_god_class_high_complexity(self, mock_db):
        """Test detecting god class with high complexity."""
        mock_db.execute_query.side_effect = [
            [{
                "qualified_name": "/test.py::ComplexClass",
                "name": "ComplexClass",
                "file_path": "/test.py",
                "line_start": 1,
                "line_end": 400,
                "containing_file": "/test.py",
                "method_count": 12,
                "total_complexity": 120,  # Very high
                "coupling_count": 25,
                "loc": 350,
                "is_abstract": False
            }],
            # LCOM calculation query - return low cohesion data
            [{"method_field_pairs": [
                {"method": "m1", "fields": ["a"]},
                {"method": "m2", "fields": ["b"]},
                {"method": "m3", "fields": ["c"]},
            ], "method_count": 12}]
        ]

        # Disable community analysis to avoid additional queries
        detector = GodClassDetector(mock_db, detector_config={
            "use_community_analysis": False,
            "use_semantic_analysis": False,
        })
        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].graph_context["total_complexity"] == 120

    def test_filters_abstract_base_classes(self, mock_db):
        """Test that small abstract base classes are filtered."""
        mock_db.execute_query.return_value = [{
            "qualified_name": "/test.py::BaseClass",
            "name": "BaseClass",
            "file_path": "/test.py",
            "line_start": 1,
            "line_end": 200,
            "containing_file": "/test.py",
            "method_count": 15,
            "total_complexity": 40,
            "coupling_count": 10,
            "loc": 150,
            "is_abstract": True  # Abstract class
        }]

        detector = GodClassDetector(mock_db)
        findings = detector.detect()

        # Small abstract class should be filtered
        assert len(findings) == 0

    def test_is_god_class_logic(self, mock_db):
        """Test god class identification logic."""
        detector = GodClassDetector(mock_db)

        # Single severe issue (very high method count)
        is_god, reason = detector._is_god_class(25, 50, 20, 300, 0.3)
        assert is_god is True
        assert "very high method count" in reason

        # Multiple moderate issues
        is_god, reason = detector._is_god_class(16, 55, 35, 350, 0.65)
        assert is_god is True
        assert "," in reason  # Multiple reasons

        # Not a god class (under thresholds)
        is_god, reason = detector._is_god_class(10, 30, 15, 200, 0.4)
        assert is_god is False

    def test_severity_calculation(self, mock_db):
        """Test severity calculation for god classes."""
        detector = GodClassDetector(mock_db)

        # Critical: multiple severe violations
        severity = detector._calculate_severity(35, 160, 75, 1100, 0.85)
        assert severity == Severity.CRITICAL

        # High: one critical or multiple high violations
        severity = detector._calculate_severity(25, 110, 55, 600, 0.7)
        assert severity == Severity.HIGH

        # Medium: moderate violations
        severity = detector._calculate_severity(17, 60, 35, 350, 0.5)
        assert severity == Severity.MEDIUM

        # Low: minor violations
        severity = detector._calculate_severity(12, 40, 20, 250, 0.3)
        assert severity == Severity.LOW

    def test_lcom_calculation(self, mock_db):
        """Test LCOM (Lack of Cohesion) metric calculation."""
        mock_db.execute_query.return_value = [{
            "method_field_pairs": [
                {"method": "method1", "fields": ["field_a", "field_b"]},
                {"method": "method2", "fields": ["field_a", "field_b"]},  # Shares fields with method1
                {"method": "method3", "fields": []},  # Shares nothing
            ],
            "method_count": 3
        }]

        detector = GodClassDetector(mock_db)
        lcom = detector._calculate_lcom("/test.py::MyClass")

        # 2 pairs share fields, 1 doesn't -> LCOM should be > 0
        assert 0.0 <= lcom <= 1.0

    def test_lcom_handles_errors(self, mock_db):
        """Test LCOM calculation handles errors gracefully."""
        mock_db.execute_query.side_effect = Exception("Query failed")

        detector = GodClassDetector(mock_db)
        lcom = detector._calculate_lcom("/test.py::MyClass")

        # Should return neutral value on error
        assert lcom == 0.5

    def test_configurable_thresholds(self, mock_db):
        """Test that detector respects custom threshold configuration."""
        # Create detector with custom thresholds (much lower than defaults)
        custom_config = {
            "god_class_high_method_count": 10,  # Default: 20
            "god_class_medium_method_count": 5,  # Default: 15
            "god_class_high_complexity": 50,  # Default: 100
            "god_class_medium_complexity": 25,  # Default: 50
            "god_class_high_loc": 200,  # Default: 500
            "god_class_medium_loc": 100,  # Default: 300
            "god_class_high_lcom": 0.7,  # Default: 0.8
            "god_class_medium_lcom": 0.5,  # Default: 0.6
        }

        detector = GodClassDetector(mock_db, detector_config=custom_config)

        # Verify thresholds were set
        assert detector.high_method_count == 10
        assert detector.medium_method_count == 5
        assert detector.high_complexity == 50
        assert detector.medium_complexity == 25
        assert detector.high_loc == 200
        assert detector.medium_loc == 100
        assert detector.high_lcom == 0.7
        assert detector.medium_lcom == 0.5

        # Test that a class with 8 methods is now detected (above medium threshold of 5)
        is_god, reason = detector._is_god_class(8, 30, 20, 150, 0.4)
        # With default thresholds (15), this wouldn't be detected
        # With custom thresholds (5), this should be detected
        assert is_god is True
        assert "high method count" in reason

    def test_default_thresholds_when_no_config(self, mock_db):
        """Test that detector uses defaults when no config is provided."""
        detector = GodClassDetector(mock_db)

        # Verify default thresholds
        assert detector.high_method_count == 20
        assert detector.medium_method_count == 15
        assert detector.high_complexity == 100
        assert detector.medium_complexity == 50
        assert detector.high_loc == 500
        assert detector.medium_loc == 300
        assert detector.high_lcom == 0.8
        assert detector.medium_lcom == 0.6
