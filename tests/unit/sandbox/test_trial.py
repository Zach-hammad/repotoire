"""Unit tests for sandbox trial management and usage limits."""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import pytest

from repotoire.sandbox.trial import (
    TrialManager,
    TrialStatus,
    TrialLimitExceeded,
    TIER_EXECUTION_LIMITS,
    get_trial_manager,
    check_trial_limit,
)
from repotoire.sandbox.config import DEFAULT_TRIAL_EXECUTIONS


class TestTrialStatus:
    """Tests for TrialStatus dataclass."""

    def test_trial_status_creation(self):
        """Test basic TrialStatus creation."""
        status = TrialStatus(
            customer_id="cust_123",
            executions_used=10,
            executions_limit=50,
            is_trial=True,
            is_exceeded=False,
        )

        assert status.customer_id == "cust_123"
        assert status.executions_used == 10
        assert status.executions_limit == 50
        assert status.is_trial is True
        assert status.is_exceeded is False

    def test_executions_remaining(self):
        """Test executions_remaining property."""
        status = TrialStatus(
            customer_id="cust_123",
            executions_used=30,
            executions_limit=50,
            is_trial=True,
            is_exceeded=False,
        )

        assert status.executions_remaining == 20

    def test_executions_remaining_exceeded(self):
        """Test executions_remaining when limit exceeded."""
        status = TrialStatus(
            customer_id="cust_123",
            executions_used=60,
            executions_limit=50,
            is_trial=True,
            is_exceeded=True,
        )

        # Should not go negative
        assert status.executions_remaining == 0

    def test_usage_percentage(self):
        """Test usage_percentage property."""
        status = TrialStatus(
            customer_id="cust_123",
            executions_used=25,
            executions_limit=50,
            is_trial=True,
            is_exceeded=False,
        )

        assert status.usage_percentage == 50.0

    def test_usage_percentage_zero_limit(self):
        """Test usage_percentage with zero limit."""
        status = TrialStatus(
            customer_id="cust_123",
            executions_used=10,
            executions_limit=0,
            is_trial=True,
            is_exceeded=True,
        )

        assert status.usage_percentage == 100.0

    def test_to_dict(self):
        """Test to_dict method."""
        status = TrialStatus(
            customer_id="cust_123",
            executions_used=25,
            executions_limit=50,
            is_trial=True,
            is_exceeded=False,
            subscription_tier="trial",
        )

        result = status.to_dict()

        assert result["customer_id"] == "cust_123"
        assert result["executions_used"] == 25
        assert result["executions_limit"] == 50
        assert result["executions_remaining"] == 25
        assert result["usage_percentage"] == 50.0
        assert result["is_trial"] is True
        assert result["is_exceeded"] is False
        assert result["subscription_tier"] == "trial"


class TestTrialLimitExceeded:
    """Tests for TrialLimitExceeded exception."""

    def test_exception_creation(self):
        """Test basic exception creation."""
        exc = TrialLimitExceeded(
            "Trial limit exceeded",
            customer_id="cust_123",
            used=50,
            limit=50,
        )

        assert "Trial limit exceeded" in str(exc)
        assert exc.customer_id == "cust_123"
        assert exc.used == 50
        assert exc.limit == 50
        assert "repotoire.dev/pricing" in exc.upgrade_url

    def test_exception_defaults(self):
        """Test exception with default values."""
        exc = TrialLimitExceeded("Limit exceeded")

        assert exc.customer_id is None
        assert exc.used == 0
        assert exc.limit == DEFAULT_TRIAL_EXECUTIONS

    def test_exception_is_sandbox_error(self):
        """Test that exception inherits from SandboxError."""
        from repotoire.sandbox.exceptions import SandboxError

        exc = TrialLimitExceeded("Test")
        assert isinstance(exc, SandboxError)


class TestTierExecutionLimits:
    """Tests for tier execution limits."""

    def test_trial_limit(self):
        """Test trial tier limit."""
        assert TIER_EXECUTION_LIMITS["trial"] == DEFAULT_TRIAL_EXECUTIONS
        assert TIER_EXECUTION_LIMITS["trial"] == 50

    def test_no_free_tier(self):
        """Test that free tier does not exist (Option A: trial → paid)."""
        assert "free" not in TIER_EXECUTION_LIMITS

    def test_pro_limit(self):
        """Test pro tier limit ($49/mo)."""
        assert TIER_EXECUTION_LIMITS["pro"] == 5000

    def test_enterprise_unlimited(self):
        """Test enterprise tier is unlimited."""
        assert TIER_EXECUTION_LIMITS["enterprise"] == -1

    def test_only_three_tiers(self):
        """Test only trial, pro, enterprise tiers exist."""
        assert set(TIER_EXECUTION_LIMITS.keys()) == {"trial", "pro", "enterprise"}


