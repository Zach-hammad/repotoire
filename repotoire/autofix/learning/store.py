"""JSONL-based storage for fix decisions with in-memory caching."""

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
import os
import time

from repotoire.logging_config import get_logger
from repotoire.autofix.learning.models import (
    FixDecision,
    LearningStats,
    RejectionPattern,
    UserDecision,
    RejectionReason,
)

logger = get_logger(__name__)

# Default storage location
DEFAULT_STORAGE_PATH = Path.home() / ".repotoire" / "decisions.jsonl"

# Minimum decisions needed before learning kicks in
MIN_DECISIONS_FOR_LEARNING = 10

# Minimum decisions for a fix type to be considered in patterns
MIN_FIX_TYPE_DECISIONS = 5


class DecisionStore:
    """Persistent storage for user decisions on fix proposals."""

    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize decision store.

        Args:
            storage_path: Path to JSONL file. Defaults to ~/.repotoire/decisions.jsonl
        """
        self.storage_path = storage_path or DEFAULT_STORAGE_PATH
        self._cache: List[FixDecision] = []
        self._load_cache()

    def _load_cache(self) -> None:
        """Load existing decisions into memory cache."""
        self._cache = []

        if not self.storage_path.exists():
            logger.debug(f"Decision store not found at {self.storage_path}")
            return

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        decision = FixDecision.from_jsonl(line)
                        self._cache.append(decision)
                    except Exception as e:
                        logger.warning(
                            f"Failed to parse decision on line {line_num}: {e}"
                        )

            logger.info(f"Loaded {len(self._cache)} decisions from {self.storage_path}")

        except Exception as e:
            logger.error(f"Failed to load decision store: {e}")

    def record(self, decision: FixDecision) -> None:
        """Record a new decision.

        Appends to file AND adds to memory cache.

        Args:
            decision: The fix decision to record
        """
        # Ensure directory exists
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        # Append to file
        try:
            with open(self.storage_path, "a", encoding="utf-8") as f:
                f.write(decision.to_jsonl() + "\n")

            # Add to cache
            self._cache.append(decision)
            logger.debug(f"Recorded decision {decision.id} for fix {decision.fix_id}")

        except Exception as e:
            logger.error(f"Failed to record decision: {e}")
            raise

    def get_all_decisions(
        self,
        repository: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> List[FixDecision]:
        """Get all decisions, optionally filtered.

        Args:
            repository: Filter by repository path
            since: Filter by timestamp (decisions after this time)

        Returns:
            List of matching decisions
        """
        decisions = self._cache

        if repository:
            # Normalize paths for comparison
            repo_path = str(Path(repository).resolve())
            decisions = [
                d for d in decisions if str(Path(d.repository).resolve()) == repo_path
            ]

        if since:
            decisions = [d for d in decisions if d.timestamp >= since]

        return decisions

    def get_stats(
        self,
        repository: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> LearningStats:
        """Calculate statistics from recorded decisions.

        Args:
            repository: Filter by repository path
            since: Filter by timestamp

        Returns:
            LearningStats with aggregated statistics
        """
        decisions = self.get_all_decisions(repository=repository, since=since)

        if not decisions:
            return LearningStats()

        # Calculate basic stats
        total = len(decisions)
        approved_count = sum(
            1 for d in decisions if d.decision == UserDecision.APPROVED
        )
        modified_count = sum(
            1 for d in decisions if d.decision == UserDecision.MODIFIED
        )
        approval_rate = (approved_count + modified_count) / total if total > 0 else 0.0

        # Breakdown by fix type
        by_fix_type: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"approved": 0, "rejected": 0, "modified": 0}
        )
        for d in decisions:
            by_fix_type[d.fix_type][d.decision.value] += 1

        # Breakdown by confidence
        by_confidence: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"approved": 0, "rejected": 0, "modified": 0}
        )
        for d in decisions:
            conf_key = d.confidence.upper()
            by_confidence[conf_key][d.decision.value] += 1

        # Breakdown by finding type
        by_finding_type: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"approved": 0, "rejected": 0, "modified": 0}
        )
        for d in decisions:
            by_finding_type[d.finding_type][d.decision.value] += 1

        # Breakdown by rejection reason
        by_rejection_reason: Dict[str, int] = defaultdict(int)
        for d in decisions:
            if d.rejection_reason:
                by_rejection_reason[d.rejection_reason.value] += 1

        # Calculate recent approval rate (last 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        recent_decisions = [d for d in decisions if d.timestamp >= thirty_days_ago]
        recent_approval_rate = None
        if recent_decisions:
            recent_approved = sum(
                1 for d in recent_decisions if d.decision in [UserDecision.APPROVED, UserDecision.MODIFIED]
            )
            recent_approval_rate = recent_approved / len(recent_decisions)

        # Calculate trend
        trend = self._calculate_trend(decisions)

        # Find rejection patterns
        rejection_patterns = self._find_rejection_patterns(decisions)

        return LearningStats(
            total_decisions=total,
            approval_rate=approval_rate,
            by_fix_type=dict(by_fix_type),
            by_confidence=dict(by_confidence),
            by_finding_type=dict(by_finding_type),
            by_rejection_reason=dict(by_rejection_reason),
            rejection_patterns=rejection_patterns,
            recent_approval_rate=recent_approval_rate,
            trend=trend,
        )

    def _calculate_trend(self, decisions: List[FixDecision]) -> Optional[str]:
        """Calculate trend in approval rate.

        Args:
            decisions: List of decisions to analyze

        Returns:
            Trend direction (improving/declining/stable) or None
        """
        if len(decisions) < 20:
            return None

        # Sort by timestamp
        sorted_decisions = sorted(decisions, key=lambda d: d.timestamp)

        # Split into halves
        midpoint = len(sorted_decisions) // 2
        first_half = sorted_decisions[:midpoint]
        second_half = sorted_decisions[midpoint:]

        # Calculate approval rates
        def approval_rate(ds: List[FixDecision]) -> float:
            if not ds:
                return 0.0
            approved = sum(1 for d in ds if d.decision in [UserDecision.APPROVED, UserDecision.MODIFIED])
            return approved / len(ds)

        first_rate = approval_rate(first_half)
        second_rate = approval_rate(second_half)

        # Determine trend
        diff = second_rate - first_rate
        if diff > 0.1:
            return "improving"
        elif diff < -0.1:
            return "declining"
        else:
            return "stable"

    def _find_rejection_patterns(
        self, decisions: List[FixDecision]
    ) -> List[RejectionPattern]:
        """Identify patterns in rejected fixes.

        Args:
            decisions: List of decisions to analyze

        Returns:
            List of identified rejection patterns
        """
        patterns = []

        # Pattern 1: Fix types with high rejection rates
        fix_type_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"approved": 0, "rejected": 0}
        )
        for d in decisions:
            outcome = "approved" if d.decision in [UserDecision.APPROVED, UserDecision.MODIFIED] else "rejected"
            fix_type_stats[d.fix_type][outcome] += 1

        for fix_type, stats in fix_type_stats.items():
            total = stats["approved"] + stats["rejected"]
            if total < MIN_FIX_TYPE_DECISIONS:
                continue

            rejection_rate = stats["rejected"] / total
            if rejection_rate >= 0.5:  # 50% or more rejected
                patterns.append(
                    RejectionPattern(
                        pattern=f"{fix_type} fixes",
                        rejection_rate=rejection_rate,
                        sample_size=total,
                        fix_type=fix_type,
                        recommendation=f"Be more cautious with {fix_type} fixes (historically {rejection_rate:.0%} rejected)",
                    )
                )

        # Pattern 2: Common rejection reasons
        reason_counts: Dict[RejectionReason, int] = defaultdict(int)
        reason_comments: Dict[RejectionReason, List[str]] = defaultdict(list)

        for d in decisions:
            if d.rejection_reason:
                reason_counts[d.rejection_reason] += 1
                if d.rejection_comment:
                    reason_comments[d.rejection_reason].append(d.rejection_comment)

        total_rejections = sum(reason_counts.values())
        if total_rejections >= MIN_FIX_TYPE_DECISIONS:
            for reason, count in reason_counts.items():
                if count >= 3:  # At least 3 occurrences
                    rate = count / total_rejections
                    comments = reason_comments[reason][:3]  # Sample comments
                    patterns.append(
                        RejectionPattern(
                            pattern=f"Rejection reason: {reason.value}",
                            rejection_rate=rate,
                            sample_size=count,
                            reason=reason,
                            recommendation=self._get_reason_recommendation(reason),
                            user_comments=comments,
                        )
                    )

        # Sort by rejection rate descending
        patterns.sort(key=lambda p: p.rejection_rate, reverse=True)

        return patterns

    def _get_reason_recommendation(self, reason: RejectionReason) -> str:
        """Get recommendation for a rejection reason.

        Args:
            reason: The rejection reason

        Returns:
            Recommendation string
        """
        recommendations = {
            RejectionReason.STYLE_MISMATCH: "Ensure generated code matches existing naming conventions and style",
            RejectionReason.TOO_RISKY: "Generate smaller, more focused changes",
            RejectionReason.INCORRECT_LOGIC: "Provide more context about expected behavior",
            RejectionReason.NOT_NEEDED: "Be more selective about which issues to fix",
            RejectionReason.BREAKS_TESTS: "Consider test implications before generating fixes",
            RejectionReason.OTHER: "Review user comments for specific feedback",
        }
        return recommendations.get(reason, "Review rejection patterns for improvement")

    def get_historical_context(
        self,
        fix_type: str,
        repository: Optional[str] = None,
    ) -> Optional[str]:
        """Get historical context message for a fix type.

        Args:
            fix_type: The type of fix
            repository: Optional repository filter

        Returns:
            Context message or None if not enough data
        """
        stats = self.get_stats(repository=repository)

        if stats.total_decisions < MIN_DECISIONS_FOR_LEARNING:
            return None

        type_stats = stats.by_fix_type.get(fix_type, {})
        approved = type_stats.get("approved", 0) + type_stats.get("modified", 0)
        rejected = type_stats.get("rejected", 0)
        total = approved + rejected

        if total < 3:
            return None

        approval_rate = approved / total
        return f"Based on history, you approve {approval_rate:.0%} of similar {fix_type} fixes"

    def clear(self) -> None:
        """Clear all stored decisions (for testing)."""
        self._cache = []
        if self.storage_path.exists():
            self.storage_path.unlink()
        logger.info("Cleared all decisions")


def create_decision_id(fix_id: str) -> str:
    """Create a unique decision ID.

    Args:
        fix_id: The fix proposal ID

    Returns:
        Unique decision ID
    """
    timestamp = time.time()
    return f"dec-{fix_id}-{timestamp:.2f}"
