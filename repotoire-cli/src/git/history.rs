//! Git history extraction using libgit2
//!
//! Extracts commit history, calculates churn metrics, and tracks file changes
//! over time using the git2 crate (Rust bindings to libgit2).

use anyhow::{Context, Result};
use chrono::{DateTime, TimeZone, Utc};
use git2::{DiffOptions, ObjectType, Repository, Sort};
use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::Once;
use tracing::debug;

/// One-time libgit2 global tuning for read-only analysis workloads.
/// - Disables SHA hash verification (5-15% speedup — we only read, never write)
/// - Increases tree object cache limit from 4KB to 256KB (fewer re-decompressions)
static GIT2_TUNED: Once = Once::new();

fn tune_libgit2() {
    GIT2_TUNED.call_once(|| {
        // Skip SHA re-verification on decompressed objects.
        // We only read; tamper detection is not needed for analysis.
        git2::opts::strict_hash_verification(false);

        // Raise tree cache limit — default 4KB is too small for large repos.
        // SAFETY: called once before any threads via Once guard.
        unsafe {
            let _ = git2::opts::set_cache_object_limit(ObjectType::Tree, 256 * 1024);
        }
    });
}

/// Information about a git commit.
#[derive(Debug, Clone)]
pub struct CommitInfo {
    /// Short hash (12 characters)
    pub hash: String,
    /// Full commit hash
    pub full_hash: String,
    /// Author name
    pub author: String,
    /// Author email
    pub author_email: String,
    /// Commit timestamp (ISO 8601)
    pub timestamp: String,
    /// Commit message (first line)
    pub message: String,
    /// Files changed in this commit
    pub files_changed: Vec<String>,
    /// Total lines added
    pub insertions: usize,
    /// Total lines deleted
    pub deletions: usize,
}

/// Churn metrics for a file.
#[derive(Debug, Clone, Default)]
pub struct FileChurn {
    /// Total lines added across all commits
    pub total_insertions: usize,
    /// Total lines deleted across all commits
    pub total_deletions: usize,
    /// Number of commits touching this file
    pub commit_count: usize,
    /// Unique authors who modified this file
    pub authors: Vec<String>,
    /// Most recent modification timestamp
    pub last_modified: Option<String>,
    /// Most recent author
    pub last_author: Option<String>,
}

/// Per-hunk detail with line range and insertion/deletion counts.
/// Used by `get_churn_and_hunks` for function-level accuracy.
#[derive(Debug, Clone)]
pub struct HunkDetail {
    /// Start line in the new file (1-indexed)
    pub new_start: u32,
    /// End line in the new file (exclusive)
    pub new_end: u32,
    /// Lines added in this hunk
    pub insertions: usize,
    /// Lines deleted in this hunk
    pub deletions: usize,
}

/// Git history analyzer using libgit2.
pub struct GitHistory {
    repo: Repository,
}

/// Extract file path from a diff delta and process it for churn tracking.
fn process_diff_file_cb(
    delta: git2::DiffDelta<'_>,
    churn_map: &mut HashMap<String, FileChurn>,
    author: &str,
    timestamp: &str,
) {
    let Some(path) = delta.new_file().path() else {
        return;
    };
    let path_str = path.to_string_lossy().to_string();
    process_diff_delta(churn_map, path_str, author, timestamp);
}

/// Process a single diff delta, updating churn tracking for the file.
fn process_diff_delta(
    churn_map: &mut HashMap<String, FileChurn>,
    path_str: String,
    author: &str,
    timestamp: &str,
) {
    let entry = churn_map.entry(path_str).or_default();
    entry.commit_count += 1;

    // Track authors
    if !entry.authors.contains(&author.to_string()) {
        entry.authors.push(author.to_string());
    }

    // Update last modified if this is newer
    if entry.last_modified.is_none() {
        entry.last_modified = Some(timestamp.to_string());
        entry.last_author = Some(author.to_string());
    }
}

/// Create DiffOptions tuned for performance.
///
/// - `skip_binary_check`: avoids decompressing blobs just to detect binary files
/// - `context_lines(0)`: no surrounding context needed for analysis
/// - `ignore_submodules`: skip submodule diffing
fn fast_diff_opts() -> DiffOptions {
    let mut opts = DiffOptions::new();
    opts.skip_binary_check(true);
    opts.ignore_submodules(true);
    opts.context_lines(0);
    opts.interhunk_lines(0);
    opts
}

