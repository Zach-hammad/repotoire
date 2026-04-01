# Smart Diff Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Polish Smart Diff with 6 improvements: help text, filtered count hint, rename handling, markdown formatter, auto-analyze, and integration tests.

**Spec:** `docs/superpowers/specs/2026-04-01-smart-diff-polish-design.md`

---

## File Structure

### New Files
None.

### Modified Files
| File | Changes |
|------|---------|
| `src/cli/mod.rs` | Update `--help` examples for Diff command |
| `src/cli/diff.rs` | Filtered count hint, markdown formatter, auto-analyze fallback |
| `src/cli/diff_hunks.rs` | Rename tracking in parser and attribution |
| `tests/integration_test.rs` | 5 new diff integration tests |

---

### Task 1: Update `--help` examples

**Files:** `src/cli/mod.rs`

- [ ] **Step 1: Update the `after_help` text for the Diff variant**

Find the `after_help` string for the Diff command (~line 235) and replace the examples section:

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

- [ ] **Step 2: Verify compilation**

Run: `cargo check`

- [ ] **Step 3: Commit**

```bash
git add src/cli/mod.rs
git commit -m "docs(diff): add --all/--changed examples to --help output"
```

---

### Task 2: Filtered count hint

**Files:** `src/cli/diff.rs`

- [ ] **Step 1: Update `format_text()` to show hint when filtered findings exist**

In `format_text()` (~line 180), replace the `if result.new_findings.is_empty()` block:

```rust
if result.new_findings.is_empty() && result.all_new_count > 0 {
    // Hunk-only mode filtered everything, but findings exist elsewhere
    let check = if no_emoji { "[ok]" } else { "\u{2705}" };
    out.push_str(&format!(
        "  {} {}\n",
        check,
        style("No new findings in your changes").green()
    ));
    let info = if no_emoji { "i" } else { "\u{2139}\u{fe0f}" };
    out.push_str(&format!(
        "  {} {} finding{} in other files (use --all to see)\n\n",
        style(info).dim(),
        result.all_new_count,
        if result.all_new_count == 1 { "" } else { "s" }
    ));
} else if result.new_findings.is_empty() {
    let check = if no_emoji { "[ok]" } else { "\u{2705}" };
    out.push_str(&format!(
        "  {} {}\n\n",
        check,
        style("No new findings").green()
    ));
} else {
    // ... existing YOUR CHANGES / PRE-EXISTING / UNRELATED sections
}
```

- [ ] **Step 2: Add `total_new_findings` to JSON output**

In `format_json()`, add to the output JSON object:

```rust
"total_new_findings": result.all_new_count,
```

This makes the unfiltered count visible in JSON for CI consumers.

- [ ] **Step 3: Add test for filtered hint**

```rust
#[test]
fn test_format_text_filtered_hint() {
    let result = SmartDiffResult {
        base_ref: "main".to_string(),
        head_ref: "HEAD".to_string(),
        files_changed: 1,
        new_findings: vec![], // filtered to zero
        all_new_count: 5,     // but 5 exist unfiltered
        fixed_findings: vec![],
        score_before: Some(90.0),
        score_after: Some(88.0),
    };
    let text = format_text(&result, true);
    assert!(text.contains("No new findings in your changes"));
    assert!(text.contains("5 findings in other files"));
    assert!(text.contains("--all"));
}

#[test]
fn test_format_text_truly_zero() {
    let result = SmartDiffResult {
        base_ref: "main".to_string(),
        head_ref: "HEAD".to_string(),
        files_changed: 1,
        new_findings: vec![],
        all_new_count: 0,
        fixed_findings: vec![],
        score_before: Some(90.0),
        score_after: Some(90.0),
    };
    let text = format_text(&result, true);
    assert!(text.contains("No new findings"));
    assert!(!text.contains("--all"));
}
```

- [ ] **Step 4: Verify**

Run: `cargo test diff -- --nocapture`

- [ ] **Step 5: Commit**

```bash
git add src/cli/diff.rs
git commit -m "feat(diff): show hint when findings exist but are filtered by attribution"
```

---

### Task 3: Rename handling

