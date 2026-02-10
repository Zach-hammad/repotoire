/-
    Health Score Verification

    Proves properties of Repotoire's health scoring algorithm:
    - Weight conservation (REPO-181)
    - Score bounds (REPO-181)
    - Grade coverage completeness (REPO-186)
    - Grade disjointness (REPO-186)
    - Grade ordering/monotonicity (REPO-186)

    Uses percentages (0-100) for exact arithmetic proofs.

    In Python: weights are 0.40, 0.30, 0.30 (floats)
    Here: weights are 40, 30, 30 (percentages summing to 100)

    Python: repotoire/detectors/engine.py:57-64, 467-482
-/

namespace Repotoire.HealthScore

-- ============================================================================
-- SECTION 1: Weight Definitions and Conservation (REPO-181)
-- ============================================================================

-- Category weights as percentages (must sum to 100)
def WEIGHT_STRUCTURE : Nat := 40      -- 40% = 0.40
def WEIGHT_QUALITY : Nat := 30        -- 30% = 0.30
def WEIGHT_ARCHITECTURE : Nat := 30   -- 30% = 0.30

-- Theorem: Weights sum to 100%
theorem weights_sum_to_100 :
    WEIGHT_STRUCTURE + WEIGHT_QUALITY + WEIGHT_ARCHITECTURE = 100 := by
  rfl

-- Theorem: Individual weights are valid (≤ 100)
theorem weight_structure_valid : WEIGHT_STRUCTURE ≤ 100 := by decide
theorem weight_quality_valid : WEIGHT_QUALITY ≤ 100 := by decide
theorem weight_architecture_valid : WEIGHT_ARCHITECTURE ≤ 100 := by decide

-- ============================================================================
-- SECTION 2: Score Validity and Bounds (REPO-181)
-- ============================================================================

-- A score is valid if it's in [0, 100]
def is_valid_score (score : Nat) : Prop :=
  score ≤ 100

-- Theorem: Zero is a valid score
theorem zero_is_valid : is_valid_score 0 := by
  unfold is_valid_score
  decide

-- Theorem: 100 is a valid score
theorem hundred_is_valid : is_valid_score 100 := by
  unfold is_valid_score
  decide

-- Theorem: Any score > 100 is invalid
theorem over_hundred_invalid (score : Nat) (h : score > 100) : ¬is_valid_score score := by
  unfold is_valid_score
  omega

-- Weighted score calculation (scaled by 100 to avoid fractions)
-- Result is in [0, 10000] and needs to be divided by 100 for final score
def calculate_weighted_score (structure_score quality_score arch_score : Nat) : Nat :=
  WEIGHT_STRUCTURE * structure_score +
  WEIGHT_QUALITY * quality_score +
  WEIGHT_ARCHITECTURE * arch_score

-- Theorem: Weighted sum of valid scores produces valid result (after scaling)
theorem weighted_score_bounded
    (s1 s2 s3 : Nat)
    (h1 : is_valid_score s1)
    (h2 : is_valid_score s2)
    (h3 : is_valid_score s3) :
    calculate_weighted_score s1 s2 s3 ≤ 10000 := by
  unfold calculate_weighted_score WEIGHT_STRUCTURE WEIGHT_QUALITY WEIGHT_ARCHITECTURE
  unfold is_valid_score at h1 h2 h3
  -- 40 * s1 + 30 * s2 + 30 * s3 ≤ 40 * 100 + 30 * 100 + 30 * 100 = 10000
  omega

-- Theorem: Minimum weighted score is 0
theorem weighted_score_nonneg (s1 s2 s3 : Nat) :
    calculate_weighted_score s1 s2 s3 ≥ 0 := by
  unfold calculate_weighted_score
  omega

-- Final score after scaling (integer division by 100)
def final_score (s1 s2 s3 : Nat) : Nat :=
  calculate_weighted_score s1 s2 s3 / 100

-- Theorem: Final score is valid (in [0, 100])
theorem final_score_bounded
    (s1 s2 s3 : Nat)
    (h1 : is_valid_score s1)
    (h2 : is_valid_score s2)
    (h3 : is_valid_score s3) :
    is_valid_score (final_score s1 s2 s3) := by
  unfold is_valid_score final_score
  have h := weighted_score_bounded s1 s2 s3 h1 h2 h3
  omega

-- Theorem: Perfect scores produce perfect final score
theorem perfect_scores_produce_100 :
    final_score 100 100 100 = 100 := by
  native_decide

-- Theorem: Zero scores produce zero final score
theorem zero_scores_produce_0 :
    final_score 0 0 0 = 0 := by
  native_decide

-- ============================================================================
-- SECTION 3: Grade Definitions and Thresholds (REPO-186)
-- ============================================================================

-- Grade thresholds
def GRADE_A_MIN : Nat := 90
def GRADE_B_MIN : Nat := 80
def GRADE_C_MIN : Nat := 70
def GRADE_D_MIN : Nat := 60

