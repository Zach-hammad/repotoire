"""Tests for AI Churn Pattern Detector.

Tests the AIChurnDetector for detecting AI-generated code patterns
through rapid churn analysis.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import pytest

from repotoire.detectors.ai_churn_detector import (
    AIChurnDetector,
    FileChurnData,
    FunctionChurnData,
)
from repotoire.models import Finding, Severity


@pytest.fixture
def mock_graph_client():
    """Create a mock FalkorDB client."""
    client = MagicMock()
    client.execute_query = MagicMock(return_value=[])
    return client


@pytest.fixture
def detector(mock_graph_client):
    """Create an AIChurnDetector instance with mocked dependencies."""
    config = {
        "repo_path": "/fake/repo/path",
        "analysis_window_days": 90,
    }
    return AIChurnDetector(mock_graph_client, detector_config=config)


class TestFileChurnData:
    """Tests for FileChurnData dataclass."""
    
    def test_churn_ratio_calculation(self):
        """Test churn ratio is correctly calculated."""
        data = FileChurnData(
            file_path="test.py",
            lines_added_initially=100,
            lines_modified_first_week=50,
        )
        assert data.churn_ratio == 0.5
    
    def test_churn_ratio_zero_initial_lines(self):
        """Test churn ratio handles zero initial lines."""
        data = FileChurnData(
            file_path="test.py",
            lines_added_initially=0,
            lines_modified_first_week=50,
        )
        assert data.churn_ratio == 0.0
    
    def test_rapid_revision_score_48h(self):
        """Test rapid revision score with 48h modifications."""
        data = FileChurnData(
            file_path="test.py",
            modification_count_first_48h=2,
            modification_count_first_week=0,
        )
        # 2 modifications * 0.3 = 0.6
        assert data.rapid_revision_score == 0.6
    
    def test_rapid_revision_score_combined(self):
        """Test rapid revision score with both 48h and week modifications."""
        data = FileChurnData(
            file_path="test.py",
            modification_count_first_48h=1,
            modification_count_first_week=3,
        )
        # (1 * 0.3) + (3 * 0.1) = 0.3 + 0.3 = 0.6
        assert abs(data.rapid_revision_score - 0.6) < 0.001
    
    def test_rapid_revision_score_capped_at_1(self):
        """Test rapid revision score is capped at 1.0."""
        data = FileChurnData(
            file_path="test.py",
            modification_count_first_48h=10,
            modification_count_first_week=10,
        )
        assert data.rapid_revision_score == 1.0


class TestFunctionChurnData:
    """Tests for FunctionChurnData dataclass."""
    
    def test_churn_ratio_calculation(self):
        """Test function churn ratio is correctly calculated."""
        data = FunctionChurnData(
            qualified_name="module::func",
            file_path="test.py",
            function_name="func",
            line_start=10,
            line_end=50,
            lines_added_initially=40,
            lines_modified_first_week=20,
        )
        assert data.churn_ratio == 0.5


class TestAIChurnDetector:
    """Tests for AIChurnDetector class."""
    
    def test_init_with_config(self, mock_graph_client):
        """Test detector initialization with config."""
        config = {
            "repo_path": "/path/to/repo",
            "analysis_window_days": 60,
            "churn_ratio_threshold": 0.6,
        }
        detector = AIChurnDetector(mock_graph_client, detector_config=config)
        
        assert detector.repo_path == "/path/to/repo"
        assert detector.analysis_window_days == 60
        assert detector.churn_threshold == 0.6
    
    def test_init_with_defaults(self, mock_graph_client):
        """Test detector initialization with default values."""
        detector = AIChurnDetector(mock_graph_client)
        
        assert detector.repo_path is None
        assert detector.analysis_window_days == 90
        assert detector.churn_threshold == 0.5
    
    def test_is_code_file_python(self, detector):
        """Test code file detection for Python files."""
        assert detector._is_code_file("test.py") is True
        assert detector._is_code_file("src/module.py") is True
    
    def test_is_code_file_javascript(self, detector):
        """Test code file detection for JavaScript/TypeScript."""
        assert detector._is_code_file("app.js") is True
        assert detector._is_code_file("app.jsx") is True
        assert detector._is_code_file("app.ts") is True
        assert detector._is_code_file("app.tsx") is True
    
    def test_is_code_file_other_languages(self, detector):
        """Test code file detection for other languages."""
        assert detector._is_code_file("main.go") is True
        assert detector._is_code_file("lib.rs") is True
        assert detector._is_code_file("App.java") is True
    
    def test_is_code_file_non_code(self, detector):
        """Test non-code files are rejected."""
        assert detector._is_code_file("readme.md") is False
        assert detector._is_code_file("config.json") is False
        assert detector._is_code_file("style.css") is False
        assert detector._is_code_file("image.png") is False
    
    def test_calculate_file_severity_critical_churn_ratio(self, detector):
        """Test critical severity for very high churn ratio."""
        data = FileChurnData(
            file_path="test.py",
            lines_added_initially=100,
            lines_modified_first_week=150,  # 1.5 ratio
        )
        assert detector._calculate_file_severity(data) == Severity.CRITICAL
    
    def test_calculate_file_severity_critical_many_48h_mods(self, detector):
        """Test critical severity for many 48h modifications."""
        data = FileChurnData(
            file_path="test.py",
            lines_added_initially=100,
            lines_modified_first_week=20,
            modification_count_first_48h=4,
        )
        assert detector._calculate_file_severity(data) == Severity.CRITICAL
    
    def test_calculate_file_severity_high_churn_ratio(self, detector):
        """Test high severity for high churn ratio."""
        data = FileChurnData(
            file_path="test.py",
            lines_added_initially=100,
            lines_modified_first_week=60,  # 0.6 ratio
        )
        assert detector._calculate_file_severity(data) == Severity.HIGH
    
    def test_calculate_file_severity_high_48h_mods(self, detector):
        """Test high severity for 3 48h modifications."""
        data = FileChurnData(
            file_path="test.py",
            lines_added_initially=100,
            lines_modified_first_week=20,
            modification_count_first_48h=3,
        )
        assert detector._calculate_file_severity(data) == Severity.HIGH
    
    def test_calculate_file_severity_medium_churn_ratio(self, detector):
        """Test medium severity for moderate churn ratio."""
        data = FileChurnData(
            file_path="test.py",
            lines_added_initially=100,
            lines_modified_first_week=40,  # 0.4 ratio
        )
        assert detector._calculate_file_severity(data) == Severity.MEDIUM
    
    def test_calculate_file_severity_medium_48h_mods(self, detector):
        """Test medium severity for 2 48h modifications."""
        data = FileChurnData(
            file_path="test.py",
            lines_added_initially=100,
            lines_modified_first_week=10,
            modification_count_first_48h=2,
        )
        assert detector._calculate_file_severity(data) == Severity.MEDIUM
    
    def test_calculate_file_severity_low(self, detector):
        """Test low severity for minor churn."""
        data = FileChurnData(
            file_path="test.py",
            lines_added_initially=100,
            lines_modified_first_week=10,
            modification_count_first_48h=1,
        )
        assert detector._calculate_file_severity(data) == Severity.LOW
    
    def test_calculate_file_severity_info(self, detector):
        """Test info severity for no churn."""
        data = FileChurnData(
            file_path="test.py",
            lines_added_initially=100,
            lines_modified_first_week=0,
            modification_count_first_48h=0,
        )
        assert detector._calculate_file_severity(data) == Severity.INFO
    
    def test_create_file_churn_finding_high_churn(self, detector):
        """Test finding creation for high churn file."""
        data = FileChurnData(
            file_path="src/ai_generated.py",
            created_at=datetime.now(timezone.utc) - timedelta(days=7),
            first_commit_sha="abc123def456",
            lines_added_initially=200,
            lines_modified_first_week=150,  # 0.75 ratio
            modification_count_first_48h=3,
            modification_count_first_week=5,
        )
        
        finding = detector._create_file_churn_finding(data)
        
        assert finding is not None
        assert finding.detector == "AIChurnDetector"
        assert finding.severity == Severity.HIGH
        assert "ai_generated.py" in finding.title
        assert "src/ai_generated.py" in finding.affected_files
        assert finding.graph_context["churn_ratio"] == 0.75
        assert finding.graph_context["modifications_48h"] == 3
        assert "ai-churn" in finding.collaboration_metadata[0].tags
    
    def test_create_file_churn_finding_critical(self, detector):
        """Test finding creation for critical churn file."""
        data = FileChurnData(
            file_path="src/very_churny.py",
            created_at=datetime.now(timezone.utc) - timedelta(days=3),
            first_commit_sha="xyz789",
            lines_added_initially=100,
            lines_modified_first_week=120,  # 1.2 ratio
            modification_count_first_48h=4,
            modification_count_first_week=6,
        )
        
        finding = detector._create_file_churn_finding(data)
        
        assert finding is not None
        assert finding.severity == Severity.CRITICAL
        assert "more than it was initially written" in finding.description
    
    def test_create_file_churn_finding_skips_small_files(self, detector):
        """Test that small files are skipped."""
        data = FileChurnData(
            file_path="tiny.py",
            lines_added_initially=5,  # Below MIN_LINES_THRESHOLD
            lines_modified_first_week=3,
            modification_count_first_48h=2,
        )
        
        finding = detector._create_file_churn_finding(data)
        assert finding is None
    
    def test_create_file_churn_finding_skips_no_modifications(self, detector):
        """Test that files with no modifications are skipped."""
        data = FileChurnData(
            file_path="stable.py",
            lines_added_initially=100,
            lines_modified_first_week=0,
            modification_count_first_48h=0,
            modification_count_first_week=0,
        )
        
        finding = detector._create_file_churn_finding(data)
        assert finding is None
    
    def test_create_file_churn_finding_skips_info_severity(self, detector):
        """Test that INFO severity findings are not created."""
        data = FileChurnData(
            file_path="mostly_stable.py",
            lines_added_initially=100,
            lines_modified_first_week=5,
            modification_count_first_48h=0,
            modification_count_first_week=1,
        )
        
        finding = detector._create_file_churn_finding(data)
        assert finding is None
    
    @patch('repotoire.detectors.ai_churn_detector.GIT_AVAILABLE', False)
    def test_detect_without_git(self, mock_graph_client):
        """Test detect returns empty when GitPython not available."""
        detector = AIChurnDetector(mock_graph_client)
        findings = detector.detect()
        assert findings == []
    
    def test_detect_without_repo_path(self, detector):
        """Test detect returns empty when no repo path configured."""
        detector.repo_path = None
        detector._git_repo = None
        findings = detector.detect()
        assert findings == []
    
    def test_severity_method(self, detector):
        """Test severity method returns finding's severity."""
        finding = Finding(
            id=str(uuid4()),
            detector="AIChurnDetector",
            severity=Severity.HIGH,
            title="Test finding",
            description="Test description",
            affected_nodes=["test.py"],
            affected_files=["test.py"],
        )
        
        assert detector.severity(finding) == Severity.HIGH


