# Pipeline Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Protect the analysis pipeline from symlink escapes, path traversal, oversized files, and expose the `--since` CLI flag.

**Architecture:** Add a centralized `validate_file()` function in `cli/analyze/files.rs` that rejects symlinks, out-of-boundary paths, and oversized files. Integrate it into both file collection paths (`collect_source_files` and `get_changed_files_since`). Add `--since` as a CLI argument in `cli/mod.rs`.

**Tech Stack:** Rust, std::fs (symlink_metadata, canonicalize), clap 4 (CLI args)

---

### Task 1: Add `validate_file()` function with tests

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/files.rs:1-16` (add imports, constant, function)

**Step 1: Add the `validate_file()` function**

Add this at the top of `files.rs`, after the existing imports (line 15):

```rust
/// Maximum file size to accept for analysis (2MB, matching parser guardrail).
const MAX_ANALYSIS_FILE_BYTES: u64 = 2 * 1024 * 1024;

/// Validate a file for analysis: reject symlinks, out-of-boundary paths, and oversized files.
///
/// Returns `Some(canonical_path)` if the file passes all checks, `None` otherwise.
fn validate_file(path: &Path, repo_canonical: &Path) -> Option<PathBuf> {
    // 1. Reject symlinks
    match std::fs::symlink_metadata(path) {
        Ok(meta) => {
            if meta.file_type().is_symlink() {
                tracing::warn!("Skipping symlink: {}", path.display());
                return None;
            }
            // 2. Check file size
            if meta.len() > MAX_ANALYSIS_FILE_BYTES {
                tracing::warn!(
                    "Skipping oversized file: {} ({:.1}MB exceeds {}MB limit)",
                    path.display(),
                    meta.len() as f64 / (1024.0 * 1024.0),
                    MAX_ANALYSIS_FILE_BYTES / (1024 * 1024),
                );
                return None;
            }
        }
        Err(e) => {
            tracing::warn!("Cannot stat file {}: {}", path.display(), e);
            return None;
        }
    }

    // 3. Canonicalize and check boundary
    match path.canonicalize() {
        Ok(canonical) => {
            if !canonical.starts_with(repo_canonical) {
                tracing::warn!(
                    "Skipping file outside repository boundary: {} (resolves to {})",
                    path.display(),
                    canonical.display(),
                );
                return None;
            }
            Some(canonical)
        }
        Err(e) => {
            tracing::warn!("Cannot canonicalize {}: {}", path.display(), e);
            None
        }
    }
}
```

**Step 2: Run `cargo check` to verify it compiles**

Run: `cargo check 2>&1`
Expected: Compiles with possible dead_code warning for `validate_file` (not used yet).

**Step 3: Add tests**

Add this test module at the bottom of `files.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn test_validate_file_accepts_normal_file() {
        let dir = TempDir::new().unwrap();
        let file = dir.path().join("test.py");
        fs::write(&file, "print('hello')").unwrap();
        let repo_canonical = dir.path().canonicalize().unwrap();
        assert!(validate_file(&file, &repo_canonical).is_some());
    }

    #[test]
    fn test_validate_file_rejects_nonexistent() {
        let dir = TempDir::new().unwrap();
        let repo_canonical = dir.path().canonicalize().unwrap();
        let fake = dir.path().join("nope.py");
        assert!(validate_file(&fake, &repo_canonical).is_none());
    }

    #[test]
    fn test_validate_file_rejects_oversized() {
        let dir = TempDir::new().unwrap();
        let file = dir.path().join("big.py");
        // Write a file just over 2MB
        let data = vec![b'x'; 2 * 1024 * 1024 + 1];
        fs::write(&file, &data).unwrap();
        let repo_canonical = dir.path().canonicalize().unwrap();
        assert!(validate_file(&file, &repo_canonical).is_none());
    }

    #[test]
    fn test_validate_file_rejects_symlink() {
        let dir = TempDir::new().unwrap();
        let real = dir.path().join("real.py");
        fs::write(&real, "x = 1").unwrap();
        let link = dir.path().join("link.py");

        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(&real, &link).unwrap();
            let repo_canonical = dir.path().canonicalize().unwrap();
            assert!(validate_file(&link, &repo_canonical).is_none());
        }
    }

    #[test]
    fn test_validate_file_rejects_outside_boundary() {
        let parent = TempDir::new().unwrap();
        let repo = parent.path().join("repo");
        fs::create_dir(&repo).unwrap();
        let outside = parent.path().join("secret.py");
        fs::write(&outside, "password = 'hunter2'").unwrap();

        let repo_canonical = repo.canonicalize().unwrap();

        // Construct a path that traverses out: repo/../secret.py
        let traversal_path = repo.join("..").join("secret.py");
        assert!(validate_file(&traversal_path, &repo_canonical).is_none());
    }
}
```

**Step 4: Run tests**

Run: `cargo test -p repotoire --lib -- cli::analyze::files::tests -v 2>&1`
Expected: All 5 tests pass.

**Step 5: Commit**

```bash
git add repotoire-cli/src/cli/analyze/files.rs
git commit -m "feat: add validate_file() for symlink/traversal/size protection"
```

---

### Task 2: Integrate `validate_file()` into `collect_source_files()`

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/files.rs:124-162`

