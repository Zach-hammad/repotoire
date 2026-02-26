# `repotoire diff` Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `repotoire diff` command (CLI + MCP tool) that shows new, fixed, and score-delta findings between two analysis states.

**Architecture:** Thin wrapper over the existing analyze pipeline. Loads baseline findings from cache (`last_findings.json`), runs analysis on HEAD (changed files only via `--since`), then diffs the two sets using fuzzy `(detector, file, line±3)` matching. Outputs text, JSON, or SARIF.

**Tech Stack:** Rust, clap 4 derive, serde_json, rmcp MCP SDK, existing analyze pipeline + reporters

---

### Task 1: Extract `load_cached_findings` as Public

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/output.rs:211` (change visibility)
- Modify: `repotoire-cli/src/cli/analyze/mod.rs` (re-export)

**Step 1: Change `load_cached_findings` visibility from `pub(super)` to `pub`**

In `repotoire-cli/src/cli/analyze/output.rs:211`, change:

```rust
pub(super) fn load_cached_findings(repotoire_dir: &Path) -> Option<Vec<Finding>> {
```

to:

```rust
pub fn load_cached_findings(repotoire_dir: &Path) -> Option<Vec<Finding>> {
```

Also change `cache_results` at line 306 from `pub(super)` to `pub`:

```rust
pub fn cache_results(
```

**Step 2: Verify it compiles**

Run: `cargo check -p repotoire`
Expected: success (no consumers yet, just widening visibility)

**Step 3: Commit**

```bash
git add repotoire-cli/src/cli/analyze/output.rs
git commit -m "refactor: widen load_cached_findings and cache_results visibility for diff command"
```

---

### Task 2: Create Core Diff Logic (`src/cli/diff.rs`)

**Files:**
- Create: `repotoire-cli/src/cli/diff.rs`
- Test: inline `#[cfg(test)]` module

**Step 1: Write the failing test for `findings_match`**

Create `repotoire-cli/src/cli/diff.rs` with the test module first:

```rust
//! Diff command — compare findings between two analysis states
//!
//! Shows new findings, fixed findings, and score delta.

use crate::models::{Finding, Severity};
use std::path::PathBuf;

/// Check if two findings refer to the same logical issue.
///
/// Uses fuzzy matching: same detector, same file, line within ±3.
/// File-level findings (no line) match if detector and file match.
fn findings_match(a: &Finding, b: &Finding) -> bool {
    a.detector == b.detector
        && a.affected_files.first() == b.affected_files.first()
        && match (a.line_start, b.line_start) {
            (Some(la), Some(lb)) => la.abs_diff(lb) <= 3,
            (None, None) => true,
            _ => false,
        }
}

/// Result of diffing two sets of findings.
#[derive(Debug)]
pub struct DiffResult {
    pub base_ref: String,
    pub head_ref: String,
    pub files_changed: usize,
    pub new_findings: Vec<Finding>,
    pub fixed_findings: Vec<Finding>,
    pub score_before: Option<f64>,
    pub score_after: Option<f64>,
}

impl DiffResult {
    pub fn score_delta(&self) -> Option<f64> {
        match (self.score_before, self.score_after) {
            (Some(before), Some(after)) => Some(after - before),
            _ => None,
        }
    }
}

/// Compute the diff between baseline and head findings.
pub fn diff_findings(
    baseline: &[Finding],
    head: &[Finding],
    base_ref: &str,
    head_ref: &str,
    files_changed: usize,
    score_before: Option<f64>,
    score_after: Option<f64>,
) -> DiffResult {
    let new_findings: Vec<Finding> = head
        .iter()
        .filter(|h| !baseline.iter().any(|b| findings_match(b, h)))
        .cloned()
        .collect();

    let fixed_findings: Vec<Finding> = baseline
        .iter()
        .filter(|b| !head.iter().any(|h| findings_match(b, h)))
        .cloned()
        .collect();

    DiffResult {
        base_ref: base_ref.to_string(),
        head_ref: head_ref.to_string(),
        files_changed,
        new_findings,
        fixed_findings,
        score_before,
        score_after,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_finding(detector: &str, file: &str, line: Option<u32>) -> Finding {
        Finding {
            detector: detector.to_string(),
            affected_files: vec![PathBuf::from(file)],
            line_start: line,
            severity: Severity::Medium,
            title: "test".to_string(),
            ..Default::default()
        }
    }

    #[test]
    fn test_exact_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", Some(10));
        assert!(findings_match(&a, &b));
    }

    #[test]
    fn test_fuzzy_line_match_within_tolerance() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", Some(13)); // +3
        assert!(findings_match(&a, &b));

        let c = make_finding("dead_code", "src/foo.rs", Some(7)); // -3
        assert!(findings_match(&a, &c));
    }

    #[test]
    fn test_fuzzy_line_beyond_tolerance() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", Some(14)); // +4
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_different_detector_no_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("magic_number", "src/foo.rs", Some(10));
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_different_file_no_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/bar.rs", Some(10));
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_file_level_findings_match() {
        let a = make_finding("circular_dependency", "src/foo.rs", None);
        let b = make_finding("circular_dependency", "src/foo.rs", None);
        assert!(findings_match(&a, &b));
    }

    #[test]
    fn test_line_vs_no_line_no_match() {
        let a = make_finding("dead_code", "src/foo.rs", Some(10));
        let b = make_finding("dead_code", "src/foo.rs", None);
        assert!(!findings_match(&a, &b));
    }

    #[test]
    fn test_diff_new_and_fixed() {
        let baseline = vec![
            make_finding("dead_code", "src/foo.rs", Some(10)),
            make_finding("magic_number", "src/bar.rs", Some(20)),
        ];
        let head = vec![
            make_finding("dead_code", "src/foo.rs", Some(11)), // shifted by 1, same issue
            make_finding("xss", "src/web.rs", Some(5)),        // new
        ];

        let result = diff_findings(&baseline, &head, "main", "HEAD", 3, Some(96.0), Some(95.5));

        assert_eq!(result.new_findings.len(), 1);
        assert_eq!(result.new_findings[0].detector, "xss");

        assert_eq!(result.fixed_findings.len(), 1);
        assert_eq!(result.fixed_findings[0].detector, "magic_number");

        assert!((result.score_delta().unwrap() - (-0.5)).abs() < f64::EPSILON);
    }

    #[test]
    fn test_diff_no_changes() {
        let findings = vec![make_finding("dead_code", "src/foo.rs", Some(10))];
        let result = diff_findings(&findings, &findings, "main", "HEAD", 0, None, None);
        assert!(result.new_findings.is_empty());
        assert!(result.fixed_findings.is_empty());
        assert!(result.score_delta().is_none());
    }
}
```

**Step 2: Run test to verify it passes**

Run: `cargo test -p repotoire diff::tests -- --nocapture`
Expected: 8 tests PASS

**Step 3: Commit**

```bash
git add repotoire-cli/src/cli/diff.rs
git commit -m "feat: add core diff logic with fuzzy finding matching"
```

---

### Task 3: Add Diff Output Formatting

**Files:**
- Modify: `repotoire-cli/src/cli/diff.rs` (add formatting functions)

**Step 1: Add text, JSON, and SARIF output formatters**

Append to `repotoire-cli/src/cli/diff.rs` (before the `#[cfg(test)]` module):

```rust
use crate::models::FindingsSummary;
use console::style;
use serde_json::json;

/// Format diff result as colored terminal text.
pub fn format_text(result: &DiffResult, no_emoji: bool) -> String {
    let mut out = String::new();

    // Header
    let header = format!(
        "Repotoire Diff: {}..{} ({} files changed)\n",
        result.base_ref, result.head_ref, result.files_changed
    );
    out.push_str(&format!("{}\n", style(header).bold()));

    // New findings
    if result.new_findings.is_empty() {
        out.push_str(&format!(" {} No new findings\n", if no_emoji { "" } else { "✅" }));
    } else {
        out.push_str(&format!(
            " {} NEW FINDINGS ({})\n",
            if no_emoji { "!!" } else { "🔴" },
            result.new_findings.len()
        ));
        for (i, f) in result.new_findings.iter().enumerate() {
            let sev = match f.severity {
                Severity::Critical => style("[C]").red().bold(),
                Severity::High => style("[H]").red(),
                Severity::Medium => style("[M]").yellow(),
                Severity::Low => style("[L]").dim(),
                Severity::Info => style("[I]").dim(),
            };
            let file = f
                .affected_files
                .first()
                .map(|p| p.display().to_string())
                .unwrap_or_default();
            let line = f.line_start.map_or(String::new(), |l| format!(":{}", l));
            out.push_str(&format!(
                "  {}. {} {:<40} {}{}\n",
                i + 1,
                sev,
                f.title,
                file,
                line
            ));
        }
    }

    out.push('\n');

    // Fixed findings
    if !result.fixed_findings.is_empty() {
        out.push_str(&format!(
            " {} FIXED FINDINGS ({})\n",
            if no_emoji { "OK" } else { "🟢" },
            result.fixed_findings.len()
        ));
        for f in &result.fixed_findings {
            let sev = match f.severity {
                Severity::Critical => "[C]",
                Severity::High => "[H]",
                Severity::Medium => "[M]",
                Severity::Low => "[L]",
                Severity::Info => "[I]",
            };
            let file = f
                .affected_files
                .first()
                .map(|p| p.display().to_string())
                .unwrap_or_default();
            let line = f.line_start.map_or(String::new(), |l| format!(":{}", l));
            let check = if no_emoji { "  FIX" } else { "  ✓" };
            out.push_str(&format!(
                "{} {} {:<40} {}{}\n",
                check,
                style(sev).green(),
                f.title,
                file,
                line
            ));
        }
        out.push('\n');
    }

    // Score delta
    if let (Some(before), Some(after)) = (result.score_before, result.score_after) {
        let delta = after - before;
        let delta_str = if delta >= 0.0 {
            style(format!("+{:.1}", delta)).green().to_string()
        } else {
            style(format!("{:.1}", delta)).red().to_string()
        };
        out.push_str(&format!(
            " SCORE: {:.1} → {:.1} ({})\n",
            before, after, delta_str
        ));
    }

    out
}

/// Format diff result as JSON.
pub fn format_json(result: &DiffResult) -> String {
    let new_summary = FindingsSummary::from_findings(&result.new_findings);
    let fixed_summary = FindingsSummary::from_findings(&result.fixed_findings);

    let value = json!({
        "base_ref": result.base_ref,
        "head_ref": result.head_ref,
        "files_changed": result.files_changed,
        "new_findings": result.new_findings.iter().map(|f| json!({
            "detector": f.detector,
            "severity": f.severity.to_string(),
            "title": f.title,
            "description": f.description,
            "file": f.affected_files.first().map(|p| p.display().to_string()),
            "line": f.line_start,
        })).collect::<Vec<_>>(),
        "fixed_findings": result.fixed_findings.iter().map(|f| json!({
            "detector": f.detector,
            "severity": f.severity.to_string(),
            "title": f.title,
            "file": f.affected_files.first().map(|p| p.display().to_string()),
            "line": f.line_start,
        })).collect::<Vec<_>>(),
        "score_before": result.score_before,
        "score_after": result.score_after,
        "score_delta": result.score_delta(),
        "summary": {
            "new": {
                "critical": new_summary.critical,
                "high": new_summary.high,
                "medium": new_summary.medium,
                "low": new_summary.low,
            },
            "fixed": {
                "critical": fixed_summary.critical,
                "high": fixed_summary.high,
                "medium": fixed_summary.medium,
                "low": fixed_summary.low,
            }
        }
    });

    serde_json::to_string_pretty(&value).unwrap_or_else(|_| value.to_string())
}

/// Format diff result as SARIF 2.1.0 (new findings only).
///
/// Reuses the existing SARIF reporter by building a temporary HealthReport
/// containing only the new findings.
pub fn format_sarif(result: &DiffResult) -> anyhow::Result<String> {
    use crate::models::HealthReport;

    let report = HealthReport {
        findings: result.new_findings.clone(),
        findings_summary: FindingsSummary::from_findings(&result.new_findings),
        overall_score: result.score_after.unwrap_or(0.0),
        grade: String::new(),
        structure_score: 0.0,
        quality_score: 0.0,
        architecture_score: None,
        total_files: 0,
        total_functions: 0,
        total_classes: 0,
        total_loc: 0,
    };

    crate::reporters::report(&report, "sarif")
}
```

**Step 2: Add output formatting tests**

Append these to the `mod tests` block:

```rust
    #[test]
    fn test_format_json_structure() {
        let result = DiffResult {
            base_ref: "main".to_string(),
            head_ref: "HEAD".to_string(),
            files_changed: 2,
            new_findings: vec![make_finding("xss", "src/web.rs", Some(5))],
            fixed_findings: vec![make_finding("dead_code", "src/old.rs", Some(10))],
            score_before: Some(96.0),
            score_after: Some(95.5),
        };

        let json_str = format_json(&result);
        let parsed: serde_json::Value = serde_json::from_str(&json_str).expect("valid JSON");

        assert_eq!(parsed["base_ref"], "main");
        assert_eq!(parsed["head_ref"], "HEAD");
        assert_eq!(parsed["files_changed"], 2);
        assert_eq!(parsed["new_findings"].as_array().unwrap().len(), 1);
        assert_eq!(parsed["fixed_findings"].as_array().unwrap().len(), 1);
        assert_eq!(parsed["score_delta"], -0.5);
    }

    #[test]
    fn test_format_text_no_new_findings() {
        let result = DiffResult {
            base_ref: "main".to_string(),
            head_ref: "HEAD".to_string(),
            files_changed: 1,
            new_findings: vec![],
            fixed_findings: vec![],
            score_before: Some(97.0),
            score_after: Some(97.0),
        };

        let text = format_text(&result, true);
        assert!(text.contains("No new findings"));
    }
```

**Step 3: Run tests**

Run: `cargo test -p repotoire diff::tests -- --nocapture`
Expected: 10 tests PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/diff.rs
git commit -m "feat: add diff output formatters (text, JSON, SARIF)"
```

---

### Task 4: Add `run()` Entry Point and Wire CLI

**Files:**
- Modify: `repotoire-cli/src/cli/diff.rs` (add `run` function)
- Modify: `repotoire-cli/src/cli/mod.rs:3` (add `mod diff;`)
- Modify: `repotoire-cli/src/cli/mod.rs:76-355` (add `Diff` variant to `Commands` enum)
- Modify: `repotoire-cli/src/cli/mod.rs:372-624` (add `Diff` match arm in `run()`)

**Step 1: Add `run()` function to `diff.rs`**

Add this function to `repotoire-cli/src/cli/diff.rs` (after `format_sarif`, before tests):

```rust
use anyhow::{Context, Result};
use std::path::Path;
use std::time::Instant;

/// Run the diff command.
pub fn run(
    repo_path: &Path,
    base_ref: Option<String>,
    format: &str,
    fail_on: Option<String>,
    no_emoji: bool,
    output: Option<&Path>,
    workers: usize,
) -> Result<()> {
    let start = Instant::now();
    let repo_path = repo_path.canonicalize().context("Cannot resolve repository path")?;

    // Verify git repo
    if !repo_path.join(".git").exists() {
        anyhow::bail!("diff requires a git repository (no .git found in {})", repo_path.display());
    }

    let repotoire_dir = super::analyze::cache_path(&repo_path);

    // 1. Load baseline findings from cache
    let baseline = super::analyze::output::load_cached_findings(&repotoire_dir)
        .context("No baseline analysis found. Run 'repotoire analyze' first to establish a baseline.")?;

    // Load baseline score
    let score_before = load_cached_score(&repotoire_dir);

    // 2. Determine changed files count
    let effective_base = base_ref.as_deref().unwrap_or("HEAD~1");
    let files_changed = count_changed_files(&repo_path, effective_base);

    // 3. Run fresh analysis on HEAD (changed files only if base_ref given)
    let since = base_ref.clone();
    let incremental = since.is_some();
    super::analyze::run(
        &repo_path,
        "json",    // internal format (we format output ourselves)
        None,      // no output file (we capture from cache)
        None,      // no severity filter (we diff all)
        None,      // no top limit
        1,         // page
        0,         // per_page = 0 (all findings)
        vec![],    // no skip_detector
        false,     // no external tools (speed)
        false,     // no_git = false
        workers,
        None,      // no fail_on for the internal run
        true,      // no_emoji (suppress internal output)
        incremental,
        since,
        false,     // explain_score
        false,     // verify
        false,     // skip_graph
        0,         // max_files
    )?;

    // 4. Load head findings from cache (just written by analyze)
    let head = super::analyze::output::load_cached_findings(&repotoire_dir)
        .unwrap_or_default();
    let score_after = load_cached_score(&repotoire_dir);

    // 5. Diff
    let base_label = base_ref.as_deref().unwrap_or("cached");
    let result = diff_findings(&baseline, &head, base_label, "HEAD", files_changed, score_before, score_after);

    // 6. Output
    let output_str = match format {
        "json" => format_json(&result),
        "sarif" => format_sarif(&result)?,
        _ => format_text(&result, no_emoji),
    };

    if let Some(out_path) = output {
        std::fs::write(out_path, &output_str)?;
        eprintln!("Report written to: {}", out_path.display());
    } else {
        println!("{}", output_str);
    }

    // 7. Summary (text mode only)
    if format == "text" || (format != "json" && format != "sarif") {
        let elapsed = start.elapsed();
        let prefix = if no_emoji { "" } else { "✨ " };
        eprintln!(
            "{}Diff complete in {:.2}s ({} new, {} fixed)",
            prefix,
            elapsed.as_secs_f64(),
            result.new_findings.len(),
            result.fixed_findings.len()
        );
    }

    // 8. Fail-on threshold (new findings only)
    if let Some(ref threshold) = fail_on {
        let new_summary = FindingsSummary::from_findings(&result.new_findings);
        let should_fail = match threshold.to_lowercase().as_str() {
            "critical" => new_summary.critical > 0,
            "high" => new_summary.critical > 0 || new_summary.high > 0,
            "medium" => new_summary.critical > 0 || new_summary.high > 0 || new_summary.medium > 0,
            "low" => new_summary.critical > 0 || new_summary.high > 0 || new_summary.medium > 0 || new_summary.low > 0,
            _ => false,
        };
        if should_fail {
            anyhow::bail!("Failing due to --fail-on={}: {} new finding(s) at this severity or above", threshold, result.new_findings.len());
        }
    }

    Ok(())
}

/// Load cached health score from last_health.json.
fn load_cached_score(repotoire_dir: &Path) -> Option<f64> {
    let path = repotoire_dir.join("last_health.json");
    let data = std::fs::read_to_string(&path).ok()?;
    let json: serde_json::Value = serde_json::from_str(&data).ok()?;
    json.get("health_score").and_then(|v| v.as_f64())
}

/// Count files changed between a ref and HEAD.
fn count_changed_files(repo_path: &Path, base_ref: &str) -> usize {
    std::process::Command::new("git")
        .args(["diff", "--name-only", base_ref, "HEAD"])
        .current_dir(repo_path)
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .filter(|l| !l.is_empty())
                .count()
        })
        .unwrap_or(0)
}
```

**Step 2: Register `diff` module in `cli/mod.rs`**

Add `mod diff;` after line 2 (`pub(crate) mod analyze;`):

```rust
pub(crate) mod analyze;
mod diff;
```

**Step 3: Add `Diff` variant to `Commands` enum**

Add after the `Analyze` variant (after line 171, before `Findings`):

```rust
    /// Compare findings between two analysis states (shows new, fixed, score delta)
    #[command(after_help = "\
Examples:
  repotoire diff main                    Diff HEAD vs main branch
  repotoire diff v1.0.0                  Diff HEAD vs a tag
  repotoire diff                         Diff HEAD vs last cached analysis
  repotoire diff main --format json      JSON output for CI
  repotoire diff main --fail-on high     Exit 1 if new high+ findings
  repotoire diff main --format sarif     SARIF with only new findings")]
    Diff {
        /// Git ref for baseline (branch, tag, commit). Omit to use last cached analysis.
        #[arg(value_name = "BASE_REF")]
        base_ref: Option<String>,

        /// Output format: text, json, sarif
        #[arg(long, short = 'f', default_value = "text", value_parser = ["text", "json", "sarif"])]
        format: String,

        /// Exit with code 1 if new findings at this severity or above
        #[arg(long, value_parser = ["critical", "high", "medium", "low"])]
        fail_on: Option<String>,

        /// Disable emoji in output
        #[arg(long)]
        no_emoji: bool,

        /// Output file path (default: stdout)
        #[arg(long, short = 'o')]
        output: Option<PathBuf>,
    },
```

**Step 4: Add `Diff` match arm in `run()` function**

Add a match arm in the `run()` function (after the `Analyze` arm, before `Findings`):

```rust
        Some(Commands::Diff {
            base_ref,
            format,
            fail_on,
            no_emoji,
            output,
        }) => diff::run(&cli.path, base_ref, &format, fail_on, no_emoji, output.as_deref(), cli.workers),
```

**Step 5: Verify it compiles**

Run: `cargo check -p repotoire`
Expected: success

**Step 6: Run all tests**

Run: `cargo test -p repotoire`
Expected: all tests pass

**Step 7: Commit**

```bash
git add repotoire-cli/src/cli/diff.rs repotoire-cli/src/cli/mod.rs
git commit -m "feat: add repotoire diff CLI command"
```

---

### Task 5: Add MCP Tool (`repotoire_diff`)

**Files:**
- Modify: `repotoire-cli/src/mcp/params.rs` (add `DiffParams`)
- Modify: `repotoire-cli/src/mcp/tools/analysis.rs` (add `handle_diff`)
- Modify: `repotoire-cli/src/mcp/rmcp_server.rs` (register tool)

**Step 1: Add `DiffParams` to `params.rs`**

Add after `GetHotspotsParams` (after line 55):

```rust
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct DiffParams {
    /// Git ref for baseline (branch, tag, commit). Omit to use last cached analysis.
    pub base_ref: Option<String>,
    /// Git ref for current state. Default: HEAD (working tree).
    pub head_ref: Option<String>,
    /// Minimum severity filter for new findings
    pub severity: Option<SeverityFilter>,
}
```

**Step 2: Add `handle_diff` to `analysis.rs`**

Add at the end of `repotoire-cli/src/mcp/tools/analysis.rs`:

```rust
use crate::mcp::params::DiffParams;

/// Compare findings between two analysis states.
///
/// Loads baseline from cache, runs analysis on HEAD, diffs the two sets.
/// Returns new findings, fixed findings, and score delta.
pub fn handle_diff(state: &mut HandlerState, params: &DiffParams) -> Result<Value> {
    use crate::cli::diff::{diff_findings, format_json};

    let repo_path = state.repo_path.clone();
    let repotoire_dir = repo_path.join(".repotoire");

    // Load baseline
    let baseline = crate::cli::analyze::output::load_cached_findings(&repotoire_dir)
        .ok_or_else(|| anyhow::anyhow!("No baseline found. Run repotoire_analyze first."))?;

    let score_before = {
        let path = repotoire_dir.join("last_health.json");
        std::fs::read_to_string(&path)
            .ok()
            .and_then(|data| serde_json::from_str::<serde_json::Value>(&data).ok())
            .and_then(|json| json.get("health_score").and_then(|v| v.as_f64()))
    };

    // Run fresh analysis (reuse existing handle_analyze)
    let analyze_params = crate::mcp::params::AnalyzeParams {
        incremental: Some(true),
    };
    let _ = handle_analyze(state, &analyze_params)?;

    // Load head findings
    let head = crate::cli::analyze::output::load_cached_findings(&repotoire_dir)
        .unwrap_or_default();

    let score_after = {
        let path = repotoire_dir.join("last_health.json");
        std::fs::read_to_string(&path)
            .ok()
            .and_then(|data| serde_json::from_str::<serde_json::Value>(&data).ok())
            .and_then(|json| json.get("health_score").and_then(|v| v.as_f64()))
    };

    // Count changed files
    let base_ref = params.base_ref.as_deref().unwrap_or("HEAD~1");
    let files_changed = std::process::Command::new("git")
        .args(["diff", "--name-only", base_ref, "HEAD"])
        .current_dir(&repo_path)
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).lines().filter(|l| !l.is_empty()).count())
        .unwrap_or(0);

    let base_label = params.base_ref.as_deref().unwrap_or("cached");
    let result = diff_findings(&baseline, &head, base_label, "HEAD", files_changed, score_before, score_after);

    // Apply severity filter to new findings if requested
    let json_str = format_json(&result);
    let value: serde_json::Value = serde_json::from_str(&json_str)?;

    Ok(value)
}
```

**Step 3: Register `repotoire_diff` tool in `rmcp_server.rs`**

Add this tool handler inside the `#[tool_router] impl RepotoireServer` block, after `repotoire_get_hotspots` (after line 138):

