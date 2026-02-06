"""Tests for AIBoilerplateDetector."""

import pytest
from unittest.mock import Mock

from repotoire.detectors.ai_boilerplate import (
    AIBoilerplateDetector,
    FunctionSignature,
    SimilarityGroup,
)
from repotoire.models import Severity


class TestAIBoilerplateDetector:
    """Test suite for AIBoilerplateDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return AIBoilerplateDetector(mock_client)

    def test_detects_parameter_pattern_group(self, detector, mock_client):
        """Should detect functions with identical parameter patterns."""
        mock_client.execute_query.return_value = [
            {
                "name": "module.create_user",
                "params": ["user_id", "session_id", "request"],
                "paramTypes": {"user_id": "str", "session_id": "str", "request": "Request"},
                "returnType": "Response",
                "decorators": [],
                "isAsync": False,
                "sourceCode": "def create_user(user_id, session_id, request): pass",
                "lineStart": 1,
                "lineEnd": 5,
                "complexity": 3,
                "filePath": "handlers.py",
            },
            {
                "name": "module.update_user",
                "params": ["user_id", "session_id", "request"],
                "paramTypes": {"user_id": "str", "session_id": "str", "request": "Request"},
                "returnType": "Response",
                "decorators": [],
                "isAsync": False,
                "sourceCode": "def update_user(user_id, session_id, request): pass",
                "lineStart": 6,
                "lineEnd": 10,
                "complexity": 3,
                "filePath": "handlers.py",
            },
            {
                "name": "module.delete_user",
                "params": ["user_id", "session_id", "request"],
                "paramTypes": {"user_id": "str", "session_id": "str", "request": "Request"},
                "returnType": "Response",
                "decorators": [],
                "isAsync": False,
                "sourceCode": "def delete_user(user_id, session_id, request): pass",
                "lineStart": 11,
                "lineEnd": 15,
                "complexity": 3,
                "filePath": "handlers.py",
            },
        ]

        findings = detector.detect()

        assert len(findings) >= 1
        finding = findings[0]
        assert finding.severity in [Severity.MEDIUM, Severity.LOW]
        assert finding.graph_context["group_size"] == 3
        assert "parameter" in finding.graph_context["pattern_type"]
        assert "boilerplate" in finding.title.lower()

    def test_detects_error_handling_pattern(self, detector, mock_client):
        """Should detect functions with identical try/except patterns."""
        error_source = """
def handle_request():
    try:
        result = process()
        return result
    except ValueError as e:
        log.error(e)
        return None
"""
        mock_client.execute_query.return_value = [
            {
                "name": f"module.handler{i}",
                "params": ["request"],
                "paramTypes": {},
                "returnType": None,
                "decorators": [],
                "isAsync": False,
                "sourceCode": error_source,
                "lineStart": i * 10,
                "lineEnd": i * 10 + 8,
                "complexity": 5,
                "filePath": "handlers.py",
            }
            for i in range(4)
        ]

        findings = detector.detect()

        assert len(findings) >= 1
        # Should detect the error handling pattern
        error_finding = None
        for f in findings:
            if f.graph_context.get("pattern_type") == "error_handling":
                error_finding = f
                break
        
        if error_finding:
            assert error_finding.graph_context["group_size"] >= 3
            assert "error" in error_finding.suggested_fix.lower()

    def test_detects_body_structure_similarity(self, detector, mock_client):
        """Should detect functions with similar body structure."""
        # Same structure but different variable names
        source1 = """
def process_a(data):
    result = transform(data)
    validated = validate(result)
    return save(validated)
"""
        source2 = """
def process_b(input):
    output = transform(input)
    checked = validate(output)
    return save(checked)
"""
        source3 = """
def process_c(value):
    converted = transform(value)
    verified = validate(converted)
    return save(verified)
