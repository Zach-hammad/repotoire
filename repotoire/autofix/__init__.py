"""Auto-fix functionality for Repotoire.

This module provides AI-powered automatic code fixes with human-in-the-loop approval.
Supports multiple programming languages including Python, TypeScript, Java, and Go.
"""

from repotoire.autofix.engine import AutoFixEngine
from repotoire.autofix.reviewer import InteractiveReviewer
from repotoire.autofix.applicator import FixApplicator
from repotoire.autofix.models import (
    FixProposal,
    FixContext,
    CodeChange,
    Evidence,
    FixType,
    FixConfidence,
    FixStatus,
    FixBatch,
)
from repotoire.autofix.languages import (
    LanguageHandler,
    PythonHandler,
    TypeScriptHandler,
    JavaHandler,
    GoHandler,
    get_handler,
    get_handler_for_language,
    supported_extensions,
)

__all__ = [
    # Core auto-fix
    "AutoFixEngine",
    "InteractiveReviewer",
    "FixApplicator",
    # Models
    "FixProposal",
    "FixContext",
    "CodeChange",
    "FixType",
    "FixConfidence",
    "FixStatus",
    "FixBatch",
    # Language handlers
    "LanguageHandler",
    "PythonHandler",
    "TypeScriptHandler",
    "JavaHandler",
    "GoHandler",
    "get_handler",
    "get_handler_for_language",
    "supported_extensions",
]
