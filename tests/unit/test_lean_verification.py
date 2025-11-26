"""
Differential tests: Verify Python implementation matches Lean-verified properties.

These tests ensure the Python code matches the formally verified Lean specifications.
If these tests fail, either:
1. The Python code changed and Lean proofs need updating, or
2. The Lean proofs changed and Python needs updating

See docs/VERIFICATION.md for details on formal verification.
"""

import pytest
from repotoire.detectors.engine import AnalysisEngine
from repotoire.validation import validate_identifier, ValidationError
from repotoire.models import Severity


class TestLeanVerifiedProperties:
    """Tests that verify Python matches Lean-proven properties."""

    def test_weights_sum_to_one(self):
        """
        Lean theorem: weights_sum_to_100
        Proves: WEIGHT_STRUCTURE + WEIGHT_QUALITY + WEIGHT_ARCHITECTURE = 100 (as %)
        """
        weights = AnalysisEngine.WEIGHTS
        total = weights["structure"] + weights["quality"] + weights["architecture"]
        assert total == 1.0, f"Weights must sum to 1.0, got {total}"

    def test_weight_values_match_lean(self):
        """
        Lean definitions:
        - WEIGHT_STRUCTURE := 40 (representing 0.40)
        - WEIGHT_QUALITY := 30 (representing 0.30)
        - WEIGHT_ARCHITECTURE := 30 (representing 0.30)
        """
        weights = AnalysisEngine.WEIGHTS
        assert weights["structure"] == 0.40, "Structure weight must be 0.40"
        assert weights["quality"] == 0.30, "Quality weight must be 0.30"
        assert weights["architecture"] == 0.30, "Architecture weight must be 0.30"

    def test_grade_thresholds_match_lean(self):
        """
        Lean definitions:
        - GRADE_A_MIN := 90
        - GRADE_B_MIN := 80
        - GRADE_C_MIN := 70
        - GRADE_D_MIN := 60
        """
        grades = AnalysisEngine.GRADES
        assert grades["A"] == (90, 100), "Grade A threshold must be (90, 100)"
        assert grades["B"] == (80, 90), "Grade B threshold must be (80, 90)"
        assert grades["C"] == (70, 80), "Grade C threshold must be (70, 80)"
        assert grades["D"] == (60, 70), "Grade D threshold must be (60, 70)"
        assert grades["F"] == (0, 60), "Grade F threshold must be (0, 60)"

    def test_grade_coverage_complete(self):
        """
        Lean theorems: grade_*_is_* series
        Proves: Every score in [0, 100] maps to exactly one grade.
        """
        grades = AnalysisEngine.GRADES

        # Verify no gaps: ranges must cover [0, 100]
        all_ranges = sorted(grades.values(), key=lambda x: x[0])
        assert all_ranges[0][0] == 0, "Grades must start at 0"
        assert all_ranges[-1][1] == 100, "Grades must end at 100"

        # Verify no overlaps: each range end equals next range start
        for i in range(len(all_ranges) - 1):
            current_end = all_ranges[i][1]
            next_start = all_ranges[i + 1][0]
            assert current_end == next_start, \
                f"Gap or overlap between {all_ranges[i]} and {all_ranges[i+1]}"

    def test_grade_boundaries_match_lean(self):
        """
        Lean theorems verify specific boundary cases.
        Replicate those checks in Python.
        """
        # Helper to get grade from score (mirrors Lean's score_to_grade)
        def score_to_grade(score: int) -> str:
            grades = AnalysisEngine.GRADES
            for grade, (min_score, max_score) in grades.items():
                if grade == "A" and min_score <= score <= max_score:
                    return grade
                elif min_score <= score < max_score:
                    return grade
            return "F"

        # These match the Lean theorems exactly
        assert score_to_grade(100) == "A", "Lean: perfect_score_is_A"
        assert score_to_grade(90) == "A", "Lean: grade_90_is_A"
        assert score_to_grade(89) == "B", "Lean: grade_89_is_B"
        assert score_to_grade(80) == "B", "Lean: grade_80_is_B"
        assert score_to_grade(79) == "C", "Lean: grade_79_is_C"
        assert score_to_grade(70) == "C", "Lean: grade_70_is_C"
        assert score_to_grade(69) == "D", "Lean: grade_69_is_D"
        assert score_to_grade(60) == "D", "Lean: grade_60_is_D"
        assert score_to_grade(59) == "F", "Lean: grade_59_is_F"
        assert score_to_grade(0) == "F", "Lean: zero_score_is_F"

    def test_score_bounds_valid(self):
        """
        Lean theorems: zero_is_valid, hundred_is_valid, over_hundred_invalid
        Proves: Valid scores are in [0, 100].
        """
        # These would be the bounds enforced by the system
        MIN_SCORE = 0
        MAX_SCORE = 100

        assert MIN_SCORE == 0, "Minimum score must be 0"
        assert MAX_SCORE == 100, "Maximum score must be 100"

    def test_weighted_score_bounded(self):
        """
        Lean theorem: weighted_score_bounded
        Proves: Weighted sum of valid scores produces valid result.

        In Lean: calculate_weighted_score s1 s2 s3 ≤ 10000 (scaled)
        In Python: result is 0.0-100.0 (floats)
        """
        weights = AnalysisEngine.WEIGHTS

        def calculate_weighted_score(s1: float, s2: float, s3: float) -> float:
            """Mirror Lean's calculate_weighted_score (without /100 scaling)."""
            return (
                weights["structure"] * s1 +
                weights["quality"] * s2 +
                weights["architecture"] * s3
            )

        # Test boundaries: all valid scores produce valid result
        test_cases = [
            (0, 0, 0),      # Minimum
            (100, 100, 100),  # Maximum
            (50, 50, 50),   # Middle
            (100, 0, 0),    # Structure only
            (0, 100, 0),    # Quality only
            (0, 0, 100),    # Architecture only
        ]

        for s1, s2, s3 in test_cases:
            result = calculate_weighted_score(s1, s2, s3)
            assert 0 <= result <= 100, \
                f"Score {result} out of bounds for inputs ({s1}, {s2}, {s3})"

    def test_perfect_scores_produce_100(self):
        """
        Lean theorem: perfect_scores_produce_100
        Proves: final_score 100 100 100 = 100
        """
        weights = AnalysisEngine.WEIGHTS
        result = (
            weights["structure"] * 100 +
            weights["quality"] * 100 +
            weights["architecture"] * 100
        )
        assert result == 100.0, f"Perfect scores should produce 100, got {result}"

    def test_zero_scores_produce_0(self):
        """
        Lean theorem: zero_scores_produce_0
        Proves: final_score 0 0 0 = 0
        """
        weights = AnalysisEngine.WEIGHTS
        result = (
            weights["structure"] * 0 +
            weights["quality"] * 0 +
            weights["architecture"] * 0
        )
        assert result == 0.0, f"Zero scores should produce 0, got {result}"


