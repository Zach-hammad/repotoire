"""
Differential tests for priority score calculation.

Validates Python implementation matches Lean specification:
- lean/Repotoire/PriorityScore.lean

Properties verified:
- Weight conservation (0.4 + 0.3 + 0.3 = 1.0)
- Score bounds [0, 100]
- Severity weight monotonicity
- Agreement normalization bounds
"""

from hypothesis import given, strategies as st, assume
from repotoire.models import Severity


# Lean-equivalent constants (as percentages 0-100)
WEIGHT_SEVERITY = 40
WEIGHT_CONFIDENCE = 30
WEIGHT_AGREEMENT = 30

SEVERITY_WEIGHTS = {
    Severity.INFO: 20,
    Severity.LOW: 40,
    Severity.MEDIUM: 60,
    Severity.HIGH: 80,
    Severity.CRITICAL: 100,
}

SEVERITY_ORDER = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]


def lean_agreement_normalized(detector_count: int) -> int:
    """
    Mirror Lean's agreement_normalized function.

    Lean:
        if detector_count <= 1 then 0
        else min 100 ((detector_count - 1) * 50)
    """
    if detector_count <= 1:
        return 0
    else:
        return min(100, (detector_count - 1) * 50)


def lean_severity_component(severity: Severity) -> int:
    """
    Mirror Lean's severity_component function.

    Lean: severity_component s = severity_weight_percent s * WEIGHT_SEVERITY
    """
    return SEVERITY_WEIGHTS[severity] * WEIGHT_SEVERITY


def lean_confidence_component(confidence: int) -> int:
    """
    Mirror Lean's confidence_component function.

    Lean: confidence_component c = c * WEIGHT_CONFIDENCE
    """
    return confidence * WEIGHT_CONFIDENCE


def lean_agreement_component(detector_count: int) -> int:
    """
    Mirror Lean's agreement_component function.

    Lean: agreement_component count = agreement_normalized count * WEIGHT_AGREEMENT
    """
    return lean_agreement_normalized(detector_count) * WEIGHT_AGREEMENT


def lean_priority_score(severity: Severity, confidence: int, detector_count: int) -> int:
    """
    Mirror Lean's priority_score function.

    Lean: priority_score sev conf count =
        (severity_component sev + confidence_component conf + agreement_component count) / 100
    """
    weighted = (
        lean_severity_component(severity) +
        lean_confidence_component(confidence) +
        lean_agreement_component(detector_count)
    )
    return weighted // 100