class TestTrialManager:
    """Tests for TrialManager class."""

    @pytest.fixture
    def manager(self):
        """Create a TrialManager without DB connection."""
        return TrialManager(connection_string=None)

    @pytest.fixture
    def mock_db_manager(self):
        """Create a TrialManager with mocked DB."""
        manager = TrialManager(connection_string="postgresql://test")
        manager._connected = True
        manager._conn = MagicMock()
        return manager

    def test_init_no_connection_string(self, manager):
        """Test initialization without connection string."""
        assert manager.connection_string is None
        assert manager._connected is False

    def test_init_with_connection_string(self):
        """Test initialization with connection string."""
        manager = TrialManager(connection_string="postgresql://localhost/test")
        assert manager.connection_string == "postgresql://localhost/test"
        assert manager._connected is False

    @pytest.mark.asyncio
    async def test_get_trial_status_no_db(self, manager):
        """Test get_trial_status without DB returns permissive default."""
        status = await manager.get_trial_status("cust_123")

        assert status.customer_id == "cust_123"
        assert status.executions_used == 0
        assert status.executions_limit == DEFAULT_TRIAL_EXECUTIONS
        assert status.is_trial is True
        assert status.is_exceeded is False

    @pytest.mark.asyncio
    async def test_check_can_execute_no_db(self, manager):
        """Test check_can_execute without DB allows execution."""
        can_execute, message = await manager.check_can_execute("cust_123")

        assert can_execute is True
        assert "remaining" in message

    @pytest.mark.asyncio
    async def test_increment_usage_no_db(self, manager):
        """Test increment_usage without DB returns 0."""
        count = await manager.increment_usage("cust_123")
        assert count == 0

    @pytest.mark.asyncio
    async def test_get_trial_status_with_db(self, mock_db_manager):
        """Test get_trial_status with mocked DB."""
        # Mock cursor and query result
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (25, "trial", None)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_db_manager._conn.cursor.return_value = mock_cursor

        # Run in executor returns the result directly for our mock
        with patch.object(asyncio, 'get_event_loop') as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(
                return_value=(25, "trial", None)
            )

            status = await mock_db_manager.get_trial_status("cust_123")

        assert status.customer_id == "cust_123"
        assert status.executions_used == 25
        assert status.is_trial is True

    @pytest.mark.asyncio
    async def test_check_can_execute_exceeded(self, mock_db_manager):
        """Test check_can_execute when limit exceeded."""
        # Mock status to show exceeded
        with patch.object(mock_db_manager, 'get_trial_status') as mock_status:
            mock_status.return_value = TrialStatus(
                customer_id="cust_123",
                executions_used=50,
                executions_limit=50,
                is_trial=True,
                is_exceeded=True,
            )

            can_execute, message = await mock_db_manager.check_can_execute("cust_123")

        assert can_execute is False
        assert "exceeded" in message.lower()
        assert "upgrade" in message.lower()

    @pytest.mark.asyncio
    async def test_check_can_execute_within_limit(self, mock_db_manager):
        """Test check_can_execute when within limit."""
        with patch.object(mock_db_manager, 'get_trial_status') as mock_status:
            mock_status.return_value = TrialStatus(
                customer_id="cust_123",
                executions_used=25,
                executions_limit=50,
                is_trial=True,
                is_exceeded=False,
            )

            can_execute, message = await mock_db_manager.check_can_execute("cust_123")

        assert can_execute is True
        assert "25 executions remaining" in message

    @pytest.mark.asyncio
    async def test_check_can_execute_warning_at_80_percent(self, mock_db_manager):
        """Test warning is logged when usage reaches 80%."""
        with patch.object(mock_db_manager, 'get_trial_status') as mock_status:
            mock_status.return_value = TrialStatus(
                customer_id="cust_123",
                executions_used=42,  # 84% of 50
                executions_limit=50,
                is_trial=True,
                is_exceeded=False,
            )

            with patch('repotoire.sandbox.trial.logger') as mock_logger:
                can_execute, message = await mock_db_manager.check_can_execute("cust_123")

                # Should log warning
                mock_logger.warning.assert_called_once()
                assert "approaching limit" in mock_logger.warning.call_args[0][0]

    @pytest.mark.asyncio
    async def test_increment_usage_with_db(self, mock_db_manager):
        """Test increment_usage with mocked DB."""
        with patch.object(asyncio, 'get_event_loop') as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=26)

            count = await mock_db_manager.increment_usage("cust_123")

        assert count == 26

    @pytest.mark.asyncio
    async def test_upgrade_tier(self, mock_db_manager):
        """Test upgrade_tier updates subscription."""
        with patch.object(asyncio, 'get_event_loop') as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)

            await mock_db_manager.upgrade_tier("cust_123", "pro")

            # Should have called run_in_executor
            mock_loop.return_value.run_in_executor.assert_called_once()

    @pytest.mark.asyncio
    async def test_upgrade_tier_invalid(self, mock_db_manager):
        """Test upgrade_tier with invalid tier."""
        with pytest.raises(ValueError, match="Invalid tier"):
            await mock_db_manager.upgrade_tier("cust_123", "invalid_tier")

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async context manager."""
        manager = TrialManager(connection_string=None)

        async with manager as m:
            assert m is manager

        # Should not raise even without connection

    @pytest.mark.asyncio
    async def test_connect_missing_psycopg2(self):
        """Test connect raises ImportError if psycopg2 missing."""
        manager = TrialManager(connection_string="postgresql://test")

        with patch.dict('sys.modules', {'psycopg2': None}):
            with patch('builtins.__import__', side_effect=ImportError("No module")):
                with pytest.raises(ImportError, match="psycopg2-binary"):
                    await manager.connect()


class TestCheckTrialLimitDecorator:
    """Tests for check_trial_limit decorator."""

    @pytest.mark.asyncio
    async def test_decorator_allows_execution(self):
        """Test decorator allows execution when within limit."""
        mock_manager = MagicMock()
        mock_manager._connected = True
        mock_manager.check_can_execute = AsyncMock(return_value=(True, "OK"))
        mock_manager.increment_usage = AsyncMock(return_value=1)

        with patch('repotoire.sandbox.trial.get_trial_manager', return_value=mock_manager):
            @check_trial_limit
            async def my_func(customer_id: str):
                return "success"

            result = await my_func(customer_id="cust_123")

        assert result == "success"
        mock_manager.check_can_execute.assert_called_once_with("cust_123")
        mock_manager.increment_usage.assert_called_once_with("cust_123")

    @pytest.mark.asyncio
    async def test_decorator_blocks_execution(self):
        """Test decorator blocks execution when limit exceeded."""
        mock_manager = MagicMock()
        mock_manager._connected = True
        mock_manager.check_can_execute = AsyncMock(
            return_value=(False, "Trial limit exceeded")
        )
        mock_manager.get_trial_status = AsyncMock(
            return_value=TrialStatus(
                customer_id="cust_123",
                executions_used=50,
                executions_limit=50,
                is_trial=True,
                is_exceeded=True,
            )
        )

        with patch('repotoire.sandbox.trial.get_trial_manager', return_value=mock_manager):
            @check_trial_limit
            async def my_func(customer_id: str):
                return "success"

            with pytest.raises(TrialLimitExceeded) as exc_info:
                await my_func(customer_id="cust_123")

        assert exc_info.value.customer_id == "cust_123"
        assert exc_info.value.used == 50
        assert exc_info.value.limit == 50

    @pytest.mark.asyncio
    async def test_decorator_requires_customer_id(self):
        """Test decorator raises error if customer_id not provided."""
        @check_trial_limit
        async def my_func():
            return "success"

        with pytest.raises(ValueError, match="customer_id required"):
            await my_func()

    @pytest.mark.asyncio
    async def test_decorator_extracts_customer_id_from_args(self):
        """Test decorator extracts customer_id from positional args."""
        mock_manager = MagicMock()
        mock_manager._connected = True
        mock_manager.check_can_execute = AsyncMock(return_value=(True, "OK"))
        mock_manager.increment_usage = AsyncMock(return_value=1)

        with patch('repotoire.sandbox.trial.get_trial_manager', return_value=mock_manager):
            @check_trial_limit
            async def my_func(customer_id: str, code: str):
                return f"ran {code}"

            result = await my_func("cust_123", "print('hello')")

        assert result == "ran print('hello')"
        mock_manager.check_can_execute.assert_called_once_with("cust_123")

    @pytest.mark.asyncio
    async def test_decorator_connects_if_not_connected(self):
        """Test decorator connects manager if not connected."""
        mock_manager = MagicMock()
        mock_manager._connected = False
        mock_manager.connect = AsyncMock()
        mock_manager.check_can_execute = AsyncMock(return_value=(True, "OK"))
        mock_manager.increment_usage = AsyncMock(return_value=1)

        with patch('repotoire.sandbox.trial.get_trial_manager', return_value=mock_manager):
            @check_trial_limit
            async def my_func(customer_id: str):
                return "success"

            await my_func(customer_id="cust_123")

        mock_manager.connect.assert_called_once()


class TestGetTrialManager:
    """Tests for get_trial_manager function."""

    def test_returns_singleton(self):
        """Test get_trial_manager returns same instance."""
        # Reset global
        import repotoire.sandbox.trial as trial_module
        trial_module._global_manager = None

        manager1 = get_trial_manager()
        manager2 = get_trial_manager()

        assert manager1 is manager2

    def test_creates_manager_if_none(self):
        """Test get_trial_manager creates manager if none exists."""
        import repotoire.sandbox.trial as trial_module
        trial_module._global_manager = None

        manager = get_trial_manager()

        assert manager is not None
        assert isinstance(manager, TrialManager)


class TestTrialStatusEdgeCases:
    """Edge case tests for trial status calculations."""

    def test_pro_tier_status(self):
        """Test status for pro tier ($49/mo, 5000 executions)."""
        status = TrialStatus(
            customer_id="cust_123",
            executions_used=1000,
            executions_limit=5000,
            is_trial=False,
            is_exceeded=False,
            subscription_tier="pro",
        )

        assert status.executions_remaining == 4000
        assert status.usage_percentage == 20.0

    def test_enterprise_unlimited_status(self):
        """Test status for enterprise (unlimited) tier."""
        status = TrialStatus(
            customer_id="cust_123",
            executions_used=100000,
            executions_limit=999999,  # Represented as large number
            is_trial=False,
            is_exceeded=False,
            subscription_tier="enterprise",
        )

        assert status.executions_remaining == 899999
        assert status.is_exceeded is False

    def test_pro_tier_near_limit(self):
        """Test pro tier near monthly limit."""
        status = TrialStatus(
            customer_id="cust_123",
            executions_used=4000,
            executions_limit=5000,
            is_trial=False,
            is_exceeded=False,
            subscription_tier="pro",
        )

        assert status.executions_remaining == 1000
        assert status.usage_percentage == 80.0

    def test_unknown_tier_blocked(self):
        """Test that unknown tiers are blocked (limit=0)."""
        # This simulates legacy 'free' tier users or invalid tiers
        status = TrialStatus(
            customer_id="cust_123",
            executions_used=10,
            executions_limit=0,  # Unknown tier gets 0 limit
            is_trial=False,
            is_exceeded=True,
            subscription_tier="free",  # Legacy tier, no longer valid
        )

        assert status.executions_remaining == 0
        assert status.is_exceeded is True


class TestTrialIntegration:
    """Integration-style tests for trial workflow."""

    @pytest.mark.asyncio
    async def test_full_trial_workflow_no_db(self):
        """Test complete trial workflow without database."""
        manager = TrialManager(connection_string=None)

        # Check status
        status = await manager.get_trial_status("new_customer")
        assert status.is_trial is True
        assert status.executions_used == 0

        # Check can execute
        can_execute, msg = await manager.check_can_execute("new_customer")
        assert can_execute is True

        # Increment (no-op without DB)
        count = await manager.increment_usage("new_customer")
        assert count == 0

    @pytest.mark.asyncio
    async def test_exceeded_customer_blocked(self):
        """Test that exceeded customer is blocked."""
        manager = TrialManager(connection_string=None)

        # Mock the get_trial_status to return exceeded
        with patch.object(manager, 'get_trial_status') as mock_status:
            mock_status.return_value = TrialStatus(
                customer_id="heavy_user",
                executions_used=50,
                executions_limit=50,
                is_trial=True,
                is_exceeded=True,
            )

            can_execute, msg = await manager.check_can_execute("heavy_user")

            assert can_execute is False
            assert "upgrade" in msg.lower()
            assert "repotoire.dev/pricing" in msg
