"""Auto-fix functionality for Repotoire.

This module provides AI-powered automatic code fixes with human-in-the-loop approval.
Supports multiple programming languages including Python, TypeScript, Java, and Go.
Also includes template-based fixes for deterministic, fast code transformations.
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
from repotoire.autofix.templates import (
    FixTemplate,
    PatternType,
    TemplateEvidence,
    TemplateFile,
    TemplateMatch,
    TemplateRegistry,
    TemplateLoadError,
    get_registry,
    reset_registry,
    DEFAULT_TEMPLATE_DIRS,
)
from repotoire.autofix.style import (
    StyleAnalyzer,
    StyleEnforcer,
    StyleProfile,
    StyleRule,
    classify_naming,
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
    # Templates
    "FixTemplate",
    "PatternType",
    "TemplateEvidence",
    "TemplateFile",
    "TemplateMatch",
    "TemplateRegistry",
    "TemplateLoadError",
    "get_registry",
    "reset_registry",
    "DEFAULT_TEMPLATE_DIRS",
    # Style analysis
    "StyleAnalyzer",
    "StyleEnforcer",
    "StyleProfile",
    "StyleRule",
    "classify_naming",
]
