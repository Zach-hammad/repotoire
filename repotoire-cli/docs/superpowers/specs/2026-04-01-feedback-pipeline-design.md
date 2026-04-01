# Feedback Pipeline: Local Suppression + Telemetry Enrichment

## Context

`repotoire feedback <index> --fp` writes labels to `~/.local/share/repotoire/training_data.jsonl` but `repotoire analyze` never reads them back. The pipeline is write-only: FP-labeled findings reappear at full confidence on every run. The `train` command exists but requires 10+ examples and is a separate manual step.

Additionally, the `detector_feedback` PostHog event fires on every feedback command but is missing key fields needed to prioritize detector fixes:
- `language` is always empty string
- No `finding_title` (can't see WHICH finding was labeled)
- No `reason` (can't see WHY the user thinks it's an FP)
- No `file_extension` (can't distinguish `.ts` vs `.tsx` vs `.mjs` patterns)

## Goals

1. FP-labeled findings are suppressed on subsequent `repotoire analyze` runs
2. TP-labeled findings are pinned (always appear regardless of classifier filtering)
3. PostHog `detector_feedback` event includes enough context to prioritize detector fixes
4. Labels naturally expire when code changes (finding_id changes → label stops matching)

## Non-Goals

- Auto-training the ML classifier during analyze
- Changing the `train` command
- Adding new CLI flags or commands
- Cross-repo label sharing

---

## Part 1: Local Suppression

### How It Works

During postprocess, after Step 0.6 (confidence enrichment):

1. Call `FeedbackCollector::load_all()` to read `training_data.jsonl` (reuses existing I/O logic — no duplication). Skip unparseable lines with `tracing::warn!` on the first bad line.
2. Build `HashMap<String, bool>` mapping `LabeledFinding.finding_id → LabeledFinding.is_true_positive`. Iterate in file order and use plain `insert` — **last entry wins** so re-labels (user changes FP→TP or vice versa) take effect correctly.
3. For each finding, match on `finding.id == labeled.finding_id`:
   - **FP label**: Remove from findings vec (retain filter). Store removed finding IDs in a set for `--show-all` recovery. This is a hard removal, not confidence-based — it works regardless of whether `--min-confidence` is set.
   - **TP label**: set `confidence = 0.95`, set `deterministic = true`, add `threshold_metadata["user_label"] = "true_positive"`
   - **No match**: unchanged
4. If `--show-all` is active, re-insert FP-labeled findings with `confidence = 0.05` and `threshold_metadata["user_label"] = "false_positive"` so they appear in output but are clearly marked.

### Why This Works

- **FP suppression**: Hard removal from findings vec — works regardless of `--min-confidence` setting. With `--show-all`, FP-labeled findings reappear with confidence 0.05 and a `user_label` metadata marker.
- **TP pinning**: `deterministic = true` bypasses the classifier filter (Step 7). Confidence 0.95 survives enrichment and threshold checks. Note: TP pinning protects against statistical filtering (Steps 0.7 and 7) only. Config-based removal (detector disabled, path exclusion, inline `repotoire:ignore`) still takes effect — those represent explicit user choices that override labels.
- **Natural expiry**: `finding_id` is a deterministic FNV-1a hash of detector + file path + line number (title is NOT included). File paths are relative to repo root (verified: `affected_files` from detectors and `last_findings.json` both use repo-relative paths). If the code changes (line moves, file renamed), the ID changes and the stale label stops matching. **Known limitation**: if a detector changes its title/message for the same location (e.g., after a detector update), the old label persists. This is acceptable because version-bumped binaries typically change code structures enough to invalidate stale labels naturally.
- **No new files**: Reuses `FeedbackCollector::load_all()` to read the existing `training_data.jsonl` — no duplicated I/O logic.

### Integration Point

New function `apply_user_labels(findings: &mut [Finding])` in `src/cli/analyze/postprocess.rs`, called **after Step 0.6** (confidence enrichment) and **before Step 0.7** (min-confidence filter). This ordering ensures user labels override any enrichment-based confidence adjustments — if a user says it's an FP, bundled-code penalties or multi-detector bonuses don't matter. If a user says it's a TP, enrichment can't pull confidence below the pinned 0.95.

### Data Format (existing, unchanged)

Each line in `training_data.jsonl`:
```json
{
  "finding_id": "abc123",
  "detector": "GlobalVariablesDetector",
  "severity": "low",
  "title": "Global mutable variable: currentAuth",
  "description": "...",
  "file_path": "lib/auth.ts",
  "line_start": 18,
  "is_true_positive": false,
  "reason": "Module-scoped let in TS, not a global",
  "timestamp": "2026-04-01T12:00:00Z"
}
```

Only `finding_id` and `is_true_positive` are used for matching. The rest is context for training/telemetry.

---

## Part 2: Telemetry Enrichment

### Current Event (`detector_feedback`)

```json
{
  "event": "detector_feedback",
  "properties": {
    "repo_id": "hash",
    "detector": "GlobalVariablesDetector",
    "verdict": "false_positive",
    "severity": "low",
    "language": "",
    "version": "0.6.0"
  }
}
```

### Enriched Event

```json
{
  "event": "detector_feedback",
  "properties": {
    "repo_id": "hash",
    "detector": "GlobalVariablesDetector",
    "verdict": "false_positive",
    "severity": "low",
    "language": "typescript",
    "file_extension": "ts",
    "finding_title": "Global mutable variable: currentAuth",
    "reason": "Module-scoped let in TS, not a global",
    "version": "0.6.0"
  }
}
```

### New Fields

| Field | Source | Why |
|-------|--------|-----|
| `language` | Map file extension to language name | Know which language parsers produce FPs |
| `file_extension` | `finding.affected_files[0]` extension | More precise than language (`.ts` vs `.tsx` vs `.mts`) |
| `finding_title` | `finding.title` | Know WHICH specific pattern is an FP |
| `reason` | `--reason "..."` flag value | Know WHY users think it's an FP — patterns for detector fixes |

### Language Mapping

```rust
fn ext_to_language(ext: &str) -> &'static str {
    match ext {
        "py" => "python",
        "js" | "mjs" | "cjs" => "javascript",
        "ts" | "mts" | "cts" => "typescript",
        "jsx" => "jsx",
        "tsx" => "tsx",
        "rs" => "rust",
        "go" => "go",
        "java" => "java",
        "cs" => "csharp",
        "c" => "c",
        "h" => "c_or_cpp", // ambiguous: parser uses content heuristic, telemetry can't
        "cpp" | "cc" | "cxx" | "hpp" => "cpp",
        _ => "unknown",
    }
}
```

### Integration Point

In `src/cli/mod.rs` where `DetectorFeedback` is constructed (line ~811), fill in the missing fields from the `finding` struct and the `reason` argument.

### Implementation Notes

- New fields on `DetectorFeedback` should use `Option<String>` with `#[serde(skip_serializing_if = "Option::is_none")]` — matching the pattern used by `repo_id` and `framework`. The `reason` field is optional (user may not pass `--reason`).
- The `ext_to_language` mapping should ideally be shared with or derived from the parser's extension registry in `src/parsers/mod.rs` to avoid drift when new extensions are added.

### Privacy

- `finding_title` may include detector-inferred identifiers (variable names, function names, class names) but no file contents, literal values, or string literals
- `reason` is user-provided and opt-in (`--reason` is optional)
- `file_extension` reveals language choice, not file content
- No file paths or code snippets are sent

---

## Verification

### Local Suppression

```bash
# 1. Analyze, note finding #N
repotoire analyze .

# 2. Label as FP
repotoire feedback N --fp --reason "module-scoped variable"

# 3. Re-analyze — finding should be gone
repotoire clean .  # clear cache
repotoire analyze .
# Finding #N should not appear

# 4. Re-analyze with --show-all — finding visible with low confidence
repotoire analyze . --show-all
# Finding should appear with confidence ~0.05 and user_label metadata
```

### Telemetry

Verify enriched event by inspecting the PostHog capture payload in debug mode or checking the PostHog dashboard for `detector_feedback` events with non-empty `language`, `finding_title`, and `reason` fields.

---

## Files Changed

| File | Changes |
|------|---------|
| `src/cli/analyze/postprocess.rs` | Add `apply_user_labels()` function, call after Step 0.6, before Step 0.7. Hard-remove FP findings, pin TP findings. |
| `src/cli/mod.rs` | Enrich `DetectorFeedback` event with language, file_extension, finding_title, reason |
| `src/telemetry/events.rs` | Add `file_extension`, `finding_title`, `reason` fields (Option + skip_serializing_if) to `DetectorFeedback` |
| `src/classifier/feedback.rs` | Add `load_label_map() -> HashMap<String, bool>` convenience method (wraps `load_all()`) |
