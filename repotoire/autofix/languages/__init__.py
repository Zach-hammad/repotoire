"""Language handlers for multi-language auto-fix support.

This module provides language-specific handlers for syntax validation,
import extraction, and LLM prompt generation.
"""

from pathlib import Path
from typing import Dict, Type

from repotoire.autofix.languages.base import LanguageHandler
from repotoire.autofix.languages.python import PythonHandler
from repotoire.autofix.languages.typescript import TypeScriptHandler
from repotoire.autofix.languages.java import JavaHandler
from repotoire.autofix.languages.go import GoHandler

# Registry of handlers by extension
_EXTENSION_HANDLERS: Dict[str, Type[LanguageHandler]] = {
    # Python
    ".py": PythonHandler,
    ".pyi": PythonHandler,
    ".pyw": PythonHandler,
    # TypeScript/JavaScript
    ".ts": TypeScriptHandler,
    ".tsx": TypeScriptHandler,
    ".js": TypeScriptHandler,
    ".jsx": TypeScriptHandler,
    ".mjs": TypeScriptHandler,
    ".cjs": TypeScriptHandler,
    # Java
    ".java": JavaHandler,
    # Go
    ".go": GoHandler,
}

# Cached handler instances (handlers are stateless, so we can reuse them)
_handler_cache: Dict[str, LanguageHandler] = {}


def get_handler(file_path: str) -> LanguageHandler:
    """Get the appropriate language handler for a file.

    Args:
        file_path: Path to the file (used to determine extension)

    Returns:
        LanguageHandler instance for the file's language.
        Defaults to PythonHandler for unknown extensions.
    """
    # Extract extension
    ext = Path(file_path).suffix.lower()

    # Check cache
    if ext in _handler_cache:
        return _handler_cache[ext]

    # Get handler class (default to Python)
    handler_class = _EXTENSION_HANDLERS.get(ext, PythonHandler)

    # Create and cache instance
    handler = handler_class()
    _handler_cache[ext] = handler

    return handler


def get_handler_for_language(language: str) -> LanguageHandler:
    """Get handler by language name.

    Args:
        language: Language name (case-insensitive): python, typescript, java, go

    Returns:
        LanguageHandler instance for the specified language.
        Defaults to PythonHandler for unknown languages.
    """
    language = language.lower()

    # Map language names to extensions
    language_extensions = {
        "python": ".py",
        "typescript": ".ts",
        "javascript": ".js",
        "java": ".java",
        "go": ".go",
        "golang": ".go",
    }

    ext = language_extensions.get(language, ".py")
    return get_handler(f"dummy{ext}")


def supported_extensions() -> list[str]:
    """Return list of supported file extensions.

    Returns:
        List of file extensions (e.g., ['.py', '.ts', '.java', '.go'])
    """
    return list(_EXTENSION_HANDLERS.keys())


def clear_handler_cache() -> None:
    """Clear the handler cache. Useful for testing."""
    _handler_cache.clear()


__all__ = [
    "LanguageHandler",
    "PythonHandler",
    "TypeScriptHandler",
    "JavaHandler",
    "GoHandler",
    "get_handler",
    "get_handler_for_language",
    "supported_extensions",
    "clear_handler_cache",
]
