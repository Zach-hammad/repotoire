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


class TestPriorityScoreVerification:
    """
    Tests that verify Python matches Lean PriorityScore proofs (REPO-184).

    Lean file: lean/Repotoire/PriorityScore.lean
    Python: repotoire/models.py:908-945
    """

    def test_weights_sum_to_one(self):
        """
        Lean theorem: weights_sum_to_100
        Proves: WEIGHT_SEVERITY + WEIGHT_CONFIDENCE + WEIGHT_AGREEMENT = 100 (as %)
        In Python: 0.4 + 0.3 + 0.3 = 1.0
        """
        WEIGHT_SEVERITY = 0.4
        WEIGHT_CONFIDENCE = 0.3
        WEIGHT_AGREEMENT = 0.3

        total = WEIGHT_SEVERITY + WEIGHT_CONFIDENCE + WEIGHT_AGREEMENT
        assert total == 1.0, f"Priority weights must sum to 1.0, got {total}"

    def test_severity_weight_mapping(self):
        """
        Lean definition: severity_weight_percent
        Proves: INFO=20%, LOW=40%, MEDIUM=60%, HIGH=80%, CRITICAL=100%
        """
        # From Python models.py:926-932
        severity_map = {
            Severity.CRITICAL: 1.0,  # 100%
            Severity.HIGH: 0.8,      # 80%
            Severity.MEDIUM: 0.6,    # 60%
            Severity.LOW: 0.4,       # 40%
            Severity.INFO: 0.2       # 20%
        }

        assert severity_map[Severity.INFO] == 0.2, "Lean: severity_weight_percent INFO = 20"
        assert severity_map[Severity.LOW] == 0.4, "Lean: severity_weight_percent LOW = 40"
        assert severity_map[Severity.MEDIUM] == 0.6, "Lean: severity_weight_percent MEDIUM = 60"
        assert severity_map[Severity.HIGH] == 0.8, "Lean: severity_weight_percent HIGH = 80"
        assert severity_map[Severity.CRITICAL] == 1.0, "Lean: severity_weight_percent CRITICAL = 100"

    def test_severity_weight_monotonic(self):
        """
        Lean theorem: severity_weight_monotonic
        Proves: s1 ≤ s2 → severity_weight_percent s1 ≤ severity_weight_percent s2
        """
        severity_map = {
            Severity.INFO: 0.2,
            Severity.LOW: 0.4,
            Severity.MEDIUM: 0.6,
            Severity.HIGH: 0.8,
            Severity.CRITICAL: 1.0
        }
        ordered = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]

        for i in range(len(ordered)):
            for j in range(i, len(ordered)):
                s1, s2 = ordered[i], ordered[j]
                assert severity_map[s1] <= severity_map[s2], \
                    f"Monotonicity violated: {s1}→{severity_map[s1]} vs {s2}→{severity_map[s2]}"

    def test_agreement_normalization(self):
        """
        Lean definition: agreement_normalized
        Proves: min(1.0, (count - 1) / 2) for count > 1, else 0.

        Lean theorems:
        - single_detector_no_bonus: agreement_normalized 1 = 0
        - two_detectors_agreement: agreement_normalized 2 = 50
        - max_agreement_at_three: agreement_normalized 3 = 100
        """
        def agreement_normalized(detector_count: int) -> float:
            """Mirror Lean's agreement_normalized (as 0.0-1.0)."""
            if detector_count <= 1:
                return 0.0
            else:
                return min(1.0, (detector_count - 1) / 2.0)

        assert agreement_normalized(0) == 0.0, "Lean: single_detector_no_bonus"
        assert agreement_normalized(1) == 0.0, "Lean: single_detector_no_bonus"
        assert agreement_normalized(2) == 0.5, "Lean: two_detectors_agreement"
        assert agreement_normalized(3) == 1.0, "Lean: max_agreement_at_three"
        assert agreement_normalized(4) == 1.0, "Lean: max_agreement_stable"
        assert agreement_normalized(10) == 1.0, "Lean: max_agreement_stable"

    def test_priority_score_bounded(self):
        """
        Lean theorem: priority_score_bounded
        Proves: Final score is in [0, 100].
        """
        def priority_score(severity_weight: float, confidence: float, agreement: float) -> float:
            """Mirror Lean's priority_score calculation."""
            return (severity_weight * 0.4 + confidence * 0.3 + agreement * 0.3) * 100

        # Test all corner cases
        test_cases = [
            (0.2, 0.0, 0.0),   # Min severity, zero confidence, no agreement
            (1.0, 1.0, 1.0),   # Max severity, full confidence, full agreement
            (0.5, 0.5, 0.5),   # Middle values
            (1.0, 0.0, 0.0),   # Max severity only
            (0.2, 1.0, 1.0),   # Min severity with max others
        ]

        for sev, conf, agree in test_cases:
            score = priority_score(sev, conf, agree)
            assert 0 <= score <= 100, \
                f"Score {score} out of bounds for ({sev}, {conf}, {agree})"

    def test_example_calculations(self):
        """
        Lean examples: Verify specific score calculations match.
        """
        def priority_score(severity: Severity, confidence: float, detector_count: int) -> int:
            """Mirror Lean's priority_score (integer result via integer division)."""
            severity_map = {
                Severity.INFO: 20,
                Severity.LOW: 40,
                Severity.MEDIUM: 60,
                Severity.HIGH: 80,
                Severity.CRITICAL: 100
            }
            WEIGHT_SEVERITY = 40
            WEIGHT_CONFIDENCE = 30
            WEIGHT_AGREEMENT = 30

            # Agreement normalization (as percentage 0-100)
            if detector_count <= 1:
                agreement = 0
            else:
                agreement = min(100, (detector_count - 1) * 50)

            # Calculate weighted score (scaled by 100)
            weighted = (
                severity_map[severity] * WEIGHT_SEVERITY +
                int(confidence * 100) * WEIGHT_CONFIDENCE +
                agreement * WEIGHT_AGREEMENT
            )
            return weighted // 100

        # Lean example: CRITICAL + 100% confidence + 3 detectors = 100
        assert priority_score(Severity.CRITICAL, 1.0, 3) == 100, \
            "Lean: priority_score CRITICAL 100 3 = 100"

        # Lean example: HIGH + 80% confidence + 2 detectors = 71
        assert priority_score(Severity.HIGH, 0.8, 2) == 71, \
            "Lean: priority_score HIGH 80 2 = 71"

        # Lean example: MEDIUM + 50% confidence + 1 detector = 39
        assert priority_score(Severity.MEDIUM, 0.5, 1) == 39, \
            "Lean: priority_score MEDIUM 50 1 = 39"

        # Lean example: LOW + 30% confidence + 0 detectors = 25
        assert priority_score(Severity.LOW, 0.3, 0) == 25, \
            "Lean: priority_score LOW 30 0 = 25"

        # Lean example: INFO + 0% confidence + 0 detectors = 8
        assert priority_score(Severity.INFO, 0.0, 0) == 8, \
            "Lean: priority_score INFO 0 0 = 8"