**Files:** `src/cli/diff_hunks.rs`

- [ ] **Step 1: Add `renames` field to `DiffHunks`**

```rust
pub struct DiffHunks {
    hunks: HashMap<PathBuf, Vec<(u32, u32)>>,
    changed_files: HashSet<PathBuf>,
    /// old_path -> new_path for renames detected in the diff
    renames: HashMap<PathBuf, PathBuf>,
}
```

Update `from_git_diff()` error fallback to include `renames: HashMap::new()`.

- [ ] **Step 2: Parse rename headers in `parse_diff()`**

Add a `pending_rename_from: Option<PathBuf>` local variable. In the line loop, before the existing `+++ b/` check:

```rust
if let Some(old) = line.strip_prefix("rename from ") {
    pending_rename_from = Some(PathBuf::from(old));
} else if let Some(new) = line.strip_prefix("rename to ") {
    if let Some(old) = pending_rename_from.take() {
        renames.insert(old, PathBuf::from(new));
    }
} else if let Some(path) = line.strip_prefix("+++ b/") {
    // ... existing code
```

Include `renames` in the returned `Self`.

- [ ] **Step 3: Update `attribute()` to resolve renames**

```rust
pub fn attribute(&self, file: &Path, line: Option<u32>) -> Attribution {
    // Resolve old->new rename if this finding uses the pre-rename path
    let effective = self.renames.get(file).map(|p| p.as_path()).unwrap_or(file);

    if !self.changed_files.contains(effective) {
        return Attribution::InUnchangedFile;
    }

    let line = match line {
        Some(l) => l,
        None => return Attribution::InChangedFile,
    };

    if let Some(file_hunks) = self.hunks.get(effective) {
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
```

- [ ] **Step 4: Add rename tests**

```rust
#[test]
fn test_parse_diff_rename_without_content_change() {
    let diff = "\
diff --git a/src/old.rs b/src/new.rs
similarity index 100%
rename from src/old.rs
rename to src/new.rs
";
    let hunks = DiffHunks::parse_diff(diff);
    // Old path resolves to new path via rename
    assert_eq!(
        hunks.attribute(Path::new("src/old.rs"), Some(10)),
        Attribution::InChangedFile
    );
}

#[test]
fn test_parse_diff_rename_with_content_change() {
    let diff = "\
diff --git a/src/old.rs b/src/new.rs
similarity index 80%
rename from src/old.rs
rename to src/new.rs
--- a/src/old.rs
+++ b/src/new.rs
@@ -5,0 +5,3 @@ fn foo() {
+    added();
+    lines();
+    here();
";
    let hunks = DiffHunks::parse_diff(diff);
    // Finding at line 6 in old path -> resolves via rename -> in hunk
    assert_eq!(
        hunks.attribute(Path::new("src/old.rs"), Some(6)),
        Attribution::InChangedHunk
    );
    // Finding at line 100 in old path -> resolves via rename -> in file but not hunk
    assert_eq!(
        hunks.attribute(Path::new("src/old.rs"), Some(100)),
        Attribution::InChangedFile
    );
}
```

- [ ] **Step 5: Verify**

Run: `cargo test diff_hunks -- --nocapture`

- [ ] **Step 6: Commit**

```bash
git add src/cli/diff_hunks.rs
git commit -m "fix(diff): handle git renames in hunk attribution"
```

---

### Task 4: Markdown formatter

**Files:** `src/cli/diff.rs`

- [ ] **Step 1: Add `format_markdown()` function**

After `format_text()`, add a new function that produces clean Markdown without ANSI escapes. Groups findings by attribution into `## Your Changes`, `## Pre-existing`, and `## Unrelated` sections using Markdown tables (Severity | Finding | Location). Includes the filtered count hint using blockquote syntax. Score delta as bold text. Fixed count at the bottom.

Also add a `format_markdown_row()` helper that formats a single finding as a table row with backtick-wrapped location.

- [ ] **Step 2: Wire into `emit_output()`**

In `emit_output()`, update the match:

```rust
let output_str = match format {
    OutputFormat::Json => format_json(result),
    OutputFormat::Sarif => format_sarif(result)?,
    OutputFormat::Markdown => format_markdown(result),
    _ => format_text(result, no_emoji),
};
```

