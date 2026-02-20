//! File caching for detectors
//!
//! Provides a shared cache to avoid re-reading files across multiple detectors.
//!
//! Cache layers (FileCache, IncrementalCache) implement the `CacheLayer` trait
//! and are coordinated via `CacheCoordinator` for consistent invalidation.

pub mod paths;
pub mod traits;

pub use traits::{CacheCoordinator, CacheLayer};

use dashmap::DashMap;
use rayon::prelude::*;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

pub use paths::{
    cache_dir, ensure_cache_dir, findings_cache_path, git_cache_path, graph_db_path,
    graph_stats_path,
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

    /// File content (cached, lazy-loading)
    pub fn content(&self, path: &Path) -> Option<Arc<String>> {
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

    /// File lines (cached, lazy-loading)
    pub fn lines(&self, path: &Path) -> Option<Arc<Vec<String>>> {
        // Check cache first
        if let Some(lines) = self.lines.get(path) {
            return Some(Arc::clone(&lines));
        }

        // Get content and split into lines
        let content = self.content(path)?;
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

    /// Clear all cached data (#13 — prevent stale data in watch/server mode)
    pub fn clear(&self) {
        self.contents.clear();
        self.lines.clear();
    }
}

impl Default for FileCache {
    fn default() -> Self {
        Self::new()
    }
}

impl CacheLayer for FileCache {
    fn name(&self) -> &str {
        "file-content"
    }

    fn is_populated(&self) -> bool {
        !self.contents.is_empty()
    }

    fn invalidate_files(&mut self, changed_files: &[&Path]) {
        for path in changed_files {
            self.contents.remove(*path);
            self.lines.remove(*path);
        }
    }

    fn invalidate_all(&mut self) {
        self.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::sync::Arc;

    #[test]
    fn test_file_cache_implements_cache_layer() {
        let mut cache = FileCache::new();

        // Verify name
        assert_eq!(cache.name(), "file-content");

        // Verify is_populated returns false when empty
        assert!(!cache.is_populated());

        // Insert some content
        let path_a = PathBuf::from("/tmp/a.rs");
        let path_b = PathBuf::from("/tmp/b.rs");
        cache
            .contents
            .insert(path_a.clone(), Arc::new("fn main() {}".to_string()));
        cache
            .contents
            .insert(path_b.clone(), Arc::new("fn helper() {}".to_string()));
        cache.lines.insert(
            path_a.clone(),
            Arc::new(vec!["fn main() {}".to_string()]),
        );
        cache.lines.insert(
            path_b.clone(),
            Arc::new(vec!["fn helper() {}".to_string()]),
        );

        // Verify is_populated returns true after adding content
        assert!(cache.is_populated());

        // Invalidate a specific file
        let path_a_ref: &Path = &path_a;
        cache.invalidate_files(&[path_a_ref]);

        // path_a should be removed from both contents and lines
        assert!(cache.contents.get(&path_a).is_none());
        assert!(cache.lines.get(&path_a).is_none());

        // path_b should still be present
        assert!(cache.contents.get(&path_b).is_some());
        assert!(cache.lines.get(&path_b).is_some());

        // Cache should still be populated
        assert!(cache.is_populated());
    }

    #[test]
    fn test_file_cache_invalidate_all() {
        let mut cache = FileCache::new();

        // Insert content
        let path_a = PathBuf::from("/tmp/a.rs");
        let path_b = PathBuf::from("/tmp/b.rs");
        cache
            .contents
            .insert(path_a.clone(), Arc::new("content a".to_string()));
        cache
            .contents
            .insert(path_b.clone(), Arc::new("content b".to_string()));
        cache
            .lines
            .insert(path_a.clone(), Arc::new(vec!["content a".to_string()]));
        cache
            .lines
            .insert(path_b.clone(), Arc::new(vec!["content b".to_string()]));

        assert!(cache.is_populated());

        // Invalidate all
        cache.invalidate_all();

        // Everything should be gone
        assert!(!cache.is_populated());
        assert!(cache.contents.is_empty());
        assert!(cache.lines.is_empty());
    }
}
