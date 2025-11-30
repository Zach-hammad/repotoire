"""Tests for LazyClassDetector (REPO-222)."""

import pytest
from unittest.mock import Mock

from repotoire.detectors.lazy_class import LazyClassDetector
from repotoire.models import Severity


class TestLazyClassDetector:
    """Test suite for LazyClassDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return LazyClassDetector(mock_client)

    def test_detects_lazy_class(self, detector, mock_client):
        """Test detection of lazy class."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.UserHelper",
                "class_name": "UserHelper",
                "method_count": 2,
                "total_loc": 10,
                "avg_method_loc": 5.0,
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 25,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.LOW
        assert "UserHelper" in findings[0].title
        assert findings[0].graph_context["method_count"] == 2
        assert findings[0].graph_context["total_loc"] == 10

    def test_excludes_adapter_pattern(self, detector, mock_client):
        """Test exclusion of Adapter pattern classes."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.DatabaseAdapter",
                "class_name": "DatabaseAdapter",
                "method_count": 2,
                "total_loc": 12,
                "avg_method_loc": 6.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 0  # Excluded

    def test_excludes_config_classes(self, detector, mock_client):
        """Test exclusion of config classes."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.AppConfig",
                "class_name": "AppConfig",
                "method_count": 1,
                "total_loc": 15,
                "avg_method_loc": 5.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 0  # Excluded

    def test_excludes_exceptions(self, detector, mock_client):
        """Test exclusion of exception classes."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.CustomException",
                "class_name": "CustomException",
                "method_count": 1,
                "total_loc": 10,
                "avg_method_loc": 5.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 0  # Excluded

    def test_excludes_dto_classes(self, detector, mock_client):
        """Test exclusion of DTO classes."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.UserDTO",
                "class_name": "UserDTO",
                "method_count": 2,
                "total_loc": 12,
                "avg_method_loc": 6.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 0  # Excluded

    def test_excludes_test_classes(self, detector, mock_client):
        """Test exclusion of test classes."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "test_module.TestSomething",
                "class_name": "TestSomething",
                "method_count": 2,
                "total_loc": 12,
                "avg_method_loc": 6.0,
                "file_path": "test_module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 0  # Excluded

    def test_should_exclude_patterns(self, detector):
        """Test pattern exclusion logic."""
        assert detector._should_exclude("MyAdapter") is True
        assert detector._should_exclude("RequestDTO") is True
        assert detector._should_exclude("BaseClass") is True
        assert detector._should_exclude("UserService") is False
        assert detector._should_exclude("DataProcessor") is False
        assert detector._should_exclude("AppSettings") is True
        assert detector._should_exclude("MyException") is True
        assert detector._should_exclude("ValidationError") is True

    def test_empty_class_name_excluded(self, detector):
        """Test empty class name is excluded."""
        assert detector._should_exclude("") is True
        assert detector._should_exclude(None) is True

    def test_no_findings_for_empty_codebase(self, detector, mock_client):
        """Test no findings when codebase is empty."""
        mock_client.execute_query.return_value = []

        findings = detector.detect()

        assert len(findings) == 0

    def test_config_overrides_thresholds(self, mock_client):
        """Test config can override default thresholds."""
        detector = LazyClassDetector(
            mock_client,
            detector_config={
                "max_methods": 5,
                "max_avg_loc_per_method": 10,
                "min_total_loc": 20,
            }
        )

        assert detector.max_methods == 5
        assert detector.max_avg_loc == 10
        assert detector.min_total_loc == 20

    def test_severity_always_low(self, detector, mock_client):
        """Test severity is always LOW for lazy classes."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.SmallClass",
                "class_name": "SmallClass",
                "method_count": 1,
                "total_loc": 10,
                "avg_method_loc": 5.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert detector.severity(findings[0]) == Severity.LOW

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Test collaboration metadata is added to findings."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.TinyClass",
                "class_name": "TinyClass",
                "method_count": 2,
                "total_loc": 10,
                "avg_method_loc": 5.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert len(findings[0].collaboration_metadata) > 0
        metadata = findings[0].collaboration_metadata[0]
        assert metadata.detector == "LazyClassDetector"
        assert metadata.confidence == 0.75
        assert "lazy_class" in metadata.tags

    def test_multiple_lazy_classes_detected(self, detector, mock_client):
        """Test detection of multiple lazy classes."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.TinyClass1",
                "class_name": "TinyClass1",
                "method_count": 2,
                "total_loc": 10,
                "avg_method_loc": 5.0,
                "file_path": "module.py",
            },
            {
                "qualified_name": "module.TinyClass2",
                "class_name": "TinyClass2",
                "method_count": 1,
                "total_loc": 10,
                "avg_method_loc": 5.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 2

    def test_query_error_returns_empty(self, detector, mock_client):
        """Test query error returns empty findings list."""
        mock_client.execute_query.side_effect = Exception("Database error")

        findings = detector.detect()

        assert len(findings) == 0

    def test_finding_has_line_info(self, detector, mock_client):
        """Test finding includes line start and end."""
        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.SmallClass",
                "class_name": "SmallClass",
                "method_count": 2,
                "total_loc": 10,
                "avg_method_loc": 5.0,
                "file_path": "module.py",
                "line_start": 15,
                "line_end": 30,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].line_start == 15
        assert findings[0].line_end == 30


class TestLazyClassDetectorWithEnricher:
    """Test LazyClassDetector with GraphEnricher."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def mock_enricher(self):
        """Create a mock enricher."""
        return Mock()

    def test_enricher_flags_entities(self, mock_client, mock_enricher):
        """Test entities are flagged via enricher."""
        detector = LazyClassDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.TinyClass",
                "class_name": "TinyClass",
                "method_count": 2,
                "total_loc": 10,
                "avg_method_loc": 5.0,
                "file_path": "module.py",
            }
        ]

        detector.detect()

        mock_enricher.flag_entity.assert_called_once()
        call_args = mock_enricher.flag_entity.call_args
        assert call_args.kwargs["entity_qualified_name"] == "module.TinyClass"
        assert call_args.kwargs["detector"] == "LazyClassDetector"

    def test_enricher_failure_does_not_break_detection(self, mock_client, mock_enricher):
        """Test detection continues even if enricher fails."""
        detector = LazyClassDetector(mock_client, enricher=mock_enricher)
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        mock_client.execute_query.return_value = [
            {
                "qualified_name": "module.TinyClass",
                "class_name": "TinyClass",
                "method_count": 2,
                "total_loc": 10,
                "avg_method_loc": 5.0,
                "file_path": "module.py",
            }
        ]

        # Should not raise exception
        findings = detector.detect()

        assert len(findings) == 1
