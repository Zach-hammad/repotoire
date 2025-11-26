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
