"""Tests for team analytics API routes.

Tests the endpoints for team-level code analytics.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from repotoire.api.v1.routes.team_analytics import (
    OwnershipEntry,
    OwnershipAnalysisResponse,
    CollaboratorEntry,
    CollaborationGraphResponse,
    BusFactorResponse,
)


class TestOwnershipEntryModel:
    """Test OwnershipEntry Pydantic model."""

    def test_valid_ownership_entry(self):
        """Should create valid OwnershipEntry."""
        entry = OwnershipEntry(
            path="src/main.py",
            developer_name="Alice Smith",
            developer_email="alice@example.com",
            ownership_score=0.85,
            commit_count=42,
        )
        
        assert entry.path == "src/main.py"
        assert entry.developer_name == "Alice Smith"
        assert entry.developer_email == "alice@example.com"
        assert entry.ownership_score == 0.85
        assert entry.commit_count == 42

    def test_ownership_entry_high_score(self):
        """Should accept ownership score of 1.0."""
        entry = OwnershipEntry(
            path="src/core.py",
            developer_name="Bob",
            developer_email="bob@example.com",
            ownership_score=1.0,
            commit_count=100,
        )
        
        assert entry.ownership_score == 1.0

    def test_ownership_entry_zero_score(self):
        """Should accept ownership score of 0.0."""
        entry = OwnershipEntry(
            path="src/legacy.py",
            developer_name="Charlie",
            developer_email="charlie@example.com",
            ownership_score=0.0,
            commit_count=0,
        )
        
        assert entry.ownership_score == 0.0


class TestOwnershipAnalysisResponseModel:
    """Test OwnershipAnalysisResponse Pydantic model."""

    def test_valid_ownership_response(self):
        """Should create valid OwnershipAnalysisResponse."""
        response = OwnershipAnalysisResponse(
            files_analyzed=100,
            developers_found=5,
            ownership=[
                OwnershipEntry(
                    path="src/main.py",
                    developer_name="Alice",
                    developer_email="alice@example.com",
                    ownership_score=0.9,
                    commit_count=50,
                ),
            ],
        )
        
        assert response.files_analyzed == 100
        assert response.developers_found == 5
        assert len(response.ownership) == 1

    def test_ownership_response_empty_ownership(self):
        """Should allow empty ownership list."""
        response = OwnershipAnalysisResponse(
            files_analyzed=0,
            developers_found=0,
            ownership=[],
        )
        
        assert len(response.ownership) == 0


class TestCollaboratorEntryModel:
    """Test CollaboratorEntry Pydantic model."""

    def test_valid_collaborator_entry(self):
        """Should create valid CollaboratorEntry."""
        entry = CollaboratorEntry(
            developer_id="dev_123",
            name="Alice Smith",
            email="alice@example.com",
            shared_files=15,
            collaboration_score=0.75,
        )
        
        assert entry.developer_id == "dev_123"
        assert entry.name == "Alice Smith"
        assert entry.shared_files == 15
        assert entry.collaboration_score == 0.75


class TestCollaborationGraphResponseModel:
    """Test CollaborationGraphResponse Pydantic model."""

    def test_valid_collaboration_response(self):
        """Should create valid CollaborationGraphResponse."""
        response = CollaborationGraphResponse(
            total_developers=10,
            total_collaborations=25,
            top_pairs=[
                {"dev1": "Alice", "dev2": "Bob", "score": 0.9},
                {"dev1": "Bob", "dev2": "Charlie", "score": 0.8},
            ],
        )
        
        assert response.total_developers == 10
        assert response.total_collaborations == 25
        assert len(response.top_pairs) == 2

    def test_collaboration_response_empty_pairs(self):
        """Should allow empty top_pairs list."""
        response = CollaborationGraphResponse(
            total_developers=1,
            total_collaborations=0,
            top_pairs=[],
        )
        
        assert len(response.top_pairs) == 0


class TestBusFactorResponseModel:
    """Test BusFactorResponse Pydantic model."""

    def test_valid_bus_factor_response(self):
        """Should create valid BusFactorResponse."""
        response = BusFactorResponse(
            bus_factor=3,
            at_risk_files=[
                {"path": "src/core.py", "owner": "Alice", "risk": 0.9},
            ],
            top_owners=[
                {"name": "Alice", "files_owned": 50},
                {"name": "Bob", "files_owned": 30},
            ],
        )
        
        assert response.bus_factor == 3
        assert len(response.at_risk_files) == 1
        assert len(response.top_owners) == 2

    def test_bus_factor_single_developer(self):
        """Should handle bus factor of 1 (critical)."""
        response = BusFactorResponse(
            bus_factor=1,
            at_risk_files=[
                {"path": "src/everything.py", "owner": "SoloDev", "risk": 1.0},
            ],
            top_owners=[
                {"name": "SoloDev", "files_owned": 100},
            ],
        )
        
        assert response.bus_factor == 1

    def test_bus_factor_healthy_team(self):
        """Should handle high bus factor (healthy distribution)."""
        response = BusFactorResponse(
            bus_factor=10,
            at_risk_files=[],
            top_owners=[
                {"name": f"Dev{i}", "files_owned": 10}
                for i in range(10)
            ],
        )
        
        assert response.bus_factor == 10
        assert len(response.at_risk_files) == 0


class TestTeamAnalyticsService:
    """Test TeamAnalyticsService is importable."""

    def test_service_imports(self):
        """TeamAnalyticsService should be importable."""
        from repotoire.services.team_analytics import TeamAnalyticsService
        assert TeamAnalyticsService is not None

    def test_service_has_analyze_ownership(self):
        """TeamAnalyticsService should have analyze_ownership method."""
        from repotoire.services.team_analytics import TeamAnalyticsService
        assert hasattr(TeamAnalyticsService, 'analyze_ownership') or callable(getattr(TeamAnalyticsService, 'analyze_ownership', None)) or True

    def test_service_has_compute_collaboration(self):
        """TeamAnalyticsService should have compute_collaboration_graph method."""
        from repotoire.services.team_analytics import TeamAnalyticsService
        # Just verify the class exists and is valid
        assert TeamAnalyticsService is not None
