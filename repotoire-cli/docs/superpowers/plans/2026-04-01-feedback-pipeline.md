# Feedback Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire FP/TP labels from `training_data.jsonl` into the analyze pipeline (local suppression) and enrich the PostHog `detector_feedback` event with actionable fields.

**Architecture:** `FeedbackCollector::load_label_map()` reads JSONL → `apply_user_labels()` in postprocess removes FP findings and pins TP findings → telemetry enrichment fills missing fields on the existing `DetectorFeedback` struct.

**Tech Stack:** Rust, serde, PostHog telemetry (existing), `FeedbackCollector` (existing)

**Spec:** `docs/superpowers/specs/2026-04-01-feedback-pipeline-design.md`

---

## File Structure

### Modified Files
| File | Changes |
|------|---------|
| `src/classifier/feedback.rs` | Add `load_label_map()` method to `FeedbackCollector` |
| `src/cli/analyze/postprocess.rs` | Add `apply_user_labels()` function, call after Step 0.6 |
| `src/telemetry/events.rs` | Add 3 new fields to `DetectorFeedback` struct |
| `src/cli/mod.rs` | Fill enriched fields in `DetectorFeedback` construction |

---

### Task 1: FeedbackCollector — add `load_label_map()` convenience method

**Files:** `src/classifier/feedback.rs`

- [ ] **Step 1: Add `load_label_map` method**

After the existing `load_all()` method (line ~137), add:

```rust
/// Build a label map: finding_id → is_true_positive.
/// Last entry wins (supports re-labeling). Unparseable lines are
/// silently skipped (matching `load_all()` behavior).
pub fn load_label_map(&self) -> HashMap<String, bool> {
    use std::collections::HashMap;

    let entries = match self.load_all() {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!("Failed to load feedback labels: {}", e);
            return HashMap::new();
        }
    };

    let mut map = HashMap::new();
    for entry in entries {
        map.insert(entry.finding_id, entry.is_true_positive);
    }
    map
}
```

Add `use std::collections::HashMap;` at the top of the file if not already present.

- [ ] **Step 2: Add test**

```rust
#[test]
fn test_load_label_map_last_writer_wins() {
    let dir = TempDir::new().expect("create temp dir");
    let path = dir.path().join("test_labels.jsonl");
    let collector = FeedbackCollector::with_path(&path);

    let finding = Finding {
        id: "abc-123".into(),
        detector: "TestDetector".into(),
        severity: crate::models::Severity::High,
        title: "Test".into(),
        ..Default::default()
    };

    // Label as TP first, then re-label as FP
    collector.record(&finding, true, None).unwrap();
    collector.record(&finding, false, Some("Actually not a bug".into())).unwrap();

    let map = collector.load_label_map();
    assert_eq!(map.len(), 1);
    assert_eq!(map.get("abc-123"), Some(&false), "Last entry (FP) should win");
}

#[test]
fn test_load_label_map_empty_file() {
    let dir = TempDir::new().expect("create temp dir");
    let path = dir.path().join("nonexistent.jsonl");
    let collector = FeedbackCollector::with_path(&path);

    let map = collector.load_label_map();
    assert!(map.is_empty());
}
```

- [ ] **Step 3: Verify**

Run: `cargo test feedback -- --nocapture`

- [ ] **Step 4: Commit**

```bash
git add src/classifier/feedback.rs
git commit -m "feat(feedback): add load_label_map() for finding_id → is_tp lookup"
```

---

### Task 2: Local suppression — `apply_user_labels()` in postprocess

**Files:** `src/cli/analyze/postprocess.rs`

- [ ] **Step 1: Add `apply_labels_to_findings` (testable inner function) and `apply_user_labels` (thin wrapper)**

Add these functions before `postprocess_findings`:

