"""Auto-fix functionality for Repotoire.

This module provides AI-powered automatic code fixes with human-in-the-loop approval.
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

__all__ = [
    "AutoFixEngine",
    "InteractiveReviewer",
    "FixApplicator",
    "FixProposal",
    "FixContext",
    "CodeChange",
    "FixType",
    "FixConfidence",
    "FixStatus",
    "FixBatch",
]
