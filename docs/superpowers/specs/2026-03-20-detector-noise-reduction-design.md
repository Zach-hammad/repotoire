# Detector Noise Reduction Design

*2026-03-20*

## Problem

Repotoire averages 1,526 findings per repo across 56 popular open-source projects. The target is 20-80 high-confidence findings per repo — each worth opening a PR for. The top 10 noisiest detectors produce 59% of all findings and fall into three categories of brokenness:

1. **Wrong mental model** — the detector measures the wrong signal
2. **Missing context** — the detector can't distinguish real issues from noise
3. **Wrong calibration** — the detector's thresholds don't match how code is written in 2026

## Goal

Reduce average findings per repo from ~1,500 to ~50-80 by fixing the root cause in each detector — not by hiding findings or raising thresholds arbitrarily. Every remaining finding should be something a developer would act on.

## Non-Goals

- Finding budget / ranking system (masks the problem)
- Tiered detection modes (splits the product)
- Removing detectors entirely (the concepts are valid, the implementations need fixing)

---

## Evidence Base

Data from analyzing 56 popular open-source repos (ripgrep, django, react, cargo, redis, etc.):

| Detector | Findings | Repos | Root Cause |
|---|---|---|---|
| LazyClassDetector | 8,418 | 38 | Wrong model: small class ≠ lazy class |
| DataClumpsDetector | 8,350 | 54 | Missing context: no refactorability check |
| ModuleCohesionDetector | 5,683 | 49 | Wrong calibration + dead Louvain code |
| CoreUtilityDetector | 5,528 | 48 | Wrong model: utility function ≠ smell |
| AIComplexitySpikeDetector | 4,773 | 56 | Missing context: no way to distinguish intentional vs accidental complexity |
| ShotgunSurgeryDetector | 3,939 | 40 | Wrong model: widely-called ≠ risky (2,179 criticals) |
| LongMethodsDetector | 3,176 | 56 | Wrong calibration: 50-line threshold + over-engineered discounting |
| MagicNumbersDetector | 3,172 | 54 | Missing context: flags constants in declarations, not just logic |
| DeepNestingDetector | 2,957 | 49 | Wrong calibration: 4-level threshold too low for Rust/Go/Java |

---

## Prerequisite: Git History Plumbing for Detectors

Several rearchitected detectors (ChangeCoupling, ComplexityOutlier) require git history data that is not currently available in `AnalysisContext`. This plumbing must be implemented first.

**Current state:** `AnalysisContext` (at `src/detectors/analysis_context.rs`) has no field for git history, `CoChangeMatrix`, or per-file churn data. The `CoChangeMatrix` is consumed during `GraphPrimitives::compute()` at freeze time — its per-pair weights are baked into the weighted overlay graph but not directly queryable per-node from detectors. `GitHistory::get_file_churn()` exists but is not exposed to detectors.

**Required changes:**

1. Add `git_churn: Option<HashMap<PathBuf, FileChurnInfo>>` to `AnalysisContext`, populated during the git_enrich stage. `FileChurnInfo` contains `commits_90d: u32`, `last_modified: DateTime<Utc>`, `age_days: u32`.
2. Add `co_change_summary: Option<HashMap<NodeIndex, f64>>` to `AnalysisContext` — a per-node aggregate co-change score (sum of co-change weights with all callers), computed from `CoChangeMatrix` during freeze and stored in `GraphPrimitives`.
3. These are `Option` fields — detectors gracefully degrade when git data is unavailable (shallow clones, non-git directories).

---

## Category 1: Wrong Mental Model (Rearchitect)

### 1.1 ShotgunSurgeryDetector → Change Coupling Detector

**Current behavior:** Flags classes/functions with high fan-in across multiple modules. `Flask` (120 callers, 30 files, 5 modules) is flagged as CRITICAL.

**Why it's wrong:** A widely-called class with a stable API is safe infrastructure, not a risk. The real shotgun surgery risk is code that is both widely depended on AND frequently changing — every change forces updates across callers.

**New algorithm:**

```
risk = normalized_co_change × churn_rate
```

