# Smart Diff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add hunk-level finding attribution to `repotoire diff` — show only findings the developer introduced, not pre-existing noise.

**Architecture:** Parse `git diff -U0` to extract changed line ranges per file. Tag each new finding as `InChangedHunk`, `InChangedFile`, or `InUnchangedFile`. Default to showing only `InChangedHunk` findings. Add `--all` flag for backward compat.

**Tech Stack:** Existing Rust CLI (clap 4), git CLI for diff parsing, regex for hunk header parsing.

**Spec:** `docs/superpowers/specs/2026-03-31-smart-diff-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/cli/diff_hunks.rs` | Git diff parser, DiffHunks struct, Attribution enum |

### Modified Files
| File | Changes |
|------|---------|
| `src/cli/diff.rs` | Add attribution to DiffResult, filter by attribution, `--all`/`--changed` flags, update formatters |
| `src/cli/mod.rs` | Add `--all` and `--changed` args to Diff command |

---

### Task 1: DiffHunks parser + Attribution enum

**Files:**
- Create: `src/cli/diff_hunks.rs`
- Modify: `src/cli/mod.rs` (add `pub mod diff_hunks;` if needed, or keep it private in diff.rs)

- [ ] **Step 1: Create diff_hunks.rs with structs and tests first**

```rust
//! Parse git diff -U0 output to extract changed line ranges per file.
//!
//! Used by the diff command to attribute findings to changed hunks.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

/// How a finding relates to the code changes in a diff.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Attribution {
    /// Finding's line falls within a changed hunk (±3 lines margin).
    /// This is the PR author's responsibility.
    InChangedHunk,
    /// Finding is in a changed file but NOT in a changed hunk.
    /// Pre-existing issue.
    InChangedFile,
    /// Finding is in a file not touched by the diff.
    InUnchangedFile,
}

/// Changed line ranges extracted from a git diff.
pub struct DiffHunks {
    /// file_path → Vec of (start_line, end_line) ranges (1-based, inclusive).
    hunks: HashMap<PathBuf, Vec<(u32, u32)>>,
    /// All files that appear in the diff.
    changed_files: HashSet<PathBuf>,
}

/// Line tolerance for hunk attribution (matches fuzzy matching in findings_match).
const HUNK_MARGIN: u32 = 3;

impl DiffHunks {
    /// Parse `git diff -U0 <base_ref>..HEAD` output.
    pub fn from_git_diff(repo_path: &Path, base_ref: &str) -> anyhow::Result<Self> {
        let output = std::process::Command::new("git")
            .args(["diff", "-U0", &format!("{base_ref}..HEAD")])
            .current_dir(repo_path)
            .output()
            .map_err(|e| anyhow::anyhow!("Failed to run git diff: {e}"))?;

        if !output.status.success() {
            // If git diff fails (e.g., invalid ref), return empty hunks
            return Ok(Self {
                hunks: HashMap::new(),
                changed_files: HashSet::new(),
            });
        }

        let diff_text = String::from_utf8_lossy(&output.stdout);
        Ok(Self::parse_diff(&diff_text))
    }

    /// Parse raw git diff -U0 text into DiffHunks.
    pub fn parse_diff(diff_text: &str) -> Self {
        let mut hunks: HashMap<PathBuf, Vec<(u32, u32)>> = HashMap::new();
        let mut changed_files: HashSet<PathBuf> = HashSet::new();
        let mut current_file: Option<PathBuf> = None;

        for line in diff_text.lines() {
            // Track current file from +++ header
            if let Some(path) = line.strip_prefix("+++ b/") {
                let p = PathBuf::from(path);
                changed_files.insert(p.clone());
                current_file = Some(p);
            } else if line.starts_with("--- ") {
                // Also track files from --- header (for deleted files)
                if let Some(path) = line.strip_prefix("--- a/") {
                    changed_files.insert(PathBuf::from(path));
                }
            } else if line.starts_with("@@ ") {
                // Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
                if let Some(ref file) = current_file {
                    if let Some((start, count)) = parse_hunk_header(line) {
                        let end = if count == 0 {
                            start // deletion at this line, no new lines
                        } else {
                            start + count - 1
                        };
                        if count > 0 {
                            hunks.entry(file.clone()).or_default().push((start, end));
                        }
                    }
                }
            }
        }

        Self {
            hunks,
            changed_files,
        }
    }

    /// Attribute a finding based on its file and line.
    pub fn attribute(&self, file: &Path, line: Option<u32>) -> Attribution {
        if !self.changed_files.contains(file) {
            return Attribution::InUnchangedFile;
        }

        // File-level findings (no line number) → InChangedFile
        let line = match line {
            Some(l) => l,
            None => return Attribution::InChangedFile,
        };

        // Check if line falls within any hunk (±HUNK_MARGIN)
        if let Some(file_hunks) = self.hunks.get(file) {
            for &(start, end) in file_hunks {
                let expanded_start = start.saturating_sub(HUNK_MARGIN);
                let expanded_end = end.saturating_add(HUNK_MARGIN);
                if line >= expanded_start && line <= expanded_end {
                    return Attribution::InChangedHunk;
                }
            }
        }

        Attribution::InChangedFile
    }

    /// Number of changed files.
    pub fn changed_file_count(&self) -> usize {
        self.changed_files.len()
    }
}

/// Parse a hunk header line and extract (new_start, new_count).
/// Format: @@ -old_start,old_count +new_start,new_count @@
/// Count defaults to 1 if omitted (e.g., @@ -1 +1 @@).
fn parse_hunk_header(line: &str) -> Option<(u32, u32)> {
    // Find the + section
    let plus_idx = line.find('+')?;
    let after_plus = &line[plus_idx + 1..];

    // Find the end (space before @@)
    let end_idx = after_plus.find(" @@").unwrap_or(after_plus.len());
    let range_str = &after_plus[..end_idx];

    if let Some((start_str, count_str)) = range_str.split_once(',') {
        let start = start_str.parse::<u32>().ok()?;
        let count = count_str.parse::<u32>().ok()?;
        Some((start, count))
    } else {
        let start = range_str.parse::<u32>().ok()?;
        Some((start, 1)) // count defaults to 1
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_hunk_header_with_count() {
        assert_eq!(parse_hunk_header("@@ -10,5 +20,3 @@ fn foo()"), Some((20, 3)));
    }

    #[test]
    fn test_parse_hunk_header_without_count() {
        assert_eq!(parse_hunk_header("@@ -10 +20 @@"), Some((20, 1)));
    }

    #[test]
    fn test_parse_hunk_header_zero_count() {
        // Deletion: no new lines added
        assert_eq!(parse_hunk_header("@@ -10,3 +20,0 @@"), Some((20, 0)));
    }

    #[test]
    fn test_parse_diff_single_file() {
        let diff = "\
diff --git a/src/main.rs b/src/main.rs
--- a/src/main.rs
+++ b/src/main.rs
@@ -10,3 +10,5 @@ fn main() {
+    let x = 1;
+    let y = 2;
";
        let hunks = DiffHunks::parse_diff(diff);
        assert!(hunks.changed_files.contains(&PathBuf::from("src/main.rs")));
        let file_hunks = hunks.hunks.get(&PathBuf::from("src/main.rs")).unwrap();
        assert_eq!(file_hunks, &[(10, 14)]); // start=10, count=5, end=14
    }

    #[test]
    fn test_parse_diff_multiple_hunks() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -5,2 +5,3 @@ fn handler() {
+    new_line();
@@ -50,1 +51,4 @@ fn query() {
+    more();
+    code();
+    here();
";
        let hunks = DiffHunks::parse_diff(diff);
        let file_hunks = hunks.hunks.get(&PathBuf::from("src/api.rs")).unwrap();
        assert_eq!(file_hunks.len(), 2);
        assert_eq!(file_hunks[0], (5, 7));   // start=5, count=3
        assert_eq!(file_hunks[1], (51, 54)); // start=51, count=4
    }

    #[test]
    fn test_attribute_in_changed_hunk() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -10,2 +10,5 @@ fn handler() {
";
        let hunks = DiffHunks::parse_diff(diff);
        // Line 12 is within hunk (10-14)
        assert_eq!(hunks.attribute(Path::new("src/api.rs"), Some(12)), Attribution::InChangedHunk);
    }

    #[test]
    fn test_attribute_in_changed_hunk_with_margin() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -10,2 +10,5 @@ fn handler() {
";
        let hunks = DiffHunks::parse_diff(diff);
        // Line 17 is hunk_end(14) + 3 margin = within margin
        assert_eq!(hunks.attribute(Path::new("src/api.rs"), Some(17)), Attribution::InChangedHunk);
        // Line 18 is hunk_end(14) + 4 = outside margin
        assert_eq!(hunks.attribute(Path::new("src/api.rs"), Some(18)), Attribution::InChangedFile);
    }

    #[test]
    fn test_attribute_in_changed_file() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -10,2 +10,5 @@ fn handler() {
";
        let hunks = DiffHunks::parse_diff(diff);
        // Line 100 is in the file but far from the hunk
        assert_eq!(hunks.attribute(Path::new("src/api.rs"), Some(100)), Attribution::InChangedFile);
    }

    #[test]
    fn test_attribute_in_unchanged_file() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -10,2 +10,5 @@ fn handler() {
";
        let hunks = DiffHunks::parse_diff(diff);
        assert_eq!(hunks.attribute(Path::new("src/other.rs"), Some(10)), Attribution::InUnchangedFile);
    }

    #[test]
    fn test_attribute_no_line_number() {
        let diff = "\
diff --git a/src/api.rs b/src/api.rs
--- a/src/api.rs
+++ b/src/api.rs
@@ -10,2 +10,5 @@ fn handler() {
";
        let hunks = DiffHunks::parse_diff(diff);
        // File-level finding (no line) → InChangedFile
        assert_eq!(hunks.attribute(Path::new("src/api.rs"), None), Attribution::InChangedFile);
    }

    #[test]
    fn test_empty_diff() {
        let hunks = DiffHunks::parse_diff("");
        assert_eq!(hunks.changed_file_count(), 0);
        assert_eq!(hunks.attribute(Path::new("any.rs"), Some(1)), Attribution::InUnchangedFile);
    }
}
```

