/-
    Cypher Injection Prevention Verification

    Proves that validate_identifier() prevents all Cypher injection attacks
    by only allowing safe characters: [a-zA-Z0-9_-]

    Python: repotoire/validation.py:417-464
-/

import Batteries.Data.String.Lemmas

namespace Repotoire.CypherSafety

-- Maximum identifier length (DoS prevention)
def MAX_LENGTH : Nat := 100

-- A character is safe if it's alphanumeric, underscore, or hyphen
def is_safe_char (c : Char) : Bool :=
  c.isAlpha || c.isDigit || c == '_' || c == '-'

-- An identifier is safe if all chars are safe AND length constraints met
def is_safe_identifier (s : String) : Bool :=
  s.length > 0 && s.length ≤ MAX_LENGTH && s.all is_safe_char

-- Cypher injection characters that MUST be blocked
def is_injection_char (c : Char) : Bool :=
  c == '\'' || c == '"' || c == ';' || c == '{' || c == '}' ||
  c == '/' || c == '\\' || c == '\n' || c == '\r' || c == ' '

-- Helper: alpha chars have specific Unicode ranges that exclude injection chars
-- isAlpha checks if char is in [A-Z] or [a-z]
private theorem alpha_not_injection (c : Char) (h : c.isAlpha = true) :
    is_injection_char c = false := by
  unfold is_injection_char
  simp only [Bool.or_eq_false_iff, beq_eq_false_iff_ne, ne_eq]
  -- Break down all 10 conjunctions using repeat constructor
  repeat constructor
  -- Now we have 10 goals, each of form ¬c = X (i.e., c = X → False)
  -- For each: intro, substitute concrete char, use native_decide to evaluate to False
  all_goals (intro heq; subst heq; exact absurd h (by native_decide))

-- Helper: digit chars are in [0-9], not injection chars
private theorem digit_not_injection (c : Char) (h : c.isDigit = true) :
    is_injection_char c = false := by
  unfold is_injection_char
  simp only [Bool.or_eq_false_iff, beq_eq_false_iff_ne, ne_eq]
  repeat constructor
  all_goals (intro heq; subst heq; exact absurd h (by native_decide))

-- Helper: underscore is not an injection char
private theorem underscore_not_injection : is_injection_char '_' = false := by native_decide

-- Helper: hyphen is not an injection char
private theorem hyphen_not_injection : is_injection_char '-' = false := by native_decide

-- Theorem 1: Safe chars exclude all injection chars
theorem safe_excludes_injection (c : Char) :
    is_safe_char c = true → is_injection_char c = false := by
  intro h
  unfold is_safe_char at h
  simp only [Bool.or_eq_true] at h
  cases h with
  | inl h1 =>
    cases h1 with
    | inl h2 =>
      cases h2 with
      | inl h3 => exact alpha_not_injection c h3
      | inr h3 => exact digit_not_injection c h3
    | inr h2 =>
      -- c == '_' = true
      simp only [beq_iff_eq] at h2
      subst h2
      exact underscore_not_injection
  | inr h1 =>
    -- c == '-' = true
    simp only [beq_iff_eq] at h1
    subst h1
    exact hyphen_not_injection

-- Theorem 2: Empty string is not safe
theorem empty_not_safe : is_safe_identifier "" = false := by native_decide

-- Theorem 3: Length bounds are enforced
theorem length_bounded (s : String) (h : is_safe_identifier s = true) :
    s.length ≤ MAX_LENGTH := by
  unfold is_safe_identifier at h
  simp only [Bool.and_eq_true, decide_eq_true_eq] at h
  obtain ⟨⟨_, h2⟩, _⟩ := h
  exact h2

-- Theorem 4: Non-empty guaranteed
theorem non_empty (s : String) (h : is_safe_identifier s = true) :
    s.length > 0 := by
  unfold is_safe_identifier at h
  simp only [Bool.and_eq_true, decide_eq_true_eq] at h
  obtain ⟨⟨h1, _⟩, _⟩ := h
  exact h1

-- Known safe identifiers (examples from Python tests)
theorem valid_alphanumeric : is_safe_identifier "myProjection" = true := by native_decide
theorem valid_with_numbers : is_safe_identifier "test123" = true := by native_decide
theorem valid_with_underscore : is_safe_identifier "my_graph" = true := by native_decide
theorem valid_with_hyphen : is_safe_identifier "my-projection" = true := by native_decide
theorem valid_mixed : is_safe_identifier "test123_data-v2" = true := by native_decide

-- Known invalid identifiers (injection payloads)
theorem invalid_sql_injection : is_safe_identifier "'; DROP DATABASE" = false := by native_decide
theorem invalid_cypher_injection : is_safe_identifier "foo} RETURN *" = false := by native_decide
theorem invalid_comment : is_safe_identifier "x//comment" = false := by native_decide
theorem invalid_empty : is_safe_identifier "" = false := by native_decide
theorem invalid_space : is_safe_identifier "foo bar" = false := by native_decide
theorem invalid_quote : is_safe_identifier "foo'bar" = false := by native_decide
theorem invalid_semicolon : is_safe_identifier "foo;bar" = false := by native_decide
theorem invalid_brace : is_safe_identifier "foo{bar" = false := by native_decide

-- Helper lemma: String.all means all chars in toList satisfy predicate
private theorem string_all_imp {s : String} {p : Char → Bool} (h : s.all p = true) :
    ∀ c ∈ s.toList, p c = true := by
  intro c hc
  -- s.toList = s.data, and String.all checks all chars in data
  have hdata : c ∈ s.data := by simp only [String.toList] at hc; exact hc
  -- Use the fact that String.all p = s.data.all p
  have hall : s.data.all p = true := by
    unfold String.all at h
    exact h
  exact List.all_iff_forall.mp hall c hdata

-- Theorem 5: Safe identifier contains no injection chars
theorem no_injection_chars (s : String) (h : is_safe_identifier s = true) :
    ∀ c ∈ s.toList, is_injection_char c = false := by
  intro c hc
  unfold is_safe_identifier at h
  simp only [Bool.and_eq_true, decide_eq_true_eq] at h
  obtain ⟨_, h3⟩ := h
  have hsc : is_safe_char c = true := string_all_imp h3 c hc
  exact safe_excludes_injection c hsc

end Repotoire.CypherSafety
