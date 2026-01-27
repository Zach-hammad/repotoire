"""Unit tests for tenant-aware logging utilities.

Tests the structured logging with automatic tenant context inclusion.

REPO-600: Multi-tenant data isolation implementation.
"""

import logging
import pytest
from unittest.mock import MagicMock, patch, call
from uuid import uuid4

from repotoire.tenant.context import (
    clear_tenant_context,
    set_tenant_context,
    reset_tenant_context,
)
from repotoire.tenant.logging import (
    get_tenant_log_context,
    log_with_tenant,
    log_tenant_operation,
    TenantLogger,
)


class TestGetTenantLogContext:
    """Tests for get_tenant_log_context function."""

    def setup_method(self):
        """Clear context before each test."""
        clear_tenant_context()

    def teardown_method(self):
        """Clean up after each test."""
        clear_tenant_context()

    def test_returns_none_values_when_no_context(self):
        """Test that None values are returned when no context is set."""
        log_ctx = get_tenant_log_context()

        assert log_ctx["tenant_id"] is None
        assert log_ctx["tenant_slug"] is None
        assert log_ctx["user_id"] is None
        assert log_ctx["request_id"] is None

    def test_returns_context_values_when_set(self):
        """Test that context values are returned when context is set."""
        org_id = uuid4()
        token = set_tenant_context(
            org_id=org_id,
            org_slug="test-org",
            user_id="user-123",
            request_id="req-abc",
        )

        try:
            log_ctx = get_tenant_log_context()

            assert log_ctx["tenant_id"] == str(org_id)
            assert log_ctx["tenant_slug"] == "test-org"
            assert log_ctx["user_id"] == "user-123"
            assert log_ctx["request_id"] == "req-abc"
        finally:
            reset_tenant_context(token)

    def test_returns_partial_context(self):
        """Test that partial context values are returned."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id)

        try:
            log_ctx = get_tenant_log_context()

            assert log_ctx["tenant_id"] == str(org_id)
            # These should not be in the dict (not set)
            assert "tenant_slug" not in log_ctx or log_ctx.get("tenant_slug") is None
        finally:
            reset_tenant_context(token)


class TestLogWithTenant:
    """Tests for log_with_tenant function."""

    def setup_method(self):
        """Clear context before each test."""
        clear_tenant_context()

    def teardown_method(self):
        """Clean up after each test."""
        clear_tenant_context()

    def test_logs_with_tenant_context(self):
        """Test that logging includes tenant context."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id, org_slug="test-org")

        mock_logger = MagicMock()

        try:
            log_with_tenant(mock_logger, logging.INFO, "Test message")

            mock_logger.log.assert_called_once()
            call_args = mock_logger.log.call_args

            assert call_args[0][0] == logging.INFO
            assert call_args[0][1] == "Test message"
            assert "extra" in call_args[1]
            assert call_args[1]["extra"]["tenant_id"] == str(org_id)
            assert call_args[1]["extra"]["tenant_slug"] == "test-org"
        finally:
            reset_tenant_context(token)

    def test_logs_with_extra_fields(self):
        """Test that extra fields are included in log."""
        mock_logger = MagicMock()

        log_with_tenant(
            mock_logger,
            logging.WARNING,
            "Test message",
            custom_field="custom_value",
            another_field=123,
        )

        call_args = mock_logger.log.call_args
        extra = call_args[1]["extra"]

        assert extra["custom_field"] == "custom_value"
        assert extra["another_field"] == 123

    def test_logs_without_tenant_context(self):
        """Test that logging works without tenant context."""
        mock_logger = MagicMock()

        log_with_tenant(mock_logger, logging.DEBUG, "No tenant context")

        mock_logger.log.assert_called_once()
        call_args = mock_logger.log.call_args
        extra = call_args[1]["extra"]

        assert extra["tenant_id"] is None


class TestLogTenantOperation:
    """Tests for log_tenant_operation function."""

    def setup_method(self):
        """Clear context before each test."""
        clear_tenant_context()

    def teardown_method(self):
        """Clean up after each test."""
        clear_tenant_context()

    def test_logs_successful_operation(self):
        """Test logging a successful operation."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id, org_slug="test-org")
        mock_logger = MagicMock()

        try:
            log_tenant_operation(
                mock_logger,
                "ingest",
                success=True,
                files_processed=100,
            )

            mock_logger.log.assert_called_once()
            call_args = mock_logger.log.call_args

            # Success logs at INFO level
            assert call_args[0][0] == logging.INFO
            assert "ingest" in call_args[0][1]
            assert "completed" in call_args[0][1]

            extra = call_args[1]["extra"]
            assert extra["operation"] == "ingest"
            assert extra["success"] is True
            assert extra["tenant_id"] == str(org_id)
            assert extra["files_processed"] == 100
        finally:
            reset_tenant_context(token)

    def test_logs_failed_operation(self):
        """Test logging a failed operation."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id, org_slug="test-org")
        mock_logger = MagicMock()

        try:
            log_tenant_operation(
                mock_logger,
                "analyze",
                success=False,
                error="Connection timeout",
            )

            call_args = mock_logger.log.call_args

            # Failure logs at WARNING level
            assert call_args[0][0] == logging.WARNING
            assert "analyze" in call_args[0][1]
            assert "failed" in call_args[0][1]

            extra = call_args[1]["extra"]
            assert extra["success"] is False
            assert extra["error"] == "Connection timeout"
        finally:
            reset_tenant_context(token)

    def test_logs_without_context(self):
        """Test logging operation without tenant context."""
        mock_logger = MagicMock()

        log_tenant_operation(mock_logger, "query", success=True)

        call_args = mock_logger.log.call_args
        extra = call_args[1]["extra"]

        assert extra["tenant_id"] is None
        assert extra["tenant_slug"] is None