- [ ] **Step 2: Add module to cli**

In `src/cli/mod.rs`, add near the top with other module declarations:
```rust
mod diff_hunks;
```

(Private `mod diff_hunks;` is sufficient — diff.rs imports via `super::diff_hunks`.)

- [ ] **Step 3: Run tests**

Run: `cargo test diff_hunks -- --nocapture`
Expected: 9 tests pass

- [ ] **Step 4: Commit**

```bash
git add src/cli/diff_hunks.rs src/cli/mod.rs
git commit -m "feat(diff): add hunk-level attribution parser for git diff -U0"
```

---

### Task 2: Wire attribution into diff command + add CLI flags

**Files:**
- Modify: `src/cli/mod.rs` (add `--all` and `--changed` flags)
- Modify: `src/cli/diff.rs` (integrate DiffHunks, filter by attribution)

- [ ] **Step 1: Add CLI flags**

In `src/cli/mod.rs`, add to the `Diff` variant (~line 241):

```rust
    Diff {
        #[arg(value_name = "BASE_REF")]
        base_ref: Option<String>,

        #[arg(long, short = 'f', default_value = "text")]
        format: crate::reporters::OutputFormat,

        #[arg(long)]
        fail_on: Option<crate::models::Severity>,

        #[arg(long)]
        no_emoji: bool,

        #[arg(long, short = 'o')]
        output: Option<PathBuf>,

        /// Show ALL new findings, not just those in changed hunks
        #[arg(long)]
        all: bool,

        /// Show findings in changed files (hunks + non-hunk), hide unrelated files
        #[arg(long)]
        changed: bool,
    },
```

