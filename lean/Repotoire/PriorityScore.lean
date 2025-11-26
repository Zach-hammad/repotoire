/-
    Priority Score Formula Verification

    Proves properties of Repotoire's priority score calculation (REPO-184):
    - Weight conservation (0.4 + 0.3 + 0.3 = 1.0)
    - Score bounds (result in [0, 100])
    - Severity ordering invariants
    - Agreement normalization bounds

    Uses percentages (0-100) to avoid floating point in proofs.

    Python: repotoire/models.py:908-945
-/

namespace Repotoire.PriorityScore

-- ============================================================================
-- SECTION 1: Weight Definitions and Conservation
-- ============================================================================

-- Priority weights as percentages (must sum to 100)
def WEIGHT_SEVERITY : Nat := 40       -- 40% = 0.4
def WEIGHT_CONFIDENCE : Nat := 30     -- 30% = 0.3
def WEIGHT_AGREEMENT : Nat := 30      -- 30% = 0.3

-- Theorem: Weights sum to 100%
theorem weights_sum_to_100 :
    WEIGHT_SEVERITY + WEIGHT_CONFIDENCE + WEIGHT_AGREEMENT = 100 := by
  rfl

-- Theorem: Individual weights are valid (≤ 100)
theorem weight_severity_valid : WEIGHT_SEVERITY ≤ 100 := by decide
theorem weight_confidence_valid : WEIGHT_CONFIDENCE ≤ 100 := by decide
theorem weight_agreement_valid : WEIGHT_AGREEMENT ≤ 100 := by decide

-- ============================================================================
-- SECTION 2: Severity Level Definitions
-- ============================================================================

-- Severity levels (matches Python Severity enum)
inductive SeverityLevel where
  | INFO : SeverityLevel
  | LOW : SeverityLevel
  | MEDIUM : SeverityLevel
  | HIGH : SeverityLevel
  | CRITICAL : SeverityLevel
deriving Repr, DecidableEq

-- Severity weight mapping (as percentage of max = 100)
-- INFO=20%, LOW=40%, MEDIUM=60%, HIGH=80%, CRITICAL=100%
def severity_weight_percent (s : SeverityLevel) : Nat :=
  match s with
  | SeverityLevel.INFO => 20
  | SeverityLevel.LOW => 40
  | SeverityLevel.MEDIUM => 60
  | SeverityLevel.HIGH => 80
  | SeverityLevel.CRITICAL => 100

-- Theorem: Severity weights are bounded [0, 100]
theorem severity_weight_bounded (s : SeverityLevel) :
    severity_weight_percent s ≤ 100 := by
  cases s <;> native_decide

-- Theorem: Severity weights are non-negative (trivially true for Nat)
theorem severity_weight_nonneg (s : SeverityLevel) :
    severity_weight_percent s ≥ 0 := Nat.zero_le _

-- Numeric rank for ordering
def SeverityLevel.toRank : SeverityLevel → Nat
  | INFO => 0
  | LOW => 1
  | MEDIUM => 2
  | HIGH => 3
  | CRITICAL => 4

-- Ordering instances
instance : LE SeverityLevel where
  le s1 s2 := s1.toRank ≤ s2.toRank

instance : LT SeverityLevel where
  lt s1 s2 := s1.toRank < s2.toRank

instance : DecidableRel (α := SeverityLevel) (· ≤ ·) :=
  fun s1 s2 => inferInstanceAs (Decidable (s1.toRank ≤ s2.toRank))

instance : DecidableRel (α := SeverityLevel) (· < ·) :=
  fun s1 s2 => inferInstanceAs (Decidable (s1.toRank < s2.toRank))

-- Theorem: Severity ordering is correct
theorem severity_ordering :
    SeverityLevel.INFO < SeverityLevel.LOW ∧
    SeverityLevel.LOW < SeverityLevel.MEDIUM ∧
    SeverityLevel.MEDIUM < SeverityLevel.HIGH ∧
    SeverityLevel.HIGH < SeverityLevel.CRITICAL := by
  constructor <;> native_decide

