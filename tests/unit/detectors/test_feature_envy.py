"""Tests for FeatureEnvyDetector."""

import pytest
from unittest.mock import Mock, MagicMock

from repotoire.detectors.feature_envy import FeatureEnvyDetector
from repotoire.models import Finding, Severity, CollaborationMetadata


class TestFeatureEnvyDetector:
    """Test suite for FeatureEnvyDetector."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock database client."""
        client = Mock()
        client.__class__.__name__ = "FalkorDBClient"
        return client

    @pytest.fixture
    def detector(self, mock_client):
        """Create a detector instance with mock client."""
        return FeatureEnvyDetector(mock_client)

    def test_detects_basic_feature_envy(self, detector, mock_client):
        """Should detect method that uses external classes more than its own."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.envious_method",
                "method_name": "envious_method",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 2,
                "external_uses": 20,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "envious_method" in findings[0].title
        assert findings[0].graph_context["external_uses"] == 20
        assert findings[0].graph_context["internal_uses"] == 2
        assert findings[0].graph_context["ratio"] == 10.0

    def test_critical_severity_for_high_ratio_and_uses(self, detector, mock_client):
        """Should be CRITICAL when ratio >= 10 and external_uses >= 30."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.very_envious",
                "method_name": "very_envious",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 3,
                "external_uses": 35,  # >= 30
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        # ratio = 35/3 = 11.67 >= 10, external >= 30
        assert findings[0].severity == Severity.CRITICAL

    def test_high_severity_for_moderate_ratio(self, detector, mock_client):
        """Should be HIGH when ratio >= 5 and external_uses >= 20."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.somewhat_envious",
                "method_name": "somewhat_envious",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 4,
                "external_uses": 22,  # >= 20, ratio = 5.5
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_medium_severity_for_lower_ratio(self, detector, mock_client):
        """Should be MEDIUM when ratio >= 3 and external_uses >= 10."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.slightly_envious",
                "method_name": "slightly_envious",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 5,
                "external_uses": 16,  # >= 10, ratio = 3.2
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.MEDIUM

    def test_low_severity_below_medium_thresholds(self, detector, mock_client):
        """Should be LOW when below medium thresholds."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.barely_envious",
                "method_name": "barely_envious",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 5,
                "external_uses": 16,  # ratio = 3.2 but external uses < 10 for medium
            }
        ]

        # Need to adjust - the query already filters by min_external_uses
        # So we need to configure lower thresholds
        detector_low = FeatureEnvyDetector(mock_client, detector_config={
            "min_external_uses": 5,
            "threshold_ratio": 2.0,
        })

        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.barely_envious",
                "method_name": "barely_envious",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 3,
                "external_uses": 8,  # ratio = 2.67, but < medium thresholds
            }
        ]

        findings = detector_low.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.LOW

    def test_no_internal_uses_infinite_ratio(self, detector, mock_client):
        """Should handle methods with no internal uses (infinite ratio)."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.all_external",
                "method_name": "all_external",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 0,
                "external_uses": 30,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].graph_context["internal_uses"] == 0
        # ratio is None for infinite
        assert findings[0].graph_context["ratio"] is None
        # Should suggest moving to utility function
        assert "utility function" in findings[0].suggested_fix

    def test_no_findings_when_query_fails(self, detector, mock_client):
        """Should return empty list when query fails."""
        mock_client.execute_query.side_effect = Exception("Database error")

        findings = detector.detect()

        assert len(findings) == 0

    def test_empty_results(self, detector, mock_client):
        """Should return empty list when no feature envy detected."""
        mock_client.execute_query.return_value = []

        findings = detector.detect()

        assert len(findings) == 0

    def test_affected_nodes_populated(self, detector, mock_client):
        """Should populate affected_nodes with method and class."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.envious",
                "method_name": "envious",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 2,
                "external_uses": 20,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "module.py::MyClass.envious" in findings[0].affected_nodes
        assert "module.py::MyClass" in findings[0].affected_nodes

    def test_affected_files_populated(self, detector, mock_client):
        """Should populate affected_files with file path."""
        mock_client.execute_query.return_value = [
            {
                "method": "src/service.py::UserService.process",
                "method_name": "process",
                "owner_class": "src/service.py::UserService",
                "file_path": "src/service.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 2,
                "external_uses": 20,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert "src/service.py" in findings[0].affected_files

    def test_collaboration_metadata_added(self, detector, mock_client):
        """Should add collaboration metadata to findings."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.envious",
                "method_name": "envious",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 2,
                "external_uses": 20,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert len(findings[0].collaboration_metadata) > 0
        metadata = findings[0].collaboration_metadata[0]
        assert metadata.detector == "FeatureEnvyDetector"
        assert "feature_envy" in metadata.tags
        assert "high_external_usage" in metadata.evidence

    def test_needs_previous_findings_property(self, detector):
        """Should declare it needs previous findings for god class collaboration."""
        assert detector.needs_previous_findings is True

    def test_god_class_severity_downgrade(self, mock_client):
        """Should downgrade severity when method is in a god class."""
        detector = FeatureEnvyDetector(mock_client)

        mock_client.execute_query.return_value = [
            {
                "method": "module.py::GodClass.envious_method",
                "method_name": "envious_method",
                "owner_class": "module.py::GodClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 3,
                "external_uses": 35,  # Would be CRITICAL normally
            }
        ]

        # Create a god class finding
        god_class_finding = Finding(
            id="god_class_GodClass",
            detector="GodClassDetector",
            severity=Severity.HIGH,
            title="God Class: GodClass",
            description="GodClass has too many responsibilities",
            affected_nodes=["module.py::GodClass"],
            affected_files=["module.py"],
        )
        god_class_finding.add_collaboration_metadata(CollaborationMetadata(
            detector="GodClassDetector",
            confidence=0.9,
            evidence=["too_many_methods"],
            tags=["god_class"],
        ))

        findings = detector.detect(previous_findings=[god_class_finding])

        assert len(findings) == 1
        # Should be downgraded from CRITICAL to HIGH
        assert findings[0].severity == Severity.HIGH
        assert findings[0].graph_context["is_god_class_symptom"] is True
        assert "god class" in findings[0].suggested_fix.lower()

    def test_standalone_issue_tag_when_not_god_class(self, detector, mock_client):
        """Should tag as standalone_issue when not a god class symptom."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::NormalClass.envious",
                "method_name": "envious",
                "owner_class": "module.py::NormalClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 2,
                "external_uses": 20,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        metadata = findings[0].collaboration_metadata[0]
        assert "standalone_issue" in metadata.tags

    def test_symptom_tag_when_god_class(self, mock_client):
        """Should tag as symptom when it's a god class method."""
        detector = FeatureEnvyDetector(mock_client)

        mock_client.execute_query.return_value = [
            {
                "method": "module.py::BigClass.envious",
                "method_name": "envious",
                "owner_class": "module.py::BigClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 2,
                "external_uses": 20,
            }
        ]

        god_class_finding = Finding(
            id="god_class_BigClass",
            detector="GodClassDetector",
            severity=Severity.HIGH,
            title="God Class: BigClass",
            description="BigClass has too many responsibilities",
            affected_nodes=["module.py::BigClass"],
            affected_files=["module.py"],
        )
        god_class_finding.add_collaboration_metadata(CollaborationMetadata(
            detector="GodClassDetector",
            confidence=0.9,
            evidence=["too_many_methods"],
            tags=["god_class"],
        ))

        findings = detector.detect(previous_findings=[god_class_finding])

        assert len(findings) == 1
        metadata = findings[0].collaboration_metadata[0]
        assert "symptom" in metadata.tags

    def test_config_threshold_override(self, mock_client):
        """Should allow threshold configuration override."""
        detector = FeatureEnvyDetector(mock_client, detector_config={
            "threshold_ratio": 5.0,
            "min_external_uses": 25,
        })

        assert detector.threshold_ratio == 5.0
        assert detector.min_external_uses == 25

    def test_effort_estimation_critical(self, detector, mock_client):
        """Should estimate large effort for critical severity."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.critical",
                "method_name": "critical",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 3,
                "external_uses": 35,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL
        assert "Large" in findings[0].estimated_effort

    def test_effort_estimation_low(self, mock_client):
        """Should estimate small effort for low severity."""
        detector = FeatureEnvyDetector(mock_client, detector_config={
            "min_external_uses": 5,
            "threshold_ratio": 2.0,
        })

        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.low",
                "method_name": "low",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 3,
                "external_uses": 8,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].severity == Severity.LOW
        assert "Small" in findings[0].estimated_effort

    def test_multiple_findings(self, detector, mock_client):
        """Should detect multiple methods with feature envy."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::ClassA.method1",
                "method_name": "method1",
                "owner_class": "module.py::ClassA",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 2,
                "external_uses": 20,
            },
            {
                "method": "module.py::ClassB.method2",
                "method_name": "method2",
                "owner_class": "module.py::ClassB",
                "file_path": "module.py",
                "line_start": 30,
                "line_end": 40,
                "internal_uses": 1,
                "external_uses": 25,
            },
        ]

        findings = detector.detect()

        assert len(findings) == 2
        method_names = {f.graph_context["owner_class"] for f in findings}
        assert "module.py::ClassA" in method_names
        assert "module.py::ClassB" in method_names

    def test_enricher_integration(self, mock_client):
        """Should call enricher when provided."""
        mock_enricher = Mock()
        detector = FeatureEnvyDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.envious",
                "method_name": "envious",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 2,
                "external_uses": 20,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        # Enricher should be called for each affected node
        assert mock_enricher.flag_entity.call_count >= 1

    def test_enricher_failure_handled_gracefully(self, mock_client):
        """Should handle enricher failures without crashing."""
        mock_enricher = Mock()
        mock_enricher.flag_entity.side_effect = Exception("Enricher error")
        detector = FeatureEnvyDetector(mock_client, enricher=mock_enricher)

        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.envious",
                "method_name": "envious",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 2,
                "external_uses": 20,
            }
        ]

        # Should not raise exception
        findings = detector.detect()

        assert len(findings) == 1

    def test_line_numbers_populated(self, detector, mock_client):
        """Should populate line_start and line_end from query results."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.envious",
                "method_name": "envious",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 42,
                "line_end": 67,
                "internal_uses": 2,
                "external_uses": 20,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].line_start == 42
        assert findings[0].line_end == 67

    def test_finding_id_format(self, detector, mock_client):
        """Should generate consistent finding IDs."""
        mock_client.execute_query.return_value = [
            {
                "method": "module.py::MyClass.method",
                "method_name": "method",
                "owner_class": "module.py::MyClass",
                "file_path": "module.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 2,
                "external_uses": 20,
            }
        ]

        findings = detector.detect()

        assert len(findings) == 1
        assert findings[0].id == "feature_envy_module.py::MyClass.method"
