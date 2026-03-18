//! Shared file content cache for cross-detector file access.
//!
//! Uses DashMap for lock-free concurrent reads. Arc<String> avoids cloning
//! file contents when multiple detectors access the same file.

use dashmap::DashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Maximum file size to cache (2MB, matches parser guardrail)
const MAX_CACHE_FILE_SIZE: u64 = 2 * 1024 * 1024;

/// Thread-safe shared file content cache.
pub struct FileContentCache {
    cache: DashMap<PathBuf, Arc<String>>,
}

impl FileContentCache {
    pub fn new() -> Self {
        Self {
            cache: DashMap::new(),
        }
    }

    /// Get file content, reading from disk on cache miss.
    /// Returns None for files that don't exist, aren't UTF-8, or exceed 2MB.
    pub fn get_or_read(&self, path: &Path) -> Option<Arc<String>> {
        if let Some(entry) = self.cache.get(path) {
            return Some(Arc::clone(entry.value()));
        }

        // Check size before reading
        if let Ok(meta) = std::fs::metadata(path) {
            if meta.len() > MAX_CACHE_FILE_SIZE {
                return None;
            }
        }

        let content = std::fs::read_to_string(path).ok()?;
        let arc = Arc::new(content);
        self.cache.insert(path.to_path_buf(), Arc::clone(&arc));
        Some(arc)
    }

    /// Number of cached files
    #[allow(dead_code)]
    pub fn len(&self) -> usize {
        self.cache.len()
    }

    /// Returns true if the cache is empty
    #[allow(dead_code)]
    pub fn is_empty(&self) -> bool {
        self.cache.is_empty()
    }
}

impl Default for FileContentCache {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_file_cache_reads_and_caches() {
        let tmp = tempfile::tempdir().unwrap();
        let file_path = tmp.path().join("test.py");
        std::fs::write(&file_path, "print('hello')").unwrap();

        let cache = FileContentCache::new();

        let content1 = cache.get_or_read(&file_path).unwrap();
        assert_eq!(&*content1, "print('hello')");
        assert_eq!(cache.len(), 1);

        let content2 = cache.get_or_read(&file_path).unwrap();
        assert!(Arc::ptr_eq(&content1, &content2));
    }

    #[test]
    fn test_file_cache_skips_large_files() {
        let tmp = tempfile::tempdir().unwrap();
        let file_path = tmp.path().join("huge.py");
        let mut f = std::fs::File::create(&file_path).unwrap();
        f.write_all(&vec![b'x'; 3 * 1024 * 1024]).unwrap();

        let cache = FileContentCache::new();
        assert!(cache.get_or_read(&file_path).is_none());
    }

    #[test]
    fn test_file_cache_returns_none_for_missing_file() {
        let cache = FileContentCache::new();
        assert!(cache.get_or_read(Path::new("/nonexistent/file.py")).is_none());
    }
}
