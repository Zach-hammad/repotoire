/-
    Severity Threshold Monotonicity Verification

    Proves that severity thresholds are monotonic: worse metrics always
    produce equal or higher severity. This ensures consistent behavior
    across all detectors.

    Python: repotoire/detectors/radon_detector.py:46-54
-/

namespace Repotoire.Thresholds

-- Severity levels as natural numbers (higher = worse)
-- Maps to Python enum: None=0, LOW=1, MEDIUM=2, HIGH=3, CRITICAL=4
inductive Severity where
  | none : Severity
  | low : Severity
  | medium : Severity
  | high : Severity
  | critical : Severity
deriving Repr, DecidableEq

-- Convert severity to natural number for comparison
def Severity.toNat : Severity → Nat
  | none => 0
  | low => 1
  | medium => 2
  | high => 3
  | critical => 4

-- Severity ordering
instance : LE Severity where
  le a b := a.toNat ≤ b.toNat

instance : LT Severity where
  lt a b := a.toNat < b.toNat

instance : DecidableRel (α := Severity) (· ≤ ·) :=
  fun a b => inferInstanceAs (Decidable (a.toNat ≤ b.toNat))

instance : DecidableRel (α := Severity) (· < ·) :=
  fun a b => inferInstanceAs (Decidable (a.toNat < b.toNat))

-- Theorem 1: Severity ordering is correct
theorem severity_ordering :
    Severity.none < Severity.low ∧
    Severity.low < Severity.medium ∧
    Severity.medium < Severity.high ∧
    Severity.high < Severity.critical := by
  constructor <;> native_decide

-- Cyclomatic Complexity thresholds (from Radon detector)
-- A (1-5): None
-- B (6-10): None
-- C (11-20): Low
-- D (21-30): Medium
-- E (31-40): High
-- F (41+): High

def COMPLEXITY_THRESHOLD_LOW : Nat := 11
def COMPLEXITY_THRESHOLD_MEDIUM : Nat := 21
def COMPLEXITY_THRESHOLD_HIGH : Nat := 31

-- Complexity to severity mapping
def complexity_to_severity (cc : Nat) : Severity :=
  if cc < COMPLEXITY_THRESHOLD_LOW then Severity.none
  else if cc < COMPLEXITY_THRESHOLD_MEDIUM then Severity.low
  else if cc < COMPLEXITY_THRESHOLD_HIGH then Severity.medium
  else Severity.high

-- Theorem 2: Complexity thresholds are ordered
theorem complexity_thresholds_ordered :
    COMPLEXITY_THRESHOLD_LOW < COMPLEXITY_THRESHOLD_MEDIUM ∧
    COMPLEXITY_THRESHOLD_MEDIUM < COMPLEXITY_THRESHOLD_HIGH := by
  constructor <;> native_decide

-- Theorem 3: Complexity mapping is monotonic
-- Higher complexity → same or worse severity
theorem complexity_monotonic (c1 c2 : Nat) (h : c1 ≤ c2) :
    complexity_to_severity c1 ≤ complexity_to_severity c2 := by
  unfold complexity_to_severity COMPLEXITY_THRESHOLD_LOW COMPLEXITY_THRESHOLD_MEDIUM COMPLEXITY_THRESHOLD_HIGH
  simp only [LE.le, Severity.toNat]
  -- Manual case analysis using by_cases (split_ifs is Mathlib-only)
  by_cases h1 : c1 < 11
  · -- c1 < 11: c1 maps to none (0)
    simp only [if_pos h1]
    by_cases h2 : c2 < 11
    · simp only [if_pos h2]; exact Nat.le_refl 0
    · simp only [if_neg h2]
      by_cases h3 : c2 < 21
      · simp only [if_pos h3]; exact Nat.zero_le 1
      · simp only [if_neg h3]
        by_cases h4 : c2 < 31
        · simp only [if_pos h4]; exact Nat.zero_le 2
        · simp only [if_neg h4]; exact Nat.zero_le 3
  · -- c1 ≥ 11
    simp only [if_neg h1]
    by_cases h2 : c1 < 21
    · -- c1 ∈ [11, 21): maps to low (1)
      simp only [if_pos h2]
      by_cases h3 : c2 < 11
      · -- c2 < 11 but c1 ≥ 11 and c1 ≤ c2: contradiction
        omega
      · simp only [if_neg h3]
        by_cases h4 : c2 < 21
        · simp only [if_pos h4]; exact Nat.le_refl 1
        · simp only [if_neg h4]
          by_cases h5 : c2 < 31
          · simp only [if_pos h5]; exact Nat.le_of_lt (by native_decide : 1 < 2)
          · simp only [if_neg h5]; exact Nat.le_of_lt (by native_decide : 1 < 3)
    · -- c1 ≥ 21
      simp only [if_neg h2]
      by_cases h3 : c1 < 31
      · -- c1 ∈ [21, 31): maps to medium (2)
        simp only [if_pos h3]
        by_cases h4 : c2 < 11
        · omega
        · simp only [if_neg h4]
          by_cases h5 : c2 < 21
          · omega
          · simp only [if_neg h5]
            by_cases h6 : c2 < 31
            · simp only [if_pos h6]; exact Nat.le_refl 2
            · simp only [if_neg h6]; exact Nat.le_of_lt (by native_decide : 2 < 3)
      · -- c1 ≥ 31: maps to high (3)
        simp only [if_neg h3]
        by_cases h4 : c2 < 11
        · omega
        · simp only [if_neg h4]
          by_cases h5 : c2 < 21
          · omega
          · simp only [if_neg h5]
            by_cases h6 : c2 < 31
            · omega
            · simp only [if_neg h6]; exact Nat.le_refl 3

