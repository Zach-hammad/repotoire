//! File collection and discovery for the analyze command.
//!
//! Handles full, incremental, and `--since` file collection modes,
//! including cached finding retrieval for unchanged files.

use super::setup::{FileCollectionResult, SUPPORTED_EXTENSIONS};
use crate::config::{glob_match, ExcludeConfig};
use crate::detectors::IncrementalCache;
use crate::models::Finding;

use anyhow::{Context, Result};
use console::style;
use ignore::WalkBuilder;
use indicatif::{ProgressBar, ProgressStyle};
use std::path::{Path, PathBuf};

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

/// Quick file list collection (no git, no incremental checking) for cache validation
pub(crate) fn collect_file_list(repo_path: &Path, exclude: &ExcludeConfig) -> Result<Vec<PathBuf>> {
    let repo_canonical = repo_path.canonicalize().with_context(|| {
        format!("Cannot canonicalize repository path: {}", repo_path.display())
    })?;
    let effective = exclude.effective_patterns();
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

/// Collect files for analysis based on mode (full, incremental, or since)
pub(super) fn collect_files_for_analysis(
    repo_path: &Path,
    since: &Option<String>,
    is_incremental_mode: bool,
    incremental_cache: &mut IncrementalCache,
    multi: &indicatif::MultiProgress,
    spinner_style: &ProgressStyle,
    exclude: &ExcludeConfig,
) -> Result<FileCollectionResult> {
    let walk_spinner = multi.add(ProgressBar::new_spinner());
    walk_spinner.set_style(spinner_style.clone());

    let (all_files, files_to_parse, cached_findings) = if let Some(ref commit) = since {
        // --since mode: only analyze files changed since specified commit
        walk_spinner.set_message(format!("Finding files changed since {}...", commit));
        walk_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

        let changed = get_changed_files_since(repo_path, commit)?;
        let all = collect_source_files(repo_path, exclude)?;

        walk_spinner.finish_with_message(format!(
            "{}Found {} changed files (since {}) out of {} total",
            style("✓ ").green(),
            style(changed.len()).cyan(),
            style(commit).yellow(),
            style(all.len()).dim()
        ));

        let cached = get_cached_findings_for_unchanged(&all, &changed, incremental_cache);
        (all, changed, cached)
    } else if is_incremental_mode {
        // --incremental mode: only analyze files changed since last run
        walk_spinner.set_message("Discovering source files (incremental mode)...");
        walk_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

        let all = collect_source_files(repo_path, exclude)?;
        let changed = incremental_cache.changed_files(&all);
        let cache_stats = incremental_cache.stats();

        walk_spinner.finish_with_message(format!(
            "{}Found {} changed files out of {} total ({} cached)",
            style("✓ ").green(),
            style(changed.len()).cyan(),
            style(all.len()).dim(),
            style(cache_stats.cached_files).dim()
        ));

        let cached = get_cached_findings_for_unchanged(&all, &changed, incremental_cache);
        (all, changed, cached)
    } else {
        // Full mode: analyze all files
        walk_spinner.set_message("Discovering source files...");
        walk_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

        let files = collect_source_files(repo_path, exclude)?;
        walk_spinner.finish_with_message(format!(
            "{}Found {} source files",
            style("✓ ").green(),
            style(files.len()).cyan()
        ));

        (files.clone(), files, Vec::new())
    };

    Ok(FileCollectionResult {
        all_files,
        files_to_parse,
        cached_findings,
    })
}

/// Collect all source files in the repository, respecting .gitignore
fn collect_source_files(repo_path: &Path, exclude: &ExcludeConfig) -> Result<Vec<PathBuf>> {
    let repo_canonical = repo_path.canonicalize().with_context(|| {
        format!("Cannot canonicalize repository path: {}", repo_path.display())
    })?;
    let effective = exclude.effective_patterns();
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
                if let Some(validated) = validate_file(path, &repo_canonical) {
                    files.push(validated);
                }
            }
        }
    }

    Ok(files)
}

/// Get files changed since a specific git commit
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
                if !path.exists() { continue; }
                let Some(validated) = validate_file(&path, &repo_canonical) else { continue; };
                if !files.contains(&validated) {
                    files.push(validated);
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

/// Get cached findings for unchanged files
fn get_cached_findings_for_unchanged(
    all_files: &[PathBuf],
    changed_files: &[PathBuf],
    incremental_cache: &IncrementalCache,
) -> Vec<Finding> {
    let unchanged: Vec<_> = all_files
        .iter()
        .filter(|f| !changed_files.contains(f))
        .collect();

    let mut cached = Vec::new();
    for file in unchanged {
        cached.extend(incremental_cache.cached_findings(file));
    }
    cached
}

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
        let traversal_path = repo.join("..").join("secret.py");
        assert!(validate_file(&traversal_path, &repo_canonical).is_none());
    }
}