- [ ] **Step 3: Add test**

Test that `format_markdown()` output starts with `# Repotoire Diff:`, contains `## Your Changes`, has table headers, has backtick-wrapped locations, and contains no ANSI escape sequences (`\x1b[`).

- [ ] **Step 4: Verify**

Run: `cargo test diff -- --nocapture`

- [ ] **Step 5: Commit**

```bash
git add src/cli/diff.rs
git commit -m "feat(diff): add attribution-aware markdown formatter"
```

---

### Task 5: Auto-analyze fallback

**Files:** `src/cli/diff.rs`

- [ ] **Step 1: Add `run_inline_analysis()` helper**

Add a private function in `diff.rs` that creates an `AnalysisEngine` (trying `load()` from session dir first, falling back to `new()`), runs `analyze()` with default config, and calls `cache_results()` to write `last_findings.json` and `last_health.json`.

NOTE: Check that `AnalysisConfig::default()` and `AnalysisEngine::new()` are public. Verify the analysis result struct field names (`result.report`, `result.findings`, etc.) by reading the actual code before implementing.

- [ ] **Step 2: Update `run()` to use auto-analyze fallback**

Replace the current `load_baseline_and_head()` call in `run()` with:

```rust
let (baseline, head, score_before, score_after) =
    match load_baseline_and_head(&repotoire_dir, &repo_path, base_ref.as_deref()) {
        Ok(result) => result,
        Err(_) => {
            eprintln!("No cached analysis found, running analysis...");
            run_inline_analysis(&repo_path, &repotoire_dir)?;
            load_baseline_and_head(&repotoire_dir, &repo_path, base_ref.as_deref())
                .context("Analysis completed but could not load findings")?
        }
    };
```

- [ ] **Step 3: Handle empty baseline gracefully**

In `load_baseline_and_head()`, change baseline loading to return empty vec instead of bailing when no baseline file exists. Keep the head loading strict (it must exist after auto-analyze).

- [ ] **Step 4: Verify manually**

```bash
cargo build
# Clear cache for a test repo, then run diff without prior analyze
# Should see "No cached analysis found, running analysis..." then diff output
```

- [ ] **Step 5: Commit**

```bash
git add src/cli/diff.rs
git commit -m "feat(diff): auto-analyze when no cached findings exist"
```

---

### Task 6: Integration tests

**Files:** `tests/integration_test.rs`

- [ ] **Step 1: Add test helpers**

Add `create_git_test_repo()` — uses existing `create_test_workspace()` to copy fixtures, then runs `git init`, configures user.email/user.name, `git add .`, `git commit`.

Add `run_diff()` — similar to existing `run_analyze()` helper but invokes `diff` subcommand.

- [ ] **Step 2: Add diff integration tests**

5 tests:
- `test_diff_text_output` — analyze, make change, commit, analyze again, diff. Assert exit 0 and output contains "Repotoire Diff".
- `test_diff_json_output` — same setup with `--format json`. Assert valid JSON with `new_findings` and `score_before` fields.
- `test_diff_sarif_output` — same setup with `--format sarif`. Assert valid JSON with `$schema` or `runs` field.
- `test_diff_all_flag_shows_more` — compare JSON finding counts: `--all` count >= default count.
- `test_diff_auto_analyze` — run diff WITHOUT prior analyze. Assert exit 0 and stderr mentions auto-analysis.

Test fixture changes should use intentionally vulnerable Python patterns (the test fixtures directory already contains deliberately bad code for detector testing).

- [ ] **Step 3: Run the integration tests**

Run: `cargo test --test integration_test diff -- --nocapture`

- [ ] **Step 4: Fix any failures**

Integration tests may expose edge cases. Fix issues as they arise.

- [ ] **Step 5: Run full CI checks**

```bash
cargo test
cargo clippy --all-features -- -D warnings
cargo fmt --all -- --check
```

- [ ] **Step 6: Commit**

```bash
git add tests/integration_test.rs
git commit -m "test(diff): add 5 integration tests for smart diff"
```
