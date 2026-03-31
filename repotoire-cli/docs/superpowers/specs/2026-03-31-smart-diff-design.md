# Smart Diff: Hunk-Level Attribution + Live Analysis

## Context

`repotoire diff` compares two sets of cached findings (baseline vs current) using fuzzy matching.
It works but has three UX problems:

1. **Shows findings in untouched files** — on a PR touching 3 files, the diff may show findings
   in files the developer didn't change. This is noise that erodes trust.
2. **Requires pre-cached baseline** — `diff` fails if you haven't run `analyze` on the base
   branch. In CI, this means the Action must analyze first, then diff.
3. **No attribution** — all findings are equally "new". The developer can't tell which findings
   they introduced vs which were pre-existing.

## Goals

1. Default to showing only findings in changed hunks (developer's responsibility)
2. Analyze HEAD on-the-fly if needed (no pre-cached baseline required)
3. Tag each finding with hunk-level attribution (in_changed_hunk, in_changed_file, unrelated)

## Non-Goals

- Line-level git blame attribution (too slow)
- Comparing arbitrary branches without checking out HEAD
- Changing the `repotoire analyze` command

---

## Design

### 1. Hunk-Level Attribution

Parse `git diff -U0 <base_ref>..HEAD` to extract changed line ranges per file.

```rust
/// Changed line ranges from a git diff.
struct DiffHunks {
    /// file_path -> Vec of changed line ranges (1-based, inclusive)
    hunks: HashMap<PathBuf, Vec<(u32, u32)>>,  // (start_line, end_line)
    /// All files that appear in the diff (including renames, deletes)
    changed_files: HashSet<PathBuf>,
}
```

**Parsing `git diff -U0` output:**

The `-U0` flag produces minimal context (no surrounding lines), making hunk headers
easy to parse. Each hunk header looks like: `@@ -old_start,old_count +new_start,new_count @@`

We only need the `+new_start,new_count` side (lines in HEAD). Parse with:
```
^@@.*\+(\d+)(?:,(\d+))?\s@@
```
Where `new_start` is the line and `new_count` defaults to 1 if omitted.

**Attribution logic for each finding:**

```rust
enum Attribution {
    /// Finding's line falls within a changed hunk (+/-3 lines margin).
    /// This is the PR author's responsibility.
    InChangedHunk,
    /// Finding is in a changed file but NOT in a changed hunk.
    /// Pre-existing issue; may or may not be related to the change.
    InChangedFile,
    /// Finding is in a file not touched by the PR.
    /// Cross-cutting effect (e.g., new detector found old issue).
    InUnchangedFile,
}
```

For a finding at `file:line`, check:
1. Is `file` in `changed_files`? No -> `InUnchangedFile`
2. Is `line` within any hunk range in `hunks[file]` (+/-3 margin)? Yes -> `InChangedHunk`
3. Otherwise -> `InChangedFile`

**Margin of +/-3 lines:** Findings may be reported a few lines away from the actual issue
(e.g., a function signature finding on the line after `fn`). The +/-3 margin matches the
existing fuzzy matching tolerance in the diff command.

### 2. Default Filtering

`repotoire diff` defaults to showing only `InChangedHunk` findings. This is the developer's
responsibility -- findings they introduced in this PR.

```
repotoire diff main              -> InChangedHunk findings only (default)
repotoire diff main --all        -> all new findings (current behavior)
repotoire diff main --changed    -> InChangedHunk + InChangedFile (changed files only)
```

The `--all` flag restores the current (v0.6.0) behavior for backward compatibility.

### 3. Live Analysis

When `repotoire diff <base_ref>` is called:

1. **Analyze HEAD** -- run full analysis on the current working tree. This produces current
   findings + score. Fast on warm cache (~0.34s), reasonable on cold (~3.8s).

2. **Load baseline** -- try to load cached findings for `<base_ref>`:
   - First: check session cache (from a previous `analyze` run)
   - If no cache: use empty baseline (all findings are "new"). This is correct for
     first-time PRs and avoids the "must analyze base first" error.

3. **Diff** -- compare findings with hunk-level attribution.

This makes `repotoire diff main` a single command that works out of the box -- no need to
run `analyze` first.

### 4. Output Changes

**Text output (default):**

```
Repotoire Diff: main..HEAD (3 files changed)

YOUR CHANGES (2 findings introduced by this PR)
  [C] SQL injection                    src/api.rs:42
  [C] Command injection                src/utils.rs:15

PRE-EXISTING (1 finding in changed files, not in your hunks)
  [H] XSS                             src/api.rs:180

Score: 85.2 -> 78.4 (-6.8)

3 findings fixed since main
```

With `--all`, shows additional "UNRELATED" section for findings in untouched files.

**JSON output:**

Each finding gets an `attribution` field:
```json
{
  "new_findings": [
    {
      "detector": "sql-injection",
      "severity": "critical",
      "file": "src/api.rs",
      "line": 42,
      "attribution": "in_changed_hunk"
    }
  ]
}
```

**SARIF output:**

Filters to `InChangedHunk` findings only (for GitHub Code Scanning inline annotations).
This means Code Scanning annotations appear ONLY on lines the developer changed -- no noise.

### 5. GitHub Action Integration

The Action already calls `repotoire diff <base_sha>`. With the new default behavior,
it automatically gets hunk-scoped findings without any Action changes. The SARIF output
already filters to hunk-level, so Code Scanning annotations are precise.

No Action changes needed -- the CLI improvement propagates automatically.

---

## Implementation

### New file: `src/cli/diff_hunks.rs`

Parses `git diff -U0` output and provides the `DiffHunks` struct + `Attribution` enum.

```rust
pub struct DiffHunks { ... }

impl DiffHunks {
    /// Parse git diff -U0 output into changed line ranges per file.
    pub fn from_git_diff(repo_path: &Path, base_ref: &str) -> anyhow::Result<Self>;

    /// Attribute a finding based on its file and line.
    pub fn attribute(&self, file: &Path, line: Option<u32>) -> Attribution;
}
```

### Modified files

| File | Changes |
|------|---------|
| `src/cli/diff.rs` | Add `--all` / `--changed` flags. Integrate DiffHunks for attribution. Run analyze before diff if no cache. Filter output by attribution. |
| `src/cli/mod.rs` | Add `--all` and `--changed` args to Diff command |
| `src/cli/diff_hunks.rs` | New: git diff parser + attribution logic |

### CLI changes

```
repotoire diff [BASE_REF] [FLAGS]

Args:
  BASE_REF    Git ref for baseline (default: HEAD~1)

Flags:
  --all       Show all new findings (not just in changed hunks)
  --changed   Show findings in changed files (hunks + non-hunk)
  --format    Output format: text, json, sarif (default: text)
  --fail-on   Exit code 1 if new hunk-level findings at severity (default: none)
  --no-emoji  Disable emoji
  --output    Output file path
```

Default (no flag): show only `InChangedHunk` findings.

---

## Verification

### Unit Tests

- Parse `git diff -U0` output with single hunk, multiple hunks, file add, file delete, rename
- Attribution: finding in hunk -> `InChangedHunk`, finding in file but outside hunk -> `InChangedFile`, finding in untouched file -> `InUnchangedFile`
- Margin: finding at hunk_end + 3 -> `InChangedHunk`, hunk_end + 4 -> `InChangedFile`

### Integration Tests

- `repotoire diff HEAD~1` on repotoire's own repo -- produces output, no crash
- `--all` shows more findings than default
- `--fail-on critical` exits 1 when critical finding in changed hunk

### Manual Test

```bash
# Make a change, commit, diff against previous
echo "// test" >> src/lib.rs
git add src/lib.rs && git commit -m "test"
repotoire diff HEAD~1 --format text
# Should show only findings in src/lib.rs changes
git reset --hard HEAD~1  # clean up
```
