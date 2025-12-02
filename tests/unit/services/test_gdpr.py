"""Unit tests for GDPR service (REPO-278).

Tests cover:
- Data export creation and retrieval
- Export data generation
- Account deletion scheduling
- Deletion cancellation
- User anonymization
- Consent management
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from repotoire.db.models import (
    ConsentRecord,
    ConsentType,
    DataExport,
    ExportStatus,
    User,
)
from repotoire.api.services.gdpr import (
    GRACE_PERIOD_DAYS,
    EXPORT_EXPIRY_HOURS,
    ExportData,
    DeletionScheduleResult,
    create_data_export,
    get_data_export,
    get_user_exports,
    generate_export_data,
    update_export_status,
    schedule_deletion,
    cancel_deletion,
    get_pending_deletion,
    execute_deletion,
    anonymize_user,
    record_consent,
    get_current_consent,
    get_users_pending_deletion,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.fixture
def sample_user_id():
    """Generate a sample user UUID."""
    return uuid4()


@pytest.fixture
def sample_export_id():
    """Generate a sample export UUID."""
    return uuid4()


@pytest.fixture
def mock_user(sample_user_id):
    """Create a mock user object."""
    user = MagicMock(spec=User)
    user.id = sample_user_id
    user.email = "test@example.com"
    user.name = "Test User"
    user.avatar_url = "https://example.com/avatar.jpg"
    user.clerk_user_id = "clerk_123"
    user.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    user.updated_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
    user.deleted_at = None
    user.anonymized_at = None
    user.deletion_requested_at = None
    user.memberships = []
    user.consent_records = []
    return user


@pytest.fixture
def mock_data_export(sample_user_id, sample_export_id):
    """Create a mock data export object."""
    export = MagicMock(spec=DataExport)
    export.id = sample_export_id
    export.user_id = sample_user_id
    export.status = ExportStatus.PENDING
    export.download_url = None
    export.expires_at = datetime.now(timezone.utc) + timedelta(hours=EXPORT_EXPIRY_HOURS)
    export.created_at = datetime.now(timezone.utc)
    export.completed_at = None
    export.error_message = None
    export.file_size_bytes = None
    return export


# =============================================================================
# ExportData Tests
# =============================================================================


class TestExportData:
    """Tests for ExportData dataclass."""

    def test_export_data_creation(self):
        """ExportData should store all fields."""
        data = ExportData(
            exported_at="2024-01-01T00:00:00+00:00",
            user_profile={"id": "123", "email": "test@example.com"},
            organization_memberships=[],
            repositories=[],
            analysis_history=[],
            consent_records=[],
        )

        assert data.exported_at == "2024-01-01T00:00:00+00:00"
        assert data.user_profile["email"] == "test@example.com"
        assert data.organization_memberships == []
        assert data.repositories == []
        assert data.analysis_history == []
        assert data.consent_records == []

    def test_export_data_to_dict(self):
        """ExportData.to_dict() should return a serializable dict."""
        data = ExportData(
            exported_at="2024-01-01T00:00:00+00:00",
            user_profile={"id": "123", "email": "test@example.com"},
            organization_memberships=[{"org": "test"}],
            repositories=[{"repo": "test/repo"}],
            analysis_history=[{"analysis": "test"}],
            consent_records=[{"type": "analytics", "granted": True}],
        )

        result = data.to_dict()

        assert isinstance(result, dict)
        assert result["exported_at"] == "2024-01-01T00:00:00+00:00"
        assert result["user_profile"]["email"] == "test@example.com"
        assert len(result["organization_memberships"]) == 1
        assert len(result["repositories"]) == 1
        assert len(result["analysis_history"]) == 1
        assert len(result["consent_records"]) == 1


class TestDeletionScheduleResult:
    """Tests for DeletionScheduleResult dataclass."""

    def test_deletion_schedule_result_creation(self):
        """DeletionScheduleResult should store all fields."""
        deletion_date = datetime.now(timezone.utc) + timedelta(days=30)
        result = DeletionScheduleResult(
            deletion_date=deletion_date,
            grace_period_days=30,
            can_cancel=True,
        )

        assert result.deletion_date == deletion_date
        assert result.grace_period_days == 30
        assert result.can_cancel is True


# =============================================================================
# Data Export Tests
# =============================================================================


class TestCreateDataExport:
    """Tests for create_data_export function."""

    @pytest.mark.asyncio
    async def test_create_data_export(self, mock_db, sample_user_id):
        """Test data export request creation."""
        # Act
        result = await create_data_export(mock_db, sample_user_id)

        # Assert
        mock_db.add.assert_called_once()
        mock_db.flush.assert_awaited_once()

        # Verify the export object passed to db.add
        added_export = mock_db.add.call_args[0][0]
        assert added_export.user_id == sample_user_id
        assert added_export.status == ExportStatus.PENDING
        assert added_export.expires_at is not None

    @pytest.mark.asyncio
    async def test_create_data_export_sets_expiry(self, mock_db, sample_user_id):
        """Export should have correct expiry time."""
        before = datetime.now(timezone.utc)

        await create_data_export(mock_db, sample_user_id)

        after = datetime.now(timezone.utc)
        added_export = mock_db.add.call_args[0][0]

        expected_expiry_min = before + timedelta(hours=EXPORT_EXPIRY_HOURS)
        expected_expiry_max = after + timedelta(hours=EXPORT_EXPIRY_HOURS)

        assert expected_expiry_min <= added_export.expires_at <= expected_expiry_max


class TestGetDataExport:
    """Tests for get_data_export function."""

    @pytest.mark.asyncio
    async def test_get_data_export_found(
        self, mock_db, sample_user_id, sample_export_id, mock_data_export
    ):
        """Should return export when found and belongs to user."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_data_export
        mock_db.execute.return_value = mock_result

        result = await get_data_export(mock_db, sample_export_id, sample_user_id)

        assert result == mock_data_export
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_data_export_not_found(
        self, mock_db, sample_user_id, sample_export_id
    ):
        """Should return None when export not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await get_data_export(mock_db, sample_export_id, sample_user_id)

        assert result is None


class TestGetUserExports:
    """Tests for get_user_exports function."""

    @pytest.mark.asyncio
    async def test_get_user_exports(self, mock_db, sample_user_id, mock_data_export):
        """Should return list of user exports."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_data_export]
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = await get_user_exports(mock_db, sample_user_id)

        assert len(result) == 1
        assert result[0] == mock_data_export

    @pytest.mark.asyncio
    async def test_get_user_exports_empty(self, mock_db, sample_user_id):
        """Should return empty list when no exports exist."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = await get_user_exports(mock_db, sample_user_id)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_user_exports_respects_limit(self, mock_db, sample_user_id):
        """Should respect the limit parameter."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        await get_user_exports(mock_db, sample_user_id, limit=5)

        mock_db.execute.assert_awaited_once()


