//! Git blame integration for line-level ownership tracking
//!
//! Provides functionality to determine who last modified specific lines
//! or line ranges in a file, useful for identifying function/class ownership.

use anyhow::{Context, Result};
use chrono::{TimeZone, Utc};
use dashmap::DashMap;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use crate::git::raw::{blame_file as raw_blame_file, RawRepo};

/// Cached blame entry keyed by file content hash.
///
/// Mtime was previously used for validity but mtime changes on any file touch —
/// editor save, build system `touch`, git checkout — even when content is
/// identical, so the cache invalidated spuriously and warm runs did no better
/// than cold. Content hashing (xxh3) is the same scheme `IncrementalCache`
/// uses for findings, which behaves correctly.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CachedBlame {
    pub entries: Vec<LineBlame>,
    #[serde(default)]
    pub content_hash: u64,
}

/// Persistent git cache stored in ~/.cache/repotoire/<repo>/git_cache.json
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

    /// Check if file cache is valid (content hash matches).
    pub fn is_valid(&self, file_path: &str, repo_root: &Path) -> bool {
        let Some(cached) = self.files.get(file_path) else {
            return false;
        };
        // `content_hash == 0` indicates an old-schema entry (pre-content-hash
        // migration). Treat as invalid so the entry gets refreshed rather
        // than trusted with no validation.
        if cached.content_hash == 0 {
            return false;
        }
        hash_file_content(repo_root.join(file_path))
            .map(|h| h == cached.content_hash)
            .unwrap_or(false)
    }
}

/// xxh3 hash of the file's current on-disk contents. Fast (~5 GB/s on ARM64
/// per xxhash-rust benchmarks) and already a workspace dependency used by
/// `IncrementalCache::file_hash`.
fn hash_file_content(path: impl AsRef<Path>) -> Option<u64> {
    let bytes = fs::read(path.as_ref()).ok()?;
    Some(xxhash_rust::xxh3::xxh3_64(&bytes))
}