/// Create fast DiffOptions with an exact pathspec filter.
fn fast_pathspec_opts(path: &str) -> DiffOptions {
    let mut opts = fast_diff_opts();
    opts.pathspec(path);
    opts.disable_pathspec_match(true);
    opts
}

/// Check whether a commit is before the given timestamp cutoff
fn is_commit_before(commit: &git2::Commit, since: Option<DateTime<Utc>>) -> bool {
    let Some(since_ts) = since else { return false };
    let commit_time = commit.time();
    Utc.timestamp_opt(commit_time.seconds(), 0)
        .single()
        .is_some_and(|dt| dt < since_ts)
}

impl GitHistory {
    /// Create a new GitHistory for a repository.
    ///
    /// # Arguments
    /// * `path` - Path to the repository (or any subdirectory)
    pub fn new(path: &Path) -> Result<Self> {
        Self::open(path)
    }

    /// Open a git repository.
    ///
    /// # Arguments
    /// * `path` - Path to the repository (or any subdirectory)
    pub fn open(path: &Path) -> Result<Self> {
        tune_libgit2();
        let repo = Repository::discover(path)
            .with_context(|| format!("Failed to open git repository at {:?}", path))?;
        debug!("Opened git repository at {:?}", repo.path());
        Ok(Self { repo })
    }

    /// Check if a path is inside a git repository.
    pub fn is_git_repo(path: &Path) -> bool {
        Repository::discover(path).is_ok()
    }

    /// Get the repository root path.
    pub fn repo_root(&self) -> Result<&Path> {
        self.repo
            .workdir()
            .context("Repository has no working directory (bare repo?)")
    }

    /// Get commit history for a specific file.
    ///
    /// # Arguments
    /// * `file_path` - Relative path to file within repo
    /// * `max_commits` - Maximum number of commits to retrieve
    pub fn get_file_commits(&self, file_path: &str, max_commits: usize) -> Result<Vec<CommitInfo>> {
        let mut revwalk = self.repo.revwalk()?;
        revwalk.set_sorting(Sort::TIME)?;
        revwalk.push_head()?;

        let mut commits = Vec::new();
        let mut diff_opts = fast_pathspec_opts(file_path);

        for oid_result in revwalk {
            if commits.len() >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(oid)?;

            // Check if this commit touched the file
            let parent = commit.parent(0).ok();
            let tree = commit.tree()?;
            let parent_tree = parent.as_ref().map(|p| p.tree()).transpose()?;

            let diff = self.repo.diff_tree_to_tree(
                parent_tree.as_ref(),
                Some(&tree),
                Some(&mut diff_opts),
            )?;

            // Skip if no changes to this file
            if diff.deltas().len() == 0 {
                continue;
            }

            let commit_info = self.extract_commit_info(&commit)?;
            commits.push(commit_info);
        }

        Ok(commits)
    }

    /// Get recent commits across the entire repository.
    ///
    /// # Arguments
    /// * `max_commits` - Maximum number of commits to retrieve
    /// * `since` - Optional timestamp to filter commits after
    pub fn get_recent_commits(
        &self,
        max_commits: usize,
        since: Option<DateTime<Utc>>,
    ) -> Result<Vec<CommitInfo>> {
        let mut revwalk = self.repo.revwalk()?;
        revwalk.set_sorting(Sort::TIME)?;
        revwalk.push_head()?;

        let mut commits = Vec::new();

        for oid_result in revwalk {
            if commits.len() >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(oid)?;

            // Filter by timestamp if specified — commits are sorted by time, so stop early
            if is_commit_before(&commit, since) {
                break;
            }

            let commit_info = self.extract_commit_info(&commit)?;
            commits.push(commit_info);
        }

        Ok(commits)
    }

