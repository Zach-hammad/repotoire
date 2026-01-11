"""Circuit breaker pattern for external service resilience.

This module provides a circuit breaker implementation for protecting
against cascading failures when external services (OpenAI, Stripe, etc.)
become unavailable or slow.

The circuit breaker has three states:
- CLOSED: Normal operation, requests pass through
- OPEN: Service is failing, requests are rejected immediately
- HALF_OPEN: Testing if service has recovered

Usage:
    from repotoire.api.shared.services.circuit_breaker import circuit_breaker

    @circuit_breaker("openai", failure_threshold=5, recovery_timeout=30)
    async def call_openai_api():
        ...

    # Or use the class directly
    breaker = CircuitBreaker("stripe", failure_threshold=3)
    try:
        async with breaker:
            result = await stripe_api_call()
    except CircuitOpenError:
        # Handle service unavailable
        pass
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, Optional, TypeVar

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open."""

    def __init__(self, service_name: str, retry_after: float):
        self.service_name = service_name
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker open for {service_name}. Retry after {retry_after:.1f}s"
        )


@dataclass
class CircuitBreaker:
    """Circuit breaker for protecting external service calls.

    Attributes:
        name: Identifier for the service being protected
        failure_threshold: Number of failures before opening the circuit
        recovery_timeout: Seconds to wait before attempting recovery
        success_threshold: Number of successes in half-open before closing
        timeout: Optional timeout for individual calls
    """
    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    success_threshold: int = 2
    timeout: Optional[float] = None

    # Internal state
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: Optional[float] = field(default=None, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        """Get current circuit state, checking for recovery timeout."""
        if self._state == CircuitState.OPEN:
            if self._last_failure_time is not None:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info(
                        f"Circuit breaker {self.name} transitioning to HALF_OPEN",
                        extra={"service": self.name, "elapsed": elapsed}
                    )
        return self._state

    @property
    def is_closed(self) -> bool:
        """Check if the circuit is closed (normal operation)."""
        return self.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if the circuit is open (rejecting requests)."""
        return self.state == CircuitState.OPEN

    @property
    def time_until_retry(self) -> float:
        """Get seconds until circuit may transition to half-open."""
        if self._state != CircuitState.OPEN or self._last_failure_time is None:
            return 0.0
        elapsed = time.monotonic() - self._last_failure_time
        return max(0.0, self.recovery_timeout - elapsed)

    async def __aenter__(self) -> "CircuitBreaker":
        """Enter the circuit breaker context."""
        async with self._lock:
            if self.is_open:
                raise CircuitOpenError(self.name, self.time_until_retry)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Exit the circuit breaker context, recording success or failure."""
        if exc_type is None:
            await self.record_success()
        elif exc_type is not CircuitOpenError:
            await self.record_failure(exc_val)
        return False

    async def record_success(self) -> None:
        """Record a successful call."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    logger.info(
                        f"Circuit breaker {self.name} CLOSED after recovery",
                        extra={"service": self.name}
                    )
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    async def record_failure(self, error: Optional[Exception] = None) -> None:
        """Record a failed call."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # Immediate transition back to open on failure during recovery
                self._state = CircuitState.OPEN
                logger.warning(
                    f"Circuit breaker {self.name} re-OPENED during recovery",
                    extra={
                        "service": self.name,
                        "error": str(error) if error else None
                    }
                )
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    f"Circuit breaker {self.name} OPENED after {self._failure_count} failures",
                    extra={
                        "service": self.name,
                        "failure_count": self._failure_count,
                        "error": str(error) if error else None
                    }
                )

    def reset(self) -> None:
        """Reset the circuit breaker to closed state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None
        logger.info(f"Circuit breaker {self.name} manually reset")


# Global registry of circuit breakers
_circuit_breakers: Dict[str, CircuitBreaker] = {}
_registry_lock = asyncio.Lock()


async def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
    success_threshold: int = 2,
    timeout: Optional[float] = None,
) -> CircuitBreaker:
    """Get or create a circuit breaker for a service.

    This ensures only one circuit breaker exists per service name,
    so state is shared across all callers.
    """
    async with _registry_lock:
        if name not in _circuit_breakers:
            _circuit_breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                success_threshold=success_threshold,
                timeout=timeout,
            )
        return _circuit_breakers[name]


def get_circuit_breaker_sync(name: str) -> Optional[CircuitBreaker]:
    """Get an existing circuit breaker by name (sync version)."""
    return _circuit_breakers.get(name)


def get_all_circuit_breakers() -> Dict[str, CircuitBreaker]:
    """Get all registered circuit breakers."""
    return dict(_circuit_breakers)


def circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
    success_threshold: int = 2,
    timeout: Optional[float] = None,
) -> Callable:
    """Decorator to apply circuit breaker pattern to an async function.

    Usage:
        @circuit_breaker("openai", failure_threshold=5, recovery_timeout=30)
        async def call_openai():
            ...

    Args:
        name: Service name for the circuit breaker
        failure_threshold: Number of failures before opening
        recovery_timeout: Seconds before attempting recovery
        success_threshold: Successes needed to close after half-open
        timeout: Optional timeout for individual calls
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            breaker = await get_circuit_breaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                success_threshold=success_threshold,
                timeout=timeout,
            )

            async with breaker:
                if timeout:
                    return await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=timeout
                    )
                return await func(*args, **kwargs)

        return wrapper
    return decorator


# Pre-configured circuit breakers for common services
async def get_openai_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for OpenAI API calls."""
    return await get_circuit_breaker(
        "openai",
        failure_threshold=5,
        recovery_timeout=60.0,  # OpenAI rate limits often last ~60s
        success_threshold=2,
        timeout=30.0,  # API calls should complete in 30s
    )


async def get_stripe_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for Stripe API calls."""
    return await get_circuit_breaker(
        "stripe",
        failure_threshold=3,
        recovery_timeout=30.0,
        success_threshold=2,
        timeout=10.0,  # Stripe is usually fast
    )


async def get_github_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for GitHub API calls."""
    return await get_circuit_breaker(
        "github",
        failure_threshold=5,
        recovery_timeout=45.0,
        success_threshold=2,
        timeout=15.0,
    )


async def get_clerk_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for Clerk API calls."""
    return await get_circuit_breaker(
        "clerk",
        failure_threshold=5,
        recovery_timeout=30.0,
        success_threshold=2,
        timeout=10.0,
    )