/// Update disk cache with new blame entries, keyed by content hash.
fn update_disk_cache(
    disk_cache: &std::sync::RwLock<GitCache>,
    file_path: &str,
    repo_path: &Path,
    entries: Vec<LineBlame>,
) {
    let Some(content_hash) = hash_file_content(repo_path.join(file_path)) else {
        return;
    };
    let mut dc = disk_cache
        .write()
        .unwrap_or_else(|e| e.into_inner());
    dc.files.insert(
        file_path.to_string(),
        CachedBlame {
            entries,
            content_hash,
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
    repo: RawRepo,
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
        let repo = RawRepo::discover(path)
            .with_context(|| format!("Failed to open git repository at {:?}", path))?;
        let repo_path = repo.workdir().to_path_buf();

        // Load disk cache from ~/.cache/repotoire/<repo>/git_cache.json
        let cache_path = crate::cache::git_cache_path(&repo_path);
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
            let dc = disk_cache
                .read()
                .unwrap_or_else(|e| e.into_inner());
            let cached_entries = dc
                .is_valid(file_path, &repo_path)
                .then(|| dc.files.get(file_path))
                .flatten()
                .map(|c| c.entries.clone());
            drop(dc);
            if let Some(entries) = cached_entries {
                mem_cache.insert(file_path.clone(), entries);
                cache_hits.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                return;
            }

            // Each worker discovers its own RawRepo. Attempting to share a
            // single Arc<RawRepo> across rayon workers regressed performance
            // on measured benchmarks — RawRepo's internal Mutex<LruCache>
            // serialized blame work under 8-16 parallel threads, and the
            // added sys time dwarfed the discover savings.
            let Ok(repo) = RawRepo::discover(&repo_path) else {
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

        // Raw module doesn't support line range restriction, so blame the full file and filter
        let all_entries = self.get_cached_file_blame(file_path)?;

        let mut entries: Vec<LineBlame> = all_entries
            .into_iter()
            .filter(|e| e.line_end >= line_start && e.line_start <= line_end)
            .collect();

        // Sort by timestamp descending (most recent first)
        entries.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));

        Ok(entries)
    }

    /// Get blame information for entire file.
    pub fn blame_file(&self, file_path: &str) -> Result<Vec<LineBlame>> {
        let hunks = raw_blame_file(&self.repo, file_path)
            .with_context(|| format!("Failed to blame {}", file_path))?;

        Ok(hunks_to_line_blames(&hunks))
    }

    /// Get cached blame for entire file, or compute and cache it.
    fn get_cached_file_blame(&self, file_path: &str) -> Result<Vec<LineBlame>> {
        // 1. Memory cache.
        if let Some(cached) = self.file_cache.get(file_path) {
            return Ok(cached.clone());
        }

        // 2. Disk cache — prewarm_cache may not have run for this file, or the
        // memory cache may have been cleared. Before recomputing, check if we
        // already have a valid on-disk entry from a previous run.
        {
            let dc = self
                .disk_cache
                .read()
                .unwrap_or_else(|e| e.into_inner());
            if dc.is_valid(file_path, &self.repo_path) {
                if let Some(entry) = dc.files.get(file_path) {
                    let entries = entry.entries.clone();
                    self.file_cache
                        .insert(file_path.to_string(), entries.clone());
                    return Ok(entries);
                }
            }
        }

        // 3. Fresh compute — also persist to disk so future runs hit the cache.
        let entries = self.blame_file(file_path)?;
        self.file_cache
            .insert(file_path.to_string(), entries.clone());
        update_disk_cache(&self.disk_cache, file_path, &self.repo_path, entries.clone());
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

/// Convert raw blame hunks to LineBlame entries, merging consecutive hunks from the same commit.
fn hunks_to_line_blames(hunks: &[crate::git::raw::BlameHunk]) -> Vec<LineBlame> {
    let mut entries = Vec::new();
    let mut current_hash: Option<String> = None;
    let mut current_entry: Option<LineBlame> = None;

    for hunk in hunks {
        let hex = hunk.commit.to_hex();
        let short_hash = hex[..hex.len().min(12)].to_string();
        let timestamp = format_epoch_time(hunk.author_time);
        let line_start = hunk.orig_start_line;
        let num_lines = hunk.num_lines;

        // Merge consecutive hunks from same commit
        if current_hash.as_ref() == Some(&hex) {
            if let Some(entry) = current_entry.as_mut() {
                entry.line_end = line_start + num_lines - 1;
                entry.line_count = entry.line_end - entry.line_start + 1;
                continue;
            }
        }

        // Save previous entry
        if let Some(entry) = current_entry.take() {
            entries.push(entry);
        }

        // Start new entry
        current_hash = Some(hex.clone());
        current_entry = Some(LineBlame {
            commit_hash: short_hash,
            full_hash: hex,
            author: hunk.author_name.clone(),
            author_email: hunk.author_email.clone(),
            timestamp,
            line_start,
            line_end: line_start + num_lines - 1,
            line_count: num_lines,
        });
    }

    // Don't forget the last entry
    if let Some(entry) = current_entry {
        entries.push(entry);
    }

    entries
}

/// Blame a file using a provided repository (for parallel pre-warming).
fn blame_file_with_repo(repo: &RawRepo, file_path: &str) -> Result<Vec<LineBlame>> {
    let hunks = raw_blame_file(repo, file_path)
        .with_context(|| format!("Failed to blame {}", file_path))?;

    Ok(hunks_to_line_blames(&hunks))
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
    use std::fs;
    use std::process::Command;
    use tempfile::tempdir;

    fn create_test_repo_with_file() -> Result<tempfile::TempDir> {
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

        // Create file with multiple lines
        let content = "line 1\nline 2\nline 3\nline 4\nline 5\n";
        fs::write(dir.path().join("test.py"), content)?;

        run(&["add", "test.py"]);
        run(&["commit", "-m", "Add test file"]);

        Ok(dir)
    }

    #[test]
    fn test_blame_file() -> Result<()> {
        let dir = create_test_repo_with_file()?;
        let blame = GitBlame::open(dir.path())?;

        let entries = blame.blame_file("test.py")?;
        assert!(!entries.is_empty());
        assert_eq!(entries[0].author, "Test User");
        Ok(())
    }

    #[test]
    fn test_blame_lines() -> Result<()> {
        let dir = create_test_repo_with_file()?;
        let blame = GitBlame::open(dir.path())?;

        let entries = blame.blame_lines("test.py", 2, 4)?;
        assert!(!entries.is_empty());
        Ok(())
    }

    #[test]
    fn test_entity_blame() -> Result<()> {
        let dir = create_test_repo_with_file()?;
        let blame = GitBlame::open(dir.path())?;

        let info = blame.get_entity_blame("test.py", 1, 5)?;
        assert!(info.last_modified.is_some());
        assert_eq!(info.last_author, Some("Test User".to_string()));
        assert_eq!(info.commit_count, 1);
        Ok(())
    }

    #[test]
    fn test_file_ownership() -> Result<()> {
        let dir = create_test_repo_with_file()?;
        let blame = GitBlame::open(dir.path())?;

        let ownership = blame.get_file_ownership("test.py")?;
        assert!(ownership.contains_key("Test User"));
        assert!((ownership["Test User"] - 100.0).abs() < 0.01);
        Ok(())
    }
}