    /// Calculate churn metrics for a file.
    ///
    /// # Arguments
    /// * `file_path` - Relative path to file within repo
    /// * `max_commits` - Maximum number of commits to analyze
    pub fn get_file_churn(&self, file_path: &str, max_commits: usize) -> Result<FileChurn> {
        let commits = self.get_file_commits(file_path, max_commits)?;

        let mut churn = FileChurn::default();
        let mut author_set = std::collections::HashSet::new();

        for commit in &commits {
            // Get diff stats for this specific file in this commit
            let stats = self.get_commit_file_stats(&commit.full_hash, file_path)?;
            churn.total_insertions += stats.0;
            churn.total_deletions += stats.1;
            author_set.insert(commit.author.clone());
        }

        churn.commit_count = commits.len();
        churn.authors = author_set.into_iter().collect();

        if let Some(latest) = commits.first() {
            churn.last_modified = Some(latest.timestamp.clone());
            churn.last_author = Some(latest.author.clone());
        }

        Ok(churn)
    }

    /// Get churn metrics for all files in the repository.
    ///
    /// # Arguments
    /// * `max_commits` - Maximum number of commits to analyze per file
    pub fn get_all_file_churn(&self, max_commits: usize) -> Result<HashMap<String, FileChurn>> {
        let mut churn_map: HashMap<String, FileChurn> = HashMap::new();

        let mut revwalk = self.repo.revwalk()?;
        revwalk.set_sorting(Sort::TIME)?;
        revwalk.simplify_first_parent()?;
        revwalk.push_head()?;

        let mut diff_opts = fast_diff_opts();

        for (commit_count, oid_result) in revwalk.enumerate() {
            if commit_count >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(oid)?;

            // Get diff with parent
            let parent = commit.parent(0).ok();
            let tree = commit.tree()?;
            let parent_tree = parent.as_ref().map(|p| p.tree()).transpose()?;

            let diff = self
                .repo
                .diff_tree_to_tree(parent_tree.as_ref(), Some(&tree), Some(&mut diff_opts))?;

            let author = commit.author().name().unwrap_or("Unknown").to_string();
            let timestamp = format_git_time(&commit.time());

            // Process each file in the diff
            let churn_ref = &mut churn_map;
            let author_ref = &author;
            let ts_ref = &timestamp;
            diff.foreach(
                &mut |delta, _| {
                    process_diff_file_cb(delta, churn_ref, author_ref, ts_ref);
                    true
                },
                None,
                None,
                None,
            )?;

        }

        Ok(churn_map)
    }

    /// Get line change stats for a specific file in a specific commit.
    fn get_commit_file_stats(&self, commit_hash: &str, file_path: &str) -> Result<(usize, usize)> {
        let oid = git2::Oid::from_str(commit_hash)?;
        let commit = self.repo.find_commit(oid)?;

        let parent = commit.parent(0).ok();
        let tree = commit.tree()?;
        let parent_tree = parent.as_ref().map(|p| p.tree()).transpose()?;

        let mut diff_opts = fast_pathspec_opts(file_path);

        let diff =
            self.repo
                .diff_tree_to_tree(parent_tree.as_ref(), Some(&tree), Some(&mut diff_opts))?;

        let stats = diff.stats()?;
        Ok((stats.insertions(), stats.deletions()))
    }

    /// Extract commit information from a git2 Commit object.
    fn extract_commit_info(&self, commit: &git2::Commit) -> Result<CommitInfo> {
        let author = commit.author();
        let timestamp = format_git_time(&commit.time());
        let message = commit
            .message()
            .unwrap_or("")
            .lines()
            .next()
            .unwrap_or("")
            .to_string();

        // Get changed files
        let parent = commit.parent(0).ok();
        let tree = commit.tree()?;
        let parent_tree = parent.as_ref().map(|p| p.tree()).transpose()?;

        let mut diff_opts = fast_diff_opts();
        let diff = self
            .repo
            .diff_tree_to_tree(parent_tree.as_ref(), Some(&tree), Some(&mut diff_opts))?;

        let mut files_changed = Vec::new();
        diff.foreach(
            &mut |delta, _| {
                if let Some(path) = delta.new_file().path() {
                    files_changed.push(path.to_string_lossy().to_string());
                }
                true
            },
            None,
            None,
            None,
        )?;

        let stats = diff.stats()?;

        Ok(CommitInfo {
            hash: commit.id().to_string()[..12].to_string(),
            full_hash: commit.id().to_string(),
            author: author.name().unwrap_or("Unknown").to_string(),
            author_email: author.email().unwrap_or("").to_string(),
            timestamp,
            message,
            files_changed,
            insertions: stats.insertions(),
            deletions: stats.deletions(),
        })
    }

