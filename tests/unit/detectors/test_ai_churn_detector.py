"""Tests for AI Churn Pattern Detector.

Tests the AIChurnDetector for detecting AI-generated code patterns
through fix velocity and churn analysis at the function level.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import pytest

from repotoire.detectors.ai_churn_detector import (
    AIChurnDetector,
    FunctionChurnRecord,
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


class TestFunctionChurnRecord:
    """Tests for FunctionChurnRecord dataclass."""
    
    def test_time_to_first_fix_calculation(self):
        """Test time to first fix is correctly calculated."""
        now = datetime.now(timezone.utc)
        record = FunctionChurnRecord(
            qualified_name="test.py::my_func",
            file_path="test.py",
            function_name="my_func",
            created_at=now - timedelta(hours=24),
            first_modification_at=now - timedelta(hours=12),
        )
        assert record.time_to_first_fix == timedelta(hours=12)
        assert record.time_to_first_fix_hours == 12.0
    
    def test_time_to_first_fix_none_without_modification(self):
        """Test time to first fix is None if no modification."""
        record = FunctionChurnRecord(
            qualified_name="test.py::my_func",
            file_path="test.py",
            function_name="my_func",
            created_at=datetime.now(timezone.utc),
        )
        assert record.time_to_first_fix is None
        assert record.time_to_first_fix_hours is None
    
    def test_modifications_first_week_count(self):
        """Test counting modifications within first week."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=10)
        
        record = FunctionChurnRecord(
            qualified_name="test.py::my_func",
            file_path="test.py",
            function_name="my_func",
            created_at=created,
            modifications=[
                (created + timedelta(days=1), "abc123", 5, 2),  # In first week
                (created + timedelta(days=3), "def456", 3, 1),  # In first week
                (created + timedelta(days=8), "ghi789", 2, 0),  # After first week
            ]
        )
        assert record.modifications_first_week == 2
    
    def test_lines_changed_first_week(self):
        """Test total lines changed in first week."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=10)
        
        record = FunctionChurnRecord(
            qualified_name="test.py::my_func",
            file_path="test.py",
            function_name="my_func",
            created_at=created,
            modifications=[
                (created + timedelta(days=1), "abc123", 5, 2),  # 7 lines
                (created + timedelta(days=3), "def456", 3, 1),  # 4 lines
                (created + timedelta(days=8), "ghi789", 10, 5),  # Not counted
            ]
        )
        assert record.lines_changed_first_week == 11  # 7 + 4
    
    def test_churn_ratio_calculation(self):
        """Test churn ratio is correctly calculated."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=5)
        
        record = FunctionChurnRecord(
            qualified_name="test.py::my_func",
            file_path="test.py",
            function_name="my_func",
            created_at=created,
            lines_original=100,
            modifications=[
                (created + timedelta(days=1), "abc123", 30, 20),  # 50 lines
            ]
        )
        assert record.churn_ratio == 0.5
    
    def test_churn_ratio_zero_original_lines(self):
        """Test churn ratio handles zero original lines."""
        record = FunctionChurnRecord(
            qualified_name="test.py::my_func",
            file_path="test.py",
            function_name="my_func",
            lines_original=0,
        )
        assert record.churn_ratio == 0.0
    
    def test_is_high_velocity_fix_true(self):
        """Test high velocity fix detection - positive case."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(hours=60)
        
        record = FunctionChurnRecord(
            qualified_name="test.py::my_func",
            file_path="test.py",
            function_name="my_func",
            created_at=created,
            first_modification_at=created + timedelta(hours=24),  # Fixed in 24h
            modifications=[
                (created + timedelta(hours=24), "abc123", 5, 2),
                (created + timedelta(hours=36), "def456", 3, 1),
            ]
        )
        # 24h fix + 2 modifications = high velocity
        assert record.is_high_velocity_fix is True
    
    def test_is_high_velocity_fix_false_slow(self):
        """Test high velocity fix detection - too slow."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=5)
        
        record = FunctionChurnRecord(
            qualified_name="test.py::my_func",
            file_path="test.py",
            function_name="my_func",
            created_at=created,
            first_modification_at=created + timedelta(hours=72),  # 3 days
            modifications=[
                (created + timedelta(hours=72), "abc123", 5, 2),
                (created + timedelta(hours=96), "def456", 3, 1),
            ]
        )
        assert record.is_high_velocity_fix is False
    
    def test_is_high_velocity_fix_false_few_mods(self):
        """Test high velocity fix detection - not enough modifications."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=2)
        
        record = FunctionChurnRecord(
            qualified_name="test.py::my_func",
            file_path="test.py",
            function_name="my_func",
            created_at=created,
            first_modification_at=created + timedelta(hours=12),
            modifications=[
                (created + timedelta(hours=12), "abc123", 5, 2),  # Only 1 mod
            ]
        )
        assert record.is_high_velocity_fix is False
    
    def test_ai_churn_score_high(self):
        """Test AI churn score calculation - high score case."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=3)
        
        record = FunctionChurnRecord(
            qualified_name="test.py::my_func",
            file_path="test.py",
            function_name="my_func",
            created_at=created,
            first_modification_at=created + timedelta(hours=12),  # Fast fix
            lines_original=50,
            modifications=[
                (created + timedelta(hours=12), "a", 10, 5),
                (created + timedelta(hours=24), "b", 15, 10),
                (created + timedelta(hours=36), "c", 20, 5),
                (created + timedelta(hours=48), "d", 10, 5),
            ]  # 4 modifications, 80 lines changed = 1.6 churn ratio
        )
        score = record.ai_churn_score
        # Fast fix (0.4) + many mods (0.3) + high churn (0.3) = 1.0 (capped)
        assert score >= 0.8
    
    def test_ai_churn_score_low(self):
        """Test AI churn score calculation - low score case."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=10)
        
        record = FunctionChurnRecord(
            qualified_name="test.py::my_func",
            file_path="test.py",
            function_name="my_func",
            created_at=created,
            first_modification_at=created + timedelta(days=5),  # Slow fix
            lines_original=100,
            modifications=[
                (created + timedelta(days=5), "a", 5, 2),  # Only 7 lines = 0.07 churn
            ]
        )
        score = record.ai_churn_score
        assert score <= 0.3


class TestAIChurnDetector:
    """Tests for AIChurnDetector class."""
    
    def test_init_with_config(self, mock_graph_client):
        """Test detector initialization with config."""
        config = {
            "repo_path": "/path/to/repo",
            "analysis_window_days": 60,
        }
        detector = AIChurnDetector(mock_graph_client, detector_config=config)
        
        assert detector.repo_path == "/path/to/repo"
        assert detector.analysis_window_days == 60
    
    def test_init_with_defaults(self, mock_graph_client):
        """Test detector initialization with default values."""
        detector = AIChurnDetector(mock_graph_client)
        
        assert detector.repo_path is None
        assert detector.analysis_window_days == 90
    
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
    
    def test_detect_language(self, detector):
        """Test language detection from file path."""
        assert detector._detect_language("test.py") == "python"
        assert detector._detect_language("app.js") == "javascript"
        assert detector._detect_language("main.go") == "go"
        assert detector._detect_language("lib.rs") == "rust"
        assert detector._detect_language("unknown.xyz") == "unknown"
    
    def test_get_function_patterns_python(self, detector):
        """Test function patterns for Python."""
        patterns = detector._get_function_patterns("python")
        assert len(patterns) >= 1
        assert any("def" in p for p in patterns)
    
    def test_get_function_patterns_javascript(self, detector):
        """Test function patterns for JavaScript."""
        patterns = detector._get_function_patterns("javascript")
        assert len(patterns) >= 1
        assert any("function" in p for p in patterns)


class TestSeverityCalculation:
    """Tests for severity calculation based on fix velocity and churn."""
    
    def test_severity_critical_high_churn(self, detector):
        """Test critical severity for churn ratio > 1.0."""
        now = datetime.now(timezone.utc)
        record = FunctionChurnRecord(
            qualified_name="test.py::func",
            file_path="test.py",
            function_name="func",
            created_at=now - timedelta(days=5),
            lines_original=50,
            modifications=[
                (now - timedelta(days=4), "a", 30, 30),  # 60 lines = 1.2 churn
            ]
        )
        assert detector._calculate_severity(record) == Severity.CRITICAL
    
    def test_severity_critical_fast_fix_many_mods(self, detector):
        """Test critical severity for fix < 24h with 4+ modifications."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=3)
        record = FunctionChurnRecord(
            qualified_name="test.py::func",
            file_path="test.py",
            function_name="func",
            created_at=created,
            first_modification_at=created + timedelta(hours=12),
            lines_original=100,
            modifications=[
                (created + timedelta(hours=12), "a", 5, 2),
                (created + timedelta(hours=18), "b", 3, 1),
                (created + timedelta(hours=20), "c", 4, 2),
                (created + timedelta(hours=22), "d", 2, 1),
            ]
        )
        assert detector._calculate_severity(record) == Severity.CRITICAL
    
    def test_severity_high_velocity_fix(self, detector):
        """Test high severity for fix < 48h with 2+ modifications (key signal)."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=3)
        record = FunctionChurnRecord(
            qualified_name="test.py::func",
            file_path="test.py",
            function_name="func",
            created_at=created,
            first_modification_at=created + timedelta(hours=36),  # < 48h
            lines_original=100,
            modifications=[
                (created + timedelta(hours=36), "a", 5, 2),
                (created + timedelta(hours=44), "b", 3, 1),
            ]
        )
        assert detector._calculate_severity(record) == Severity.HIGH
    
    def test_severity_high_churn_ratio(self, detector):
        """Test high severity for churn ratio > 0.5."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=5)
        record = FunctionChurnRecord(
            qualified_name="test.py::func",
            file_path="test.py",
            function_name="func",
            created_at=created,
            first_modification_at=created + timedelta(days=3),  # Slow fix
            lines_original=100,
            modifications=[
                (created + timedelta(days=3), "a", 35, 25),  # 60 lines = 0.6 churn
            ]
        )
        assert detector._calculate_severity(record) == Severity.HIGH
    
    def test_severity_medium_slow_fix(self, detector):
        """Test medium severity for fix within 72h."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=5)
        record = FunctionChurnRecord(
            qualified_name="test.py::func",
            file_path="test.py",
            function_name="func",
            created_at=created,
            first_modification_at=created + timedelta(hours=60),  # < 72h
            lines_original=100,
            modifications=[
                (created + timedelta(hours=60), "a", 10, 5),  # 15 lines = 0.15 churn
            ]
        )
        assert detector._calculate_severity(record) == Severity.MEDIUM
    
    def test_severity_medium_moderate_churn(self, detector):
        """Test medium severity for churn ratio > 0.3."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=10)
        record = FunctionChurnRecord(
            qualified_name="test.py::func",
            file_path="test.py",
            function_name="func",
            created_at=created,
            first_modification_at=created + timedelta(days=5),
            lines_original=100,
            modifications=[
                (created + timedelta(days=5), "a", 20, 15),  # 35 lines = 0.35 churn
            ]
        )
        assert detector._calculate_severity(record) == Severity.MEDIUM
    
    def test_severity_low_single_mod(self, detector):
        """Test low severity for single modification."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=10)
        record = FunctionChurnRecord(
            qualified_name="test.py::func",
            file_path="test.py",
            function_name="func",
            created_at=created,
            first_modification_at=created + timedelta(days=5),
            lines_original=100,
            modifications=[
                (created + timedelta(days=5), "a", 5, 2),  # 7 lines = 0.07 churn
            ]
        )
        assert detector._calculate_severity(record) == Severity.LOW
    
    def test_severity_info_no_modifications(self, detector):
        """Test info severity for no modifications."""
        now = datetime.now(timezone.utc)
        record = FunctionChurnRecord(
            qualified_name="test.py::func",
            file_path="test.py",
            function_name="func",
            created_at=now - timedelta(days=10),
            lines_original=100,
        )
        assert detector._calculate_severity(record) == Severity.INFO


class TestFindingCreation:
    """Tests for finding creation."""
    
    def test_create_finding_high_velocity(self, detector):
        """Test finding creation for high velocity fix pattern."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=3)
        record = FunctionChurnRecord(
            qualified_name="src/api.py::process_request",
            file_path="src/api.py",
            function_name="process_request",
            created_at=created,
            creation_commit="abc12345",
            first_modification_at=created + timedelta(hours=18),
            first_modification_commit="def67890",
            lines_original=50,
            modifications=[
                (created + timedelta(hours=18), "def67890", 10, 5),
                (created + timedelta(hours=24), "ghi11111", 8, 3),
                (created + timedelta(hours=36), "jkl22222", 5, 2),
            ]
        )
        
        finding = detector._create_finding(record)
        
        assert finding is not None
        assert finding.detector == "AIChurnDetector"
        assert finding.severity == Severity.HIGH
        assert "process_request" in finding.title
        assert "src/api.py" in finding.affected_files
        assert finding.graph_context["is_high_velocity_fix"] is True
        assert "fix-velocity" in finding.collaboration_metadata[0].tags
    
    def test_create_finding_critical_churn(self, detector):
        """Test finding creation for critical churn ratio."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=5)
        record = FunctionChurnRecord(
            qualified_name="src/core.py::heavy_func",
            file_path="src/core.py",
            function_name="heavy_func",
            created_at=created,
            creation_commit="abc123",
            first_modification_at=created + timedelta(days=1),
            first_modification_commit="def456",
            lines_original=80,
            modifications=[
                (created + timedelta(days=1), "def456", 50, 40),  # 90 lines = 1.125 churn
            ]
        )
        
        finding = detector._create_finding(record)
        
        assert finding is not None
        assert finding.severity == Severity.CRITICAL
        assert finding.graph_context["churn_ratio"] > 1.0
        assert "Critical churn ratio" in finding.description
    
    def test_create_finding_skips_info_severity(self, detector):
        """Test that INFO severity records don't create findings."""
        now = datetime.now(timezone.utc)
        record = FunctionChurnRecord(
            qualified_name="src/utils.py::helper",
            file_path="src/utils.py",
            function_name="helper",
            created_at=now - timedelta(days=30),
            lines_original=20,
            modifications=[],  # No modifications
        )
        
        finding = detector._create_finding(record)
        assert finding is None
    
    def test_finding_includes_timeline(self, detector):
        """Test that finding includes modification timeline."""
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=3)
        record = FunctionChurnRecord(
            qualified_name="test.py::func",
            file_path="test.py",
            function_name="func",
            created_at=created,
            creation_commit="aaa",
            first_modification_at=created + timedelta(hours=12),
            first_modification_commit="bbb",
            lines_original=30,
            modifications=[
                (created + timedelta(hours=12), "bbb", 5, 2),
                (created + timedelta(hours=24), "ccc", 3, 1),
            ]
        )
        
        finding = detector._create_finding(record)
        
        assert finding is not None
        assert "Modification Timeline" in finding.description
        assert "bbb" in finding.description