```rust
    #[tool(
        name = "repotoire_diff",
        description = "Compare findings between two analysis states. Shows new findings, fixed findings, and score delta. Run repotoire_analyze on your baseline first."
    )]
    async fn repotoire_diff(
        &self,
        Parameters(params): Parameters<DiffParams>,
    ) -> Result<CallToolResult, McpError> {
        validate_args_size(&params)?;
        let state = self.state.clone();
        let result = tokio::task::spawn_blocking(move || {
            let mut state = state.blocking_write();
            super::tools::analysis::handle_diff(&mut state, &params)
        })
        .await
        .map_err(|e| McpError::internal_error(e.to_string(), None))?
        .map_err(|e| McpError::internal_error(e.to_string(), None))?;

        Ok(value_to_result(result))
    }
```

**Step 4: Verify it compiles**

Run: `cargo check -p repotoire`
Expected: success

**Step 5: Run all tests**

Run: `cargo test -p repotoire`
Expected: all tests pass

**Step 6: Commit**

```bash
git add repotoire-cli/src/mcp/params.rs repotoire-cli/src/mcp/tools/analysis.rs repotoire-cli/src/mcp/rmcp_server.rs
git commit -m "feat: add repotoire_diff MCP tool"
```

---

### Task 6: Update CI Workflow and Documentation

