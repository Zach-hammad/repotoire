"""Unit tests for DeadCode + Vulture cross-validation (REPO-153).

Tests the cross-validation between DeadCodeDetector (graph-based) and
VultureDetector (AST-based) for high-confidence dead code detection.
"""

import tempfile
from unittest.mock import Mock, patch
from datetime import datetime
from pathlib import Path

import pytest

from repotoire.detectors.dead_code import DeadCodeDetector
from repotoire.detectors.vulture_detector import VultureDetector
from repotoire.models import CollaborationMetadata, Finding, Severity


@pytest.fixture
def mock_db():
    """Create a mock Neo4j client."""
    db = Mock()
    db.execute_query = Mock(return_value=[])
    return db


@pytest.fixture
def mock_enricher():
    """Create a mock GraphEnricher."""
    enricher = Mock()
    enricher.flag_entity = Mock()
    return enricher


@pytest.fixture
def temp_repo_path():
    """Create a temporary directory for repository path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestDeadCodeExtractVultureUnused:
    """Test DeadCodeDetector._extract_vulture_unused() method."""

    def test_extract_vulture_unused_empty(self, mock_db):
        """Test extraction with no previous findings."""
        detector = DeadCodeDetector(mock_db)
        result = detector._extract_vulture_unused(None)
        assert result == {}

        result = detector._extract_vulture_unused([])
        assert result == {}

    def test_extract_vulture_unused_no_vulture_findings(self, mock_db):
        """Test extraction ignores non-Vulture findings."""
        detector = DeadCodeDetector(mock_db)

        other_finding = Finding(
            id="test-123",
            detector="RuffDetector",  # Not VultureDetector
            severity=Severity.HIGH,
            title="Test Finding",
            description="Test",
            affected_nodes=["test"],
            affected_files=["/test.py"],
        )

        result = detector._extract_vulture_unused([other_finding])
        assert result == {}

    def test_extract_vulture_unused_extracts_correctly(self, mock_db):
        """Test extraction correctly extracts Vulture findings."""
        detector = DeadCodeDetector(mock_db)

        vulture_finding = Finding(
            id="vulture-123",
            detector="VultureDetector",
            severity=Severity.LOW,
            title="Unused function: my_func",
            description="Test",
            affected_nodes=[],
            affected_files=["/path/to/module.py"],
            graph_context={
                "item_name": "my_func",
                "item_type": "function",
                "confidence": 90,
                "line": 42,
            },
        )

        result = detector._extract_vulture_unused([vulture_finding])

        # Should have both exact key and name-only key
        assert "/path/to/module.py:my_func" in result
        assert "my_func" in result

        # Check values
        entry = result["/path/to/module.py:my_func"]
        assert entry["name"] == "my_func"
        assert entry["type"] == "function"
        assert entry["confidence"] == 90
        assert entry["file"] == "/path/to/module.py"
        assert entry["line"] == 42

    def test_extract_vulture_unused_multiple_findings(self, mock_db):
        """Test extraction with multiple Vulture findings."""
        detector = DeadCodeDetector(mock_db)

        findings = [
            Finding(
                id="vulture-1",
                detector="VultureDetector",
                severity=Severity.LOW,
                title="Unused function: func1",
                description="Test",
                affected_nodes=[],
                affected_files=["/module_a.py"],
                graph_context={
                    "item_name": "func1",
                    "item_type": "function",
                    "confidence": 90,
                },
            ),
            Finding(
                id="vulture-2",
                detector="VultureDetector",
                severity=Severity.LOW,
                title="Unused class: MyClass",
                description="Test",
                affected_nodes=[],
                affected_files=["/module_b.py"],
                graph_context={
                    "item_name": "MyClass",
                    "item_type": "class",
                    "confidence": 95,
                },
            ),
        ]

        result = detector._extract_vulture_unused(findings)

        assert len(result) == 4  # 2 exact keys + 2 name keys
        assert "/module_a.py:func1" in result
        assert "/module_b.py:MyClass" in result
        assert "func1" in result
        assert "MyClass" in result

    def test_extract_vulture_unused_skips_missing_item_name(self, mock_db):
        """Test extraction skips findings without item_name."""
        detector = DeadCodeDetector(mock_db)

        finding = Finding(
            id="vulture-bad",
            detector="VultureDetector",
            severity=Severity.LOW,
            title="Test",
            description="Test",
            affected_nodes=[],
            affected_files=["/test.py"],
            graph_context={"item_type": "function"},  # Missing item_name
        )

        result = detector._extract_vulture_unused([finding])
        assert result == {}


class TestDeadCodeCheckVultureConfirms:
    """Test DeadCodeDetector._check_vulture_confirms() method."""

    def test_check_vulture_confirms_exact_match(self, mock_db):
        """Test exact file:name match."""
        detector = DeadCodeDetector(mock_db)

        vulture_unused = {
            "/module.py:my_func": {"name": "my_func", "confidence": 95},
            "my_func": {"name": "my_func", "confidence": 95},
        }

        result = detector._check_vulture_confirms("my_func", "/module.py", vulture_unused)
        assert result is not None
        assert result["name"] == "my_func"
        assert result["confidence"] == 95

    def test_check_vulture_confirms_name_only_match(self, mock_db):
        """Test name-only fallback match."""
        detector = DeadCodeDetector(mock_db)

        vulture_unused = {
            "/other_module.py:my_func": {"name": "my_func", "confidence": 90},
            "my_func": {"name": "my_func", "confidence": 90},
        }

        # Different file path, but same name should match via fallback
        result = detector._check_vulture_confirms("my_func", "/different.py", vulture_unused)
        assert result is not None
        assert result["name"] == "my_func"

    def test_check_vulture_confirms_no_match(self, mock_db):
        """Test no match returns None."""
        detector = DeadCodeDetector(mock_db)

        vulture_unused = {
            "/module.py:other_func": {"name": "other_func", "confidence": 95},
            "other_func": {"name": "other_func", "confidence": 95},
        }

        result = detector._check_vulture_confirms("my_func", "/module.py", vulture_unused)
        assert result is None

    def test_check_vulture_confirms_prefers_exact_match(self, mock_db):
        """Test exact match is preferred over name-only match."""
        detector = DeadCodeDetector(mock_db)

        vulture_unused = {
            "/module.py:my_func": {"name": "my_func", "confidence": 99, "source": "exact"},
            "my_func": {"name": "my_func", "confidence": 80, "source": "name_only"},
        }

        result = detector._check_vulture_confirms("my_func", "/module.py", vulture_unused)
        assert result["confidence"] == 99
        assert result["source"] == "exact"


class TestDeadCodeConfidenceScoring:
    """Test confidence scoring with cross-validation."""

    def test_graph_only_confidence(self, mock_db):
        """Test confidence is 70% when graph-only."""
        detector = DeadCodeDetector(mock_db)

        # Mock query to return a dead function (first call) and no classes (second call)
        # Using a name that won't be filtered by internal patterns
        mock_db.execute_query.side_effect = [
            [
                {
                    "qualified_name": "module._obsolete_thing",
                    "name": "_obsolete_thing",  # Private method, won't hit public API filters
                    "file_path": "/module.py",
                    "line_start": 10,
                    "complexity": 5,
                    "containing_file": "/module.py",
                    "decorators": [],
                }
            ],
            [],  # No dead classes
        ]

        findings = detector.detect(previous_findings=[])
        assert len(findings) == 1

        # Check confidence is base (70%)
        assert findings[0].graph_context["confidence"] == 0.70
        assert findings[0].graph_context["vulture_confirmed"] is False
        assert findings[0].graph_context["safe_to_remove"] is False
        assert "graph_analysis" in findings[0].graph_context["validators"]
        assert "vulture" not in findings[0].graph_context["validators"]

    def test_vulture_validated_confidence(self, mock_db):
        """Test confidence is 95% when Vulture validates."""
        detector = DeadCodeDetector(mock_db)

        # Create a Vulture finding
        vulture_finding = Finding(
            id="vulture-123",
            detector="VultureDetector",
            severity=Severity.LOW,
            title="Unused function: _obsolete_thing",
            description="Test",
            affected_nodes=[],
            affected_files=["/module.py"],
            graph_context={
                "item_name": "_obsolete_thing",
                "item_type": "function",
                "confidence": 92,
            },
        )

        # Mock query to return the same dead function (first call) and no classes (second)
        mock_db.execute_query.side_effect = [
            [
                {
                    "qualified_name": "module._obsolete_thing",
                    "name": "_obsolete_thing",
                    "file_path": "/module.py",
                    "line_start": 10,
                    "complexity": 5,
                    "containing_file": "/module.py",
                    "decorators": [],
                }
            ],
            [],  # No dead classes
        ]

        findings = detector.detect(previous_findings=[vulture_finding])
        assert len(findings) == 1

        # Check confidence is validated (95%)
        assert findings[0].graph_context["confidence"] == 0.95
        assert findings[0].graph_context["vulture_confirmed"] is True
        assert findings[0].graph_context["safe_to_remove"] is True
        assert "graph_analysis" in findings[0].graph_context["validators"]
        assert "vulture" in findings[0].graph_context["validators"]

    def test_safe_to_remove_in_suggested_fix(self, mock_db):
        """Test suggested fix mentions 'SAFE TO REMOVE' when validated."""
        detector = DeadCodeDetector(mock_db)

        vulture_finding = Finding(
            id="vulture-123",
            detector="VultureDetector",
            severity=Severity.LOW,
            title="Unused function: _obsolete_thing",
            description="Test",
            affected_nodes=[],
            affected_files=["/module.py"],
            graph_context={
                "item_name": "_obsolete_thing",
                "item_type": "function",
                "confidence": 95,
            },
        )

        mock_db.execute_query.side_effect = [
            [
                {
                    "qualified_name": "module._obsolete_thing",
                    "name": "_obsolete_thing",
                    "file_path": "/module.py",
                    "line_start": 10,
                    "complexity": 5,
                    "containing_file": "/module.py",
                    "decorators": [],
                }
            ],
            [],  # No dead classes
        ]

        findings = detector.detect(previous_findings=[vulture_finding])
        assert len(findings) == 1
        assert "SAFE TO REMOVE" in findings[0].suggested_fix

    def test_review_required_without_validation(self, mock_db):
        """Test suggested fix mentions 'REVIEW REQUIRED' without validation."""
        detector = DeadCodeDetector(mock_db)

        mock_db.execute_query.side_effect = [
            [
                {
                    "qualified_name": "module._obsolete_thing",
                    "name": "_obsolete_thing",
                    "file_path": "/module.py",
                    "line_start": 10,
                    "complexity": 5,
                    "containing_file": "/module.py",
                    "decorators": [],
                }
            ],
            [],  # No dead classes
        ]

        findings = detector.detect(previous_findings=[])
        assert len(findings) == 1
        assert "REVIEW REQUIRED" in findings[0].suggested_fix


class TestDeadCodeCollaborationTags:
    """Test collaboration metadata tags based on validation."""

    def test_safe_to_remove_tag_when_validated(self, mock_db):
        """Test 'safe_to_remove' tag added when validated."""
        detector = DeadCodeDetector(mock_db)

        vulture_finding = Finding(
            id="vulture-123",
            detector="VultureDetector",
            severity=Severity.LOW,
            title="Unused function: _obsolete_thing",
            description="Test",
            affected_nodes=[],
            affected_files=["/module.py"],
            graph_context={
                "item_name": "_obsolete_thing",
                "item_type": "function",
                "confidence": 95,
            },
        )

        mock_db.execute_query.side_effect = [
            [
                {
                    "qualified_name": "module._obsolete_thing",
                    "name": "_obsolete_thing",
                    "file_path": "/module.py",
                    "line_start": 10,
                    "complexity": 5,
                    "containing_file": "/module.py",
                    "decorators": [],
                }
            ],
            [],  # No dead classes
        ]

        findings = detector.detect(previous_findings=[vulture_finding])
        assert len(findings) == 1
        assert findings[0].has_tag("safe_to_remove")
        assert not findings[0].has_tag("review_required")

    def test_review_required_tag_without_validation(self, mock_db):
        """Test 'review_required' tag added without validation."""
        detector = DeadCodeDetector(mock_db)

        mock_db.execute_query.side_effect = [
            [
                {
                    "qualified_name": "module._obsolete_thing",
                    "name": "_obsolete_thing",
                    "file_path": "/module.py",
                    "line_start": 10,
                    "complexity": 5,
                    "containing_file": "/module.py",
                    "decorators": [],
                }
            ],
            [],  # No dead classes
        ]

        findings = detector.detect(previous_findings=[])
        assert len(findings) == 1
        assert findings[0].has_tag("review_required")
        assert not findings[0].has_tag("safe_to_remove")

    def test_vulture_confirmed_in_evidence(self, mock_db):
        """Test 'vulture_confirmed' added to evidence when validated."""
        detector = DeadCodeDetector(mock_db)

        vulture_finding = Finding(
            id="vulture-123",
            detector="VultureDetector",
            severity=Severity.LOW,
            title="Test",
            description="Test",
            affected_nodes=[],
            affected_files=["/module.py"],
            graph_context={
                "item_name": "_obsolete_thing",
                "item_type": "function",
                "confidence": 95,
            },
        )

        mock_db.execute_query.side_effect = [
            [
                {
                    "qualified_name": "module._obsolete_thing",
                    "name": "_obsolete_thing",
                    "file_path": "/module.py",
                    "line_start": 10,
                    "complexity": 5,
                    "containing_file": "/module.py",
                    "decorators": [],
                }
            ],
            [],  # No dead classes
        ]

        findings = detector.detect(previous_findings=[vulture_finding])
        assert len(findings) == 1

        collab_meta = findings[0].collaboration_metadata[0]
        assert "vulture_confirmed" in collab_meta.evidence


class TestVultureDynamicUsage:
    """Test VultureDetector._check_dynamic_usage() method."""

    def test_check_dynamic_usage_no_patterns(self, mock_db, temp_repo_path):
        """Test no dynamic usage when no patterns detected."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        # Mock no dynamic calls
        mock_db.execute_query.return_value = [{"has_dynamic_calls": False}]

        result = detector._check_dynamic_usage("my_func", "/module.py")
        assert result["dynamic_usage"] is False
        assert result["patterns"] == []
        assert result["confidence_reduction"] == 0.0

    def test_check_dynamic_usage_getattr_detected(self, mock_db, temp_repo_path):
        """Test dynamic usage detected for getattr calls."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        # First call for dynamic calls, second for decorators
        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": True}],
            [{"has_decorators": False}],
        ]

        result = detector._check_dynamic_usage("my_func", "/module.py")
        assert result["dynamic_usage"] is True
        assert "getattr/setattr" in result["patterns"]
        assert result["confidence_reduction"] >= 0.15

    def test_check_dynamic_usage_factory_pattern(self, mock_db, temp_repo_path):
        """Test dynamic usage detected for factory patterns."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": False}],
            [{"has_decorators": False}],
        ]

        result = detector._check_dynamic_usage("create_widget", "/module.py")
        assert result["dynamic_usage"] is True
        assert any("factory_pattern" in p for p in result["patterns"])
        assert result["confidence_reduction"] >= 0.10

    def test_check_dynamic_usage_decorator_detected(self, mock_db, temp_repo_path):
        """Test dynamic usage detected for decorated items."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": False}],
            [{"has_decorators": True}],
        ]

        result = detector._check_dynamic_usage("my_func", "/module.py")
        assert result["dynamic_usage"] is True
        assert "has_decorators" in result["patterns"]
        assert result["confidence_reduction"] >= 0.20

    def test_check_dynamic_usage_pytest_fixture(self, mock_db, temp_repo_path):
        """Test dynamic usage detected for pytest fixtures."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": False}],
            [{"has_decorators": False}],
        ]

        result = detector._check_dynamic_usage("fixture_db", "/module.py")
        assert result["dynamic_usage"] is True
        assert "pytest_fixture" in result["patterns"]
        assert result["confidence_reduction"] >= 0.30

    def test_check_dynamic_usage_conftest(self, mock_db, temp_repo_path):
        """Test dynamic usage detected for conftest.py."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": False}],
            [{"has_decorators": False}],
        ]

        result = detector._check_dynamic_usage("any_func", "/tests/conftest.py")
        assert result["dynamic_usage"] is True
        assert "pytest_fixture" in result["patterns"]

    def test_check_dynamic_usage_caches_results(self, mock_db, temp_repo_path):
        """Test dynamic usage results are cached."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": False}],
            [{"has_decorators": False}],
        ]

        # First call should query
        result1 = detector._check_dynamic_usage("my_func", "/module.py")

        # Reset mock to ensure second call doesn't query
        mock_db.execute_query.reset_mock()

        # Second call should use cache
        result2 = detector._check_dynamic_usage("my_func", "/module.py")

        assert result1 == result2
        mock_db.execute_query.assert_not_called()

    def test_check_dynamic_usage_confidence_capped(self, mock_db, temp_repo_path):
        """Test confidence reduction is capped at 50%."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        # Multiple patterns detected
        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": True}],  # +15%
            [{"has_decorators": True}],  # +20%
        ]

        # Also a pytest fixture (+30%)
        result = detector._check_dynamic_usage("fixture_create_widget", "/conftest.py")

        # Total would be 65%, but should be capped at 50%
        assert result["confidence_reduction"] == 0.50


class TestVultureShouldFilterFinding:
    """Test VultureDetector._should_filter_finding() method."""

    def test_filter_pytest_fixture_prefix(self, mock_db, temp_repo_path):
        """Test filtering pytest fixtures with prefix."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        finding = {"name": "fixture_db", "type": "function", "confidence": 80}
        assert detector._should_filter_finding(finding) is True

    def test_filter_pytest_fixture_suffix(self, mock_db, temp_repo_path):
        """Test filtering pytest fixtures with suffix."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        finding = {"name": "db_fixture", "type": "function", "confidence": 80}
        assert detector._should_filter_finding(finding) is True

    def test_filter_callback_patterns(self, mock_db, temp_repo_path):
        """Test filtering callback patterns."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        callbacks = ["on_click", "handle_event", "event_handler", "my_callback"]
        for name in callbacks:
            finding = {"name": name, "type": "function", "confidence": 80}
            assert detector._should_filter_finding(finding) is True, f"Should filter {name}"

    def test_filter_setup_teardown(self, mock_db, temp_repo_path):
        """Test filtering setUp/tearDown methods."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        methods = ["setUp", "tearDown", "setUpClass", "tearDownClass"]
        for name in methods:
            finding = {"name": name, "type": "method", "confidence": 80}
            assert detector._should_filter_finding(finding) is True, f"Should filter {name}"

    def test_filter_factory_methods(self, mock_db, temp_repo_path):
        """Test filtering factory methods."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        factories = ["create_user", "build_config", "make_widget"]
        for name in factories:
            finding = {"name": name, "type": "function", "confidence": 80}
            assert detector._should_filter_finding(finding) is True, f"Should filter {name}"

    def test_keep_high_confidence_findings(self, mock_db, temp_repo_path):
        """Test high confidence findings are kept even with patterns."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        # Even though it matches "create_" pattern, 95% confidence keeps it
        finding = {"name": "create_user", "type": "function", "confidence": 95}
        assert detector._should_filter_finding(finding) is False

    def test_keep_normal_functions(self, mock_db, temp_repo_path):
        """Test normal functions are not filtered."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        finding = {"name": "calculate_total", "type": "function", "confidence": 80}
        assert detector._should_filter_finding(finding) is False