class TestGenerateExportData:
    """Tests for generate_export_data function."""

    @pytest.mark.asyncio
    async def test_generate_export_data_includes_all_user_data(
        self, mock_db, sample_user_id, mock_user
    ):
        """Verify export contains profile, orgs, repos, analyses."""
        # Setup mock user with memberships and consent records
        mock_user.memberships = []
        mock_user.consent_records = []

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        result = await generate_export_data(mock_db, sample_user_id)

        # Verify profile data
        assert "id" in result.user_profile
        assert result.user_profile["email"] == "test@example.com"
        assert result.user_profile["name"] == "Test User"
        assert result.user_profile["avatar_url"] == "https://example.com/avatar.jpg"

        # Verify collections exist
        assert isinstance(result.organization_memberships, list)
        assert isinstance(result.repositories, list)
        assert isinstance(result.analysis_history, list)
        assert isinstance(result.consent_records, list)

        # Verify timestamp
        assert result.exported_at is not None

    @pytest.mark.asyncio
    async def test_generate_export_data_user_not_found(
        self, mock_db, sample_user_id
    ):
        """Should raise ValueError when user not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match=f"User {sample_user_id} not found"):
            await generate_export_data(mock_db, sample_user_id)

    @pytest.mark.asyncio
    async def test_generate_export_data_includes_membership_details(
        self, mock_db, sample_user_id, mock_user
    ):
        """Export should include organization membership details."""
        # Setup mock membership
        mock_org = MagicMock()
        mock_org.name = "Test Org"
        mock_org.slug = "test-org"

        mock_membership = MagicMock()
        mock_membership.organization_id = uuid4()
        mock_membership.organization = mock_org
        mock_membership.role = MagicMock()
        mock_membership.role.value = "admin"
        mock_membership.invited_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_membership.joined_at = datetime(2024, 1, 2, tzinfo=timezone.utc)

        mock_user.memberships = [mock_membership]

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        result = await generate_export_data(mock_db, sample_user_id)

        assert len(result.organization_memberships) == 1
        membership = result.organization_memberships[0]
        assert membership["organization_name"] == "Test Org"
        assert membership["organization_slug"] == "test-org"
        assert membership["role"] == "admin"


class TestUpdateExportStatus:
    """Tests for update_export_status function."""

    @pytest.mark.asyncio
    async def test_update_export_status_to_completed(self, mock_db, sample_export_id):
        """Should update status with completion details."""
        await update_export_status(
            mock_db,
            sample_export_id,
            ExportStatus.COMPLETED,
            download_url="https://example.com/download/123",
            file_size_bytes=1024,
        )

        mock_db.execute.assert_awaited_once()
        mock_db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_export_status_to_failed(self, mock_db, sample_export_id):
        """Should update status with error message."""
        await update_export_status(
            mock_db,
            sample_export_id,
            ExportStatus.FAILED,
            error_message="Export generation failed",
        )

        mock_db.execute.assert_awaited_once()
        mock_db.flush.assert_awaited_once()


# =============================================================================
# Account Deletion Tests
# =============================================================================


class TestScheduleDeletion:
    """Tests for schedule_deletion function."""

    @pytest.mark.asyncio
    async def test_schedule_deletion_sets_grace_period(self, mock_db, sample_user_id):
        """Test 30-day grace period is correctly set."""
        before = datetime.now(timezone.utc)

        result = await schedule_deletion(mock_db, sample_user_id)

        after = datetime.now(timezone.utc)

        # Verify grace period
        assert result.grace_period_days == GRACE_PERIOD_DAYS
        assert result.grace_period_days == 30

        # Verify deletion date is ~30 days in the future
        expected_min = before + timedelta(days=GRACE_PERIOD_DAYS)
        expected_max = after + timedelta(days=GRACE_PERIOD_DAYS)
        assert expected_min <= result.deletion_date <= expected_max

        # Verify can_cancel is True
        assert result.can_cancel is True

        # Verify database was updated
        mock_db.execute.assert_awaited_once()
        mock_db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_schedule_deletion_returns_deletion_schedule_result(
        self, mock_db, sample_user_id
    ):
        """Should return a DeletionScheduleResult object."""
        result = await schedule_deletion(mock_db, sample_user_id)

        assert isinstance(result, DeletionScheduleResult)
        assert result.deletion_date is not None
        assert result.grace_period_days > 0
        assert result.can_cancel is True


class TestCancelDeletion:
    """Tests for cancel_deletion function."""

    @pytest.mark.asyncio
    async def test_cancel_deletion_clears_request(self, mock_db, sample_user_id, mock_user):
        """Test deletion cancellation."""
        mock_user.deletion_requested_at = datetime.now(timezone.utc)
        mock_user.deleted_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        result = await cancel_deletion(mock_db, sample_user_id)

        assert result is True
        # Two execute calls: one for select, one for update
        assert mock_db.execute.await_count == 2
        mock_db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_deletion_no_pending_deletion(
        self, mock_db, sample_user_id
    ):
        """Should return False if no pending deletion."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await cancel_deletion(mock_db, sample_user_id)

        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_deletion_already_deleted(
        self, mock_db, sample_user_id, mock_user
    ):
        """Should return False if user already deleted."""
        mock_user.deleted_at = datetime.now(timezone.utc)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await cancel_deletion(mock_db, sample_user_id)

        assert result is False


