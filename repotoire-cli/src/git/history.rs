//! Git history extraction using libgit2
//!
//! Extracts commit history, calculates churn metrics, and tracks file changes
//! over time using the git2 crate (Rust bindings to libgit2).

use anyhow::{Context, Result};
use chrono::{DateTime, TimeZone, Utc};
use git2::{DiffOptions, Repository, Sort};
use std::collections::HashMap;
use std::path::Path;
use tracing::debug;

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

/// Git history analyzer using libgit2.
pub struct GitHistory {
    repo: Repository,
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
        let _file_path_normalized = Path::new(file_path);

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

            let mut diff_opts = DiffOptions::new();
            diff_opts.pathspec(file_path);

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

            // Filter by timestamp if specified
            if let Some(since_ts) = since {
                let commit_time = commit.time();
                let commit_dt = Utc.timestamp_opt(commit_time.seconds(), 0).single();
                if commit_dt.is_some_and(|dt| dt < since_ts) {
                    break; // Commits are sorted by time, so we can stop
                }
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
        revwalk.push_head()?;

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
                .diff_tree_to_tree(parent_tree.as_ref(), Some(&tree), None)?;

            let author = commit.author().name().unwrap_or("Unknown").to_string();
            let timestamp = format_git_time(&commit.time());

            // Process each file in the diff
            diff.foreach(
                &mut |delta, _| {
                    if let Some(path) = delta.new_file().path() {
                        let path_str = path.to_string_lossy().to_string();
                        let entry = churn_map.entry(path_str).or_default();
                        entry.commit_count += 1;

                        // Track authors
                        if !entry.authors.contains(&author) {
                            entry.authors.push(author.clone());
                        }

                        // Update last modified if this is newer
                        if entry.last_modified.is_none() {
                            entry.last_modified = Some(timestamp.clone());
                            entry.last_author = Some(author.clone());
                        }
                    }
                    true
                },
                None,
                None,
                None,
            )?;

            // Get line stats
            let _stats = diff.stats()?;
            // Note: Per-file line stats require iterating patches, done in get_commit_file_stats
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

        let mut diff_opts = DiffOptions::new();
        diff_opts.pathspec(file_path);

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

        let diff = self
            .repo
            .diff_tree_to_tree(parent_tree.as_ref(), Some(&tree), None)?;

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
                let path = if dir.is_empty() {
                    entry.name().unwrap_or("").to_string()
                } else {
                    format!("{}{}", dir, entry.name().unwrap_or(""))
                };
                files.push(path);
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

        let mut diff_opts = DiffOptions::new();
        diff_opts.pathspec(file_path);

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
