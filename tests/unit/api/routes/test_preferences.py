"""Unit tests for user preferences API routes.

Tests cover:
- GET preferences (creates defaults on first access)
- PUT preferences (partial updates)
- Invalid theme values fallback to "system"
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from repotoire.api.shared.auth import ClerkUser


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_clerk_user():
    """Create a mock Clerk user."""
    return ClerkUser(
        user_id="user_test123",
        session_id="sess_test123",
        org_id="org_test123",
        org_slug="test-org",
    )


@pytest.fixture
def mock_db_user():
    """Create a mock database user."""
    user = MagicMock()
    user.id = uuid4()
    user.email = "test@example.com"
    user.name = "Test User"
    user.clerk_user_id = "user_test123"
    return user


@pytest.fixture
def mock_preferences(mock_db_user):
    """Create mock user preferences."""
    prefs = MagicMock()
    prefs.user_id = mock_db_user.id
    prefs.theme = "dark"
    prefs.new_fix_alerts = True
    prefs.critical_security_alerts = True
    prefs.weekly_summary = False
    prefs.auto_approve_high_confidence = False
    prefs.generate_tests = True
    prefs.create_git_branches = True
    return prefs


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    db = AsyncMock()
    return db


# =============================================================================
# Response Model Tests
# =============================================================================


class TestUserPreferencesModels:
    """Tests for user preferences Pydantic models."""

    def test_preferences_response_defaults(self):
        """UserPreferencesResponse should have correct defaults."""
        from repotoire.api.v1.routes.preferences import UserPreferencesResponse

        response = UserPreferencesResponse()

        assert response.theme == "system"
        assert response.new_fix_alerts is True
        assert response.critical_security_alerts is True
        assert response.weekly_summary is False
        assert response.auto_approve_high_confidence is False
        assert response.generate_tests is True
        assert response.create_git_branches is True

    def test_preferences_update_all_optional(self):
        """UserPreferencesUpdate should have all optional fields."""
        from repotoire.api.v1.routes.preferences import UserPreferencesUpdate

        # All fields are optional, so empty update is valid
        update = UserPreferencesUpdate()
        assert update.theme is None
        assert update.new_fix_alerts is None
        assert update.critical_security_alerts is None
        assert update.weekly_summary is None
        assert update.auto_approve_high_confidence is None
        assert update.generate_tests is None
        assert update.create_git_branches is None

    def test_preferences_update_partial(self):
        """UserPreferencesUpdate should accept partial updates."""
        from repotoire.api.v1.routes.preferences import UserPreferencesUpdate

        update = UserPreferencesUpdate(
            theme="dark",
            weekly_summary=True,
        )
        assert update.theme == "dark"
        assert update.weekly_summary is True
        assert update.new_fix_alerts is None  # Not provided


# =============================================================================
# GET Preferences Tests
# =============================================================================


class TestGetPreferences:
    """Tests for GET /account/preferences endpoint."""

    @pytest.mark.asyncio
    async def test_get_preferences_creates_defaults_for_new_user(
        self, mock_clerk_user, mock_db_user, mock_db
    ):
        """Should create and return default preferences for user without settings."""
        from repotoire.api.v1.routes.preferences import (
            get_preferences,
            get_or_create_preferences,
        )

        # Mock database to return no existing preferences
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        with patch(
            "repotoire.api.v1.routes.preferences.get_or_create_db_user",
            return_value=mock_db_user,
        ):
            response = await get_preferences(
                user=mock_clerk_user,
                db=mock_db,
            )

        # Should return defaults
        assert response.theme == "system"
        assert response.new_fix_alerts is True
        assert response.critical_security_alerts is True
        assert response.weekly_summary is False
        assert response.auto_approve_high_confidence is False
        assert response.generate_tests is True
        assert response.create_git_branches is True

    @pytest.mark.asyncio
    async def test_get_preferences_returns_existing_settings(
        self, mock_clerk_user, mock_db_user, mock_preferences, mock_db
    ):
        """Should return existing user preferences when they exist."""
        from repotoire.api.v1.routes.preferences import get_preferences

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_preferences
        mock_db.execute.return_value = mock_result
        mock_db.commit = AsyncMock()

        with patch(
            "repotoire.api.v1.routes.preferences.get_or_create_db_user",
            return_value=mock_db_user,
        ):
            response = await get_preferences(
                user=mock_clerk_user,
                db=mock_db,
            )

        # Should return existing preferences
        assert response.theme == "dark"
        assert response.new_fix_alerts is True

    @pytest.mark.asyncio
    async def test_get_or_create_preferences_creates_new(self, mock_db_user, mock_db):
        """get_or_create_preferences should create new preferences if none exist."""
        from repotoire.api.v1.routes.preferences import get_or_create_preferences

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        prefs = await get_or_create_preferences(mock_db, mock_db_user)

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
        assert prefs.user_id == mock_db_user.id
        assert prefs.theme == "system"


# =============================================================================
# PUT Preferences Tests
# =============================================================================


class TestUpdatePreferences:
    """Tests for PUT /account/preferences endpoint."""

    @pytest.mark.asyncio
    async def test_update_preferences_partial_update(
        self, mock_clerk_user, mock_db_user, mock_preferences, mock_db
    ):
        """Should only update provided fields."""
        from repotoire.api.v1.routes.preferences import (
            update_preferences,
            UserPreferencesUpdate,
        )

        # Setup existing preferences
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_preferences
        mock_db.execute.return_value = mock_result
        mock_db.commit = AsyncMock()

        # Only update weekly_summary
        update = UserPreferencesUpdate(weekly_summary=True)

        with patch(
            "repotoire.api.v1.routes.preferences.get_or_create_db_user",
            return_value=mock_db_user,
        ):
            response = await update_preferences(
                update=update,
                user=mock_clerk_user,
                db=mock_db,
            )

        # Only weekly_summary should change
        assert mock_preferences.weekly_summary is True
        # Other fields unchanged
        assert mock_preferences.theme == "dark"

    @pytest.mark.asyncio
    async def test_update_preferences_invalid_theme_fallback(
        self, mock_clerk_user, mock_db_user, mock_preferences, mock_db
    ):
        """Should fallback to 'system' for invalid theme values."""
        from repotoire.api.v1.routes.preferences import (
            update_preferences,
            UserPreferencesUpdate,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_preferences
        mock_db.execute.return_value = mock_result
        mock_db.commit = AsyncMock()

        # Try invalid theme
        update = UserPreferencesUpdate(theme="invalid_theme")

        with patch(
            "repotoire.api.v1.routes.preferences.get_or_create_db_user",
            return_value=mock_db_user,
        ):
            response = await update_preferences(
                update=update,
                user=mock_clerk_user,
                db=mock_db,
            )

        # Should fallback to "system"
        assert mock_preferences.theme == "system"
        assert response.theme == "system"

    @pytest.mark.asyncio
    async def test_update_preferences_valid_themes(
        self, mock_clerk_user, mock_db_user, mock_db
    ):
        """Should accept valid theme values: light, dark, system."""
        from repotoire.api.v1.routes.preferences import (
            update_preferences,
            UserPreferencesUpdate,
        )

        for valid_theme in ["light", "dark", "system"]:
            mock_prefs = MagicMock()
            mock_prefs.theme = "dark"
            mock_prefs.new_fix_alerts = True
            mock_prefs.critical_security_alerts = True
            mock_prefs.weekly_summary = False
            mock_prefs.auto_approve_high_confidence = False
            mock_prefs.generate_tests = True
            mock_prefs.create_git_branches = True

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_prefs
            mock_db.execute.return_value = mock_result
            mock_db.commit = AsyncMock()

            update = UserPreferencesUpdate(theme=valid_theme)

            with patch(
                "repotoire.api.v1.routes.preferences.get_or_create_db_user",
                return_value=mock_db_user,
            ):
                response = await update_preferences(
                    update=update,
                    user=mock_clerk_user,
                    db=mock_db,
                )

            assert mock_prefs.theme == valid_theme
            assert response.theme == valid_theme

    @pytest.mark.asyncio
    async def test_update_preferences_all_fields(
        self, mock_clerk_user, mock_db_user, mock_preferences, mock_db
    ):
        """Should update all fields when all are provided."""
        from repotoire.api.v1.routes.preferences import (
            update_preferences,
            UserPreferencesUpdate,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_preferences
        mock_db.execute.return_value = mock_result
        mock_db.commit = AsyncMock()

        update = UserPreferencesUpdate(
            theme="light",
            new_fix_alerts=False,
            critical_security_alerts=False,
            weekly_summary=True,
            auto_approve_high_confidence=True,
            generate_tests=False,
            create_git_branches=False,
        )

        with patch(
            "repotoire.api.v1.routes.preferences.get_or_create_db_user",
            return_value=mock_db_user,
        ):
            response = await update_preferences(
                update=update,
                user=mock_clerk_user,
                db=mock_db,
            )

        assert mock_preferences.theme == "light"
        assert mock_preferences.new_fix_alerts is False
        assert mock_preferences.critical_security_alerts is False
        assert mock_preferences.weekly_summary is True
        assert mock_preferences.auto_approve_high_confidence is True
        assert mock_preferences.generate_tests is False
        assert mock_preferences.create_git_branches is False

    @pytest.mark.asyncio
    async def test_update_preferences_creates_if_not_exists(
        self, mock_clerk_user, mock_db_user, mock_db
    ):
        """Should create preferences if they don't exist when updating."""
        from repotoire.api.v1.routes.preferences import (
            update_preferences,
            UserPreferencesUpdate,
        )

        # Mock: first call returns None (no prefs), flush creates them
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        update = UserPreferencesUpdate(theme="dark")

        with patch(
            "repotoire.api.v1.routes.preferences.get_or_create_db_user",
            return_value=mock_db_user,
        ):
            response = await update_preferences(
                update=update,
                user=mock_clerk_user,
                db=mock_db,
            )

        # Should have added new preferences
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