-- Theorem: Thresholds are ordered
theorem thresholds_ordered :
    GRADE_D_MIN < GRADE_C_MIN ∧
    GRADE_C_MIN < GRADE_B_MIN ∧
    GRADE_B_MIN < GRADE_A_MIN ∧
    GRADE_A_MIN ≤ 100 := by
  constructor <;> native_decide

-- Grade type
inductive Grade where
  | A : Grade
  | B : Grade
  | C : Grade
  | D : Grade
  | F : Grade
deriving Repr, DecidableEq

-- Grade to numeric rank (higher is better)
def Grade.toRank : Grade → Nat
  | A => 4
  | B => 3
  | C => 2
  | D => 1
  | F => 0

-- Grade ordering
instance : LE Grade where
  le g1 g2 := g1.toRank ≤ g2.toRank

instance : LT Grade where
  lt g1 g2 := g1.toRank < g2.toRank

instance : DecidableRel (α := Grade) (· ≤ ·) :=
  fun g1 g2 => inferInstanceAs (Decidable (g1.toRank ≤ g2.toRank))

instance : DecidableRel (α := Grade) (· < ·) :=
  fun g1 g2 => inferInstanceAs (Decidable (g1.toRank < g2.toRank))

-- Grade assignment function (matches Python _score_to_grade)
def score_to_grade (score : Nat) : Grade :=
  if score ≥ GRADE_A_MIN then Grade.A
  else if score ≥ GRADE_B_MIN then Grade.B
  else if score ≥ GRADE_C_MIN then Grade.C
  else if score ≥ GRADE_D_MIN then Grade.D
  else Grade.F

-- String version for backward compatibility
def score_to_grade_string (score : Nat) : String :=
  match score_to_grade score with
  | Grade.A => "A"
  | Grade.B => "B"
  | Grade.C => "C"
  | Grade.D => "D"
  | Grade.F => "F"

-- ============================================================================
-- SECTION 4: Grade Boundary Correctness (REPO-186)
-- ============================================================================

-- Theorem: Perfect score gets A
theorem perfect_score_is_A : score_to_grade 100 = Grade.A := by rfl

-- Theorem: Zero score gets F
theorem zero_score_is_F : score_to_grade 0 = Grade.F := by rfl

-- Theorem: Grade boundaries are correct
theorem grade_90_is_A : score_to_grade 90 = Grade.A := by rfl
theorem grade_89_is_B : score_to_grade 89 = Grade.B := by rfl
theorem grade_80_is_B : score_to_grade 80 = Grade.B := by rfl
theorem grade_79_is_C : score_to_grade 79 = Grade.C := by rfl
theorem grade_70_is_C : score_to_grade 70 = Grade.C := by rfl
theorem grade_69_is_D : score_to_grade 69 = Grade.D := by rfl
theorem grade_60_is_D : score_to_grade 60 = Grade.D := by rfl
theorem grade_59_is_F : score_to_grade 59 = Grade.F := by rfl

-- ============================================================================
-- SECTION 5: Grade Coverage Completeness (REPO-186)
-- ============================================================================

-- Check if score is in grade range
def in_grade_range (score : Nat) (g : Grade) : Prop :=
  match g with
  | Grade.A => score ≥ GRADE_A_MIN ∧ score ≤ 100
  | Grade.B => score ≥ GRADE_B_MIN ∧ score < GRADE_A_MIN
  | Grade.C => score ≥ GRADE_C_MIN ∧ score < GRADE_B_MIN
  | Grade.D => score ≥ GRADE_D_MIN ∧ score < GRADE_C_MIN
  | Grade.F => score < GRADE_D_MIN

-- Theorem: Every valid score maps to exactly one grade (coverage)
theorem grade_coverage (score : Nat) (_h : is_valid_score score) :
    ∃ g : Grade, score_to_grade score = g := by
  exact ⟨score_to_grade score, rfl⟩

-- Theorem: Grade assignment covers all valid scores
-- (More explicit version showing the score determines the grade)
theorem grade_assignment_total (score : Nat) (_h : score ≤ 100) :
    score_to_grade score = Grade.A ∨
    score_to_grade score = Grade.B ∨
    score_to_grade score = Grade.C ∨
    score_to_grade score = Grade.D ∨
    score_to_grade score = Grade.F := by
  unfold score_to_grade GRADE_A_MIN GRADE_B_MIN GRADE_C_MIN GRADE_D_MIN
  by_cases h1 : score ≥ 90
  · left; simp only [if_pos h1]
  · simp only [if_neg h1]
    by_cases h2 : score ≥ 80
    · right; left; simp only [if_pos h2]
    · simp only [if_neg h2]
      by_cases h3 : score ≥ 70
      · right; right; left; simp only [if_pos h3]
      · simp only [if_neg h3]
        by_cases h4 : score ≥ 60
        · right; right; right; left; simp only [if_pos h4]
        · right; right; right; right; simp only [if_neg h4]

-- ============================================================================
-- SECTION 6: Grade Disjointness (REPO-186)
-- ============================================================================