class TestDetectorIntegration:
    """Integration tests with mocked git."""
    
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
            affected_nodes=["test.py::func"],
            affected_files=["test.py"],
        )
        
        assert detector.severity(finding) == Severity.HIGH
    
    @patch('repotoire.detectors.ai_churn_detector.git')
    @patch('repotoire.detectors.ai_churn_detector.GIT_AVAILABLE', True)
    def test_analyze_function_churn_with_mocked_git(self, mock_git, mock_graph_client):
        """Test function churn analysis with mocked git repository."""
        now = datetime.now(timezone.utc)
        
        # Mock a diff that adds a function
        mock_diff = Mock()
        mock_diff.a_path = None
        mock_diff.b_path = "test.py"
        mock_diff.diff = b"""
@@ -0,0 +1,15 @@
+def my_new_function(arg1, arg2):
+    '''A new function added.'''
+    result = arg1 + arg2
+    if result > 10:
+        return result * 2
+    else:
+        return result
+    # More lines
+    x = 1
+    y = 2
+    z = 3
+    return x + y + z
"""
        
        # Mock commit
        mock_commit = Mock()
        mock_commit.committed_datetime = now - timedelta(days=5)
        mock_commit.hexsha = "abc123def456"
        mock_commit.parents = []
        mock_commit.diff.return_value = [mock_diff]
        
        # Mock repository
        mock_repo = Mock()
        mock_repo.iter_commits.return_value = [mock_commit]
        mock_git.Repo.return_value = mock_repo
        mock_git.NULL_TREE = "NULL_TREE"
        
        # Create detector
        config = {"repo_path": "/test/repo"}
        detector = AIChurnDetector(mock_graph_client, detector_config=config)
        detector._git_repo = mock_repo
        
        records = detector._analyze_function_churn()
        
        # Should have found the function
        assert "test.py::my_new_function" in records
        record = records["test.py::my_new_function"]
        assert record.function_name == "my_new_function"
        assert record.lines_original > 0
