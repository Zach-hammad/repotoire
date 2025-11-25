"""Unit tests for GraphEnricher (REPO-151 Phase 2).

Tests the graph enrichment utility for cross-detector collaboration.
"""

from unittest.mock import Mock, patch
from datetime import datetime

import pytest

from repotoire.graph.enricher import GraphEnricher


@pytest.fixture
def mock_db():
    """Create a mock Neo4j client."""
    db = Mock()
    db.execute_query = Mock()
    return db


@pytest.fixture
def enricher(mock_db):
    """Create a GraphEnricher instance with mock database."""
    return GraphEnricher(mock_db)


class TestFlagEntity:
    """Test flag_entity method."""

    def test_flag_entity_creates_metadata(self, enricher, mock_db):
        """Test that flagging an entity creates a DetectorMetadata node."""
        mock_db.execute_query.return_value = [{"metadata_id": "detector-metadata-123"}]

        result = enricher.flag_entity(
            entity_qualified_name="mymodule.MyClass",
            detector="GodClassDetector",
            severity="HIGH",
            issues=["high_lcom", "many_methods"],
            confidence=0.9
        )

        # Should have called the database
        mock_db.execute_query.assert_called_once()
        call_args = mock_db.execute_query.call_args

        # Check parameters
        params = call_args[0][1]
        assert params["qualified_name"] == "mymodule.MyClass"
        assert params["detector"] == "GodClassDetector"
        assert params["severity"] == "HIGH"
        assert params["issues"] == ["high_lcom", "many_methods"]
        assert params["confidence"] == 0.9

        # Should return the metadata ID
        assert result == "detector-metadata-123"

    def test_flag_entity_with_metadata(self, enricher, mock_db):
        """Test flagging with additional metadata."""
        mock_db.execute_query.return_value = [{"metadata_id": "detector-metadata-456"}]

        result = enricher.flag_entity(
            entity_qualified_name="mymodule.complex_func",
            detector="RadonDetector",
            severity="MEDIUM",
            issues=["high_complexity"],
            confidence=0.85,
            metadata={"complexity": 25, "threshold": 10}
        )

        # Check metadata was JSON-encoded
        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert '"complexity": 25' in params["metadata"]

    def test_flag_entity_not_found(self, enricher, mock_db):
        """Test flagging an entity that doesn't exist in graph."""
        mock_db.execute_query.return_value = []

        result = enricher.flag_entity(
            entity_qualified_name="nonexistent.Entity",
            detector="TestDetector",
            severity="LOW",
            issues=["test_issue"],
            confidence=0.5
        )

        # Should still return a metadata ID (generated)
        assert result.startswith("detector-metadata-")

    def test_flag_entity_handles_exception(self, enricher, mock_db):
        """Test that exceptions are handled gracefully."""
        mock_db.execute_query.side_effect = Exception("DB connection error")

        result = enricher.flag_entity(
            entity_qualified_name="mymodule.MyClass",
            detector="TestDetector",
            severity="HIGH",
            issues=["test"],
            confidence=0.9
        )

        # Should still return a metadata ID (generated)
        assert result.startswith("detector-metadata-")


class TestGetFlaggedEntities:
    """Test get_flagged_entities method."""

    def test_get_all_flagged_entities(self, enricher, mock_db):
        """Test getting all flagged entities without filters."""
        mock_db.execute_query.return_value = [
            {
                "entity": "mymodule.ClassA",
                "entity_types": ["Class"],
                "detector": "GodClassDetector",
                "severity": "HIGH",
                "issues": ["high_lcom"],
                "confidence": 0.9,
                "timestamp": "2025-01-01T00:00:00",
                "metadata": "{}"
            },
            {
                "entity": "mymodule.ClassB",
                "entity_types": ["Class"],
                "detector": "FeatureEnvyDetector",
                "severity": "MEDIUM",
                "issues": ["external_usage"],
                "confidence": 0.8,
                "timestamp": "2025-01-01T00:00:00",
                "metadata": "{}"
            }
        ]

        results = enricher.get_flagged_entities()

        assert len(results) == 2
        assert results[0]["entity"] == "mymodule.ClassA"
        assert results[1]["detector"] == "FeatureEnvyDetector"

    def test_filter_by_detector(self, enricher, mock_db):
        """Test filtering by detector name."""
        mock_db.execute_query.return_value = [
            {"entity": "mymodule.ClassA", "detector": "GodClassDetector"}
        ]

        enricher.get_flagged_entities(detector="GodClassDetector")

        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert params["detector"] == "GodClassDetector"

    def test_filter_by_severity(self, enricher, mock_db):
        """Test filtering by severity."""
        mock_db.execute_query.return_value = []

        enricher.get_flagged_entities(severity="HIGH")

        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert params["severity"] == "HIGH"

    def test_filter_by_min_confidence(self, enricher, mock_db):
        """Test filtering by minimum confidence."""
        mock_db.execute_query.return_value = []

        enricher.get_flagged_entities(min_confidence=0.8)

        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert params["min_confidence"] == 0.8

    def test_handles_exception(self, enricher, mock_db):
        """Test that exceptions return empty list."""
        mock_db.execute_query.side_effect = Exception("Query failed")

        results = enricher.get_flagged_entities()

        assert results == []


