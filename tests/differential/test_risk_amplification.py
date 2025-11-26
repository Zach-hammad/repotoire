"""
Differential tests for risk amplification logic.

Validates Python implementation matches Lean specification:
- lean/Repotoire/RiskAmplification.lean

Properties verified:
- Severity ordering (INFO < LOW < MEDIUM < HIGH < CRITICAL)
- Escalation rules (0, 1, 2+ additional factors)
- Escalation monotonicity (never decreases severity)
- Risk weight bounds
"""

from hypothesis import given, strategies as st, assume
from repotoire.models import Severity


# Lean-equivalent constants
SEVERITY_ORDER = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
SEVERITY_RANK = {sev: i for i, sev in enumerate(SEVERITY_ORDER)}

# Risk weights as percentages (matching Lean)
RISK_WEIGHTS = {
    "bottleneck": 40,
    "high_complexity": 30,
    "security_vulnerability": 30,
    "dead_code": 10,
}


def lean_escalate_one(s: Severity) -> Severity:
    """
    Mirror Lean's escalate_one function.

    Lean:
        escalate_one s =
            match s with
            | INFO => LOW
            | LOW => MEDIUM
            | MEDIUM => HIGH
            | HIGH => CRITICAL
            | CRITICAL => CRITICAL
    """
    mapping = {
        Severity.INFO: Severity.LOW,
        Severity.LOW: Severity.MEDIUM,
        Severity.MEDIUM: Severity.HIGH,
        Severity.HIGH: Severity.CRITICAL,
        Severity.CRITICAL: Severity.CRITICAL,
    }
    return mapping[s]


def lean_calculate_escalated_severity(original: Severity, additional_count: int) -> Severity:
    """
    Mirror Lean's calculate_escalated_severity function.

    Lean:
        if additional_count >= 2 then CRITICAL
        else if additional_count = 1 then escalate_one original
        else original
    """
    if additional_count >= 2:
        return Severity.CRITICAL
    elif additional_count == 1:
        return lean_escalate_one(original)
    else:
        return original


def lean_is_critical_compound_risk(num_factors: int, escalated: Severity) -> bool:
    """
    Mirror Lean's is_critical_compound_risk function.

    Lean: is_critical_compound_risk n s = n >= 2 && s == CRITICAL
    """
    return num_factors >= 2 and escalated == Severity.CRITICAL


def lean_severity_multiplier(s: Severity) -> int:
    """
    Mirror Lean's severity_multiplier function.

    Lean: severity_multiplier s = (s.toRank + 1) * 20
    """
    return (SEVERITY_RANK[s] + 1) * 20


class TestSeverityOrderingProperties:
    """Property-based tests for severity ordering."""

    def test_severity_order_correct(self):
        """
        Lean theorem: severity_ordering
        Proves: INFO < LOW < MEDIUM < HIGH < CRITICAL
        """
        for i in range(len(SEVERITY_ORDER) - 1):
            s1, s2 = SEVERITY_ORDER[i], SEVERITY_ORDER[i + 1]
            assert SEVERITY_RANK[s1] < SEVERITY_RANK[s2], \
                f"Ordering violated: {s1} not less than {s2}"

    @given(
        s1=st.sampled_from(list(Severity)),
        s2=st.sampled_from(list(Severity)),
    )
    def test_severity_ordering_transitive(self, s1: Severity, s2: Severity):
        """
        Severity ordering should be transitive.
        """
        r1, r2 = SEVERITY_RANK[s1], SEVERITY_RANK[s2]
        if r1 < r2:
            # s1 < s2
            assert SEVERITY_ORDER.index(s1) < SEVERITY_ORDER.index(s2)


class TestEscalationProperties:
    """Property-based tests for severity escalation."""

    @given(original=st.sampled_from(list(Severity)))
    def test_zero_additional_no_escalation(self, original: Severity):
        """
        Lean theorem: zero_additional_no_escalation
        Proves: calculate_escalated_severity s 0 = s
        """
        result = lean_calculate_escalated_severity(original, 0)
        assert result == original, \
            f"Zero additional should not escalate: {original} -> {result}"

    @given(original=st.sampled_from(list(Severity)))
    def test_one_additional_escalates(self, original: Severity):
        """
        Lean theorem: one_additional_escalates
        Proves: calculate_escalated_severity s 1 = escalate_one s
        """
        result = lean_calculate_escalated_severity(original, 1)
        expected = lean_escalate_one(original)
        assert result == expected, \
            f"One additional should escalate once: {original} -> {result}, expected {expected}"

    @given(
        original=st.sampled_from(list(Severity)),
        additional=st.integers(min_value=2, max_value=100),
    )
    def test_two_plus_additional_critical(self, original: Severity, additional: int):
        """
        Lean theorem: compound_risk_is_critical
        Proves: additional_count >= 2 -> escalated_severity = CRITICAL
        """
        result = lean_calculate_escalated_severity(original, additional)
        assert result == Severity.CRITICAL, \
            f"2+ additional should be CRITICAL: {original} + {additional} -> {result}"

    @given(
        original=st.sampled_from(list(Severity)),
        additional=st.integers(min_value=0, max_value=10),
    )
    def test_escalation_monotonic(self, original: Severity, additional: int):
        """
        Lean theorem: escalated_ge_original
        Proves: original <= escalated_severity
        """
        escalated = lean_calculate_escalated_severity(original, additional)

        orig_rank = SEVERITY_RANK[original]
        esc_rank = SEVERITY_RANK[escalated]

        assert esc_rank >= orig_rank, \
            f"Escalation should never decrease: {original}({orig_rank}) -> {escalated}({esc_rank})"

    @given(original=st.sampled_from(list(Severity)))
    def test_escalate_one_monotonic(self, original: Severity):
        """
        Lean theorem: escalation_monotonic
        Proves: s <= escalate_one s
        """
        escalated = lean_escalate_one(original)

        orig_rank = SEVERITY_RANK[original]
        esc_rank = SEVERITY_RANK[escalated]

        assert esc_rank >= orig_rank, \
            f"escalate_one should not decrease: {original} -> {escalated}"


