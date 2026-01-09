"""Integration tests for account API routes (REPO-278).

Tests cover:
- Account status endpoint
- Data export endpoints
- Account deletion endpoints
- Consent management endpoints
"""

import os
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from repotoire.api.v1.routes.account import router
from repotoire.api.auth import ClerkUser
from repotoire.db.models import ConsentType, DataExport, ExportStatus, User


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_user():
    """Create a mock database user."""
    user = MagicMock(spec=User)
    user.id = uuid4()
    user.email = "test@example.com"
    user.name = "Test User"
    user.avatar_url = "https://example.com/avatar.jpg"
    user.clerk_user_id = "user_test123"
    user.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    user.updated_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
    user.deleted_at = None
    user.anonymized_at = None
    user.deletion_requested_at = None
    user.has_pending_deletion = False
    user.memberships = []
    user.consent_records = []
    return user


@pytest.fixture
def mock_clerk_user():
    """Create a mock Clerk user."""
    return ClerkUser(user_id="user_test123", session_id="sess_test123")


@pytest.fixture
def mock_export(mock_user):
    """Create a mock data export."""
    export = MagicMock(spec=DataExport)
    export.id = uuid4()
    export.user_id = mock_user.id
    export.status = ExportStatus.PENDING
    export.download_url = None
    export.expires_at = datetime(2024, 12, 31, tzinfo=timezone.utc)
    export.created_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
    export.completed_at = None
    export.error_message = None
    export.file_size_bytes = None
    return export


@pytest.fixture
def app():
    """Create test FastAPI app with account routes."""
    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


# =============================================================================
# Response Model Validation Tests
# =============================================================================


class TestResponseModels:
    """Tests for response model validation."""

    def test_data_export_response_serialization(self, mock_export):
        """DataExportResponse should serialize correctly."""
        from repotoire.api.v1.routes.account import DataExportResponse

        response = DataExportResponse(
            export_id=str(mock_export.id),
            status=mock_export.status.value,
            download_url=mock_export.download_url,
            expires_at=mock_export.expires_at,
            created_at=mock_export.created_at,
            file_size_bytes=mock_export.file_size_bytes,
        )

        assert response.export_id == str(mock_export.id)
        assert response.status == "pending"
        assert response.download_url is None

    def test_deletion_scheduled_response(self):
        """DeletionScheduledResponse should have correct structure."""
        from repotoire.api.v1.routes.account import DeletionScheduledResponse

        deletion_date = datetime(2024, 7, 1, tzinfo=timezone.utc)
        response = DeletionScheduledResponse(
            deletion_scheduled_for=deletion_date,
            grace_period_days=30,
            cancellation_url="/settings/privacy?cancel_deletion=true",
            message="Account scheduled for deletion",
        )

        assert response.deletion_scheduled_for == deletion_date
        assert response.grace_period_days == 30
        assert "cancel_deletion" in response.cancellation_url

    def test_consent_response_defaults(self):
        """ConsentResponse should have essential always true."""
        from repotoire.api.v1.routes.account import ConsentResponse

        response = ConsentResponse(analytics=False, marketing=False)

        assert response.essential is True
        assert response.analytics is False
        assert response.marketing is False

    def test_account_status_response(self):
        """AccountStatusResponse should include all fields."""
        from repotoire.api.v1.routes.account import (
            AccountStatusResponse,
            ConsentResponse,
        )

        response = AccountStatusResponse(
            user_id="test-user-id",
            email="test@example.com",
            has_pending_deletion=False,
            deletion_scheduled_for=None,
            consent=ConsentResponse(analytics=True, marketing=False),
        )

        assert response.user_id == "test-user-id"
        assert response.email == "test@example.com"
        assert response.has_pending_deletion is False
        assert response.consent.analytics is True


# =============================================================================
# Request Validation Tests
# =============================================================================


class TestRequestValidation:
    """Tests for request model validation."""

    def test_delete_confirmation_valid_email(self):
        """DeleteConfirmation should validate email format."""
        from repotoire.api.v1.routes.account import DeleteConfirmation

        confirmation = DeleteConfirmation(
            email="test@example.com",
            confirmation_text="delete my account",
        )

        assert confirmation.email == "test@example.com"
        assert confirmation.confirmation_text == "delete my account"

    def test_delete_confirmation_invalid_email(self):
        """DeleteConfirmation should reject invalid email."""
        from pydantic import ValidationError
        from repotoire.api.v1.routes.account import DeleteConfirmation

        with pytest.raises(ValidationError):
            DeleteConfirmation(
                email="not-an-email",
                confirmation_text="delete my account",
            )

    def test_consent_update_request(self):
        """ConsentUpdateRequest should accept boolean values."""
        from repotoire.api.v1.routes.account import ConsentUpdateRequest

        request = ConsentUpdateRequest(analytics=True, marketing=False)

        assert request.analytics is True
        assert request.marketing is False

    def test_consent_update_request_requires_both_fields(self):
        """ConsentUpdateRequest should require both analytics and marketing."""
        from pydantic import ValidationError
        from repotoire.api.v1.routes.account import ConsentUpdateRequest

        with pytest.raises(ValidationError):
            ConsentUpdateRequest(analytics=True)  # Missing marketing


