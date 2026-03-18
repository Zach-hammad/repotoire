# Detector Language Audit & GBDT Bypass Design

**Date:** 2026-03-18
**Status:** Draft
**Scope:** All 107 detectors — audit language support correctness, fix gaps, add per-detector GBDT bypass

## Problem Statement

QA testing revealed that many detectors declare language support they don't actually provide. The issues fall into 4 root causes:

1. **Extension-loop mismatches** — `file_extensions()` lists languages that `detect()` never scans
2. **Content access mismatches** — detectors using `masked_content()` when they need raw (or vice versa), causing patterns to be invisible
3. **GBDT over-filtering** — the ML postprocessor drops valid findings for certain detectors/languages where contextual features are weak
4. **Implementation gaps** — detectors with same-line restrictions, missing content flags, or incomplete language patterns

## Design

### Phase 1: Programmatic Audit

Run an automated audit of all 107 detectors extracting 6 dimensions per detector:

| Dimension | How to Extract | Mismatch Indicates |
|-----------|---------------|-------------------|
| **Language gating** | Compare `file_extensions()` return vs all `files_with_extension(s)` calls in `detect()` | Languages declared but never scanned |
| **Content access** | Check `masked_content()` vs `content()` usage | Wrong access pattern for detector's purpose |
| **GBDT bypass eligibility** | Check `is_deterministic()` + detector category + pattern type | High-precision detectors being filtered unnecessarily |
| **Content flag alignment** | Compare `content_requirements()` vs actual pre-filter keywords | Content flags that miss language-specific patterns |
| **Same-line restrictions** | Find user-input checks that require indicators on the same line as the vulnerability pattern | Cross-line vulnerabilities missed |
| **Unit test coverage** | Count `#[test]` functions, check which languages are tested | Missing test coverage for claimed languages |

**Output:** Append audit results to `docs/QA_FINDINGS.md` as a new "Detector Audit" section with a per-detector table. This table directly drives the Phase 3 work list — each row with a non-"Clean" status becomes a work unit.

**Category labels per detector:**
- **Clean** — all 6 dimensions aligned
- **Extension fix** — needs languages added/removed from scan loop
- **Content fix** — needs raw↔masked swap or dual access
- **GBDT bypass** — should opt out of ML filtering
- **Logic fix** — needs cross-line context, content flag additions, or pattern improvements
- **Test gap** — needs tests for claimed languages

### Phase 2: Add `bypass_postprocessor()` Trait Method

Add to the `Detector` trait in `repotoire-cli/src/detectors/base.rs`:

```rust
/// Whether this detector's findings should bypass GBDT postprocessor filtering.
///
/// Detectors with high-precision, pattern-based detection (e.g., regex-matched
/// security vulnerabilities) should return `true` since the GBDT classifier
/// adds no value and can incorrectly filter valid findings.
///
/// Default: `false` (findings go through normal GBDT filtering)
fn bypass_postprocessor(&self) -> bool {
    false
}
```

**Propagation mechanism:** Do NOT add a field to the `Finding` struct — findings are data and shouldn't carry detector metadata. Instead:

1. In `runner.rs`, after collecting all findings, build a `HashSet<String>` of detector names where `bypass_postprocessor()` returns `true`
2. Pass this set to the postprocessor alongside findings
3. In `postprocess.rs`, check `bypass_set.contains(&f.detector)` alongside `f.deterministic`

```rust
// postprocess.rs — updated filter
if f.deterministic || bypass_set.contains(&f.detector) {
    return true; // skip GBDT filtering
}
```

This keeps `Finding` clean and avoids touching serialization/deserialization.

### Phase 3: Fix Detectors by Category

#### 3a: Extension-loop fixes
Same pattern as the 6 fixes already shipped — add missing extensions to `files_with_extensions()` calls. Trivial, ~1 line each. The audit table from Phase 1 provides the exact list.

#### 3b: Content access fixes

**CorsMisconfigDetector** — uses `masked_content()` for pattern matching, but tree-sitter masks the `'*'` inside string literals like `cors({origin: '*'})` to spaces, breaking the CORS wildcard regex.

**Fix:** Run the CORS wildcard regex against raw content (where `'*'` is preserved), then validate each match against masked content to confirm it's not inside a comment or documentation string. Pattern:

```rust
// 1. Match on raw content (sees actual string values)
let raw_lines: Vec<&str> = raw.lines().collect();
let masked_lines: Vec<&str> = masked.lines().collect();
for (i, line) in raw_lines.iter().enumerate() {
    if CORS_PATTERN.is_match(line) {
        // 2. Verify the match isn't inside a comment (masked line would be all spaces there)
        let masked_line = masked_lines.get(i).unwrap_or(&"");
        if !masked_line.trim().is_empty() {
            // Line has actual code — this is a real finding
        }
    }
}
```

**PrototypePollutionDetector** — already uses raw content (masking is NOT the issue). **Scoped out of this spec.** Needs a separate investigation into why the regex doesn't match TS fixtures — likely the `__proto__` assignment pattern in the fixture doesn't match the regex's expected context (the regex requires user-input indicators on the same line). File a follow-up issue.