class TestAIChurnDetectorIntegration:
    """Integration tests for AIChurnDetector with mocked git."""
    
    @patch('repotoire.detectors.ai_churn_detector.git')
    @patch('repotoire.detectors.ai_churn_detector.GIT_AVAILABLE', True)
    def test_analyze_file_churn_with_mocked_git(self, mock_git, mock_graph_client):
        """Test file churn analysis with mocked git repository."""
        # Create mock commits
        now = datetime.now(timezone.utc)
        
        # Mock commit objects
        mock_commit1 = Mock()
        mock_commit1.committed_datetime = now - timedelta(days=5)
        mock_commit1.hexsha = "abc123"
        mock_commit1.parents = []  # Initial commit
        mock_commit1.stats.files = {
            "new_file.py": {"insertions": 100, "deletions": 0}
        }
        
        mock_diff1 = Mock()
        mock_diff1.a_path = None
        mock_diff1.b_path = "new_file.py"
        mock_commit1.diff.return_value = [mock_diff1]
        
        mock_commit2 = Mock()
        mock_commit2.committed_datetime = now - timedelta(days=4, hours=12)  # Within 48h
        mock_commit2.hexsha = "def456"
        mock_commit2.parents = [mock_commit1]
        mock_commit2.stats.files = {
            "new_file.py": {"insertions": 20, "deletions": 10}
        }
        
        mock_diff2 = Mock()
        mock_diff2.a_path = "new_file.py"
        mock_diff2.b_path = "new_file.py"
        mock_commit1.diff.return_value = [mock_diff2]
        
        # Mock repository
        mock_repo = Mock()
        mock_repo.iter_commits.return_value = [mock_commit2, mock_commit1]
        mock_git.Repo.return_value = mock_repo
        mock_git.NULL_TREE = "NULL_TREE"
        
        # Create detector and analyze
        config = {"repo_path": "/test/repo"}
        detector = AIChurnDetector(mock_graph_client, detector_config=config)
        
        # Force repo initialization
        detector._git_repo = mock_repo
        
        churn_data = detector._analyze_file_churn()
        
        assert "new_file.py" in churn_data
        file_data = churn_data["new_file.py"]
        assert file_data.lines_added_initially == 100


