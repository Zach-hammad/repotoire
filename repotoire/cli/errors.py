"""CLI error handling with user-friendly messages.

This module provides consistent, helpful error messages for CLI users.
Stack traces are hidden by default but available with --verbose.
"""

import functools
from dataclasses import dataclass
from typing import Callable, Optional, ParamSpec, TypeVar

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from repotoire.logging_config import get_logger

logger = get_logger(__name__)
console = Console(stderr=True)

P = ParamSpec("P")
R = TypeVar("R")


@dataclass
class CLIError(Exception):
    """Base CLI error with user-friendly messaging."""

    message: str
    hint: Optional[str] = None
    fix: Optional[str] = None
    docs_url: Optional[str] = None
    show_traceback: bool = False

    def __str__(self) -> str:
        return self.message


@dataclass
class DatabaseError(CLIError):
    """Database-related errors (connection, schema, corruption)."""

    message: str = "Database error"
    hint: str = "The local database may be corrupted or from an incompatible version."
    fix: str = "rm -rf .repotoire/ && repotoire ingest ."


@dataclass
class AuthError(CLIError):
    """Authentication/authorization errors."""

    message: str = "Authentication failed"
    hint: str = "Your API key may be invalid or expired."
    fix: str = "repotoire login"
    docs_url: str = "https://docs.repotoire.com/cli/authentication"


@dataclass
class NetworkError(CLIError):
    """Network connectivity errors."""

    message: str = "Network error"
    hint: str = "Could not connect to the Repotoire API."
    fix: str = "Check your internet connection and try again."


@dataclass
class ConfigError(CLIError):
    """Configuration errors."""

    message: str = "Configuration error"
    hint: str = "Your configuration file may be invalid."
    fix: str = "repotoire config --reset"


@dataclass
class ParseError(CLIError):
    """Code parsing errors."""

    message: str = "Failed to parse code"
    hint: str = "Some files could not be parsed. This usually means syntax errors."
    fix: str = "Fix syntax errors in your code or exclude problematic files."


@dataclass
class ResourceError(CLIError):
    """Resource exhaustion (memory, disk, etc)."""

    message: str = "Resource limit exceeded"
    hint: str = "The operation ran out of memory or disk space."
    fix: str = "Try with smaller files: repotoire ingest --batch-size 50 --max-file-size 5"


@dataclass
class EmbeddingError(CLIError):
    """Embedding generation errors."""

    message: str = "Embedding generation failed"
    hint: str = "Could not generate vector embeddings."
    fix: str = "Try --no-embeddings or set a valid API key for your embedding backend."


# Error classification rules: (pattern, error_class, custom_message)
ERROR_PATTERNS: list[tuple[str, type[CLIError], Optional[str]]] = [
    # Database errors
    ("table does not exist", DatabaseError, "Database schema is missing or corrupted"),
    ("catalog exception", DatabaseError, "Database schema mismatch"),
    ("binder exception", DatabaseError, "Query refers to missing tables/columns"),
    ("parser exception", DatabaseError, "Invalid query syntax (this is a bug)"),
    ("connection refused", DatabaseError, "Could not connect to database"),
    ("database is locked", DatabaseError, "Database is in use by another process"),
    ("database path cannot be", DatabaseError, "Database is corrupted"),
    ("failed to initialize", DatabaseError, "Could not initialize database"),
    ("configurationerror", DatabaseError, None),
    ("no nodes found", DatabaseError, "No code found - run 'repotoire ingest .' first"),
    ("no code found", DatabaseError, "No code found - run 'repotoire ingest .' first"),

    # Auth errors
    ("401", AuthError, "API key is invalid or expired"),
    ("403", AuthError, "Access denied - check your permissions"),
    ("authentication", AuthError, None),
    ("unauthorized", AuthError, None),

    # Network errors
    ("connection error", NetworkError, None),
    ("timeout", NetworkError, "Request timed out - server may be overloaded"),
    ("ssl", NetworkError, "SSL/TLS error - check your network settings"),
    ("name resolution", NetworkError, "Could not resolve hostname"),

    # Resource errors
    ("memory", ResourceError, "Out of memory"),
    ("disk", ResourceError, "Out of disk space"),
    ("too many open files", ResourceError, "File descriptor limit reached"),

    # Embedding errors
    ("rate limit", EmbeddingError, "Embedding API rate limit exceeded - wait and retry"),
    ("quota", EmbeddingError, "API quota exceeded"),
    ("openai", EmbeddingError, "OpenAI API error"),
    ("voyage", EmbeddingError, "Voyage API error"),
]