class TestGetEntityFlags:
    """Test get_entity_flags method."""

    def test_get_flags_for_entity(self, enricher, mock_db):
        """Test getting all flags for a specific entity."""
        mock_db.execute_query.return_value = [
            {
                "detector": "GodClassDetector",
                "severity": "HIGH",
                "issues": ["high_lcom"],
                "confidence": 0.9,
                "timestamp": "2025-01-01T00:00:00",
                "metadata": "{}"
            },
            {
                "detector": "RadonDetector",
                "severity": "MEDIUM",
                "issues": ["high_complexity"],
                "confidence": 0.85,
                "timestamp": "2025-01-01T00:00:00",
                "metadata": "{}"
            }
        ]

        results = enricher.get_entity_flags("mymodule.MyClass")

        assert len(results) == 2
        assert results[0]["detector"] == "GodClassDetector"
        assert results[1]["detector"] == "RadonDetector"

    def test_entity_not_flagged(self, enricher, mock_db):
        """Test getting flags for an unflagged entity."""
        mock_db.execute_query.return_value = []

        results = enricher.get_entity_flags("clean.CleanClass")

        assert results == []

    def test_handles_exception(self, enricher, mock_db):
        """Test that exceptions return empty list."""
        mock_db.execute_query.side_effect = Exception("Query failed")

        results = enricher.get_entity_flags("mymodule.MyClass")

        assert results == []


class TestIsEntityFlagged:
    """Test is_entity_flagged method."""

    def test_entity_is_flagged(self, enricher, mock_db):
        """Test checking if an entity is flagged."""
        mock_db.execute_query.return_value = [{"flag_count": 2}]

        result = enricher.is_entity_flagged("mymodule.MyClass")

        assert result is True

    def test_entity_not_flagged(self, enricher, mock_db):
        """Test checking unflagged entity."""
        mock_db.execute_query.return_value = [{"flag_count": 0}]

        result = enricher.is_entity_flagged("clean.CleanClass")

        assert result is False

    def test_check_specific_detector(self, enricher, mock_db):
        """Test checking if entity flagged by specific detector."""
        mock_db.execute_query.return_value = [{"flag_count": 1}]

        result = enricher.is_entity_flagged(
            "mymodule.MyClass",
            detector="GodClassDetector"
        )

        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert params["detector"] == "GodClassDetector"
        assert result is True

    def test_handles_empty_result(self, enricher, mock_db):
        """Test handling empty query result."""
        mock_db.execute_query.return_value = []

        result = enricher.is_entity_flagged("mymodule.MyClass")

        assert result is False

    def test_handles_exception(self, enricher, mock_db):
        """Test that exceptions return False."""
        mock_db.execute_query.side_effect = Exception("Query failed")

        result = enricher.is_entity_flagged("mymodule.MyClass")

        assert result is False


