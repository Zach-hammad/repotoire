"""Unit tests for AuditService.

Tests cover:
- Audit log creation (application events)
- Clerk webhook event parsing and logging
- Request context extraction (IP, user agent)
- Query filtering and pagination
- Clerk event deduplication
- Decorator-based audit logging
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from repotoire.db.models.audit import AuditLog, AuditStatus, EventSource


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def audit_service():
    """Create AuditService instance."""
    from repotoire.services.audit import AuditService

    return AuditService()


@pytest.fixture
def mock_db():
    """Create mock database session."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    # Set up execute to return a mock result
    mock_result = MagicMock()
    mock_result.scalar.return_value = 0
    mock_result.scalar_one_or_none.return_value = None
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result.scalars.return_value = mock_scalars
    db.execute = AsyncMock(return_value=mock_result)

    return db


@pytest.fixture
def sample_user_id():
    """Sample user UUID."""
    return uuid4()


@pytest.fixture
def sample_org_id():
    """Sample organization UUID."""
    return uuid4()


@pytest.fixture
def mock_request():
    """Create mock FastAPI request."""
    request = MagicMock()
    request.headers = {
        "user-agent": "Mozilla/5.0 Test Browser",
        "x-forwarded-for": "203.0.113.45, 10.0.0.1",
    }
    request.client = MagicMock()
    request.client.host = "192.168.1.1"

    # Mock user from auth middleware
    request.state = MagicMock()
    request.state.user = MagicMock()
    request.state.user.id = uuid4()
    request.state.user.email = "user@example.com"
    request.state.organization = MagicMock()
    request.state.organization.id = uuid4()

    return request


# ============================================================================
# AuditService.log() Tests
# ============================================================================


class TestAuditServiceLog:
    """Tests for AuditService.log() method."""

    @pytest.mark.asyncio
    async def test_log_creates_audit_entry(
        self, audit_service, mock_db, sample_user_id, sample_org_id
    ):
        """log() should create an AuditLog entry with correct fields."""
        result = await audit_service.log(
            db=mock_db,
            event_type="repo.connected",
            actor_id=sample_user_id,
            actor_email="user@example.com",
            actor_ip="203.0.113.45",
            organization_id=sample_org_id,
            resource_type="repository",
            resource_id="repo-123",
            action="created",
            status=AuditStatus.SUCCESS,
            metadata={"repo_name": "owner/repo"},
        )

        # Should add the audit log to the session
        mock_db.add.assert_called_once()
        added_log = mock_db.add.call_args[0][0]

        assert isinstance(added_log, AuditLog)
        assert added_log.event_type == "repo.connected"
        assert added_log.event_source == EventSource.APPLICATION
        assert added_log.actor_id == sample_user_id
        assert added_log.actor_email == "user@example.com"
        assert added_log.actor_ip == "203.0.113.45"
        assert added_log.organization_id == sample_org_id
        assert added_log.resource_type == "repository"
        assert added_log.resource_id == "repo-123"
        assert added_log.action == "created"
        assert added_log.status == AuditStatus.SUCCESS
        assert added_log.event_metadata == {"repo_name": "owner/repo"}

        mock_db.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_with_clerk_source(self, audit_service, mock_db):
        """log() should accept Clerk as event source."""
        result = await audit_service.log(
            db=mock_db,
            event_type="user.login",
            event_source=EventSource.CLERK,
            actor_email="user@example.com",
            clerk_event_id="evt_123",
        )

        added_log = mock_db.add.call_args[0][0]
        assert added_log.event_source == EventSource.CLERK
        assert added_log.clerk_event_id == "evt_123"

    @pytest.mark.asyncio
    async def test_log_with_failure_status(self, audit_service, mock_db):
        """log() should handle failure status correctly."""
        result = await audit_service.log(
            db=mock_db,
            event_type="analysis.triggered",
            status=AuditStatus.FAILURE,
            metadata={"error": "Rate limit exceeded"},
        )

        added_log = mock_db.add.call_args[0][0]
        assert added_log.status == AuditStatus.FAILURE
        assert added_log.event_metadata["error"] == "Rate limit exceeded"

    @pytest.mark.asyncio
    async def test_log_sets_timestamp_automatically(self, audit_service, mock_db):
        """log() should set timestamp to current UTC time."""
        before = datetime.now(timezone.utc)

        await audit_service.log(
            db=mock_db,
            event_type="test.event",
        )

        after = datetime.now(timezone.utc)

        added_log = mock_db.add.call_args[0][0]
        assert added_log.timestamp >= before
        assert added_log.timestamp <= after


