# Vendor Exclusion Defaults — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add built-in default vendor/third-party exclusion patterns at the file collection stage.

**Architecture:** Add `DEFAULT_EXCLUDE_PATTERNS` constant and `skip_defaults` config field. Apply patterns during file walks in `collect_source_files()` and `collect_file_list()`.

**Tech Stack:** Rust, serde, ignore crate (WalkBuilder)

---

### Task 1: Add DEFAULT_EXCLUDE_PATTERNS and ExcludeConfig changes

**Files:**
- Modify: `repotoire-cli/src/config/project_config.rs`

**Step 1: Add the default patterns constant**

Add after line 34 (after `use tracing::{debug, warn};`):

```rust
/// Built-in default exclusion patterns for vendored/third-party code.
/// These are applied automatically unless `skip_defaults = true` in config.
pub const DEFAULT_EXCLUDE_PATTERNS: &[&str] = &[
    "**/vendor/**",
    "**/node_modules/**",
    "**/third_party/**",
    "**/third-party/**",
    "**/bower_components/**",
    "**/dist/**",
    "**/*.min.js",
    "**/*.min.css",
    "**/*.bundle.js",
];
```

**Step 2: Add `skip_defaults` field to ExcludeConfig**

Change `ExcludeConfig` struct to:

```rust
/// Path exclusion configuration
#[derive(Debug, Clone, Deserialize, Default)]
pub struct ExcludeConfig {
    /// Paths/patterns to exclude from analysis
    #[serde(default)]
    pub paths: Vec<String>,

    /// If true, disable built-in default exclusion patterns (vendor/, node_modules/, etc.)
    #[serde(default)]
    pub skip_defaults: bool,
}
```

**Step 3: Add `effective_patterns()` method to ExcludeConfig**

```rust
impl ExcludeConfig {
    /// Returns the effective list of exclusion patterns, combining defaults with user patterns.
    /// If `skip_defaults` is true, only user-specified patterns are returned.
    pub fn effective_patterns(&self) -> Vec<String> {
        let mut patterns = Vec::new();

        if !self.skip_defaults {
            patterns.extend(DEFAULT_EXCLUDE_PATTERNS.iter().map(|s| s.to_string()));
        }

        for p in &self.paths {
            if !patterns.contains(p) {
                patterns.push(p.clone());
            }
        }

        patterns
    }
}
```

**Step 4: Update `should_exclude()` to use effective patterns**

Change the `should_exclude` method on `ProjectConfig` to:

```rust
pub fn should_exclude(&self, path: &Path) -> bool {
    let path_str = path.to_string_lossy();

    for pattern in &self.exclude.effective_patterns() {
        if glob_match(pattern, &path_str) {
            return true;
        }
    }

    false
}
```

**Step 5: Run `cargo check`**

Run: `cargo check -p repotoire-cli`
Expected: Compiles with no errors

**Step 6: Commit**

```bash
git add repotoire-cli/src/config/project_config.rs
git commit -m "feat: add built-in default vendor exclusion patterns"
```

---

