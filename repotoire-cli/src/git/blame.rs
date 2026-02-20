//! Git blame integration for line-level ownership tracking
//!
//! Provides functionality to determine who last modified specific lines
//! or line ranges in a file, useful for identifying function/class ownership.

use anyhow::{Context, Result};
use chrono::{TimeZone, Utc};
use dashmap::DashMap;
use git2::{BlameOptions, Repository};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::SystemTime;

/// Cached blame entry with file modification time.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CachedBlame {
    pub entries: Vec<LineBlame>,
    pub mtime_secs: u64,
}

/// Persistent git cache stored in .repotoire/git_cache.json
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct GitCache {
    pub files: HashMap<String, CachedBlame>,
}

impl GitCache {
    /// Load cache from disk.
    pub fn load(cache_path: &Path) -> Self {
        if let Ok(data) = fs::read_to_string(cache_path) {
            serde_json::from_str(&data).unwrap_or_default()
        } else {
            Self::default()
        }
    }

    /// Save cache to disk.
    pub fn save(&self, cache_path: &Path) -> Result<()> {
        let data = serde_json::to_string(self)?;
        fs::write(cache_path, data)?;
        Ok(())
    }

    /// Check if file cache is valid (mtime matches).
    pub fn is_valid(&self, file_path: &str, repo_root: &Path) -> bool {
        let Some(cached) = self.files.get(file_path) else {
            return false;
        };
        get_file_mtime_secs(repo_root.join(file_path))
            .map(|mtime| mtime == cached.mtime_secs)
            .unwrap_or(false)
    }
}

/// Get file modification time in seconds since epoch.
fn get_file_mtime_secs(path: impl AsRef<Path>) -> Option<u64> {
    fs::metadata(path.as_ref())
        .ok()?
        .modified()
        .ok()?
        .duration_since(SystemTime::UNIX_EPOCH)
        .ok()
        .map(|d| d.as_secs())
}

/// Update disk cache with new blame entries.
fn update_disk_cache(
    disk_cache: &std::sync::RwLock<GitCache>,
    file_path: &str,
    repo_path: &Path,
    entries: Vec<LineBlame>,
) {
    let Some(mtime_secs) = get_file_mtime_secs(repo_path.join(file_path)) else {
        return;
    };
    let mut dc = disk_cache
        .write()
        .expect("git disk cache lock poisoned");
    dc.files.insert(
        file_path.to_string(),
        CachedBlame {
            entries,
            mtime_secs,
        },
    );
}

/// Blame information for a single line or contiguous range.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LineBlame {
    /// Commit hash (short)
    pub commit_hash: String,
    /// Full commit hash
    pub full_hash: String,
    /// Author name
    pub author: String,
    /// Author email
    pub author_email: String,
    /// Timestamp (ISO 8601)
    pub timestamp: String,
    /// Starting line number (1-indexed)
    pub line_start: u32,
    /// Ending line number (1-indexed)
    pub line_end: u32,
    /// Number of lines in this blame block
    pub line_count: u32,
}

/// Aggregated blame information for a code entity (function/class).
#[derive(Debug, Clone, Default)]
pub struct BlameInfo {
    /// Most recent modification timestamp
    pub last_modified: Option<String>,
    /// Author of most recent modification
    pub last_author: Option<String>,
    /// Email of most recent author
    pub last_author_email: Option<String>,
    /// Most recent commit hash
    pub last_commit: Option<String>,
    /// Number of unique commits touching this entity
    pub commit_count: usize,
    /// Number of unique authors
    pub author_count: usize,
    /// List of unique authors
    pub authors: Vec<String>,
    /// Individual blame entries
    pub blame_entries: Vec<LineBlame>,
}

/// Git blame analyzer with file-level caching.
pub struct GitBlame {
    repo: Repository,
    repo_path: PathBuf,
    /// In-memory cache of file blame results
    file_cache: Arc<DashMap<String, Vec<LineBlame>>>,
    /// Persistent disk cache
    disk_cache: Arc<std::sync::RwLock<GitCache>>,
    /// Path to disk cache file
    cache_path: PathBuf,
}

impl GitBlame {
    /// Open a repository for blame analysis.
    pub fn open(path: &Path) -> Result<Self> {
        let repo = Repository::discover(path)
            .with_context(|| format!("Failed to open git repository at {:?}", path))?;
        let repo_path = repo.workdir().unwrap_or(repo.path()).to_path_buf();

        // Load disk cache from ~/.cache/repotoire/<repo>/git_cache.json
        let cache_path = crate::cache::get_git_cache_path(&repo_path);
        let disk_cache = GitCache::load(&cache_path);

        Ok(Self {
            repo,
            repo_path,
            file_cache: Arc::new(DashMap::new()),
            disk_cache: Arc::new(std::sync::RwLock::new(disk_cache)),
            cache_path,
        })
    }