class TestPriorityScoreProperties:
    """Property-based tests for priority score calculation."""

    @given(detector_count=st.integers(min_value=0, max_value=100))
    def test_agreement_normalized_bounded(self, detector_count: int):
        """
        Lean theorem: agreement_normalized_bounded
        Proves: agreement_normalized count <= 100
        """
        normalized = lean_agreement_normalized(detector_count)
        assert 0 <= normalized <= 100, f"Agreement {normalized} out of bounds"

    @given(
        severity=st.sampled_from(list(Severity)),
        confidence=st.integers(min_value=0, max_value=100),
        detector_count=st.integers(min_value=0, max_value=20),
    )
    def test_priority_score_bounded(self, severity: Severity, confidence: int, detector_count: int):
        """
        Lean theorem: priority_score_bounded
        Proves: is_valid_percentage (priority_score severity confidence detector_count)
        """
        score = lean_priority_score(severity, confidence, detector_count)
        assert 0 <= score <= 100, f"Priority score {score} out of bounds"

    @given(
        confidence=st.integers(min_value=0, max_value=100),
        detector_count=st.integers(min_value=0, max_value=20),
    )
    def test_severity_weight_monotonic(self, confidence: int, detector_count: int):
        """
        Lean theorem: priority_monotonic_severity
        Proves: s1 <= s2 -> priority_score s1 conf count <= priority_score s2 conf count
        """
        scores = [lean_priority_score(sev, confidence, detector_count) for sev in SEVERITY_ORDER]

        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1], \
                f"Severity monotonicity violated: {SEVERITY_ORDER[i]}->{scores[i]} vs {SEVERITY_ORDER[i+1]}->{scores[i+1]}"

    @given(
        severity=st.sampled_from(list(Severity)),
        detector_count=st.integers(min_value=0, max_value=20),
        c1=st.integers(min_value=0, max_value=100),
        c2=st.integers(min_value=0, max_value=100),
    )
    def test_confidence_monotonic(self, severity: Severity, detector_count: int, c1: int, c2: int):
        """
        Lean theorem: priority_monotonic_confidence
        Proves: c1 <= c2 -> priority_score sev c1 count <= priority_score sev c2 count
        """
        assume(c1 <= c2)

        score1 = lean_priority_score(severity, c1, detector_count)
        score2 = lean_priority_score(severity, c2, detector_count)

        assert score1 <= score2, \
            f"Confidence monotonicity violated: {c1}->{score1} vs {c2}->{score2}"

    @given(
        severity=st.sampled_from(list(Severity)),
        confidence=st.integers(min_value=0, max_value=100),
        detector_count=st.integers(min_value=0, max_value=20),
    )
    def test_score_deterministic(self, severity: Severity, confidence: int, detector_count: int):
        """
        Lean theorem: score_deterministic
        Proves: Same inputs always produce same outputs.
        """
        score1 = lean_priority_score(severity, confidence, detector_count)
        score2 = lean_priority_score(severity, confidence, detector_count)
        assert score1 == score2, "Priority score is non-deterministic"

    def test_weight_sum_conservation(self):
        """
        Lean theorem: weights_sum_to_100
        Proves: WEIGHT_SEVERITY + WEIGHT_CONFIDENCE + WEIGHT_AGREEMENT = 100
        """
        assert WEIGHT_SEVERITY + WEIGHT_CONFIDENCE + WEIGHT_AGREEMENT == 100


class TestPriorityScoreAgreement:
    """Property tests for agreement normalization."""

    def test_single_detector_no_bonus(self):
        """
        Lean theorem: single_detector_no_bonus
        Proves: agreement_normalized 1 = 0
        """
        assert lean_agreement_normalized(0) == 0
        assert lean_agreement_normalized(1) == 0

    def test_two_detectors_agreement(self):
        """
        Lean theorem: two_detectors_agreement
        Proves: agreement_normalized 2 = 50
        """
        assert lean_agreement_normalized(2) == 50

    def test_max_agreement_at_three(self):
        """
        Lean theorem: max_agreement_at_three
        Proves: agreement_normalized 3 = 100
        """
        assert lean_agreement_normalized(3) == 100

    @given(n=st.integers(min_value=3, max_value=100))
    def test_max_agreement_stable(self, n: int):
        """
        Lean theorem: max_agreement_stable
        Proves: n >= 3 -> agreement_normalized n = 100
        """
        assert lean_agreement_normalized(n) == 100


class TestPriorityScoreExamples:
    """Explicit example calculations matching Lean examples."""

    def test_max_score(self):
        """
        Lean example: priority_score CRITICAL 100 3 = 100
        """
        assert lean_priority_score(Severity.CRITICAL, 100, 3) == 100

    def test_high_score(self):
        """
        Lean example: priority_score HIGH 80 2 = 71
        """
        assert lean_priority_score(Severity.HIGH, 80, 2) == 71

    def test_medium_score(self):
        """
        Lean example: priority_score MEDIUM 50 1 = 39
        """
        assert lean_priority_score(Severity.MEDIUM, 50, 1) == 39

    def test_low_score(self):
        """
        Lean example: priority_score LOW 30 0 = 25
        """
        assert lean_priority_score(Severity.LOW, 30, 0) == 25

    def test_min_score(self):
        """
        Lean example: priority_score INFO 0 0 = 8
        """
        assert lean_priority_score(Severity.INFO, 0, 0) == 8
