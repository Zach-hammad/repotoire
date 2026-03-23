# Watch Mode Hardening Design

*2026-03-21*

## Problem

Watch mode works but has architectural issues that block building an LSP server on top of it:

1. **Untestable** — all logic is in one 424-line function. Delta computation, file filtering, display, error handling, and telemetry are tangled together.
2. **Crashes on errors** — `engine.analyze(&config)?` propagates any error and kills the watch session. A syntax error in one file shouldn't stop watching.
3. **Divergent file filtering** — watch uses a hardcoded `is_ignored_path()` function instead of the `ignore` crate with `.repotoireignore` support that analyze uses. Watch and analyze can disagree on what gets analyzed.
4. **Display coupling** — terminal output is embedded in the event loop. The core "file changed → re-analyze → compute delta" logic can't be reused by an LSP server or any non-terminal consumer.
5. **Zero test coverage** — no unit tests, no integration tests.
6. **Missing flag parity** — watch doesn't support `--all-detectors`, `--severity`, or `--workers`. Users can't customize watch the way they customize analyze.

## Goal

Rearchitect watch into a reusable `WatchEngine` that can serve both the CLI and a future LSP server. Fix all robustness issues. Add tests.

## Non-Goals

- LSP implementation (separate spec)
- New UI features (TUI watch mode, dashboard, etc.)
- Watch mode for non-source files (configs, markdown, TOML)
- Changing the debounce strategy or notify crate usage (the file watching I/O layer works fine)

---

## Architecture

### Current

One file (`cli/watch.rs`, 424 lines), one function (`run()`), everything coupled:

```
run() does EVERYTHING:
  CLI setup → engine init → file watcher setup → event loop →
  file filtering (hardcoded) → delta computation → display (stdout) → telemetry
```

### New

`cli/watch/` module with 5 files, clear boundaries between I/O and pure logic:

```
cli/watch/
├── mod.rs        — run(): CLI entry point, wires watcher → engine → display
├── engine.rs     — WatchEngine struct: reusable analysis + error recovery
├── delta.rs      — WatchDelta, compute_delta(): pure logic, fully testable
├── display.rs    — Terminal output: display_delta(), severity_icon(), etc.
└── filter.rs     — WatchFilter: file extension + ignore crate filtering
```

### Key Abstraction: WatchEngine

The central change is extracting a `WatchEngine` struct that holds the analysis engine, tracks state across iterations, and handles errors gracefully. This struct is reusable by any consumer — CLI watch, LSP server, or tests.

```rust
pub struct WatchEngine {
    engine: AnalysisEngine,
    config: AnalysisConfig,
    last_result: Option<AnalysisResult>,
    iteration: u32,
    session_dir: PathBuf,
}

pub enum WatchReanalysis {
    /// Analysis succeeded, here's what changed.
    Delta(WatchDelta),
    /// Analysis failed (e.g., syntax error). Message included. Keep watching.
    Error(String),
    /// No meaningful change in results.
    Unchanged,
}

impl WatchEngine {
    /// Create a new watch engine for the given repo.
    pub fn new(repo_path: &Path, config: AnalysisConfig) -> Result<Self>;

    /// Run initial cold analysis. Called once on startup.
    pub fn initial_analyze(&mut self) -> Result<AnalysisResult>;

    /// Re-analyze after file changes. Never panics or propagates errors —
    /// analysis failures return WatchReanalysis::Error so the caller can
    /// display the error and keep watching.
    pub fn reanalyze(&mut self, changed_files: &[PathBuf]) -> WatchReanalysis;

    /// Access the latest result (for telemetry, score tracking, etc.)
    pub fn last_result(&self) -> Option<&AnalysisResult>;

    /// Persist engine state to disk. Called periodically and on shutdown.
    pub fn save(&self) -> Result<()>;
}
```

`reanalyze()` takes the changed files list from the notify watcher. This makes testing straightforward — create a `WatchEngine`, write a file, call `reanalyze(&[path])`, assert on the returned delta. No need for a real filesystem watcher in tests.