# =============================================================================
# Database Integration Tests (with Neon)
# =============================================================================


def _has_database_url() -> bool:
    """Check if DATABASE_URL is configured for remote database."""
    url = os.getenv("DATABASE_URL", "")
    return bool(url.strip())


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestGDPRServiceWithDatabase:
    """Integration tests for GDPR service with real database."""

    @pytest.mark.asyncio
    async def test_create_data_export(self, db_session, test_user):
        """Test creating a data export request."""
        from repotoire.api.services.gdpr import create_data_export

        export = await create_data_export(db_session, test_user.id)

        assert export is not None
        assert export.user_id == test_user.id
        assert export.status == ExportStatus.PENDING
        assert export.expires_at is not None

    @pytest.mark.asyncio
    async def test_schedule_deletion(self, db_session, test_user):
        """Test scheduling account deletion with grace period."""
        from repotoire.api.services.gdpr import schedule_deletion, GRACE_PERIOD_DAYS

        result = await schedule_deletion(db_session, test_user.id)

        assert result.grace_period_days == GRACE_PERIOD_DAYS
        assert result.can_cancel is True
        assert result.deletion_date > datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_cancel_deletion(self, db_session, test_user_with_deletion):
        """Test cancelling a pending deletion."""
        from repotoire.api.services.gdpr import cancel_deletion

        success = await cancel_deletion(db_session, test_user_with_deletion.id)

        assert success is True

    @pytest.mark.asyncio
    async def test_cancel_deletion_no_pending(self, db_session, test_user):
        """Test cancelling when no pending deletion exists."""
        from repotoire.api.services.gdpr import cancel_deletion

        success = await cancel_deletion(db_session, test_user.id)

        assert success is False

    @pytest.mark.asyncio
    async def test_record_consent(self, db_session, test_user):
        """Test recording consent preferences."""
        from repotoire.api.services.gdpr import record_consent

        record = await record_consent(
            db_session,
            test_user.id,
            ConsentType.ANALYTICS,
            granted=True,
            ip_address="192.168.1.1",
            user_agent="Test/1.0",
        )

        assert record is not None
        assert record.user_id == test_user.id
        assert record.consent_type == ConsentType.ANALYTICS
        assert record.granted is True

    @pytest.mark.asyncio
    async def test_get_current_consent_defaults(self, db_session, test_user):
        """Test getting default consent when no records exist."""
        from repotoire.api.services.gdpr import get_current_consent

        consent = await get_current_consent(db_session, test_user.id)

        assert consent["essential"] is True
        assert consent["analytics"] is False
        assert consent["marketing"] is False

    @pytest.mark.asyncio
    async def test_get_current_consent_after_update(self, db_session, test_user):
        """Test getting consent after recording preferences."""
        from repotoire.api.services.gdpr import record_consent, get_current_consent

        # Record consent
        await record_consent(db_session, test_user.id, ConsentType.ANALYTICS, granted=True)
        await record_consent(db_session, test_user.id, ConsentType.MARKETING, granted=True)

        consent = await get_current_consent(db_session, test_user.id)

        assert consent["analytics"] is True
        assert consent["marketing"] is True

    @pytest.mark.asyncio
    async def test_consent_latest_wins(self, db_session, test_user):
        """Test that the most recent consent decision takes precedence."""
        import asyncio
        from repotoire.api.services.gdpr import record_consent, get_current_consent

        # Grant analytics consent
        await record_consent(db_session, test_user.id, ConsentType.ANALYTICS, granted=True)
        await db_session.flush()

        # Small delay to ensure different timestamps
        await asyncio.sleep(0.1)

        # Then revoke it
        await record_consent(db_session, test_user.id, ConsentType.ANALYTICS, granted=False)
        await db_session.flush()

        consent = await get_current_consent(db_session, test_user.id)

        # Most recent (revoked) should win
        assert consent["analytics"] is False

    @pytest.mark.asyncio
    async def test_get_user_exports(self, db_session, test_user):
        """Test listing user's data exports."""
        from repotoire.api.services.gdpr import create_data_export, get_user_exports

        # Create multiple exports
        await create_data_export(db_session, test_user.id)
        await create_data_export(db_session, test_user.id)

        exports = await get_user_exports(db_session, test_user.id)

        assert len(exports) >= 2

    @pytest.mark.asyncio
    async def test_get_pending_deletion_none(self, db_session, test_user):
        """Test checking pending deletion when none exists."""
        from repotoire.api.services.gdpr import get_pending_deletion

        result = await get_pending_deletion(db_session, test_user.id)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_pending_deletion_exists(self, db_session, test_user_with_deletion):
        """Test checking pending deletion when scheduled."""
        from repotoire.api.services.gdpr import get_pending_deletion, GRACE_PERIOD_DAYS

        result = await get_pending_deletion(db_session, test_user_with_deletion.id)

        assert result is not None
        # Deletion date should be ~30 days from request
        expected_min = datetime.now(timezone.utc) + timedelta(days=GRACE_PERIOD_DAYS - 1)
        expected_max = datetime.now(timezone.utc) + timedelta(days=GRACE_PERIOD_DAYS + 1)
        assert expected_min <= result <= expected_max

    @pytest.mark.asyncio
    async def test_generate_export_data(self, db_session, test_user):
        """Test generating export data."""
        from repotoire.api.services.gdpr import generate_export_data

        export_data = await generate_export_data(db_session, test_user.id)

        assert export_data is not None
        assert export_data.user_profile["email"] == test_user.email
        assert export_data.user_profile["name"] == test_user.name
        assert "exported_at" in export_data.to_dict()

    @pytest.mark.asyncio
    async def test_generate_export_data_user_not_found(self, db_session):
        """Test generating export for non-existent user."""
        from repotoire.api.services.gdpr import generate_export_data

        fake_user_id = uuid4()

        with pytest.raises(ValueError, match=f"User {fake_user_id} not found"):
            await generate_export_data(db_session, fake_user_id)

    @pytest.mark.asyncio
    async def test_anonymize_user(self, db_session, test_user):
        """Test user anonymization."""
        from repotoire.api.services.gdpr import anonymize_user
        from sqlalchemy import select

        original_email = test_user.email

        await anonymize_user(db_session, test_user.id)
        await db_session.flush()

        # Refresh user from database
        result = await db_session.execute(
            select(User).where(User.id == test_user.id)
        )
        updated_user = result.scalar_one()

        assert updated_user.email != original_email
        assert "anonymized.repotoire.io" in updated_user.email
        assert updated_user.name == "Deleted User"
        assert updated_user.avatar_url is None
        assert updated_user.deleted_at is not None
        assert updated_user.anonymized_at is not None


