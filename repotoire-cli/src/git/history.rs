//! Git history extraction using raw git implementation
//!
//! Extracts commit history, calculates churn metrics, and tracks file changes
//! over time using the hand-rolled `crate::git::raw` module (pure Rust, no libgit2).

use anyhow::{Context, Result};
use chrono::{DateTime, TimeZone, Utc};
use std::collections::{HashMap, HashSet};
use std::path::Path;
use tracing::debug;

use crate::git::raw::{
    compute_stats, diff_blobs, diff_trees, DiffStatus, Oid, RawRepo, RevWalk,
};

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

/// Git history analyzer using raw git implementation.
pub struct GitHistory {
    repo: RawRepo,
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

/// Check whether a commit's committer_time is before the given timestamp cutoff
fn is_commit_before(committer_time: i64, since: Option<DateTime<Utc>>) -> bool {
    let Some(since_ts) = since else { return false };
    Utc.timestamp_opt(committer_time, 0)
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
        let repo = RawRepo::discover(path)
            .with_context(|| format!("Failed to open git repository at {:?}", path))?;
        debug!("Opened git repository at {:?}", repo.git_dir());
        Ok(Self { repo })
    }

    /// Check if a path is inside a git repository.
    pub fn is_git_repo(path: &Path) -> bool {
        RawRepo::discover(path).is_ok()
    }

    /// Get the repository root path.
    pub fn repo_root(&self) -> Result<&Path> {
        Ok(self.repo.workdir())
    }

