# Smart Diff Polish: 6 Improvements

Follow-up to the Smart Diff v1 (2026-03-31). Addresses gaps found during review.

---

## 1. Integration Tests

**Problem:** Zero integration tests for `repotoire diff`. All tests are unit-level (parser, formatter). No test exercises the actual binary end-to-end.

**Design:**

Add to `tests/integration_test.rs`:

- `test_diff_text_output` — create a temp git repo with fixture files, run `analyze`, modify a file, run `analyze` again, then run `diff HEAD~1`. Assert: exit 0, output contains "Repotoire Diff", output contains file that was changed.
- `test_diff_json_output` — same setup, `--format json`. Assert: valid JSON, has `new_findings` array with `attribution` field, has `score_before`/`score_after`.
- `test_diff_sarif_output` — same setup, `--format sarif`. Assert: valid JSON, has SARIF `$schema`, `runs` array.
- `test_diff_all_flag` — compare finding counts: `--all` >= default (hunk-only).
- `test_diff_fail_on` — inject a known finding, assert exit code 1 with `--fail-on medium`.
- `test_diff_no_baseline` — run `diff` without prior `analyze`. Assert: graceful error message (or auto-analyze, see item #2).

**Test helper:**

```rust
/// Create a minimal git repo in a temp directory with fixture files.
fn create_git_test_repo() -> TempDir {
    let temp = tempfile::tempdir().unwrap();
    // git init, copy fixtures, git add, git commit
    // Returns a repo ready for diff testing
}

/// Run repotoire diff on a path and return (stdout, stderr, exit_code).
fn run_diff(path: &Path, args: &[&str]) -> (String, String, i32) {
    // Similar to existing run_analyze helper
}
```

**Files:** `tests/integration_test.rs`

---

## 2. Live Analysis (Auto-Analyze)

**Problem:** The spec says `repotoire diff main` should work out-of-the-box by analyzing HEAD on-the-fly. Currently it bails with "No baseline found. Run 'repotoire analyze' first."

**Design:**

In `diff::run()`, after `load_baseline_and_head()` fails to find HEAD findings, automatically run an analysis:

```rust
// In run(), replace the hard bail on missing head findings:
let (baseline, head, score_before, score_after) =
    match load_baseline_and_head(&repotoire_dir, &repo_path, base_ref.as_deref()) {
        Ok(result) => result,
        Err(_) => {
            // No cache — run a quick analysis to generate findings
            eprintln!("No cached analysis found, running analysis...");
            run_inline_analysis(&repo_path, &repotoire_dir)?;
            // Retry load after analysis
            load_baseline_and_head(&repotoire_dir, &repo_path, base_ref.as_deref())
                .context("Analysis completed but findings could not be loaded")?
        }
    };
```

`run_inline_analysis()` creates an `AnalysisEngine`, runs `analyze()` with default config, and calls `cache_results()` to save findings to `last_findings.json`.

**Baseline behavior when no baseline exists:** Use empty baseline (all findings are "new"). This is correct for first-run — user sees everything their code currently has.

**Edge cases:**
- If analysis itself fails (e.g., empty repo), propagate the error.
- Show a brief message so the user knows why it takes longer: `"No cached analysis found, running analysis..."` on stderr.
- Don't run analysis if `last_findings.json` exists but `baseline_findings.json` doesn't — that means the user has run analyze once but not twice. Use empty baseline.

**Files:** `src/cli/diff.rs`

---

## 3. Markdown Formatter

**Problem:** Markdown output falls through to `format_text()`, which uses ANSI color codes (via `console::style`). Markdown gets garbled terminal escapes.

**Design:**

Add `format_markdown()` that produces clean markdown with attribution sections:

```markdown
# Repotoire Diff: main..HEAD (3 files changed)

## Your Changes (2 findings)

| Severity | Finding | Location |
|----------|---------|----------|
| Critical | SQL injection | src/api.rs:42 |
| Critical | Command injection | src/utils.rs:15 |

## Pre-existing (1 in changed files)

| Severity | Finding | Location |
|----------|---------|----------|
| High | XSS | src/api.rs:180 |

**Score:** 85.2 → 78.4 (-6.8)

✨ 3 findings fixed
```

Wire into `emit_output()`:

```rust
OutputFormat::Markdown => format_markdown(result),
```

**Files:** `src/cli/diff.rs`

---

## 4. `--help` Examples Update

**Problem:** The `after_help` text for the Diff command doesn't mention `--all` or `--changed`.

**Design:**

Update the `after_help` in `src/cli/mod.rs` Diff variant:

```
Examples:
  repotoire diff                         Diff latest vs previous analysis
  repotoire diff main                    Diff against main branch
  repotoire diff --all                   Show ALL new findings (not just your changes)
  repotoire diff --changed              Show findings in changed files only
  repotoire diff --format json           JSON output for CI
  repotoire diff --fail-on high          Exit 1 if new high+ findings in your hunks
  repotoire diff --format sarif          SARIF with only hunk-level findings
```

**Files:** `src/cli/mod.rs`

---

## 5. Filtered Count Hint

**Problem:** When default mode shows 0 hunk findings but there ARE findings in other scopes, the user sees "No new findings" with no idea that `--all` would show more.

**Design:**

In `format_text()`, when `hunk_findings` is empty but `result.all_new_count > 0`:

```rust
if result.new_findings.is_empty() && result.all_new_count > 0 {
    let check = if no_emoji { "[ok]" } else { "✅" };
    out.push_str(&format!(
        "  {} {}\n",
        check,
        style("No new findings in your changes").green()
    ));
    out.push_str(&format!(
        "  {} {} finding{} in other files (use --all to see)\n\n",
        style("ℹ").dim(),
        result.all_new_count,
        if result.all_new_count == 1 { "" } else { "s" }
    ));
} else if result.new_findings.is_empty() {
    // Truly zero findings everywhere
    let check = if no_emoji { "[ok]" } else { "✅" };
    out.push_str(&format!(
        "  {} {}\n\n",
        check,
        style("No new findings").green()
    ));
}
```

Same hint for `format_markdown()` and `format_json()` (add `all_new_count` to JSON output — it's already there via `SmartDiffResult`; just make it visible in the JSON as `"total_new_findings"`).

**Files:** `src/cli/diff.rs`

---

## 6. Rename Handling

**Problem:** Git renames produce `rename from`/`rename to` headers. The current parser tracks `--- a/old` and `+++ b/new` as separate files. If a finding reports against the old path, it won't match the new path's hunks — incorrectly attributed as `InUnchangedFile`.

**Design:**

Track renames in `DiffHunks`:

```rust
pub struct DiffHunks {
    hunks: HashMap<PathBuf, Vec<(u32, u32)>>,
    changed_files: HashSet<PathBuf>,
    /// old_path -> new_path for renames
    renames: HashMap<PathBuf, PathBuf>,
}
```

Parse rename headers:

```rust
// In parse_diff(), before the +++ check:
if let Some(old) = line.strip_prefix("rename from ") {
    pending_rename_from = Some(PathBuf::from(old));
}
if let Some(new) = line.strip_prefix("rename to ") {
    if let Some(old) = pending_rename_from.take() {
        renames.insert(old, PathBuf::from(new));
    }
}
```

Update `attribute()` to check renames:

```rust
pub fn attribute(&self, file: &Path, line: Option<u32>) -> Attribution {
    // Check if this file was renamed — look up the new name
    let effective_file = self.renames.get(file)
        .map(|p| p.as_path())
        .unwrap_or(file);

    if !self.changed_files.contains(effective_file) {
        return Attribution::InUnchangedFile;
    }
    // ... rest uses effective_file for hunk lookup
}
```

Add tests for rename scenarios:
- File renamed without content change — finding against old path attributed as `InChangedFile`
- File renamed with content change — finding in changed hunk attributed as `InChangedHunk`

**Files:** `src/cli/diff_hunks.rs`

---

## Verification

### Unit Tests (new)
- Rename parsing tests in `diff_hunks.rs`
- `format_markdown()` output tests in `diff.rs`
- Filtered count hint test in `diff.rs`

### Integration Tests (new)
- 6 tests in `tests/integration_test.rs` (see item #1)

### Manual
```bash
cd repotoire-cli
cargo test diff
cargo clippy -- -D warnings
cargo fmt -- --check
cargo build && ./target/debug/repotoire diff HEAD~1
```