class TestFunctionLevelChurn:
    """Tests for function-level churn detection."""
    
    def test_detect_function_churn_queries_graph(self, mock_graph_client, detector):
        """Test that function churn detection queries the graph."""
        # Set up file churn data with high-churn files
        file_churn = {
            "high_churn.py": FileChurnData(
                file_path="high_churn.py",
                lines_added_initially=100,
                lines_modified_first_week=60,  # 0.6 ratio
                modification_count_first_48h=3,
            )
        }
        
        # Mock graph response with a complex function
        mock_graph_client.execute_query.return_value = [
            {
                "qualified_name": "high_churn.py::complex_func",
                "name": "complex_func",
                "file_path": "high_churn.py",
                "line_start": 10,
                "line_end": 50,
                "complexity": 15,
                "loc": 40,
            }
        ]
        
        findings = detector._detect_function_churn(file_churn)
        
        # Should have queried the graph
        mock_graph_client.execute_query.assert_called_once()
        
        # Should have created a finding
        assert len(findings) == 1
        assert findings[0].title.startswith("Complex function")
        assert "complex_func" in findings[0].title
    
    def test_detect_function_churn_skips_low_complexity(self, mock_graph_client, detector):
        """Test that low complexity functions in churning files are skipped."""
        file_churn = {
            "moderate_churn.py": FileChurnData(
                file_path="moderate_churn.py",
                lines_added_initially=100,
                lines_modified_first_week=35,  # 0.35 ratio (MEDIUM)
                modification_count_first_48h=1,
            )
        }
        
        # Mock graph response with a simple function
        mock_graph_client.execute_query.return_value = [
            {
                "qualified_name": "moderate_churn.py::simple_func",
                "name": "simple_func",
                "file_path": "moderate_churn.py",
                "line_start": 10,
                "line_end": 20,
                "complexity": 5,  # Low complexity
                "loc": 10,
            }
        ]
        
        findings = detector._detect_function_churn(file_churn)
        
        # Should not create finding for simple function in moderate-churn file
        assert len(findings) == 0
    
    def test_create_function_finding_bumps_severity_for_complex(self, detector):
        """Test that complexity bumps severity for function findings."""
        file_churn = FileChurnData(
            file_path="test.py",
            lines_added_initially=100,
            lines_modified_first_week=35,  # 0.35 = MEDIUM
            modification_count_first_48h=1,
        )
        
        func_data = {
            "qualified_name": "test.py::complex_func",
            "name": "complex_func",
            "file_path": "test.py",
            "line_start": 10,
            "complexity": 20,  # High complexity
        }
        
        finding = detector._create_function_finding(func_data, file_churn)
        
        # Severity should be bumped from MEDIUM to HIGH due to complexity
        assert finding is not None
        assert finding.severity == Severity.HIGH