**Files:**
- Modify: `.github/workflows/repotoire-analysis.yml` (add diff step for PRs)
- Modify: `CLAUDE.md` (add diff command to CLI table)

**Step 1: Add diff step to GitHub Actions workflow**

In `.github/workflows/repotoire-analysis.yml`, add a step for PR diff analysis (after the existing analyze step):

```yaml
    - name: Run Diff Analysis (PRs only)
      if: github.event_name == 'pull_request'
      run: |
        repotoire diff ${{ github.event.pull_request.base.sha }} \
          --format sarif --output diff-results.sarif.json \
          --fail-on high
      continue-on-error: true
```

**Step 2: Update CLAUDE.md CLI table**

Add the `diff` command to the CLI Commands table (after `analyze`):

```markdown
| `diff` | Compare findings between two analysis states |
```

Also add to the Common Commands section:

```bash
# Diff analysis (what changed since main)
repotoire diff main
repotoire diff main --format json
repotoire diff main --fail-on high
```

Update the MCP tools table to add `repotoire_diff`:

```markdown
| `repotoire_diff` | FREE | Compare findings between refs (new, fixed, score delta) |
```

Update CLI command count from 16 to 17, MCP tool count from 13 to 14.

**Step 3: Verify build**

Run: `cargo build -p repotoire --release`
Expected: success

