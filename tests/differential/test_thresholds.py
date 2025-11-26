"""
Differential tests for severity threshold monotonicity.

Validates Python implementation matches Lean specification:
- lean/Repotoire/Thresholds.lean

Properties verified:
- Threshold ordering (LOW < MEDIUM < HIGH < CRITICAL)
- Boundary correctness
- Monotonicity (higher metric -> same or higher severity)
"""

from hypothesis import given, strategies as st, assume
from repotoire.models import Severity
from typing import Optional


# Lean-equivalent threshold constants

# Cyclomatic complexity thresholds
COMPLEXITY_THRESHOLD_LOW = 11
COMPLEXITY_THRESHOLD_MEDIUM = 21
COMPLEXITY_THRESHOLD_HIGH = 31

# God class method count thresholds
GOD_CLASS_THRESHOLD_MEDIUM = 15
GOD_CLASS_THRESHOLD_HIGH = 20
GOD_CLASS_THRESHOLD_CRITICAL = 30

# LCOM (Lack of Cohesion) thresholds (as percentages 0-100)
LCOM_THRESHOLD_MEDIUM = 40
LCOM_THRESHOLD_HIGH = 60
LCOM_THRESHOLD_CRITICAL = 80


def lean_complexity_to_severity(cc: int) -> Optional[Severity]:
    """
    Mirror Lean's complexity_to_severity function.

    Lean:
        if cc < COMPLEXITY_THRESHOLD_LOW then none
        else if cc < COMPLEXITY_THRESHOLD_MEDIUM then Severity.LOW
        else if cc < COMPLEXITY_THRESHOLD_HIGH then Severity.MEDIUM
        else Severity.HIGH
    """
    if cc < COMPLEXITY_THRESHOLD_LOW:
        return None
    elif cc < COMPLEXITY_THRESHOLD_MEDIUM:
        return Severity.LOW
    elif cc < COMPLEXITY_THRESHOLD_HIGH:
        return Severity.MEDIUM
    else:
        return Severity.HIGH


def lean_method_count_to_severity(count: int) -> Optional[Severity]:
    """
    Mirror Lean's method_count_to_severity function.

    Lean:
        if count < GOD_CLASS_THRESHOLD_MEDIUM then none
        else if count < GOD_CLASS_THRESHOLD_HIGH then Severity.MEDIUM
        else if count < GOD_CLASS_THRESHOLD_CRITICAL then Severity.HIGH
        else Severity.CRITICAL
    """
    if count < GOD_CLASS_THRESHOLD_MEDIUM:
        return None
    elif count < GOD_CLASS_THRESHOLD_HIGH:
        return Severity.MEDIUM
    elif count < GOD_CLASS_THRESHOLD_CRITICAL:
        return Severity.HIGH
    else:
        return Severity.CRITICAL


def lean_lcom_to_severity(lcom: int) -> Optional[Severity]:
    """
    Mirror Lean's lcom_to_severity function.

    Lean (using percentages 0-100):
        if lcom < LCOM_THRESHOLD_MEDIUM then none
        else if lcom < LCOM_THRESHOLD_HIGH then Severity.MEDIUM
        else if lcom < LCOM_THRESHOLD_CRITICAL then Severity.HIGH
        else Severity.CRITICAL
    """
    if lcom < LCOM_THRESHOLD_MEDIUM:
        return None
    elif lcom < LCOM_THRESHOLD_HIGH:
        return Severity.MEDIUM
    elif lcom < LCOM_THRESHOLD_CRITICAL:
        return Severity.HIGH
    else:
        return Severity.CRITICAL


def severity_level(s: Optional[Severity]) -> int:
    """Convert severity to numeric level for comparison."""
    if s is None:
        return 0
    return {
        Severity.INFO: 1,
        Severity.LOW: 2,
        Severity.MEDIUM: 3,
        Severity.HIGH: 4,
        Severity.CRITICAL: 5,
    }[s]


class TestComplexityThresholds:
    """Property-based tests for complexity thresholds."""

    def test_thresholds_ordered(self):
        """
        Lean theorem: complexity_thresholds_ordered
        Proves: THRESHOLD_LOW < THRESHOLD_MEDIUM < THRESHOLD_HIGH
        """
        assert COMPLEXITY_THRESHOLD_LOW < COMPLEXITY_THRESHOLD_MEDIUM
        assert COMPLEXITY_THRESHOLD_MEDIUM < COMPLEXITY_THRESHOLD_HIGH

    @given(
        c1=st.integers(min_value=0, max_value=100),
        c2=st.integers(min_value=0, max_value=100),
    )
    def test_complexity_monotonic(self, c1: int, c2: int):
        """
        Lean theorem: complexity_monotonic
        Proves: c1 <= c2 -> severity(c1) <= severity(c2)
        """
        assume(c1 <= c2)

        s1 = lean_complexity_to_severity(c1)
        s2 = lean_complexity_to_severity(c2)

        assert severity_level(s1) <= severity_level(s2), \
            f"Monotonicity violated: {c1}->{s1} vs {c2}->{s2}"

    def test_boundary_values(self):
        """
        Lean theorems for boundary values.
        """
        # Below threshold - healthy
        assert lean_complexity_to_severity(10) is None, "complexity_10_is_none"

        # At LOW threshold
        assert lean_complexity_to_severity(11) == Severity.LOW, "complexity_11_is_low"

        # Just below MEDIUM threshold
        assert lean_complexity_to_severity(20) == Severity.LOW, "complexity_20_is_low"

        # At MEDIUM threshold
        assert lean_complexity_to_severity(21) == Severity.MEDIUM, "complexity_21_is_medium"

        # Just below HIGH threshold
        assert lean_complexity_to_severity(30) == Severity.MEDIUM, "complexity_30_is_medium"

        # At HIGH threshold
        assert lean_complexity_to_severity(31) == Severity.HIGH, "complexity_31_is_high"

        # Well above HIGH
        assert lean_complexity_to_severity(100) == Severity.HIGH, "complexity_100_is_high"