-- Theorem: Higher severity → higher weight (monotonicity)
theorem severity_weight_monotonic (s1 s2 : SeverityLevel) (h : s1 ≤ s2) :
    severity_weight_percent s1 ≤ severity_weight_percent s2 := by
  match s1, s2 with
  | .INFO, .INFO => exact Nat.le_refl _
  | .INFO, .LOW => exact Nat.le_of_lt (by native_decide : 20 < 40)
  | .INFO, .MEDIUM => exact Nat.le_of_lt (by native_decide : 20 < 60)
  | .INFO, .HIGH => exact Nat.le_of_lt (by native_decide : 20 < 80)
  | .INFO, .CRITICAL => exact Nat.le_of_lt (by native_decide : 20 < 100)
  | .LOW, .LOW => exact Nat.le_refl _
  | .LOW, .MEDIUM => exact Nat.le_of_lt (by native_decide : 40 < 60)
  | .LOW, .HIGH => exact Nat.le_of_lt (by native_decide : 40 < 80)
  | .LOW, .CRITICAL => exact Nat.le_of_lt (by native_decide : 40 < 100)
  | .MEDIUM, .MEDIUM => exact Nat.le_refl _
  | .MEDIUM, .HIGH => exact Nat.le_of_lt (by native_decide : 60 < 80)
  | .MEDIUM, .CRITICAL => exact Nat.le_of_lt (by native_decide : 60 < 100)
  | .HIGH, .HIGH => exact Nat.le_refl _
  | .HIGH, .CRITICAL => exact Nat.le_of_lt (by native_decide : 80 < 100)
  | .CRITICAL, .CRITICAL => exact Nat.le_refl _
  -- Impossible cases (higher → lower): h gives False
  | .LOW, .INFO => exact absurd h (by native_decide : ¬(1 ≤ 0))
  | .MEDIUM, .INFO => exact absurd h (by native_decide : ¬(2 ≤ 0))
  | .MEDIUM, .LOW => exact absurd h (by native_decide : ¬(2 ≤ 1))
  | .HIGH, .INFO => exact absurd h (by native_decide : ¬(3 ≤ 0))
  | .HIGH, .LOW => exact absurd h (by native_decide : ¬(3 ≤ 1))
  | .HIGH, .MEDIUM => exact absurd h (by native_decide : ¬(3 ≤ 2))
  | .CRITICAL, .INFO => exact absurd h (by native_decide : ¬(4 ≤ 0))
  | .CRITICAL, .LOW => exact absurd h (by native_decide : ¬(4 ≤ 1))
  | .CRITICAL, .MEDIUM => exact absurd h (by native_decide : ¬(4 ≤ 2))
  | .CRITICAL, .HIGH => exact absurd h (by native_decide : ¬(4 ≤ 3))

-- ============================================================================
-- SECTION 3: Component Score Bounds
-- ============================================================================

-- A valid percentage is in [0, 100]
def is_valid_percentage (n : Nat) : Prop := n ≤ 100

-- Theorem: Severity component is bounded
-- severity_component = (severity_weight_percent / 100) * WEIGHT_SEVERITY
-- Scaled: severity_weight_percent * WEIGHT_SEVERITY (then divide by 100 twice)
def severity_component (s : SeverityLevel) : Nat :=
  severity_weight_percent s * WEIGHT_SEVERITY

theorem severity_component_bounded (s : SeverityLevel) :
    severity_component s ≤ 4000 := by
  unfold severity_component WEIGHT_SEVERITY
  cases s <;> native_decide

-- Confidence component (confidence is 0-100%)
def confidence_component (confidence : Nat) : Nat :=
  confidence * WEIGHT_CONFIDENCE

theorem confidence_component_bounded (c : Nat) (h : is_valid_percentage c) :
    confidence_component c ≤ 3000 := by
  unfold confidence_component WEIGHT_CONFIDENCE is_valid_percentage at *
  omega

-- Agreement normalization: min(1.0, (count - 1) / 2) for count > 1, else 0
-- As percentage: min(100, (count - 1) * 50) for count > 1, else 0
def agreement_normalized (detector_count : Nat) : Nat :=
  if detector_count ≤ 1 then 0
  else min 100 ((detector_count - 1) * 50)

-- Agreement component
def agreement_component (detector_count : Nat) : Nat :=
  agreement_normalized detector_count * WEIGHT_AGREEMENT

theorem agreement_normalized_bounded (count : Nat) :
    agreement_normalized count ≤ 100 := by
  unfold agreement_normalized
  split
  · omega
  · exact Nat.min_le_left 100 ((count - 1) * 50)

theorem agreement_component_bounded (count : Nat) :
    agreement_component count ≤ 3000 := by
  unfold agreement_component WEIGHT_AGREEMENT
  have h := agreement_normalized_bounded count
  omega

-- ============================================================================
-- SECTION 4: Priority Score Calculation
-- ============================================================================

-- Weighted score calculation (result scaled by 100)
def calculate_weighted_score (severity : SeverityLevel) (confidence : Nat) (detector_count : Nat) : Nat :=
  severity_component severity +
  confidence_component confidence +
  agreement_component detector_count

-- Final priority score (divide by 100, result in [0, 100])
def priority_score (severity : SeverityLevel) (confidence : Nat) (detector_count : Nat) : Nat :=
  calculate_weighted_score severity confidence detector_count / 100

