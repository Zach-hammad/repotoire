"""Tests for the learning feedback system."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytest

from repotoire.autofix.learning import (
    UserDecision,
    RejectionReason,
    FixDecision,
    LearningStats,
    RejectionPattern,
    DecisionStore,
    AdaptiveConfidence,
    create_decision_id,
    MIN_DECISIONS_FOR_LEARNING,
)
from repotoire.autofix.models import FixConfidence


class TestModels:
    """Tests for learning data models."""

    def test_user_decision_enum(self):
        """Test UserDecision enum values."""
        assert UserDecision.APPROVED.value == "approved"
        assert UserDecision.REJECTED.value == "rejected"
        assert UserDecision.MODIFIED.value == "modified"

    def test_rejection_reason_enum(self):
        """Test RejectionReason enum values."""
        assert RejectionReason.STYLE_MISMATCH.value == "style_mismatch"
        assert RejectionReason.TOO_RISKY.value == "too_risky"
        assert RejectionReason.INCORRECT_LOGIC.value == "incorrect_logic"
        assert RejectionReason.NOT_NEEDED.value == "not_needed"
        assert RejectionReason.BREAKS_TESTS.value == "breaks_tests"
        assert RejectionReason.OTHER.value == "other"

    def test_fix_decision_creation(self):
        """Test FixDecision model creation."""
        decision = FixDecision(
            id="dec-abc123-1234567890.12",
            fix_id="abc123",
            decision=UserDecision.APPROVED,
            fix_type="refactor",
            confidence="HIGH",
            finding_type="Complex function detected",
            file_path="src/utils.py",
            repository="/home/user/project",
        )

        assert decision.id == "dec-abc123-1234567890.12"
        assert decision.fix_id == "abc123"
        assert decision.decision == UserDecision.APPROVED
        assert decision.rejection_reason is None
        assert decision.rejection_comment is None
        assert decision.fix_type == "refactor"
        assert decision.confidence == "HIGH"
        assert isinstance(decision.timestamp, datetime)
        assert decision.characteristics == {}

    def test_fix_decision_with_rejection(self):
        """Test FixDecision model with rejection details."""
        decision = FixDecision(
            id="dec-def456-1234567890.12",
            fix_id="def456",
            decision=UserDecision.REJECTED,
            rejection_reason=RejectionReason.STYLE_MISMATCH,
            rejection_comment="We use camelCase, not snake_case",
            fix_type="rename",
            confidence="MEDIUM",
            finding_type="Unclear variable name",
            file_path="src/api.py",
            repository="/home/user/project",
            characteristics={"lines_changed": 5},
        )

        assert decision.decision == UserDecision.REJECTED
        assert decision.rejection_reason == RejectionReason.STYLE_MISMATCH
        assert decision.rejection_comment == "We use camelCase, not snake_case"
        assert decision.characteristics == {"lines_changed": 5}

    def test_fix_decision_jsonl_serialization(self):
        """Test FixDecision JSONL serialization/deserialization."""
        original = FixDecision(
            id="dec-test-1234567890.12",
            fix_id="test123",
            decision=UserDecision.REJECTED,
            rejection_reason=RejectionReason.TOO_RISKY,
            rejection_comment="Too many changes",
            fix_type="extract",
            confidence="LOW",
            finding_type="Long function",
            file_path="src/main.py",
            repository="/path/to/repo",
            characteristics={"lines_changed": 50},
        )

        jsonl = original.to_jsonl()
        restored = FixDecision.from_jsonl(jsonl)

        assert restored.id == original.id
        assert restored.fix_id == original.fix_id
        assert restored.decision == original.decision
        assert restored.rejection_reason == original.rejection_reason
        assert restored.rejection_comment == original.rejection_comment
        assert restored.fix_type == original.fix_type
        assert restored.confidence == original.confidence
        assert restored.characteristics == original.characteristics

    def test_learning_stats_approval_rate_calculation(self):
        """Test LearningStats approval rate methods."""
        stats = LearningStats(
            total_decisions=20,
            approval_rate=0.8,
            by_fix_type={
                "refactor": {"approved": 15, "rejected": 2, "modified": 0},
                "extract": {"approved": 1, "rejected": 2, "modified": 0},
            },
            by_confidence={
                "HIGH": {"approved": 10, "rejected": 1, "modified": 0},
                "MEDIUM": {"approved": 5, "rejected": 2, "modified": 0},
                "LOW": {"approved": 1, "rejected": 1, "modified": 0},
            },
        )

        # Fix type approval rates
        refactor_rate = stats.get_fix_type_approval_rate("refactor")
        assert refactor_rate == pytest.approx(15 / 17, rel=1e-2)

        extract_rate = stats.get_fix_type_approval_rate("extract")
        assert extract_rate == pytest.approx(1 / 3, rel=1e-2)

        unknown_rate = stats.get_fix_type_approval_rate("unknown")
        assert unknown_rate is None

        # Confidence approval rates
        high_rate = stats.get_confidence_approval_rate("HIGH")
        assert high_rate == pytest.approx(10 / 11, rel=1e-2)

        low_rate = stats.get_confidence_approval_rate("LOW")
        assert low_rate == pytest.approx(0.5, rel=1e-2)

    def test_create_decision_id(self):
        """Test decision ID generation."""
        fix_id = "abc123"
        decision_id = create_decision_id(fix_id)

        assert decision_id.startswith(f"dec-{fix_id}-")
        assert len(decision_id) > len(f"dec-{fix_id}-")

        # IDs should be unique (different timestamps)
        import time
        time.sleep(0.01)
        another_id = create_decision_id(fix_id)
        assert decision_id != another_id


class TestDecisionStore:
    """Tests for DecisionStore class."""

    @pytest.fixture
    def temp_store_path(self, tmp_path):
        """Create a temporary path for the decision store."""
        return tmp_path / "decisions.jsonl"

    @pytest.fixture
    def store(self, temp_store_path):
        """Create a fresh DecisionStore for each test."""
        return DecisionStore(storage_path=temp_store_path)

    def test_record_and_load_decision(self, store, temp_store_path):
        """Test recording and loading decisions."""
        decision = FixDecision(
            id="dec-test-1234567890.12",
            fix_id="test123",
            decision=UserDecision.APPROVED,
            fix_type="refactor",
            confidence="HIGH",
            finding_type="Complex function",
            file_path="src/utils.py",
            repository="/test/repo",
        )

        store.record(decision)

        # Verify in memory
        assert len(store._cache) == 1
        assert store._cache[0].id == decision.id

        # Verify on disk
        assert temp_store_path.exists()
        with open(temp_store_path) as f:
            lines = f.readlines()
        assert len(lines) == 1

        # Test loading from disk
        new_store = DecisionStore(storage_path=temp_store_path)
        assert len(new_store._cache) == 1
        assert new_store._cache[0].id == decision.id

    def test_get_all_decisions_with_filters(self, store):
        """Test filtering decisions by repository and time."""
        now = datetime.utcnow()

        # Add decisions for different repos and times
        decisions = [
            FixDecision(
                id="dec-1-1.0",
                fix_id="fix1",
                decision=UserDecision.APPROVED,
                fix_type="refactor",
                confidence="HIGH",
                finding_type="Complex",
                file_path="a.py",
                repository="/repo/a",
                timestamp=now - timedelta(days=10),
            ),
            FixDecision(
                id="dec-2-2.0",
                fix_id="fix2",
                decision=UserDecision.REJECTED,
                fix_type="extract",
                confidence="MEDIUM",
                finding_type="Long",
                file_path="b.py",
                repository="/repo/b",
                timestamp=now - timedelta(days=5),
            ),
            FixDecision(
                id="dec-3-3.0",
                fix_id="fix3",
                decision=UserDecision.APPROVED,
                fix_type="simplify",
                confidence="HIGH",
                finding_type="Nested",
                file_path="c.py",
                repository="/repo/a",
                timestamp=now,
            ),
        ]

        for d in decisions:
            store.record(d)

        # Filter by repository
        repo_a_decisions = store.get_all_decisions(repository="/repo/a")
        assert len(repo_a_decisions) == 2

        # Filter by time (last 7 days)
        recent_decisions = store.get_all_decisions(since=now - timedelta(days=7))
        assert len(recent_decisions) == 2

        # Combined filter
        combined = store.get_all_decisions(
            repository="/repo/a",
            since=now - timedelta(days=3),
        )
        assert len(combined) == 1
        assert combined[0].id == "dec-3-3.0"

    def test_get_stats_empty_store(self, store):
        """Test stats for empty store."""
        stats = store.get_stats()

        assert stats.total_decisions == 0
        assert stats.approval_rate == 0.0
        assert stats.by_fix_type == {}
        assert stats.rejection_patterns == []

    def test_get_stats_with_decisions(self, store):
        """Test stats calculation with multiple decisions."""
        # Add 15 refactor decisions (12 approved, 3 rejected)
        for i in range(12):
            store.record(
                FixDecision(
                    id=f"dec-ref-{i}.0",
                    fix_id=f"ref{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                )
            )
        for i in range(3):
            store.record(
                FixDecision(
                    id=f"dec-ref-rej-{i}.0",
                    fix_id=f"ref-rej{i}",
                    decision=UserDecision.REJECTED,
                    rejection_reason=RejectionReason.STYLE_MISMATCH,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                )
            )

        # Add 5 extract decisions (1 approved, 4 rejected)
        store.record(
            FixDecision(
                id="dec-ext-0.0",
                fix_id="ext0",
                decision=UserDecision.APPROVED,
                fix_type="extract",
                confidence="MEDIUM",
                finding_type="Long",
                file_path="b.py",
                repository="/test",
            )
        )
        for i in range(4):
            store.record(
                FixDecision(
                    id=f"dec-ext-rej-{i}.0",
                    fix_id=f"ext-rej{i}",
                    decision=UserDecision.REJECTED,
                    rejection_reason=RejectionReason.TOO_RISKY,
                    fix_type="extract",
                    confidence="MEDIUM",
                    finding_type="Long",
                    file_path="b.py",
                    repository="/test",
                )
            )

        stats = store.get_stats()

        assert stats.total_decisions == 20
        assert stats.approval_rate == pytest.approx(13 / 20, rel=1e-2)

        # Check fix type breakdown
        assert stats.by_fix_type["refactor"]["approved"] == 12
        assert stats.by_fix_type["refactor"]["rejected"] == 3
        assert stats.by_fix_type["extract"]["approved"] == 1
        assert stats.by_fix_type["extract"]["rejected"] == 4

        # Check rejection reasons
        assert stats.by_rejection_reason["style_mismatch"] == 3
        assert stats.by_rejection_reason["too_risky"] == 4

    def test_find_rejection_patterns(self, store):
        """Test rejection pattern detection."""
        # Add extract decisions with high rejection rate (80%)
        for i in range(2):
            store.record(
                FixDecision(
                    id=f"dec-ext-ok-{i}.0",
                    fix_id=f"ext-ok{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="extract",
                    confidence="MEDIUM",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )
        for i in range(8):
            store.record(
                FixDecision(
                    id=f"dec-ext-bad-{i}.0",
                    fix_id=f"ext-bad{i}",
                    decision=UserDecision.REJECTED,
                    rejection_reason=RejectionReason.TOO_RISKY,
                    rejection_comment="Too many changes" if i < 3 else None,
                    fix_type="extract",
                    confidence="MEDIUM",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )

        stats = store.get_stats()

        # Should find pattern for extract fixes
        extract_patterns = [
            p for p in stats.rejection_patterns if p.fix_type == "extract"
        ]
        assert len(extract_patterns) == 1
        assert extract_patterns[0].rejection_rate == pytest.approx(0.8, rel=1e-2)

        # Should find pattern for rejection reason
        reason_patterns = [
            p for p in stats.rejection_patterns
            if p.reason == RejectionReason.TOO_RISKY
        ]
        assert len(reason_patterns) == 1

    def test_get_historical_context(self, store):
        """Test historical context message generation."""
        # Not enough data
        context = store.get_historical_context(fix_type="refactor")
        assert context is None

        # Add enough decisions
        for i in range(15):
            store.record(
                FixDecision(
                    id=f"dec-{i}.0",
                    fix_id=f"fix{i}",
                    decision=UserDecision.APPROVED if i < 14 else UserDecision.REJECTED,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                )
            )

        context = store.get_historical_context(fix_type="refactor")
        assert context is not None
        assert "93%" in context or "94%" in context  # ~14/15

    def test_clear(self, store, temp_store_path):
        """Test clearing the store."""
        store.record(
            FixDecision(
                id="dec-test-1.0",
                fix_id="test",
                decision=UserDecision.APPROVED,
                fix_type="refactor",
                confidence="HIGH",
                finding_type="Complex",
                file_path="a.py",
                repository="/test",
            )
        )

        assert len(store._cache) == 1
        assert temp_store_path.exists()

        store.clear()

        assert len(store._cache) == 0
        assert not temp_store_path.exists()


class TestAdaptiveConfidence:
    """Tests for AdaptiveConfidence class."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a fresh DecisionStore for each test."""
        return DecisionStore(storage_path=tmp_path / "decisions.jsonl")

    @pytest.fixture
    def adaptive(self, store):
        """Create AdaptiveConfidence instance."""
        return AdaptiveConfidence(store)

    def test_no_adjustment_without_data(self, adaptive):
        """Test that confidence is not adjusted without enough data."""
        result = adaptive.adjust_confidence(
            base=FixConfidence.HIGH,
            fix_type="refactor",
        )
        assert result == FixConfidence.HIGH

    def test_no_adjustment_insufficient_fix_type_data(self, adaptive, store):
        """Test no adjustment when fix type has insufficient data."""
        # Add enough total decisions but not for this fix type
        for i in range(15):
            store.record(
                FixDecision(
                    id=f"dec-{i}.0",
                    fix_id=f"fix{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="simplify",  # Different fix type
                    confidence="HIGH",
                    finding_type="Nested",
                    file_path="a.py",
                    repository="/test",
                )
            )

        result = adaptive.adjust_confidence(
            base=FixConfidence.HIGH,
            fix_type="refactor",  # No data for this
        )
        assert result == FixConfidence.HIGH

    def test_downgrade_high_to_medium_low_approval(self, adaptive, store):
        """Test downgrading HIGH to MEDIUM when approval rate is low."""
        # Add decisions with 20% approval rate (2/10)
        for i in range(2):
            store.record(
                FixDecision(
                    id=f"dec-ok-{i}.0",
                    fix_id=f"ok{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="extract",
                    confidence="HIGH",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )
        for i in range(8):
            store.record(
                FixDecision(
                    id=f"dec-bad-{i}.0",
                    fix_id=f"bad{i}",
                    decision=UserDecision.REJECTED,
                    fix_type="extract",
                    confidence="HIGH",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )

        result = adaptive.adjust_confidence(
            base=FixConfidence.HIGH,
            fix_type="extract",
        )
        assert result == FixConfidence.MEDIUM

    def test_downgrade_medium_to_low(self, adaptive, store):
        """Test downgrading MEDIUM to LOW when approval rate is low."""
        # Add decisions with 20% approval rate
        for i in range(2):
            store.record(
                FixDecision(
                    id=f"dec-ok-{i}.0",
                    fix_id=f"ok{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="extract",
                    confidence="MEDIUM",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )
        for i in range(8):
            store.record(
                FixDecision(
                    id=f"dec-bad-{i}.0",
                    fix_id=f"bad{i}",
                    decision=UserDecision.REJECTED,
                    fix_type="extract",
                    confidence="MEDIUM",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )

        result = adaptive.adjust_confidence(
            base=FixConfidence.MEDIUM,
            fix_type="extract",
        )
        assert result == FixConfidence.LOW

    def test_upgrade_low_to_medium_high_approval(self, adaptive, store):
        """Test upgrading LOW to MEDIUM when approval rate is high."""
        # Add decisions with 95% approval rate
        for i in range(19):
            store.record(
                FixDecision(
                    id=f"dec-ok-{i}.0",
                    fix_id=f"ok{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                )
            )
        store.record(
            FixDecision(
                id="dec-bad-0.0",
                fix_id="bad0",
                decision=UserDecision.REJECTED,
                fix_type="refactor",
                confidence="HIGH",
                finding_type="Complex",
                file_path="a.py",
                repository="/test",
            )
        )

        result = adaptive.adjust_confidence(
            base=FixConfidence.LOW,
            fix_type="refactor",
        )
        assert result == FixConfidence.MEDIUM

    def test_no_upgrade_to_high(self, adaptive, store):
        """Test that MEDIUM is not automatically upgraded to HIGH."""
        # Add decisions with 95% approval rate
        for i in range(19):
            store.record(
                FixDecision(
                    id=f"dec-ok-{i}.0",
                    fix_id=f"ok{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                )
            )
        store.record(
            FixDecision(
                id="dec-bad-0.0",
                fix_id="bad0",
                decision=UserDecision.REJECTED,
                fix_type="refactor",
                confidence="HIGH",
                finding_type="Complex",
                file_path="a.py",
                repository="/test",
            )
        )

        result = adaptive.adjust_confidence(
            base=FixConfidence.MEDIUM,
            fix_type="refactor",
        )
        # Should NOT be upgraded to HIGH for safety
        assert result == FixConfidence.MEDIUM

    def test_get_prompt_adjustments_empty(self, adaptive):
        """Test empty prompt adjustments without data."""
        result = adaptive.get_prompt_adjustments()
        assert result == ""

    def test_get_prompt_adjustments_with_patterns(self, adaptive, store):
        """Test prompt adjustments with rejection patterns."""
        # Add extract decisions with high rejection rate
        for i in range(2):
            store.record(
                FixDecision(
                    id=f"dec-ok-{i}.0",
                    fix_id=f"ok{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="extract",
                    confidence="MEDIUM",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )
        for i in range(8):
            store.record(
                FixDecision(
                    id=f"dec-bad-{i}.0",
                    fix_id=f"bad{i}",
                    decision=UserDecision.REJECTED,
                    rejection_reason=RejectionReason.STYLE_MISMATCH,
                    rejection_comment="Wrong naming convention" if i < 3 else None,
                    fix_type="extract",
                    confidence="MEDIUM",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )

        result = adaptive.get_prompt_adjustments()
        assert "Historical Feedback" in result
        assert "extract" in result.lower()

    def test_get_warnings(self, adaptive, store):
        """Test warning generation for fix types."""
        # Not enough data
        warnings = adaptive.get_warnings(fix_type="extract")
        assert warnings == []

        # Add decisions with 60% rejection rate
        for i in range(4):
            store.record(
                FixDecision(
                    id=f"dec-ok-{i}.0",
                    fix_id=f"ok{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="extract",
                    confidence="MEDIUM",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )
        for i in range(6):
            store.record(
                FixDecision(
                    id=f"dec-bad-{i}.0",
                    fix_id=f"bad{i}",
                    decision=UserDecision.REJECTED,
                    fix_type="extract",
                    confidence="MEDIUM",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )

        warnings = adaptive.get_warnings(fix_type="extract")
        assert len(warnings) > 0
        assert "60%" in warnings[0]

    def test_should_skip_auto_approve(self, adaptive, store):
        """Test auto-approve skip logic."""
        # Not enough data
        assert adaptive.should_skip_auto_approve(fix_type="extract") is False

        # Add decisions with 60% rejection rate
        for i in range(4):
            store.record(
                FixDecision(
                    id=f"dec-ok-{i}.0",
                    fix_id=f"ok{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="extract",
                    confidence="HIGH",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )
        for i in range(6):
            store.record(
                FixDecision(
                    id=f"dec-bad-{i}.0",
                    fix_id=f"bad{i}",
                    decision=UserDecision.REJECTED,
                    fix_type="extract",
                    confidence="HIGH",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/test",
                )
            )

        # Should skip auto-approve because approval rate < 50%
        assert adaptive.should_skip_auto_approve(fix_type="extract") is True

        # Other fix types should not be affected
        assert adaptive.should_skip_auto_approve(fix_type="refactor") is False

    def test_repository_filtering(self, adaptive, store):
        """Test that decisions are filtered by repository."""
        # Add approved decisions for repo A
        for i in range(10):
            store.record(
                FixDecision(
                    id=f"dec-a-{i}.0",
                    fix_id=f"a{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="extract",
                    confidence="HIGH",
                    finding_type="Long",
                    file_path="a.py",
                    repository="/repo/a",
                )
            )

        # Add rejected decisions for repo B
        for i in range(10):
            store.record(
                FixDecision(
                    id=f"dec-b-{i}.0",
                    fix_id=f"b{i}",
                    decision=UserDecision.REJECTED,
                    fix_type="extract",
                    confidence="HIGH",
                    finding_type="Long",
                    file_path="b.py",
                    repository="/repo/b",
                )
            )

        # Repo A should have high approval → no downgrade
        result_a = adaptive.adjust_confidence(
            base=FixConfidence.HIGH,
            fix_type="extract",
            repository="/repo/a",
        )
        assert result_a == FixConfidence.HIGH

        # Repo B should have low approval → downgrade
        result_b = adaptive.adjust_confidence(
            base=FixConfidence.HIGH,
            fix_type="extract",
            repository="/repo/b",
        )
        assert result_b == FixConfidence.MEDIUM


class TestIntegration:
    """Integration tests for the learning system."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a fresh DecisionStore for each test."""
        return DecisionStore(storage_path=tmp_path / "decisions.jsonl")

    def test_full_feedback_loop(self, store):
        """Test the complete feedback loop: record → stats → adjust."""
        adaptive = AdaptiveConfidence(store)

        # Initial state: no adjustment
        initial = adaptive.adjust_confidence(
            base=FixConfidence.HIGH,
            fix_type="refactor",
        )
        assert initial == FixConfidence.HIGH

        # Simulate user rejecting 8 out of 10 refactor fixes
        for i in range(2):
            store.record(
                FixDecision(
                    id=f"dec-ok-{i}.0",
                    fix_id=f"ok{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                )
            )
        for i in range(8):
            store.record(
                FixDecision(
                    id=f"dec-bad-{i}.0",
                    fix_id=f"bad{i}",
                    decision=UserDecision.REJECTED,
                    rejection_reason=RejectionReason.STYLE_MISMATCH,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                )
            )

        # Now the system should downgrade confidence
        adjusted = adaptive.adjust_confidence(
            base=FixConfidence.HIGH,
            fix_type="refactor",
        )
        assert adjusted == FixConfidence.MEDIUM

        # And provide helpful prompt adjustments
        prompt_adjustments = adaptive.get_prompt_adjustments()
        assert "Historical Feedback" in prompt_adjustments
        assert "style" in prompt_adjustments.lower() or "refactor" in prompt_adjustments.lower()

    def test_trend_calculation(self, store):
        """Test trend calculation over time."""
        now = datetime.utcnow()

        # First half: mostly rejected (30% approval)
        for i in range(3):
            store.record(
                FixDecision(
                    id=f"dec-old-ok-{i}.0",
                    fix_id=f"old-ok{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                    timestamp=now - timedelta(days=30),
                )
            )
        for i in range(7):
            store.record(
                FixDecision(
                    id=f"dec-old-bad-{i}.0",
                    fix_id=f"old-bad{i}",
                    decision=UserDecision.REJECTED,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                    timestamp=now - timedelta(days=30),
                )
            )

        # Second half: mostly approved (90% approval)
        for i in range(9):
            store.record(
                FixDecision(
                    id=f"dec-new-ok-{i}.0",
                    fix_id=f"new-ok{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                    timestamp=now - timedelta(days=1),
                )
            )
        store.record(
            FixDecision(
                id="dec-new-bad-0.0",
                fix_id="new-bad0",
                decision=UserDecision.REJECTED,
                fix_type="refactor",
                confidence="HIGH",
                finding_type="Complex",
                file_path="a.py",
                repository="/test",
                timestamp=now - timedelta(days=1),
            )
        )

        stats = store.get_stats()
        assert stats.trend == "improving"

    def test_modified_decisions_count_as_approval(self, store):
        """Test that MODIFIED decisions are counted as approvals."""
        # Add 10 decisions: 5 approved, 3 modified, 2 rejected
        for i in range(5):
            store.record(
                FixDecision(
                    id=f"dec-app-{i}.0",
                    fix_id=f"app{i}",
                    decision=UserDecision.APPROVED,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                )
            )
        for i in range(3):
            store.record(
                FixDecision(
                    id=f"dec-mod-{i}.0",
                    fix_id=f"mod{i}",
                    decision=UserDecision.MODIFIED,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                )
            )
        for i in range(2):
            store.record(
                FixDecision(
                    id=f"dec-rej-{i}.0",
                    fix_id=f"rej{i}",
                    decision=UserDecision.REJECTED,
                    fix_type="refactor",
                    confidence="HIGH",
                    finding_type="Complex",
                    file_path="a.py",
                    repository="/test",
                )
            )

        stats = store.get_stats()
        # 8 out of 10 should be counted as approved (5 approved + 3 modified)
        assert stats.approval_rate == pytest.approx(0.8, rel=1e-2)
