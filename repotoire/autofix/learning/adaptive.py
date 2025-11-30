"""Adaptive confidence adjustment based on historical feedback."""

from typing import Optional, List

from repotoire.logging_config import get_logger
from repotoire.autofix.models import FixConfidence
from repotoire.autofix.learning.store import DecisionStore, MIN_DECISIONS_FOR_LEARNING
from repotoire.autofix.learning.models import LearningStats, RejectionReason

logger = get_logger(__name__)

# Thresholds for confidence adjustment
LOW_APPROVAL_THRESHOLD = 0.3  # Below 30% approval → downgrade
HIGH_APPROVAL_THRESHOLD = 0.9  # Above 90% approval → upgrade

# Minimum decisions for a specific fix type before adjusting
MIN_FIX_TYPE_DECISIONS = 5


class AdaptiveConfidence:
    """Adjusts fix confidence based on historical user decisions."""

    def __init__(self, store: DecisionStore):
        """Initialize adaptive confidence calculator.

        Args:
            store: Decision store for historical data
        """
        self.store = store

    def adjust_confidence(
        self,
        base: FixConfidence,
        fix_type: str,
        repository: Optional[str] = None,
    ) -> FixConfidence:
        """Adjust confidence level based on historical approval rates.

        Args:
            base: Base confidence from LLM
            fix_type: Type of fix being proposed
            repository: Optional repository path for filtering

        Returns:
            Adjusted FixConfidence (may be same as base)
        """
        stats = self.store.get_stats(repository=repository)

        # Not enough data to make adjustments
        if stats.total_decisions < MIN_DECISIONS_FOR_LEARNING:
            logger.debug(
                f"Not enough decisions ({stats.total_decisions}) for adaptive confidence"
            )
            return base

        # Get fix type specific stats
        type_stats = stats.by_fix_type.get(fix_type, {})
        approved = type_stats.get("approved", 0) + type_stats.get("modified", 0)
        rejected = type_stats.get("rejected", 0)
        total = approved + rejected

        # Not enough data for this specific fix type
        if total < MIN_FIX_TYPE_DECISIONS:
            logger.debug(f"Not enough decisions for fix type '{fix_type}' ({total})")
            return base

        approval_rate = approved / total
        logger.debug(
            f"Fix type '{fix_type}' has {approval_rate:.0%} approval rate ({total} decisions)"
        )

        # Apply adjustment rules
        adjusted = base

        # Low historical approval → downgrade confidence
        if approval_rate < LOW_APPROVAL_THRESHOLD:
            if base == FixConfidence.HIGH:
                adjusted = FixConfidence.MEDIUM
                logger.info(
                    f"Downgraded confidence HIGH→MEDIUM for '{fix_type}' "
                    f"(historical approval: {approval_rate:.0%})"
                )
            elif base == FixConfidence.MEDIUM:
                adjusted = FixConfidence.LOW
                logger.info(
                    f"Downgraded confidence MEDIUM→LOW for '{fix_type}' "
                    f"(historical approval: {approval_rate:.0%})"
                )

        # High historical approval → upgrade confidence (but not to HIGH for safety)
        elif approval_rate > HIGH_APPROVAL_THRESHOLD:
            if base == FixConfidence.LOW:
                adjusted = FixConfidence.MEDIUM
                logger.info(
                    f"Upgraded confidence LOW→MEDIUM for '{fix_type}' "
                    f"(historical approval: {approval_rate:.0%})"
                )
            # Don't upgrade MEDIUM→HIGH automatically for safety

        return adjusted

    def get_prompt_adjustments(
        self,
        repository: Optional[str] = None,
    ) -> str:
        """Generate prompt adjustments based on rejection patterns.

        This returns text that should be appended to the fix generation prompt
        to help the LLM avoid historically rejected patterns.

        Args:
            repository: Optional repository path for filtering

        Returns:
            Markdown formatted adjustment text (empty string if no patterns)
        """
        stats = self.store.get_stats(repository=repository)

        # Not enough data
        if stats.total_decisions < MIN_DECISIONS_FOR_LEARNING:
            return ""

        if not stats.rejection_patterns:
            return ""

        lines = ["## Historical Feedback (from user decisions)", ""]

        # Add fix type warnings
        fix_type_warnings = [
            p for p in stats.rejection_patterns if p.fix_type is not None
        ]
        for pattern in fix_type_warnings[:3]:  # Limit to top 3
            lines.append(f"- {pattern.recommendation}")

        # Add rejection reason guidance
        reason_patterns = [p for p in stats.rejection_patterns if p.reason is not None]
        for pattern in reason_patterns[:3]:
            reason_name = pattern.reason.value.replace("_", " ")
            lines.append(f"- Address {reason_name} issues: {pattern.recommendation}")

            # Include sample user comments
            if pattern.user_comments:
                for comment in pattern.user_comments[:2]:
                    lines.append(f'  - User comment: "{comment}"')

        if len(lines) > 2:  # More than just header
            return "\n".join(lines)
        return ""

    def get_warnings(
        self,
        fix_type: str,
        repository: Optional[str] = None,
    ) -> List[str]:
        """Get warning messages for a specific fix type.

        Args:
            fix_type: The type of fix
            repository: Optional repository filter

        Returns:
            List of warning messages
        """
        warnings = []
        stats = self.store.get_stats(repository=repository)

        if stats.total_decisions < MIN_DECISIONS_FOR_LEARNING:
            return warnings

        # Check fix type approval rate
        type_stats = stats.by_fix_type.get(fix_type, {})
        approved = type_stats.get("approved", 0) + type_stats.get("modified", 0)
        rejected = type_stats.get("rejected", 0)
        total = approved + rejected

        if total >= MIN_FIX_TYPE_DECISIONS:
            rejection_rate = rejected / total
            if rejection_rate >= 0.5:
                warnings.append(
                    f"This '{fix_type}' fix type has been rejected {rejection_rate:.0%} of the time historically."
                )

        # Check for relevant rejection patterns
        for pattern in stats.rejection_patterns:
            if pattern.fix_type == fix_type:
                warnings.append(pattern.recommendation)
                break

        return warnings

    def should_skip_auto_approve(
        self,
        fix_type: str,
        repository: Optional[str] = None,
    ) -> bool:
        """Determine if auto-approve should be disabled for this fix type.

        Args:
            fix_type: The type of fix
            repository: Optional repository filter

        Returns:
            True if auto-approve should be skipped
        """
        stats = self.store.get_stats(repository=repository)

        if stats.total_decisions < MIN_DECISIONS_FOR_LEARNING:
            return False

        type_stats = stats.by_fix_type.get(fix_type, {})
        approved = type_stats.get("approved", 0) + type_stats.get("modified", 0)
        rejected = type_stats.get("rejected", 0)
        total = approved + rejected

        if total < MIN_FIX_TYPE_DECISIONS:
            return False

        approval_rate = approved / total

        # Skip auto-approve if historically rejected more than 50%
        return approval_rate < 0.5

    def get_review_context(
        self,
        fix_type: str,
        confidence: FixConfidence,
        repository: Optional[str] = None,
    ) -> Optional[str]:
        """Get context message to show during review.

        Args:
            fix_type: Type of fix being reviewed
            confidence: Confidence level of the fix
            repository: Optional repository filter

        Returns:
            Context message or None
        """
        return self.store.get_historical_context(
            fix_type=fix_type,
            repository=repository,
        )