-- God Class thresholds (method count)
-- <15: None
-- 15-19: Medium
-- 20-29: High
-- 30+: Critical

def GOD_CLASS_THRESHOLD_MEDIUM : Nat := 15
def GOD_CLASS_THRESHOLD_HIGH : Nat := 20
def GOD_CLASS_THRESHOLD_CRITICAL : Nat := 30

def method_count_to_severity (count : Nat) : Severity :=
  if count < GOD_CLASS_THRESHOLD_MEDIUM then Severity.none
  else if count < GOD_CLASS_THRESHOLD_HIGH then Severity.medium
  else if count < GOD_CLASS_THRESHOLD_CRITICAL then Severity.high
  else Severity.critical

-- Theorem 4: God class thresholds are ordered
theorem god_class_thresholds_ordered :
    GOD_CLASS_THRESHOLD_MEDIUM < GOD_CLASS_THRESHOLD_HIGH ∧
    GOD_CLASS_THRESHOLD_HIGH < GOD_CLASS_THRESHOLD_CRITICAL := by
  constructor <;> native_decide

-- Theorem 5: God class mapping is monotonic
theorem god_class_monotonic (m1 m2 : Nat) (h : m1 ≤ m2) :
    method_count_to_severity m1 ≤ method_count_to_severity m2 := by
  unfold method_count_to_severity GOD_CLASS_THRESHOLD_MEDIUM GOD_CLASS_THRESHOLD_HIGH GOD_CLASS_THRESHOLD_CRITICAL
  simp only [LE.le, Severity.toNat]
  by_cases h1 : m1 < 15
  · simp only [if_pos h1]
    by_cases h2 : m2 < 15
    · simp only [if_pos h2]; exact Nat.le_refl 0
    · simp only [if_neg h2]
      by_cases h3 : m2 < 20
      · simp only [if_pos h3]; exact Nat.zero_le 2
      · simp only [if_neg h3]
        by_cases h4 : m2 < 30
        · simp only [if_pos h4]; exact Nat.zero_le 3
        · simp only [if_neg h4]; exact Nat.zero_le 4
  · simp only [if_neg h1]
    by_cases h2 : m1 < 20
    · simp only [if_pos h2]
      by_cases h3 : m2 < 15
      · omega
      · simp only [if_neg h3]
        by_cases h4 : m2 < 20
        · simp only [if_pos h4]; exact Nat.le_refl 2
        · simp only [if_neg h4]
          by_cases h5 : m2 < 30
          · simp only [if_pos h5]; exact Nat.le_of_lt (by native_decide : 2 < 3)
          · simp only [if_neg h5]; exact Nat.le_of_lt (by native_decide : 2 < 4)
    · simp only [if_neg h2]
      by_cases h3 : m1 < 30
      · simp only [if_pos h3]
        by_cases h4 : m2 < 15
        · omega
        · simp only [if_neg h4]
          by_cases h5 : m2 < 20
          · omega
          · simp only [if_neg h5]
            by_cases h6 : m2 < 30
            · simp only [if_pos h6]; exact Nat.le_refl 3
            · simp only [if_neg h6]; exact Nat.le_of_lt (by native_decide : 3 < 4)
      · simp only [if_neg h3]
        by_cases h4 : m2 < 15
        · omega
        · simp only [if_neg h4]
          by_cases h5 : m2 < 20
          · omega
          · simp only [if_neg h5]
            by_cases h6 : m2 < 30
            · omega
            · simp only [if_neg h6]; exact Nat.le_refl 4

-- LCOM thresholds (scaled to 0-100 to avoid floats)
-- Python uses 0.0-1.0, we use 0-100
-- <40: None (cohesive)
-- 40-59: Medium
-- 60-79: High
-- 80+: Critical