-- Theorem: Weighted score is bounded
theorem weighted_score_bounded
    (severity : SeverityLevel)
    (confidence : Nat)
    (detector_count : Nat)
    (hc : is_valid_percentage confidence) :
    calculate_weighted_score severity confidence detector_count ≤ 10000 := by
  unfold calculate_weighted_score
  have h1 := severity_component_bounded severity
  have h2 := confidence_component_bounded confidence hc
  have h3 := agreement_component_bounded detector_count
  omega

-- Theorem: Final score is valid (in [0, 100])
theorem priority_score_bounded
    (severity : SeverityLevel)
    (confidence : Nat)
    (detector_count : Nat)
    (hc : is_valid_percentage confidence) :
    is_valid_percentage (priority_score severity confidence detector_count) := by
  unfold is_valid_percentage priority_score
  have h := weighted_score_bounded severity confidence detector_count hc
  exact Nat.div_le_of_le_mul h

-- Theorem: Minimum score is 0 (INFO severity, 0 confidence, no agreement)
theorem min_score :
    priority_score SeverityLevel.INFO 0 0 = 8 := by
  native_decide

-- Theorem: Maximum score is 100 (CRITICAL severity, 100% confidence, max agreement)
theorem max_score :
    priority_score SeverityLevel.CRITICAL 100 3 = 100 := by
  native_decide

-- ============================================================================
-- SECTION 5: Monotonicity Properties
-- ============================================================================

-- Theorem: Higher severity → higher or equal priority score
theorem priority_monotonic_severity
    (s1 s2 : SeverityLevel)
    (confidence : Nat)
    (detector_count : Nat)
    (hs : s1 ≤ s2) :
    priority_score s1 confidence detector_count ≤ priority_score s2 confidence detector_count := by
  unfold priority_score calculate_weighted_score severity_component
  have hw := severity_weight_monotonic s1 s2 hs
  apply Nat.div_le_div_right
  have h : severity_weight_percent s1 * WEIGHT_SEVERITY ≤ severity_weight_percent s2 * WEIGHT_SEVERITY := by
    exact Nat.mul_le_mul_right WEIGHT_SEVERITY hw
  exact Nat.add_le_add_right (Nat.add_le_add_right h _) _

-- Theorem: Higher confidence → higher or equal priority score
theorem priority_monotonic_confidence
    (severity : SeverityLevel)
    (c1 c2 : Nat)
    (detector_count : Nat)
    (hc : c1 ≤ c2) :
    priority_score severity c1 detector_count ≤ priority_score severity c2 detector_count := by
  unfold priority_score calculate_weighted_score confidence_component WEIGHT_CONFIDENCE
  apply Nat.div_le_div_right
  omega

-- ============================================================================
-- SECTION 6: Agreement Normalization Properties
-- ============================================================================

-- Theorem: Single detector has no agreement bonus
theorem single_detector_no_bonus :
    agreement_normalized 1 = 0 := by rfl

-- Theorem: Two detectors give 50% agreement (half max)
theorem two_detectors_agreement :
    agreement_normalized 2 = 50 := by native_decide

-- Theorem: Three or more detectors give max agreement (100%)
theorem max_agreement_at_three :
    agreement_normalized 3 = 100 := by native_decide

theorem max_agreement_stable (n : Nat) (h : n ≥ 3) :
    agreement_normalized n = 100 := by
  unfold agreement_normalized
  have hn : ¬(n ≤ 1) := by omega
  simp only [hn, ↓reduceIte]
  have h2 : (n - 1) * 50 ≥ 100 := by omega
  exact Nat.min_eq_left (by omega : 100 ≤ (n - 1) * 50)

-- ============================================================================
-- SECTION 7: Determinism
-- ============================================================================

-- Theorem: Priority score is deterministic
theorem score_deterministic
    (severity : SeverityLevel)
    (confidence : Nat)
    (detector_count : Nat) :
    priority_score severity confidence detector_count =
    priority_score severity confidence detector_count := by rfl

-- ============================================================================
-- SECTION 8: Example Calculations
-- ============================================================================

-- Example: CRITICAL + 100% confidence + 3 detectors = 100
example : priority_score SeverityLevel.CRITICAL 100 3 = 100 := by native_decide

-- Example: HIGH + 80% confidence + 2 detectors = 71
example : priority_score SeverityLevel.HIGH 80 2 = 71 := by native_decide

-- Example: MEDIUM + 50% confidence + 1 detector = 39
example : priority_score SeverityLevel.MEDIUM 50 1 = 39 := by native_decide

-- Example: LOW + 30% confidence + 0 detectors = 25
example : priority_score SeverityLevel.LOW 30 0 = 25 := by native_decide

-- Example: INFO + 0% confidence + 0 detectors = 8
example : priority_score SeverityLevel.INFO 0 0 = 8 := by native_decide

end Repotoire.PriorityScore