    /// Get the list of all tracked files in the repository.
    pub fn get_tracked_files(&self) -> Result<Vec<String>> {
        let head = self.repo.head()?;
        let tree = head.peel_to_tree()?;

        let mut files = Vec::new();
        tree.walk(git2::TreeWalkMode::PreOrder, |dir, entry| {
            if entry.kind() == Some(git2::ObjectType::Blob) {
                let name = entry.name().unwrap_or("");
                files.push(format!("{dir}{name}"));
            }
            git2::TreeWalkResult::Ok
        })?;

        Ok(files)
    }

    /// Get commits that modified a specific line range in a file.
    ///
    /// This is useful for finding commits that touched a specific function or class.
    pub fn get_line_range_commits(
        &self,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        max_commits: usize,
    ) -> Result<Vec<CommitInfo>> {
        // First get all commits for the file
        let file_commits = self.get_file_commits(file_path, max_commits * 2)?;

        // Filter to only commits that touched the line range
        // This requires checking the diff hunks
        let mut matching_commits = Vec::new();

        for commit in file_commits {
            if matching_commits.len() >= max_commits {
                break;
            }

            // Check if this commit touched lines in the range
            if self.commit_touches_lines(&commit.full_hash, file_path, line_start, line_end)? {
                matching_commits.push(commit);
            }
        }

        Ok(matching_commits)
    }

    /// Get file commits with pre-computed hunk line ranges.
    ///
    /// Returns `(CommitInfo, Vec<(hunk_start, hunk_end)>)` for each commit that touched the file.
    /// This avoids re-diffing per function — callers can filter by line range in-memory.
    pub fn get_file_commits_with_hunks(
        &self,
        file_path: &str,
        max_commits: usize,
    ) -> Result<Vec<(CommitInfo, Vec<(u32, u32)>)>> {
        let mut revwalk = self.repo.revwalk()?;
        revwalk.set_sorting(Sort::TIME)?;
        revwalk.push_head()?;

        let mut results = Vec::new();
        let mut diff_opts = fast_pathspec_opts(file_path);

        for oid_result in revwalk {
            if results.len() >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(oid)?;

            let parent = commit.parent(0).ok();
            let tree = commit.tree()?;
            let parent_tree = parent.as_ref().map(|p| p.tree()).transpose()?;

            let diff = self.repo.diff_tree_to_tree(
                parent_tree.as_ref(),
                Some(&tree),
                Some(&mut diff_opts),
            )?;

            if diff.deltas().len() == 0 {
                continue;
            }

            // Collect hunk ranges in a single pass
            let mut hunks = Vec::new();
            diff.foreach(
                &mut |_, _| true,
                None,
                Some(&mut |_, hunk| {
                    let start = hunk.new_start();
                    let end = start + hunk.new_lines();
                    hunks.push((start, end));
                    true
                }),
                None,
            )?;

            // Extract commit info with file-scoped stats
            let stats = diff.stats()?;
            let author = commit.author();
            let timestamp = format_git_time(&commit.time());
            let message = commit
                .message()
                .unwrap_or("")
                .lines()
                .next()
                .unwrap_or("")
                .to_string();

            let info = CommitInfo {
                hash: commit.id().to_string()[..12].to_string(),
                full_hash: commit.id().to_string(),
                author: author.name().unwrap_or("Unknown").to_string(),
                author_email: author.email().unwrap_or("").to_string(),
                timestamp,
                message,
                files_changed: vec![file_path.to_string()],
                insertions: stats.insertions(),
                deletions: stats.deletions(),
            };

            results.push((info, hunks));
        }

        Ok(results)
    }

