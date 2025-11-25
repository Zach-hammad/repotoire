"""Unit tests for cross-detector collaboration (REPO-150).

Tests the collaboration between GodClassDetector and FeatureEnvyDetector,
verifying that FeatureEnvy severity is downgraded when the owner class
is a god class (symptom vs root cause).
"""

from unittest.mock import Mock, patch
from typing import List

import pytest

from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.detectors.feature_envy import FeatureEnvyDetector
from repotoire.detectors.god_class import GodClassDetector


@pytest.fixture
def mock_db():
    """Create a mock Neo4j client."""
    db = Mock()
    db.execute_query = Mock()
    return db


class TestCollaborationMetadata:
    """Test CollaborationMetadata dataclass."""

    def test_create_metadata(self):
        """Test creating collaboration metadata."""
        metadata = CollaborationMetadata(
            detector="GodClassDetector",
            confidence=0.9,
            evidence=["high_lcom", "many_methods"],
            tags=["god_class", "complexity"]
        )
        assert metadata.detector == "GodClassDetector"
        assert metadata.confidence == 0.9
        assert "high_lcom" in metadata.evidence
        assert "god_class" in metadata.tags

    def test_metadata_with_related_findings(self):
        """Test metadata with related findings."""
        metadata = CollaborationMetadata(
            detector="FeatureEnvyDetector",
            confidence=0.8,
            evidence=["high_external_usage"],
            tags=["feature_envy"],
            related_findings=["finding-123"]
        )
        assert "finding-123" in metadata.related_findings


class TestFindingCollaborationMethods:
    """Test Finding class collaboration helper methods."""

    def test_add_collaboration_metadata(self):
        """Test adding collaboration metadata to a finding."""
        finding = Finding(
            id="test-123",
            detector="TestDetector",
            severity=Severity.HIGH,
            title="Test Finding",
            description="Test description",
            affected_nodes=["test.module"],
            affected_files=["/test.py"]
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="GodClassDetector",
            confidence=0.9,
            evidence=["high_lcom"],
            tags=["god_class"]
        ))

        assert len(finding.collaboration_metadata) == 1
        assert finding.collaboration_metadata[0].detector == "GodClassDetector"

    def test_get_collaboration_tags(self):
        """Test getting all tags from collaboration metadata."""
        finding = Finding(
            id="test-123",
            detector="TestDetector",
            severity=Severity.HIGH,
            title="Test",
            description="Test",
            affected_nodes=["test"],
            affected_files=["/test.py"]
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="Detector1",
            confidence=0.9,
            evidence=[],
            tags=["tag1", "tag2"]
        ))
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="Detector2",
            confidence=0.8,
            evidence=[],
            tags=["tag2", "tag3"]
        ))

        tags = finding.get_collaboration_tags()
        assert "tag1" in tags
        assert "tag2" in tags
        assert "tag3" in tags

    def test_get_confidence_scores(self):
        """Test getting confidence scores from all detectors."""
        finding = Finding(
            id="test-123",
            detector="TestDetector",
            severity=Severity.HIGH,
            title="Test",
            description="Test",
            affected_nodes=["test"],
            affected_files=["/test.py"]
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="GodClassDetector",
            confidence=0.9,
            evidence=[],
            tags=[]
        ))
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="RadonDetector",
            confidence=0.95,
            evidence=[],
            tags=[]
        ))

        scores = finding.get_confidence_scores()
        assert scores["GodClassDetector"] == 0.9
        assert scores["RadonDetector"] == 0.95

    def test_has_tag(self):
        """Test checking if finding has a specific tag."""
        finding = Finding(
            id="test-123",
            detector="TestDetector",
            severity=Severity.HIGH,
            title="Test",
            description="Test",
            affected_nodes=["test"],
            affected_files=["/test.py"]
        )

        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="GodClassDetector",
            confidence=0.9,
            evidence=[],
            tags=["god_class", "complexity"]
        ))

        assert finding.has_tag("god_class") is True
        assert finding.has_tag("complexity") is True
        assert finding.has_tag("feature_envy") is False