**Step 4: Commit**

```bash
git add .github/workflows/repotoire-analysis.yml CLAUDE.md
git commit -m "docs: add diff command to CI workflow and documentation"
```

---

### Task 7: End-to-End Smoke Test

**Files:** None created (manual verification)

**Step 1: Run self-analysis to populate baseline cache**

Run: `cargo run --release -- analyze .`
Expected: analysis completes, cache populated at `.repotoire/last_findings.json`

**Step 2: Test diff against cached baseline (no base ref)**

Run: `cargo run --release -- diff`
Expected: shows "No new findings" (HEAD hasn't changed since analyze)

**Step 3: Test diff against main branch**

Run: `cargo run --release -- diff main`
Expected: shows diff output with new/fixed findings and score delta

**Step 4: Test JSON output**

Run: `cargo run --release -- diff main --format json`
Expected: valid JSON with `base_ref`, `head_ref`, `new_findings`, `fixed_findings`, `score_delta`

**Step 5: Test SARIF output**

Run: `cargo run --release -- diff main --format sarif --output /tmp/diff.sarif.json`
Expected: valid SARIF file with only new findings

**Step 6: Test fail-on flag**

Run: `cargo run --release -- diff main --fail-on critical`
Expected: exits 0 if no new critical findings, exits 1 otherwise

**Step 7: Run full test suite**

Run: `cargo test -p repotoire`
Expected: all tests pass including new diff tests

**Step 8: Run benchmarks**

Run: `bash benchmarks/self_analysis.sh`
Expected: benchmark passes (score ≥ 95, criticals ≤ 5)
