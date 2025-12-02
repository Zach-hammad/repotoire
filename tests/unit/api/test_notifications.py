"""Unit tests for notifications API routes.

Tests cover:
- Getting email preferences (default and custom)
- Updating email preferences
- Resetting email preferences
- Request/response model validation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_user():
    """Create a mock database user."""
    from repotoire.db.models import User

    user = MagicMock(spec=User)
    user.id = uuid4()
    user.email = "test@example.com"
    user.name = "Test User"
    user.clerk_user_id = "user_test123"
    return user


@pytest.fixture
def mock_clerk_user():
    """Create a mock Clerk user."""
    from repotoire.api.auth import ClerkUser

    return ClerkUser(user_id="user_test123", session_id="sess_test123")


@pytest.fixture
def mock_email_preferences(mock_user):
    """Create mock email preferences."""
    from repotoire.db.models import EmailPreferences

    prefs = MagicMock(spec=EmailPreferences)
    prefs.user_id = mock_user.id
    prefs.analysis_complete = True
    prefs.analysis_failed = True
    prefs.health_regression = True
    prefs.weekly_digest = False
    prefs.team_notifications = True
    prefs.billing_notifications = True
    prefs.regression_threshold = 10
    return prefs


@pytest.fixture
def app():
    """Create test FastAPI app with notifications routes."""
    from repotoire.api.routes.notifications import router

    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# =============================================================================
# Response Model Tests
# =============================================================================


class TestEmailPreferencesModels:
    """Tests for email preferences Pydantic models."""

    def test_email_preferences_request_defaults(self):
        """EmailPreferencesRequest should have sensible defaults."""
        from repotoire.api.routes.notifications import EmailPreferencesRequest

        request = EmailPreferencesRequest()

        assert request.analysis_complete is True
        assert request.analysis_failed is True
        assert request.health_regression is True
        assert request.weekly_digest is False
        assert request.team_notifications is True
        assert request.billing_notifications is True
        assert request.regression_threshold == 10

    def test_email_preferences_request_custom_values(self):
        """EmailPreferencesRequest should accept custom values."""
        from repotoire.api.routes.notifications import EmailPreferencesRequest

        request = EmailPreferencesRequest(
            analysis_complete=False,
            analysis_failed=False,
            health_regression=False,
            weekly_digest=True,
            team_notifications=False,
            billing_notifications=False,
            regression_threshold=25,
        )

        assert request.analysis_complete is False
        assert request.weekly_digest is True
        assert request.regression_threshold == 25

    def test_email_preferences_request_threshold_validation(self):
        """EmailPreferencesRequest should validate regression_threshold bounds."""
        from pydantic import ValidationError
        from repotoire.api.routes.notifications import EmailPreferencesRequest

        # Below minimum
        with pytest.raises(ValidationError):
            EmailPreferencesRequest(regression_threshold=0)

        # Above maximum
        with pytest.raises(ValidationError):
            EmailPreferencesRequest(regression_threshold=100)

        # Valid values at boundaries
        request_min = EmailPreferencesRequest(regression_threshold=1)
        request_max = EmailPreferencesRequest(regression_threshold=50)
        assert request_min.regression_threshold == 1
        assert request_max.regression_threshold == 50

    def test_email_preferences_response(self, mock_email_preferences):
        """EmailPreferencesResponse should serialize correctly."""
        from repotoire.api.routes.notifications import EmailPreferencesResponse

        response = EmailPreferencesResponse.model_validate(mock_email_preferences)

        assert response.analysis_complete is True
        assert response.weekly_digest is False
        assert response.regression_threshold == 10


# =============================================================================
# API Route Tests (Unit Tests with Mocks)
# =============================================================================


class TestGetEmailPreferences:
    """Tests for GET /notifications/preferences endpoint."""

    @pytest.mark.asyncio
    async def test_get_preferences_defaults_for_new_user(
        self, mock_clerk_user, mock_user
    ):
        """Should return default preferences for user without custom settings."""
        from repotoire.api.routes.notifications import get_email_preferences

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with patch(
            "repotoire.api.routes.notifications.get_user_by_clerk_id",
            return_value=mock_user,
        ):
            response = await get_email_preferences(
                current_user=mock_clerk_user,
                session=mock_session,
            )

        assert response.analysis_complete is True
        assert response.analysis_failed is True
        assert response.health_regression is True
        assert response.weekly_digest is False
        assert response.team_notifications is True
        assert response.billing_notifications is True
        assert response.regression_threshold == 10

    @pytest.mark.asyncio
    async def test_get_preferences_returns_custom_settings(
        self, mock_clerk_user, mock_user, mock_email_preferences
    ):
        """Should return user's custom preferences when they exist."""
        from repotoire.api.routes.notifications import get_email_preferences

        # Customize the mock preferences
        mock_email_preferences.weekly_digest = True
        mock_email_preferences.regression_threshold = 20

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_email_preferences
        mock_session.execute.return_value = mock_result

        with patch(
            "repotoire.api.routes.notifications.get_user_by_clerk_id",
            return_value=mock_user,
        ):
            response = await get_email_preferences(
                current_user=mock_clerk_user,
                session=mock_session,
            )

        assert response.weekly_digest is True
        assert response.regression_threshold == 20