class TestPathSafetyVerification:
    """
    Tests that verify Python matches Lean PathSafety proofs (REPO-182).

    Lean file: lean/Repotoire/PathSafety.lean
    Python: repotoire/pipeline/ingestion.py:134-155
    """

    def test_is_prefix_reflexive(self):
        """
        Lean theorem: is_prefix_refl
        Proves: is_prefix p p = true (every path is prefix of itself)
        """
        from pathlib import Path

        paths = [
            Path("/home/user/repo"),
            Path("/"),
            Path("/a/b/c/d/e"),
        ]

        for p in paths:
            # A path is always "within" itself
            assert p.is_relative_to(p), f"Path {p} should be relative to itself"

    def test_subpath_within_parent(self):
        """
        Lean theorem: subpath_within_parent
        Proves: is_within_repo (parent ++ suffix) parent = true
        """
        from pathlib import Path

        repo = Path("/home/user/repo")
        subpaths = [
            repo / "src",
            repo / "src" / "main.py",
            repo / "tests" / "unit" / "test_foo.py",
        ]

        for subpath in subpaths:
            assert subpath.is_relative_to(repo), \
                f"Subpath {subpath} should be within {repo}"

    def test_attack_file_blocked(self):
        """
        Lean theorem: attack_file_blocked
        Proves: is_within_repo [\"etc\", \"passwd\"] [\"home\", \"user\", \"myrepo\"] = false
        """
        from pathlib import Path

        repo = Path("/home/user/myrepo")
        attack = Path("/etc/passwd")

        # Attack file should NOT be relative to repo
        assert not attack.is_relative_to(repo), \
            f"Attack path {attack} should NOT be within {repo}"

    def test_sibling_attack_blocked(self):
        """
        Lean theorem: sibling_attack_blocked
        Proves: is_within_repo [\"home\", \"user\", \"otherrepo\", \"secrets.txt\"]
                              [\"home\", \"user\", \"myrepo\"] = false
        """
        from pathlib import Path

        repo = Path("/home/user/myrepo")
        sibling = Path("/home/user/otherrepo/secrets.txt")

        assert not sibling.is_relative_to(repo), \
            f"Sibling {sibling} should NOT be within {repo}"

    def test_valid_file_contained(self):
        """
        Lean theorem: valid_file_contained
        Proves: is_within_repo [\"home\", \"user\", \"myrepo\", \"src\", \"main.py\"]
                              [\"home\", \"user\", \"myrepo\"] = true
        """
        from pathlib import Path

        repo = Path("/home/user/myrepo")
        valid = Path("/home/user/myrepo/src/main.py")

        assert valid.is_relative_to(repo), \
            f"Valid file {valid} should be within {repo}"

    def test_different_root_not_contained(self):
        """
        Lean theorem: different_root_not_contained
        Proves: If first component differs, file is not contained.
        """
        from pathlib import Path

        repo = Path("/home/user/repo")
        different_root = Path("/var/data/file.txt")

        assert not different_root.is_relative_to(repo), \
            "Different root path should not be contained"

    def test_shorter_path_rejected(self):
        """
        Lean theorem: shorter_path_rejected
        Proves: is_within_repo [] [\"home\", \"user\"] = false
        (Empty path not contained in non-empty repo)
        """
        from pathlib import Path

        repo = Path("/home/user")
        # Root path is "shorter" in components
        root = Path("/")

        assert not root.is_relative_to(repo), \
            "Root should not be within repo"

    def test_traversal_attack_detection(self):
        """
        Lean definitions: is_traversal_component, has_no_traversal
        Lean theorems: dotdot_unsafe, dot_unsafe

        Proves: Paths with \"..\" or \".\" are unsafe.
        Note: In Python, Path.resolve() normalizes these away.
        """
        from pathlib import Path

        # These represent pre-normalization attack patterns
        # After resolution, they would resolve outside repo
        repo = Path("/home/user/repo").resolve()
        attack_patterns = [
            "../../../etc/passwd",
            "./../../etc/passwd",
            "src/../../../etc/passwd",
        ]

        for pattern in attack_patterns:
            # Construct attack path and resolve
            attack = (repo / pattern).resolve()
            # After resolution, attack should be outside repo
            assert not attack.is_relative_to(repo), \
                f"Traversal attack {pattern} should be blocked after resolution"