    /// Save the disk cache.
    pub fn save_cache(&self) -> Result<()> {
        let cache = self
            .disk_cache
            .read()
            .expect("git disk cache lock poisoned");
        cache.save(&self.cache_path)
    }

    /// Pre-warm the blame cache in parallel for the given files.
    /// Uses disk cache for unchanged files, computes fresh for modified files.
    pub fn prewarm_cache(&self, file_paths: &[String]) -> (usize, usize) {
        let mem_cache = Arc::clone(&self.file_cache);
        let disk_cache = Arc::clone(&self.disk_cache);
        let repo_path = self.repo_path.clone();

        let cache_hits = std::sync::atomic::AtomicUsize::new(0);
        let computed = std::sync::atomic::AtomicUsize::new(0);

        file_paths.par_iter().for_each(|file_path| {
            // Skip if already in memory cache
            if mem_cache.contains_key(file_path) {
                return;
            }

            // Check disk cache first
            {
                let dc = disk_cache
                    .read()
                    .expect("git disk cache lock poisoned");
                let cached = dc
                    .is_valid(file_path, &repo_path)
                    .then(|| dc.files.get(file_path))
                    .flatten();
                if let Some(cached) = cached {
                    mem_cache.insert(file_path.clone(), cached.entries.clone());
                    cache_hits.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    return;
                }
            }

            // Compute fresh blame
            let Ok(repo) = Repository::discover(&repo_path) else {
                return;
            };
            let Ok(entries) = blame_file_with_repo(&repo, file_path) else {
                return;
            };
            mem_cache.insert(file_path.clone(), entries.clone());
            update_disk_cache(&disk_cache, file_path, &repo_path, entries);
            computed.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        });

        // Save disk cache after warming
        if let Ok(dc) = disk_cache.read() {
            let _ = dc.save(&self.cache_path);
        }

        (
            cache_hits.load(std::sync::atomic::Ordering::Relaxed),
            computed.load(std::sync::atomic::Ordering::Relaxed),
        )
    }