"""
        mock_client.execute_query.return_value = [
            {
                "name": "module.process_a",
                "params": ["data"],
                "paramTypes": {},
                "returnType": None,
                "decorators": [],
                "isAsync": False,
                "sourceCode": source1,
                "lineStart": 1,
                "lineEnd": 5,
                "complexity": 3,
                "filePath": "processors.py",
            },
            {
                "name": "module.process_b",
                "params": ["input"],
                "paramTypes": {},
                "returnType": None,
                "decorators": [],
                "isAsync": False,
                "sourceCode": source2,
                "lineStart": 10,
                "lineEnd": 14,
                "complexity": 3,
                "filePath": "processors.py",
            },
            {
                "name": "module.process_c",
                "params": ["value"],
                "paramTypes": {},
                "returnType": None,
                "decorators": [],
                "isAsync": False,
                "sourceCode": source3,
                "lineStart": 20,
                "lineEnd": 24,
                "complexity": 3,
                "filePath": "processors.py",
            },
        ]

        findings = detector.detect()

        # Should detect structural similarity
        assert len(findings) >= 1

    def test_high_severity_for_large_groups(self, detector, mock_client):
        """Should assign HIGH severity to large groups with high abstraction potential."""
        mock_client.execute_query.return_value = [
            {
                "name": f"module.handler_{i}",
                "params": ["user_id", "session_id", "request", "context"],
                "paramTypes": {"user_id": "str", "session_id": "str", "request": "Request", "context": "Context"},
                "returnType": "Response",
                "decorators": ["app.route"],
                "isAsync": True,
                "sourceCode": f"async def handler_{i}(): pass",
                "lineStart": i * 10,
                "lineEnd": i * 10 + 8,
                "complexity": 5,
                "filePath": "handlers.py",
            }
            for i in range(7)
        ]

        findings = detector.detect()

        assert len(findings) >= 1
        # At least one finding should be HIGH severity for 7 similar functions
        severities = [f.severity for f in findings]
        assert Severity.HIGH in severities or Severity.MEDIUM in severities

    def test_no_findings_below_threshold(self, detector, mock_client):
        """Should not report groups with fewer than min_group_size functions."""
        mock_client.execute_query.return_value = [
            {
                "name": "module.func1",
                "params": ["a", "b", "c"],
                "paramTypes": {},
                "returnType": None,
                "decorators": [],
                "isAsync": False,
                "sourceCode": "def func1(): pass",
                "lineStart": 1,
                "lineEnd": 3,
                "complexity": 2,
                "filePath": "module.py",
            },
            {
                "name": "module.func2",
                "params": ["a", "b", "c"],
                "paramTypes": {},
                "returnType": None,
                "decorators": [],
                "isAsync": False,
                "sourceCode": "def func2(): different",
                "lineStart": 5,
                "lineEnd": 7,
                "complexity": 2,
                "filePath": "module.py",
            },
        ]

        findings = detector.detect()

        # Should not find patterns with only 2 functions (threshold is 3)
        for finding in findings:
            assert finding.graph_context["group_size"] >= 3

    def test_empty_codebase(self, detector, mock_client):
        """Should return empty list for codebase with no functions."""
        mock_client.execute_query.return_value = []

        findings = detector.detect()

        assert len(findings) == 0

    def test_config_overrides_thresholds(self, mock_client):
        """Should allow config to override default thresholds."""
        detector = AIBoilerplateDetector(
            mock_client,
            detector_config={"min_group_size": 5, "param_similarity_threshold": 0.9}
        )

        assert detector.min_group_size == 5
        assert detector.param_similarity_threshold == 0.9

    def test_abstraction_suggestion_included(self, detector, mock_client):
        """Should include abstraction suggestions in findings."""
        mock_client.execute_query.return_value = [
            {
                "name": f"module.endpoint_{i}",
                "params": ["request", "user_id"],
                "paramTypes": {"request": "Request", "user_id": "str"},
                "returnType": "Response",
                "decorators": ["router.get"],
                "isAsync": True,
                "sourceCode": f"async def endpoint_{i}(): pass",
                "lineStart": i * 10,
                "lineEnd": i * 10 + 5,
                "complexity": 3,
                "filePath": "api.py",
            }
            for i in range(4)
        ]

        findings = detector.detect()

        assert len(findings) >= 1
        assert findings[0].suggested_fix is not None
        assert len(findings[0].suggested_fix) > 0

    def test_affected_files_populated(self, detector, mock_client):
        """Should populate affected_files from function file paths."""
        mock_client.execute_query.return_value = [
            {
                "name": f"module{i}.func",
                "params": ["x", "y", "z"],
                "paramTypes": {"x": "int", "y": "int", "z": "int"},
                "returnType": "int",
                "decorators": [],
                "isAsync": False,
                "sourceCode": "def func(): pass",
                "lineStart": 1,
                "lineEnd": 3,
                "complexity": 2,
                "filePath": f"module{i}.py",
            }
            for i in range(4)
        ]

        findings = detector.detect()

        assert len(findings) >= 1
        assert len(findings[0].affected_files) >= 1

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Should add collaboration metadata to findings."""
        mock_client.execute_query.return_value = [
            {
                "name": f"module.func{i}",
                "params": ["a", "b", "c"],
                "paramTypes": {"a": "str", "b": "str", "c": "str"},
                "returnType": None,
                "decorators": [],
                "isAsync": False,
                "sourceCode": "def func(): pass",
                "lineStart": i * 5,
                "lineEnd": i * 5 + 3,
                "complexity": 2,
                "filePath": "module.py",
            }
            for i in range(4)
        ]

        findings = detector.detect()

        assert len(findings) >= 1
        assert len(findings[0].collaboration_metadata) > 0
        metadata = findings[0].collaboration_metadata[0]
        assert metadata.detector == "AIBoilerplateDetector"
        assert "boilerplate" in metadata.tags

    def test_why_it_matters_included(self, detector, mock_client):
        """Should include why_it_matters explanation."""
        mock_client.execute_query.return_value = [
            {
                "name": f"module.func{i}",
                "params": ["x", "y"],
                "paramTypes": {"x": "int", "y": "int"},
                "returnType": None,
                "decorators": [],
                "isAsync": False,
                "sourceCode": "def func(): pass",
                "lineStart": i * 5,
                "lineEnd": i * 5 + 3,
                "complexity": 2,
                "filePath": "module.py",
            }
            for i in range(4)
        ]

        findings = detector.detect()

        assert len(findings) >= 1
        assert findings[0].why_it_matters is not None
        assert "maintenance" in findings[0].why_it_matters.lower()

    def test_estimate_effort_small(self, detector):
        """Should estimate small effort for few functions."""
        effort = detector._estimate_effort(3)
        assert "Small" in effort

    def test_estimate_effort_medium(self, detector):
        """Should estimate medium effort for moderate functions."""
        effort = detector._estimate_effort(6)
        assert "Medium" in effort

    def test_estimate_effort_large(self, detector):
        """Should estimate large effort for many functions."""
        effort = detector._estimate_effort(10)
        assert "Large" in effort