class TestVultureConfidenceAdjustment:
    """Test VultureDetector confidence adjustment based on dynamic patterns."""

    @patch.object(VultureDetector, "_run_vulture")
    @patch.object(VultureDetector, "_should_filter_finding", return_value=False)
    def test_confidence_reduced_for_dynamic_usage(self, mock_filter, mock_vulture, mock_db, temp_repo_path):
        """Test confidence is reduced when dynamic patterns detected."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        mock_vulture.return_value = [
            {
                "file": "/module.py",
                "line": 10,
                "type": "function",
                "name": "create_widget",  # Factory pattern
                "confidence": 90,
                "message": "unused function 'create_widget' (90% confidence)"
            }
        ]

        # Mock graph queries
        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": False}],
            [{"has_decorators": False}],
            [{"file_loc": 100}],  # File context query
        ]

        findings = detector.detect()

        # Factory pattern reduces confidence by 10%
        assert len(findings) == 1
        ctx = findings[0].graph_context
        assert ctx["original_confidence"] == 90
        assert ctx["confidence"] < 90
        assert ctx["dynamic_usage"] is True
        assert any("factory_pattern" in p for p in ctx["dynamic_patterns"])

    @patch.object(VultureDetector, "_run_vulture")
    def test_high_confidence_maintained_no_patterns(self, mock_vulture, mock_db, temp_repo_path):
        """Test confidence maintained when no dynamic patterns."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        mock_vulture.return_value = [
            {
                "file": "/module.py",
                "line": 10,
                "type": "function",
                "name": "unused_helper",
                "confidence": 95,
                "message": "unused function 'unused_helper' (95% confidence)"
            }
        ]

        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": False}],
            [{"has_decorators": False}],
            [{"file_loc": 100}],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        ctx = findings[0].graph_context
        assert ctx["confidence"] == 95  # No reduction
        assert ctx["dynamic_usage"] is False