class TestCleanupMetadata:
    """Test cleanup_metadata method."""

    def test_cleanup_all_metadata(self, enricher, mock_db):
        """Test cleaning up all detector metadata."""
        mock_db.execute_query.return_value = [{"deleted_count": 15}]

        deleted = enricher.cleanup_metadata()

        assert deleted == 15
        # Should not have detector filter in query
        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert "detector" not in params

    def test_cleanup_specific_detector(self, enricher, mock_db):
        """Test cleaning up metadata for specific detector."""
        mock_db.execute_query.return_value = [{"deleted_count": 5}]

        deleted = enricher.cleanup_metadata(detector="GodClassDetector")

        assert deleted == 5
        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert params["detector"] == "GodClassDetector"

    def test_cleanup_no_metadata(self, enricher, mock_db):
        """Test cleanup when no metadata exists."""
        mock_db.execute_query.return_value = [{"deleted_count": 0}]

        deleted = enricher.cleanup_metadata()

        assert deleted == 0

    def test_handles_exception(self, enricher, mock_db):
        """Test that exceptions return 0."""
        mock_db.execute_query.side_effect = Exception("Cleanup failed")

        deleted = enricher.cleanup_metadata()

        assert deleted == 0


class TestGetDuplicateFindings:
    """Test get_duplicate_findings method."""

    def test_find_duplicates(self, enricher, mock_db):
        """Test finding entities flagged by multiple detectors."""
        mock_db.execute_query.return_value = [
            {
                "detector": "GodClassDetector",
                "severity": "HIGH",
                "confidence": 0.9,
                "all_detectors": ["GodClassDetector", "RadonDetector"]
            },
            {
                "detector": "RadonDetector",
                "severity": "MEDIUM",
                "confidence": 0.85,
                "all_detectors": ["GodClassDetector", "RadonDetector"]
            }
        ]

        results = enricher.get_duplicate_findings(
            "mymodule.MyClass",
            min_detectors=2
        )

        assert len(results) == 2
        assert "GodClassDetector" in results[0]["all_detectors"]
        assert "RadonDetector" in results[0]["all_detectors"]

    def test_no_duplicates(self, enricher, mock_db):
        """Test when entity is flagged by only one detector."""
        mock_db.execute_query.return_value = []

        results = enricher.get_duplicate_findings(
            "mymodule.SingleFlag",
            min_detectors=2
        )

        assert results == []


class TestFindHotspots:
    """Test find_hotspots method."""

    def test_find_hotspots(self, enricher, mock_db):
        """Test finding code hotspots."""
        mock_db.execute_query.return_value = [
            {
                "entity": "mymodule.HotClass",
                "entity_type": "Class",
                "detector_count": 4,
                "detectors": ["GodClassDetector", "RadonDetector", "MypyDetector", "BanditDetector"],
                "avg_confidence": 0.88,
                "severity": "HIGH",
                "issues": ["high_lcom", "high_complexity", "type_error", "security"]
            }
        ]

        results = enricher.find_hotspots(min_detectors=3)

        assert len(results) == 1
        assert results[0]["entity"] == "mymodule.HotClass"
        assert results[0]["detector_count"] == 4

    def test_filter_by_min_confidence(self, enricher, mock_db):
        """Test filtering hotspots by minimum confidence."""
        mock_db.execute_query.return_value = []

        enricher.find_hotspots(min_confidence=0.9)

        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert params["min_confidence"] == 0.9

    def test_filter_by_severity(self, enricher, mock_db):
        """Test filtering hotspots by severity."""
        mock_db.execute_query.return_value = []

        enricher.find_hotspots(severity="HIGH")

        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert params["severity"] == "HIGH"

    def test_limit_results(self, enricher, mock_db):
        """Test limiting hotspot results."""
        mock_db.execute_query.return_value = []

        enricher.find_hotspots(limit=10)

        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert params["limit"] == 10


class TestFindHighConfidenceIssues:
    """Test find_high_confidence_issues method."""

    def test_find_high_confidence(self, enricher, mock_db):
        """Test finding high confidence issues."""
        mock_db.execute_query.return_value = [
            {
                "entity": "mymodule.ConfidentIssue",
                "entity_type": "Function",
                "detector": "BanditDetector",
                "confidence": 0.98,
                "severity": "CRITICAL",
                "issues": ["sql_injection"],
                "metadata": "{}"
            }
        ]

        results = enricher.find_high_confidence_issues(min_confidence=0.95)

        assert len(results) == 1
        assert results[0]["confidence"] == 0.98

    def test_filter_by_severity(self, enricher, mock_db):
        """Test filtering high confidence by severity."""
        mock_db.execute_query.return_value = []

        enricher.find_high_confidence_issues(severity="CRITICAL")

        call_args = mock_db.execute_query.call_args
        params = call_args[0][1]
        assert params["severity"] == "CRITICAL"


