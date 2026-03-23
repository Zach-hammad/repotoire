# Watch Mode Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rearchitect `repotoire watch` from a monolithic 424-line function into testable modules with error recovery, consistent file filtering, and CLI flag parity.

**Architecture:** Split `cli/watch.rs` into `cli/watch/` module with 5 files: `mod.rs` (thin CLI glue), `engine.rs` (reusable `WatchEngine`), `delta.rs` (pure delta computation), `display.rs` (terminal output), `filter.rs` (ignore-crate file filtering). The `WatchEngine` struct is the reusable core — it will also serve a future LSP server.

**Tech Stack:** Rust, `notify`/`notify_debouncer_full` (file watching), `ignore` crate (gitignore/repotoireignore filtering), `console` (terminal styling), `chrono` (timestamps).

**Spec:** `docs/superpowers/specs/2026-03-21-watch-mode-hardening-design.md`

---

## File Structure

| File | Responsibility | Status |
|------|----------------|--------|
| `repotoire-cli/src/cli/watch/mod.rs` | CLI entry: `run()` wires watcher → engine → display | Create (from existing `watch.rs`) |
| `repotoire-cli/src/cli/watch/delta.rs` | `WatchDelta` struct + `compute_delta()` pure function | Create |
| `repotoire-cli/src/cli/watch/display.rs` | Terminal output: `display_delta()`, `display_error()`, `display_initial()`, helpers | Create |
| `repotoire-cli/src/cli/watch/filter.rs` | `WatchFilter` struct using `ignore` crate | Create |
| `repotoire-cli/src/cli/watch/engine.rs` | `WatchEngine` struct + `WatchReanalysis` enum | Create |
| `repotoire-cli/src/cli/mod.rs` | Update `Watch` command definition + dispatch | Modify (lines 335-343, 647) |
| `repotoire-cli/src/cli/analyze/files.rs` | Widen `SUPPORTED_EXTENSIONS` visibility from `pub(super)` to `pub(crate)` | Modify (line 22) |
| `repotoire-cli/src/cli/watch.rs` | Delete after migration | Delete |

---

### Task 0: Prerequisites — derive Default and widen visibility

**Files:**
- Modify: `repotoire-cli/src/scoring/graph_scorer.rs` (add `Default` derive to `ScoreBreakdown`, `PillarBreakdown`)
- Modify: `repotoire-cli/src/engine/mod.rs` (add `Default` derive to `AnalysisStats`, `AnalysisMode`)
- Modify: `repotoire-cli/src/cli/mod.rs` (change `mod watch;` to `pub mod watch;` on line 4)
- Modify: `repotoire-cli/src/cli/analyze/files.rs` (change `pub(super)` to `pub(crate)` on line 22)

- [ ] **Step 1: Add `Default` derives for test constructibility**

In `repotoire-cli/src/scoring/graph_scorer.rs`, add `Default` to the derive lists for `ScoreBreakdown` and `PillarBreakdown`. Find each struct's `#[derive(...)]` line and add `Default`.

In `repotoire-cli/src/engine/mod.rs`, add `Default` to `AnalysisStats`'s derive list. For `AnalysisMode` enum, add `Default` derive and mark `Cold` as `#[default]`.

- [ ] **Step 2: Make `watch` module public**

In `repotoire-cli/src/cli/mod.rs` line 4, change:
```rust
mod watch;
```
to:
```rust
pub mod watch;
```

- [ ] **Step 3: Widen `SUPPORTED_EXTENSIONS` visibility**

In `repotoire-cli/src/cli/analyze/files.rs` line 22, change:
```rust
pub(super) const SUPPORTED_EXTENSIONS: &[&str] = &[
```
to:
```rust
pub(crate) const SUPPORTED_EXTENSIONS: &[&str] = &[
```

Note: This list includes `rb`, `php`, `swift` which the old `WATCH_EXTENSIONS` lacked, and lacks `cjs` which the old list had. This is the intended unification — watch will now match analyze's extension list exactly.

- [ ] **Step 4: Verify it compiles**

Run: `nix-shell -p gnumake --run "cargo check 2>&1 | tail -3"`
Expected: `Finished`

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/scoring/graph_scorer.rs repotoire-cli/src/engine/mod.rs repotoire-cli/src/cli/mod.rs repotoire-cli/src/cli/analyze/files.rs
git commit -m "chore: add Default derives and widen visibility for watch refactor"
```

---

### Task 1: Convert `watch.rs` to `watch/mod.rs` (no behavior change)

**Files:**
- Delete: `repotoire-cli/src/cli/watch.rs`
- Create: `repotoire-cli/src/cli/watch/mod.rs` (exact same content)

- [ ] **Step 1: Create the `watch/` directory**

```bash
mkdir -p repotoire-cli/src/cli/watch
```

- [ ] **Step 2: Move the file**

```bash
mv repotoire-cli/src/cli/watch.rs repotoire-cli/src/cli/watch/mod.rs
```

- [ ] **Step 3: Verify it compiles**

Run: `nix-shell -p gnumake --run "cargo check 2>&1 | tail -3"`
Expected: `Finished` (no errors — `mod watch;` in `cli/mod.rs` resolves to `watch/mod.rs` automatically)

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/watch/
git add repotoire-cli/src/cli/watch.rs
git commit -m "refactor(watch): convert watch.rs to watch/mod.rs module directory"
```

---

### Task 2: Extract `delta.rs` — pure delta computation