class TestVultureCollaborationMetadata:
    """Test VultureDetector collaboration metadata for cross-validation."""

    @patch.object(VultureDetector, "_run_vulture")
    @patch.object(VultureDetector, "_should_filter_finding", return_value=False)
    def test_adds_review_required_for_dynamic_patterns(self, mock_filter, mock_vulture, mock_db, temp_repo_path):
        """Test 'review_required' tag added for dynamic patterns."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        mock_vulture.return_value = [
            {
                "file": "/module.py",
                "line": 10,
                "type": "function",
                "name": "build_config",  # Factory pattern, but filter is mocked
                "confidence": 85,
                "message": "unused function 'build_config' (85% confidence)"
            }
        ]

        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": False}],
            [{"has_decorators": False}],
            [{"file_loc": 100}],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].has_tag("review_required")
        collab = findings[0].collaboration_metadata[0]
        assert "dynamic_patterns_detected" in collab.evidence

    @patch.object(VultureDetector, "_run_vulture")
    def test_adds_high_confidence_tag(self, mock_vulture, mock_db, temp_repo_path):
        """Test 'high_confidence' tag added for 90%+ confidence."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        mock_vulture.return_value = [
            {
                "file": "/module.py",
                "line": 10,
                "type": "function",
                "name": "truly_dead_func",
                "confidence": 95,
                "message": "unused function 'truly_dead_func' (95% confidence)"
            }
        ]

        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": False}],
            [{"has_decorators": False}],
            [{"file_loc": 100}],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].has_tag("high_confidence")

    @patch.object(VultureDetector, "_run_vulture")
    def test_item_info_in_graph_context(self, mock_vulture, mock_db, temp_repo_path):
        """Test item info is stored in graph_context for cross-validation."""
        detector = VultureDetector(
            mock_db,
            detector_config={"repository_path": temp_repo_path}
        )

        mock_vulture.return_value = [
            {
                "file": "/path/to/module.py",
                "line": 42,
                "type": "function",
                "name": "dead_function",
                "confidence": 90,
                "message": "unused function 'dead_function' (90% confidence)"
            }
        ]

        mock_db.execute_query.side_effect = [
            [{"has_dynamic_calls": False}],
            [{"has_decorators": False}],
            [{"file_loc": 200}],
        ]

        findings = detector.detect()

        assert len(findings) == 1
        ctx = findings[0].graph_context

        # These are used by DeadCodeDetector for cross-validation
        assert ctx["item_name"] == "dead_function"
        assert ctx["item_type"] == "function"
        assert ctx["line"] == 42