Where:
- `normalized_co_change`: `co_change_summary[node] / fan_in` — average co-change weight per caller. Normalized to [0, 1] range so the formula produces bounded output regardless of fan_in scale.
- `churn_rate`: `min(commits_90d / 9.0, 1.0)` — 9+ commits in 90 days saturates at 1.0. From `git_churn` in `AnalysisContext`.

The formula produces values in [0, 1]. No unbounded multiplication.

**Detection logic:**
1. For each class/function with fan_in >= 10
2. Look up `co_change_summary[node]` from `AnalysisContext`. If unavailable (no git data), skip this detector entirely.
3. `normalized_co_change = co_change_summary[node] / fan_in`
4. Look up `churn_rate` from `git_churn`. If unavailable, skip.
5. `risk = normalized_co_change × churn_rate`
6. Only flag if risk > 0.1 AND fan_in >= 10

**Severity:**
- risk > 0.5 AND fan_in >= 30 → Critical
- risk > 0.3 AND fan_in >= 15 → High
- risk > 0.1 → Medium
- Below → don't flag

**Fallback:** If git history is unavailable (shallow clone, non-git repo), this detector produces zero findings. This is correct — without change history, coupling risk cannot be assessed.

**What this fixes:** `Flask` has high fan_in but near-zero churn_rate → risk ≈ 0 → not flagged. A volatile internal module with 15 callers and daily changes → risk > 0.3 → flagged as High.

**Rename:** `ShotgunSurgeryDetector` → `ChangeCouplingDetector`. The finding title changes from "Shotgun Surgery Risk" to "Change Coupling Risk: {name} (changed {N} times, {M} callers affected)".

**Backward compatibility:** Old detector name kept as an alias in config parsing and `repotoire:ignore` suppression comments. Old SARIF rule IDs mapped to new ones. Findings cache invalidated on upgrade (expected: first run after upgrade is a full analysis).

### 1.2 CoreUtilityDetector → Remove or Demote to Informational

**Current behavior:** Flags functions with fan_in >= 10 from >= 3 modules. 79% of findings are Info severity. The detector itself knows these aren't problems.

**Why it's wrong:** A widely-used utility function IS the correct abstraction. Flagging `Render` (27 directories) or `String` (10 callers) as a finding tells users their well-factored code is wrong.

**Decision: Remove from default findings output.**

