"""Tests for RefusedBequestDetector (REPO-230)."""

import pytest
from unittest.mock import Mock

from repotoire.detectors.refused_bequest import RefusedBequestDetector
from repotoire.models import Severity


class TestRefusedBequestDetector:
    """Test suite for RefusedBequestDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "Neo4jClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return RefusedBequestDetector(mock_client)

    def test_detects_refused_bequest(self, detector, mock_client):
        """Test detection of refused bequest."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.EmailNotifier",
                "child_class": "EmailNotifier",
                "parent_name": "module.Notifier",
                "parent_class": "Notifier",
                "total_overrides": 4,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 50,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH  # No parent calls
        assert "EmailNotifier" in findings[0].title
        assert "Notifier" in findings[0].title

    def test_excludes_abstract_parent(self, detector, mock_client):
        """Test exclusion of abstract base classes."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.ConcreteClass",
                "child_class": "ConcreteClass",
                "parent_name": "module.AbstractBase",
                "parent_class": "AbstractBase",
                "total_overrides": 3,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 0  # Excluded

    def test_excludes_interface_parent(self, detector, mock_client):
        """Test exclusion of interface classes."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.Implementation",
                "child_class": "Implementation",
                "parent_name": "module.ServiceInterface",
                "parent_class": "ServiceInterface",
                "total_overrides": 3,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 0  # Excluded

    def test_excludes_protocol_parent(self, detector, mock_client):
        """Test exclusion of Protocol classes."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.Handler",
                "child_class": "Handler",
                "parent_name": "module.HandlerProtocol",
                "parent_class": "HandlerProtocol",
                "total_overrides": 2,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 0  # Excluded

    def test_excludes_mixin_parent(self, detector, mock_client):
        """Test exclusion of mixin classes."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.MyClass",
                "child_class": "MyClass",
                "parent_name": "module.LoggingMixin",
                "parent_class": "LoggingMixin",
                "total_overrides": 2,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 0  # Excluded

    def test_severity_high_for_zero_ratio(self, detector, mock_client):
        """Test HIGH severity when no overrides call parent."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.Child",
                "child_class": "Child",
                "parent_name": "module.Parent",
                "parent_class": "Parent",
                "total_overrides": 5,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_severity_medium_for_low_ratio(self, detector, mock_client):
        """Test MEDIUM severity when <20% call parent."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.Child",
                "child_class": "Child",
                "parent_name": "module.Parent",
                "parent_class": "Parent",
                "total_overrides": 10,
                "overrides_calling_parent": 1,
                "parent_call_ratio": 0.1,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM

    def test_severity_low_for_moderate_ratio(self, detector, mock_client):
        """Test LOW severity when 20-30% call parent."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.Child",
                "child_class": "Child",
                "parent_name": "module.Parent",
                "parent_class": "Parent",
                "total_overrides": 4,
                "overrides_calling_parent": 1,
                "parent_call_ratio": 0.25,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.LOW

    def test_is_abstract_parent(self, detector):
        """Test abstract parent detection."""
        assert detector._is_abstract_parent("AbstractBase") is True
        assert detector._is_abstract_parent("BaseClass") is True
        assert detector._is_abstract_parent("MyABC") is True
        assert detector._is_abstract_parent("ServiceInterface") is True
        assert detector._is_abstract_parent("LoggingMixin") is True
        assert detector._is_abstract_parent("UserService") is False
        assert detector._is_abstract_parent("Notifier") is False

    def test_empty_parent_name(self, detector):
        """Test empty parent name returns False."""
        assert detector._is_abstract_parent("") is False
        assert detector._is_abstract_parent(None) is False

    def test_no_findings_for_empty_codebase(self, detector, mock_client):
        """Test no findings when codebase is empty."""
        mock_client.execute_query.return_value = []

        findings = detector.detect()

        assert len(findings) == 0

    def test_config_overrides_thresholds(self, mock_client):
        """Test config can override default thresholds."""
        detector = RefusedBequestDetector(
            mock_client,
            detector_config={
                "min_overrides": 3,
                "max_parent_call_ratio": 0.5,
            }
        )

        assert detector.min_overrides == 3
        assert detector.max_parent_call_ratio == 0.5

    def test_query_error_returns_empty(self, detector, mock_client):
        """Test query error returns empty findings list."""
        mock_client.execute_query.side_effect = Exception("Database error")

        findings = detector.detect()

        assert len(findings) == 0

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Test collaboration metadata is added to findings."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.Child",
                "child_class": "Child",
                "parent_name": "module.Parent",
                "parent_class": "Parent",
                "total_overrides": 3,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert len(findings[0].collaboration_metadata) > 0
        metadata = findings[0].collaboration_metadata[0]
        assert metadata.detector == "RefusedBequestDetector"
        assert metadata.confidence == 0.8
        assert "refused_bequest" in metadata.tags

    def test_graph_context_populated(self, detector, mock_client):
        """Test graph context has correct values."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.Child",
                "child_class": "Child",
                "parent_name": "module.Parent",
                "parent_class": "Parent",
                "total_overrides": 5,
                "overrides_calling_parent": 1,
                "parent_call_ratio": 0.2,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].graph_context["total_overrides"] == 5
        assert findings[0].graph_context["overrides_calling_parent"] == 1
        assert findings[0].graph_context["parent_call_ratio"] == 0.2

    def test_finding_has_line_info(self, detector, mock_client):
        """Test finding includes line start and end."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.Child",
                "child_class": "Child",
                "parent_name": "module.Parent",
                "parent_class": "Parent",
                "total_overrides": 3,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
                "line_start": 20,
                "line_end": 60,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].line_start == 20
        assert findings[0].line_end == 60

    def test_multiple_refused_bequests_detected(self, detector, mock_client):
        """Test detection of multiple refused bequest violations."""
        mock_client.execute_query.return_value = [
            {
                "child_name": "module.Child1",
                "child_class": "Child1",
                "parent_name": "module.Parent1",
                "parent_class": "Parent1",
                "total_overrides": 3,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
            },
            {
                "child_name": "module.Child2",
                "child_class": "Child2",
                "parent_name": "module.Parent2",
                "parent_class": "Parent2",
                "total_overrides": 4,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
            }
        ]

        findings = detector.detect()

        assert len(findings) == 2


class TestRefusedBequestDetectorWithEnricher:
    """Test RefusedBequestDetector with GraphEnricher."""

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
        detector = RefusedBequestDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.return_value = [
            {
                "child_name": "module.Child",
                "child_class": "Child",
                "parent_name": "module.Parent",
                "parent_class": "Parent",
                "total_overrides": 3,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
            }
        ]

        detector.detect()

        mock_enricher.flag_entity.assert_called_once()
        call_args = mock_enricher.flag_entity.call_args
        assert call_args.kwargs["entity_qualified_name"] == "module.Child"
        assert call_args.kwargs["detector"] == "RefusedBequestDetector"

    def test_enricher_failure_does_not_break_detection(self, mock_client, mock_enricher):
        """Test detection continues even if enricher fails."""
        detector = RefusedBequestDetector(mock_client, enricher=mock_enricher)
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")

        mock_client.execute_query.return_value = [
            {
                "child_name": "module.Child",
                "child_class": "Child",
                "parent_name": "module.Parent",
                "parent_class": "Parent",
                "total_overrides": 3,
                "overrides_calling_parent": 0,
                "parent_call_ratio": 0.0,
                "file_path": "module.py",
            }
        ]

        # Should not raise exception
        findings = detector.detect()

        assert len(findings) == 1