**Files:**
- Create: `repotoire-cli/src/cli/watch/delta.rs`
- Modify: `repotoire-cli/src/cli/watch/mod.rs`

- [ ] **Step 1: Write the failing test in `delta.rs`**

Create `repotoire-cli/src/cli/watch/delta.rs` with the test module first:

```rust
use std::path::PathBuf;
use std::time::Duration;

use crate::engine::{AnalysisResult, AnalysisStats, ScoreResult};
use crate::models::{Finding, Severity};
use crate::scoring::ScoreBreakdown;

/// Delta between two consecutive analysis results.
pub struct WatchDelta {
    pub new_findings: Vec<Finding>,
    pub fixed_findings: Vec<Finding>,
    pub total_findings: usize,
    pub score: f64,
    pub score_delta: Option<f64>,
    pub elapsed: Duration,
    pub changed_files: Vec<PathBuf>,
}

/// Compute the delta between a new result and an optional previous result.
/// Pure function — no I/O, fully testable.
pub fn compute_delta(
    current: &AnalysisResult,
    previous: Option<&AnalysisResult>,
    changed_files: Vec<PathBuf>,
    elapsed: Duration,
) -> WatchDelta {
    todo!()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_finding(detector: &str, file: &str, line: u32) -> Finding {
        Finding {
            detector: detector.to_string(),
            affected_files: vec![PathBuf::from(file)],
            line_start: Some(line),
            severity: Severity::High,
            title: format!("{} finding", detector),
            ..Default::default()
        }
    }

    fn make_result(findings: Vec<Finding>, score: f64) -> AnalysisResult {
        // Note: ScoreBreakdown and AnalysisStats do NOT derive Default.
        // As a prerequisite, add `#[derive(Default)]` to:
        //   - ScoreBreakdown in scoring/graph_scorer.rs
        //   - PillarBreakdown in scoring/graph_scorer.rs
        //   - AnalysisStats in engine/mod.rs
        //   - AnalysisMode in engine/mod.rs (add #[default] to Cold variant)
        // This is safe — all fields have sensible zero/empty defaults.
        AnalysisResult {
            findings,
            score: ScoreResult {
                overall: score,
                grade: "B".to_string(),
                breakdown: ScoreBreakdown::default(),
            },
            stats: AnalysisStats::default(),
        }
    }

    #[test]
    fn delta_no_previous() {
        let result = make_result(vec![make_finding("XSS", "a.rs", 10)], 85.0);
        let delta = compute_delta(&result, None, vec![], Duration::from_millis(100));
        assert!(delta.new_findings.is_empty(), "first run has no 'new' findings");
        assert!(delta.fixed_findings.is_empty());
        assert_eq!(delta.total_findings, 1);
        assert_eq!(delta.score, 85.0);
        assert!(delta.score_delta.is_none());
    }

    #[test]
    fn delta_new_findings() {
        let prev = make_result(vec![], 90.0);
        let curr = make_result(vec![make_finding("XSS", "a.rs", 10)], 85.0);
        let delta = compute_delta(&curr, Some(&prev), vec![], Duration::from_millis(50));
        assert_eq!(delta.new_findings.len(), 1);
        assert_eq!(delta.new_findings[0].detector, "XSS");
        assert!(delta.fixed_findings.is_empty());
        assert_eq!(delta.score_delta, Some(-5.0));
    }

    #[test]
    fn delta_fixed_findings() {
        let prev = make_result(vec![make_finding("XSS", "a.rs", 10)], 85.0);
        let curr = make_result(vec![], 90.0);
        let delta = compute_delta(&curr, Some(&prev), vec![], Duration::from_millis(50));
        assert!(delta.new_findings.is_empty());
        assert_eq!(delta.fixed_findings.len(), 1);
        assert_eq!(delta.fixed_findings[0].detector, "XSS");
        assert_eq!(delta.score_delta, Some(5.0));
    }

    #[test]
    fn delta_both_new_and_fixed() {
        let prev = make_result(vec![make_finding("XSS", "a.rs", 10)], 85.0);
        let curr = make_result(vec![make_finding("SQLi", "b.rs", 20)], 84.0);
        let delta = compute_delta(&curr, Some(&prev), vec![], Duration::from_millis(50));
        assert_eq!(delta.new_findings.len(), 1);
        assert_eq!(delta.new_findings[0].detector, "SQLi");
        assert_eq!(delta.fixed_findings.len(), 1);
        assert_eq!(delta.fixed_findings[0].detector, "XSS");
    }

    #[test]
    fn delta_same_results() {
        let f = make_finding("XSS", "a.rs", 10);
        let prev = make_result(vec![f.clone()], 85.0);
        let curr = make_result(vec![f], 85.0);
        let delta = compute_delta(&curr, Some(&prev), vec![], Duration::from_millis(50));
        assert!(delta.new_findings.is_empty());
        assert!(delta.fixed_findings.is_empty());
        assert_eq!(delta.score_delta, Some(0.0));
    }

    #[test]
    fn delta_fingerprint_stability() {
        // Same (detector, file, line) but different title should NOT be "new"
        let prev = make_result(vec![make_finding("XSS", "a.rs", 10)], 85.0);
        let mut f = make_finding("XSS", "a.rs", 10);
        f.title = "Different title".to_string();
        let curr = make_result(vec![f], 85.0);
        let delta = compute_delta(&curr, Some(&prev), vec![], Duration::from_millis(50));
        assert!(delta.new_findings.is_empty(), "same fingerprint = not new");
        assert!(delta.fixed_findings.is_empty());
    }
}
```

- [ ] **Step 2: Add `mod delta;` to `watch/mod.rs` and run tests to verify they fail**

Add at the top of `repotoire-cli/src/cli/watch/mod.rs`:
```rust
pub mod delta;
```

Run: `nix-shell -p gnumake --run "cargo test watch::delta -- --nocapture 2>&1 | tail -10"`
Expected: FAIL — `todo!()` panics.

- [ ] **Step 3: Implement `compute_delta`**

Replace the `todo!()` in `delta.rs` with the implementation (moved from `watch/mod.rs` lines 34-85, adapted to new signature):

```rust
pub fn compute_delta(
    current: &AnalysisResult,
    previous: Option<&AnalysisResult>,
    changed_files: Vec<PathBuf>,
    elapsed: Duration,
) -> WatchDelta {
    let score = current.score.overall;
    let total_findings = current.findings.len();

    let Some(prev) = previous else {
        return WatchDelta {
            new_findings: Vec::new(),
            fixed_findings: Vec::new(),
            total_findings,
            score,
            score_delta: None,
            elapsed,
            changed_files,
        };
    };

    let score_delta = Some(current.score.overall - prev.score.overall);

    let fingerprint = |f: &Finding| -> (String, Option<PathBuf>, Option<u32>) {
        (
            f.detector.clone(),
            f.affected_files.first().cloned(),
            f.line_start,
        )
    };

    let prev_set: std::collections::HashSet<_> = prev.findings.iter().map(&fingerprint).collect();
    let curr_set: std::collections::HashSet<_> = current.findings.iter().map(&fingerprint).collect();

    let new_findings: Vec<Finding> = current
        .findings
        .iter()
        .filter(|f| !prev_set.contains(&fingerprint(f)))
        .cloned()
        .collect();

    let fixed_findings: Vec<Finding> = prev
        .findings
        .iter()
        .filter(|f| !curr_set.contains(&fingerprint(f)))
        .cloned()
        .collect();

    WatchDelta {
        new_findings,
        fixed_findings,
        total_findings,
        score,
        score_delta,
        elapsed,
        changed_files,
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell -p gnumake --run "cargo test watch::delta -- --nocapture 2>&1 | tail -10"`
Expected: 6 tests PASS.

- [ ] **Step 5: Remove old `WatchDelta` and `compute_delta` from `mod.rs`**

In `repotoire-cli/src/cli/watch/mod.rs`:
- Delete the old `WatchDelta` struct (lines 25-31) and `compute_delta` function (lines 33-85).
- Replace usages with `use delta::{WatchDelta, compute_delta};`
- Update the `compute_delta` call site (around line 200) to pass `changed_files.clone()` and `elapsed` as new args.

- [ ] **Step 6: Verify it compiles**

Run: `nix-shell -p gnumake --run "cargo check 2>&1 | tail -3"`
Expected: `Finished`

- [ ] **Step 7: Commit**

```bash
git add repotoire-cli/src/cli/watch/delta.rs repotoire-cli/src/cli/watch/mod.rs
git commit -m "refactor(watch): extract delta.rs with WatchDelta and compute_delta"
```

---

### Task 3: Extract `display.rs` — terminal output

**Files:**
- Create: `repotoire-cli/src/cli/watch/display.rs`
- Modify: `repotoire-cli/src/cli/watch/mod.rs`

- [ ] **Step 1: Create `display.rs` with all display functions**

Move these functions from `mod.rs` to `display.rs`:
- `display_delta()` (lines 272-369)
- `score_suffix()` (lines 372-380)
- `is_ai_detector()` (lines 383-391)
- `severity_icon()` (lines 410-423)
- `filter_delta_relaxed()` (lines 253-269) — rename to `filter_delta_by_severity()` and generalize to accept a `Severity` parameter

Add two new functions:
- `display_error()` — for showing analysis errors inline
- `display_initial()` — for showing the initial analysis result

```rust
use std::path::{Path, PathBuf};
use std::time::Duration;

use console::style;

use super::delta::WatchDelta;
use crate::engine::AnalysisResult;
use crate::models::{Finding, Severity};

/// Display the initial analysis result on startup.
pub fn display_initial(result: &AnalysisResult, elapsed: Duration, no_emoji: bool, quiet: bool) {
    if quiet {
        return;
    }
    println!(
        "  {} Initial analysis: {} findings, score {:.1} ({:.2}s)",
        style("✓").green(),
        result.findings.len(),
        result.score.overall,
        elapsed.as_secs_f64()
    );
    println!();
}

/// Display an analysis error inline (red, with timestamp). Keep watching.
pub fn display_error(message: &str, changed_files: &[PathBuf], repo_path: &Path, no_emoji: bool) {
    let time = chrono::Local::now().format("%H:%M:%S");
    let file_list: String = changed_files
        .iter()
        .map(|p| {
            p.strip_prefix(repo_path)
                .unwrap_or(p)
                .display()
                .to_string()
        })
        .collect::<Vec<_>>()
        .join(", ");
    eprintln!(
        "{} {} {} {}",
        style(format!("[{}]", time)).dim(),
        if no_emoji { "ERR" } else { "❌" },
        style(&file_list).dim(),
        style(format!("Analysis error: {}", message)).red()
    );
    eprintln!(
        "           {}",
        style("Watching for next change...").dim()
    );
}

/// Display the results of an incremental update.
pub fn display_delta(
    delta: &WatchDelta,
    repo_path: &Path,
    no_emoji: bool,
    quiet: bool,
) {
    // ... move existing display_delta body from mod.rs here unchanged ...
    // (uses delta.changed_files and delta.elapsed instead of separate params)
}

/// Filter a WatchDelta to only show findings at or above min_severity.
pub fn filter_delta_by_severity(delta: WatchDelta, min_severity: Severity) -> WatchDelta {
    // Severity discriminants: Info=0, Low=1, Medium=2, High=3, Critical=4
    // Keep findings at or above min_severity
    let dominated_by = |s: Severity| -> bool {
        (s as u8) >= (min_severity as u8)
    };
    WatchDelta {
        new_findings: delta
            .new_findings
            .into_iter()
            .filter(|f| dominated_by(f.severity))
            .collect(),
        fixed_findings: delta
            .fixed_findings
            .into_iter()
            .filter(|f| dominated_by(f.severity))
            .collect(),
        ..delta
    }
}

// Move severity_icon, score_suffix, is_ai_detector here unchanged.

/// Display compact one-liner when no findings changed (Unchanged variant).
/// Takes data directly — no dependency on WatchEngine (keeps display decoupled).
pub fn display_unchanged(
    changed_files: &[PathBuf],
    repo_path: &Path,
    total_findings: usize,
    score: Option<f64>,
    no_emoji: bool,
) {
    let time = chrono::Local::now().format("%H:%M:%S");
    let file_list: String = changed_files
        .iter()
        .map(|p| p.strip_prefix(repo_path).unwrap_or(p).display().to_string())
        .collect::<Vec<_>>()
        .join(", ");
    let score_str = score
        .map(|s| format!(", score {:.1}", s))
        .unwrap_or_default();
    println!(
        "{} {} {} ({} total findings{})",
        style(format!("[{}]", time)).dim(),
        if no_emoji { "→" } else { "📝" },
        style(&file_list).dim(),
        total_findings,
        score_str,
    );
}
```

Note: The `display_delta` function signature changes — it no longer takes `changed_files`, `elapsed`, or `repo_path` separately; these are now fields on `WatchDelta`. Adapt the body to read from `delta.changed_files` and `delta.elapsed`. The `repo_path` is still needed for stripping path prefixes.

- [ ] **Step 2: Add `pub mod display;` to `watch/mod.rs`**

Add to `repotoire-cli/src/cli/watch/mod.rs`:
```rust
pub mod display;
```

Remove the moved functions from `mod.rs`. Replace the call sites:
- `display_delta(...)` → `display::display_delta(...)`
- `filter_delta_relaxed(delta)` → `display::filter_delta_by_severity(delta, Severity::High)`
- `severity_icon(...)` → (only used inside display, no external callers)

- [ ] **Step 3: Verify it compiles**

Run: `nix-shell -p gnumake --run "cargo check 2>&1 | tail -3"`
Expected: `Finished`

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/watch/display.rs repotoire-cli/src/cli/watch/mod.rs
git commit -m "refactor(watch): extract display.rs with terminal output functions"
```

---

### Task 4: Extract `filter.rs` — ignore-crate file filtering

**Files:**
- Create: `repotoire-cli/src/cli/watch/filter.rs`
- Modify: `repotoire-cli/src/cli/watch/mod.rs`

(Note: `SUPPORTED_EXTENSIONS` visibility and `pub mod watch` already handled in Task 0.)

- [ ] **Step 1: Write failing tests in `filter.rs`**

Create `repotoire-cli/src/cli/watch/filter.rs`:

```rust
use std::path::{Path, PathBuf};
use std::collections::HashSet;

use notify_debouncer_full::DebouncedEvent;

use crate::cli::analyze::files::SUPPORTED_EXTENSIONS;

/// File filter for watch mode. Uses the `ignore` crate to respect
/// .gitignore and .repotoireignore patterns, matching analyze's behavior.
pub struct WatchFilter {
    repo_path: PathBuf,
    matcher: ignore::gitignore::Gitignore,
}

impl WatchFilter {
    pub fn new(repo_path: &Path) -> Self {
        todo!()
    }

    /// Check if a path should trigger re-analysis.
    pub fn should_analyze(&self, path: &Path) -> bool {
        todo!()
    }

    /// Collect and deduplicate changed source files from notify events.
    pub fn collect_changed(&self, events: &[DebouncedEvent]) -> Vec<PathBuf> {
        events
            .iter()
            .flat_map(|event| event.paths.iter())
            .filter(|p| self.should_analyze(p))
            .cloned()
            .collect::<HashSet<_>>()
            .into_iter()
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn filter_extensions() {
        let tmp = TempDir::new().unwrap();
        let filter = WatchFilter::new(tmp.path());

        // Supported extensions
        let rs = tmp.path().join("main.rs");
        fs::write(&rs, "fn main() {}").unwrap();
        assert!(filter.should_analyze(&rs));

        let py = tmp.path().join("app.py");
        fs::write(&py, "print('hi')").unwrap();
        assert!(filter.should_analyze(&py));

        let ts = tmp.path().join("index.ts");
        fs::write(&ts, "const x = 1;").unwrap();
        assert!(filter.should_analyze(&ts));

        // Unsupported extensions
        let md = tmp.path().join("README.md");
        fs::write(&md, "# hello").unwrap();
        assert!(!filter.should_analyze(&md));

        let toml = tmp.path().join("Cargo.toml");
        fs::write(&toml, "[package]").unwrap();
        assert!(!filter.should_analyze(&toml));

        let lock = tmp.path().join("Cargo.lock");
        fs::write(&lock, "[[package]]").unwrap();
        assert!(!filter.should_analyze(&lock));
    }

    #[test]
    fn filter_gitignore() {
        let tmp = TempDir::new().unwrap();
        fs::write(tmp.path().join(".gitignore"), "target/\n*.generated.rs\n").unwrap();
        let filter = WatchFilter::new(tmp.path());

        // Ignored by .gitignore
        let target_dir = tmp.path().join("target");
        fs::create_dir_all(&target_dir).unwrap();
        let target_file = target_dir.join("debug.rs");
        fs::write(&target_file, "").unwrap();
        assert!(!filter.should_analyze(&target_file));

        let generated = tmp.path().join("output.generated.rs");
        fs::write(&generated, "").unwrap();
        assert!(!filter.should_analyze(&generated));

        // Not ignored
        let src = tmp.path().join("src.rs");
        fs::write(&src, "fn main() {}").unwrap();
        assert!(filter.should_analyze(&src));
    }

    #[test]
    fn filter_repotoireignore() {
        let tmp = TempDir::new().unwrap();
        fs::write(tmp.path().join(".repotoireignore"), "vendor/\n").unwrap();
        let filter = WatchFilter::new(tmp.path());

        let vendor_dir = tmp.path().join("vendor");
        fs::create_dir_all(&vendor_dir).unwrap();
        let vendor_file = vendor_dir.join("lib.rs");
        fs::write(&vendor_file, "").unwrap();
        assert!(!filter.should_analyze(&vendor_file));

        let src = tmp.path().join("lib.rs");
        fs::write(&src, "").unwrap();
        assert!(filter.should_analyze(&src));
    }

    #[test]
    fn filter_no_ignore_files() {
        let tmp = TempDir::new().unwrap();
        // No .gitignore or .repotoireignore — everything with valid extension passes
        let filter = WatchFilter::new(tmp.path());
        let f = tmp.path().join("main.rs");
        fs::write(&f, "").unwrap();
        assert!(filter.should_analyze(&f));
    }

    #[test]
    fn filter_collect_deduplicates() {
        let tmp = TempDir::new().unwrap();
        let f = tmp.path().join("main.rs");
        fs::write(&f, "fn main() {}").unwrap();
        let filter = WatchFilter::new(tmp.path());

        // Simulate two notify events for the same file
        let event1 = DebouncedEvent {
            paths: vec![f.clone()],
            ..Default::default()
        };
        let event2 = DebouncedEvent {
            paths: vec![f.clone()],
            ..Default::default()
        };
        let changed = filter.collect_changed(&[event1, event2]);
        assert_eq!(changed.len(), 1, "duplicate events should be deduplicated");
    }
}
```

- [ ] **Step 2: Add `pub mod filter;` to `watch/mod.rs` and run tests to verify they fail**

Run: `nix-shell -p gnumake --run "cargo test watch::filter -- --nocapture 2>&1 | tail -10"`
Expected: FAIL — `todo!()` panics.

- [ ] **Step 3: Implement `WatchFilter`**

Replace the `todo!()`s in `filter.rs`:

```rust
impl WatchFilter {
    pub fn new(repo_path: &Path) -> Self {
        let mut builder = ignore::gitignore::GitignoreBuilder::new(repo_path);

        // Walk directory tree for nested .gitignore and .repotoireignore files
        for entry in ignore::WalkBuilder::new(repo_path)
            .hidden(false)
            .ignore(false)
            .git_ignore(false)
            .build()
            .flatten()
        {
            let path = entry.path();
            if path.file_name() == Some(".gitignore".as_ref())
                || path.file_name() == Some(".repotoireignore".as_ref())
            {
                let _ = builder.add(path);
            }
        }

        let matcher = builder.build().unwrap_or_else(|_| {
            ignore::gitignore::GitignoreBuilder::new(repo_path)
                .build()
                .unwrap()
        });

        Self {
            repo_path: repo_path.to_path_buf(),
            matcher,
        }
    }

    pub fn should_analyze(&self, path: &Path) -> bool {
        let has_ext = path
            .extension()
            .and_then(|e| e.to_str())
            .is_some_and(|ext| SUPPORTED_EXTENSIONS.contains(&ext));
        if !has_ext {
            return false;
        }

        let rel = path.strip_prefix(&self.repo_path).unwrap_or(path);
        !self.matcher.matched(rel, path.is_dir()).is_ignore() && path.is_file()
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell -p gnumake --run "cargo test watch::filter -- --nocapture 2>&1 | tail -10"`
Expected: 5 tests PASS.

- [ ] **Step 5: Remove old `is_ignored_path`, `WATCH_EXTENSIONS` from `mod.rs`**

In `repotoire-cli/src/cli/watch/mod.rs`:
- Delete `WATCH_EXTENSIONS` constant (lines 19-22).
- Delete `is_ignored_path()` function (lines 394-407).
- Replace inline file filtering logic (lines 169-182) with `filter.collect_changed(&events)` — this requires `WatchFilter` to be constructed in `run()` and the events passed to it. Wire this up.

- [ ] **Step 6: Verify it compiles**

Run: `nix-shell -p gnumake --run "cargo check 2>&1 | tail -3"`
Expected: `Finished`

- [ ] **Step 7: Commit**

```bash
git add repotoire-cli/src/cli/watch/filter.rs repotoire-cli/src/cli/watch/mod.rs
git commit -m "refactor(watch): extract filter.rs with WatchFilter using ignore crate"
```

---

### Task 5: Extract `engine.rs` — WatchEngine with error recovery

**Files:**
- Create: `repotoire-cli/src/cli/watch/engine.rs`
- Modify: `repotoire-cli/src/cli/watch/mod.rs`

- [ ] **Step 1: Create `engine.rs` with `WatchEngine` and `WatchReanalysis`**

```rust
use std::path::{Path, PathBuf};

use anyhow::Result;

use super::delta::{compute_delta, WatchDelta};
use crate::engine::{AnalysisConfig, AnalysisEngine, AnalysisResult};

/// Result of a re-analysis attempt.
pub enum WatchReanalysis {
    /// Analysis succeeded, here's what changed.
    Delta(WatchDelta),
    /// Analysis failed (e.g., syntax error). Message included. Keep watching.
    Error(String),
    /// No meaningful change in findings.
    Unchanged,
}

pub struct WatchEngine {
    engine: AnalysisEngine,
    config: AnalysisConfig,
    last_result: Option<AnalysisResult>,
    iteration: u32,
    session_dir: PathBuf,
}

impl WatchEngine {
    pub fn new(repo_path: &Path, config: AnalysisConfig) -> Result<Self> {
        let engine = AnalysisEngine::new(repo_path)?;
        let session_dir = crate::cache::cache_dir(repo_path).join("session");
        Ok(Self {
            engine,
            config,
            last_result: None,
            iteration: 0,
            session_dir,
        })
    }

    /// Run initial cold analysis. Called once on startup.
    pub fn initial_analyze(&mut self) -> Result<AnalysisResult> {
        let result = self.engine.analyze(&self.config)?;
        let _ = self.engine.save(&self.session_dir);
        self.last_result = Some(result.clone());
        Ok(result)
    }

    /// Re-analyze after file changes. Never propagates errors —
    /// analysis failures return WatchReanalysis::Error.
    pub fn reanalyze(&mut self, changed_files: &[PathBuf]) -> WatchReanalysis {
        let start = std::time::Instant::now();

        crate::parsers::clear_structural_fingerprint_cache();

        match self.engine.analyze(&self.config) {
            Ok(result) => {
                let delta = compute_delta(
                    &result,
                    self.last_result.as_ref(),
                    changed_files.to_vec(),
                    start.elapsed(),
                );
                self.last_result = Some(result);
                self.iteration += 1;

                if self.iteration % 10 == 0 {
                    let _ = self.save();
                }

                if delta.new_findings.is_empty() && delta.fixed_findings.is_empty() {
                    WatchReanalysis::Unchanged
                } else {
                    WatchReanalysis::Delta(delta)
                }
            }
            Err(e) => WatchReanalysis::Error(format!("{:#}", e)),
        }
    }

    /// Access the latest result (for telemetry, score tracking).
    pub fn last_result(&self) -> Option<&AnalysisResult> {
        self.last_result.as_ref()
    }

    /// Persist engine state to disk.
    pub fn save(&self) -> Result<()> {
        self.engine.save(&self.session_dir)?;
        Ok(())
    }
}
```

- [ ] **Step 2: Add `pub mod engine;` to `watch/mod.rs`**

- [ ] **Step 3: Verify it compiles**

Run: `nix-shell -p gnumake --run "cargo check 2>&1 | tail -3"`
Expected: `Finished`

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/watch/engine.rs repotoire-cli/src/cli/watch/mod.rs
git commit -m "refactor(watch): extract engine.rs with WatchEngine and error recovery"
```

---

### Task 6: Refactor `mod.rs` to thin glue

**Files:**
- Modify: `repotoire-cli/src/cli/watch/mod.rs`

- [ ] **Step 1: Rewrite `run()` to use `WatchEngine`, `WatchFilter`, and `display` functions**

Replace the entire `run()` body. The new version should be ~70 lines:

```rust
pub mod delta;
pub mod display;
pub mod engine;
pub mod filter;

use anyhow::Result;
use console::style;
use notify::RecursiveMode;
use notify_debouncer_full::{new_debouncer, DebounceEventResult};
use std::path::Path;
use std::sync::mpsc;
use std::time::Duration;

use crate::engine::AnalysisConfig;
use crate::models::Severity;

use self::display::{display_delta, display_error, display_initial, filter_delta_by_severity};
use self::engine::{WatchEngine, WatchReanalysis};
use self::filter::WatchFilter;

pub fn run(
    path: &Path,
    severity: Option<&str>,
    all_detectors: bool,
    workers: usize,
    no_emoji: bool,
    quiet: bool,
    telemetry: &crate::telemetry::Telemetry,
) -> Result<()> {
    let repo_path = std::fs::canonicalize(path)?;
    let session_start = std::time::Instant::now();

    if !quiet {
        let icon = if no_emoji { "" } else { "👁️  " };
        println!(
            "\n{}Watching {} for changes...\n",
            style(icon).bold(),
            style(repo_path.display()).cyan()
        );
        println!("  {} Save a file to trigger analysis", style("→").dim());
        println!("  {} Press Ctrl+C to stop\n", style("→").dim());
    }

    let config = AnalysisConfig {
        workers,
        all_detectors,
        no_git: !repo_path.join(".git").exists(),
        ..Default::default()
    };

    // Initial analysis
    if !quiet {
        println!("  {} Running initial analysis...", style("⏳").dim());
    }
    let start = std::time::Instant::now();
    let mut engine = WatchEngine::new(&repo_path, config)?;
    let initial_result = engine.initial_analyze()?;
    display_initial(&initial_result, start.elapsed(), no_emoji, quiet);

    // File watcher
    let filter = WatchFilter::new(&repo_path);
    let (tx, rx) = mpsc::channel();
    let mut debouncer = new_debouncer(
        Duration::from_millis(500),
        None,
        move |result: DebounceEventResult| {
            if let Ok(events) = result {
                let _ = tx.send(events);
            }
        },
    )?;
    debouncer.watch(&repo_path, RecursiveMode::Recursive)?;

    let mut files_changed_total = 0u64;
    let mut reanalysis_count = 0u64;
    let score_start = initial_result.score.overall;

    // Event loop
    while let Ok(events) = rx.recv() {
        let changed = filter.collect_changed(&events);
        if changed.is_empty() {
            continue;
        }

        files_changed_total += changed.len() as u64;
        reanalysis_count += 1;

        match engine.reanalyze(&changed) {
            WatchReanalysis::Delta(delta) => {
                let delta = if let Some(sev) = severity {
                    filter_delta_by_severity(delta, parse_severity(sev))
                } else {
                    delta
                };
                display_delta(&delta, &repo_path, no_emoji, quiet);
            }
            WatchReanalysis::Error(msg) => {
                display_error(&msg, &changed, &repo_path, no_emoji);
            }
            WatchReanalysis::Unchanged => {
                if !quiet {
                    let last = engine.last_result();
                    display::display_unchanged(
                        &changed,
                        &repo_path,
                        last.map(|r| r.findings.len()).unwrap_or(0),
                        last.map(|r| r.score.overall),
                        no_emoji,
                    );
                }
            }
        }
    }

    // Exit summary (preserve existing behavior)
    println!(
        "\n{} Watch session: {} re-analyses, {} files changed.",
        if no_emoji { "" } else { "📊" },
        reanalysis_count,
        files_changed_total,
    );

    // Cleanup
    let _ = engine.save();

    // Telemetry
    let score_end = engine.last_result().map(|r| r.score.overall).unwrap_or(0.0);
    if let crate::telemetry::Telemetry::Active(ref state) = *telemetry {
        if let Some(distinct_id) = &state.distinct_id {
            let repo_id = crate::telemetry::config::compute_repo_id(&repo_path);
            let event = crate::telemetry::events::WatchSession {
                repo_id,
                duration_s: session_start.elapsed().as_secs(),
                reanalysis_count,
                files_changed_total,
                score_start,
                score_end,
                version: env!("CARGO_PKG_VERSION").to_string(),
            };
            let props = serde_json::to_value(&event).unwrap_or_default();
            crate::telemetry::posthog::capture_queued("watch_session", distinct_id, props);
        }
    }

    Ok(())
}

fn parse_severity(s: &str) -> Severity {
    match s {
        "critical" => Severity::Critical,
        "high" => Severity::High,
        "medium" => Severity::Medium,
        "low" => Severity::Low,
        _ => Severity::Low,
    }
}
```

Note: `display_unchanged` is a new helper in `display.rs` for the compact one-liner when nothing changed. Move the existing compact display logic (lines 294-308 of old `mod.rs`) into this function.

- [ ] **Step 2: Verify it compiles**

Run: `nix-shell -p gnumake --run "cargo check 2>&1 | tail -3"`
Expected: `Finished`

- [ ] **Step 3: Verify all existing tests pass**

Run: `nix-shell -p gnumake --run "cargo test 2>&1 | tail -5"`
Expected: All tests pass, no regressions.

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/watch/
git commit -m "refactor(watch): rewrite mod.rs as thin glue wiring engine → display"
```

---

### Task 7: Update CLI command definition

**Files:**
- Modify: `repotoire-cli/src/cli/mod.rs` (lines 335-343 and 647)

- [ ] **Step 1: Update `Watch` variant in `Commands` enum**

In `repotoire-cli/src/cli/mod.rs`, replace lines 335-343:

```rust
    /// Watch for file changes and re-analyze in real-time (debounced, incremental)
    ///
    /// Monitors your codebase for saves and runs detectors on changed files.
    /// Uses debouncing to avoid re-running on every keystroke.
    Watch {
        /// Minimum severity to display: critical, high, medium, low
        #[arg(long, value_parser = ["critical", "high", "medium", "low"])]
        severity: Option<String>,

        /// Run all detectors including deep-scan (code smells, style, dead code)
        #[arg(long)]
        all_detectors: bool,
    },
```

- [ ] **Step 2: Update dispatch in `run_cli()`**

Replace line 647:
```rust
Some(Commands::Watch { relaxed }) => watch::run(&cli.path, relaxed, false, false, &telemetry),
```
with:
```rust
Some(Commands::Watch { severity, all_detectors }) => {
    watch::run(&cli.path, severity.as_deref(), all_detectors, cli.workers, false, false, &telemetry)
}
```

Note: `no_emoji` and `quiet` are hardcoded to `false` — these could be wired from global flags in a future PR but are out of scope for this spec.

- [ ] **Step 3: Update telemetry command name extraction**

Check if there's a match arm for `Watch` in the telemetry command extraction (around line 460). Update it to match the new destructured fields:
```rust
Some(Commands::Watch { .. }) => ("watch".into(), None),
```

- [ ] **Step 4: Verify it compiles**

Run: `nix-shell -p gnumake --run "cargo check 2>&1 | tail -3"`
Expected: `Finished`

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/cli/mod.rs
git commit -m "feat(watch): add --severity, --all-detectors flags; remove --relaxed; plumb global --workers"
```

---

### Task 8: Integration test for `WatchEngine`

**Files:**
- Create: `repotoire-cli/tests/watch_engine_test.rs`

- [ ] **Step 1: Write integration tests**

```rust
//! Integration tests for WatchEngine — the reusable core of watch mode.

use std::fs;
use std::path::PathBuf;
use tempfile::TempDir;

use repotoire::cli::watch::engine::{WatchEngine, WatchReanalysis};
use repotoire::engine::AnalysisConfig;

fn setup_repo() -> TempDir {
    let tmp = TempDir::new().unwrap();
    // Create a minimal repo structure
    fs::write(
        tmp.path().join("main.py"),
        r#"
import os
def hello():
    print("hello")
"#,
    )
    .unwrap();
    tmp
}

fn default_config() -> AnalysisConfig {
    AnalysisConfig {
        workers: 2,
        no_git: true,
        ..Default::default()
    }
}

#[test]
fn engine_initial_analyze() {
    let tmp = setup_repo();
    let mut engine = WatchEngine::new(tmp.path(), default_config()).unwrap();
    let result = engine.initial_analyze().unwrap();
    // Should complete without error and produce a score
    assert!(result.score.overall > 0.0);
    assert!(engine.last_result().is_some());
}

#[test]
fn engine_reanalyze_unchanged() {
    let tmp = setup_repo();
    let mut engine = WatchEngine::new(tmp.path(), default_config()).unwrap();
    let _ = engine.initial_analyze().unwrap();

    // Reanalyze without changing files → Unchanged
    let result = engine.reanalyze(&[]);
    assert!(matches!(result, WatchReanalysis::Unchanged));
}

#[test]
fn engine_reanalyze_new_finding() {
    let tmp = setup_repo();
    let mut engine = WatchEngine::new(tmp.path(), default_config()).unwrap();
    let _ = engine.initial_analyze().unwrap();

    // Add a file with a known issue (hardcoded secret)
    let bad_file = tmp.path().join("config.py");
    fs::write(
        &bad_file,
        r#"
AWS_SECRET_KEY = "AKIAIOSFODNN7EXAMPLE"
DATABASE_PASSWORD = "super_secret_password_123"
"#,
    )
    .unwrap();

    let result = engine.reanalyze(&[bad_file]);
    // Should find at least one new finding (secrets detector)
    match result {
        WatchReanalysis::Delta(delta) => {
            assert!(
                !delta.new_findings.is_empty(),
                "Expected new findings from hardcoded secret, got none"
            );
        }
        WatchReanalysis::Unchanged => {
            // Acceptable if the detector didn't fire — the engine still worked
        }
        WatchReanalysis::Error(msg) => {
            panic!("Expected Delta or Unchanged, got Error: {}", msg);
        }
    }
}

#[test]
fn engine_error_recovery() {
    let tmp = setup_repo();
    let mut engine = WatchEngine::new(tmp.path(), default_config()).unwrap();
    let initial = engine.initial_analyze().unwrap();
    let initial_score = initial.score.overall;

    // Write a file — even if analysis fails on parse, the engine should not panic
    let bad_file = tmp.path().join("broken.py");
    fs::write(&bad_file, "def broken(\n    # intentionally unclosed").unwrap();

    let result = engine.reanalyze(&[bad_file.clone()]);
    // The engine should either return a result (tree-sitter is error-tolerant)
    // or return an Error — but NOT panic
    match result {
        WatchReanalysis::Delta(_) | WatchReanalysis::Unchanged => {
            // tree-sitter handles syntax errors gracefully, so this is expected
        }
        WatchReanalysis::Error(_) => {
            // Also acceptable — error was caught
            // Previous result should still be valid
            assert!(engine.last_result().is_some());
            let last_score = engine.last_result().unwrap().score.overall;
            assert_eq!(last_score, initial_score, "Score should not change on error");
        }
    }
}
```

Note: These tests depend on `WatchEngine` being publicly accessible. Ensure `pub mod watch;` is in `cli/mod.rs` and the engine module types are `pub`. If `repotoire` doesn't re-export `cli::watch::engine`, adjust the import path to use `repotoire_cli` or the crate's lib.rs re-exports. Check how existing integration tests import crate types and follow the same pattern.

- [ ] **Step 2: Run the integration tests**

Run: `nix-shell -p gnumake --run "cargo test watch_engine_test -- --nocapture 2>&1 | tail -15"`
Expected: 4 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/tests/watch_engine_test.rs
git commit -m "test(watch): add integration tests for WatchEngine"
```

---

### Task 9: Final verification

- [ ] **Step 1: Run the full test suite**

Run: `nix-shell -p gnumake --run "cargo test 2>&1 | tail -10"`
Expected: All 1,621+ tests pass, no regressions.

- [ ] **Step 2: Verify watch mode runs manually**

Run: `nix-shell -p gnumake --run "cargo run -- watch . --severity high --all-detectors"`
Expected: Watch starts, shows initial analysis, waits for file changes. Ctrl+C to exit.

- [ ] **Step 3: Verify --relaxed is gone**

Run: `nix-shell -p gnumake --run "cargo run -- watch . --relaxed 2>&1"`
Expected: Error about unknown flag `--relaxed`.

- [ ] **Step 4: Commit any final fixes**

```bash
git add -A
git commit -m "chore(watch): final verification and cleanup"
```
