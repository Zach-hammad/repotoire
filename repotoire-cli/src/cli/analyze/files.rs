//! File collection and discovery for the analyze command.
//!
//! Handles full, incremental, and `--since` file collection modes,
//! including cached finding retrieval for unchanged files.

use super::setup::{FileCollectionResult, SUPPORTED_EXTENSIONS};
use crate::detectors::IncrementalCache;
use crate::models::Finding;

use anyhow::{Context, Result};
use console::style;
use ignore::WalkBuilder;
use indicatif::{ProgressBar, ProgressStyle};
use std::path::{Path, PathBuf};

/// Quick file list collection (no git, no incremental checking) for cache validation
pub(super) fn collect_file_list(repo_path: &Path) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();

    let walker = WalkBuilder::new(repo_path)
        .hidden(true)
        .git_ignore(true)
        .git_global(false)
        .git_exclude(true)
        .build();

    for entry in walker.filter_map(|e| e.ok()) {
        let path = entry.path();
        if path.is_file() {
            if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
                if SUPPORTED_EXTENSIONS.contains(&ext) {
                    files.push(path.to_path_buf());
                }
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
) -> Result<FileCollectionResult> {
    let walk_spinner = multi.add(ProgressBar::new_spinner());
    walk_spinner.set_style(spinner_style.clone());

    let (all_files, files_to_parse, cached_findings) = if let Some(ref commit) = since {
        // --since mode: only analyze files changed since specified commit
        walk_spinner.set_message(format!("Finding files changed since {}...", commit));
        walk_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

        let changed = get_changed_files_since(repo_path, commit)?;
        let all = collect_source_files(repo_path)?;

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

        let all = collect_source_files(repo_path)?;
        let changed = incremental_cache.get_changed_files(&all);
        let cache_stats = incremental_cache.get_stats();

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

        let files = collect_source_files(repo_path)?;
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
fn collect_source_files(repo_path: &Path) -> Result<Vec<PathBuf>> {
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
                files.push(path.to_path_buf());
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
        .map(|l| repo_path.join(l))
        .filter(|p| p.exists())
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
                if path.exists() && !files.contains(&path) {
                    files.push(path);
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
        cached.extend(incremental_cache.get_cached_findings(file));
    }
    cached
}