# ============================================================================
# AuditService.log_from_request() Tests
# ============================================================================


class TestAuditServiceLogFromRequest:
    """Tests for AuditService.log_from_request() method."""

    @pytest.mark.asyncio
    async def test_extracts_actor_from_request_state(
        self, audit_service, mock_db, mock_request
    ):
        """log_from_request() should extract actor info from request.state."""
        await audit_service.log_from_request(
            db=mock_db,
            request=mock_request,
            event_type="repo.connected",
        )

        added_log = mock_db.add.call_args[0][0]
        assert added_log.actor_id == mock_request.state.user.id
        assert added_log.actor_email == "user@example.com"
        assert added_log.organization_id == mock_request.state.organization.id

    @pytest.mark.asyncio
    async def test_extracts_ip_from_x_forwarded_for(
        self, audit_service, mock_db, mock_request
    ):
        """log_from_request() should use X-Forwarded-For header for IP."""
        await audit_service.log_from_request(
            db=mock_db,
            request=mock_request,
            event_type="test.event",
        )

        added_log = mock_db.add.call_args[0][0]
        # Should use first IP from X-Forwarded-For
        assert added_log.actor_ip == "203.0.113.45"

    @pytest.mark.asyncio
    async def test_extracts_ip_from_x_real_ip(self, audit_service, mock_db):
        """log_from_request() should fall back to X-Real-IP header."""
        request = MagicMock()
        request.headers = {"x-real-ip": "10.0.0.5"}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"
        request.state = MagicMock()
        request.state.user = None
        request.state.organization = None

        await audit_service.log_from_request(
            db=mock_db,
            request=request,
            event_type="test.event",
        )

        added_log = mock_db.add.call_args[0][0]
        assert added_log.actor_ip == "10.0.0.5"

    @pytest.mark.asyncio
    async def test_extracts_ip_from_client_host(self, audit_service, mock_db):
        """log_from_request() should fall back to client.host."""
        request = MagicMock()
        request.headers = {}
        request.client = MagicMock()
        request.client.host = "192.168.1.100"
        request.state = MagicMock()
        request.state.user = None
        request.state.organization = None

        await audit_service.log_from_request(
            db=mock_db,
            request=request,
            event_type="test.event",
        )

        added_log = mock_db.add.call_args[0][0]
        assert added_log.actor_ip == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_extracts_user_agent(self, audit_service, mock_db, mock_request):
        """log_from_request() should extract user agent header."""
        await audit_service.log_from_request(
            db=mock_db,
            request=mock_request,
            event_type="test.event",
        )

        added_log = mock_db.add.call_args[0][0]
        assert added_log.actor_user_agent == "Mozilla/5.0 Test Browser"


# ============================================================================
# AuditService.log_clerk_event() Tests
# ============================================================================