    /// Batch-fetch commits with hunks for multiple files in a single revwalk.
    ///
    /// Instead of N separate revwalks (one per file), walks commit history once
    /// and collects hunk data for all target files simultaneously. This eliminates
    /// N-1 redundant revwalks and N-1 repository opens.
    pub fn get_batch_file_commits_with_hunks(
        &self,
        target_files: &[String],
        max_commits_per_file: usize,
    ) -> Result<HashMap<String, Vec<(CommitInfo, Vec<(u32, u32)>)>>> {
        if target_files.is_empty() {
            return Ok(HashMap::new());
        }

        let target_set: HashSet<&str> = target_files.iter().map(|s| s.as_str()).collect();
        let mut results: HashMap<String, Vec<(CommitInfo, Vec<(u32, u32)>)>> = HashMap::new();
        let mut commit_counts: HashMap<String, usize> = HashMap::new();

        let mut revwalk = self.repo.revwalk()?;
        revwalk.set_sorting(Sort::TIME)?;
        revwalk.simplify_first_parent()?;
        revwalk.push_head()?;

        let max_total_commits = 500;
        let mut saturated = 0usize;
        let total_target = target_files.len();
        let mut broad_opts = fast_diff_opts();

        for (idx, oid_result) in revwalk.enumerate() {
            if idx >= max_total_commits || saturated >= total_target {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(oid)?;

            let parent = commit.parent(0).ok();
            let tree = commit.tree()?;
            let parent_tree = parent.as_ref().map(|p| p.tree()).transpose()?;

            // Full diff to identify which target files appear in this commit
            let diff = self
                .repo
                .diff_tree_to_tree(parent_tree.as_ref(), Some(&tree), Some(&mut broad_opts))?;

            // Check deltas for target files (fast: iterates tree comparison structs)
            let mut matched: Vec<String> = Vec::new();
            for delta in diff.deltas() {
                if let Some(path) = delta.new_file().path() {
                    let p = path.to_string_lossy();
                    if target_set.contains(p.as_ref()) {
                        let count = commit_counts.get(p.as_ref()).copied().unwrap_or(0);
                        if count < max_commits_per_file {
                            matched.push(p.to_string());
                        }
                    }
                }
            }

            if matched.is_empty() {
                continue;
            }

            // Extract commit metadata once
            let author = commit.author();
            let timestamp = format_git_time(&commit.time());
            let message = commit
                .message()
                .unwrap_or("")
                .lines()
                .next()
                .unwrap_or("")
                .to_string();

            // For each matched file, pathspec diff for accurate hunks + stats
            for file_path in matched {
                let mut diff_opts = fast_pathspec_opts(file_path.as_str());

                let file_diff = self.repo.diff_tree_to_tree(
                    parent_tree.as_ref(),
                    Some(&tree),
                    Some(&mut diff_opts),
                )?;

                if file_diff.deltas().len() == 0 {
                    continue;
                }

                let mut hunks = Vec::new();
                file_diff.foreach(
                    &mut |_, _| true,
                    None,
                    Some(&mut |_, hunk| {
                        let start = hunk.new_start();
                        let end = start + hunk.new_lines();
                        hunks.push((start, end));
                        true
                    }),
                    None,
                )?;

                let stats = file_diff.stats()?;

                let info = CommitInfo {
                    hash: commit.id().to_string()[..12].to_string(),
                    full_hash: commit.id().to_string(),
                    author: author.name().unwrap_or("Unknown").to_string(),
                    author_email: author.email().unwrap_or("").to_string(),
                    timestamp: timestamp.clone(),
                    message: message.clone(),
                    files_changed: vec![file_path.clone()],
                    insertions: stats.insertions(),
                    deletions: stats.deletions(),
                };

                results.entry(file_path.clone()).or_default().push((info, hunks));

                let count = commit_counts.entry(file_path).or_default();
                *count += 1;
                if *count >= max_commits_per_file {
                    saturated += 1;
                }
            }
        }

        Ok(results)
    }

    /// Phase 1: Count how many commits touched each file using file_cb ONLY.
    ///
    /// Uses pure tree-OID comparison — zero content decompression.
    /// This is dramatically faster than using `hunk_cb` because libgit2 only
    /// compares tree entry OIDs, never decompressing blob content.
    pub fn get_file_churn_counts(&self, max_commits: usize) -> Result<HashMap<String, usize>> {
        let mut churn_counts: HashMap<String, usize> = HashMap::new();

        let mut revwalk = self.repo.revwalk()?;
        revwalk.set_sorting(Sort::TIME)?;
        revwalk.simplify_first_parent()?;
        revwalk.push_head()?;

        let mut diff_opts = fast_diff_opts();

        for (idx, oid_result) in revwalk.enumerate() {
            if idx >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(oid)?;

            // Skip commits with no parent (initial commit or shallow clone boundary).
            // Diffing against the empty tree produces ALL files as "added" —
            // no meaningful churn signal, and very expensive on large repos.
            let parent = match commit.parent(0) {
                Ok(p) => p,
                Err(_) => continue,
            };

            let tree = commit.tree()?;
            let parent_tree = parent.tree()?;

            let diff = self
                .repo
                .diff_tree_to_tree(Some(&parent_tree), Some(&tree), Some(&mut diff_opts))?;

            // Use deltas() iterator instead of foreach() — avoids FFI callback overhead.
            // Pure tree-OID comparison, no content decompression.
            for delta in diff.deltas() {
                if let Some(path) = delta.new_file().path() {
                    let path_str = path.to_string_lossy().to_string();
                    *churn_counts.entry(path_str).or_default() += 1;
                }
            }
        }

        Ok(churn_counts)
    }

    /// Phase 2: Get per-file commit history with hunk details for a specific set of paths.
    ///
    /// Uses multi-pathspec filtering so libgit2 only diffs files in `paths`,
    /// skipping tree entries for all other files. Combined with `hunk_cb` to extract
    /// per-hunk line ranges and insertion/deletion counts.
    ///
    /// `since` enables early termination — stops walking when commits are older
    /// than the cutoff (commits are time-sorted, newest first).
    pub fn get_hunks_for_paths(
        &self,
        paths: &HashSet<String>,
        max_commits: usize,
        max_commits_per_file: usize,
        since: Option<DateTime<Utc>>,
    ) -> Result<HashMap<String, Vec<(CommitInfo, Vec<HunkDetail>)>>> {
        use std::cell::RefCell;
        use std::sync::Arc;

        let mut file_commits: HashMap<String, Vec<(CommitInfo, Vec<HunkDetail>)>> = HashMap::new();
        let mut file_commit_counts: HashMap<String, usize> = HashMap::new();

        let mut revwalk = self.repo.revwalk()?;
        revwalk.set_sorting(Sort::TIME)?;
        revwalk.simplify_first_parent()?;
        revwalk.push_head()?;

        // Multi-pathspec: add each high-churn path as a literal pathspec.
        // libgit2 skips tree entries not matching any pathspec.
        let mut diff_opts = fast_diff_opts();
        for path in paths {
            diff_opts.pathspec(path);
        }
        diff_opts.disable_pathspec_match(true);

        for (idx, oid_result) in revwalk.enumerate() {
            if idx >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(oid)?;

            // Early cutoff: stop when commits are older than the analysis window
            if is_commit_before(&commit, since) {
                break;
            }

            // Skip commits with no parent (initial commit or shallow clone boundary)
            let parent = match commit.parent(0) {
                Ok(p) => p,
                Err(_) => continue,
            };
            let tree = commit.tree()?;
            let parent_tree = parent.tree()?;

            let diff = self
                .repo
                .diff_tree_to_tree(Some(&parent_tree), Some(&tree), Some(&mut diff_opts))?;

            // file_cb + hunk_cb for pathspec-filtered files only.
            // RefCell for shared state between file_cb and hunk_cb closures.
            let current_file: RefCell<Option<String>> = RefCell::new(None);
            let mut file_hunks: HashMap<String, Vec<HunkDetail>> = HashMap::new();

            diff.foreach(
                &mut |delta, _progress| {
                    *current_file.borrow_mut() = delta
                        .new_file()
                        .path()
                        .map(|p| p.to_string_lossy().to_string());
                    true
                },
                None, // binary_cb
                Some(&mut |_delta, hunk| {
                    if let Some(ref file) = *current_file.borrow() {
                        file_hunks.entry(file.clone()).or_default().push(HunkDetail {
                            new_start: hunk.new_start(),
                            new_end: hunk.new_start() + hunk.new_lines(),
                            insertions: hunk.new_lines() as usize,
                            deletions: hunk.old_lines() as usize,
                        });
                    }
                    true
                }),
                None, // no line_cb
            )?;

            if file_hunks.is_empty() {
                continue;
            }

            // Build CommitInfo once per commit, wrap in Arc so file entries share it.
            let author = commit.author();
            let timestamp = format_git_time(&commit.time());
            let message = commit
                .message()
                .unwrap_or("")
                .lines()
                .next()
                .unwrap_or("")
                .to_string();
            let hash_str = commit.id().to_string();
            let short_hash = hash_str[..12.min(hash_str.len())].to_string();

            let shared_info = Arc::new(CommitInfo {
                hash: short_hash,
                full_hash: hash_str,
                author: author.name().unwrap_or("Unknown").to_string(),
                author_email: author.email().unwrap_or("").to_string(),
                timestamp,
                message,
                files_changed: file_hunks.keys().cloned().collect(),
                insertions: 0,
                deletions: 0,
            });

            for (file_path, hunks) in file_hunks {
                let count = file_commit_counts.entry(file_path.clone()).or_default();
                if *count >= max_commits_per_file {
                    continue;
                }

                // Clone from Arc — most commits touch few high-churn files so this is minimal.
                let info = (*shared_info).clone();
                file_commits
                    .entry(file_path)
                    .or_default()
                    .push((info, hunks));
                *count += 1;
            }
        }

        Ok(file_commits)
    }

    /// Check if a commit touched specific lines in a file.
    fn commit_touches_lines(
        &self,
        commit_hash: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
    ) -> Result<bool> {
        let oid = git2::Oid::from_str(commit_hash)?;
        let commit = self.repo.find_commit(oid)?;

        let parent = commit.parent(0).ok();
        let tree = commit.tree()?;
        let parent_tree = parent.as_ref().map(|p| p.tree()).transpose()?;

        let mut diff_opts = fast_pathspec_opts(file_path);

        let diff =
            self.repo
                .diff_tree_to_tree(parent_tree.as_ref(), Some(&tree), Some(&mut diff_opts))?;

        let mut touches_lines = false;

        diff.foreach(
            &mut |_, _| true,
            None,
            Some(&mut |_, hunk| {
                // Check if hunk overlaps with our line range
                let hunk_start = hunk.new_start();
                let hunk_end = hunk_start + hunk.new_lines();

                if hunk_start <= line_end && hunk_end >= line_start {
                    touches_lines = true;
                }
                true
            }),
            None,
        )?;

        Ok(touches_lines)
    }
}

/// Format a git timestamp as ISO 8601.
fn format_git_time(time: &git2::Time) -> String {
    match Utc.timestamp_opt(time.seconds(), 0).single() {
        Some(dt) => dt.to_rfc3339(),
        None => "1970-01-01T00:00:00Z".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn create_test_repo() -> Result<(tempfile::TempDir, Repository)> {
        let dir = tempdir()?;
        let repo = Repository::init(dir.path())?;

        // Configure user for commits
        let mut config = repo.config()?;
        config.set_str("user.name", "Test User")?;
        config.set_str("user.email", "test@example.com")?;

        // Create initial commit
        {
            let sig = repo.signature()?;
            let tree_id = {
                let mut index = repo.index()?;
                std::fs::write(dir.path().join("test.txt"), "hello")?;
                index.add_path(Path::new("test.txt"))?;
                index.write()?;
                index.write_tree()?
            };
            let tree = repo.find_tree(tree_id)?;
            repo.commit(Some("HEAD"), &sig, &sig, "Initial commit", &tree, &[])?;
        }

        Ok((dir, repo))
    }

    #[test]
    fn test_open_repo() -> Result<()> {
        let (dir, _repo) = create_test_repo()?;
        let history = GitHistory::open(dir.path())?;
        assert!(history.repo_root()?.exists());
        Ok(())
    }

    #[test]
    fn test_is_git_repo() -> Result<()> {
        let (dir, _repo) = create_test_repo()?;
        assert!(GitHistory::is_git_repo(dir.path()));

        let non_repo = tempdir()?;
        assert!(!GitHistory::is_git_repo(non_repo.path()));
        Ok(())
    }

    #[test]
    fn test_get_recent_commits() -> Result<()> {
        let (dir, _repo) = create_test_repo()?;
        let history = GitHistory::open(dir.path())?;

        let commits = history.get_recent_commits(10, None)?;
        assert_eq!(commits.len(), 1);
        assert_eq!(commits[0].message, "Initial commit");
        Ok(())
    }

    #[test]
    fn test_get_file_commits() -> Result<()> {
        let (dir, _repo) = create_test_repo()?;
        let history = GitHistory::open(dir.path())?;

        let commits = history.get_file_commits("test.txt", 10)?;
        assert_eq!(commits.len(), 1);
        Ok(())
    }
}