Error recovery is built into `reanalyze()`: if `engine.analyze()` fails, the method returns `WatchReanalysis::Error(message)` instead of propagating. The previous `last_result` stays valid. When the user fixes the error and saves again, the next `reanalyze()` succeeds normally.

### WatchFilter

Replaces the hardcoded `is_ignored_path()` with the `ignore` crate, matching how analyze filters files.

```rust
pub struct WatchFilter {
    repo_path: PathBuf,
    // Built from .gitignore + .repotoireignore + hardcoded exclusions
    // Plus WATCH_EXTENSIONS allowlist
}

impl WatchFilter {
    /// Build a filter for the given repo. Reads .gitignore and .repotoireignore.
    pub fn new(repo_path: &Path) -> Self;

    /// Check if a path should trigger re-analysis.
    pub fn should_analyze(&self, path: &Path) -> bool;

    /// Collect and deduplicate changed source files from notify events.
    pub fn collect_changed(&self, events: &[notify_debouncer_full::DebouncedEvent]) -> Vec<PathBuf>;
}
```

`should_analyze()` checks:
1. File has a supported extension (shared `SUPPORTED_EXTENSIONS` constant — same list analyze uses, not a separate `WATCH_EXTENSIONS`)
2. File is not ignored by `.gitignore` or `.repotoireignore`. Uses `ignore::gitignore::GitignoreBuilder` with recursive directory walking to handle nested `.gitignore` files in subdirectories — matching the behavior of analyze's `WalkBuilder`. Not just root-level ignore files.
3. File exists (skip delete events for files that are gone)

### WatchDelta

Extracted from the current `compute_delta()` function. Pure data + pure function, no I/O.

```rust
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
) -> WatchDelta;
```

Finding fingerprint for matching across runs: `(detector, file, line_start)` — unchanged from current implementation.

### Display

All terminal formatting extracted from the current code. Same visual output as today.

```rust
/// Display the result of an incremental re-analysis.
pub fn display_delta(delta: &WatchDelta, repo_path: &Path, no_emoji: bool, quiet: bool);

/// Display an analysis error inline (red, with timestamp).
pub fn display_error(message: &str, changed_files: &[PathBuf], repo_path: &Path, no_emoji: bool);

/// Display the initial analysis result on startup.
pub fn display_initial(result: &AnalysisResult, elapsed: Duration, no_emoji: bool, quiet: bool);

/// Severity filtering for display (when --severity is set).
pub fn filter_delta_by_severity(delta: WatchDelta, min_severity: Severity) -> WatchDelta;
```

### CLI run() — Thin Glue

After the rearchitecture, `run()` in `mod.rs` becomes ~60 lines of wiring:

```rust
pub fn run(path, severity, all_detectors, workers, no_emoji, quiet, telemetry) -> Result<()> {
    let repo_path = std::fs::canonicalize(path)?;
    let filter = WatchFilter::new(&repo_path);

    let config = AnalysisConfig {
        workers,
        all_detectors,
        no_git: !repo_path.join(".git").exists(),
        ..Default::default()
    };

    let mut engine = WatchEngine::new(&repo_path, config)?;
    let initial = engine.initial_analyze()?;
    display_initial(&initial, ...);

    // Set up notify debouncer — unchanged from current implementation
    let (tx, rx) = mpsc::channel();
    let mut debouncer = new_debouncer(Duration::from_millis(500), None, move |result| { ... })?;
    debouncer.watch(&repo_path, RecursiveMode::Recursive)?;

    // Event loop
    while let Ok(events) = rx.recv() {
        let changed = filter.collect_changed(&events);
        if changed.is_empty() { continue; }

        crate::parsers::clear_structural_fingerprint_cache();

        match engine.reanalyze(&changed) {
            WatchReanalysis::Delta(delta) => {
                let delta = if let Some(sev) = &severity {
                    filter_delta_by_severity(delta, parse_severity(sev))
                } else {
                    delta
                };
                display_delta(&delta, &repo_path, no_emoji, quiet);
            }
            WatchReanalysis::Error(msg) => {
                display_error(&msg, &changed, &repo_path, no_emoji);
            }
            WatchReanalysis::Unchanged => {}
        }
    }

    engine.save()?;
    send_telemetry(&engine, session_start, telemetry);
    Ok(())
}
```