def classify_error(error: Exception) -> CLIError:
    """Classify an exception into a user-friendly CLIError.
    
    Args:
        error: The original exception
        
    Returns:
        A CLIError with helpful messaging
    """
    if isinstance(error, CLIError):
        return error

    error_str = str(error).lower()
    error_type = type(error).__name__

    # Check patterns
    for pattern, error_class, custom_msg in ERROR_PATTERNS:
        if pattern in error_str or pattern in error_type.lower():
            msg = custom_msg or str(error)
            return error_class(message=msg)

    # Default: wrap in CLIError
    return CLIError(
        message=str(error)[:300],
        hint="An unexpected error occurred.",
        fix="Run with --log-level DEBUG for more details, or report this issue.",
        docs_url="https://github.com/Zach-hammad/repotoire/issues",
    )


def format_error(error: CLIError, verbose: bool = False) -> Panel:
    """Format a CLIError as a rich Panel.
    
    Args:
        error: The error to format
        verbose: Whether to show full details
        
    Returns:
        Rich Panel with formatted error
    """
    content = Text()

    # Main error message
    content.append(error.message, style="bold")
    content.append("\n")

    # Hint
    if error.hint:
        content.append("\n")
        content.append("💡 ", style="yellow")
        content.append(error.hint, style="dim")

    # Fix
    if error.fix:
        content.append("\n\n")
        content.append("Fix: ", style="green bold")
        content.append(error.fix, style="cyan")

    # Docs
    if error.docs_url:
        content.append("\n\n")
        content.append("📚 ", style="blue")
        content.append(error.docs_url, style="blue underline")

    return Panel(
        content,
        title="[red bold]Error[/red bold]",
        border_style="red",
        padding=(1, 2),
    )


def print_error(
    error: Exception,
    verbose: bool = False,
    exit_code: int = 1,
) -> None:
    """Print an error message and optionally exit.
    
    Args:
        error: The exception to print
        verbose: Show full traceback
        exit_code: Exit code (0 = don't exit)
    """
    cli_error = classify_error(error)

    # Log full traceback only at debug level (not visible by default)
    logger.debug(f"CLI error: {error}", exc_info=True)

    # Print user-friendly message
    console.print()
    console.print(format_error(cli_error, verbose=verbose))

    # Show traceback only in verbose mode
    if verbose or cli_error.show_traceback:
        console.print("\n[dim]Traceback (for debugging):[/dim]")
        console.print_exception(show_locals=False)

    if exit_code:
        raise click.Abort()


def handle_errors(
    *,
    exit_on_error: bool = True,
    reraise: tuple[type[Exception], ...] = (),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator for CLI commands that provides friendly error handling.
    
    Usage:
        @cli.command()
        @handle_errors()
        def my_command():
            ...
    
    Args:
        exit_on_error: Whether to exit on error (default: True)
        reraise: Exception types to re-raise without handling
        
    Returns:
        Decorated function
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Check for verbose flag in click context
            ctx = click.get_current_context(silent=True)
            verbose = False
            if ctx:
                verbose = ctx.params.get("verbose", False) or ctx.params.get("log_level") == "DEBUG"

            try:
                return func(*args, **kwargs)
            except reraise:
                raise
            except click.Abort:
                raise
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted[/yellow]")
                raise click.Abort()
            except CLIError as e:
                print_error(e, verbose=verbose, exit_code=1 if exit_on_error else 0)
            except Exception as e:
                print_error(e, verbose=verbose, exit_code=1 if exit_on_error else 0)

        return wrapper
    return decorator


# Convenience function for raising errors with context
def fail(
    message: str,
    *,
    hint: Optional[str] = None,
    fix: Optional[str] = None,
    docs_url: Optional[str] = None,
) -> None:
    """Raise a CLI error with the given message.
    
    Args:
        message: Error message
        hint: Optional hint for the user
        fix: Optional fix command
        docs_url: Optional documentation URL
        
    Raises:
        CLIError: Always
    """
    raise CLIError(message=message, hint=hint, fix=fix, docs_url=docs_url)
