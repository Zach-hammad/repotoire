//! Incremental file fingerprinting and cache system for fast re-analysis
//!
//! This module provides caching of detector findings keyed by file content hash,
//! enabling incremental analysis that only re-runs detectors on changed files.
//!
//! Uses xxhash for speed when available, falls back to md5.
//!
//! # Example
//!
//! ```ignore
//! let cache = IncrementalCache::new(Path::new("/repo/.repotoire/cache"));
//! let changed = cache.get_changed_files(&all_files);
//! for f in changed {
//!     let findings = run_detector(&f);
//!     cache.cache_findings(&f, &findings);
//! }
//! cache.save_cache()?;
//! ```

use crate::models::{Finding, Severity};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};
use tracing::{debug, info, warn};

/// Cache format version - bump when schema changes
const CACHE_VERSION: u32 = 2;

/// Buffer size for hashing large files (64KB chunks)
const HASH_BUFFER_SIZE: usize = 65536;

/// Serialized finding for cache storage
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CachedFinding {
    pub id: String,
    pub detector: String,
    pub severity: String,
    pub title: String,
    pub description: String,
    pub affected_files: Vec<String>,
    pub line_start: Option<u32>,
    pub line_end: Option<u32>,
    pub suggested_fix: Option<String>,
    pub estimated_effort: Option<String>,
    pub category: Option<String>,
    pub cwe_id: Option<String>,
    pub why_it_matters: Option<String>,
}

impl From<&Finding> for CachedFinding {
    fn from(f: &Finding) -> Self {
        Self {
            id: f.id.clone(),
            detector: f.detector.clone(),
            severity: format!("{:?}", f.severity),
            title: f.title.clone(),
            description: f.description.clone(),
            affected_files: f
                .affected_files
                .iter()
                .map(|p| p.to_string_lossy().to_string())
                .collect(),
            line_start: f.line_start,
            line_end: f.line_end,
            suggested_fix: f.suggested_fix.clone(),
            estimated_effort: f.estimated_effort.clone(),
            category: f.category.clone(),
            cwe_id: f.cwe_id.clone(),
            why_it_matters: f.why_it_matters.clone(),
        }
    }
}

impl CachedFinding {
    fn to_finding(&self) -> Finding {
        let severity = match self.severity.to_lowercase().as_str() {
            "critical" => Severity::Critical,
            "high" => Severity::High,
            "medium" => Severity::Medium,
            "low" => Severity::Low,
            "info" => Severity::Info,
            _ => Severity::Medium,
        };

        Finding {
            id: self.id.clone(),
            detector: self.detector.clone(),
            severity,
            title: self.title.clone(),
            description: self.description.clone(),
            affected_files: self.affected_files.iter().map(PathBuf::from).collect(),
            line_start: self.line_start,
            line_end: self.line_end,
            suggested_fix: self.suggested_fix.clone(),
            estimated_effort: self.estimated_effort.clone(),
            category: self.category.clone(),
            cwe_id: self.cwe_id.clone(),
            why_it_matters: self.why_it_matters.clone(),
            ..Default::default()
        }
    }
}

/// Cached file entry
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CachedFile {
    hash: String,
    findings: Vec<CachedFinding>,
    timestamp: u64,
}

/// Graph-level cache data
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct GraphCache {
    hash: Option<String>,
    detectors: HashMap<String, Vec<CachedFinding>>,
}

/// Full cache structure
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CacheData {
    version: u32,
    files: HashMap<String, CachedFile>,
    graph: GraphCache,
}

impl Default for CacheData {
    fn default() -> Self {
        Self {
            version: CACHE_VERSION,
            files: HashMap::new(),
            graph: GraphCache::default(),
        }
    }
}

/// Cache statistics
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CacheStats {
    pub cached_files: usize,
    pub total_findings: usize,
    pub graph_hash: Option<String>,
    pub graph_detectors: usize,
    pub graph_findings: usize,
    pub cache_version: u32,
}