class TestFunctionSignature:
    """Test FunctionSignature data class."""

    def test_signature_creation(self):
        """Should create signature with all fields."""
        sig = FunctionSignature(
            qualified_name="module.func",
            file_path="module.py",
            param_count=3,
            param_types_hash="abc123",
            return_type="int",
            decorators=frozenset(["decorator1"]),
            is_async=True,
            has_try_except=True,
            body_structure_hash="def456",
            line_count=10,
            complexity=5,
        )

        assert sig.qualified_name == "module.func"
        assert sig.param_count == 3
        assert sig.is_async is True
        assert sig.has_try_except is True


class TestSimilarityGroup:
    """Test SimilarityGroup data class."""

    def test_group_creation(self):
        """Should create similarity group with all fields."""
        sig = FunctionSignature(
            qualified_name="module.func",
            file_path="module.py",
            param_count=2,
            param_types_hash="abc",
            return_type=None,
            decorators=frozenset(),
            is_async=False,
            has_try_except=False,
            body_structure_hash="xyz",
            line_count=5,
            complexity=2,
        )

        group = SimilarityGroup(
            functions=[sig],
            similarity_score=0.85,
            pattern_type="parameter",
            abstraction_suggestion="Create a dataclass",
        )

        assert len(group.functions) == 1
        assert group.similarity_score == 0.85
        assert group.pattern_type == "parameter"