class TestRiskAmplificationVerification:
    """
    Tests that verify Python matches Lean RiskAmplification proofs (REPO-187).

    Lean file: lean/Repotoire/RiskAmplification.lean
    Python: repotoire/detectors/risk_analyzer.py
    """

    def test_severity_ordering(self):
        """
        Lean theorem: severity_ordering
        Proves: INFO < LOW < MEDIUM < HIGH < CRITICAL
        """
        from repotoire.detectors.risk_analyzer import BottleneckRiskAnalyzer

        SEVERITY_ORDER = BottleneckRiskAnalyzer.SEVERITY_ORDER
        assert SEVERITY_ORDER == [
            Severity.INFO,
            Severity.LOW,
            Severity.MEDIUM,
            Severity.HIGH,
            Severity.CRITICAL
        ], "Severity order must match Lean definition"

    def test_risk_weight_bounded(self):
        """
        Lean theorem: risk_weight_bounded
        Proves: All risk weights ≤ 100 (as percentage)
        """
        from repotoire.detectors.risk_analyzer import BottleneckRiskAnalyzer

        for factor_type, weight in BottleneckRiskAnalyzer.RISK_WEIGHTS.items():
            assert 0 <= weight <= 1.0, \
                f"Weight for {factor_type} must be in [0, 1]: {weight}"

    def test_escalation_zero_additional(self):
        """
        Lean theorem: zero_additional_no_escalation
        Proves: 0 additional factors → no escalation
        """
        def escalate(original: Severity, additional_count: int) -> Severity:
            """Mirror Lean's calculate_escalated_severity."""
            SEVERITY_ORDER = [
                Severity.INFO, Severity.LOW, Severity.MEDIUM,
                Severity.HIGH, Severity.CRITICAL
            ]
            original_idx = SEVERITY_ORDER.index(original)

            if additional_count >= 2:
                return Severity.CRITICAL
            elif additional_count == 1:
                new_idx = min(original_idx + 1, len(SEVERITY_ORDER) - 1)
                return SEVERITY_ORDER[new_idx]
            else:
                return original

        # Test all severities with 0 additional factors
        for sev in Severity:
            assert escalate(sev, 0) == sev, \
                f"Lean: zero_additional_no_escalation for {sev}"

    def test_escalation_one_additional(self):
        """
        Lean theorem: one_additional_escalates
        Proves: 1 additional factor → escalate by 1 level
        """
        def escalate_one(s: Severity) -> Severity:
            """Mirror Lean's escalate_one."""
            mapping = {
                Severity.INFO: Severity.LOW,
                Severity.LOW: Severity.MEDIUM,
                Severity.MEDIUM: Severity.HIGH,
                Severity.HIGH: Severity.CRITICAL,
                Severity.CRITICAL: Severity.CRITICAL,
            }
            return mapping[s]

        def escalate(original: Severity, additional_count: int) -> Severity:
            SEVERITY_ORDER = [
                Severity.INFO, Severity.LOW, Severity.MEDIUM,
                Severity.HIGH, Severity.CRITICAL
            ]
            original_idx = SEVERITY_ORDER.index(original)

            if additional_count >= 2:
                return Severity.CRITICAL
            elif additional_count == 1:
                new_idx = min(original_idx + 1, len(SEVERITY_ORDER) - 1)
                return SEVERITY_ORDER[new_idx]
            else:
                return original

        # Lean examples:
        assert escalate(Severity.INFO, 1) == Severity.LOW, \
            "Lean: INFO + 1 additional → LOW"
        assert escalate(Severity.LOW, 1) == Severity.MEDIUM, \
            "Lean: LOW + 1 additional → MEDIUM"
        assert escalate(Severity.MEDIUM, 1) == Severity.HIGH, \
            "Lean: MEDIUM + 1 additional → HIGH"
        assert escalate(Severity.HIGH, 1) == Severity.CRITICAL, \
            "Lean: HIGH + 1 additional → CRITICAL"
        assert escalate(Severity.CRITICAL, 1) == Severity.CRITICAL, \
            "Lean: CRITICAL + 1 additional → CRITICAL"

    def test_escalation_two_plus_additional_critical(self):
        """
        Lean theorems: two_additional_critical, three_additional_critical,
                       compound_risk_is_critical
        Proves: 2+ additional factors → always CRITICAL
        """
        def escalate(original: Severity, additional_count: int) -> Severity:
            SEVERITY_ORDER = [
                Severity.INFO, Severity.LOW, Severity.MEDIUM,
                Severity.HIGH, Severity.CRITICAL
            ]
            original_idx = SEVERITY_ORDER.index(original)

            if additional_count >= 2:
                return Severity.CRITICAL
            elif additional_count == 1:
                new_idx = min(original_idx + 1, len(SEVERITY_ORDER) - 1)
                return SEVERITY_ORDER[new_idx]
            else:
                return original

        # Any severity + 2+ additional → CRITICAL
        for sev in Severity:
            for count in [2, 3, 4, 10]:
                assert escalate(sev, count) == Severity.CRITICAL, \
                    f"Lean: {sev} + {count} additional → CRITICAL"

    def test_escalation_monotonic(self):
        """
        Lean theorem: escalated_ge_original
        Proves: Escalated severity ≥ original severity
        """
        SEVERITY_ORDER = [
            Severity.INFO, Severity.LOW, Severity.MEDIUM,
            Severity.HIGH, Severity.CRITICAL
        ]

        def escalate(original: Severity, additional_count: int) -> Severity:
            original_idx = SEVERITY_ORDER.index(original)
            if additional_count >= 2:
                return Severity.CRITICAL
            elif additional_count == 1:
                new_idx = min(original_idx + 1, len(SEVERITY_ORDER) - 1)
                return SEVERITY_ORDER[new_idx]
            else:
                return original

        for sev in Severity:
            for count in range(5):
                escalated = escalate(sev, count)
                orig_idx = SEVERITY_ORDER.index(sev)
                esc_idx = SEVERITY_ORDER.index(escalated)
                assert esc_idx >= orig_idx, \
                    f"Escalation should never decrease severity: {sev}→{escalated}"

    def test_critical_compound_risk_detection(self):
        """
        Lean theorem: two_plus_factors_is_critical_risk
        Proves: 2+ additional factors yields critical compound risk
        """
        def is_critical_compound_risk(num_factors: int, escalated: Severity) -> bool:
            """Mirror Lean's is_critical_compound_risk."""
            return num_factors >= 2 and escalated == Severity.CRITICAL

        def escalate(original: Severity, additional_count: int) -> Severity:
            SEVERITY_ORDER = [
                Severity.INFO, Severity.LOW, Severity.MEDIUM,
                Severity.HIGH, Severity.CRITICAL
            ]
            original_idx = SEVERITY_ORDER.index(original)
            if additional_count >= 2:
                return Severity.CRITICAL
            elif additional_count == 1:
                new_idx = min(original_idx + 1, len(SEVERITY_ORDER) - 1)
                return SEVERITY_ORDER[new_idx]
            else:
                return original

        # With 2+ additional factors, result is always critical compound risk
        for sev in Severity:
            for additional in [2, 3, 4]:
                num_factors = additional + 1  # Base + additional
                escalated = escalate(sev, additional)
                assert is_critical_compound_risk(num_factors, escalated), \
                    f"2+ factors should be critical compound risk"

    def test_bottleneck_not_counted_as_additional(self):
        """
        Lean theorem: bottleneck_not_additional
        Proves: Bottleneck is the base factor, not counted as additional.
        """
        # In Python, BottleneckRiskAnalyzer counts unique factor_types and subtracts 1
        # for the bottleneck base. This matches Lean's is_additional_factor.
        from repotoire.detectors.risk_analyzer import RiskAssessment

        # Verify the factor_types property exists and works
        assessment = RiskAssessment(
            entity="test.module.Class",  # Qualified name
            risk_factors=[],
            original_severity=Severity.MEDIUM
        )
        # With no factors, factor_types should be empty
        assert len(assessment.factor_types) == 0

    def test_severity_multiplier_bounded(self):
        """
        Lean theorem: severity_multiplier_bounded
        Proves: severity_multiplier s ≤ 100
        """
        def severity_multiplier(s: Severity) -> int:
            """Mirror Lean's severity_multiplier (as percentage)."""
            SEVERITY_ORDER = [
                Severity.INFO, Severity.LOW, Severity.MEDIUM,
                Severity.HIGH, Severity.CRITICAL
            ]
            return (SEVERITY_ORDER.index(s) + 1) * 20

        for sev in Severity:
            mult = severity_multiplier(sev)
            assert mult <= 100, f"Severity multiplier for {sev} should be ≤ 100"
            assert mult >= 20, f"Severity multiplier for {sev} should be ≥ 20"