/// File fingerprinting and findings cache for incremental analysis
///
/// Stores file hashes and associated findings to avoid re-running detectors
/// on unchanged files. Cache is persisted to disk as JSON.
pub struct IncrementalCache {
    cache_dir: PathBuf,
    cache_file: PathBuf,
    cache: CacheData,
    dirty: bool,
}

impl IncrementalCache {
    /// Create a new cache
    pub fn new(cache_dir: &Path) -> Self {
        let cache_dir = cache_dir.to_path_buf();
        let cache_file = cache_dir.join("findings_cache.json");

        // Ensure cache directory exists
        if let Err(e) = fs::create_dir_all(&cache_dir) {
            warn!("Failed to create cache directory: {}", e);
        }

        let mut instance = Self {
            cache_dir,
            cache_file,
            cache: CacheData::default(),
            dirty: false,
        };

        // Load existing cache
        if let Err(e) = instance.load_cache() {
            debug!("Failed to load cache: {}", e);
        }

        instance
    }

    /// Compute fast content hash of a file
    pub fn get_file_hash(&self, path: &Path) -> String {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};

        match fs::File::open(path) {
            Ok(mut file) => {
                let mut hasher = DefaultHasher::new();
                let mut buffer = [0u8; HASH_BUFFER_SIZE];

                loop {
                    match file.read(&mut buffer) {
                        Ok(0) => break,
                        Ok(n) => buffer[..n].hash(&mut hasher),
                        Err(_) => break,
                    }
                }

                format!("{:016x}", hasher.finish())
            }
            Err(_) => format!("error:{}", path.display()),
        }
    }

    /// Load cache from disk
    fn load_cache(&mut self) -> Result<()> {
        if !self.cache_file.exists() {
            debug!("No cache file found at {:?}", self.cache_file);
            return Ok(());
        }

        let file = File::open(&self.cache_file).context("Failed to open cache file")?;
        let reader = BufReader::new(file);
        let data: CacheData = serde_json::from_reader(reader).context("Failed to parse cache")?;

        // Version check - rebuild if schema changed
        if data.version != CACHE_VERSION {
            info!(
                "Cache version mismatch (got {}, expected {}), rebuilding",
                data.version, CACHE_VERSION
            );
            self.invalidate_all();
            return Ok(());
        }

        self.cache = data;
        debug!("Loaded cache with {} files", self.cache.files.len());

        Ok(())
    }

    /// Persist cache to disk
    pub fn save_cache(&mut self) -> Result<()> {
        if !self.dirty {
            return Ok(());
        }

        // Write to temp file first, then rename (atomic on POSIX)
        let tmp_file = self.cache_file.with_extension("tmp");

        let file = File::create(&tmp_file).context("Failed to create temp cache file")?;
        let writer = BufWriter::new(file);
        serde_json::to_writer(writer, &self.cache).context("Failed to write cache")?;

        // Atomic rename
        fs::rename(&tmp_file, &self.cache_file).context("Failed to rename temp cache")?;

        self.dirty = false;
        debug!("Saved cache with {} files", self.cache.files.len());

        Ok(())
    }

    /// Check if file has changed since last cache
    pub fn is_file_changed(&self, path: &Path) -> bool {
        let path_key = self.path_key(path);
        match self.cache.files.get(&path_key) {
            None => true,
            Some(cached) => {
                let current_hash = self.get_file_hash(path);
                cached.hash != current_hash
            }
        }
    }

    /// Retrieve cached findings for a file
    pub fn get_cached_findings(&self, path: &Path) -> Vec<Finding> {
        let path_key = self.path_key(path);

        match self.cache.files.get(&path_key) {
            None => vec![],
            Some(cached) => {
                // Check if file changed - if so, cached findings are stale
                let current_hash = self.get_file_hash(path);
                if cached.hash != current_hash {
                    return vec![];
                }

                cached.findings.iter().map(|cf| cf.to_finding()).collect()
            }
        }
    }

    /// Store findings for a file in the cache
    pub fn cache_findings(&mut self, path: &Path, findings: &[Finding]) {
        let path_key = self.path_key(path);
        let file_hash = self.get_file_hash(path);

        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);

        let cached_findings: Vec<CachedFinding> =
            findings.iter().map(CachedFinding::from).collect();

        self.cache.files.insert(
            path_key,
            CachedFile {
                hash: file_hash,
                findings: cached_findings,
                timestamp,
            },
        );
        self.dirty = true;
    }

    /// Filter to only files that have changed since last cache
    pub fn get_changed_files(&self, all_files: &[PathBuf]) -> Vec<PathBuf> {
        let mut changed = Vec::new();

        for path in all_files {
            let path_key = self.path_key(path);
            match self.cache.files.get(&path_key) {
                None => changed.push(path.clone()),
                Some(cached) => {
                    let current_hash = self.get_file_hash(path);
                    if cached.hash != current_hash {
                        changed.push(path.clone());
                    }
                }
            }
        }

        debug!(
            "Incremental analysis: {}/{} files changed",
            changed.len(),
            all_files.len()
        );
        changed
    }

    /// Remove a file from the cache
    pub fn invalidate_file(&mut self, path: &Path) {
        let path_key = self.path_key(path);
        if self.cache.files.remove(&path_key).is_some() {
            self.dirty = true;
        }
    }

    /// Clear the entire cache
    pub fn invalidate_all(&mut self) {
        self.cache = CacheData::default();
        self.dirty = true;
    }

    // -------------------------------------------------------------------------
    // Graph-level caching methods
    // -------------------------------------------------------------------------

    /// Check if the graph has changed since last cache
    pub fn is_graph_changed(&self, current_hash: &str) -> bool {
        match &self.cache.graph.hash {
            None => true,
            Some(cached_hash) => cached_hash != current_hash,
        }
    }

    /// Store findings from a graph-level detector
    pub fn cache_graph_findings(&mut self, detector_name: &str, findings: &[Finding]) {
        let cached_findings: Vec<CachedFinding> =
            findings.iter().map(CachedFinding::from).collect();

        self.cache
            .graph
            .detectors
            .insert(detector_name.to_string(), cached_findings);
        self.dirty = true;
    }

    /// Retrieve cached findings for a specific graph detector
    pub fn get_cached_graph_findings(&self, detector_name: &str) -> Vec<Finding> {
        self.cache
            .graph
            .detectors
            .get(detector_name)
            .map(|findings| findings.iter().map(|cf| cf.to_finding()).collect())
            .unwrap_or_default()
    }

    /// Retrieve all cached findings from all graph detectors
    pub fn get_all_cached_graph_findings(&self) -> Vec<Finding> {
        self.cache
            .graph
            .detectors
            .values()
            .flat_map(|findings| findings.iter().map(|cf| cf.to_finding()))
            .collect()
    }

    /// Update the cached graph hash after running graph detectors
    pub fn update_graph_hash(&mut self, hash: &str) {
        self.cache.graph.hash = Some(hash.to_string());
        self.dirty = true;
    }

    /// Get cache statistics
    pub fn get_stats(&self) -> CacheStats {
        let total_findings: usize = self.cache.files.values().map(|f| f.findings.len()).sum();
        let graph_findings: usize = self.cache.graph.detectors.values().map(|f| f.len()).sum();

        CacheStats {
            cached_files: self.cache.files.len(),
            total_findings,
            graph_hash: self.cache.graph.hash.clone(),
            graph_detectors: self.cache.graph.detectors.len(),
            graph_findings,
            cache_version: self.cache.version,
        }
    }

    /// Convert path to cache key
    fn path_key(&self, path: &Path) -> String {
        path.canonicalize()
            .unwrap_or_else(|_| path.to_path_buf())
            .to_string_lossy()
            .to_string()
    }
}