---

## CLI Changes

### New Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--severity` | `Option<String>` | None | Minimum severity to display: critical, high, medium, low |
| `--all-detectors` | `bool` | false | Run all 106 detectors including deep-scan |

Note: `--workers` is already a global CLI flag on the `Cli` struct (default 8, range 1-64). Watch currently ignores it and hardcodes `workers: 8`. The fix is to plumb the global `cli.workers` value through to watch's `AnalysisConfig`, not to add a duplicate flag.

### Removed Flags

| Flag | Reason |
|------|--------|
| `--relaxed` | Deprecated since March 2026. Replaced by `--severity high`. |

### Updated Command Definition

```rust
/// Watch for file changes and re-analyze
Watch {
    /// Minimum severity to display: critical, high, medium, low
    #[arg(long, value_parser = ["critical", "high", "medium", "low"])]
    severity: Option<String>,

    /// Run all detectors including deep-scan (code smells, style, dead code)
    #[arg(long)]
    all_detectors: bool,
},
```

The dispatch in `run()` passes the global `cli.workers` to `WatchEngine`'s config.

---

## Error Recovery

### Current Behavior

```rust
let result = engine.analyze(&config)?;  // ? kills the watch session
```

A single file with a syntax error, an I/O error reading a file, or any analysis engine failure terminates the entire watch session.

### New Behavior

`WatchEngine::reanalyze()` catches all errors from `engine.analyze()`:

```rust
pub fn reanalyze(&mut self, changed_files: &[PathBuf]) -> WatchReanalysis {
    let start = std::time::Instant::now();

    match self.engine.analyze(&self.config) {
        Ok(result) => {
            let delta = compute_delta(&result, self.last_result.as_ref(), changed_files.to_vec(), start.elapsed());
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
        Err(e) => {
            // Don't update last_result — previous state stays valid
            WatchReanalysis::Error(format!("{:#}", e))
        }
    }
}
```

The `Unchanged` variant replaces the current "compact summary line" — the display layer decides whether to show a quiet one-liner or nothing.

### Error Display

```
[14:32:05] Analysis error: src/lib.rs:42 — expected `;`
           Watching for next change...
```

Red-colored, with timestamp. No stack trace. The user fixes the file, saves, and the next analysis runs clean.

---

## File Filtering

### Current (Broken)

```rust
fn is_ignored_path(path: &Path, repo_path: &Path) -> bool {
    // Hardcoded: target/, node_modules/, .git/, .repotoire/, __pycache__, .next/, dist/, build/
    // Also skips any path starting with '.'
}
```

This misses:
- `.repotoireignore` patterns
- `.gitignore` patterns beyond the hardcoded list
- Project config exclusions from `repotoire.toml`

### New (Consistent)

`WatchFilter::new()` walks the repo directory tree to collect all `.gitignore` and `.repotoireignore` files (including nested ones in subdirectories), matching the behavior of analyze's `WalkBuilder`. Uses the shared `SUPPORTED_EXTENSIONS` constant from `cli::analyze::files` instead of a separate watch-specific list.

