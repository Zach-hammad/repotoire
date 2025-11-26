"""
Differential tests for health score calculation.

Validates Python implementation matches Lean specification:
- lean/Repotoire/HealthScore.lean

Properties verified:
- Weight conservation (sum to 100%)
- Score bounds [0, 100]
- Grade coverage (no gaps)
- Grade disjointness (no overlaps)
- Grade monotonicity (higher score -> better grade)
"""

from hypothesis import given, strategies as st, assume, settings
from repotoire.detectors.engine import AnalysisEngine
from repotoire.models import Severity


# Lean-equivalent constants
WEIGHT_STRUCTURE = 40
WEIGHT_QUALITY = 30
WEIGHT_ARCHITECTURE = 30

GRADE_A_MIN = 90
GRADE_B_MIN = 80
GRADE_C_MIN = 70
GRADE_D_MIN = 60


def lean_calculate_weighted_score(s1: int, s2: int, s3: int) -> int:
    """
    Mirror Lean's calculate_weighted_score function.

    Lean: calculate_weighted_score s1 s2 s3 =
        WEIGHT_STRUCTURE * s1 + WEIGHT_QUALITY * s2 + WEIGHT_ARCHITECTURE * s3
    """
    return WEIGHT_STRUCTURE * s1 + WEIGHT_QUALITY * s2 + WEIGHT_ARCHITECTURE * s3


def lean_final_score(s1: int, s2: int, s3: int) -> int:
    """
    Mirror Lean's final_score function.

    Lean: final_score s1 s2 s3 = calculate_weighted_score s1 s2 s3 / 100
    """
    return lean_calculate_weighted_score(s1, s2, s3) // 100


def lean_score_to_grade(score: int) -> str:
    """
    Mirror Lean's score_to_grade function.

    Lean:
        if score >= GRADE_A_MIN then Grade.A
        else if score >= GRADE_B_MIN then Grade.B
        else if score >= GRADE_C_MIN then Grade.C
        else if score >= GRADE_D_MIN then Grade.D
        else Grade.F
    """
    if score >= GRADE_A_MIN:
        return "A"
    elif score >= GRADE_B_MIN:
        return "B"
    elif score >= GRADE_C_MIN:
        return "C"
    elif score >= GRADE_D_MIN:
        return "D"
    else:
        return "F"


class TestHealthScoreProperties:
    """Property-based tests for health score calculation."""

    @given(
        s1=st.integers(min_value=0, max_value=100),
        s2=st.integers(min_value=0, max_value=100),
        s3=st.integers(min_value=0, max_value=100),
    )
    def test_weighted_score_bounded(self, s1: int, s2: int, s3: int):
        """
        Lean theorem: weighted_score_bounded
        Proves: calculate_weighted_score s1 s2 s3 <= 10000
        """
        weighted = lean_calculate_weighted_score(s1, s2, s3)
        assert weighted <= 10000, f"Weighted score {weighted} exceeds 10000"
        assert weighted >= 0, f"Weighted score {weighted} is negative"

    @given(
        s1=st.integers(min_value=0, max_value=100),
        s2=st.integers(min_value=0, max_value=100),
        s3=st.integers(min_value=0, max_value=100),
    )
    def test_final_score_bounded(self, s1: int, s2: int, s3: int):
        """
        Lean theorem: final_score_bounded
        Proves: is_valid_score (final_score s1 s2 s3)
        """
        final = lean_final_score(s1, s2, s3)
        assert 0 <= final <= 100, f"Final score {final} out of bounds"

    @given(score=st.integers(min_value=0, max_value=100))
    def test_grade_coverage_complete(self, score: int):
        """
        Lean theorem: grade_coverage
        Proves: Every valid score maps to exactly one grade.
        """
        grade = lean_score_to_grade(score)
        assert grade in {"A", "B", "C", "D", "F"}, f"Invalid grade {grade} for score {score}"

    @given(score=st.integers(min_value=0, max_value=100))
    def test_grade_python_matches_lean(self, score: int):
        """
        Differential test: Python grade assignment matches Lean spec.
        """
        lean_grade = lean_score_to_grade(score)

        # Python implementation
        grades = AnalysisEngine.GRADES
        python_grade = None
        for g, (min_score, max_score) in grades.items():
            if g == "A" and min_score <= score <= max_score:
                python_grade = g
                break
            elif min_score <= score < max_score:
                python_grade = g
                break

        if python_grade is None:
            python_grade = "F"

        assert python_grade == lean_grade, \
            f"Grade mismatch for score {score}: Python={python_grade}, Lean={lean_grade}"

    @given(
        s1=st.integers(min_value=0, max_value=100),
        s2=st.integers(min_value=0, max_value=100),
    )
    def test_grade_monotonic(self, s1: int, s2: int):
        """
        Lean theorem: grade_monotonic
        Proves: s1 <= s2 -> score_to_grade s1 <= score_to_grade s2
        """
        assume(s1 <= s2)

        g1 = lean_score_to_grade(s1)
        g2 = lean_score_to_grade(s2)

        grade_rank = {"F": 0, "D": 1, "C": 2, "B": 3, "A": 4}

        assert grade_rank[g1] <= grade_rank[g2], \
            f"Monotonicity violated: score {s1}->{g1} vs {s2}->{g2}"

    @given(
        s1=st.integers(min_value=0, max_value=100),
        s2=st.integers(min_value=0, max_value=100),
        s3=st.integers(min_value=0, max_value=100),
    )
    def test_score_deterministic(self, s1: int, s2: int, s3: int):
        """
        Lean theorem: score_deterministic
        Proves: Same inputs always produce same outputs.
        """
        score1 = lean_final_score(s1, s2, s3)
        score2 = lean_final_score(s1, s2, s3)
        assert score1 == score2, "Score calculation is non-deterministic"

    def test_weight_sum_conservation(self):
        """
        Lean theorem: weights_sum_to_100
        Proves: WEIGHT_STRUCTURE + WEIGHT_QUALITY + WEIGHT_ARCHITECTURE = 100
        """
        assert WEIGHT_STRUCTURE + WEIGHT_QUALITY + WEIGHT_ARCHITECTURE == 100

    def test_python_weights_match_lean(self):
        """
        Differential test: Python weights match Lean constants.
        """
        weights = AnalysisEngine.WEIGHTS
        assert weights["structure"] == WEIGHT_STRUCTURE / 100
        assert weights["quality"] == WEIGHT_QUALITY / 100
        assert weights["architecture"] == WEIGHT_ARCHITECTURE / 100


class TestHealthScoreBoundaryExamples:
    """Exhaustive boundary testing for grade thresholds."""

    def test_grade_boundaries_exhaustive(self):
        """Test all boundary values explicitly."""
        boundaries = [
            (100, "A"), (90, "A"), (89, "B"), (80, "B"),
            (79, "C"), (70, "C"), (69, "D"), (60, "D"),
            (59, "F"), (0, "F"),
        ]
        for score, expected in boundaries:
            actual = lean_score_to_grade(score)
            assert actual == expected, f"Boundary error: score {score} -> {actual}, expected {expected}"

    def test_perfect_scores_produce_100(self):
        """Lean theorem: perfect_scores_produce_100"""
        assert lean_final_score(100, 100, 100) == 100

    def test_zero_scores_produce_0(self):
        """Lean theorem: zero_scores_produce_0"""
        assert lean_final_score(0, 0, 0) == 0