impl Drop for IncrementalCache {
    fn drop(&mut self) {
        if let Err(e) = self.save_cache() {
            warn!("Failed to save cache on drop: {}", e);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn create_test_finding(file: &str) -> Finding {
        Finding {
            id: "test-1".to_string(),
            detector: "TestDetector".to_string(),
            severity: Severity::Medium,
            title: "Test finding".to_string(),
            description: "Test description".to_string(),
            affected_files: vec![PathBuf::from(file)],
            line_start: Some(10),
            line_end: Some(20),
            suggested_fix: None,
            estimated_effort: None,
            category: None,
            cwe_id: None,
            why_it_matters: None,
            ..Default::default()
        }
    }

    #[test]
    fn test_cache_creation() {
        let tmp = TempDir::new().unwrap();
        let cache = IncrementalCache::new(tmp.path());
        let stats = cache.get_stats();
        assert_eq!(stats.cached_files, 0);
        assert_eq!(stats.cache_version, CACHE_VERSION);
    }

    #[test]
    fn test_file_hash() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("test.txt");
        fs::write(&file_path, "hello world").unwrap();

        let cache = IncrementalCache::new(tmp.path());
        let hash1 = cache.get_file_hash(&file_path);
        let hash2 = cache.get_file_hash(&file_path);

        // Same content should have same hash
        assert_eq!(hash1, hash2);

        // Different content should have different hash
        fs::write(&file_path, "changed content").unwrap();
        let hash3 = cache.get_file_hash(&file_path);
        assert_ne!(hash1, hash3);
    }

    #[test]
    fn test_cache_findings() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("test.py");
        fs::write(&file_path, "def test(): pass").unwrap();

        let mut cache = IncrementalCache::new(tmp.path());
        let finding = create_test_finding(&file_path.to_string_lossy());

        // Cache findings
        cache.cache_findings(&file_path, &[finding.clone()]);

        // Retrieve cached findings
        let cached = cache.get_cached_findings(&file_path);
        assert_eq!(cached.len(), 1);
        assert_eq!(cached[0].id, finding.id);
    }