class TestUpdateEmailPreferences:
    """Tests for PUT /notifications/preferences endpoint."""

    @pytest.mark.asyncio
    async def test_update_preferences_creates_new(
        self, mock_clerk_user, mock_user, mock_email_preferences
    ):
        """Should create new preferences if none exist."""
        from repotoire.api.routes.notifications import (
            update_email_preferences,
            EmailPreferencesRequest,
        )

        mock_session = AsyncMock()

        # First call returns None (no existing prefs), second returns the new prefs
        mock_result_none = MagicMock()
        mock_result_none.scalar_one_or_none.return_value = None

        mock_result_prefs = MagicMock()
        mock_result_prefs.scalar_one_or_none.return_value = mock_email_preferences

        mock_session.execute.return_value = mock_result_none

        request = EmailPreferencesRequest(
            analysis_complete=False,
            weekly_digest=True,
        )

        with patch(
            "repotoire.api.routes.notifications.get_user_by_clerk_id",
            return_value=mock_user,
        ):
            response = await update_email_preferences(
                preferences=request,
                current_user=mock_clerk_user,
                session=mock_session,
            )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_preferences_updates_existing(
        self, mock_clerk_user, mock_user, mock_email_preferences
    ):
        """Should update existing preferences."""
        from repotoire.api.routes.notifications import (
            update_email_preferences,
            EmailPreferencesRequest,
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_email_preferences
        mock_session.execute.return_value = mock_result

        request = EmailPreferencesRequest(
            analysis_complete=False,
            regression_threshold=30,
        )

        with patch(
            "repotoire.api.routes.notifications.get_user_by_clerk_id",
            return_value=mock_user,
        ):
            response = await update_email_preferences(
                preferences=request,
                current_user=mock_clerk_user,
                session=mock_session,
            )

        # Verify preferences were updated
        assert mock_email_preferences.analysis_complete is False
        assert mock_email_preferences.regression_threshold == 30
        mock_session.commit.assert_called_once()


class TestResetEmailPreferences:
    """Tests for POST /notifications/preferences/reset endpoint."""

    @pytest.mark.asyncio
    async def test_reset_preferences_deletes_existing(
        self, mock_clerk_user, mock_user, mock_email_preferences
    ):
        """Should delete existing preferences and return defaults."""
        from repotoire.api.routes.notifications import reset_email_preferences

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_email_preferences
        mock_session.execute.return_value = mock_result

        with patch(
            "repotoire.api.routes.notifications.get_user_by_clerk_id",
            return_value=mock_user,
        ):
            response = await reset_email_preferences(
                current_user=mock_clerk_user,
                session=mock_session,
            )

        mock_session.delete.assert_called_once_with(mock_email_preferences)
        mock_session.commit.assert_called_once()

        # Should return defaults
        assert response.analysis_complete is True
        assert response.weekly_digest is False
        assert response.regression_threshold == 10

    @pytest.mark.asyncio
    async def test_reset_preferences_no_existing(
        self, mock_clerk_user, mock_user
    ):
        """Should return defaults even if no preferences exist."""
        from repotoire.api.routes.notifications import reset_email_preferences

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with patch(
            "repotoire.api.routes.notifications.get_user_by_clerk_id",
            return_value=mock_user,
        ):
            response = await reset_email_preferences(
                current_user=mock_clerk_user,
                session=mock_session,
            )

        mock_session.delete.assert_not_called()
        assert response.analysis_complete is True
        assert response.weekly_digest is False


class TestUserNotFound:
    """Tests for user not found scenarios."""

    @pytest.mark.asyncio
    async def test_get_preferences_user_not_found(self, mock_clerk_user):
        """Should raise 404 if user not found."""
        from fastapi import HTTPException
        from repotoire.api.routes.notifications import get_email_preferences

        mock_session = AsyncMock()

        with patch(
            "repotoire.api.routes.notifications.get_user_by_clerk_id",
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_email_preferences(
                    current_user=mock_clerk_user,
                    session=mock_session,
                )

        assert exc_info.value.status_code == 404
        assert "User not found" in exc_info.value.detail