    /// Get blame information for a specific line range.
    ///
    /// # Arguments
    /// * `file_path` - Relative path to file within repo
    /// * `line_start` - Starting line (1-indexed)
    /// * `line_end` - Ending line (1-indexed)
    pub fn blame_lines(
        &self,
        file_path: &str,
        line_start: u32,
        line_end: u32,
    ) -> Result<Vec<LineBlame>> {
        if line_start == 0 || line_end == 0 || line_end < line_start {
            return Ok(vec![]);
        }

        let mut opts = BlameOptions::new();
        opts.min_line(line_start as usize);
        opts.max_line(line_end as usize);

        let blame = self
            .repo
            .blame_file(Path::new(file_path), Some(&mut opts))
            .with_context(|| {
                format!("Failed to blame {}:{}-{}", file_path, line_start, line_end)
            })?;

        let mut entries = Vec::new();
        let mut seen_commits: HashMap<String, LineBlame> = HashMap::new();

        for hunk in blame.iter() {
            let commit_id = hunk.final_commit_id();
            let hash = commit_id.to_string();
            let short_hash = hash[..hash.len().min(12)].to_string();

            let sig = hunk.final_signature();
            let author = sig.name().unwrap_or("Unknown").to_string();
            let email = sig.email().unwrap_or("").to_string();

            // Get commit time
            let timestamp = if let Ok(commit) = self.repo.find_commit(commit_id) {
                format_git_time(&commit.time())
            } else {
                "1970-01-01T00:00:00Z".to_string()
            };

            let line_no = hunk.final_start_line() as u32;
            let line_count = hunk.lines_in_hunk() as u32;

            // Merge consecutive hunks from same commit
            if let Some(existing) = seen_commits.get_mut(&hash) {
                existing.line_end = (line_no + line_count - 1).max(existing.line_end);
                existing.line_count = existing.line_end - existing.line_start + 1;
            } else {
                let entry = LineBlame {
                    commit_hash: short_hash,
                    full_hash: hash.clone(),
                    author,
                    author_email: email,
                    timestamp,
                    line_start: line_no,
                    line_end: line_no + line_count - 1,
                    line_count,
                };
                seen_commits.insert(hash, entry);
            }
        }

        entries.extend(seen_commits.into_values());

        // Sort by timestamp descending (most recent first)
        entries.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));

        Ok(entries)
    }

    /// Get blame information for entire file.
    pub fn blame_file(&self, file_path: &str) -> Result<Vec<LineBlame>> {
        let blame = self
            .repo
            .blame_file(Path::new(file_path), None)
            .with_context(|| format!("Failed to blame {}", file_path))?;

        let mut entries = Vec::new();
        let mut current_hash: Option<String> = None;
        let mut current_entry: Option<LineBlame> = None;

        for hunk in blame.iter() {
            let commit_id = hunk.final_commit_id();
            let hash = commit_id.to_string();
            let short_hash = hash[..hash.len().min(12)].to_string();

            let sig = hunk.final_signature();
            let author = sig.name().unwrap_or("Unknown").to_string();
            let email = sig.email().unwrap_or("").to_string();

            let timestamp = if let Ok(commit) = self.repo.find_commit(commit_id) {
                format_git_time(&commit.time())
            } else {
                "1970-01-01T00:00:00Z".to_string()
            };

            let line_no = hunk.final_start_line() as u32;
            let line_count = hunk.lines_in_hunk() as u32;

            // Merge consecutive hunks from same commit
            if current_hash.as_ref() == Some(&hash) {
                if let Some(ref mut entry) = current_entry {
                    entry.line_end = line_no + line_count - 1;
                    entry.line_count = entry.line_end - entry.line_start + 1;
                    continue;
                }
            }

            // Save previous entry
            if let Some(entry) = current_entry.take() {
                entries.push(entry);
            }

            // Start new entry
            current_hash = Some(hash.clone());
            current_entry = Some(LineBlame {
                commit_hash: short_hash,
                full_hash: hash,
                author,
                author_email: email,
                timestamp,
                line_start: line_no,
                line_end: line_no + line_count - 1,
                line_count,
            });
        }

        // Don't forget the last entry
        if let Some(entry) = current_entry {
            entries.push(entry);
        }

        Ok(entries)
    }

    /// Get cached blame for entire file, or compute and cache it.
    fn get_cached_file_blame(&self, file_path: &str) -> Result<Vec<LineBlame>> {
        // Check cache first
        if let Some(cached) = self.file_cache.get(file_path) {
            return Ok(cached.clone());
        }

        // Compute and cache
        let entries = self.blame_file(file_path)?;
        self.file_cache
            .insert(file_path.to_string(), entries.clone());
        Ok(entries)
    }

    /// Get aggregated blame information for an entity (function/class).
    /// Uses cached file blame for efficiency.
    ///
    /// # Arguments
    /// * `file_path` - Relative path to file
    /// * `line_start` - Entity starting line (1-indexed)
    /// * `line_end` - Entity ending line (1-indexed)
    pub fn get_entity_blame(
        &self,
        file_path: &str,
        line_start: u32,
        line_end: u32,
    ) -> Result<BlameInfo> {
        // Use cached full-file blame and filter to line range
        let all_entries = self.get_cached_file_blame(file_path)?;
        let blame_entries: Vec<LineBlame> = all_entries
            .into_iter()
            .filter(|e| e.line_end >= line_start && e.line_start <= line_end)
            .collect();

        if blame_entries.is_empty() {
            return Ok(BlameInfo::default());
        }

        // Find most recent
        let most_recent = blame_entries
            .iter()
            .max_by(|a, b| a.timestamp.cmp(&b.timestamp));

        // Collect unique authors and commits
        let mut authors_set = std::collections::HashSet::new();
        let mut commits_set = std::collections::HashSet::new();

        for entry in &blame_entries {
            authors_set.insert(entry.author.clone());
            commits_set.insert(entry.commit_hash.clone());
        }

        let authors: Vec<String> = authors_set.into_iter().collect();

        Ok(BlameInfo {
            last_modified: most_recent.map(|e| e.timestamp.clone()),
            last_author: most_recent.map(|e| e.author.clone()),
            last_author_email: most_recent.map(|e| e.author_email.clone()),
            last_commit: most_recent.map(|e| e.commit_hash.clone()),
            commit_count: commits_set.len(),
            author_count: authors.len(),
            authors,
            blame_entries,
        })
    }

    /// Get ownership information - who "owns" a file based on lines written.
    ///
    /// Returns a map of author -> percentage of lines they wrote.
    pub fn get_file_ownership(&self, file_path: &str) -> Result<HashMap<String, f64>> {
        let blame_entries = self.blame_file(file_path)?;

        let total_lines: u32 = blame_entries.iter().map(|e| e.line_count).sum();
        if total_lines == 0 {
            return Ok(HashMap::new());
        }

        let mut author_lines: HashMap<String, u32> = HashMap::new();
        for entry in blame_entries {
            *author_lines.entry(entry.author).or_insert(0) += entry.line_count;
        }

        let ownership: HashMap<String, f64> = author_lines
            .into_iter()
            .map(|(author, lines)| (author, (lines as f64 / total_lines as f64) * 100.0))
            .collect();

        Ok(ownership)
    }
}