class TestCrossValidationIntegration:
    """Integration tests for DeadCode + Vulture cross-validation."""

    def test_dead_code_reads_vulture_findings(self, mock_db):
        """Test DeadCodeDetector correctly reads VultureDetector findings."""
        # Create Vulture findings first
        vulture_findings = [
            Finding(
                id="v1",
                detector="VultureDetector",
                severity=Severity.LOW,
                title="Unused function: helper_func",
                description="Test",
                affected_nodes=[],
                affected_files=["/utils.py"],
                graph_context={
                    "item_name": "helper_func",
                    "item_type": "function",
                    "confidence": 95,
                    "line": 10,
                },
            ),
            Finding(
                id="v2",
                detector="VultureDetector",
                severity=Severity.LOW,
                title="Unused class: OldClass",
                description="Test",
                affected_nodes=[],
                affected_files=["/models.py"],
                graph_context={
                    "item_name": "OldClass",
                    "item_type": "class",
                    "confidence": 90,
                    "line": 50,
                },
            ),
        ]

        # Now run DeadCodeDetector with Vulture findings
        dead_code_detector = DeadCodeDetector(mock_db)

        # Extract should capture both
        extracted = dead_code_detector._extract_vulture_unused(vulture_findings)
        assert "/utils.py:helper_func" in extracted
        assert "/models.py:OldClass" in extracted
        assert "helper_func" in extracted
        assert "OldClass" in extracted

    def test_cross_validation_class_detection(self, mock_db):
        """Test cross-validation for class detection."""
        detector = DeadCodeDetector(mock_db)

        vulture_finding = Finding(
            id="vulture-class",
            detector="VultureDetector",
            severity=Severity.LOW,
            title="Unused class: DeadClass",
            description="Test",
            affected_nodes=[],
            affected_files=["/module.py"],
            graph_context={
                "item_name": "DeadClass",
                "item_type": "class",
                "confidence": 95,
            },
        )

        # Mock the class query (second call after functions)
        mock_db.execute_query.side_effect = [
            [],  # No dead functions
            [  # Dead class
                {
                    "qualified_name": "module.DeadClass",
                    "name": "DeadClass",
                    "file_path": "/module.py",
                    "complexity": 10,
                    "containing_file": "/module.py",
                    "method_count": 3,
                }
            ],
        ]

        findings = detector.detect(previous_findings=[vulture_finding])
        assert len(findings) == 1
        assert findings[0].graph_context["vulture_confirmed"] is True
        assert findings[0].graph_context["confidence"] == 0.95

    def test_partial_validation(self, mock_db):
        """Test when only some findings are validated by Vulture."""
        detector = DeadCodeDetector(mock_db)

        # Only one Vulture finding
        vulture_finding = Finding(
            id="vulture-1",
            detector="VultureDetector",
            severity=Severity.LOW,
            title="Unused function: func1",
            description="Test",
            affected_nodes=[],
            affected_files=["/module.py"],
            graph_context={
                "item_name": "func1",
                "item_type": "function",
                "confidence": 95,
            },
        )

        # Two dead functions from graph
        mock_db.execute_query.side_effect = [
            [
                {
                    "qualified_name": "module.func1",
                    "name": "func1",
                    "file_path": "/module.py",
                    "line_start": 10,
                    "complexity": 5,
                    "containing_file": "/module.py",
                    "decorators": [],
                },
                {
                    "qualified_name": "module.func2",
                    "name": "func2",
                    "file_path": "/module.py",
                    "line_start": 20,
                    "complexity": 3,
                    "containing_file": "/module.py",
                    "decorators": [],
                },
            ],
            [],  # No dead classes
        ]

        findings = detector.detect(previous_findings=[vulture_finding])
        assert len(findings) == 2

        # Find each finding
        func1_finding = next(f for f in findings if "func1" in f.title)
        func2_finding = next(f for f in findings if "func2" in f.title)

        # func1 should be validated
        assert func1_finding.graph_context["vulture_confirmed"] is True
        assert func1_finding.graph_context["confidence"] == 0.95

        # func2 should NOT be validated
        assert func2_finding.graph_context["vulture_confirmed"] is False
        assert func2_finding.graph_context["confidence"] == 0.70