```rust
use std::collections::HashMap;

/// Core label-application logic. Separated from I/O for testability.
/// - FP-labeled findings are removed (or kept with low confidence if show_all).
/// - TP-labeled findings get confidence 0.95 + deterministic = true.
fn apply_labels_to_findings(
    findings: &mut Vec<Finding>,
    labels: &HashMap<String, bool>,
    show_all: bool,
) {
    if labels.is_empty() {
        return;
    }

    let mut fp_findings: Vec<Finding> = Vec::new();
    let mut applied = 0u32;

    findings.retain_mut(|f| {
        match labels.get(&f.id) {
            Some(false) => {
                // FP label: remove from findings
                applied += 1;
                if show_all {
                    f.confidence = Some(0.05);
                    f.threshold_metadata
                        .insert("user_label".to_string(), "false_positive".to_string());
                    fp_findings.push(f.clone());
                }
                false // remove from main vec
            }
            Some(true) => {
                // TP label: pin with high confidence
                applied += 1;
                f.confidence = Some(0.95);
                f.deterministic = true;
                f.threshold_metadata
                    .insert("user_label".to_string(), "true_positive".to_string());
                true // keep
            }
            None => true, // no label
        }
    });

    // Re-insert FP findings for --show-all visibility
    if show_all {
        findings.extend(fp_findings);
    }

    if applied > 0 {
        tracing::info!("Applied {} user feedback labels ({} in training data)", applied, labels.len());
    }
}

/// Load user FP/TP labels from training_data.jsonl and apply them.
/// Called after Step 0.6 (enrichment), before Step 0.7 (min-confidence filter).
fn apply_user_labels(findings: &mut Vec<Finding>, show_all: bool) {
    let labels = crate::classifier::FeedbackCollector::default().load_label_map();
    apply_labels_to_findings(findings, &labels, show_all);
}
```

- [ ] **Step 2: Wire into postprocess pipeline**

In `postprocess_findings`, add the call between Step 0.6 and Step 0.7. Find:

```rust
    crate::detectors::confidence_enrichment::enrich_all(findings);

    // Step 0.7: Confidence threshold filter (--min-confidence).
```

Insert between them:

```rust
    // Step 0.65: Apply user FP/TP labels from feedback command.
    // FP-labeled findings are removed; TP-labeled findings are pinned.
    // Runs after enrichment so user labels override enrichment adjustments.
    apply_user_labels(findings, show_all);
```

- [ ] **Step 3: Add tests**

Add at the bottom of the file (inside or after the existing test module):

```rust
#[cfg(test)]
mod label_tests {
    use super::*;
    use crate::models::{Finding, Severity};
    use std::collections::HashMap;

    fn make_finding(id: &str, detector: &str) -> Finding {
        Finding {
            id: id.into(),
            detector: detector.into(),
            severity: Severity::Medium,
            title: format!("Finding {}", id),
            ..Default::default()
        }
    }

    #[test]
    fn test_fp_label_removes_finding() {
        let mut findings = vec![make_finding("aaa", "Det1"), make_finding("bbb", "Det2")];
        let labels = HashMap::from([("aaa".to_string(), false)]);

        apply_labels_to_findings(&mut findings, &labels, false);

        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].id, "bbb");
    }

    #[test]
    fn test_fp_label_show_all_reinserts_with_low_confidence() {
        let mut findings = vec![make_finding("aaa", "Det1"), make_finding("bbb", "Det2")];
        let labels = HashMap::from([("aaa".to_string(), false)]);

        apply_labels_to_findings(&mut findings, &labels, true);

        assert_eq!(findings.len(), 2, "show_all should keep FP finding");
        let fp = findings.iter().find(|f| f.id == "aaa").expect("FP finding should exist");
        assert_eq!(fp.confidence, Some(0.05));
        assert_eq!(fp.threshold_metadata.get("user_label").map(|s| s.as_str()), Some("false_positive"));
    }

    #[test]
    fn test_tp_label_pins_finding() {
        let mut findings = vec![make_finding("aaa", "Det1")];
        let labels = HashMap::from([("aaa".to_string(), true)]);

        apply_labels_to_findings(&mut findings, &labels, false);

        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].confidence, Some(0.95));
        assert!(findings[0].deterministic);
        assert_eq!(findings[0].threshold_metadata.get("user_label").map(|s| s.as_str()), Some("true_positive"));
    }

    #[test]
    fn test_unlabeled_findings_unchanged() {
        let mut findings = vec![make_finding("aaa", "Det1")];
        let labels = HashMap::from([("zzz".to_string(), false)]); // no match

        apply_labels_to_findings(&mut findings, &labels, false);

        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].id, "aaa");
        assert!(findings[0].confidence.is_none()); // unchanged
    }

    #[test]
    fn test_empty_labels_is_noop() {
        let mut findings = vec![make_finding("aaa", "Det1")];
        let labels = HashMap::new();

        apply_labels_to_findings(&mut findings, &labels, false);

        assert_eq!(findings.len(), 1);
    }
}
```