```rust
use crate::cli::analyze::files::SUPPORTED_EXTENSIONS;

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
            ignore::gitignore::GitignoreBuilder::new(repo_path).build().unwrap()
        });

        Self { repo_path: repo_path.to_path_buf(), matcher }
    }

    pub fn should_analyze(&self, path: &Path) -> bool {
        // Check extension against shared list
        let has_ext = path.extension()
            .and_then(|e| e.to_str())
            .is_some_and(|ext| SUPPORTED_EXTENSIONS.contains(&ext));
        if !has_ext { return false; }

        // Check ignore patterns (nested .gitignore + .repotoireignore)
        let rel = path.strip_prefix(&self.repo_path).unwrap_or(path);
        !self.matcher.matched(rel, path.is_dir()).is_ignore()
            && path.is_file()
    }
}
```

---

## Tests

### Unit Tests: `delta.rs`

| Test | Description |
|------|-------------|
| `delta_no_previous` | First analysis — no previous result, delta has zero new/fixed, score_delta is None |
| `delta_new_findings` | Finding added in new result appears in `new_findings` |
| `delta_fixed_findings` | Finding absent from new result appears in `fixed_findings` |
| `delta_both_new_and_fixed` | Simultaneous adds and fixes |
| `delta_score_tracking` | Score delta computed correctly, None when no previous |
| `delta_same_results` | Identical results → empty new_findings and fixed_findings |
| `delta_fingerprint_stability` | Same finding with different title but same (detector, file, line) is not "new" |

### Unit Tests: `filter.rs`

| Test | Description |
|------|-------------|
| `filter_extensions` | `.rs`, `.py`, `.ts` pass; `.md`, `.toml`, `.lock` rejected |
| `filter_gitignore` | Paths matching `.gitignore` patterns are rejected |
| `filter_repotoireignore` | Paths matching `.repotoireignore` patterns are rejected |
| `filter_no_ignore_files` | Works when no `.gitignore` or `.repotoireignore` exist |
| `filter_collect_deduplicates` | Multiple events for the same file produce one entry |

### Integration Tests: `engine.rs`

| Test | Description |
|------|-------------|
| `engine_initial_analyze` | Create engine on temp repo with known bad code, initial analysis returns findings |
| `engine_reanalyze_new_finding` | Add a file with a known issue, `reanalyze()` returns delta with new finding |
| `engine_reanalyze_fixed` | Fix a known issue, `reanalyze()` returns delta with fixed finding |
| `engine_error_recovery` | Write invalid syntax, `reanalyze()` returns `Error`, write valid syntax, next `reanalyze()` returns `Delta` |
| `engine_unchanged` | Save without modifying source, `reanalyze()` returns `Unchanged` |

---

## Implementation Order

1. Create `cli/watch/` directory, move current `watch.rs` → `cli/watch/mod.rs` (no behavior change)
2. Extract `delta.rs` — move `WatchDelta`, `compute_delta()`. Update signature to accept `changed_files` and `elapsed`.
3. Extract `display.rs` — move `display_delta()`, `severity_icon()`, `score_suffix()`, `is_ai_detector()`. Add `display_error()` and `display_initial()`.
4. Extract `filter.rs` — create `WatchFilter` using `ignore` crate. Replace `is_ignored_path()` and inline extension check.
5. Extract `engine.rs` — create `WatchEngine` with `initial_analyze()` and `reanalyze()`. Implement error recovery.
6. Refactor `mod.rs` `run()` to thin glue wiring watcher → engine → display.
7. Update watch command definition: add `--severity`, `--all-detectors`. Plumb global `--workers` to watch config. Remove `--relaxed`.
8. Add unit tests for `delta.rs` and `filter.rs`.
9. Add integration tests for `engine.rs`.
10. Verify full test suite passes (`cargo test`).

Steps 1-6 are pure refactoring — no behavior change, existing tests must still pass after each step. Steps 7-9 add new behavior and coverage.

---

## Success Criteria

- Watch mode survives syntax errors without crashing
- Watch respects `.repotoireignore` patterns (same filtering as analyze)
- `--all-detectors`, `--severity`, `--workers` flags work in watch
- `WatchEngine` is usable from non-CLI code (future LSP)
- All new tests pass, existing test suite unaffected
- No user-visible output regression (same display format as before)