Core utility detection is useful *context* for other detectors (ShotgunSurgery/ChangeCoupling needs to know what's a utility) and for the architecture overview, but it should not produce findings.

**Implementation:**
1. Keep the detection logic — other detectors and the architecture report use it
2. Remove it from the `DETECTOR_FACTORIES` registry (it no longer produces findings)
3. **Pre-compute during graph freeze:** Add a `core_utilities: HashSet<NodeIndex>` field to `GraphPrimitives`. Compute it during `GraphPrimitives::compute()` (alongside PageRank, betweenness, etc.) using the existing CoreUtility logic (fan_in >= threshold, fan_out <= 2, cross-module callers >= 3). This runs once at O(n) and is O(1) to query.
4. Expose via `GraphQuery` trait: `fn is_core_utility(&self, idx: NodeIndex) -> bool` — returns true if the node is in the pre-computed set. Other detectors (ChangeCoupling, RedundantClass) call this to adjust their behavior.
5. If a user explicitly wants to see utilities, `repotoire graph . utilities` already exists

### 1.3 LazyClassDetector → Redundant Class Detector

**Current behavior:** Flags classes with <= 3 methods AND <= 50 LOC AND < 5 external callers. Flags `CertParamType` (2 methods, 46 LOC) — an idiomatic C struct wrapper.

**Why it's wrong:** Small, focused classes are GOOD design. The real smell isn't "small class" — it's "class that duplicates behavior found elsewhere" or "class that wraps another class without adding value."

**New algorithm:**

1. For each class with <= 5 methods:
2. **Overlap check:** Check if another class in the same module (same directory) has overlapping method names. Match on **name + arity** (parameter count), not parameter types — type information is unreliable across languages (Python/JS/Go often lack type annotations in tree-sitter output). Two classes with 3+ methods sharing the same name+arity are likely redundant.
3. **Trivial wrapper check:** Use the call graph, not AST body inspection. A class is a trivial wrapper if: all of its methods have exactly 1 outgoing `Calls` edge, AND all those edges target methods on the same single other class. This is a graph-only check — no AST body parsing needed.
4. Only flag if (2) OR (3) is true
5. Skip: data classes (all fields, no methods beyond getters/constructors), enums, error types, trait implementations (Rust), interface implementations (Java/C#)

**Severity:**
- Trivial wrapper with 0 external callers → Medium ("consider inlining into the wrapped class")
- Overlapping with sibling class → Medium ("consider merging with {SiblingClass}")
- Otherwise → don't flag

**What this fixes:** `CertParamType` (standalone struct, no overlap, not a wrapper) → not flagged. Two classes `HttpClient` and `ApiClient` with identical `get()`, `post()`, `delete()` methods → flagged.

**Rename:** `LazyClassDetector` → `RedundantClassDetector`. Title: "Redundant Class: {name} (overlaps with {other})" or "Trivial Wrapper: {name} (delegates entirely to {inner})".

**Backward compatibility:** Old name kept as config/suppression alias. Cache invalidated on upgrade.

---

## Category 2: Missing Context (Enrich Detection)

### 2.1 AIComplexitySpikeDetector → Complexity Outlier Detector

**Current behavior:** Flags functions where complexity > mean + 2σ. Labels them "Possible AI-generated code." Fires in ALL 56 repos. Flags `flush` (complexity 95) in redis — a hand-crafted function in a 15-year-old project.

**Two problems:**
1. z_score = 2.0 is too sensitive (flags 2.3% of all functions)
2. "AI-generated" label is inflammatory and usually wrong

**New algorithm:**

Require a **compound signal** — complexity outlier alone is insufficient:

1. **Complexity outlier**: z_score >= 3.0 (top 0.3% instead of 2.3%)
2. **PLUS at least one of:**
   - **Naming anomaly**: function name is generic (`process`, `handle`, `execute`, `doStuff`, `run`, `func1`) — well-named complex functions are intentional
   - **Recency**: function is in a file with high churn (from `git_churn` in `AnalysisContext` — `commits_90d >= 5`). This is a file-level check, not per-function blame, which avoids the expensive per-line blame API. A complex function in a frequently-changing file is more likely to be accidental than one in a stable file.
   - **Structural anomaly**: function has both high complexity AND high nesting AND low cohesion (calls to many unrelated modules) — suggests accidental rather than intentional complexity
3. **AND** complexity >= 20 (absolute floor, not just relative)

**Severity:**
- All three compound signals → High ("Complexity outlier — likely needs refactoring")
- Two compound signals → Medium
- One compound signal → Low
- Complexity outlier alone → don't flag

**Rename:** Drop "AI" entirely. `AIComplexitySpikeDetector` → `ComplexityOutlierDetector`. Title: "Complexity Outlier: {name} (complexity {N}, {reason})".

**What this fixes:** redis `flush` (complexity 95, well-named, 15 years old, intentional) → not flagged. A recently-added `processData` with complexity 25 and calls to 8 unrelated modules → flagged as Medium.

### 2.2 DataClumpsDetector — Add Refactorability Check

**Current behavior:** Flags any 3+ parameters appearing together in 3+ functions. Flags `(buf, cmd, name)` — standard CLI handler arguments.

**Why it's wrong:** Parameter repetition is only a smell when creating a struct/record would actually improve the code. Repeated `(request, response, context)` in a web framework is the API convention, not a missing abstraction.

**New algorithm:**

Keep the existing parameter grouping logic, but add filters:

1. **Raise thresholds**: min_params 3 → 4, min_occurrences 3 → 5
2. **Add call-graph forwarding check**: Use the call graph to detect pure forwarding — if every function containing the clump has a `Calls` edge to the same target function, the clump is a pass-through API convention, not a missing abstraction. Only flag when at least 2 of the functions **use** the parameters independently (have different call targets or additional logic). This is a graph-only check, no AST body parsing needed.
3. **Deduplicate subsets**: if `(a, b, c)` and `(a, b, c, d)` are both detected, only report `(a, b, c, d)` — the larger clump subsumes the smaller.
4. **Skip framework conventions**: parameter groups matching known patterns are excluded. Initial list: `(req, res, next)`, `(ctx, w, r)`, `(self, other)`, `(request, *args, **kwargs)`, `(app, req, res)`, `(t, ctx)`. This list is maintained as a constant in the detector and can be extended via config (`data_clumps.skip_patterns`).

**Note:** The type-diversity check (at least 2 different types) is deferred — type information is unreliable across Python/JS/Go. The threshold raise and forwarding check alone should achieve ~80% reduction. Type-diversity can be added as a follow-up when type inference is more robust.

**Severity:**
- 5+ params, 8+ occurrences, used together in body → High
- 4+ params, 5+ occurrences → Medium
- Below → don't flag

**What this fixes:** `(buf, cmd, name)` appearing in 3 CLI handlers → not flagged (below new thresholds, likely just forwarded). `(user_id, account_id, transaction_id, amount)` appearing in 8 functions where all 4 are used in business logic → flagged as High.

### 2.3 MagicNumbersDetector — Context-Aware Filtering

**Current behavior:** Flags numbers not in the acceptable set. Already has a massive exemption list (0-99, HTTP codes, powers of 2). Still produces 428 findings on 8 repos.

**Why remaining findings are mostly noise:** Numbers like `750`, `156`, `280` in express.js are likely pixel dimensions, timeout values, or protocol constants that are meaningful in context.

**New algorithm:**

Add a **usage context requirement**:

1. Only flag magic numbers that appear in **conditional expressions** (`if x > 750`, `match n { 750 => ... }`, `while i < 750`) or **arithmetic** (`x * 750`, `x + 750`). The existing regex-based detector already classifies line context — extend the context classification to detect comparison operators (`<`, `>`, `<=`, `>=`, `==`, `!=`) and arithmetic operators (`+`, `-`, `*`, `/`, `%`) on the same line as the number.
2. Numbers in **declarations** (`const X = 750`, `let timeout = 750`, `x := 750`) are NOT magic — they're named constants. The detector already has `NAMED_CONST_PATTERN` — extend it to also match `let`/`var`/`:=` assignment patterns, not just `const`/`static`.
3. Numbers in **function arguments** (`set_timeout(750)`) are NOT flagged — resolving parameter names at call sites would require AST traversal, which is out of scope for this regex-based detector. Accept this as a false negative.
4. Numbers in **array/collection literals** (`[1, 2, 3, 750]`) are NOT magic — they're data. Detect by checking if the number is preceded by `[` or `,` and followed by `,` or `]`.
5. Require the number to appear in **2+ files** for Medium severity. Single-file magic numbers are not flagged (too localized to be a cross-cutting concern).

**Severity:**
- In conditional/arithmetic, 3+ files → Medium
- In conditional/arithmetic, 2 files → Low
- Single file or in declaration/argument → don't flag (or Info if `--verbose`)

**What this fixes:** `const TIMEOUT = 750` → not flagged. `if response.status > 750` → flagged (unlikely HTTP status). `setTimeout(750)` → not flagged (argument to a named parameter).

---

## Category 3: Wrong Calibration (Retune)

### 3.1 DeepNestingDetector — Language-Aware Thresholds

**Current:** Threshold 4, match discount 1.

**New defaults:**

| Language | Threshold | Match Discount | Rationale |
|---|---|---|---|
| Rust | 6 | 2 | `match` + `if let` patterns are idiomatic; 4 levels is normal |
| Go | 6 | 1 | `if err != nil` adds a level every error check |
| Java, C# | 6 | 2 | Deep class hierarchies, try-catch-finally |
| Python | 5 | 1 | Significant whitespace makes nesting visible; less idiomatic to nest deeply |
| TypeScript, JavaScript | 5 | 1 | Callback patterns, but modern async/await reduces nesting |
| C, C++ | 6 | 2 | Macro-heavy code, manual resource management |
| Default | 5 | 2 | |

**Severity (unchanged logic, new thresholds):**
- effective_depth > threshold + 4 → High
- effective_depth > threshold + 2 → Medium
- effective_depth > threshold → Low

**Implementation:** `detect()` reads the file extension, selects the language threshold. The adaptive calibration (`ctx.threshold(MetricKind::NestingDepth, ...)`) still applies on top — if the codebase naturally nests at 7 levels, the adaptive threshold rises to match.

### 3.2 LongMethodsDetector — Simplify and Raise

**Current:** 50-line threshold with complex match-arm discount, handler 2x multiplier, orchestrator 1.5x multiplier. The discounting logic is more complex than the detection.

**New approach:**

1. **Raise base threshold to 80 lines** — this alone eliminates most low-severity noise
2. **Remove match arm discount entirely** — a function is long regardless of why it's long
3. **Remove handler multiplier** — handlers should be short; giving them 2x is backwards. (Note: DeepNesting gives handlers a higher nesting threshold because nesting is structural — request parsing naturally nests. Length is different — a 160-line handler should be split regardless of its role.)
4. **Keep orchestrator severity reduction** (not threshold multiplier) — orchestrators at 100 lines get Medium instead of High, but they're still flagged
5. **Add language-specific thresholds:**
   - Python: 60 lines (significant whitespace encourages shorter functions)
   - Rust: 80 lines (match expressions are verbose but readable)
   - Go: 80 lines (error handling adds lines)
   - Java/C#: 100 lines (boilerplate-heavy languages)
   - C/C++: 100 lines (manual resource management)
   - Default: 80 lines

**Severity:**
- length > threshold × 3 → High
- length > threshold × 2 → Medium
- length > threshold → Low

### 3.3 ModuleCohesionDetector — Wire Louvain or Simplify

**Current:** Naive internal/external call ratio with 0.3 threshold. Has dead Louvain community detection code.

**Two options (pick during implementation):**

**Option A — Wire Louvain:** Use the community assignments from `GraphPrimitives` (already computed during freeze). Files within the same Louvain community should have high internal cohesion; files that span communities are the real smell. Replace the naive ratio with: `cohesion = calls_within_same_community / total_calls`. Threshold: 0.4.

**Option B — Simplify to pass-through detection:** Remove the ratio-based approach entirely. Only flag files where **zero** internal calls exist AND the file has 5+ external calls — these are pure pass-through/coordination files that might belong in a different module. This produces far fewer findings but each one is actionable.

**Recommended: Option B for now, Option A as a follow-up.** The naive ratio is producing 5,683 medium-severity findings. Switching to pass-through detection produces maybe 50-100 findings. Wire Louvain in a separate task when the community detection infrastructure is validated.

**Minimum module size:** Only flag pass-through files in modules (directories) with 5+ files. A file with 0 internal calls in a 2-file module is normal — there's only one other file to call.

**Severity:**
- Pure pass-through (0 internal, 10+ external, module has 5+ files) → Medium
- Pure pass-through (0 internal, 5-9 external, module has 5+ files) → Low

---

## Expected Impact

Based on the 56-repo dataset, estimated finding counts after all changes:

| Detector | Before | After (Est.) | Reduction | Method |
|---|---|---|---|---|
| LazyClass → RedundantClass | 8,418 | ~200 | 97% | Only overlapping/wrapper classes |
| DataClumps | 8,350 | ~800-1,500 | 80-90% | Higher thresholds + forwarding check (type-diversity deferred) |
| ModuleCohesion | 5,683 | ~100 | 98% | Pass-through detection only |
| CoreUtility | 5,528 | 0 | 100% | Removed from findings |
| AIComplexity → ComplexityOutlier | 4,773 | ~300 | 94% | Compound signal requirement |
| ShotgunSurgery → ChangeCoupling | 3,939 | ~150 | 96% | Co-change × fan-in × churn |
| LongMethods | 3,176 | ~500 | 84% | 80-line threshold, simplified |
| MagicNumbers | 3,172 | ~200 | 94% | Conditional/arithmetic context only |
| DeepNesting | 2,957 | ~400 | 86% | Language-aware 5-6 level thresholds |
| **Top 9 total** | **46,000** | **~2,350** | **95%** | |

Remaining 97 detectors produce ~39,000 findings. With the top 9 fixed, total per-repo average drops from ~1,526 to roughly **740**. Further reduction requires similar analysis of the next tier of detectors (TodoScanner, AIMissingTests, DuplicateCode, etc.), but this first pass addresses the majority of the noise.

The 20-80 target will require a second pass and likely a smart ranking layer on top, but this spec addresses the root causes rather than masking symptoms.

---

## Implementation Order

These changes are independent and can be parallelized:

1. **DeepNesting + LongMethods** (calibration) — simplest, highest confidence, immediate impact
2. **CoreUtility** (removal) — zero risk, immediate noise reduction
3. **MagicNumbers** (context filtering) — moderate complexity, high impact
4. **DataClumps** (threshold + refactorability) — moderate complexity
5. **ModuleCohesion** (simplify to pass-through) — moderate complexity
6. **LazyClass → RedundantClass** (rearchitect) — needs duplicate/wrapper detection logic
7. **AIComplexitySpike → ComplexityOutlier** (rearchitect) — needs git blame integration
8. **ShotgunSurgery → ChangeCoupling** (rearchitect) — needs CoChangeMatrix integration

Tasks 1-5 are threshold/filter changes. Tasks 6-8 are algorithmic rewrites.

---

## Testing Strategy

For each detector change:

1. **Regression test against the 56-repo dataset**: Run the modified detector against cached JSON from `seed-results/json/`. Compare finding counts before/after. Document the delta.
2. **False negative spot-check**: For the top 10 repos, manually review 5 findings that were removed. Are any of them actually worth keeping?
3. **True positive verification**: For the remaining findings, manually review 10. Are they actionable? Would a developer open a PR?
4. **Inline unit tests**: Each detector's existing `#[cfg(test)]` module is updated with new test cases matching the changed behavior.

---

## Backward Compatibility

Three detectors are renamed:
- `ShotgunSurgeryDetector` → `ChangeCouplingDetector`
- `LazyClassDetector` → `RedundantClassDetector`
- `AIComplexitySpikeDetector` → `ComplexityOutlierDetector`

**Config:** Old detector names are accepted as aliases in `repotoire.toml` `[detectors.{name}]` sections. A deprecation warning is printed: "Detector '{old}' has been renamed to '{new}'. Please update your config."

**Inline suppressions:** `// repotoire:ignore[ShotgunSurgeryDetector]` continues to work via an alias map in the suppression parser.

**SARIF:** Rule IDs change. Consumers keyed on old rule IDs will not match. This is a breaking change — document in CHANGELOG.

**Findings cache:** Renamed detectors invalidate the cache. First run after upgrade is a full analysis. This is expected and documented.

---

## Risks

| Risk | Mitigation |
|---|---|
| Over-correction: real issues now hidden | False negative spot-checks in testing. Gradual rollout — ship calibration changes first, rearchitecture second. |
| ChangeCoupling requires git history plumbing | New `git_churn` and `co_change_summary` fields in AnalysisContext (see Prerequisite section). Fallback: skip detector if git data unavailable. |
| RedundantClass needs cross-class comparison | Graph-only check (name+arity overlap, call-graph wrapper detection). Capped to same-module comparison. |
| ComplexityOutlier needs file churn data | Uses file-level `git_churn` from AnalysisContext, not per-function blame. Fallback: use only naming + structural signals. |
| Language-aware thresholds need file extension | Already available via `language_for_extension()`. Unknown extensions use the default threshold. |
| ChangeCoupling formula over-fires | Formula normalized to [0, 1] range. Thresholds calibrated against seed data before release. |
| DataClumps reduction less than estimated | Type-diversity check deferred. Threshold raise + forwarding check alone should achieve 80% reduction. |
| Cache invalidation on upgrade | Documented in CHANGELOG. First run is slower, subsequent runs use incremental cache. |