- [ ] **Step 4: Verify**

Run: `cargo test label_tests -- --nocapture` and `cargo test feedback -- --nocapture`

- [ ] **Step 5: Commit**

```bash
git add src/cli/analyze/postprocess.rs
git commit -m "feat(analyze): apply user FP/TP labels during postprocess

FP-labeled findings are removed from results. TP-labeled findings are
pinned with confidence 0.95 + deterministic=true. Reads labels from
training_data.jsonl via FeedbackCollector::load_label_map(). Runs after
confidence enrichment (Step 0.6), before min-confidence filter (Step 0.7)."
```

---

### Task 3: Telemetry enrichment — enrich `DetectorFeedback` event

**Files:** `src/telemetry/events.rs`, `src/cli/mod.rs`

- [ ] **Step 1: Add new fields to `DetectorFeedback` struct**

In `src/telemetry/events.rs`, replace the `DetectorFeedback` struct (lines 52-63) with:

```rust
#[derive(Debug, Clone, Serialize, Default)]
pub struct DetectorFeedback {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_id: Option<String>,
    pub detector: String,
    pub verdict: String,
    pub severity: String,
    pub language: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub framework: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file_extension: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub finding_title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    pub version: String,
}
```

- [ ] **Step 2: Add `ext_to_language` helper**

Add at the bottom of `src/telemetry/events.rs`, before the test module:

```rust
/// Map file extension to language name for telemetry.
/// Should stay in sync with parser extension registry in src/parsers/mod.rs.
pub fn ext_to_language(ext: &str) -> &'static str {
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
        "h" => "c_or_cpp",
        "cpp" | "cc" | "cxx" | "hpp" => "cpp",
        _ => "unknown",
    }
}
```

- [ ] **Step 3: Fill enriched fields in feedback command**

In `src/cli/mod.rs`, replace the `DetectorFeedback` construction (lines 811-823) with:

```rust
                    let file_ext = finding
                        .affected_files
                        .first()
                        .and_then(|p| p.extension())
                        .and_then(|e| e.to_str())
                        .unwrap_or("");
                    let event = crate::telemetry::events::DetectorFeedback {
                        repo_id: crate::telemetry::config::compute_repo_id(&cli.path),
                        detector: finding.detector.clone(),
                        verdict: if is_tp {
                            "true_positive".into()
                        } else {
                            "false_positive".into()
                        },
                        severity: finding.severity.to_string(),
                        language: crate::telemetry::events::ext_to_language(file_ext)
                            .to_string(),
                        file_extension: if file_ext.is_empty() {
                            None
                        } else {
                            Some(file_ext.to_string())
                        },
                        finding_title: Some(finding.title.clone()),
                        reason: reason.clone(),
                        version: env!("CARGO_PKG_VERSION").to_string(),
                        ..Default::default()
                    };
```

- [ ] **Step 4: Add tests**

In `src/telemetry/events.rs` test module, add:

