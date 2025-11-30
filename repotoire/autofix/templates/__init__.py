"""Fix templates for automatic code fixes.

This module provides template-based code fixes that can be applied without LLM calls.
Templates are loaded from YAML files and matched against code using regex or literal patterns.

Usage:
    from repotoire.autofix.templates import get_registry, TemplateRegistry

    # Get the global registry (lazy-loaded from default paths)
    registry = get_registry()

    # Match code against templates
    match = registry.match(code, file_path="src/module.py", language="python")
    if match:
        print(f"Template: {match.template.name}")
        print(f"Fixed code: {match.fixed_code}")
"""

from repotoire.autofix.templates.models import (
    FixTemplate,
    PatternType,
    TemplateEvidence,
    TemplateFile,
    TemplateMatch,
)
from repotoire.autofix.templates.registry import (
    DEFAULT_TEMPLATE_DIRS,
    TemplateLoadError,
    TemplateRegistry,
    get_registry,
    reset_registry,
)

__all__ = [
    # Models
    "FixTemplate",
    "PatternType",
    "TemplateEvidence",
    "TemplateFile",
    "TemplateMatch",
    # Registry
    "TemplateRegistry",
    "TemplateLoadError",
    "get_registry",
    "reset_registry",
    "DEFAULT_TEMPLATE_DIRS",
]