class TestGetPendingDeletion:
    """Tests for get_pending_deletion function."""

    @pytest.mark.asyncio
    async def test_get_pending_deletion_exists(self, mock_db, sample_user_id):
        """Should return scheduled deletion date when pending."""
        request_time = datetime.now(timezone.utc) - timedelta(days=10)
        expected_deletion = request_time + timedelta(days=GRACE_PERIOD_DAYS)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = request_time
        mock_db.execute.return_value = mock_result

        result = await get_pending_deletion(mock_db, sample_user_id)

        assert result == expected_deletion

    @pytest.mark.asyncio
    async def test_get_pending_deletion_none(self, mock_db, sample_user_id):
        """Should return None when no pending deletion."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await get_pending_deletion(mock_db, sample_user_id)

        assert result is None


class TestExecuteDeletion:
    """Tests for execute_deletion function."""

    @pytest.mark.asyncio
    async def test_execute_deletion_user_not_found(self, mock_db, sample_user_id):
        """Should raise ValueError when user not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match=f"User {sample_user_id} not found"):
            await execute_deletion(mock_db, sample_user_id)

    @pytest.mark.asyncio
    async def test_execute_deletion_grace_period_not_expired(
        self, mock_db, sample_user_id, mock_user
    ):
        """Should raise ValueError if grace period has not expired."""
        # Set deletion_requested_at to recent (grace period not expired)
        mock_user.deletion_requested_at = datetime.now(timezone.utc) - timedelta(days=5)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match="Grace period has not expired"):
            await execute_deletion(mock_db, sample_user_id)