def LCOM_THRESHOLD_MEDIUM : Nat := 40
def LCOM_THRESHOLD_HIGH : Nat := 60
def LCOM_THRESHOLD_CRITICAL : Nat := 80

def lcom_to_severity (lcom : Nat) : Severity :=
  if lcom < LCOM_THRESHOLD_MEDIUM then Severity.none
  else if lcom < LCOM_THRESHOLD_HIGH then Severity.medium
  else if lcom < LCOM_THRESHOLD_CRITICAL then Severity.high
  else Severity.critical

-- Theorem 6: LCOM thresholds are ordered
theorem lcom_thresholds_ordered :
    LCOM_THRESHOLD_MEDIUM < LCOM_THRESHOLD_HIGH ∧
    LCOM_THRESHOLD_HIGH < LCOM_THRESHOLD_CRITICAL := by
  constructor <;> native_decide

-- Theorem 7: LCOM mapping is monotonic
theorem lcom_monotonic (l1 l2 : Nat) (h : l1 ≤ l2) :
    lcom_to_severity l1 ≤ lcom_to_severity l2 := by
  unfold lcom_to_severity LCOM_THRESHOLD_MEDIUM LCOM_THRESHOLD_HIGH LCOM_THRESHOLD_CRITICAL
  simp only [LE.le, Severity.toNat]
  by_cases h1 : l1 < 40
  · simp only [if_pos h1]
    by_cases h2 : l2 < 40
    · simp only [if_pos h2]; exact Nat.le_refl 0
    · simp only [if_neg h2]
      by_cases h3 : l2 < 60
      · simp only [if_pos h3]; exact Nat.zero_le 2
      · simp only [if_neg h3]
        by_cases h4 : l2 < 80
        · simp only [if_pos h4]; exact Nat.zero_le 3
        · simp only [if_neg h4]; exact Nat.zero_le 4
  · simp only [if_neg h1]
    by_cases h2 : l1 < 60
    · simp only [if_pos h2]
      by_cases h3 : l2 < 40
      · omega
      · simp only [if_neg h3]
        by_cases h4 : l2 < 60
        · simp only [if_pos h4]; exact Nat.le_refl 2
        · simp only [if_neg h4]
          by_cases h5 : l2 < 80
          · simp only [if_pos h5]; exact Nat.le_of_lt (by native_decide : 2 < 3)
          · simp only [if_neg h5]; exact Nat.le_of_lt (by native_decide : 2 < 4)
    · simp only [if_neg h2]
      by_cases h3 : l1 < 80
      · simp only [if_pos h3]
        by_cases h4 : l2 < 40
        · omega
        · simp only [if_neg h4]
          by_cases h5 : l2 < 60
          · omega
          · simp only [if_neg h5]
            by_cases h6 : l2 < 80
            · simp only [if_pos h6]; exact Nat.le_refl 3
            · simp only [if_neg h6]; exact Nat.le_of_lt (by native_decide : 3 < 4)
      · simp only [if_neg h3]
        by_cases h4 : l2 < 40
        · omega
        · simp only [if_neg h4]
          by_cases h5 : l2 < 60
          · omega
          · simp only [if_neg h5]
            by_cases h6 : l2 < 80
            · omega
            · simp only [if_neg h6]; exact Nat.le_refl 4

-- Specific boundary tests (matching Python test vectors)
theorem complexity_10_is_none : complexity_to_severity 10 = Severity.none := by native_decide
theorem complexity_11_is_low : complexity_to_severity 11 = Severity.low := by native_decide
theorem complexity_20_is_low : complexity_to_severity 20 = Severity.low := by native_decide
theorem complexity_21_is_medium : complexity_to_severity 21 = Severity.medium := by native_decide
theorem complexity_30_is_medium : complexity_to_severity 30 = Severity.medium := by native_decide
theorem complexity_31_is_high : complexity_to_severity 31 = Severity.high := by native_decide
theorem complexity_100_is_high : complexity_to_severity 100 = Severity.high := by native_decide

theorem god_class_14_is_none : method_count_to_severity 14 = Severity.none := by native_decide
theorem god_class_15_is_medium : method_count_to_severity 15 = Severity.medium := by native_decide
theorem god_class_20_is_high : method_count_to_severity 20 = Severity.high := by native_decide
theorem god_class_30_is_critical : method_count_to_severity 30 = Severity.critical := by native_decide

theorem lcom_39_is_none : lcom_to_severity 39 = Severity.none := by native_decide
theorem lcom_40_is_medium : lcom_to_severity 40 = Severity.medium := by native_decide
theorem lcom_60_is_high : lcom_to_severity 60 = Severity.high := by native_decide
theorem lcom_80_is_critical : lcom_to_severity 80 = Severity.critical := by native_decide

end Repotoire.Thresholds
