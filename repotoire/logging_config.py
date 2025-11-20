"""Structured logging configuration for Falkor.

Provides JSON-formatted structured logging with context and duration tracking.
"""

import logging
import json
import time
import os
from pathlib import Path
from typing import Any, Dict, Optional
from contextvars import ContextVar
from functools import wraps


# Context variables for request-scoped logging
log_context: ContextVar[Dict[str, Any]] = ContextVar('log_context', default={})


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON with context.

        Args:
            record: Log record to format

        Returns:
            JSON-formatted log string
        """
        # Base log entry
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add file location if available
        if record.pathname:
            log_data["file"] = {
                "path": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info),
            }

        # Add context from context vars
        context = log_context.get()
        if context:
            log_data["context"] = context

        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in [
                "name", "msg", "args", "created", "filename", "funcName", "levelname",
                "levelno", "lineno", "module", "msecs", "message", "pathname",
                "process", "processName", "relativeCreated", "thread", "threadName",
                "exc_info", "exc_text", "stack_info", "getMessage", "taskName",
            ]:
                log_data[key] = value

        return json.dumps(log_data)


class HumanReadableFormatter(logging.Formatter):
    """Human-readable formatter for console output."""

    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'

    def format(self, record: logging.LogRecord) -> str:
        """Format log record in human-readable format with colors.

        Args:
            record: Log record to format

        Returns:
            Formatted log string
        """
        # Get color for log level
        color = self.COLORS.get(record.levelname, '')

        # Base message
        message = f"{color}{record.levelname}{self.RESET} [{record.name}] {record.getMessage()}"

        # Add context if available
        context = log_context.get()
        if context:
            context_str = ", ".join(f"{k}={v}" for k, v in context.items())
            message += f" ({context_str})"

        # Add exception if present
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)

        return message


def configure_logging(
    level: Optional[str] = None,
    json_output: bool = False,
    log_file: Optional[str] = None,
) -> None:
    """Configure structured logging for Falkor.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
               Defaults to LOG_LEVEL env var or INFO.
        json_output: If True, output JSON format. If False, human-readable format.
                     Defaults to LOG_FORMAT=json env var.
        log_file: Optional file path for log output. Defaults to LOG_FILE env var.
    """
    # Get log level from env or parameter
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Get format from env or parameter
    if os.getenv("LOG_FORMAT") == "json":
        json_output = True

    # Get log file from env or parameter
    if log_file is None:
        log_file = os.getenv("LOG_FILE")

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, level))

    if json_output:
        console_handler.setFormatter(StructuredFormatter())
    else:
        console_handler.setFormatter(HumanReadableFormatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))

    root_logger.addHandler(console_handler)

    # File handler if specified
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(getattr(logging, level))
        file_handler.setFormatter(StructuredFormatter())  # Always JSON for file
        root_logger.addHandler(file_handler)


def set_context(**kwargs: Any) -> None:
    """Set context variables for structured logging.

    Args:
        **kwargs: Context key-value pairs

    Example:
        >>> set_context(operation="ingest", repo_path="/path/to/repo")
    """
    current = log_context.get().copy()
    current.update(kwargs)
    log_context.set(current)


def clear_context() -> None:
    """Clear all context variables."""
    log_context.set({})


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


def log_operation(operation_name: str):
    """Decorator to log operation with duration tracking.

    Args:
        operation_name: Name of the operation to log

    Example:
        >>> @log_operation("parse_file")
        ... def parse_file(path):
        ...     # parsing logic
        ...     pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger(func.__module__)

            # Set operation context
            set_context(operation=operation_name)

            start_time = time.time()
            logger.info(f"Starting {operation_name}")

            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time

                logger.info(
                    f"Completed {operation_name}",
                    extra={"duration_seconds": round(duration, 3)}
                )

                return result

            except Exception as e:
                duration = time.time() - start_time

                logger.error(
                    f"Failed {operation_name}: {str(e)}",
                    extra={"duration_seconds": round(duration, 3)},
                    exc_info=True
                )
                raise

            finally:
                clear_context()

        return wrapper
    return decorator


class LogContext:
    """Context manager for scoped logging context.

    Example:
        >>> with LogContext(file_path="test.py", operation="parse"):
        ...     logger.info("Parsing file")  # Will include context
    """

    def __init__(self, **kwargs: Any):
        """Initialize context manager with context variables.

        Args:
            **kwargs: Context key-value pairs
        """
        self.context = kwargs
        self.previous_context = None

    def __enter__(self):
        """Enter context and set variables."""
        self.previous_context = log_context.get().copy()
        set_context(**self.context)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context and restore previous variables."""
        log_context.set(self.previous_context)