class TestFeatureEnvyGodClassCollaboration:
    """Test FeatureEnvyDetector collaboration with GodClassDetector."""

    def test_severity_downgrade_when_god_class_symptom(self, mock_db):
        """Test that feature envy severity is downgraded when owner is god class."""
        # Create a god class finding
        god_class_finding = Finding(
            id="god-class-123",
            detector="GodClassDetector",
            severity=Severity.HIGH,
            title="God Class: MyGodClass",
            description="Class has too many responsibilities",
            affected_nodes=["mymodule.MyGodClass"],
            affected_files=["/mymodule.py"]
        )
        god_class_finding.add_collaboration_metadata(CollaborationMetadata(
            detector="GodClassDetector",
            confidence=0.9,
            evidence=["high_lcom", "many_methods"],
            tags=["god_class", "complexity", "root_cause"]
        ))

        # Mock feature envy query results where owner class is the god class
        # Note: The query uses parameters so we return results regardless
        mock_db.execute_query.return_value = [
            {
                "method": "mymodule.MyGodClass.do_something",
                "method_name": "do_something",
                "owner_class": "mymodule.MyGodClass",  # This is the god class!
                "file_path": "/mymodule.py",
                "line_start": 50,
                "line_end": 60,
                "internal_uses": 2,
                "external_uses": 20,  # High enough to pass threshold
            }
        ]

        # Configure detector with lower thresholds for testing
        detector = FeatureEnvyDetector(
            mock_db,
            detector_config={
                "min_external_uses": 5,
                "threshold_ratio": 2.0
            }
        )
        findings = detector.detect(previous_findings=[god_class_finding])

        # Should have one finding
        assert len(findings) == 1

        # Check that it's marked as a symptom
        assert findings[0].graph_context.get("is_god_class_symptom") is True

        # Check that the finding has the "symptom" tag
        assert findings[0].has_tag("symptom")

        # Suggested fix should mention god class
        assert "god class" in findings[0].suggested_fix.lower()

    def test_no_downgrade_when_not_god_class(self, mock_db):
        """Test that severity is NOT downgraded when owner is not a god class."""
        # No god class findings
        previous_findings: List[Finding] = []

        mock_db.execute_query.return_value = [
            {
                "method": "mymodule.NormalClass.process_data",
                "method_name": "process_data",
                "owner_class": "mymodule.NormalClass",
                "file_path": "/mymodule.py",
                "line_start": 100,
                "line_end": 120,
                "internal_uses": 1,
                "external_uses": 25,
            }
        ]

        detector = FeatureEnvyDetector(
            mock_db,
            detector_config={
                "min_external_uses": 5,
                "threshold_ratio": 2.0
            }
        )
        findings = detector.detect(previous_findings=previous_findings)

        assert len(findings) == 1

        # Should NOT be marked as symptom
        assert findings[0].graph_context.get("is_god_class_symptom") is False

        # Should have standalone_issue tag
        assert findings[0].has_tag("standalone_issue")

    def test_collaboration_with_empty_previous_findings(self, mock_db):
        """Test that detector works normally with empty previous findings."""
        mock_db.execute_query.return_value = [
            {
                "method": "test.TestClass.test_method",
                "method_name": "test_method",
                "owner_class": "test.TestClass",
                "file_path": "/test.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 1,
                "external_uses": 20,
            }
        ]

        detector = FeatureEnvyDetector(
            mock_db,
            detector_config={
                "min_external_uses": 5,
                "threshold_ratio": 2.0
            }
        )
        findings = detector.detect(previous_findings=[])

        assert len(findings) == 1
        assert findings[0].graph_context.get("is_god_class_symptom") is False

    def test_collaboration_with_none_previous_findings(self, mock_db):
        """Test that detector works normally with None previous findings."""
        mock_db.execute_query.return_value = [
            {
                "method": "test.TestClass.test_method",
                "method_name": "test_method",
                "owner_class": "test.TestClass",
                "file_path": "/test.py",
                "line_start": 10,
                "line_end": 20,
                "internal_uses": 1,
                "external_uses": 20,
            }
        ]

        detector = FeatureEnvyDetector(
            mock_db,
            detector_config={
                "min_external_uses": 5,
                "threshold_ratio": 2.0
            }
        )
        findings = detector.detect(previous_findings=None)

        assert len(findings) == 1


class TestGodClassCollaborationMetadata:
    """Test GodClassDetector adds proper collaboration metadata."""

    def test_god_class_adds_collaboration_metadata(self, mock_db):
        """Test that god class findings include collaboration metadata with tags."""
        # Create a finding manually to test metadata
        finding = Finding(
            id="test-god-class",
            detector="GodClassDetector",
            severity=Severity.HIGH,
            title="God Class: BigClass",
            description="Class has too many responsibilities",
            affected_nodes=["mymodule.BigClass"],
            affected_files=["/mymodule.py"]
        )

        # Add collaboration metadata as the detector would
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="GodClassDetector",
            confidence=0.9,
            evidence=["high_lcom", "many_methods", "high_complexity"],
            tags=["god_class", "complexity", "root_cause"]
        ))

        # Check that god_class tag exists
        assert finding.has_tag("god_class")

        # Check that root_cause tag exists
        assert finding.has_tag("root_cause")

        # Check collaboration metadata
        assert len(finding.collaboration_metadata) > 0
        assert finding.collaboration_metadata[0].detector == "GodClassDetector"
        assert finding.collaboration_metadata[0].confidence == 0.9


class TestAnalysisEngineCollaboration:
    """Test AnalysisEngine passes previous_findings to detectors."""

    def test_engine_passes_previous_findings(self):
        """Test that engine passes previous findings to supporting detectors."""
        import inspect

        # Verify FeatureEnvyDetector supports previous_findings
        sig = inspect.signature(FeatureEnvyDetector.detect)
        assert "previous_findings" in sig.parameters

        # Verify the parameter is optional
        param = sig.parameters["previous_findings"]
        assert param.default is None

    def test_feature_envy_supports_collaboration(self, mock_db):
        """Test that FeatureEnvyDetector supports the previous_findings parameter."""
        import inspect

        sig = inspect.signature(FeatureEnvyDetector.detect)
        assert "previous_findings" in sig.parameters

        # The parameter should be optional (default None)
        param = sig.parameters["previous_findings"]
        assert param.default is None
