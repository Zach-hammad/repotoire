"""Unit tests for TenantContext async-safe tenant propagation.

Tests the ContextVar-based tenant context system for multi-tenant isolation.
"""

import asyncio
import pytest
from uuid import uuid4, UUID

from repotoire.tenant.context import (
    TenantContext,
    TenantContextManager,
    set_tenant_context,
    set_tenant_context_from_obj,
    get_tenant_context,
    require_tenant_context,
    reset_tenant_context,
    clear_tenant_context,
    get_current_org_id,
    get_current_org_id_str,
)


class TestTenantContext:
    """Tests for TenantContext dataclass."""

    def test_tenant_context_creation(self):
        """Test creating a TenantContext."""
        org_id = uuid4()
        ctx = TenantContext(
            org_id=org_id,
            org_slug="acme-corp",
            user_id="user_123",
        )

        assert ctx.org_id == org_id
        assert ctx.org_slug == "acme-corp"
        assert ctx.user_id == "user_123"

    def test_tenant_context_is_immutable(self):
        """Test that TenantContext is immutable (frozen dataclass)."""
        ctx = TenantContext(org_id=uuid4())

        with pytest.raises(AttributeError):
            ctx.org_id = uuid4()

    def test_org_id_str_property(self):
        """Test org_id_str property returns string."""
        org_id = uuid4()
        ctx = TenantContext(org_id=org_id)

        assert ctx.org_id_str == str(org_id)
        assert isinstance(ctx.org_id_str, str)

    def test_with_request_id(self):
        """Test creating new context with different request_id."""
        ctx = TenantContext(
            org_id=uuid4(),
            org_slug="acme-corp",
            request_id="req-1",
        )

        new_ctx = ctx.with_request_id("req-2")

        assert new_ctx.request_id == "req-2"
        assert new_ctx.org_id == ctx.org_id
        assert new_ctx.org_slug == ctx.org_slug
        assert ctx.request_id == "req-1"  # Original unchanged

    def test_to_log_context(self):
        """Test conversion to log context dict."""
        org_id = uuid4()
        ctx = TenantContext(
            org_id=org_id,
            org_slug="acme-corp",
            user_id="user_123",
            request_id="req-abc",
        )

        log_ctx = ctx.to_log_context()

        assert log_ctx["tenant_id"] == str(org_id)
        assert log_ctx["tenant_slug"] == "acme-corp"
        assert log_ctx["user_id"] == "user_123"
        assert log_ctx["request_id"] == "req-abc"

    def test_to_log_context_minimal(self):
        """Test log context with only org_id."""
        org_id = uuid4()
        ctx = TenantContext(org_id=org_id)

        log_ctx = ctx.to_log_context()

        assert log_ctx["tenant_id"] == str(org_id)
        assert "tenant_slug" not in log_ctx
        assert "user_id" not in log_ctx


class TestTenantContextVar:
    """Tests for ContextVar operations."""

    def setup_method(self):
        """Clear context before each test."""
        clear_tenant_context()

    def teardown_method(self):
        """Clear context after each test."""
        clear_tenant_context()

    def test_set_and_get_context(self):
        """Test setting and getting tenant context."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id, org_slug="test-org")

        ctx = get_tenant_context()

        assert ctx is not None
        assert ctx.org_id == org_id
        assert ctx.org_slug == "test-org"

        reset_tenant_context(token)

    def test_get_context_when_not_set(self):
        """Test getting context when not set returns None."""
        ctx = get_tenant_context()
        assert ctx is None

    def test_require_context_when_set(self):
        """Test require_tenant_context returns context when set."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id)

        ctx = require_tenant_context()

        assert ctx.org_id == org_id

        reset_tenant_context(token)

    def test_require_context_when_not_set_raises(self):
        """Test require_tenant_context raises when not set."""
        with pytest.raises(RuntimeError) as exc_info:
            require_tenant_context()

        assert "Tenant context not set" in str(exc_info.value)

    def test_reset_context(self):
        """Test resetting context restores previous state."""
        # Set first context
        org1 = uuid4()
        token1 = set_tenant_context(org_id=org1, org_slug="org-1")

        # Set second context
        org2 = uuid4()
        token2 = set_tenant_context(org_id=org2, org_slug="org-2")

        ctx = get_tenant_context()
        assert ctx.org_id == org2

        # Reset to first context
        reset_tenant_context(token2)

        ctx = get_tenant_context()
        assert ctx.org_id == org1

        # Reset to no context
        reset_tenant_context(token1)

        ctx = get_tenant_context()
        assert ctx is None

    def test_clear_context(self):
        """Test clearing context sets to None."""
        org_id = uuid4()
        set_tenant_context(org_id=org_id)

        ctx = get_tenant_context()
        assert ctx is not None

        clear_tenant_context()

        ctx = get_tenant_context()
        assert ctx is None

    def test_set_context_from_obj(self):
        """Test setting context from existing TenantContext object."""
        ctx = TenantContext(
            org_id=uuid4(),
            org_slug="test-org",
            user_id="user-123",
        )

        token = set_tenant_context_from_obj(ctx)

        retrieved = get_tenant_context()
        assert retrieved is ctx

        reset_tenant_context(token)

    def test_convenience_functions(self):
        """Test get_current_org_id and get_current_org_id_str."""
        org_id = uuid4()
        token = set_tenant_context(org_id=org_id)

        assert get_current_org_id() == org_id
        assert get_current_org_id_str() == str(org_id)

        reset_tenant_context(token)

        assert get_current_org_id() is None
        assert get_current_org_id_str() is None