**Step 1: Update `collect_source_files()` to canonicalize repo_path and call validate_file()**

The function currently starts at line 125. Update it to:

```rust
fn collect_source_files(repo_path: &Path, exclude: &ExcludeConfig) -> Result<Vec<PathBuf>> {
    let effective = exclude.effective_patterns();
    let repo_canonical = repo_path.canonicalize().with_context(|| {
        format!("Cannot canonicalize repository path: {}", repo_path.display())
    })?;
    let mut files = Vec::new();

    let mut builder = WalkBuilder::new(repo_path);
    builder
        .hidden(true)
        .git_ignore(true)
        .git_global(true)
        .git_exclude(true)
        .require_git(false)
        .add_custom_ignore_filename(".repotoireignore");

    let walker = builder.build();

    for entry in walker.flatten() {
        let path = entry.path();

        if !path.is_file() {
            continue;
        }

        if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
            if SUPPORTED_EXTENSIONS.contains(&ext) {
                // Skip files matching exclusion patterns
                if let Ok(rel) = path.strip_prefix(repo_path) {
                    let rel_str = rel.to_string_lossy();
                    if effective.iter().any(|p| glob_match(p, &rel_str)) {
                        continue;
                    }
                }
                // Validate: symlinks, boundary, size
                if let Some(validated) = validate_file(path, &repo_canonical) {
                    files.push(validated);
                }
            }
        }
    }

    Ok(files)
}
```

Changes from original:
- Added `repo_canonical` via `canonicalize()` at the top.
- Replaced `files.push(path.to_path_buf())` with `validate_file()` gate.

**Step 2: Run tests**

Run: `cargo test -p repotoire --lib -- cli::analyze::files::tests -v 2>&1`
Expected: All tests still pass.

**Step 3: Run `cargo check`**

Run: `cargo check 2>&1`
Expected: Clean compile.

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/analyze/files.rs
git commit -m "feat: integrate validate_file into collect_source_files"
```

---

### Task 3: Integrate `validate_file()` into `collect_file_list()`

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/files.rs:18-50`

**Step 1: Update `collect_file_list()` to use validate_file()**

```rust
pub(crate) fn collect_file_list(repo_path: &Path, exclude: &ExcludeConfig) -> Result<Vec<PathBuf>> {
    let effective = exclude.effective_patterns();
    let repo_canonical = repo_path.canonicalize().with_context(|| {
        format!("Cannot canonicalize repository path: {}", repo_path.display())
    })?;
    let mut files = Vec::new();

    let walker = WalkBuilder::new(repo_path)
        .hidden(true)
        .git_ignore(true)
        .git_global(false)
        .git_exclude(true)
        .build();

    for entry in walker.filter_map(|e| e.ok()) {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let Some(ext) = path.extension().and_then(|e| e.to_str()) else {
            continue;
        };
        if SUPPORTED_EXTENSIONS.contains(&ext) {
            // Skip files matching exclusion patterns
            if let Ok(rel) = path.strip_prefix(repo_path) {
                let rel_str = rel.to_string_lossy();
                if effective.iter().any(|p| glob_match(p, &rel_str)) {
                    continue;
                }
            }
            if let Some(validated) = validate_file(path, &repo_canonical) {
                files.push(validated);
            }
        }
    }

    Ok(files)
}
```

Changes: Added `repo_canonical`, replaced `files.push(path.to_path_buf())` with `validate_file()`.

**Step 2: Run `cargo check`**

Run: `cargo check 2>&1`
Expected: Clean compile.

**Step 3: Commit**

```bash
git add repotoire-cli/src/cli/analyze/files.rs
git commit -m "feat: integrate validate_file into collect_file_list"
```

---