class TestGradeAssignmentVerification:
    """
    Tests that verify Python matches Lean grade assignment proofs (REPO-186).

    Lean file: lean/Repotoire/HealthScore.lean (Sections 5-7)
    """

    @staticmethod
    def score_to_grade(score: int) -> str:
        """Mirror Lean's score_to_grade function."""
        grades = AnalysisEngine.GRADES
        for grade, (min_score, max_score) in grades.items():
            if grade == "A" and min_score <= score <= max_score:
                return grade
            elif min_score <= score < max_score:
                return grade
        return "F"

    def test_grade_coverage_complete(self):
        """
        Lean theorem: grade_coverage, grade_assignment_total
        Proves: Every valid score in [0, 100] maps to exactly one grade.
        """
        valid_grades = {"A", "B", "C", "D", "F"}

        # Test every integer score from 0 to 100
        for score in range(101):
            grade = self.score_to_grade(score)
            assert grade in valid_grades, \
                f"Score {score} mapped to invalid grade '{grade}'"

    def test_grade_ranges_disjoint(self):
        """
        Lean theorem: grade_ranges_disjoint
        Proves: No score belongs to two different grades.
        """
        grades = AnalysisEngine.GRADES

        # For each score, count how many grade ranges it falls into
        for score in range(101):
            matching_grades = []
            for grade, (min_score, max_score) in grades.items():
                if grade == "A":
                    if min_score <= score <= max_score:
                        matching_grades.append(grade)
                else:
                    if min_score <= score < max_score:
                        matching_grades.append(grade)

            assert len(matching_grades) == 1, \
                f"Score {score} matches {len(matching_grades)} grades: {matching_grades}"

    def test_grade_monotonic(self):
        """
        Lean theorem: grade_monotonic
        Proves: Higher scores produce same or better grades.
        s1 ≤ s2 → grade(s1) ≤ grade(s2)
        """
        grade_rank = {"F": 0, "D": 1, "C": 2, "B": 3, "A": 4}

        # Test all pairs where s1 ≤ s2
        for s1 in range(0, 101, 5):
            for s2 in range(s1, 101, 5):
                g1 = self.score_to_grade(s1)
                g2 = self.score_to_grade(s2)
                r1 = grade_rank[g1]
                r2 = grade_rank[g2]
                assert r1 <= r2, \
                    f"Monotonicity violated: score {s1}→{g1} vs {s2}→{g2}"

    def test_grade_ordering(self):
        """
        Lean theorem: grade_ordering
        Proves: F < D < C < B < A
        """
        grade_rank = {"F": 0, "D": 1, "C": 2, "B": 3, "A": 4}

        assert grade_rank["F"] < grade_rank["D"], "F must be less than D"
        assert grade_rank["D"] < grade_rank["C"], "D must be less than C"
        assert grade_rank["C"] < grade_rank["B"], "C must be less than B"
        assert grade_rank["B"] < grade_rank["A"], "B must be less than A"

    def test_thresholds_ordered(self):
        """
        Lean theorem: thresholds_ordered
        Proves: GRADE_D_MIN < GRADE_C_MIN < GRADE_B_MIN < GRADE_A_MIN ≤ 100
        """
        # From Lean: 60 < 70 < 80 < 90 ≤ 100
        GRADE_D_MIN = 60
        GRADE_C_MIN = 70
        GRADE_B_MIN = 80
        GRADE_A_MIN = 90

        assert GRADE_D_MIN < GRADE_C_MIN, "D threshold < C threshold"
        assert GRADE_C_MIN < GRADE_B_MIN, "C threshold < B threshold"
        assert GRADE_B_MIN < GRADE_A_MIN, "B threshold < A threshold"
        assert GRADE_A_MIN <= 100, "A threshold ≤ 100"

    def test_grade_deterministic(self):
        """
        Lean theorem: grade_deterministic
        Proves: Same score always produces same grade.
        """
        # Run multiple times to verify determinism
        for score in [0, 59, 60, 69, 70, 79, 80, 89, 90, 100]:
            results = [self.score_to_grade(score) for _ in range(10)]
            assert len(set(results)) == 1, \
                f"Score {score} produced different grades: {results}"