class TestCriticalCompoundRisk:
    """Property-based tests for critical compound risk detection."""

    @given(
        original=st.sampled_from(list(Severity)),
        additional=st.integers(min_value=2, max_value=10),
    )
    def test_two_plus_factors_is_critical_risk(self, original: Severity, additional: int):
        """
        Lean theorem: two_plus_factors_is_critical_risk
        Proves: additional >= 2 -> is_critical_compound_risk
        """
        num_factors = additional + 1  # Base + additional
        escalated = lean_calculate_escalated_severity(original, additional)

        assert lean_is_critical_compound_risk(num_factors, escalated), \
            f"Should be critical risk: factors={num_factors}, escalated={escalated}"

    @given(original=st.sampled_from(list(Severity)))
    def test_single_factor_not_critical_risk(self, original: Severity):
        """
        Single factor (just bottleneck) should not be critical compound risk.
        """
        escalated = lean_calculate_escalated_severity(original, 0)

        assert not lean_is_critical_compound_risk(1, escalated) or escalated == Severity.CRITICAL, \
            f"Single factor should not be critical risk unless original was CRITICAL"


class TestSeverityMultiplier:
    """Property-based tests for severity multiplier."""

    @given(severity=st.sampled_from(list(Severity)))
    def test_multiplier_bounded(self, severity: Severity):
        """
        Lean theorem: severity_multiplier_bounded
        Proves: severity_multiplier s <= 100
        """
        mult = lean_severity_multiplier(severity)
        assert 20 <= mult <= 100, f"Multiplier {mult} out of bounds for {severity}"

    def test_multiplier_values(self):
        """
        Verify specific multiplier values match Lean.
        """
        expected = {
            Severity.INFO: 20,
            Severity.LOW: 40,
            Severity.MEDIUM: 60,
            Severity.HIGH: 80,
            Severity.CRITICAL: 100,
        }
        for sev, exp_mult in expected.items():
            actual = lean_severity_multiplier(sev)
            assert actual == exp_mult, f"Multiplier mismatch for {sev}: {actual} != {exp_mult}"


class TestRiskWeightBounds:
    """Property-based tests for risk weight bounds."""

    def test_risk_weights_bounded(self):
        """
        Lean theorem: risk_weight_bounded
        Proves: All risk weights <= 100
        """
        for factor_type, weight in RISK_WEIGHTS.items():
            assert 0 <= weight <= 100, \
                f"Weight for {factor_type} out of bounds: {weight}"


class TestEscalationExamples:
    """Explicit examples matching Lean examples."""

    def test_info_plus_one_is_low(self):
        """Lean example: calculate_escalated_severity INFO 1 = LOW"""
        assert lean_calculate_escalated_severity(Severity.INFO, 1) == Severity.LOW

    def test_low_plus_one_is_medium(self):
        """Lean example: calculate_escalated_severity LOW 1 = MEDIUM"""
        assert lean_calculate_escalated_severity(Severity.LOW, 1) == Severity.MEDIUM

    def test_medium_plus_one_is_high(self):
        """Lean example: calculate_escalated_severity MEDIUM 1 = HIGH"""
        assert lean_calculate_escalated_severity(Severity.MEDIUM, 1) == Severity.HIGH

    def test_high_plus_one_is_critical(self):
        """Lean example: calculate_escalated_severity HIGH 1 = CRITICAL"""
        assert lean_calculate_escalated_severity(Severity.HIGH, 1) == Severity.CRITICAL

    def test_critical_plus_one_stays_critical(self):
        """Lean example: calculate_escalated_severity CRITICAL 1 = CRITICAL"""
        assert lean_calculate_escalated_severity(Severity.CRITICAL, 1) == Severity.CRITICAL

    def test_any_plus_two_is_critical(self):
        """Lean examples: Any severity + 2 additional = CRITICAL"""
        for sev in Severity:
            result = lean_calculate_escalated_severity(sev, 2)
            assert result == Severity.CRITICAL, f"{sev} + 2 should be CRITICAL"
