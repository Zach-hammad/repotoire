"""Unit tests for ModuleCohesionDetector."""

from unittest.mock import Mock, patch

import pytest

from repotoire.detectors.module_cohesion import ModuleCohesionDetector
from repotoire.models import Severity


@pytest.fixture
def mock_db():
    """Create a mock Neo4j client."""
    db = Mock()
    db.execute_query = Mock()
    return db


class TestModuleCohesionDetector:
    """Test ModuleCohesionDetector."""

    def test_no_issues_when_gds_not_available(self, mock_db):
        """Test that detector returns empty when GDS is not available."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = False

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0
            mock_algo.check_gds_available.assert_called_once()

    def test_no_issues_when_projection_fails(self, mock_db):
        """Test that detector returns empty when projection fails."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = False

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0

    def test_no_issues_when_louvain_fails(self, mock_db):
        """Test that detector handles Louvain failure gracefully."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = None

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0
            mock_algo.cleanup_projection.assert_called()

    def test_poor_modularity_detection_very_poor(self, mock_db):
        """Test detection of very poor modularity (< 0.2)."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.15,  # Very poor
                "communityCount": 3,
            }
            mock_algo.get_god_modules.return_value = []
            mock_algo.get_misplaced_files.return_value = []
            mock_algo.get_inter_community_edges.return_value = []

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].detector == "ModuleCohesionDetector"
            assert findings[0].severity == Severity.HIGH
            assert "very poor" in findings[0].description
            assert findings[0].graph_context["modularity_score"] == 0.15

    def test_poor_modularity_detection_poor(self, mock_db):
        """Test detection of poor modularity (0.2-0.3)."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.25,  # Poor but not very poor
                "communityCount": 5,
            }
            mock_algo.get_god_modules.return_value = []
            mock_algo.get_misplaced_files.return_value = []
            mock_algo.get_inter_community_edges.return_value = []

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].severity == Severity.MEDIUM
            assert "poor" in findings[0].description

    def test_good_modularity_no_finding(self, mock_db):
        """Test that good modularity doesn't create a finding."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.65,  # Good
                "communityCount": 8,
            }
            mock_algo.get_god_modules.return_value = []
            mock_algo.get_misplaced_files.return_value = []
            mock_algo.get_inter_community_edges.return_value = []

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0

    def test_god_module_detection(self, mock_db):
        """Test detection of god modules (> 20% of codebase)."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.5,  # Good enough to not trigger poor modularity
                "communityCount": 5,
            }
            mock_algo.get_god_modules.return_value = [
                {
                    "community_id": 1,
                    "community_size": 50,
                    "percentage": 35.0,  # 35% - triggers medium
                    "total_files": 143,
                }
            ]
            mock_algo.get_misplaced_files.return_value = []
            mock_algo.get_inter_community_edges.return_value = []

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].detector == "ModuleCohesionDetector"
            assert "God module" in findings[0].title
            assert findings[0].severity == Severity.MEDIUM
            assert findings[0].graph_context["percentage"] == 35.0

    def test_god_module_severe(self, mock_db):
        """Test detection of severe god modules (>= 40%)."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.5,
                "communityCount": 3,
            }
            mock_algo.get_god_modules.return_value = [
                {
                    "community_id": 0,
                    "community_size": 80,
                    "percentage": 45.0,  # Severe
                    "total_files": 178,
                }
            ]
            mock_algo.get_misplaced_files.return_value = []
            mock_algo.get_inter_community_edges.return_value = []

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].severity == Severity.HIGH

    def test_misplaced_file_detection(self, mock_db):
        """Test detection of misplaced files."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.5,
                "communityCount": 5,
            }
            mock_algo.get_god_modules.return_value = []
            mock_algo.get_misplaced_files.return_value = [
                {
                    "qualified_name": "/src/utils/helper.py",
                    "file_path": "/src/utils/helper.py",
                    "current_community": 2,
                    "same_community_imports": 1,
                    "other_community_imports": 8,
                    "external_ratio": 0.89,  # High external ratio
                }
            ]
            mock_algo.get_inter_community_edges.return_value = []

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "misplaced" in findings[0].title.lower()
            assert findings[0].severity == Severity.MEDIUM  # >= 0.8 external ratio

    def test_misplaced_file_low_severity(self, mock_db):
        """Test misplaced file with lower external ratio gets LOW severity."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.5,
                "communityCount": 5,
            }
            mock_algo.get_god_modules.return_value = []
            mock_algo.get_misplaced_files.return_value = [
                {
                    "qualified_name": "/src/utils/helper.py",
                    "file_path": "/src/utils/helper.py",
                    "current_community": 2,
                    "same_community_imports": 3,
                    "other_community_imports": 5,
                    "external_ratio": 0.625,  # Moderate external ratio
                }
            ]
            mock_algo.get_inter_community_edges.return_value = []

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert findings[0].severity == Severity.LOW

    def test_high_coupling_detection(self, mock_db):
        """Test detection of high inter-community coupling."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.5,
                "communityCount": 5,
            }
            mock_algo.get_god_modules.return_value = []
            mock_algo.get_misplaced_files.return_value = []
            mock_algo.get_inter_community_edges.return_value = [
                {
                    "source_community": 1,
                    "target_community": 2,
                    "edge_count": 15,  # High coupling
                },
                {
                    "source_community": 1,
                    "target_community": 3,
                    "edge_count": 8,  # High coupling
                },
            ]

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert "coupling" in findings[0].title.lower()
            assert findings[0].severity == Severity.MEDIUM
            assert findings[0].graph_context["total_cross_edges"] == 23

    def test_low_coupling_no_finding(self, mock_db):
        """Test that low coupling doesn't create a finding."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.5,
                "communityCount": 5,
            }
            mock_algo.get_god_modules.return_value = []
            mock_algo.get_misplaced_files.return_value = []
            mock_algo.get_inter_community_edges.return_value = [
                {
                    "source_community": 1,
                    "target_community": 2,
                    "edge_count": 3,  # Below threshold of 5
                },
            ]

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0

    def test_multiple_findings(self, mock_db):
        """Test detection of multiple issues simultaneously."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.2,  # Poor modularity
                "communityCount": 3,
            }
            mock_algo.get_god_modules.return_value = [
                {
                    "community_id": 0,
                    "community_size": 30,
                    "percentage": 25.0,
                    "total_files": 120,
                }
            ]
            mock_algo.get_misplaced_files.return_value = [
                {
                    "qualified_name": "/src/helper.py",
                    "file_path": "/src/helper.py",
                    "current_community": 1,
                    "same_community_imports": 1,
                    "other_community_imports": 5,
                    "external_ratio": 0.83,
                }
            ]
            mock_algo.get_inter_community_edges.return_value = [
                {
                    "source_community": 0,
                    "target_community": 1,
                    "edge_count": 10,
                }
            ]

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            # Should find: poor modularity + god module + misplaced file + coupling
            assert len(findings) == 4

    def test_cleanup_on_error(self, mock_db):
        """Test that cleanup is called even on error."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.side_effect = Exception("Query failed")
            mock_algo.cleanup_projection = Mock()

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 0
            mock_algo.cleanup_projection.assert_called()

    def test_get_modularity_score(self, mock_db):
        """Test get_modularity_score accessor."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.65,
                "communityCount": 8,
            }
            mock_algo.get_god_modules.return_value = []
            mock_algo.get_misplaced_files.return_value = []
            mock_algo.get_inter_community_edges.return_value = []

            detector = ModuleCohesionDetector(mock_db)
            detector.detect()

            assert detector.get_modularity_score() == 0.65

    def test_get_community_count(self, mock_db):
        """Test get_community_count accessor."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.65,
                "communityCount": 8,
            }
            mock_algo.get_god_modules.return_value = []
            mock_algo.get_misplaced_files.return_value = []
            mock_algo.get_inter_community_edges.return_value = []

            detector = ModuleCohesionDetector(mock_db)
            detector.detect()

            assert detector.get_community_count() == 8

    def test_collaboration_metadata_added(self, mock_db):
        """Test that collaboration metadata is added to findings."""
        with patch(
            "repotoire.detectors.module_cohesion.GraphAlgorithms"
        ) as MockGraphAlgo:
            mock_algo = MockGraphAlgo.return_value
            mock_algo.check_gds_available.return_value = True
            mock_algo.create_file_import_projection.return_value = True
            mock_algo.calculate_file_communities.return_value = {
                "modularity": 0.15,
                "communityCount": 2,
            }
            mock_algo.get_god_modules.return_value = []
            mock_algo.get_misplaced_files.return_value = []
            mock_algo.get_inter_community_edges.return_value = []

            detector = ModuleCohesionDetector(mock_db)
            findings = detector.detect()

            assert len(findings) == 1
            assert len(findings[0].collaboration_metadata) == 1
            metadata = findings[0].collaboration_metadata[0]
            assert metadata.detector == "ModuleCohesionDetector"
            assert "architecture" in metadata.tags