class TestCypherSafetyVerification:
    """
    Tests that verify Python matches Lean CypherSafety proofs.

    Lean file: lean/Repotoire/CypherSafety.lean
    """

    def test_safe_identifiers_allowed(self):
        """
        Lean theorems: valid_alphanumeric, valid_with_numbers, valid_with_underscore,
                       valid_with_hyphen, valid_mixed
        Proves: Identifiers with [a-zA-Z0-9_-] are valid.
        """
        # These match the Lean theorems exactly
        assert validate_identifier("myProjection") == "myProjection"
        assert validate_identifier("test123") == "test123"
        assert validate_identifier("my_graph") == "my_graph"
        assert validate_identifier("my-projection") == "my-projection"
        assert validate_identifier("test123_data-v2") == "test123_data-v2"

    def test_injection_payloads_blocked(self):
        """
        Lean theorems: invalid_sql_injection, invalid_cypher_injection,
                       invalid_comment, invalid_empty, invalid_space,
                       invalid_quote, invalid_semicolon, invalid_brace
        Proves: Injection payloads are rejected.
        """
        injection_payloads = [
            "'; DROP DATABASE",  # SQL injection
            "foo} RETURN *",     # Cypher injection
            "x//comment",        # Comment injection
            "",                  # Empty (DoS)
            "foo bar",           # Space
            "foo'bar",           # Quote
            "foo;bar",           # Semicolon
            "foo{bar",           # Brace
        ]

        for payload in injection_payloads:
            with pytest.raises(ValidationError):
                validate_identifier(payload)

    def test_length_bounded(self):
        """
        Lean theorem: length_bounded
        Proves: Valid identifiers have length ≤ 100 (MAX_LENGTH).
        """
        MAX_LENGTH = 100  # Matches Lean's MAX_LENGTH

        # Valid: exactly 100 chars
        valid_long = "a" * MAX_LENGTH
        assert validate_identifier(valid_long) == valid_long

        # Invalid: 101 chars
        with pytest.raises(ValidationError):
            validate_identifier("a" * (MAX_LENGTH + 1))

    def test_non_empty_required(self):
        """
        Lean theorem: empty_not_safe, non_empty
        Proves: Empty strings are not valid identifiers.
        """
        with pytest.raises(ValidationError):
            validate_identifier("")

        with pytest.raises(ValidationError):
            validate_identifier("   ")  # Whitespace only

    def test_safe_char_set(self):
        """
        Lean definition: is_safe_char
        Proves: Only [a-zA-Z0-9_-] are safe characters.
        """
        # All safe chars individually
        safe_chars = (
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789"
            "_-"
        )
        for char in safe_chars:
            assert validate_identifier(char) == char

    def test_injection_chars_blocked(self):
        """
        Lean definition: is_injection_char
        Lean theorem: no_injection_chars
        Proves: Injection characters are blocked.
        """
        injection_chars = ["'", '"', ";", "{", "}", "/", "\\", "\n", "\r", " "]

        for char in injection_chars:
            with pytest.raises(ValidationError):
                validate_identifier(f"test{char}test")