    #[test]
    fn test_changed_files() {
        let tmp = TempDir::new().unwrap();
        let file1 = tmp.path().join("file1.py");
        let file2 = tmp.path().join("file2.py");
        fs::write(&file1, "content1").unwrap();
        fs::write(&file2, "content2").unwrap();

        let mut cache = IncrementalCache::new(tmp.path());

        // Cache file1
        cache.cache_findings(&file1, &[]);

        // Check changed files
        let all_files = vec![file1.clone(), file2.clone()];
        let changed = cache.get_changed_files(&all_files);

        // Only file2 should be marked as changed (not in cache)
        assert_eq!(changed.len(), 1);
        assert_eq!(changed[0], file2);
    }

    #[test]
    fn test_graph_cache() {
        let tmp = TempDir::new().unwrap();
        let mut cache = IncrementalCache::new(tmp.path());

        // Cache graph findings
        let finding = create_test_finding("test.py");
        cache.cache_graph_findings("TestDetector", &[finding]);
        cache.update_graph_hash("hash123");

        // Check graph cache
        assert!(!cache.is_graph_changed("hash123"));
        assert!(cache.is_graph_changed("different_hash"));

        let cached = cache.get_cached_graph_findings("TestDetector");
        assert_eq!(cached.len(), 1);
    }

    #[test]
    fn test_invalidation() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("test.py");
        fs::write(&file_path, "content").unwrap();

        let mut cache = IncrementalCache::new(tmp.path());
        cache.cache_findings(&file_path, &[create_test_finding("test.py")]);

        assert_eq!(cache.get_stats().cached_files, 1);

        cache.invalidate_file(&file_path);
        assert_eq!(cache.get_stats().cached_files, 0);

        cache.cache_findings(&file_path, &[create_test_finding("test.py")]);
        cache.invalidate_all();
        assert_eq!(cache.get_stats().cached_files, 0);
    }
}