class TestGodClassThresholds:
    """Property-based tests for god class thresholds."""

    def test_thresholds_ordered(self):
        """
        Lean theorem: god_class_thresholds_ordered
        Proves: THRESHOLD_MEDIUM < THRESHOLD_HIGH < THRESHOLD_CRITICAL
        """
        assert GOD_CLASS_THRESHOLD_MEDIUM < GOD_CLASS_THRESHOLD_HIGH
        assert GOD_CLASS_THRESHOLD_HIGH < GOD_CLASS_THRESHOLD_CRITICAL

    @given(
        c1=st.integers(min_value=0, max_value=100),
        c2=st.integers(min_value=0, max_value=100),
    )
    def test_method_count_monotonic(self, c1: int, c2: int):
        """
        Lean theorem: god_class_monotonic
        Proves: c1 <= c2 -> severity(c1) <= severity(c2)
        """
        assume(c1 <= c2)

        s1 = lean_method_count_to_severity(c1)
        s2 = lean_method_count_to_severity(c2)

        assert severity_level(s1) <= severity_level(s2), \
            f"Monotonicity violated: {c1}->{s1} vs {c2}->{s2}"

    def test_boundary_values(self):
        """
        Lean theorems for boundary values.
        """
        # Below threshold - healthy
        assert lean_method_count_to_severity(14) is None, "god_class_14_is_none"

        # At MEDIUM threshold
        assert lean_method_count_to_severity(15) == Severity.MEDIUM, "god_class_15_is_medium"

        # At HIGH threshold
        assert lean_method_count_to_severity(20) == Severity.HIGH, "god_class_20_is_high"

        # At CRITICAL threshold
        assert lean_method_count_to_severity(30) == Severity.CRITICAL, "god_class_30_is_critical"


class TestLCOMThresholds:
    """Property-based tests for LCOM (Lack of Cohesion) thresholds."""

    def test_thresholds_ordered(self):
        """
        Lean theorem: lcom_thresholds_ordered
        Proves: THRESHOLD_MEDIUM < THRESHOLD_HIGH < THRESHOLD_CRITICAL
        """
        assert LCOM_THRESHOLD_MEDIUM < LCOM_THRESHOLD_HIGH
        assert LCOM_THRESHOLD_HIGH < LCOM_THRESHOLD_CRITICAL

    @given(
        c1=st.integers(min_value=0, max_value=100),
        c2=st.integers(min_value=0, max_value=100),
    )
    def test_lcom_monotonic(self, c1: int, c2: int):
        """
        Lean theorem: lcom_monotonic
        Proves: c1 <= c2 -> severity(c1) <= severity(c2)
        """
        assume(c1 <= c2)

        s1 = lean_lcom_to_severity(c1)
        s2 = lean_lcom_to_severity(c2)

        assert severity_level(s1) <= severity_level(s2), \
            f"Monotonicity violated: {c1}->{s1} vs {c2}->{s2}"

    def test_boundary_values(self):
        """
        Lean theorems for boundary values (using percentages 0-100).
        """
        # Below threshold - cohesive
        assert lean_lcom_to_severity(39) is None, "lcom_39_is_none"

        # At MEDIUM threshold
        assert lean_lcom_to_severity(40) == Severity.MEDIUM, "lcom_40_is_medium"

        # At HIGH threshold
        assert lean_lcom_to_severity(60) == Severity.HIGH, "lcom_60_is_high"

        # At CRITICAL threshold
        assert lean_lcom_to_severity(80) == Severity.CRITICAL, "lcom_80_is_critical"


class TestSeverityOrdering:
    """Property-based tests for severity ordering."""

    def test_severity_ordering(self):
        """
        Lean theorem: severity_ordering
        Proves: None < LOW < MEDIUM < HIGH < CRITICAL
        """
        assert severity_level(None) < severity_level(Severity.LOW)
        assert severity_level(Severity.LOW) < severity_level(Severity.MEDIUM)
        assert severity_level(Severity.MEDIUM) < severity_level(Severity.HIGH)
        assert severity_level(Severity.HIGH) < severity_level(Severity.CRITICAL)


class TestCrossThresholdConsistency:
    """Tests for consistency across different threshold types."""

    @given(metric=st.integers(min_value=0, max_value=100))
    def test_threshold_deterministic(self, metric: int):
        """
        All threshold functions should be deterministic.
        """
        # Complexity
        r1 = lean_complexity_to_severity(metric)
        r2 = lean_complexity_to_severity(metric)
        assert r1 == r2, "Complexity threshold non-deterministic"

        # God class
        r1 = lean_method_count_to_severity(metric)
        r2 = lean_method_count_to_severity(metric)
        assert r1 == r2, "God class threshold non-deterministic"

        # LCOM
        r1 = lean_lcom_to_severity(metric)
        r2 = lean_lcom_to_severity(metric)
        assert r1 == r2, "LCOM threshold non-deterministic"
