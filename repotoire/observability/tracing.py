"""OpenTelemetry distributed tracing for Repotoire (REPO-224).

Provides production-grade tracing for:
- Analysis pipeline execution
- Detector spans with timing
- RAG query traces
- Database operations

All tracing gracefully degrades when opentelemetry is not installed.

Usage:
    from repotoire.observability import init_tracing, traced, get_tracer

    # Initialize tracing (call once at startup)
    init_tracing(service_name="repotoire", endpoint="localhost:4317")

    # Use decorator for automatic spans
    @traced("my_operation")
    def my_function():
        pass

    # Manual spans
    tracer = get_tracer()
    with tracer.start_as_current_span("custom_span") as span:
        span.set_attribute("key", "value")
        do_work()

Install with: pip install repotoire[observability]
"""

import functools
from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, Optional

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Check for OpenTelemetry availability
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.trace import Span, Status, StatusCode
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    HAS_OPENTELEMETRY = True
except ImportError:
    HAS_OPENTELEMETRY = False
    trace = None  # type: ignore
    TracerProvider = None  # type: ignore
    BatchSpanProcessor = None  # type: ignore
    Resource = None  # type: ignore
    SERVICE_NAME = None  # type: ignore
    OTLPSpanExporter = None  # type: ignore
    Span = None  # type: ignore
    Status = None  # type: ignore
    StatusCode = None  # type: ignore


class NoOpSpan:
    """No-op span that does nothing when OpenTelemetry is not available."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_attributes(self, attributes: Dict[str, Any]) -> None:
        pass

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def __enter__(self) -> "NoOpSpan":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class NoOpTracer:
    """No-op tracer that does nothing when OpenTelemetry is not available."""

    def start_span(self, name: str, **kwargs: Any) -> NoOpSpan:
        return NoOpSpan()

    @contextmanager
    def start_as_current_span(
        self, name: str, **kwargs: Any
    ) -> Generator[NoOpSpan, None, None]:
        yield NoOpSpan()


class TracingManager:
    """Manager for OpenTelemetry tracing configuration.

    Example:
        tracing = TracingManager()
        tracing.init(service_name="repotoire", endpoint="localhost:4317")

        with tracing.span("analyze_codebase") as span:
            span.set_attribute("files", 1000)
            result = analyze()
    """

    def __init__(self, enabled: bool = True) -> None:
        """Initialize tracing manager.

        Args:
            enabled: Whether tracing is enabled
        """
        self.enabled = enabled and HAS_OPENTELEMETRY
        self._initialized = False
        self._tracer: Any = None

        if enabled and not HAS_OPENTELEMETRY:
            logger.info(
                "OpenTelemetry tracing disabled (opentelemetry not installed). "
                "Install with: pip install repotoire[observability]"
            )

    def init(
        self,
        service_name: str = "repotoire",
        endpoint: str = "localhost:4317",
        insecure: bool = True,
    ) -> bool:
        """Initialize OpenTelemetry tracing.

        Args:
            service_name: Name of the service in traces
            endpoint: OTLP collector endpoint (host:port)
            insecure: Whether to use insecure connection (no TLS)

        Returns:
            True if initialization succeeded
        """
        if not self.enabled:
            logger.warning("Tracing not initialized (tracing disabled)")
            return False

        if self._initialized:
            logger.warning("Tracing already initialized")
            return True

        try:
            # Create resource with service name
            resource = Resource.create({SERVICE_NAME: service_name})

            # Create tracer provider
            provider = TracerProvider(resource=resource)

            # Create OTLP exporter
            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)

            # Add batch processor for efficient export
            processor = BatchSpanProcessor(exporter)
            provider.add_span_processor(processor)

            # Set as global provider
            trace.set_tracer_provider(provider)

            # Get tracer
            self._tracer = trace.get_tracer(service_name)
            self._initialized = True

            logger.info(
                f"OpenTelemetry tracing initialized (service: {service_name}, "
                f"endpoint: {endpoint})"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to initialize tracing: {e}")
            return False

    def get_tracer(self) -> Any:
        """Get the configured tracer.

        Returns:
            OpenTelemetry tracer or NoOpTracer if not initialized
        """
        if not self.enabled or not self._initialized:
            return NoOpTracer()
        return self._tracer

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Generator[Any, None, None]:
        """Create a span context manager.

        Args:
            name: Name of the span
            attributes: Optional attributes to set on span

        Yields:
            Span object (or NoOpSpan if tracing disabled)
        """
        if not self.enabled or not self._initialized:
            yield NoOpSpan()
            return

        with self._tracer.start_as_current_span(name) as span:
            if attributes:
                span.set_attributes(attributes)
            try:
                yield span
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    def record_detector_span(
        self,
        detector_name: str,
        findings_count: int,
        duration_seconds: float,
    ) -> None:
        """Record a detector execution as a span event.

        Args:
            detector_name: Name of the detector
            findings_count: Number of findings
            duration_seconds: Execution duration
        """
        if not self.enabled or not self._initialized:
            return

        current_span = trace.get_current_span()
        if current_span:
            current_span.add_event(
                f"detector.{detector_name}",
                attributes={
                    "detector.name": detector_name,
                    "detector.findings": findings_count,
                    "detector.duration_seconds": duration_seconds,
                },
            )


# Singleton tracing manager
_tracing_manager: Optional[TracingManager] = None


def get_tracer() -> Any:
    """Get the global tracer.

    Returns:
        OpenTelemetry tracer or NoOpTracer
    """
    global _tracing_manager
    if _tracing_manager is None:
        _tracing_manager = TracingManager()
    return _tracing_manager.get_tracer()


def init_tracing(
    service_name: str = "repotoire",
    endpoint: str = "localhost:4317",
    insecure: bool = True,
) -> bool:
    """Initialize global tracing.

    Args:
        service_name: Name of the service in traces
        endpoint: OTLP collector endpoint
        insecure: Whether to use insecure connection

    Returns:
        True if initialization succeeded
    """
    global _tracing_manager
    if _tracing_manager is None:
        _tracing_manager = TracingManager()
    return _tracing_manager.init(service_name, endpoint, insecure)


def traced(
    name: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> Callable:
    """Decorator to automatically trace a function.

    Args:
        name: Span name (defaults to function name)
        attributes: Optional attributes to add to span

    Returns:
        Decorated function

    Example:
        @traced("process_file")
        def process_file(path: str) -> dict:
            return {"path": path}

        @traced(attributes={"component": "detector"})
        def detect() -> list:
            return []
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not HAS_OPENTELEMETRY:
                return func(*args, **kwargs)

            tracer = get_tracer()
            span_name = name or func.__name__

            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    span.set_attributes(attributes)

                # Add function info
                span.set_attribute("code.function", func.__name__)
                if func.__module__:
                    span.set_attribute("code.namespace", func.__module__)

                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise

        return wrapper
    return decorator


def traced_async(
    name: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> Callable:
    """Decorator to automatically trace an async function.

    Args:
        name: Span name (defaults to function name)
        attributes: Optional attributes to add to span

    Returns:
        Decorated async function
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not HAS_OPENTELEMETRY:
                return await func(*args, **kwargs)

            tracer = get_tracer()
            span_name = name or func.__name__

            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    span.set_attributes(attributes)

                span.set_attribute("code.function", func.__name__)
                if func.__module__:
                    span.set_attribute("code.namespace", func.__module__)

                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise

        return wrapper
    return decorator