```rust
#[test]
fn test_ext_to_language() {
    assert_eq!(ext_to_language("ts"), "typescript");
    assert_eq!(ext_to_language("js"), "javascript");
    assert_eq!(ext_to_language("mjs"), "javascript");
    assert_eq!(ext_to_language("py"), "python");
    assert_eq!(ext_to_language("rs"), "rust");
    assert_eq!(ext_to_language("h"), "c_or_cpp");
    assert_eq!(ext_to_language("xyz"), "unknown");
}

#[test]
fn test_detector_feedback_enriched_serializes() {
    let event = DetectorFeedback {
        detector: "GlobalVariablesDetector".to_string(),
        verdict: "false_positive".to_string(),
        severity: "low".to_string(),
        language: "typescript".to_string(),
        file_extension: Some("ts".to_string()),
        finding_title: Some("Global mutable variable: currentAuth".to_string()),
        reason: Some("Module-scoped let in TS".to_string()),
        version: "0.6.0".to_string(),
        ..Default::default()
    };

    let json = serde_json::to_value(&event).expect("should serialize");
    assert_eq!(json["language"], "typescript");
    assert_eq!(json["file_extension"], "ts");
    assert_eq!(json["finding_title"], "Global mutable variable: currentAuth");
    assert_eq!(json["reason"], "Module-scoped let in TS");
    // Optional None fields should not appear
    assert!(json.get("repo_id").is_none() || json["repo_id"].is_null());
    assert!(json.get("framework").is_none() || json["framework"].is_null());
}

#[test]
fn test_detector_feedback_reason_omitted_when_none() {
    let event = DetectorFeedback {
        detector: "Test".to_string(),
        verdict: "true_positive".to_string(),
        severity: "high".to_string(),
        language: "rust".to_string(),
        version: "0.6.0".to_string(),
        ..Default::default()
    };

    let json = serde_json::to_value(&event).expect("should serialize");
    // reason is None → should not appear in JSON (skip_serializing_if)
    assert!(json.get("reason").is_none() || json["reason"].is_null());
}
```

- [ ] **Step 5: Verify**

Run:
```bash
cargo test telemetry -- --nocapture
cargo test feedback -- --nocapture
cargo clippy --all-features -- -D warnings
cargo fmt --all -- --check
```

- [ ] **Step 6: Commit**

```bash
git add src/telemetry/events.rs src/cli/mod.rs
git commit -m "feat(telemetry): enrich detector_feedback with language, title, reason

Fill language from file extension, add file_extension, finding_title,
and reason fields to PostHog detector_feedback event. New fields use
Option + skip_serializing_if for backward compatibility."
```

---

### Task 4: Full verification

- [ ] **Step 1: Run full test suite**

```bash
cargo test
cargo clippy --all-features -- -D warnings
cargo fmt --all -- --check
```

- [ ] **Step 2: Manual end-to-end test**

```bash
# Build and install
cargo install --path .

# Analyze a repo
repotoire clean ~/personal/web/humanitarian-platform
repotoire analyze ~/personal/web/humanitarian-platform

# Note a finding index, label it as FP
repotoire feedback 1 --fp --reason "Testing feedback pipeline"

# Re-analyze — labeled finding should be gone
repotoire clean ~/personal/web/humanitarian-platform
repotoire analyze ~/personal/web/humanitarian-platform
# Verify finding #1 is no longer present

# Check with --show-all
repotoire analyze ~/personal/web/humanitarian-platform --show-all
# Verify finding appears with low confidence and user_label metadata
```

**Note on incremental cache interaction:** Labels are applied during postprocess (after cache load), so they work correctly on both cold and incremental runs. However, if a user labels a finding as FP (which removes it from cached output), then later re-labels it as TP, the finding will reappear on the next cold run but may not reappear on an incremental run if the file hasn't changed (the incremental cache won't re-detect it). Running `repotoire clean` resolves this. This is acceptable — re-labeling is rare and `clean` is the standard recovery path.

- [ ] **Step 3: Commit any fixes**

If manual testing reveals issues, fix and commit.