- [ ] **Step 2: Add AttributedFinding + keep DiffResult compatible**

In `src/cli/diff.rs`, add the `AttributedFinding` wrapper but DON'T change `DiffResult.new_findings`
type. Instead, store attributed findings separately:

```rust
use super::diff_hunks::{Attribution, DiffHunks};

/// A finding with its attribution (how it relates to the diff).
#[derive(Debug, Clone)]
pub struct AttributedFinding {
    pub finding: Finding,
    pub attribution: Attribution,
}
```

Keep the existing `DiffResult` struct unchanged. Instead, create a new `SmartDiffResult`
that `run()` builds after attribution:

```rust
/// Result of a smart diff with attribution.
#[derive(Debug)]
pub struct SmartDiffResult {
    pub base_ref: String,
    pub head_ref: String,
    pub files_changed: usize,
    pub new_findings: Vec<AttributedFinding>,  // attributed
    pub all_new_count: usize,                   // total before filtering (for telemetry)
    pub fixed_findings: Vec<Finding>,
    pub score_before: Option<f64>,
    pub score_after: Option<f64>,
}

impl SmartDiffResult {
    pub fn score_delta(&self) -> Option<f64> {
        match (self.score_before, self.score_after) {
            (Some(before), Some(after)) => Some(after - before),
            _ => None,
        }
    }

    /// Extract just the Finding structs (for APIs that need &[Finding]).
    pub fn findings_only(&self) -> Vec<Finding> {
        self.new_findings.iter().map(|af| af.finding.clone()).collect()
    }

    /// Extract hunk-level findings only.
    pub fn hunk_findings(&self) -> Vec<Finding> {
        self.new_findings.iter()
            .filter(|af| af.attribution == Attribution::InChangedHunk)
            .map(|af| af.finding.clone())
            .collect()
    }
}
```