### Task 4: Integrate `validate_file()` into `get_changed_files_since()`

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/files.rs:164-221`

**Step 1: Update `get_changed_files_since()` to use validate_file()**

This is the highest-risk path — git output is joined to `repo_path` without boundary checking.

```rust
fn get_changed_files_since(repo_path: &Path, since: &str) -> Result<Vec<PathBuf>> {
    use std::process::Command;

    // Sanitize: reject values that look like flags to prevent git flag injection (#49)
    if since.starts_with('-') {
        anyhow::bail!(
            "Invalid --since value '{}': must be a commit hash, branch name, or tag (cannot start with '-')",
            since
        );
    }

    let repo_canonical = repo_path.canonicalize().with_context(|| {
        format!("Cannot canonicalize repository path: {}", repo_path.display())
    })?;

    let output = Command::new("git")
        .args(["diff", "--name-only", since, "HEAD"])
        .current_dir(repo_path)
        .output()
        .with_context(|| format!("Failed to run git diff since '{}'", since))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        anyhow::bail!("git diff failed: {}", stderr.trim());
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut files: Vec<PathBuf> = stdout
        .lines()
        .filter(|l| !l.is_empty())
        .filter_map(|l| {
            let joined = repo_path.join(l);
            if joined.exists() {
                validate_file(&joined, &repo_canonical)
            } else {
                None
            }
        })
        .collect();

    // Also get untracked files
    let untracked = Command::new("git")
        .args(["ls-files", "--others", "--exclude-standard"])
        .current_dir(repo_path)
        .output();

    if let Ok(out) = untracked {
        if out.status.success() {
            let new_files = String::from_utf8_lossy(&out.stdout);
            for line in new_files.lines().filter(|l| !l.is_empty()) {
                let path = repo_path.join(line);
                if path.exists() {
                    if let Some(validated) = validate_file(&path, &repo_canonical) {
                        if !files.contains(&validated) {
                            files.push(validated);
                        }
                    }
                }
            }
        }
    }

    files.retain(|p| {
        p.extension()
            .and_then(|e| e.to_str())
            .map(|ext| SUPPORTED_EXTENSIONS.contains(&ext))
            .unwrap_or(false)
    });

    Ok(files)
}
```

Changes:
- Added `repo_canonical` at the top.
- Replaced `.map(|l| repo_path.join(l)).filter(|p| p.exists())` with `.filter_map()` that calls `validate_file()`.
- Untracked file loop also calls `validate_file()`.

**Step 2: Run tests**

Run: `cargo test -p repotoire --lib -- cli::analyze::files::tests -v 2>&1`
Expected: All tests pass.

**Step 3: Run `cargo check`**

Run: `cargo check 2>&1`
Expected: Clean compile.

**Step 4: Commit**

```bash
git add repotoire-cli/src/cli/analyze/files.rs
git commit -m "fix: add path traversal protection to get_changed_files_since"
```

---

### Task 5: Expose `--since` CLI flag

**Files:**
- Modify: `repotoire-cli/src/cli/mod.rs:118-136` (add flag to Analyze struct)
- Modify: `repotoire-cli/src/cli/mod.rs:312-376` (pass through in match arm)

**Step 1: Add `--since` flag to the Analyze command struct**

In `cli/mod.rs`, inside the `Analyze` variant (after the `verify` field at line 135), add:

```rust
        /// Only analyze files changed since this commit/branch/tag (git integration)
        #[arg(long)]
        since: Option<String>,
```

**Step 2: Add `since` to the match arm destructuring**

In the `Some(Commands::Analyze { ... })` match arm (around line 312-331), add `since` to the destructured fields:

```rust
        Some(Commands::Analyze {
            format,
            output,
            severity,
            top,
            page,
            per_page,
            skip_detector,
            thorough,
            external,
            relaxed,
            no_git,
            skip_graph,
            max_files,
            lite,
            fail_on,
            no_emoji,
            explain_score,
            verify,
            since,    // <-- add this
        }) => {
```

**Step 3: Pass `since` to `analyze::run()` instead of hardcoded `None`**

Change line 370 from `None,` to `since,`:

```rust
            analyze::run(
                &cli.path,
                &format,
                output.as_deref(),
                effective_severity,
                top,
                page,
                per_page,
                skip_detector,
                run_external,
                effective_no_git,
                cli.workers,
                fail_on,
                no_emoji,
                since.is_some(),  // incremental = true when --since is provided
                since,
                explain_score,
                verify,
                effective_skip_graph,
                effective_max_files,
            )
```

**Step 4: Run `cargo check`**

Run: `cargo check 2>&1`
Expected: Clean compile.

**Step 5: Verify the flag shows in help**

Run: `cargo run -- analyze --help 2>&1 | grep -A1 since`
Expected: Shows `--since <SINCE>` with description.

**Step 6: Commit**

```bash
git add repotoire-cli/src/cli/mod.rs
git commit -m "feat: expose --since flag for analyzing files changed since a commit"
```

---

### Task 6: Add `tempfile` dev-dependency (if not already present)

**Files:**
- Check: `repotoire-cli/Cargo.toml`

**Step 1: Check if tempfile is already a dependency**

Run: `grep tempfile repotoire-cli/Cargo.toml`

If it's already there (likely as a dev-dependency), skip this task.

If not, add under `[dev-dependencies]`:
```toml
tempfile = "3"
```

**Step 2: Run `cargo test -p repotoire --lib -- cli::analyze::files::tests -v 2>&1`**

Expected: All tests pass (confirms tempfile is available).

**Step 3: Commit (only if Cargo.toml was modified)**

```bash
git add repotoire-cli/Cargo.toml
git commit -m "chore: add tempfile dev-dependency for file validation tests"
```

---

### Task 7: Final verification

**Step 1: Run full test suite**

Run: `cargo test -p repotoire 2>&1`
Expected: All tests pass.

**Step 2: Run cargo clippy**

Run: `cargo clippy -p repotoire 2>&1`
Expected: No new warnings from our changes.

**Step 3: Verify no regressions by building release**

Run: `cargo build --release 2>&1`
Expected: Clean build.