class TestTenantContextManager:
    """Tests for TenantContextManager."""

    def setup_method(self):
        """Clear context before each test."""
        clear_tenant_context()

    def teardown_method(self):
        """Clear context after each test."""
        clear_tenant_context()

    def test_sync_context_manager(self):
        """Test synchronous context manager."""
        org_id = uuid4()

        with TenantContextManager(org_id, "test-org") as ctx:
            assert ctx.org_id == org_id
            assert get_tenant_context() is ctx

        # Context should be cleared after exiting
        assert get_tenant_context() is None

    def test_async_context_manager(self):
        """Test async context manager."""
        org_id = uuid4()

        async def run_async():
            async with TenantContextManager(org_id, "test-org") as ctx:
                assert ctx.org_id == org_id
                assert get_tenant_context() is ctx

            # Context should be cleared after exiting
            assert get_tenant_context() is None

        asyncio.run(run_async())

    def test_context_manager_on_exception(self):
        """Test context is cleaned up even on exception."""
        org_id = uuid4()

        with pytest.raises(ValueError):
            with TenantContextManager(org_id, "test-org") as ctx:
                assert get_tenant_context() is ctx
                raise ValueError("Test error")

        # Context should still be cleared
        assert get_tenant_context() is None


class TestAsyncContextPropagation:
    """Tests for async context propagation across coroutines."""

    def setup_method(self):
        """Clear context before each test."""
        clear_tenant_context()

    def teardown_method(self):
        """Clear context after each test."""
        clear_tenant_context()

    def test_context_propagates_to_nested_async(self):
        """Test context propagates to nested async functions."""
        org_id = uuid4()

        async def nested_async():
            ctx = get_tenant_context()
            return ctx.org_id if ctx else None

        async def outer_async():
            return await nested_async()

        async def run_test():
            token = set_tenant_context(org_id=org_id)
            try:
                result = await outer_async()
                assert result == org_id
            finally:
                reset_tenant_context(token)

        asyncio.run(run_test())

    def test_context_isolated_between_tasks(self):
        """Test context is isolated between concurrent async tasks."""
        org1 = uuid4()
        org2 = uuid4()
        results = {}

        async def task(task_id: str, org_id: UUID):
            token = set_tenant_context(org_id=org_id, org_slug=f"org-{task_id}")
            try:
                await asyncio.sleep(0.01)  # Yield to other tasks
                ctx = get_tenant_context()
                results[task_id] = ctx.org_id if ctx else None
            finally:
                reset_tenant_context(token)

        async def run_test():
            await asyncio.gather(
                task("1", org1),
                task("2", org2),
            )

        asyncio.run(run_test())

        assert results["1"] == org1
        assert results["2"] == org2

    def test_context_preserved_after_await(self):
        """Test context is preserved after await points."""
        org_id = uuid4()

        async def check_context_after_await():
            token = set_tenant_context(org_id=org_id)
            try:
                # Multiple await points
                await asyncio.sleep(0.001)
                ctx1 = get_tenant_context()

                await asyncio.sleep(0.001)
                ctx2 = get_tenant_context()

                await asyncio.sleep(0.001)
                ctx3 = get_tenant_context()

                return ctx1, ctx2, ctx3
            finally:
                reset_tenant_context(token)

        ctx1, ctx2, ctx3 = asyncio.run(check_context_after_await())

        assert ctx1 is not None and ctx1.org_id == org_id
        assert ctx2 is not None and ctx2.org_id == org_id
        assert ctx3 is not None and ctx3.org_id == org_id