The existing `diff_findings()` function stays unchanged — it returns raw `(Vec<Finding>, Vec<Finding>)`.
Attribution is applied after.

- [ ] **Step 3: Update `run()` to parse hunks and build SmartDiffResult**

Refactor `run()`. The existing `diff_findings()` function stays — it returns the raw `DiffResult`
with `Vec<Finding>`. Then we attribute and filter:

```rust
pub fn run(
    repo_path: &Path,
    base_ref: Option<String>,
    format: crate::reporters::OutputFormat,
    fail_on: Option<crate::models::Severity>,
    no_emoji: bool,
    output: Option<&Path>,
    all: bool,
    changed: bool,
    telemetry: &crate::telemetry::Telemetry,
) -> Result<()> {
    let start = Instant::now();
    // ... existing setup code (canonicalize, verify git, load cache) ...

    // Compute raw diff (existing function, unchanged)
    let raw_diff = diff_findings(&baseline, &head, base_label, "HEAD",
        files_changed, score_before, score_after);

    // Parse git diff hunks for attribution
    let effective_base = base_ref.as_deref().unwrap_or("HEAD~1");
    let hunks = DiffHunks::from_git_diff(&repo_path, effective_base)
        .unwrap_or_else(|e| {
            tracing::debug!("git diff -U0 failed: {e}, attributing all as InUnchangedFile");
            DiffHunks::parse_diff("") // empty hunks = all findings unattributed
        });

    // Attribute each new finding
    let all_attributed: Vec<AttributedFinding> = raw_diff.new_findings.into_iter().map(|f| {
        let attr = f.affected_files.first()
            .map(|path| hunks.attribute(path, f.line_start))
            .unwrap_or(Attribution::InUnchangedFile);
        AttributedFinding { finding: f, attribution: attr }
    }).collect();

    let all_new_count = all_attributed.len();

    // Filter based on flags
    let filtered: Vec<AttributedFinding> = if all {
        all_attributed
    } else if changed {
        all_attributed.into_iter()
            .filter(|af| af.attribution != Attribution::InUnchangedFile)
            .collect()
    } else {
        all_attributed.into_iter()
            .filter(|af| af.attribution == Attribution::InChangedHunk)
            .collect()
    };

    let result = SmartDiffResult {
        base_ref: raw_diff.base_ref,
        head_ref: raw_diff.head_ref,
        files_changed: hunks.changed_file_count(),
        new_findings: filtered,
        all_new_count,
        fixed_findings: raw_diff.fixed_findings,
        score_before: raw_diff.score_before,
        score_after: raw_diff.score_after,
    };

    // ... telemetry, output, fail-on (updated for SmartDiffResult) ...
}
```

IMPORTANT: `diff_findings()` is NOT changed. It still returns `DiffResult` with `Vec<Finding>`.
The new `SmartDiffResult` is built from its output + attribution.

- [ ] **Step 4: Update the CLI dispatch in mod.rs to pass new flags**

Find where `Commands::Diff { .. }` is dispatched (~line 638) and add `all` and `changed` parameters:

```rust
Some(Commands::Diff {
    base_ref,
    format,
    fail_on,
    no_emoji,
    output,
    all,
    changed,
}) => {
    diff::run(
        &repo_path, base_ref, format, fail_on, no_emoji,
        output.as_deref(), all, changed, &telemetry,
    )?;
}
```

- [ ] **Step 5: Update `check_fail_threshold` to use attributed findings**

The fail-on check uses `SmartDiffResult.hunk_findings()` which returns `Vec<Finding>`:

```rust
fn check_fail_threshold_smart(fail_on: Option<Severity>, result: &SmartDiffResult) -> Result<()> {
    if let Some(threshold) = fail_on {
        let hunk_findings = result.hunk_findings(); // Vec<Finding> — owned, correct type
        let new_summary = FindingsSummary::from_findings(&hunk_findings);
        let should_fail = match threshold {
            Severity::Critical => new_summary.critical > 0,
            Severity::High => new_summary.critical > 0 || new_summary.high > 0,
            Severity::Medium => {
                new_summary.critical > 0 || new_summary.high > 0 || new_summary.medium > 0
            }
            Severity::Low | Severity::Info => {
                new_summary.critical > 0 || new_summary.high > 0
                    || new_summary.medium > 0 || new_summary.low > 0
            }
        };
        if should_fail {
            anyhow::bail!(
                "Failing due to --fail-on={}: {} new finding(s) in changed hunks",
                threshold, hunk_findings.len()
            );
        }
    }
    Ok(())
}
```