#### 3c: GBDT bypass opt-ins

Detectors eligible for bypass (high-precision regex-based, low FP rate):
- Security detectors with specific language patterns (InsecureCrypto, CommandInjection, SQLInjection, XSS, SSRF, XXE, etc.)
- The audit will identify the full list

Detectors that should NOT bypass (benefit from filtering):
- MagicNumbers (high inherent FP rate)
- DeadCode (context-dependent)
- AIBoilerplate (heuristic-based)
- Any detector the audit marks as "Clean" w.r.t. GBDT

**Rollback safety:** If GBDT bypass increases false positives, users can raise the confidence floor via `--min-confidence` flag (applied at step 0.7 in the postprocess pipeline, before GBDT). Per-detector bypass can also be reverted individually by flipping the trait method back to `false`.

#### 3d: InsecureDeserialize overhaul

1. Add `ObjectInputStream`, `readObject`, `XMLDecoder` to `HAS_SERIALIZE` content flag keywords in `detector_context.rs:199-207`
2. Expand user-input check from same-line to ±10 line window — scan surrounding lines for user-input indicators instead of requiring them on the exact deserialization line
3. Add Java-specific deserialization patterns (ObjectInputStream, XMLDecoder, readObject)
4. Add unit tests for Java deserialization (currently Python-only)

#### 3e: Cross-line context improvements

For detectors with same-line user-input requirements, expand to a configurable context window (default ±10 lines). Extract a shared helper:

```rust
/// Check if any line within ±window of `line_num` contains user-input indicators.
fn has_nearby_user_input(lines: &[&str], line_num: usize, window: usize) -> bool {
    let start = line_num.saturating_sub(window);
    let end = (line_num + window).min(lines.len());
    lines[start..end].iter().any(|l| {
        l.contains("req.") || l.contains("request")
            || l.contains("body") || l.contains("input")
            || l.contains("params") || l.contains("getParameter")
            || l.contains("FormValue") || l.contains("getInputStream")
    })
}
```

This affects:
- InsecureDeserializeDetector
- CommandInjectionDetector (Go patterns currently require `r.FormValue` on same line)
- Any other detectors the audit identifies

### Phase 4: Update Integration Tests

For each fixed detector, update the corresponding `tests/lang_*.rs`:
- Flip negative assertions (`assert!(!detectors.contains(...))`) to positive assertions
- Add new test cases where detectors now fire
- Ensure no regressions in existing passing tests

### Phase 5: Re-run Self-Analysis

Run the dogfooding test to measure impact:
- Finding count change (expect increase from GBDT bypass)
- Score change (may decrease slightly with more findings — acceptable)
- False positive spot-check: manually review a sample of new findings to verify they're true positives
- Verify no panics or regressions

## Execution Strategy

**Phase 1** (audit) runs first — it produces the exact work list for Phases 3-4.

**Phase 2** (trait method + propagation) is independent and can run in parallel with Phase 1.

**Phase 3** (fixes) is parallelizable across detectors — each fix is independent.

**Phase 4** (tests) runs after Phase 3 fixes for the corresponding detectors.

**Phase 5** (validation) runs last.

## Files Affected

### Core changes
- `repotoire-cli/src/detectors/base.rs` — New `bypass_postprocessor()` trait method
- `repotoire-cli/src/cli/analyze/postprocess.rs` — Accept bypass set, check it in GBDT filter
- `repotoire-cli/src/detectors/runner.rs` — Build bypass set from detector trait methods, pass to postprocessor
- `repotoire-cli/src/detectors/detector_context.rs` — Content flag additions (HAS_SERIALIZE)
- `repotoire-cli/src/engine/stages/postprocess.rs` — Thread bypass set through pipeline

### Per-detector fixes (determined by audit)
- `repotoire-cli/src/detectors/*.rs` — Various fixes per audit results

### Shared helpers
- Cross-line user-input check helper (in a shared module or inline)

### Tests
- `repotoire-cli/tests/lang_*.rs` — Update assertions
- `repotoire-cli/tests/dogfood.rs` — Verify self-analysis still passes

### Documentation
- `docs/QA_FINDINGS.md` — Append full audit table

## Out of Scope

- **PrototypePollutionDetector TS gap** — needs separate investigation (not a masking issue)
- **Per-language GBDT bypass** — future enhancement if per-detector proves too coarse
- **GBDT model retraining** — would help long-term but is a separate effort
- **New detector development** — this spec only fixes existing detectors

## Success Criteria

1. Every detector's `file_extensions()` matches its actual scan loop
2. No detector uses wrong content access pattern for its purpose
3. High-precision security detectors bypass GBDT via `bypass_postprocessor()`
4. InsecureDeserialize fires for Python pickle/yaml AND Java ObjectInputStream
5. CorsMisconfig fires for `cors({origin: '*'})`
6. All 98+ integration tests pass (with updated assertions)
7. Self-analysis completes without regression (score within ±5 points)
8. `bypass_postprocessor` flag is NOT on the `Finding` struct