-- Theorem: Grade ranges don't overlap
theorem grade_ranges_disjoint (score : Nat) (g1 g2 : Grade) (h : g1 ≠ g2) :
    ¬(in_grade_range score g1 ∧ in_grade_range score g2) := by
  intro ⟨h1, h2⟩
  unfold in_grade_range GRADE_A_MIN GRADE_B_MIN GRADE_C_MIN GRADE_D_MIN at h1 h2
  cases g1 <;> cases g2 <;> simp_all <;> omega

-- Theorem: score_to_grade produces unique result
theorem grade_unique (score : Nat) (g1 g2 : Grade)
    (h1 : score_to_grade score = g1) (h2 : score_to_grade score = g2) :
    g1 = g2 := by
  rw [← h1, ← h2]

-- ============================================================================
-- SECTION 7: Grade Ordering/Monotonicity (REPO-186)
-- ============================================================================

-- Theorem: Higher scores produce same or better grades
theorem grade_monotonic (s1 s2 : Nat) (h : s1 ≤ s2) :
    score_to_grade s1 ≤ score_to_grade s2 := by
  unfold score_to_grade GRADE_A_MIN GRADE_B_MIN GRADE_C_MIN GRADE_D_MIN
  simp only [LE.le, Grade.toRank]
  by_cases h1 : s1 ≥ 90
  · -- s1 ≥ 90: s1 maps to A
    simp only [if_pos h1]
    have h2 : s2 ≥ 90 := Nat.le_trans h1 h
    simp only [if_pos h2]
    exact Nat.le_refl 4
  · simp only [if_neg h1]
    by_cases h2 : s1 ≥ 80
    · -- s1 ∈ [80, 90): s1 maps to B
      simp only [if_pos h2]
      by_cases h3 : s2 ≥ 90
      · simp only [if_pos h3]; exact Nat.le_of_lt (by native_decide : 3 < 4)
      · simp only [if_neg h3]
        have h4 : s2 ≥ 80 := Nat.le_trans h2 h
        simp only [if_pos h4]
        exact Nat.le_refl 3
    · simp only [if_neg h2]
      by_cases h3 : s1 ≥ 70
      · -- s1 ∈ [70, 80): s1 maps to C
        simp only [if_pos h3]
        by_cases h4 : s2 ≥ 90
        · simp only [if_pos h4]; exact Nat.le_of_lt (by native_decide : 2 < 4)
        · simp only [if_neg h4]
          by_cases h5 : s2 ≥ 80
          · simp only [if_pos h5]; exact Nat.le_of_lt (by native_decide : 2 < 3)
          · simp only [if_neg h5]
            have h6 : s2 ≥ 70 := Nat.le_trans h3 h
            simp only [if_pos h6]
            exact Nat.le_refl 2
      · simp only [if_neg h3]
        by_cases h4 : s1 ≥ 60
        · -- s1 ∈ [60, 70): s1 maps to D
          simp only [if_pos h4]
          by_cases h5 : s2 ≥ 90
          · simp only [if_pos h5]; exact Nat.le_of_lt (by native_decide : 1 < 4)
          · simp only [if_neg h5]
            by_cases h6 : s2 ≥ 80
            · simp only [if_pos h6]; exact Nat.le_of_lt (by native_decide : 1 < 3)
            · simp only [if_neg h6]
              by_cases h7 : s2 ≥ 70
              · simp only [if_pos h7]; exact Nat.le_of_lt (by native_decide : 1 < 2)
              · simp only [if_neg h7]
                have h8 : s2 ≥ 60 := Nat.le_trans h4 h
                simp only [if_pos h8]
                exact Nat.le_refl 1
        · -- s1 < 60: s1 maps to F
          simp only [if_neg h4]
          by_cases h5 : s2 ≥ 90
          · simp only [if_pos h5]; exact Nat.zero_le 4
          · simp only [if_neg h5]
            by_cases h6 : s2 ≥ 80
            · simp only [if_pos h6]; exact Nat.zero_le 3
            · simp only [if_neg h6]
              by_cases h7 : s2 ≥ 70
              · simp only [if_pos h7]; exact Nat.zero_le 2
              · simp only [if_neg h7]
                by_cases h8 : s2 ≥ 60
                · simp only [if_pos h8]; exact Nat.zero_le 1
                · simp only [if_neg h8]; exact Nat.le_refl 0

-- Theorem: Grade ordering is correct
theorem grade_ordering :
    Grade.F < Grade.D ∧
    Grade.D < Grade.C ∧
    Grade.C < Grade.B ∧
    Grade.B < Grade.A := by
  constructor <;> native_decide

-- ============================================================================
-- SECTION 8: Determinism (REPO-181)
-- ============================================================================

-- Theorem: Score calculation is deterministic
theorem score_deterministic (s1 s2 s3 : Nat) :
    calculate_weighted_score s1 s2 s3 = calculate_weighted_score s1 s2 s3 := by rfl

-- Theorem: Grade assignment is deterministic
theorem grade_deterministic (score : Nat) :
    score_to_grade score = score_to_grade score := by rfl

-- Theorem: Final score is deterministic
theorem final_score_deterministic (s1 s2 s3 : Nat) :
    final_score s1 s2 s3 = final_score s1 s2 s3 := by rfl

end Repotoire.HealthScore