class TestAuditServiceLogClerkEvent:
    """Tests for AuditService.log_clerk_event() method."""

    @pytest.mark.asyncio
    async def test_maps_user_created_event(self, audit_service, mock_db):
        """log_clerk_event() should map user.created to user.signup."""
        # Mock no existing event
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        data = {
            "id": "user_123",
            "email_addresses": [
                {"id": "email_1", "email_address": "new@example.com"}
            ],
            "primary_email_address_id": "email_1",
            "first_name": "John",
            "last_name": "Doe",
        }

        await audit_service.log_clerk_event(
            db=mock_db,
            clerk_event_type="user.created",
            data=data,
            svix_id="svix_123",
        )

        added_log = mock_db.add.call_args[0][0]
        assert added_log.event_type == "user.signup"
        assert added_log.event_source == EventSource.CLERK
        assert added_log.resource_type == "user"
        assert added_log.resource_id == "user_123"
        assert added_log.action == "created"
        assert added_log.actor_email == "new@example.com"
        assert added_log.clerk_event_id == "svix_123"
        assert added_log.event_metadata["name"] == "John Doe"

    @pytest.mark.asyncio
    async def test_maps_session_created_event(self, audit_service, mock_db):
        """log_clerk_event() should map session.created to user.login."""
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        data = {
            "id": "sess_123",
            "user_id": "user_456",
            "created_at": 1699999999,
        }

        await audit_service.log_clerk_event(
            db=mock_db,
            clerk_event_type="session.created",
            data=data,
            svix_id="svix_456",
        )

        added_log = mock_db.add.call_args[0][0]
        assert added_log.event_type == "user.login"
        assert added_log.resource_type == "session"
        assert added_log.resource_id == "sess_123"
        assert added_log.action == "created"

    @pytest.mark.asyncio
    async def test_maps_organization_membership_created(self, audit_service, mock_db):
        """log_clerk_event() should map organizationMembership.created correctly."""
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        data = {
            "id": "orgmem_123",
            "organization": {"id": "org_456", "name": "Acme Inc"},
            "public_user_data": {"email_address": "member@example.com"},
        }

        await audit_service.log_clerk_event(
            db=mock_db,
            clerk_event_type="organizationMembership.created",
            data=data,
            svix_id="svix_789",
        )

        added_log = mock_db.add.call_args[0][0]
        assert added_log.event_type == "org.member_added"
        assert added_log.resource_type == "membership"
        assert added_log.resource_id == "orgmem_123"
        assert added_log.action == "created"
        assert added_log.actor_email == "member@example.com"
        assert added_log.event_metadata["organization_name"] == "Acme Inc"
        assert added_log.event_metadata["clerk_org_id"] == "org_456"

    @pytest.mark.asyncio
    async def test_deduplicates_by_svix_id(self, audit_service, mock_db):
        """log_clerk_event() should skip duplicate events."""
        # Mock existing event found
        mock_db.execute.return_value.scalar_one_or_none.return_value = AuditLog()

        result = await audit_service.log_clerk_event(
            db=mock_db,
            clerk_event_type="user.created",
            data={"id": "user_123"},
            svix_id="svix_duplicate",
        )

        # Should return None and not add a new log
        assert result is None
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_unmapped_event_type(self, audit_service, mock_db):
        """log_clerk_event() should handle unmapped Clerk event types."""
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        await audit_service.log_clerk_event(
            db=mock_db,
            clerk_event_type="custom.unknown_event",
            data={"some": "data"},
        )

        added_log = mock_db.add.call_args[0][0]
        # Should prefix with "clerk." for unmapped events
        assert added_log.event_type == "clerk.custom.unknown_event"


# ============================================================================
# AuditService.query() Tests
# ============================================================================


class TestAuditServiceQuery:
    """Tests for AuditService.query() method."""

    @pytest.mark.asyncio
    async def test_query_with_organization_filter(
        self, audit_service, mock_db, sample_org_id
    ):
        """query() should filter by organization_id."""
        mock_db.execute.return_value.scalar.return_value = 5
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        await audit_service.query(
            db=mock_db,
            organization_id=sample_org_id,
        )

        # Verify execute was called (query was built)
        assert mock_db.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_query_with_date_range(self, audit_service, mock_db):
        """query() should filter by date range."""
        mock_db.execute.return_value.scalar.return_value = 0
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        start = datetime.now(timezone.utc) - timedelta(days=7)
        end = datetime.now(timezone.utc)

        logs, total = await audit_service.query(
            db=mock_db,
            start_date=start,
            end_date=end,
        )

        assert mock_db.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_query_returns_pagination_info(self, audit_service, mock_db):
        """query() should return logs and total count."""
        mock_db.execute.return_value.scalar.return_value = 100
        mock_db.execute.return_value.scalars.return_value.all.return_value = [
            AuditLog() for _ in range(10)
        ]

        logs, total = await audit_service.query(
            db=mock_db,
            limit=10,
            offset=0,
        )

        assert len(logs) == 10
        assert total == 100

    @pytest.mark.asyncio
    async def test_query_with_event_type_filter(self, audit_service, mock_db):
        """query() should filter by event type."""
        mock_db.execute.return_value.scalar.return_value = 0
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        await audit_service.query(
            db=mock_db,
            event_type="user.login",
        )

        assert mock_db.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_query_with_multiple_event_types(self, audit_service, mock_db):
        """query() should filter by multiple event types."""
        mock_db.execute.return_value.scalar.return_value = 0
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        await audit_service.query(
            db=mock_db,
            event_types=["user.login", "user.logout", "user.signup"],
        )

        assert mock_db.execute.call_count >= 1


