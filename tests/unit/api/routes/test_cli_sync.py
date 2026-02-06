"""Tests for CLI sync API routes.

Tests the endpoints for syncing local CLI analysis to the cloud.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

from repotoire.api.v1.routes.cli_sync import (
    FindingUpload,
    HealthScoreUpload,
    CLISyncRequest,
    CLISyncResponse,
)

# CLI version for tests
TEST_CLI_VERSION = "0.1.34"


class TestFindingUploadModel:
    """Test FindingUpload Pydantic model."""

    def test_valid_finding_minimal(self):
        """Should create valid FindingUpload with minimal fields."""
        finding = FindingUpload(
            detector_id="test-detector",
            title="Test Finding",
            description="A test finding",
            severity="high",
            file_path="src/main.py",
            line_start=10,
        )
        
        assert finding.detector_id == "test-detector"
        assert finding.title == "Test Finding"
        assert finding.severity == "high"
        assert finding.file_path == "src/main.py"
        assert finding.line_start == 10

    def test_valid_finding_all_fields(self):
        """Should create valid FindingUpload with all fields."""
        finding = FindingUpload(
            detector_id="security-detector",
            title="SQL Injection",
            description="Potential SQL injection vulnerability",
            severity="critical",
            file_path="src/db/queries.py",
            line_start=42,
            line_end=50,
            category="security",
            cwe_id="CWE-89",
            why_it_matters="Could allow attackers to execute arbitrary SQL",
            suggested_fix="Use parameterized queries",
            code_snippet="cursor.execute(f'SELECT * FROM users WHERE id={user_id}')",
            metadata={"confidence": 0.95},
        )
        
        assert finding.severity == "critical"
        assert finding.cwe_id == "CWE-89"
        assert finding.line_end == 50
        assert finding.metadata["confidence"] == 0.95

    def test_finding_severity_values(self):
        """Should accept various severity values."""
        severities = ["critical", "high", "medium", "low", "info"]
        
        for severity in severities:
            finding = FindingUpload(
                detector_id="test",
                title="Test",
                description="Test",
                severity=severity,
                file_path="test.py",
                line_start=1,
            )
            assert finding.severity == severity


class TestHealthScoreUploadModel:
    """Test HealthScoreUpload Pydantic model."""

    def test_valid_health_score(self):
        """Should create valid HealthScoreUpload."""
        health = HealthScoreUpload(
            health_score=85.5,
            structure_score=90.0,
            quality_score=80.0,
            architecture_score=75.0,
        )
        
        assert health.health_score == 85.5
        assert health.structure_score == 90.0
        assert health.quality_score == 80.0
        assert health.architecture_score == 75.0

    def test_health_score_without_architecture(self):
        """Should allow missing architecture_score."""
        health = HealthScoreUpload(
            health_score=75.0,
            structure_score=80.0,
            quality_score=70.0,
        )
        
        assert health.architecture_score is None

    def test_health_score_zero_valid(self):
        """Should accept zero scores."""
        health = HealthScoreUpload(
            health_score=0.0,
            structure_score=0.0,
            quality_score=0.0,
        )
        
        assert health.health_score == 0.0

    def test_health_score_max_valid(self):
        """Should accept 100 as max score."""
        health = HealthScoreUpload(
            health_score=100.0,
            structure_score=100.0,
            quality_score=100.0,
            architecture_score=100.0,
        )
        
        assert health.health_score == 100.0

    def test_health_score_over_100_raises(self):
        """Should raise for scores over 100."""
        with pytest.raises(ValueError):
            HealthScoreUpload(
                health_score=101.0,
                structure_score=100.0,
                quality_score=100.0,
            )

    def test_health_score_negative_raises(self):
        """Should raise for negative scores."""
        with pytest.raises(ValueError):
            HealthScoreUpload(
                health_score=-1.0,
                structure_score=100.0,
                quality_score=100.0,
            )


class TestCLISyncRequestModel:
    """Test CLISyncRequest Pydantic model."""

    def test_valid_sync_request_minimal(self):
        """Should create valid sync request with minimal fields."""
        request = CLISyncRequest(
            repo_name="my-project",
            cli_version=TEST_CLI_VERSION,
            health=HealthScoreUpload(
                health_score=80.0,
                structure_score=85.0,
                quality_score=75.0,
            ),
        )
        
        assert request.repo_name == "my-project"
        assert request.health.health_score == 80.0
        assert request.findings == []
        assert request.cli_version == TEST_CLI_VERSION

    def test_valid_sync_request_full(self):
        """Should create valid sync request with all fields."""
        request = CLISyncRequest(
            repo_name="my-project",
            repo_url="https://github.com/user/my-project",
            commit_sha="abc123def456",
            branch="main",
            cli_version=TEST_CLI_VERSION,
            health=HealthScoreUpload(
                health_score=80.0,
                structure_score=85.0,
                quality_score=75.0,
            ),
            findings=[
                FindingUpload(
                    detector_id="test",
                    title="Test Finding",
                    description="A test",
                    severity="medium",
                    file_path="test.py",
                    line_start=1,
                ),
            ],
            total_files=100,
            total_functions=50,
            total_classes=20,
        )
        
        assert request.repo_url == "https://github.com/user/my-project"
        assert request.commit_sha == "abc123def456"
        assert request.branch == "main"
        assert len(request.findings) == 1
        assert request.total_files == 100

    def test_sync_request_with_multiple_findings(self):
        """Should accept multiple findings."""
        findings = [
            FindingUpload(
                detector_id=f"detector-{i}",
                title=f"Finding {i}",
                description=f"Description {i}",
                severity="low",
                file_path=f"file{i}.py",
                line_start=i * 10,
            )
            for i in range(5)
        ]
        
        request = CLISyncRequest(
            repo_name="my-project",
            cli_version=TEST_CLI_VERSION,
            health=HealthScoreUpload(
                health_score=80.0,
                structure_score=85.0,
                quality_score=75.0,
            ),
            findings=findings,
        )
        
        assert len(request.findings) == 5


class TestCLISyncResponseModel:
    """Test CLISyncResponse Pydantic model."""

    def test_valid_sync_response(self):
        """Should create valid sync response."""
        response = CLISyncResponse(
            status="synced",
            repository_id="repo_123",
            analysis_id="analysis_456",
            findings_synced=10,
            dashboard_url="https://app.repotoire.io/dashboard/repo_123",
        )
        
        assert response.status == "synced"
        assert response.repository_id == "repo_123"
        assert response.analysis_id == "analysis_456"
        assert response.findings_synced == 10
        assert "dashboard" in response.dashboard_url

    def test_sync_response_created_status(self):
        """Should allow 'created' status for new repos."""
        response = CLISyncResponse(
            status="created",
            repository_id="repo_789",
            analysis_id="analysis_101",
            findings_synced=5,
            dashboard_url="https://app.repotoire.io/dashboard/repo_789",
        )
        
        assert response.status == "created"
        assert response.repository_id == "repo_789"
        assert response.findings_synced == 5


class TestCLISyncIntegration:
    """Integration tests for CLI sync flow."""

    def test_sync_request_to_response_fields_align(self):
        """Request and response models should have compatible field types."""
        # Create a request
        request = CLISyncRequest(
            repo_name="test-repo",
            cli_version=TEST_CLI_VERSION,
            health=HealthScoreUpload(
                health_score=90.0,
                structure_score=95.0,
                quality_score=85.0,
            ),
            findings=[
                FindingUpload(
                    detector_id="test",
                    title="Test",
                    description="Test",
                    severity="high",
                    file_path="test.py",
                    line_start=1,
                ),
            ],
        )
        
        # Verify fields are serializable
        request_dict = request.model_dump()
        assert "repo_name" in request_dict
        assert "health" in request_dict
        assert "findings" in request_dict
        assert "cli_version" in request_dict
        assert len(request_dict["findings"]) == 1
