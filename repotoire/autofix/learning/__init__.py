"""Learning feedback system for auto-fix.

This module provides adaptive confidence adjustment based on user feedback
from fix approval/rejection decisions.
"""

from repotoire.autofix.learning.models import (
    UserDecision,
    RejectionReason,
    FixDecision,
    LearningStats,
    RejectionPattern,
)
from repotoire.autofix.learning.store import (
    DecisionStore,
    create_decision_id,
    DEFAULT_STORAGE_PATH,
    MIN_DECISIONS_FOR_LEARNING,
)
from repotoire.autofix.learning.adaptive import (
    AdaptiveConfidence,
    LOW_APPROVAL_THRESHOLD,
    HIGH_APPROVAL_THRESHOLD,
)

__all__ = [
    # Models
    "UserDecision",
    "RejectionReason",
    "FixDecision",
    "LearningStats",
    "RejectionPattern",
    # Store
    "DecisionStore",
    "create_decision_id",
    "DEFAULT_STORAGE_PATH",
    "MIN_DECISIONS_FOR_LEARNING",
    # Adaptive
    "AdaptiveConfidence",
    "LOW_APPROVAL_THRESHOLD",
    "HIGH_APPROVAL_THRESHOLD",
]