# ============================================================================
# IP Address Extraction Tests
# ============================================================================


class TestExtractClientIP:
    """Tests for client IP extraction."""

    def test_extract_ip_from_forwarded_for_single(self, audit_service):
        """Should extract single IP from X-Forwarded-For."""
        request = MagicMock()
        request.headers = {"x-forwarded-for": "203.0.113.45"}
        request.client = None

        ip = audit_service._extract_client_ip(request)
        assert ip == "203.0.113.45"

    def test_extract_ip_from_forwarded_for_chain(self, audit_service):
        """Should extract first IP from X-Forwarded-For chain."""
        request = MagicMock()
        request.headers = {"x-forwarded-for": "203.0.113.45, 10.0.0.1, 10.0.0.2"}
        request.client = None

        ip = audit_service._extract_client_ip(request)
        assert ip == "203.0.113.45"

    def test_extract_ip_from_real_ip(self, audit_service):
        """Should fall back to X-Real-IP."""
        request = MagicMock()
        request.headers = {"x-real-ip": "10.0.0.5"}
        request.client = None

        ip = audit_service._extract_client_ip(request)
        assert ip == "10.0.0.5"

    def test_extract_ip_from_client_host(self, audit_service):
        """Should fall back to client.host."""
        request = MagicMock()
        request.headers = {}
        request.client = MagicMock()
        request.client.host = "192.168.1.100"

        ip = audit_service._extract_client_ip(request)
        assert ip == "192.168.1.100"

    def test_extract_ip_returns_none_when_unavailable(self, audit_service):
        """Should return None when no IP is available."""
        request = MagicMock()
        request.headers = {}
        request.client = None

        ip = audit_service._extract_client_ip(request)
        assert ip is None


# ============================================================================
# Clerk Event Mapping Tests
# ============================================================================


class TestClerkEventMapping:
    """Tests for Clerk event type mapping."""

    def test_all_expected_events_are_mapped(self):
        """All documented Clerk events should have mappings."""
        from repotoire.services.audit import CLERK_EVENT_MAPPING

        expected_events = [
            "user.created",
            "user.updated",
            "user.deleted",
            "session.created",
            "session.ended",
            "session.revoked",
            "organization.created",
            "organization.updated",
            "organization.deleted",
            "organizationMembership.created",
            "organizationMembership.updated",
            "organizationMembership.deleted",
            "organizationInvitation.created",
            "organizationInvitation.accepted",
            "organizationInvitation.revoked",
        ]

        for event in expected_events:
            assert event in CLERK_EVENT_MAPPING, f"Missing mapping for {event}"

    def test_mapping_format(self):
        """Each mapping should have (event_type, resource_type, action)."""
        from repotoire.services.audit import CLERK_EVENT_MAPPING

        for clerk_event, mapping in CLERK_EVENT_MAPPING.items():
            assert len(mapping) == 3, f"Invalid mapping for {clerk_event}"
            event_type, resource_type, action = mapping
            assert isinstance(event_type, str), f"event_type must be str for {clerk_event}"
            assert resource_type is None or isinstance(resource_type, str)
            assert action is None or isinstance(action, str)


# ============================================================================
# Singleton Pattern Tests
# ============================================================================


class TestAuditServiceSingleton:
    """Tests for get_audit_service() singleton pattern."""

    def test_returns_same_instance(self):
        """get_audit_service() should return the same instance."""
        from repotoire.services.audit import get_audit_service

        service1 = get_audit_service()
        service2 = get_audit_service()

        assert service1 is service2

    def test_returns_audit_service_instance(self):
        """get_audit_service() should return AuditService type."""
        from repotoire.services.audit import AuditService, get_audit_service

        service = get_audit_service()
        assert isinstance(service, AuditService)