### Task 2: Apply exclusion at file collection stage

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/files.rs`

**Step 1: Add ExcludeConfig import and update function signatures**

Add to imports:
```rust
use crate::config::project_config::ExcludeConfig;
```

Change `collect_file_list` signature to:
```rust
pub(crate) fn collect_file_list(repo_path: &Path, exclude: &ExcludeConfig) -> Result<Vec<PathBuf>>
```

Change `collect_source_files` signature to:
```rust
fn collect_source_files(repo_path: &Path, exclude: &ExcludeConfig) -> Result<Vec<PathBuf>>
```

**Step 2: Add path filtering in `collect_source_files`**

After the extension check, add path exclusion check:

```rust
fn collect_source_files(repo_path: &Path, exclude: &ExcludeConfig) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    let effective = exclude.effective_patterns();

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

        // Skip files matching exclusion patterns
        if let Ok(rel) = path.strip_prefix(repo_path) {
            let rel_str = rel.to_string_lossy();
            if effective.iter().any(|p| glob_match(p, &rel_str)) {
                continue;
            }
        }

        if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
            if SUPPORTED_EXTENSIONS.contains(&ext) {
                files.push(path.to_path_buf());
            }
        }
    }

    Ok(files)
}
```

**Step 3: Apply same pattern in `collect_file_list`**

```rust
pub(crate) fn collect_file_list(repo_path: &Path, exclude: &ExcludeConfig) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    let effective = exclude.effective_patterns();

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

        // Skip files matching exclusion patterns
        if let Ok(rel) = path.strip_prefix(repo_path) {
            let rel_str = rel.to_string_lossy();
            if effective.iter().any(|p| glob_match(p, &rel_str)) {
                continue;
            }
        }

        let Some(ext) = path.extension().and_then(|e| e.to_str()) else {
            continue;
        };
        if SUPPORTED_EXTENSIONS.contains(&ext) {
            files.push(path.to_path_buf());
        }
    }

    Ok(files)
}
```

**Step 4: Make `glob_match` public**

In `project_config.rs`, change:
```rust
fn glob_match(pattern: &str, path: &str) -> bool {
```
to:
```rust
pub fn glob_match(pattern: &str, path: &str) -> bool {
```

**Step 5: Update callers of `collect_source_files` inside files.rs**

In `collect_files_for_analysis`, the function is called 3 times. Update the function signature to accept `exclude: &ExcludeConfig` and pass it through:

```rust
pub(super) fn collect_files_for_analysis(
    repo_path: &Path,
    since: &Option<String>,
    is_incremental_mode: bool,
    incremental_cache: &mut IncrementalCache,
    multi: &indicatif::MultiProgress,
    spinner_style: &ProgressStyle,
    exclude: &ExcludeConfig,
) -> Result<FileCollectionResult> {
```

Update the 3 internal calls:
- Line 61: `collect_source_files(repo_path)` → `collect_source_files(repo_path, exclude)`
- Line 78: `collect_source_files(repo_path)` → `collect_source_files(repo_path, exclude)`
- Line 97: `collect_source_files(repo_path)` → `collect_source_files(repo_path, exclude)`

**Step 6: Run `cargo check`**

Run: `cargo check -p repotoire-cli`
Expected: Compiler errors from callers (fixed in Task 3)

---

### Task 3: Update all callers

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs`
- Modify: `repotoire-cli/src/cli/mod.rs`

**Step 1: Update `cli/analyze/mod.rs` callers**

Find all calls to `collect_file_list` and `collect_files_for_analysis` and add the exclude parameter. The `AnalysisEnv` struct likely has access to `project_config`. Pass `&env.project_config.exclude` or `&project_config.exclude` depending on the context.

For `collect_file_list(&env.repo_path)` (line 334):
```rust
let all_files = collect_file_list(&env.repo_path, &env.project_config.exclude)?;
```

For `collect_files_for_analysis` calls, add the exclude config parameter.

**Step 2: Check if AnalysisEnv has project_config**

Read the AnalysisEnv struct and its construction to determine how to access `project_config`. If it's not available, load it with `load_project_config(&env.repo_path)`.

**Step 3: Update `cli/mod.rs` calibrate caller**

At line 613:
```rust
let files = crate::cli::analyze::files::collect_file_list(
    &repo_path,
    &crate::config::project_config::ExcludeConfig::default(),
)?;
```

**Step 4: Run `cargo check`**

Run: `cargo check -p repotoire-cli`
Expected: Compiles with no errors

**Step 5: Commit**

```bash
git add repotoire-cli/src/cli/analyze/files.rs repotoire-cli/src/cli/analyze/mod.rs repotoire-cli/src/cli/mod.rs repotoire-cli/src/config/project_config.rs
git commit -m "feat: apply vendor exclusion at file collection stage"
```

---

### Task 4: Add unit tests

**Files:**
- Modify: `repotoire-cli/src/config/project_config.rs` (tests module)

**Step 1: Add tests for effective_patterns and defaults**

```rust
#[test]
fn test_default_exclude_patterns_applied() {
    let config = ExcludeConfig::default();
    let patterns = config.effective_patterns();
    assert!(patterns.contains(&"**/vendor/**".to_string()));
    assert!(patterns.contains(&"**/node_modules/**".to_string()));
    assert!(patterns.contains(&"**/*.min.js".to_string()));
    assert_eq!(patterns.len(), DEFAULT_EXCLUDE_PATTERNS.len());
}

#[test]
fn test_skip_defaults_disables_builtin_patterns() {
    let config = ExcludeConfig {
        paths: vec!["custom/".to_string()],
        skip_defaults: true,
    };
    let patterns = config.effective_patterns();
    assert_eq!(patterns, vec!["custom/"]);
    assert!(!patterns.contains(&"**/vendor/**".to_string()));
}

#[test]
fn test_user_patterns_merged_with_defaults() {
    let config = ExcludeConfig {
        paths: vec!["generated/".to_string()],
        skip_defaults: false,
    };
    let patterns = config.effective_patterns();
    assert!(patterns.contains(&"**/vendor/**".to_string()));
    assert!(patterns.contains(&"generated/".to_string()));
    assert_eq!(patterns.len(), DEFAULT_EXCLUDE_PATTERNS.len() + 1);
}

#[test]
fn test_effective_patterns_deduplication() {
    let config = ExcludeConfig {
        paths: vec!["**/vendor/**".to_string()],
        skip_defaults: false,
    };
    let patterns = config.effective_patterns();
    let vendor_count = patterns.iter().filter(|p| *p == "**/vendor/**").count();
    assert_eq!(vendor_count, 1);
}

#[test]
fn test_should_exclude_vendor_path() {
    let config = ProjectConfig::default();
    assert!(config.should_exclude(std::path::Path::new("src/vendor/jquery.js")));
    assert!(config.should_exclude(std::path::Path::new("node_modules/react/index.js")));
    assert!(config.should_exclude(std::path::Path::new("assets/lib.min.js")));
    assert!(!config.should_exclude(std::path::Path::new("src/main.py")));
}
```

**Step 2: Run tests**

Run: `cargo test -p repotoire-cli -- test_default_exclude test_skip_defaults test_user_patterns test_effective_patterns test_should_exclude_vendor`
Expected: All pass

**Step 3: Commit**

```bash
git add repotoire-cli/src/config/project_config.rs
git commit -m "test: add unit tests for vendor exclusion defaults"
```

---

### Task 5: Build release binary and validate against Django

**Step 1: Build release binary**

Run: `cargo build --release -p repotoire-cli`

**Step 2: Re-run Django analysis**

Run: `./repotoire-cli/target/release/repotoire analyze /tmp/django --format json --per-page 0 > /tmp/django-r4-results.json 2>/dev/null`

**Step 3: Verify reduction**

Check total finding count. Expected: ~817 findings (down from 1,802).
Check that no vendor/ files appear in findings.

**Step 4: Update Django validation report**

Add Round 2 section to `docs/audit/django-validation-report.md` with new results.

**Step 5: Commit**

```bash
git add docs/audit/django-validation-report.md
git commit -m "docs: add Django Round 2 results with vendor exclusion"
```