Note: `hunk_findings()` returns `Vec<Finding>` (owned), so `FindingsSummary::from_findings(&hunk_findings)`
works because `&Vec<Finding>` coerces to `&[Finding]`.

- [ ] **Step 6: Verify compilation**

Run: `cargo check`

- [ ] **Step 7: Commit**

```bash
git add src/cli/diff.rs src/cli/mod.rs
git commit -m "feat(diff): wire hunk-level attribution with --all/--changed flags"
```

---

### Task 3: Update output formatters

**Files:**
- Modify: `src/cli/diff.rs` (format_text, format_json, format_sarif)

- [ ] **Step 1: Update format_text**

The text output now groups findings by attribution:

```rust
pub fn format_text(result: &SmartDiffResult, no_emoji: bool) -> String {
    let mut out = String::new();

    // Header
    out.push_str(&format!(
        "Repotoire Diff: {}..{} ({} files changed)\n\n",
        result.base_ref, result.head_ref, result.files_changed
    ));

    // Group new findings by attribution
    let hunk_findings: Vec<_> = result.new_findings.iter()
        .filter(|af| af.attribution == Attribution::InChangedHunk)
        .collect();
    let file_findings: Vec<_> = result.new_findings.iter()
        .filter(|af| af.attribution == Attribution::InChangedFile)
        .collect();
    let unrelated_findings: Vec<_> = result.new_findings.iter()
        .filter(|af| af.attribution == Attribution::InUnchangedFile)
        .collect();

    // YOUR CHANGES section
    if !hunk_findings.is_empty() {
        out.push_str(&format!(
            "{}\n",
            style(format!("YOUR CHANGES ({} finding{})", hunk_findings.len(),
                if hunk_findings.len() == 1 { "" } else { "s" })).bold()
        ));
        for af in &hunk_findings {
            format_finding_line(&mut out, &af.finding, no_emoji);
        }
        out.push('\n');
    }

    // PRE-EXISTING section (only shown with --changed or --all)
    if !file_findings.is_empty() {
        out.push_str(&format!(
            "{}\n",
            style(format!("PRE-EXISTING ({} in changed files)", file_findings.len())).dim()
        ));
        for af in &file_findings {
            format_finding_line(&mut out, &af.finding, no_emoji);
        }
        out.push('\n');
    }

    // UNRELATED section (only shown with --all)
    if !unrelated_findings.is_empty() {
        out.push_str(&format!(
            "{}\n",
            style(format!("UNRELATED ({} in unchanged files)", unrelated_findings.len())).dim()
        ));
        for af in &unrelated_findings {
            format_finding_line(&mut out, &af.finding, no_emoji);
        }
        out.push('\n');
    }

    // Score delta (preserve existing formatting from diff.rs:170-181)
    if let (Some(before), Some(after)) = (result.score_before, result.score_after) {
        let delta = after - before;
        let delta_str = if delta >= 0.0 {
            style(format!("+{:.1}", delta)).green().to_string()
        } else {
            style(format!("{:.1}", delta)).red().to_string()
        };
        out.push_str(&format!(
            "Score: {:.1} \u{2192} {:.1} ({})\n",
            before, after, delta_str,
        ));
    }

    // Fixed findings
    if !result.fixed_findings.is_empty() {
        let prefix = if no_emoji { "" } else { "✨ " };
        out.push_str(&format!("{}{} finding{} fixed\n",
            prefix, result.fixed_findings.len(),
            if result.fixed_findings.len() == 1 { "" } else { "s" }));
    }

    out
}

fn format_finding_line(out: &mut String, finding: &Finding, _no_emoji: bool) {
    let file = finding.affected_files.first()
        .map(|p| p.display().to_string())
        .unwrap_or_default();
    let line = finding.line_start.map(|l| format!(":{l}")).unwrap_or_default();
    out.push_str(&format!(
        "  {} {:<40} {}{}\n",
        severity_icon(&finding.severity),
        &finding.title.chars().take(40).collect::<String>(),
        file, line
    ));
}
```

- [ ] **Step 2: Update format_json to accept SmartDiffResult**