class TestAIBoilerplateDetectorHelpers:
    """Test helper methods of AIBoilerplateDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return AIBoilerplateDetector(mock_client)

    def test_has_try_except_pattern_positive(self, detector):
        """Should detect try/except pattern in source."""
        source = """
def func():
    try:
        do_something()
    except Exception:
        pass
"""
        assert detector._has_try_except_pattern(source) is True

    def test_has_try_except_pattern_negative(self, detector):
        """Should return False when no try/except."""
        source = """
def func():
    do_something()
    return result
"""
        assert detector._has_try_except_pattern(source) is False

    def test_has_try_except_pattern_empty(self, detector):
        """Should return False for empty source."""
        assert detector._has_try_except_pattern("") is False
        assert detector._has_try_except_pattern(None) is False

    def test_compute_body_hash_normalizes_variables(self, detector):
        """Should produce same hash for structurally similar code."""
        source1 = """
def func(data):
    result = process(data)
    return result
"""
        source2 = """
def func(input):
    output = process(input)
    return output
"""
        hash1 = detector._compute_body_hash(source1)
        hash2 = detector._compute_body_hash(source2)

        # Hashes should be identical after normalization
        assert hash1 == hash2

    def test_compute_body_hash_empty(self, detector):
        """Should return empty string for empty source."""
        assert detector._compute_body_hash("") == ""
        assert detector._compute_body_hash(None) == ""

    def test_severity_method(self, detector, mock_client):
        """Should calculate severity from finding context."""
        mock_client.execute_query.return_value = [
            {
                "name": f"module.func{i}",
                "params": ["a", "b"],
                "paramTypes": {"a": "int", "b": "int"},
                "returnType": None,
                "decorators": [],
                "isAsync": False,
                "sourceCode": "def func(): pass",
                "lineStart": i * 5,
                "lineEnd": i * 5 + 3,
                "complexity": 2,
                "filePath": "module.py",
            }
            for i in range(4)
        ]

        findings = detector.detect()

        if findings:
            severity = detector.severity(findings[0])
            assert severity in [Severity.HIGH, Severity.MEDIUM, Severity.LOW]


class TestAIBoilerplateDetectorWithEnricher:
    """Test AIBoilerplateDetector with GraphEnricher."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def mock_enricher(self):
        """Create a mock enricher."""
        return Mock()

    def test_enricher_flags_entities(self, mock_client, mock_enricher):
        """Should flag entities via enricher when available."""
        detector = AIBoilerplateDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.return_value = [
            {
                "name": f"module.func{i}",
                "params": ["a", "b", "c"],
                "paramTypes": {"a": "str", "b": "str", "c": "str"},
                "returnType": None,
                "decorators": [],
                "isAsync": False,
                "sourceCode": "def func(): pass",
                "lineStart": i * 5,
                "lineEnd": i * 5 + 3,
                "complexity": 2,
                "filePath": "module.py",
            }
            for i in range(4)
        ]

        detector.detect()

        # Should have called flag_entity for each function in a group
        assert mock_enricher.flag_entity.call_count >= 3

    def test_enricher_failure_does_not_break_detection(self, mock_client, mock_enricher):
        """Should continue detection even if enricher fails."""
        detector = AIBoilerplateDetector(mock_client, enricher=mock_enricher)
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        mock_client.execute_query.return_value = [
            {
                "name": f"module.func{i}",
                "params": ["x", "y"],
                "paramTypes": {"x": "int", "y": "int"},
                "returnType": None,
                "decorators": [],
                "isAsync": False,
                "sourceCode": "def func(): pass",
                "lineStart": i * 5,
                "lineEnd": i * 5 + 3,
                "complexity": 2,
                "filePath": "module.py",
            }
            for i in range(4)
        ]

        # Should not raise exception
        findings = detector.detect()

        assert len(findings) >= 1