# =============================================================================
# Full Workflow Integration Tests
# =============================================================================


@pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not configured")
class TestFullWorkflows:
    """End-to-end workflow tests with database."""

    @pytest.mark.asyncio
    async def test_full_export_workflow(self, db_session, test_user):
        """Test complete data export workflow."""
        from repotoire.api.services.gdpr import (
            create_data_export,
            get_data_export,
            update_export_status,
            generate_export_data,
        )

        # 1. Create export request
        export = await create_data_export(db_session, test_user.id)
        assert export.status == ExportStatus.PENDING

        # 2. Update to processing
        await update_export_status(db_session, export.id, ExportStatus.PROCESSING)
        await db_session.flush()

        # 3. Generate export data
        export_data = await generate_export_data(db_session, test_user.id)
        assert export_data is not None

        # 4. Mark as completed
        await update_export_status(
            db_session,
            export.id,
            ExportStatus.COMPLETED,
            download_url="https://example.com/export.json",
            file_size_bytes=1024,
        )
        await db_session.flush()

        # 5. Verify final state
        final_export = await get_data_export(db_session, export.id, test_user.id)
        assert final_export.status == ExportStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_full_deletion_workflow(self, db_session, test_user):
        """Test complete account deletion workflow."""
        from repotoire.api.services.gdpr import (
            schedule_deletion,
            get_pending_deletion,
            cancel_deletion,
        )

        # 1. Schedule deletion
        result = await schedule_deletion(db_session, test_user.id)
        assert result.can_cancel is True

        # 2. Verify pending
        pending_date = await get_pending_deletion(db_session, test_user.id)
        assert pending_date is not None

        # 3. Cancel deletion
        success = await cancel_deletion(db_session, test_user.id)
        assert success is True

        # 4. Verify no longer pending
        pending_date = await get_pending_deletion(db_session, test_user.id)
        assert pending_date is None

    @pytest.mark.asyncio
    async def test_consent_audit_trail(self, db_session, test_user):
        """Test consent changes create proper audit trail."""
        from repotoire.api.services.gdpr import record_consent, get_current_consent
        from sqlalchemy import select
        from repotoire.db.models import ConsentRecord

        # Make several consent changes
        await record_consent(db_session, test_user.id, ConsentType.ANALYTICS, granted=True)
        await record_consent(db_session, test_user.id, ConsentType.ANALYTICS, granted=False)
        await record_consent(db_session, test_user.id, ConsentType.ANALYTICS, granted=True)

        # Query all consent records
        result = await db_session.execute(
            select(ConsentRecord)
            .where(ConsentRecord.user_id == test_user.id)
            .where(ConsentRecord.consent_type == ConsentType.ANALYTICS)
        )
        records = result.scalars().all()

        # Should have 3 records (full audit trail)
        assert len(records) >= 3

        # Current consent should be the most recent (granted=True)
        consent = await get_current_consent(db_session, test_user.id)
        assert consent["analytics"] is True