Change `format_json` signature from `&DiffResult` to `&SmartDiffResult`. Add `attribution` field:

```rust
pub fn format_json(result: &SmartDiffResult) -> String {
    let findings_json: Vec<serde_json::Value> = result.new_findings.iter().map(|af| {
        json!({
            "detector": af.finding.detector,
            "severity": format!("{:?}", af.finding.severity).to_lowercase(),
            "title": af.finding.title,
            "file": af.finding.affected_files.first().map(|p| p.display().to_string()),
            "line": af.finding.line_start,
            "attribution": match af.attribution {
                Attribution::InChangedHunk => "in_changed_hunk",
                Attribution::InChangedFile => "in_changed_file",
                Attribution::InUnchangedFile => "in_unchanged_file",
            }
        })
    }).collect();

    // For summary counts, use findings_only() to get Vec<Finding> for FindingsSummary
    let all_findings = result.findings_only();
    let new_summary = FindingsSummary::from_findings(&all_findings);
    // ... rest of JSON construction using findings_json and new_summary
}
```

- [ ] **Step 3: Update format_sarif to accept SmartDiffResult**

SARIF filters to `InChangedHunk` only. Use `hunk_findings()` helper:

```rust
pub fn format_sarif(result: &SmartDiffResult) -> Result<String> {
    let hunk_findings = result.hunk_findings(); // Vec<Finding> — correct type
    let summary = FindingsSummary::from_findings(&hunk_findings);

    let health = HealthReport {
        findings: hunk_findings,
        findings_summary: summary,
        // ... other fields with defaults
    };

    crate::reporters::report_with_format(&health, crate::reporters::OutputFormat::Sarif)
}
```

The `hunk_findings()` method returns `Vec<Finding>` (owned, not references), so
`FindingsSummary::from_findings(&hunk_findings)` compiles correctly.

- [ ] **Step 4: Update telemetry to use all_new_count**

In `send_diff_telemetry`, use `result.all_new_count` for the total (preserves historical metric
semantics — always reports ALL new findings, not just filtered):

```rust
fn send_diff_telemetry(telemetry: &crate::telemetry::Telemetry, repo_path: &Path, result: &SmartDiffResult) {
    // ... existing setup ...
    let event = crate::telemetry::events::DiffRun {
        // ...
        findings_added: result.all_new_count as u64,  // total, not filtered
        findings_removed: result.fixed_findings.len() as u64,
        // ...
    };
    // ...
}
```

NOTE: Don't add new fields to `DiffRun` for attribution counts — keep telemetry schema stable.
The `all_new_count` preserves the existing metric semantics.

- [ ] **Step 5: Update `emit_output` signature**

Change `emit_output` (diff.rs:335) from `result: &DiffResult` to `result: &SmartDiffResult`.
This is the glue between `run()` and the formatters — all three formatters now take `&SmartDiffResult`,
so `emit_output` must match. The summary line `result.new_findings.len()` still works since
`Vec<AttributedFinding>` has `.len()`.

- [ ] **Step 6: Verify compilation + run tests**

Run: `cargo check && cargo test diff -- --nocapture`

- [ ] **Step 6: Commit**

```bash
git add src/cli/diff.rs
git commit -m "feat(diff): update text/JSON/SARIF formatters with attribution grouping"
```

---

### Task 4: Integration test + manual verification

- [ ] **Step 1: Run full test suite**

Run: `cargo test --no-default-features`
Expected: all tests pass

- [ ] **Step 2: Run clippy**

Run: `cargo clippy -- -D warnings`

- [ ] **Step 3: Run fmt**

Run: `cargo fmt -- --check`

- [ ] **Step 4: Manual test**

```bash
cd ~/personal/repotoire/repotoire/repotoire-cli

# Build
cargo build

# Make a test change
echo "// test smart diff" >> src/lib.rs
git add src/lib.rs && git commit -m "test: smart diff test commit"

# Run diff (default: hunk-only findings)
./target/debug/repotoire diff HEAD~1 --format text

# Run with --all (all findings)
./target/debug/repotoire diff HEAD~1 --all --format text

# Run with --changed (changed files only)
./target/debug/repotoire diff HEAD~1 --changed --format text

# JSON output
./target/debug/repotoire diff HEAD~1 --format json | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f'{f[\"attribution\"]}: {f[\"title\"][:50]}') for f in d['new_findings'][:5]]"

# Clean up
git reset --hard HEAD~1
```

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve integration issues from smart diff"
```