    /// Get commit history for a specific file.
    ///
    /// # Arguments
    /// * `file_path` - Relative path to file within repo
    /// * `max_commits` - Maximum number of commits to retrieve
    pub fn get_file_commits(&self, file_path: &str, max_commits: usize) -> Result<Vec<CommitInfo>> {
        let mut revwalk = RevWalk::new(&self.repo);
        revwalk.push_head()?;

        let mut commits = Vec::new();
        let pathspecs = vec![file_path.to_string()];

        for oid_result in revwalk {
            if commits.len() >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(&oid)?;

            // Get parent tree OID (or ZERO for root commit)
            let parent_tree_oid = if let Some(parent_oid) = commit.parents.first() {
                let parent = self.repo.find_commit(parent_oid)?;
                parent.tree_oid
            } else {
                Oid::ZERO
            };

            let deltas = diff_trees(&self.repo, &parent_tree_oid, &commit.tree_oid, &pathspecs)?;

            // Skip if no changes to this file
            if deltas.is_empty() {
                continue;
            }

            let commit_info = self.extract_commit_info(&oid, &commit)?;
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
        let mut revwalk = RevWalk::new(&self.repo);
        revwalk.push_head()?;

        let mut commits = Vec::new();

        for oid_result in revwalk {
            if commits.len() >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(&oid)?;

            // Filter by timestamp if specified — commits are sorted by time, so stop early
            if is_commit_before(commit.committer_time, since) {
                break;
            }

            let commit_info = self.extract_commit_info(&oid, &commit)?;
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

        let mut revwalk = RevWalk::new(&self.repo);
        revwalk.simplify_first_parent();
        revwalk.push_head()?;

        for (commit_count, oid_result) in revwalk.enumerate() {
            if commit_count >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(&oid)?;

            // Get parent tree OID (or ZERO for root commit)
            let parent_tree_oid = if let Some(parent_oid) = commit.parents.first() {
                let parent = self.repo.find_commit(parent_oid)?;
                parent.tree_oid
            } else {
                Oid::ZERO
            };

            let deltas = diff_trees(&self.repo, &parent_tree_oid, &commit.tree_oid, &[])?;

            let author = commit.author_name.clone();
            let timestamp = format_epoch_time(commit.committer_time);

            // Process each file in the diff
            for delta in &deltas {
                process_diff_delta(&mut churn_map, delta.new_path.clone(), &author, &timestamp);
            }
        }

        Ok(churn_map)
    }

    /// Get line change stats for a specific file in a specific commit.
    fn get_commit_file_stats(&self, commit_hash: &str, file_path: &str) -> Result<(usize, usize)> {
        let oid = Oid::from_hex(commit_hash)?;
        let commit = self.repo.find_commit(&oid)?;

        let parent_tree_oid = if let Some(parent_oid) = commit.parents.first() {
            let parent = self.repo.find_commit(parent_oid)?;
            parent.tree_oid
        } else {
            Oid::ZERO
        };

        let pathspecs = vec![file_path.to_string()];
        let deltas = diff_trees(&self.repo, &parent_tree_oid, &commit.tree_oid, &pathspecs)?;

        let mut insertions = 0usize;
        let mut deletions = 0usize;

        for delta in &deltas {
            match delta.status {
                DiffStatus::Added => {
                    if let Ok(blob) = self.repo.find_blob(&delta.new_oid) {
                        let lines = blob.split(|&b| b == b'\n').count();
                        insertions += lines;
                    }
                }
                DiffStatus::Deleted => {
                    if let Ok(blob) = self.repo.find_blob(&delta.old_oid) {
                        let lines = blob.split(|&b| b == b'\n').count();
                        deletions += lines;
                    }
                }
                DiffStatus::Modified => {
                    let old_blob = self.repo.find_blob(&delta.old_oid).unwrap_or_default();
                    let new_blob = self.repo.find_blob(&delta.new_oid).unwrap_or_default();
                    let hunks = diff_blobs(&old_blob, &new_blob);
                    let stats = compute_stats(&hunks);
                    insertions += stats.insertions;
                    deletions += stats.deletions;
                }
            }
        }

        Ok((insertions, deletions))
    }

    /// Extract commit information from a RawCommit.
    fn extract_commit_info(
        &self,
        oid: &Oid,
        commit: &crate::git::raw::RawCommit,
    ) -> Result<CommitInfo> {
        let timestamp = format_epoch_time(commit.committer_time);
        let message = commit
            .message
            .lines()
            .next()
            .unwrap_or("")
            .to_string();

        // Get changed files
        let parent_tree_oid = if let Some(parent_oid) = commit.parents.first() {
            let parent = self.repo.find_commit(parent_oid)?;
            parent.tree_oid
        } else {
            Oid::ZERO
        };

        let deltas = diff_trees(&self.repo, &parent_tree_oid, &commit.tree_oid, &[])?;

        let files_changed: Vec<String> = deltas.iter().map(|d| d.new_path.clone()).collect();

        // Compute stats
        let mut total_insertions = 0usize;
        let mut total_deletions = 0usize;
        for delta in &deltas {
            match delta.status {
                DiffStatus::Added => {
                    if let Ok(blob) = self.repo.find_blob(&delta.new_oid) {
                        total_insertions += blob.split(|&b| b == b'\n').count();
                    }
                }
                DiffStatus::Deleted => {
                    if let Ok(blob) = self.repo.find_blob(&delta.old_oid) {
                        total_deletions += blob.split(|&b| b == b'\n').count();
                    }
                }
                DiffStatus::Modified => {
                    let old_blob = self.repo.find_blob(&delta.old_oid).unwrap_or_default();
                    let new_blob = self.repo.find_blob(&delta.new_oid).unwrap_or_default();
                    let hunks = diff_blobs(&old_blob, &new_blob);
                    let stats = compute_stats(&hunks);
                    total_insertions += stats.insertions;
                    total_deletions += stats.deletions;
                }
            }
        }

        let hex = oid.to_hex();
        Ok(CommitInfo {
            hash: hex[..12].to_string(),
            full_hash: hex,
            author: commit.author_name.clone(),
            author_email: commit.author_email.clone(),
            timestamp,
            message,
            files_changed,
            insertions: total_insertions,
            deletions: total_deletions,
        })
    }

    /// Get the list of all tracked files in the repository.
    pub fn get_tracked_files(&self) -> Result<Vec<String>> {
        let (_tree_oid, entries) = self.repo.head_tree()?;

        let mut files = Vec::new();
        self.collect_tree_files(&entries, "", &mut files)?;
        Ok(files)
    }

    /// Recursively collect file paths from tree entries.
    fn collect_tree_files(
        &self,
        entries: &[crate::git::raw::TreeEntry],
        prefix: &str,
        files: &mut Vec<String>,
    ) -> Result<()> {
        for entry in entries {
            let path = if prefix.is_empty() {
                entry.name.clone()
            } else {
                format!("{prefix}{}", entry.name)
            };

            if entry.is_submodule() {
                continue;
            }

            if entry.is_tree() {
                let sub_entries = self.repo.find_tree(&entry.oid)?;
                self.collect_tree_files(&sub_entries, &format!("{path}/"), files)?;
            } else {
                files.push(path);
            }
        }
        Ok(())
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
        let mut revwalk = RevWalk::new(&self.repo);
        revwalk.push_head()?;

        let mut results = Vec::new();
        let pathspecs = vec![file_path.to_string()];

        for oid_result in revwalk {
            if results.len() >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(&oid)?;

            let parent_tree_oid = if let Some(parent_oid) = commit.parents.first() {
                let parent_commit = self.repo.find_commit(parent_oid)?;
                parent_commit.tree_oid
            } else {
                Oid::ZERO
            };

            let deltas =
                diff_trees(&self.repo, &parent_tree_oid, &commit.tree_oid, &pathspecs)?;

            if deltas.is_empty() {
                continue;
            }

            // Collect hunk ranges from blob diffs
            let mut hunks = Vec::new();
            let mut total_insertions = 0usize;
            let mut total_deletions = 0usize;

            for delta in &deltas {
                match delta.status {
                    DiffStatus::Added => {
                        if let Ok(blob) = self.repo.find_blob(&delta.new_oid) {
                            let lines = blob.split(|&b| b == b'\n').count();
                            hunks.push((1u32, lines as u32 + 1));
                            total_insertions += lines;
                        }
                    }
                    DiffStatus::Deleted => {
                        if let Ok(blob) = self.repo.find_blob(&delta.old_oid) {
                            let lines = blob.split(|&b| b == b'\n').count();
                            total_deletions += lines;
                        }
                    }
                    DiffStatus::Modified => {
                        let old_blob = self.repo.find_blob(&delta.old_oid).unwrap_or_default();
                        let new_blob = self.repo.find_blob(&delta.new_oid).unwrap_or_default();
                        let diff_hunks = diff_blobs(&old_blob, &new_blob);
                        let stats = compute_stats(&diff_hunks);
                        total_insertions += stats.insertions;
                        total_deletions += stats.deletions;
                        for h in &diff_hunks {
                            let start = h.new_start as u32;
                            let end = start + h.new_lines as u32;
                            hunks.push((start, end));
                        }
                    }
                }
            }

            let hex = oid.to_hex();
            let timestamp = format_epoch_time(commit.committer_time);
            let message = commit
                .message
                .lines()
                .next()
                .unwrap_or("")
                .to_string();

            let info = CommitInfo {
                hash: hex[..12].to_string(),
                full_hash: hex,
                author: commit.author_name.clone(),
                author_email: commit.author_email.clone(),
                timestamp,
                message,
                files_changed: vec![file_path.to_string()],
                insertions: total_insertions,
                deletions: total_deletions,
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

        let mut revwalk = RevWalk::new(&self.repo);
        revwalk.simplify_first_parent();
        revwalk.push_head()?;

        let max_total_commits = 500;
        let mut saturated = 0usize;
        let total_target = target_files.len();

        for (idx, oid_result) in revwalk.enumerate() {
            if idx >= max_total_commits || saturated >= total_target {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(&oid)?;

            let parent_tree_oid = if let Some(parent_oid) = commit.parents.first() {
                let parent_commit = self.repo.find_commit(parent_oid)?;
                parent_commit.tree_oid
            } else {
                Oid::ZERO
            };

            // Full diff to identify which target files appear in this commit
            let deltas = diff_trees(&self.repo, &parent_tree_oid, &commit.tree_oid, &[])?;

            // Check deltas for target files
            let mut matched: Vec<String> = Vec::new();
            for delta in &deltas {
                let p = &delta.new_path;
                if target_set.contains(p.as_str()) {
                    let count = commit_counts.get(p.as_str()).copied().unwrap_or(0);
                    if count < max_commits_per_file {
                        matched.push(p.clone());
                    }
                }
            }

            if matched.is_empty() {
                continue;
            }

            // Extract commit metadata once
            let hex = oid.to_hex();
            let timestamp = format_epoch_time(commit.committer_time);
            let message = commit
                .message
                .lines()
                .next()
                .unwrap_or("")
                .to_string();

            // For each matched file, pathspec diff for accurate hunks + stats
            for file_path in matched {
                let pathspecs = vec![file_path.clone()];
                let file_deltas = diff_trees(
                    &self.repo,
                    &parent_tree_oid,
                    &commit.tree_oid,
                    &pathspecs,
                )?;

                if file_deltas.is_empty() {
                    continue;
                }

                let mut hunks = Vec::new();
                let mut total_insertions = 0usize;
                let mut total_deletions = 0usize;

                for delta in &file_deltas {
                    match delta.status {
                        DiffStatus::Added => {
                            if let Ok(blob) = self.repo.find_blob(&delta.new_oid) {
                                let lines = blob.split(|&b| b == b'\n').count();
                                hunks.push((1u32, lines as u32 + 1));
                                total_insertions += lines;
                            }
                        }
                        DiffStatus::Deleted => {
                            if let Ok(blob) = self.repo.find_blob(&delta.old_oid) {
                                let lines = blob.split(|&b| b == b'\n').count();
                                total_deletions += lines;
                            }
                        }
                        DiffStatus::Modified => {
                            let old_blob =
                                self.repo.find_blob(&delta.old_oid).unwrap_or_default();
                            let new_blob =
                                self.repo.find_blob(&delta.new_oid).unwrap_or_default();
                            let diff_hunks = diff_blobs(&old_blob, &new_blob);
                            let stats = compute_stats(&diff_hunks);
                            total_insertions += stats.insertions;
                            total_deletions += stats.deletions;
                            for h in &diff_hunks {
                                let start = h.new_start as u32;
                                let end = start + h.new_lines as u32;
                                hunks.push((start, end));
                            }
                        }
                    }
                }

                let info = CommitInfo {
                    hash: hex[..12].to_string(),
                    full_hash: hex.clone(),
                    author: commit.author_name.clone(),
                    author_email: commit.author_email.clone(),
                    timestamp: timestamp.clone(),
                    message: message.clone(),
                    files_changed: vec![file_path.clone()],
                    insertions: total_insertions,
                    deletions: total_deletions,
                };

                results
                    .entry(file_path.clone())
                    .or_default()
                    .push((info, hunks));

                let count = commit_counts.entry(file_path).or_default();
                *count += 1;
                if *count >= max_commits_per_file {
                    saturated += 1;
                }
            }
        }

        Ok(results)
    }

    /// Phase 1: Count how many commits touched each file using tree-OID comparison ONLY.
    ///
    /// Uses pure tree-OID comparison — zero content decompression.
    /// This is dramatically faster than computing blob diffs because we only
    /// compare tree entry OIDs, never decompressing blob content.
    pub fn get_file_churn_counts(&self, max_commits: usize) -> Result<HashMap<String, usize>> {
        let mut churn_counts: HashMap<String, usize> = HashMap::new();

        let mut revwalk = RevWalk::new(&self.repo);
        revwalk.simplify_first_parent();
        revwalk.push_head()?;

        for (idx, oid_result) in revwalk.enumerate() {
            if idx >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(&oid)?;

            // Skip commits with no parent (initial commit or shallow clone boundary).
            // Diffing against the empty tree produces ALL files as "added" —
            // no meaningful churn signal, and very expensive on large repos.
            let parent_oid = match commit.parents.first() {
                Some(p) => p,
                None => continue,
            };

            let parent = self.repo.find_commit(parent_oid)?;

            let deltas = diff_trees(&self.repo, &parent.tree_oid, &commit.tree_oid, &[])?;

            // Pure tree-OID comparison, no content decompression.
            for delta in &deltas {
                *churn_counts.entry(delta.new_path.clone()).or_default() += 1;
            }
        }

        Ok(churn_counts)
    }

    /// Phase 2: Get per-file commit history with hunk details for a specific set of paths.
    ///
    /// Uses pathspec filtering so only files in `paths` are diffed,
    /// skipping tree entries for all other files. Combined with blob diffing
    /// to extract per-hunk line ranges and insertion/deletion counts.
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
        use std::sync::Arc;

        let mut file_commits: HashMap<String, Vec<(CommitInfo, Vec<HunkDetail>)>> = HashMap::new();
        let mut file_commit_counts: HashMap<String, usize> = HashMap::new();

        let mut revwalk = RevWalk::new(&self.repo);
        revwalk.simplify_first_parent();
        revwalk.push_head()?;

        let pathspecs: Vec<String> = paths.iter().cloned().collect();

        for (idx, oid_result) in revwalk.enumerate() {
            if idx >= max_commits {
                break;
            }

            let oid = oid_result?;
            let commit = self.repo.find_commit(&oid)?;

            // Early cutoff: stop when commits are older than the analysis window
            if is_commit_before(commit.committer_time, since) {
                break;
            }

            // Skip commits with no parent (initial commit or shallow clone boundary)
            let parent_oid = match commit.parents.first() {
                Some(p) => p,
                None => continue,
            };
            let parent = self.repo.find_commit(parent_oid)?;

            let deltas = diff_trees(
                &self.repo,
                &parent.tree_oid,
                &commit.tree_oid,
                &pathspecs,
            )?;

            if deltas.is_empty() {
                continue;
            }

            // For each delta, compute hunks via blob diff
            let mut file_hunks: HashMap<String, Vec<HunkDetail>> = HashMap::new();

            for delta in &deltas {
                let file = delta.new_path.clone();
                match delta.status {
                    DiffStatus::Added => {
                        if let Ok(blob) = self.repo.find_blob(&delta.new_oid) {
                            let lines = blob.split(|&b| b == b'\n').count();
                            file_hunks
                                .entry(file)
                                .or_default()
                                .push(HunkDetail {
                                    new_start: 1,
                                    new_end: lines as u32 + 1,
                                    insertions: lines,
                                    deletions: 0,
                                });
                        }
                    }
                    DiffStatus::Deleted => {
                        if let Ok(blob) = self.repo.find_blob(&delta.old_oid) {
                            let lines = blob.split(|&b| b == b'\n').count();
                            file_hunks
                                .entry(file)
                                .or_default()
                                .push(HunkDetail {
                                    new_start: 1,
                                    new_end: 1,
                                    insertions: 0,
                                    deletions: lines,
                                });
                        }
                    }
                    DiffStatus::Modified => {
                        let old_blob = self.repo.find_blob(&delta.old_oid).unwrap_or_default();
                        let new_blob = self.repo.find_blob(&delta.new_oid).unwrap_or_default();
                        let diff_hunks = diff_blobs(&old_blob, &new_blob);
                        for h in &diff_hunks {
                            file_hunks
                                .entry(file.clone())
                                .or_default()
                                .push(HunkDetail {
                                    new_start: h.new_start as u32,
                                    new_end: h.new_start as u32 + h.new_lines as u32,
                                    insertions: h.new_lines,
                                    deletions: h.old_lines,
                                });
                        }
                    }
                }
            }

            if file_hunks.is_empty() {
                continue;
            }

            // Build CommitInfo once per commit, wrap in Arc so file entries share it.
            let hex = oid.to_hex();
            let timestamp = format_epoch_time(commit.committer_time);
            let message = commit
                .message
                .lines()
                .next()
                .unwrap_or("")
                .to_string();
            let short_hash = hex[..12.min(hex.len())].to_string();

            let shared_info = Arc::new(CommitInfo {
                hash: short_hash,
                full_hash: hex,
                author: commit.author_name.clone(),
                author_email: commit.author_email.clone(),
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
        let oid = Oid::from_hex(commit_hash)?;
        let commit = self.repo.find_commit(&oid)?;

        let parent_tree_oid = if let Some(parent_oid) = commit.parents.first() {
            let parent = self.repo.find_commit(parent_oid)?;
            parent.tree_oid
        } else {
            Oid::ZERO
        };

        let pathspecs = vec![file_path.to_string()];
        let deltas = diff_trees(&self.repo, &parent_tree_oid, &commit.tree_oid, &pathspecs)?;

        for delta in &deltas {
            match delta.status {
                DiffStatus::Added => {
                    // Entire file is new — overlaps with any range
                    return Ok(true);
                }
                DiffStatus::Deleted => {
                    // Entire file deleted — overlaps with any range
                    return Ok(true);
                }
                DiffStatus::Modified => {
                    let old_blob = self.repo.find_blob(&delta.old_oid).unwrap_or_default();
                    let new_blob = self.repo.find_blob(&delta.new_oid).unwrap_or_default();
                    let hunks = diff_blobs(&old_blob, &new_blob);
                    for h in &hunks {
                        let hunk_start = h.new_start as u32;
                        let hunk_end = hunk_start + h.new_lines as u32;
                        if hunk_start <= line_end && hunk_end >= line_start {
                            return Ok(true);
                        }
                    }
                }
            }
        }

        Ok(false)
    }
}

/// Format an epoch timestamp (seconds since Unix epoch) as ISO 8601.
fn format_epoch_time(secs: i64) -> String {
    match Utc.timestamp_opt(secs, 0).single() {
        Some(dt) => dt.to_rfc3339(),
        None => "1970-01-01T00:00:00Z".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;
    use tempfile::tempdir;

    fn create_test_repo() -> Result<tempfile::TempDir> {
        let dir = tempdir()?;

        let run = |args: &[&str]| {
            Command::new("git")
                .args(args)
                .current_dir(dir.path())
                .env("GIT_AUTHOR_NAME", "Test User")
                .env("GIT_AUTHOR_EMAIL", "test@example.com")
                .env("GIT_COMMITTER_NAME", "Test User")
                .env("GIT_COMMITTER_EMAIL", "test@example.com")
                .output()
                .expect("git command failed")
        };

        run(&["init"]);
        run(&["config", "user.name", "Test User"]);
        run(&["config", "user.email", "test@example.com"]);

        // Create initial commit
        std::fs::write(dir.path().join("test.txt"), "hello")?;
        run(&["add", "test.txt"]);
        run(&["commit", "-m", "Initial commit"]);

        Ok(dir)
    }

    #[test]
    fn test_open_repo() -> Result<()> {
        let dir = create_test_repo()?;
        let history = GitHistory::open(dir.path())?;
        assert!(history.repo_root()?.exists());
        Ok(())
    }

    #[test]
    fn test_is_git_repo() -> Result<()> {
        let dir = create_test_repo()?;
        assert!(GitHistory::is_git_repo(dir.path()));

        let non_repo = tempdir()?;
        assert!(!GitHistory::is_git_repo(non_repo.path()));
        Ok(())
    }

    #[test]
    fn test_get_recent_commits() -> Result<()> {
        let dir = create_test_repo()?;
        let history = GitHistory::open(dir.path())?;

        let commits = history.get_recent_commits(10, None)?;
        assert_eq!(commits.len(), 1);
        assert_eq!(commits[0].message, "Initial commit");
        Ok(())
    }

    #[test]
    fn test_get_file_commits() -> Result<()> {
        let dir = create_test_repo()?;
        let history = GitHistory::open(dir.path())?;

        let commits = history.get_file_commits("test.txt", 10)?;
        assert_eq!(commits.len(), 1);
        Ok(())
    }
}