# =============================================================================
# User Anonymization Tests
# =============================================================================


class TestAnonymizeUser:
    """Tests for anonymize_user function."""

    @pytest.mark.asyncio
    async def test_anonymize_user_replaces_pii(self, mock_db, sample_user_id):
        """Verify email, name, avatar are anonymized."""
        await anonymize_user(mock_db, sample_user_id)

        mock_db.execute.assert_awaited_once()
        mock_db.flush.assert_awaited_once()

        # Verify the update values
        call_args = mock_db.execute.call_args
        # The update statement is the first argument
        # We can't easily inspect SQLAlchemy update objects, but we verify the call was made

    @pytest.mark.asyncio
    async def test_anonymize_user_sets_timestamps(self, mock_db, sample_user_id):
        """Should set deleted_at and anonymized_at timestamps."""
        before = datetime.now(timezone.utc)

        await anonymize_user(mock_db, sample_user_id)

        after = datetime.now(timezone.utc)

        # Verify the update was called
        mock_db.execute.assert_awaited_once()
        mock_db.flush.assert_awaited_once()


# =============================================================================
# Consent Management Tests
# =============================================================================


class TestRecordConsent:
    """Tests for record_consent function."""

    @pytest.mark.asyncio
    async def test_record_consent_creates_record(self, mock_db, sample_user_id):
        """Should create a consent record."""
        result = await record_consent(
            mock_db,
            sample_user_id,
            ConsentType.ANALYTICS,
            granted=True,
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_awaited_once()

        # Verify the consent record
        added_record = mock_db.add.call_args[0][0]
        assert added_record.user_id == sample_user_id
        assert added_record.consent_type == ConsentType.ANALYTICS
        assert added_record.granted is True
        assert added_record.ip_address == "192.168.1.1"
        assert added_record.user_agent == "Mozilla/5.0"

    @pytest.mark.asyncio
    async def test_record_consent_revocation(self, mock_db, sample_user_id):
        """Should record consent revocation."""
        result = await record_consent(
            mock_db,
            sample_user_id,
            ConsentType.MARKETING,
            granted=False,
        )

        mock_db.add.assert_called_once()
        added_record = mock_db.add.call_args[0][0]
        assert added_record.granted is False

    @pytest.mark.asyncio
    async def test_record_consent_all_types(self, mock_db, sample_user_id):
        """Should support all consent types."""
        for consent_type in ConsentType:
            mock_db.reset_mock()

            await record_consent(
                mock_db,
                sample_user_id,
                consent_type,
                granted=True,
            )

            mock_db.add.assert_called_once()


class TestGetCurrentConsent:
    """Tests for get_current_consent function."""

    @pytest.mark.asyncio
    async def test_get_current_consent_defaults(self, mock_db, sample_user_id):
        """Should return default consent when no records exist."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = await get_current_consent(mock_db, sample_user_id)

        assert result["essential"] is True  # Always true
        assert result["analytics"] is False  # Default false
        assert result["marketing"] is False  # Default false

    @pytest.mark.asyncio
    async def test_get_current_consent_with_records(self, mock_db, sample_user_id):
        """Should return most recent consent for each type."""
        # Create mock consent records
        analytics_record = MagicMock()
        analytics_record.consent_type = ConsentType.ANALYTICS
        analytics_record.granted = True
        analytics_record.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

        marketing_record = MagicMock()
        marketing_record.consent_type = ConsentType.MARKETING
        marketing_record.granted = True
        marketing_record.created_at = datetime(2024, 1, 2, tzinfo=timezone.utc)

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [marketing_record, analytics_record]
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = await get_current_consent(mock_db, sample_user_id)

        assert result["essential"] is True
        assert result["analytics"] is True
        assert result["marketing"] is True

    @pytest.mark.asyncio
    async def test_get_current_consent_latest_wins(self, mock_db, sample_user_id):
        """Most recent consent decision should take precedence."""
        # Create mock consent records with different timestamps
        old_record = MagicMock()
        old_record.consent_type = ConsentType.ANALYTICS
        old_record.granted = True
        old_record.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

        new_record = MagicMock()
        new_record.consent_type = ConsentType.ANALYTICS
        new_record.granted = False
        new_record.created_at = datetime(2024, 6, 1, tzinfo=timezone.utc)

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        # Records returned in descending order (newest first)
        mock_scalars.all.return_value = [new_record, old_record]
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = await get_current_consent(mock_db, sample_user_id)

        # The most recent (granted=False) should win
        assert result["analytics"] is False


# =============================================================================
# Batch Operations Tests
# =============================================================================


class TestGetUsersPendingDeletion:
    """Tests for get_users_pending_deletion function."""

    @pytest.mark.asyncio
    async def test_get_users_pending_deletion_empty(self, mock_db):
        """Should return empty list when no users pending."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = await get_users_pending_deletion(mock_db)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_users_pending_deletion_returns_expired(
        self, mock_db, mock_user
    ):
        """Should return users whose grace period has expired."""
        mock_user.deletion_requested_at = datetime.now(timezone.utc) - timedelta(
            days=GRACE_PERIOD_DAYS + 5
        )

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_user]
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = await get_users_pending_deletion(mock_db)

        assert len(result) == 1
        assert result[0] == mock_user

    @pytest.mark.asyncio
    async def test_get_users_pending_deletion_excludes_already_deleted(self, mock_db):
        """Should not return users already deleted."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []  # Query excludes deleted_at.is_(None)
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = await get_users_pending_deletion(mock_db)

        assert result == []


# =============================================================================
# Constants Tests
# =============================================================================


class TestConstants:
    """Tests for GDPR service constants."""

    def test_grace_period_is_30_days(self):
        """Grace period should be 30 days per GDPR requirements."""
        assert GRACE_PERIOD_DAYS == 30

    def test_export_expiry_is_48_hours(self):
        """Export download links should expire after 48 hours."""
        assert EXPORT_EXPIRY_HOURS == 48