class TestGetFileHotspots:
    """Test get_file_hotspots method."""

    def test_get_file_hotspots(self, enricher, mock_db):
        """Test getting hotspot analysis for a file."""
        mock_db.execute_query.return_value = [
            {
                "file_path": "repotoire/models.py",
                "file_loc": 500,
                "detector_count": 3,
                "detectors": ["GodClassDetector", "RadonDetector", "MypyDetector"],
                "total_flags": 5,
                "flags": [
                    {"detector": "GodClassDetector", "severity": "HIGH", "confidence": 0.9, "issues": ["high_lcom"]}
                ]
            }
        ]

        result = enricher.get_file_hotspots("repotoire/models.py")

        assert result["file_path"] == "repotoire/models.py"
        assert result["detector_count"] == 3
        assert result["total_flags"] == 5

    def test_file_not_found(self, enricher, mock_db):
        """Test getting hotspots for non-existent file."""
        mock_db.execute_query.return_value = []

        result = enricher.get_file_hotspots("nonexistent.py")

        assert result["file_path"] == "nonexistent.py"
        assert result["detector_count"] == 0
        assert result["total_flags"] == 0

    def test_handles_exception(self, enricher, mock_db):
        """Test that exceptions return empty stats."""
        mock_db.execute_query.side_effect = Exception("Query failed")

        result = enricher.get_file_hotspots("error.py")

        assert result["file_path"] == "error.py"
        assert result["detector_count"] == 0
        assert "error" in result


class TestIntegration:
    """Integration tests for GraphEnricher workflow."""

    def test_flag_and_check_workflow(self, enricher, mock_db):
        """Test the typical flag -> check -> cleanup workflow."""
        # Step 1: Flag an entity
        mock_db.execute_query.return_value = [{"metadata_id": "test-123"}]
        enricher.flag_entity(
            entity_qualified_name="mymodule.BadClass",
            detector="GodClassDetector",
            severity="HIGH",
            issues=["high_lcom"],
            confidence=0.9
        )

        # Step 2: Check if entity is flagged
        mock_db.execute_query.return_value = [{"flag_count": 1}]
        is_flagged = enricher.is_entity_flagged("mymodule.BadClass")
        assert is_flagged is True

        # Step 3: Get entity flags
        mock_db.execute_query.return_value = [
            {"detector": "GodClassDetector", "severity": "HIGH", "issues": ["high_lcom"], "confidence": 0.9, "timestamp": "2025-01-01", "metadata": "{}"}
        ]
        flags = enricher.get_entity_flags("mymodule.BadClass")
        assert len(flags) == 1
        assert flags[0]["detector"] == "GodClassDetector"

        # Step 4: Cleanup
        mock_db.execute_query.return_value = [{"deleted_count": 1}]
        deleted = enricher.cleanup_metadata()
        assert deleted == 1

    def test_deduplication_workflow(self, enricher, mock_db):
        """Test using enricher for finding duplicates."""
        # Multiple detectors flag same entity
        mock_db.execute_query.return_value = [{"metadata_id": "test-1"}]
        enricher.flag_entity("mymodule.ProblematicClass", "GodClassDetector", "HIGH", ["high_lcom"], 0.9)

        mock_db.execute_query.return_value = [{"metadata_id": "test-2"}]
        enricher.flag_entity("mymodule.ProblematicClass", "RadonDetector", "MEDIUM", ["high_complexity"], 0.85)

        # Check for duplicates
        mock_db.execute_query.return_value = [
            {"detector": "GodClassDetector", "severity": "HIGH", "confidence": 0.9, "all_detectors": ["GodClassDetector", "RadonDetector"]},
            {"detector": "RadonDetector", "severity": "MEDIUM", "confidence": 0.85, "all_detectors": ["GodClassDetector", "RadonDetector"]}
        ]
        duplicates = enricher.get_duplicate_findings("mymodule.ProblematicClass", min_detectors=2)

        assert len(duplicates) == 2
        assert len(duplicates[0]["all_detectors"]) == 2