class TestTenantLogger:
    """Tests for TenantLogger wrapper class."""

    def setup_method(self):
        """Clear context before each test."""
        clear_tenant_context()

    def teardown_method(self):
        """Clean up after each test."""
        clear_tenant_context()

    def test_info_includes_tenant_context(self):
        """Test that info() includes tenant context."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id, org_slug="test-org")

        with patch("logging.getLogger") as mock_get_logger:
            mock_internal_logger = MagicMock()
            mock_get_logger.return_value = mock_internal_logger

            logger = TenantLogger("test.module")

            try:
                logger.info("Test info message")

                mock_internal_logger.log.assert_called_once()
                call_args = mock_internal_logger.log.call_args

                assert call_args[0][0] == logging.INFO
                assert call_args[0][1] == "Test info message"
                assert call_args[1]["extra"]["tenant_id"] == str(org_id)
            finally:
                reset_tenant_context(token)

    def test_debug_includes_tenant_context(self):
        """Test that debug() includes tenant context."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id)

        with patch("logging.getLogger") as mock_get_logger:
            mock_internal_logger = MagicMock()
            mock_get_logger.return_value = mock_internal_logger

            logger = TenantLogger("test.module")

            try:
                logger.debug("Debug message")

                call_args = mock_internal_logger.log.call_args
                assert call_args[0][0] == logging.DEBUG
            finally:
                reset_tenant_context(token)

    def test_warning_includes_tenant_context(self):
        """Test that warning() includes tenant context."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id)

        with patch("logging.getLogger") as mock_get_logger:
            mock_internal_logger = MagicMock()
            mock_get_logger.return_value = mock_internal_logger

            logger = TenantLogger("test.module")

            try:
                logger.warning("Warning message")

                call_args = mock_internal_logger.log.call_args
                assert call_args[0][0] == logging.WARNING
            finally:
                reset_tenant_context(token)

    def test_error_includes_tenant_context(self):
        """Test that error() includes tenant context."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id)

        with patch("logging.getLogger") as mock_get_logger:
            mock_internal_logger = MagicMock()
            mock_get_logger.return_value = mock_internal_logger

            logger = TenantLogger("test.module")

            try:
                logger.error("Error message")

                call_args = mock_internal_logger.log.call_args
                assert call_args[0][0] == logging.ERROR
            finally:
                reset_tenant_context(token)

    def test_critical_includes_tenant_context(self):
        """Test that critical() includes tenant context."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id)

        with patch("logging.getLogger") as mock_get_logger:
            mock_internal_logger = MagicMock()
            mock_get_logger.return_value = mock_internal_logger

            logger = TenantLogger("test.module")

            try:
                logger.critical("Critical message")

                call_args = mock_internal_logger.log.call_args
                assert call_args[0][0] == logging.CRITICAL
            finally:
                reset_tenant_context(token)

    def test_exception_includes_tenant_context(self):
        """Test that exception() includes tenant context."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id, org_slug="test-org")

        with patch("logging.getLogger") as mock_get_logger:
            mock_internal_logger = MagicMock()
            mock_get_logger.return_value = mock_internal_logger

            logger = TenantLogger("test.module")

            try:
                logger.exception("Exception message")

                mock_internal_logger.exception.assert_called_once()
                call_args = mock_internal_logger.exception.call_args

                assert call_args[0][0] == "Exception message"
                assert call_args[1]["extra"]["tenant_id"] == str(org_id)
            finally:
                reset_tenant_context(token)

    def test_extra_fields_merged(self):
        """Test that extra fields are merged with tenant context."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id)

        with patch("logging.getLogger") as mock_get_logger:
            mock_internal_logger = MagicMock()
            mock_get_logger.return_value = mock_internal_logger

            logger = TenantLogger("test.module")

            try:
                logger.info("Message", extra={"custom": "value"})

                call_args = mock_internal_logger.log.call_args
                extra = call_args[1]["extra"]

                # Both tenant context and custom field present
                assert extra["tenant_id"] == str(org_id)
                assert extra["custom"] == "value"
            finally:
                reset_tenant_context(token)

    def test_works_without_tenant_context(self):
        """Test that TenantLogger works without tenant context."""
        with patch("logging.getLogger") as mock_get_logger:
            mock_internal_logger = MagicMock()
            mock_get_logger.return_value = mock_internal_logger

            logger = TenantLogger("test.module")
            logger.info("No context message")

            call_args = mock_internal_logger.log.call_args
            extra = call_args[1]["extra"]

            assert extra["tenant_id"] is None
