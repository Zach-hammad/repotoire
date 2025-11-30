"""Style analysis and enforcement for Repotoire auto-fix.

This module provides automatic detection of codebase style conventions
and generates instructions for LLM-based code generation that match
the existing codebase patterns.
"""

from .models import StyleProfile, StyleRule
from .analyzer import StyleAnalyzer, classify_naming
from .enforcer import StyleEnforcer

__all__ = [
    "StyleProfile",
    "StyleRule",
    "StyleAnalyzer",
    "StyleEnforcer",
    "classify_naming",
]