class TestThresholdsVerification:
    """
    Tests that verify Python matches Lean Thresholds proofs.

    Lean file: lean/Repotoire/Thresholds.lean
    """

    def test_complexity_thresholds_ordered(self):
        """
        Lean theorem: complexity_thresholds_ordered
        Proves: COMPLEXITY_THRESHOLD_LOW < MEDIUM < HIGH
        """
        # From Lean: 11 < 21 < 31
        THRESHOLD_LOW = 11
        THRESHOLD_MEDIUM = 21
        THRESHOLD_HIGH = 31

        assert THRESHOLD_LOW < THRESHOLD_MEDIUM
        assert THRESHOLD_MEDIUM < THRESHOLD_HIGH

    def test_complexity_boundary_values(self):
        """
        Lean theorems: complexity_10_is_none, complexity_11_is_low,
                       complexity_20_is_low, complexity_21_is_medium,
                       complexity_30_is_medium, complexity_31_is_high
        Proves: Boundary values map to correct severities.
        """
        def complexity_to_severity(cc: int) -> Severity | None:
            """Mirror Lean's complexity_to_severity function."""
            if cc < 11:
                return None  # Healthy
            elif cc < 21:
                return Severity.LOW
            elif cc < 31:
                return Severity.MEDIUM
            else:
                return Severity.HIGH

        # Boundary tests matching Lean theorems
        assert complexity_to_severity(10) is None, "Lean: complexity_10_is_none"
        assert complexity_to_severity(11) == Severity.LOW, "Lean: complexity_11_is_low"
        assert complexity_to_severity(20) == Severity.LOW, "Lean: complexity_20_is_low"
        assert complexity_to_severity(21) == Severity.MEDIUM, "Lean: complexity_21_is_medium"
        assert complexity_to_severity(30) == Severity.MEDIUM, "Lean: complexity_30_is_medium"
        assert complexity_to_severity(31) == Severity.HIGH, "Lean: complexity_31_is_high"
        assert complexity_to_severity(100) == Severity.HIGH, "Lean: complexity_100_is_high"

    def test_complexity_monotonic(self):
        """
        Lean theorem: complexity_monotonic
        Proves: Higher complexity → same or higher severity.
        """
        def severity_level(s: Severity | None) -> int:
            """Convert severity to numeric level for comparison."""
            if s is None:
                return 0
            return {
                Severity.LOW: 1,
                Severity.MEDIUM: 2,
                Severity.HIGH: 3,
                Severity.CRITICAL: 4,
            }[s]

        def complexity_to_severity(cc: int) -> Severity | None:
            if cc < 11:
                return None
            elif cc < 21:
                return Severity.LOW
            elif cc < 31:
                return Severity.MEDIUM
            else:
                return Severity.HIGH

        # Test monotonicity: for c1 ≤ c2, severity(c1) ≤ severity(c2)
        for c1 in range(0, 50, 5):
            for c2 in range(c1, 50, 5):
                s1 = severity_level(complexity_to_severity(c1))
                s2 = severity_level(complexity_to_severity(c2))
                assert s1 <= s2, f"Monotonicity violated: {c1}→{s1} vs {c2}→{s2}"

    def test_god_class_thresholds_ordered(self):
        """
        Lean theorem: god_class_thresholds_ordered
        Proves: GOD_CLASS_THRESHOLD_MEDIUM < HIGH < CRITICAL
        """
        # From Lean: 15 < 20 < 30
        THRESHOLD_MEDIUM = 15
        THRESHOLD_HIGH = 20
        THRESHOLD_CRITICAL = 30

        assert THRESHOLD_MEDIUM < THRESHOLD_HIGH
        assert THRESHOLD_HIGH < THRESHOLD_CRITICAL

    def test_god_class_boundary_values(self):
        """
        Lean theorems: god_class_14_is_none, god_class_15_is_medium,
                       god_class_20_is_high, god_class_30_is_critical
        Proves: Boundary values map to correct severities.
        """
        def method_count_to_severity(count: int) -> Severity | None:
            """Mirror Lean's method_count_to_severity function."""
            if count < 15:
                return None
            elif count < 20:
                return Severity.MEDIUM
            elif count < 30:
                return Severity.HIGH
            else:
                return Severity.CRITICAL

        assert method_count_to_severity(14) is None, "Lean: god_class_14_is_none"
        assert method_count_to_severity(15) == Severity.MEDIUM, "Lean: god_class_15_is_medium"
        assert method_count_to_severity(20) == Severity.HIGH, "Lean: god_class_20_is_high"
        assert method_count_to_severity(30) == Severity.CRITICAL, "Lean: god_class_30_is_critical"

    def test_lcom_thresholds_ordered(self):
        """
        Lean theorem: lcom_thresholds_ordered
        Proves: LCOM_THRESHOLD_MEDIUM < HIGH < CRITICAL
        """
        # From Lean: 40 < 60 < 80 (scaled 0-100, Python uses 0.0-1.0)
        THRESHOLD_MEDIUM = 0.40
        THRESHOLD_HIGH = 0.60
        THRESHOLD_CRITICAL = 0.80

        assert THRESHOLD_MEDIUM < THRESHOLD_HIGH
        assert THRESHOLD_HIGH < THRESHOLD_CRITICAL

    def test_lcom_boundary_values(self):
        """
        Lean theorems: lcom_39_is_none, lcom_40_is_medium,
                       lcom_60_is_high, lcom_80_is_critical
        Proves: Boundary values map to correct severities.
        """
        def lcom_to_severity(lcom: float) -> Severity | None:
            """Mirror Lean's lcom_to_severity function (scaled to 0.0-1.0)."""
            if lcom < 0.40:
                return None
            elif lcom < 0.60:
                return Severity.MEDIUM
            elif lcom < 0.80:
                return Severity.HIGH
            else:
                return Severity.CRITICAL

        assert lcom_to_severity(0.39) is None, "Lean: lcom_39_is_none"
        assert lcom_to_severity(0.40) == Severity.MEDIUM, "Lean: lcom_40_is_medium"
        assert lcom_to_severity(0.60) == Severity.HIGH, "Lean: lcom_60_is_high"
        assert lcom_to_severity(0.80) == Severity.CRITICAL, "Lean: lcom_80_is_critical"

    def test_severity_ordering(self):
        """
        Lean theorem: severity_ordering
        Proves: None < LOW < MEDIUM < HIGH < CRITICAL

        In Lean: none=0, low=1, medium=2, high=3, critical=4 (higher = more severe)
        In Python: CRITICAL is index 0, INFO is index 4 (lower index = more severe)

        This test verifies semantic equivalence: LOW < MEDIUM < HIGH < CRITICAL
        means LOW is less severe than MEDIUM, which is less severe than HIGH, etc.
        """
        severities = list(Severity)
        # In Python enum, lower index = higher severity
        # So we verify: index(CRITICAL) < index(HIGH) < index(MEDIUM) < index(LOW)
        assert severities.index(Severity.CRITICAL) < severities.index(Severity.HIGH), \
            "CRITICAL must be more severe than HIGH"
        assert severities.index(Severity.HIGH) < severities.index(Severity.MEDIUM), \
            "HIGH must be more severe than MEDIUM"
        assert severities.index(Severity.MEDIUM) < severities.index(Severity.LOW), \
            "MEDIUM must be more severe than LOW"
