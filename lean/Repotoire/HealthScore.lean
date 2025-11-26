/-
    Health Score Verification

    Proves properties of Repotoire's health scoring algorithm.
    Uses percentages (0-100) for exact arithmetic proofs.

    In Python: weights are 0.40, 0.30, 0.30 (floats)
    Here: weights are 40, 30, 30 (percentages summing to 100)
-/

namespace Repotoire.HealthScore

-- Category weights as percentages (must sum to 100)
def WEIGHT_STRUCTURE : Nat := 40      -- 40% = 0.40
def WEIGHT_QUALITY : Nat := 30        -- 30% = 0.30
def WEIGHT_ARCHITECTURE : Nat := 30   -- 30% = 0.30

-- Theorem 1: Weights sum to 100%
theorem weights_sum_to_100 :
    WEIGHT_STRUCTURE + WEIGHT_QUALITY + WEIGHT_ARCHITECTURE = 100 := by
  rfl

-- A score is valid if it's in [0, 100]
def is_valid_score (score : Nat) : Prop :=
  score ≤ 100

-- Theorem 2: Zero is a valid score
theorem zero_is_valid : is_valid_score 0 := by
  unfold is_valid_score
  decide

-- Theorem 3: 100 is a valid score
theorem hundred_is_valid : is_valid_score 100 := by
  unfold is_valid_score
  decide

-- Theorem 4: Any score > 100 is invalid
theorem over_hundred_invalid (score : Nat) (h : score > 100) : ¬is_valid_score score := by
  unfold is_valid_score
  omega

-- Grade thresholds
def GRADE_A_MIN : Nat := 90
def GRADE_B_MIN : Nat := 80
def GRADE_C_MIN : Nat := 70
def GRADE_D_MIN : Nat := 60

-- Grade assignment function
def score_to_grade (score : Nat) : String :=
  if score ≥ GRADE_A_MIN then "A"
  else if score ≥ GRADE_B_MIN then "B"
  else if score ≥ GRADE_C_MIN then "C"
  else if score ≥ GRADE_D_MIN then "D"
  else "F"

-- Theorem 5: Perfect score gets A
theorem perfect_score_is_A : score_to_grade 100 = "A" := by rfl

-- Theorem 6: Zero score gets F
theorem zero_score_is_F : score_to_grade 0 = "F" := by rfl

-- Theorem 7: Grade boundaries are correct
theorem grade_90_is_A : score_to_grade 90 = "A" := by rfl
theorem grade_89_is_B : score_to_grade 89 = "B" := by rfl
theorem grade_80_is_B : score_to_grade 80 = "B" := by rfl
theorem grade_79_is_C : score_to_grade 79 = "C" := by rfl
theorem grade_70_is_C : score_to_grade 70 = "C" := by rfl
theorem grade_69_is_D : score_to_grade 69 = "D" := by rfl
theorem grade_60_is_D : score_to_grade 60 = "D" := by rfl
theorem grade_59_is_F : score_to_grade 59 = "F" := by rfl

end Repotoire.HealthScore
