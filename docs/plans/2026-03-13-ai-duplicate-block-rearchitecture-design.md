# AIDuplicateBlockDetector Rearchitecture — Semantic-Aware Clone Detection

## Problem

The AIDuplicateBlockDetector produces 5 findings on self-analysis (11 after compound merging), ALL false positives. The detector flags structurally similar small functions as duplicates because AST fingerprinting normalizes ALL identifiers to `$ID` and ALL literals to `$LIT`/`$STR`/`$CONST`, making any match-on-enum function look identical regardless of semantic meaning.

### False Positives

| Pair | Why flagged | Why it's FP |
|------|-------------|-------------|
| `with_config`/`with_config` | Builder pattern, same AST shape | Different detectors, different fields |
| `default_model`/`title` | Match on enum → return `&str` | Different enums (`LlmBackend` vs something else) |
| `fmt`/`fmt` | Display trait impls | Different types, required by trait |
| `severity_weight`/`severity_to_security_score` | Match on Severity → return f64 | Different semantic meaning, different values |
| `is_ai_detector`/`has_training_code` | String pattern checks | Checking for different strings |

### Root Cause

In `collect_all_features()` (ast_fingerprint.rs:672), ALL leaf identifiers become `$ID`:
```rust
"identifier" => { normalized_tokens.push("$ID".to_string()); }
"property_identifier" | "field_identifier" | "type_identifier" => {
    normalized_tokens.push("$ID".to_string());
}
```

This means `match self { Severity::Critical => 5.0 }` and `match self { LlmBackend::Gpt4 => "gpt-4" }` produce identical bigram sequences.

## Design: Three-Layer Semantic Clone Detection

### Layer 1: Selective Normalization

**Principle**: Not all identifiers are equal. Local variables are "noise" (Type-2 clone variation), but function calls, type names, and enum variants carry semantic meaning.

**Change**: In `collect_all_features()`, instead of normalizing ALL identifiers to `$ID`, categorize by AST context:

| AST Context | Current | New | Rationale |
|-------------|---------|-----|-----------|
| Local variable (`let x = ...`) | `$ID` | `$ID` | Renamed in Type-2 clones |
| Parameter name | `$ID` | `$ID` | Renamed in Type-2 clones |
| Function/method call target | `$ID` | **preserve actual name** | `validate()` ≠ `transform()` |
| Type name (in type position) | `$ID` | **preserve actual name** | `Severity` ≠ `LlmBackend` |
| Enum variant (scoped identifier) | `$ID` | **preserve actual name** | `Critical` ≠ `Gpt4` |
| Field access (`self.x`) | `$ID` | **preserve actual name** | `self.name` ≠ `self.age` |
| String literal content | `$STR` | **preserve actual content** | `"unwrap"` ≠ `"SELECT"` |

**Implementation**: Use the tree-sitter AST parent context to determine identifier role. In tree-sitter:
- **Call target**: Parent is `call_expression` and child is the `function` field
- **Type name**: Node kind is `type_identifier` or parent is type annotation
- **Enum variant**: Part of `scoped_identifier` (Rust), `member_expression` (JS), `attribute` (Python)
- **Field access**: Part of `field_expression` (Rust), `member_expression` (JS), `attribute` (Python)
- **Local variable**: `identifier` node where parent is `let_declaration`, `parameter`, `assignment`, etc.

**Expected impact**: Eliminates FPs #2 (default_model/title), #4 (severity_weight/severity_to_security_score), #5 (is_ai_detector/has_training_code) directly. Functions matching on different enum types or calling different functions will now produce different bigrams.

### Layer 2: Graph-Verified Semantic Overlap

**Principle**: After MinHash/LSH finds structurally similar candidates, verify semantic overlap using the knowledge graph. Two functions with identical AST shapes but completely different call graphs are structurally coincidental, not duplicated.

Three-tier verification using existing `GraphQuery` API:

#### Tier 1: Trait Impl Filter

Rust qualified names encode trait implementations as `impl<TraitName for TypeName>::method`. If both functions are trait impls of the **same trait** on **different types**, reject — implementing the same trait on different types is idiomatic, not duplication.

```
"src/foo.rs::impl<Display for BootstrapStatus>::fmt:30"
"src/bar.rs::impl<Display for AnalysisResult>::fmt:50"
→ Same trait (Display), different types → REJECT
```

Detects by parsing qualified names (existing pattern from `lazy_class.rs`).

**Expected impact**: Eliminates FP #3 (fmt/fmt).

#### Tier 2: Callee Overlap Check

For functions WITH outgoing `Calls` edges, compute callee set overlap:
```
callee_overlap = |callees_A ∩ callees_B| / |callees_A ∪ callees_B|
```

If `callee_overlap < 0.3` → reject. Functions that call completely different downstream functions are not real clones even if their structure is similar.

Uses `graph.get_callees(qn)` — existing O(1) lookup + O(fan-out) edge iteration.

**Expected impact**: Catches real copy-paste clones (e.g., `process_user`/`process_order` both calling `validate()` + `transform()`) while rejecting coincidentally-shaped functions that call different things.

#### Tier 3: Leaf Function Context Check

For functions with NO outgoing calls (leaf functions — match expressions, getters, builders), require EITHER:
- **Same containing type** (same `impl` block or same class), OR
- **≥95% AST similarity** (extremely high bar for tiny functions)

The containing type is extracted from the qualified name:
- Rust: `impl<Type>` or `impl<Trait for Type>` → extract `Type`
- Other languages: `ClassName.method` → extract `ClassName`

**Expected impact**: Eliminates FP #1 (with_config/with_config — different detector types) and FP #4 (severity_weight/severity_to_security_score — same type but already caught by Layer 1).

### Layer 3: Size-Adaptive Threshold

**Principle**: Small functions have fewer distinguishing bigrams, so chance coincidental similarity is higher. Scale the similarity threshold by function size.

| Function size (LOC) | Threshold |
|---------------------|-----------|
| 5–10 | 0.90 |
| 11–20 | 0.80 |
| 21+ | 0.70 (current default) |

Implementation: linear interpolation in `find_duplicates()` between the function pair's average LOC and the threshold.

**Expected impact**: All 5 FPs are 5-10 LOC functions. Raising threshold to 0.90 for small functions adds another safety net.

## Files to Modify

- **`src/detectors/ast_fingerprint.rs`** — Modify `collect_all_features()` for selective normalization
- **`src/detectors/ai_duplicate_block.rs`** — Add graph verification in `find_duplicates()`, size-adaptive threshold

## What NOT to Change

- MinHash/LSH infrastructure (hash functions, banding, signature computation) — working correctly
- Test function filtering — already implemented
- `FunctionData` / `FunctionFingerprints` structs — extend if needed, don't replace
- Other detectors using `ast_fingerprint.rs` (AIBoilerplate uses structural_kinds, not normalized_bigrams)

## Verification

```bash
cargo test ai_duplicate    # All existing tests pass
cargo test ast_fingerprint # Fingerprinting tests pass
cargo run --release -- analyze .  # 0 AIDuplicateBlock FPs on self-analysis
```

Existing test `test_detects_near_duplicates` (process_user/process_order with same structure, different variable names) must still pass — these are genuine Type-2 clones with the same call targets (`validate`, `transform`).