/// Blame a file using a provided repository (for parallel pre-warming).
fn blame_file_with_repo(repo: &Repository, file_path: &str) -> Result<Vec<LineBlame>> {
    let blame = repo
        .blame_file(Path::new(file_path), None)
        .with_context(|| format!("Failed to blame {}", file_path))?;

    let mut entries = Vec::new();
    let mut current_hash: Option<String> = None;
    let mut current_entry: Option<LineBlame> = None;

    for hunk in blame.iter() {
        let commit_id = hunk.final_commit_id();
        let hash = commit_id.to_string();
        let short_hash = hash[..hash.len().min(12)].to_string();

        let sig = hunk.final_signature();
        let author = sig.name().unwrap_or("Unknown").to_string();
        let email = sig.email().unwrap_or("").to_string();

        let timestamp = if let Ok(commit) = repo.find_commit(commit_id) {
            format_git_time(&commit.time())
        } else {
            "1970-01-01T00:00:00Z".to_string()
        };

        let line_no = hunk.final_start_line() as u32;
        let line_count = hunk.lines_in_hunk() as u32;

        // Merge consecutive hunks from same commit
        if current_hash.as_ref() == Some(&hash) {
            if let Some(ref mut entry) = current_entry {
                entry.line_end = line_no + line_count - 1;
                entry.line_count = entry.line_end - entry.line_start + 1;
                continue;
            }
        }

        // Save previous entry
        if let Some(entry) = current_entry.take() {
            entries.push(entry);
        }

        // Start new entry
        current_hash = Some(hash.clone());
        current_entry = Some(LineBlame {
            commit_hash: short_hash,
            full_hash: hash,
            author,
            author_email: email,
            timestamp,
            line_start: line_no,
            line_end: line_no + line_count - 1,
            line_count,
        });
    }

    // Don't forget the last entry
    if let Some(entry) = current_entry {
        entries.push(entry);
    }

    Ok(entries)
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
    use std::fs;
    use tempfile::tempdir;

    fn create_test_repo_with_file() -> Result<(tempfile::TempDir, Repository)> {
        let dir = tempdir()?;
        let repo = Repository::init(dir.path())?;

        // Configure user
        let mut config = repo.config()?;
        config.set_str("user.name", "Test User")?;
        config.set_str("user.email", "test@example.com")?;

        // Create file with multiple lines
        let content = "line 1\nline 2\nline 3\nline 4\nline 5\n";
        fs::write(dir.path().join("test.py"), content)?;

        // Commit
        {
            let sig = repo.signature()?;
            let tree_id = {
                let mut index = repo.index()?;
                index.add_path(Path::new("test.py"))?;
                index.write()?;
                index.write_tree()?
            };
            let tree = repo.find_tree(tree_id)?;
            repo.commit(Some("HEAD"), &sig, &sig, "Add test file", &tree, &[])?;
        }

        Ok((dir, repo))
    }

    #[test]
    fn test_blame_file() -> Result<()> {
        let (dir, _repo) = create_test_repo_with_file()?;
        let blame = GitBlame::open(dir.path())?;

        let entries = blame.blame_file("test.py")?;
        assert!(!entries.is_empty());
        assert_eq!(entries[0].author, "Test User");
        Ok(())
    }

    #[test]
    fn test_blame_lines() -> Result<()> {
        let (dir, _repo) = create_test_repo_with_file()?;
        let blame = GitBlame::open(dir.path())?;

        let entries = blame.blame_lines("test.py", 2, 4)?;
        assert!(!entries.is_empty());
        Ok(())
    }

    #[test]
    fn test_entity_blame() -> Result<()> {
        let (dir, _repo) = create_test_repo_with_file()?;
        let blame = GitBlame::open(dir.path())?;

        let info = blame.get_entity_blame("test.py", 1, 5)?;
        assert!(info.last_modified.is_some());
        assert_eq!(info.last_author, Some("Test User".to_string()));
        assert_eq!(info.commit_count, 1);
        Ok(())
    }

    #[test]
    fn test_file_ownership() -> Result<()> {
        let (dir, _repo) = create_test_repo_with_file()?;
        let blame = GitBlame::open(dir.path())?;

        let ownership = blame.get_file_ownership("test.py")?;
        assert!(ownership.contains_key("Test User"));
        assert!((ownership["Test User"] - 100.0).abs() < 0.01);
        Ok(())
    }
}
