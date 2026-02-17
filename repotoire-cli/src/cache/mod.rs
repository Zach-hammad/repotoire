//! File caching for detectors
//!
//! Provides a shared cache to avoid re-reading files across multiple detectors.
//!
//! TODO(refactor): Three independent cache layers exist with no unified interface:
//!
//! - `cache/mod.rs` (file content caching)
//! - `cache/paths.rs` (path utilities)
//! - `detectors/incremental_cache.rs` (finding-level caching)
//!
//! These should share a common trait and coordinate invalidation.
//! This is the architectural root cause of cache divergence bugs.

pub mod paths;
pub mod traits;

pub use traits::{CacheCoordinator, CacheLayer};

use dashmap::DashMap;
use rayon::prelude::*;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

pub use paths::{
    ensure_cache_dir, get_cache_dir, get_findings_cache_path, get_git_cache_path,
    get_graph_db_path, get_graph_stats_path,
};

/// Global file cache instance
static GLOBAL_CACHE: OnceLock<FileCache> = OnceLock::new();

/// Get or initialize the global file cache
pub fn global_cache() -> &'static FileCache {
    GLOBAL_CACHE.get_or_init(FileCache::new)
}

/// Warm the global cache with files from a directory
pub fn warm_global_cache(root: &Path, extensions: &[&str]) {
    global_cache().warm(root, extensions);
}

/// Thread-safe file content cache
#[derive(Clone)]
pub struct FileCache {
    /// Cached file contents: path -> content
    contents: Arc<DashMap<PathBuf, Arc<String>>>,
    /// Cached file lines: path -> lines
    lines: Arc<DashMap<PathBuf, Arc<Vec<String>>>>,
}

impl FileCache {
    pub fn new() -> Self {
        Self {
            contents: Arc::new(DashMap::new()),
            lines: Arc::new(DashMap::new()),
        }
    }

    /// Pre-warm cache with files from a directory walk
    pub fn warm(&self, root: &Path, extensions: &[&str]) {
        let walker = ignore::WalkBuilder::new(root)
            .hidden(false)
            .git_ignore(true)
            .build();

        let paths: Vec<PathBuf> = walker
            .filter_map(|e| e.ok())
            .filter(|e| e.path().is_file())
            .filter(|e| {
                e.path()
                    .extension()
                    .and_then(|ext| ext.to_str())
                    .map(|ext| extensions.contains(&ext))
                    .unwrap_or(false)
            })
            .map(|e| e.path().to_path_buf())
            .collect();

        // Read files in parallel
        paths.par_iter().for_each(|path| {
            if let Ok(content) = std::fs::read_to_string(path) {
                self.contents.insert(path.clone(), Arc::new(content));
            }
        });
    }

    /// Get file content (cached)
    pub fn get_content(&self, path: &Path) -> Option<Arc<String>> {
        // Check cache first
        if let Some(content) = self.contents.get(path) {
            return Some(Arc::clone(&content));
        }

        // Read and cache
        if let Ok(content) = std::fs::read_to_string(path) {
            let arc = Arc::new(content);
            self.contents.insert(path.to_path_buf(), Arc::clone(&arc));
            Some(arc)
        } else {
            None
        }
    }

    /// Get file lines (cached)
    pub fn get_lines(&self, path: &Path) -> Option<Arc<Vec<String>>> {
        // Check cache first
        if let Some(lines) = self.lines.get(path) {
            return Some(Arc::clone(&lines));
        }

        // Get content and split into lines
        let content = self.get_content(path)?;
        let lines: Vec<String> = content.lines().map(String::from).collect();
        let arc = Arc::new(lines);
        self.lines.insert(path.to_path_buf(), Arc::clone(&arc));
        Some(arc)
    }

    /// Get list of cached file paths
    pub fn cached_paths(&self) -> Vec<PathBuf> {
        self.contents.iter().map(|r| r.key().clone()).collect()
    }

    /// Get cached paths filtered by extension
    pub fn paths_with_ext(&self, extensions: &[&str]) -> Vec<PathBuf> {
        self.contents
            .iter()
            .filter(|r| {
                r.key()
                    .extension()
                    .and_then(|e| e.to_str())
                    .map(|e| extensions.contains(&e))
                    .unwrap_or(false)
            })
            .map(|r| r.key().clone())
            .collect()
    }

    /// Cache stats
    pub fn stats(&self) -> (usize, usize) {
        (self.contents.len(), self.lines.len())
    }
}

impl Default for FileCache {
    fn default() -> Self {
        Self::new()
    }
}
