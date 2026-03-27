//! Incremental file fingerprinting and cache system for fast re-analysis
//!
//! This module provides caching of detector findings keyed by file content hash,
//! enabling incremental analysis that only re-runs detectors on changed files.
//!
//! Uses XXH3 (xxhash-rust) for fast, non-cryptographic content fingerprinting.
//!
//! # Example
//!
//! ```ignore
//! let cache = IncrementalCache::new(Path::new("/repo/.repotoire/cache"));
//! let changed = cache.changed_files(&all_files);
//! for f in changed {
//!     let findings = run_detector(&f);
//!     cache.cache_findings(&f, &findings);
//! }
//! cache.save_cache()?;
//! ```

use crate::models::{Finding, Grade, Severity};
use crate::parsers::ParseResult;
use anyhow::{Context, Result};
use dashmap::DashMap;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tracing::{debug, info, warn};

/// Cache format version - bump when schema changes
const CACHE_VERSION: u32 = 2;

/// Buffer size for hashing large files (64KB chunks)
const HASH_BUFFER_SIZE: usize = 65536;

/// Deserialize Severity from both old format ("High", Debug) and new format ("high", Display/serde).
fn deserialize_severity_compat<'de, D>(deserializer: D) -> Result<Severity, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let s = String::deserialize(deserializer)?;
    s.parse::<Severity>().map_err(serde::de::Error::custom)
}

/// Serialized finding for cache storage
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CachedFinding {
    pub id: String,
    pub detector: String,
    #[serde(deserialize_with = "deserialize_severity_compat")]
    pub severity: Severity,
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
    #[serde(default)]
    pub confidence: Option<f64>,
    #[serde(default)]
    pub deterministic: bool,
    #[serde(default)]
    pub threshold_metadata: std::collections::BTreeMap<String, String>,
}

impl From<&Finding> for CachedFinding {
    fn from(f: &Finding) -> Self {
        Self {
            id: f.id.clone(),
            detector: f.detector.clone(),
            severity: f.severity,
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
            confidence: f.confidence,
            deterministic: f.deterministic,
            threshold_metadata: f.threshold_metadata.clone(),
        }
    }
}

impl CachedFinding {
    fn to_finding(&self) -> Finding {
        Finding {
            id: self.id.clone(),
            detector: self.detector.clone(),
            severity: self.severity,
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
            confidence: self.confidence,
            deterministic: self.deterministic,
            threshold_metadata: self.threshold_metadata.clone(),
        }
    }
}

/// Cached file entry
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CachedFile {
    hash: String,
    findings: Vec<CachedFinding>,
    timestamp: u64,
    /// Qualified names of cross-file values this file depends on (e.g., "config.TIMEOUT")
    #[serde(default)]
    value_dependencies: Vec<String>,
    /// Hash of each dependency's resolved value at cache time
    #[serde(default)]
    value_hashes: HashMap<String, u64>,
}

/// Cached score result
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CachedScoreResult {
    pub score: f64,
    pub grade: Grade,
    pub total_files: usize,
    pub total_functions: usize,
    pub total_classes: usize,
    #[serde(default)]
    pub structure_score: Option<f64>,
    #[serde(default)]
    pub quality_score: Option<f64>,
    #[serde(default)]
    pub architecture_score: Option<f64>,
    #[serde(default)]
    pub total_loc: Option<usize>,
}

/// Graph-level cache data
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct GraphCache {
    hash: Option<String>,
    detectors: HashMap<String, Vec<CachedFinding>>,
    #[serde(default)]
    score: Option<CachedScoreResult>,
}

/// Cached parse result for a file
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CachedParseResult {
    hash: String,
    result: crate::parsers::ParseResult,
}

/// Full cache structure
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CacheData {
    version: u32,
    /// Binary version that created this cache (#66)
    #[serde(default)]
    binary_version: String,
    files: HashMap<String, CachedFile>,
    graph: GraphCache,
    #[serde(default)]
    parse_cache: HashMap<String, CachedParseResult>,
}

impl Default for CacheData {
    // repotoire:ignore[mutual-recursion] — false positive: new() → load_cache() → invalidate_all() → default() is a call-graph cycle but not actual recursion; each function is called at most once per construction.
    fn default() -> Self {
        Self {
            version: CACHE_VERSION,
            binary_version: env!("CARGO_PKG_VERSION").to_string(),
            files: HashMap::new(),
            graph: GraphCache::default(),
            parse_cache: HashMap::new(),
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
    #[allow(dead_code)] // Part of cache structure
    cache_dir: PathBuf,
    cache_file: PathBuf,
    cache: CacheData,
    dirty: bool,
    /// Memoized all-files hash to avoid re-hashing on every call
    memoized_files_hash: Option<(usize, String)>, // (file_count, hash)
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
            memoized_files_hash: None,
        };

        // Load existing cache
        if let Err(e) = instance.load_cache() {
            debug!("Failed to load cache: {}", e);
        }

        instance
    }

    /// Check if a warm cache exists (for auto-incremental)
    pub fn has_cache(&self) -> bool {
        !self.cache.files.is_empty() || !self.cache.parse_cache.is_empty()
    }

    /// Cached parse result for a file if unchanged
    #[allow(dead_code)] // Public API, now primarily used via ConcurrentCacheView
    pub fn cached_parse(&self, path: &Path) -> Option<crate::parsers::ParseResult> {
        let key = path.to_string_lossy().to_string();
        let hash = self.file_hash(path);

        self.cache.parse_cache.get(&key).and_then(|cached| {
            if cached.hash == hash {
                Some(cached.result.clone())
            } else {
                None
            }
        })
    }

    /// Cache a parse result for a file
    pub fn cache_parse_result(&mut self, path: &Path, result: &crate::parsers::ParseResult) {
        let key = path.to_string_lossy().to_string();
        let hash = self.file_hash(path);

        self.cache.parse_cache.insert(
            key,
            CachedParseResult {
                hash,
                result: result.clone(),
            },
        );
        self.dirty = true;
    }

    /// Compute fast content hash of a file using XXH3 (3-5x faster than SipHash)
    pub fn file_hash(&self, path: &Path) -> String {
        match fs::File::open(path) {
            Ok(mut file) => {
                let mut hasher = xxhash_rust::xxh3::Xxh3::new();
                let mut buffer = [0u8; HASH_BUFFER_SIZE];

                loop {
                    match file.read(&mut buffer) {
                        Ok(0) => break,
                        Ok(n) => hasher.update(&buffer[..n]),
                        Err(_) => break,
                    }
                }

                format!("{:016x}", hasher.digest())
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

        // Binary version check — prevent stale detector results across upgrades (#66)
        let current_version = env!("CARGO_PKG_VERSION");
        if !data.binary_version.is_empty() && data.binary_version != current_version {
            info!(
                "Binary version changed ({} → {}), invalidating cache",
                data.binary_version, current_version
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
    #[allow(dead_code)] // Public API
    pub fn is_file_changed(&self, path: &Path) -> bool {
        let path_key = self.path_key(path);
        match self.cache.files.get(&path_key) {
            None => true,
            Some(cached) => {
                let current_hash = self.file_hash(path);
                cached.hash != current_hash
            }
        }
    }

    /// Retrieve cached findings for a file
    pub fn cached_findings(&self, path: &Path) -> Vec<Finding> {
        let path_key = self.path_key(path);

        match self.cache.files.get(&path_key) {
            None => vec![],
            Some(cached) => {
                // Check if file changed - if so, cached findings are stale
                let current_hash = self.file_hash(path);
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
        let file_hash = self.file_hash(path);

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
                value_dependencies: Vec::new(),
                value_hashes: HashMap::new(),
            },
        );
        self.dirty = true;
    }

    /// Filter to only files that have changed since last cache
    pub fn changed_files(&self, all_files: &[PathBuf]) -> Vec<PathBuf> {
        let mut changed = Vec::new();

        for path in all_files {
            let path_key = self.path_key(path);
            match self.cache.files.get(&path_key) {
                None => changed.push(path.clone()),
                Some(cached) => {
                    let current_hash = self.file_hash(path);
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

    /// Remove cache entries for files that no longer exist in the file list.
    /// Call this periodically to prevent unbounded cache growth.
    pub fn prune_stale_entries(&mut self, current_files: &[PathBuf]) {
        let current_keys: std::collections::HashSet<String> =
            current_files.iter().map(|p| self.path_key(p)).collect();

        let before_files = self.cache.files.len();
        let before_parse = self.cache.parse_cache.len();

        self.cache.files.retain(|k, _| current_keys.contains(k));
        self.cache
            .parse_cache
            .retain(|k, _| current_keys.contains(k));

        let pruned_files = before_files - self.cache.files.len();
        let pruned_parse = before_parse - self.cache.parse_cache.len();

        if pruned_files > 0 || pruned_parse > 0 {
            debug!(
                "Pruned {} stale file entries, {} stale parse entries",
                pruned_files, pruned_parse
            );
            self.dirty = true;
        }
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

    /// Compute a combined hash of all files for graph-level cache validation.
    /// Result is memoized per file count to avoid re-hashing 200+ files multiple times.
    pub fn compute_all_files_hash(&mut self, files: &[std::path::PathBuf]) -> String {
        // Return memoized hash if file count matches (same analysis run)
        if let Some((count, ref hash)) = self.memoized_files_hash {
            if count == files.len() {
                return hash.clone();
            }
        }

        let mut hasher = xxhash_rust::xxh3::Xxh3::new();

        // Sort files for deterministic hashing
        let mut sorted_files: Vec<_> = files.iter().collect();
        sorted_files.sort();

        for path in sorted_files {
            // Hash file path and content hash
            let path_bytes = path.to_string_lossy();
            hasher.update(path_bytes.as_bytes());
            hasher.update(self.file_hash(path).as_bytes());
        }

        let hash = format!("{:016x}", hasher.digest());
        self.memoized_files_hash = Some((files.len(), hash.clone()));
        hash
    }

    /// Check if we can use cached detector results
    pub fn can_use_cached_detectors(&mut self, files: &[std::path::PathBuf]) -> bool {
        let current_hash = self.compute_all_files_hash(files);
        !self.is_graph_changed(&current_hash) && !self.cache.graph.detectors.is_empty()
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
    #[allow(dead_code)] // Public API
    pub fn cached_graph_findings(&self, detector_name: &str) -> Vec<Finding> {
        self.cache
            .graph
            .detectors
            .get(detector_name)
            .map(|findings| findings.iter().map(|cf| cf.to_finding()).collect())
            .unwrap_or_default()
    }

    /// Retrieve all cached findings from all graph detectors
    pub fn all_cached_graph_findings(&self) -> Vec<Finding> {
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

    /// Cache the score result (without sub-scores)
    #[allow(dead_code)] // Public API
    pub fn cache_score(
        &mut self,
        score: f64,
        grade: Grade,
        files: usize,
        functions: usize,
        classes: usize,
        total_loc: usize,
    ) {
        self.cache_score_with_subscores(CachedScoreResult {
            score,
            grade,
            total_files: files,
            total_functions: functions,
            total_classes: classes,
            structure_score: None,
            quality_score: None,
            architecture_score: None,
            total_loc: Some(total_loc),
        });
    }

    /// Cache the score result with sub-scores
    pub fn cache_score_with_subscores(&mut self, result: CachedScoreResult) {
        self.cache.graph.score = Some(result);
        self.dirty = true;
    }

    /// Cached score if available
    pub fn cached_score(&self) -> Option<&CachedScoreResult> {
        self.cache.graph.score.as_ref()
    }

    /// Check if we have a complete cached result (findings + score)
    pub fn has_complete_cache(&mut self, files: &[std::path::PathBuf]) -> bool {
        let current_hash = self.compute_all_files_hash(files);
        !self.is_graph_changed(&current_hash)
            && !self.cache.graph.detectors.is_empty()
            && self.cache.graph.score.is_some()
    }

    /// Cache statistics
    pub fn stats(&self) -> CacheStats {
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

    /// Record which cross-file values a file depends on, along with their current hashes.
    #[allow(dead_code)] // API for future incremental invalidation based on value changes
    pub fn set_value_dependencies(
        &mut self,
        file: &Path,
        deps: Vec<String>,
        hashes: HashMap<String, u64>,
    ) {
        let key = self.path_key(file);
        if let Some(cached) = self.cache.files.get_mut(&key) {
            cached.value_dependencies = deps;
            cached.value_hashes = hashes;
        }
    }

    /// Check if a cached file's value dependencies are still valid.
    /// Returns true if all dependencies have the same hash as when cached.
    #[allow(dead_code)] // API for future incremental invalidation based on value changes
    pub fn value_deps_valid(&self, file: &Path, current_hashes: &HashMap<String, u64>) -> bool {
        let key = self.path_key(file);
        if let Some(cached) = self.cache.files.get(&key) {
            if cached.value_dependencies.is_empty() {
                return true; // No dependencies, always valid
            }
            for dep in &cached.value_dependencies {
                let cached_hash = cached.value_hashes.get(dep);
                let current_hash = current_hashes.get(dep);
                if cached_hash != current_hash {
                    return false; // Dependency value changed
                }
            }
            true
        } else {
            true // No cache entry, nothing to invalidate
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

/// Compute the XXH3 content hash of a file without needing an `IncrementalCache` instance.
/// Used by `ConcurrentCacheView` to pre-validate cache entries.
fn file_hash_standalone(path: &Path) -> String {
    match fs::File::open(path) {
        Ok(mut file) => {
            let mut hasher = xxhash_rust::xxh3::Xxh3::new();
            let mut buffer = [0u8; HASH_BUFFER_SIZE];

            loop {
                match file.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(n) => hasher.update(&buffer[..n]),
                    Err(_) => break,
                }
            }

            format!("{:016x}", hasher.digest())
        }
        Err(_) => format!("error:{}", path.display()),
    }
}

/// Lock-free concurrent view for parallel read access during parsing.
///
/// Created from `IncrementalCache` before entering a `par_iter` loop.
/// Contains pre-validated cache entries (file hash already checked) so the
/// parallel loop can do a simple `DashMap::get()` with no file I/O for cache
/// hits.  New parse results are collected into a separate `DashMap` and merged
/// back into the `IncrementalCache` after the loop finishes.
pub struct ConcurrentCacheView {
    /// Pre-validated cached parse results keyed by file path.
    /// Wrapped in `Arc` so cache hits are cheap atomic increments
    /// instead of deep `Vec<FunctionInfo>` / `Vec<ClassInfo>` clones.
    pub parse_cache: DashMap<PathBuf, Arc<ParseResult>>,
}

impl IncrementalCache {
    /// Create a concurrent view populated from existing cache data.
    ///
    /// Only entries whose on-disk file hash still matches the cached hash are
    /// included, so consumers can treat every entry as valid without re-hashing.
    ///
    /// `files` limits which paths are checked — entries for files not in the
    /// list are skipped to avoid unnecessary I/O.
    pub fn concurrent_view(&self, files: &[PathBuf]) -> ConcurrentCacheView {
        let parse_cache = DashMap::with_capacity(files.len());

        for file in files {
            let key = file.to_string_lossy().to_string();
            if let Some(cached) = self.cache.parse_cache.get(&key) {
                let current_hash = file_hash_standalone(file);
                if cached.hash == current_hash {
                    parse_cache.insert(file.clone(), Arc::new(cached.result.clone()));
                }
            }
        }

        ConcurrentCacheView { parse_cache }
    }

    /// Merge new parse results from a `DashMap` back into the persistent cache.
    ///
    /// Call this after the parallel parsing loop to persist newly parsed files.
    /// Accepts `Arc<ParseResult>` — the Arc is unwrapped (or cloned if shared)
    /// because the on-disk cache stores owned `ParseResult` values.
    pub fn merge_new_parse_results(&mut self, new_results: DashMap<PathBuf, Arc<ParseResult>>) {
        for (path, arc_result) in new_results.into_iter() {
            let result = Arc::try_unwrap(arc_result).unwrap_or_else(|arc| (*arc).clone());
            self.cache_parse_result(&path, &result);
        }
    }
}

impl crate::cache::CacheLayer for IncrementalCache {
    fn name(&self) -> &str {
        "incremental-findings"
    }

    fn is_populated(&self) -> bool {
        self.has_cache()
    }

    fn invalidate_files(&mut self, changed_files: &[&std::path::Path]) {
        for path in changed_files {
            self.invalidate_file(path);
        }
    }

    fn invalidate_all(&mut self) {
        // Clear all cached data
        self.cache = CacheData::default();
        self.dirty = true;
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
        let tmp = TempDir::new().expect("should create temp dir");
        let cache = IncrementalCache::new(tmp.path());
        let stats = cache.stats();
        assert_eq!(stats.cached_files, 0);
        assert_eq!(stats.cache_version, CACHE_VERSION);
    }

    #[test]
    fn test_file_hash() {
        let tmp = TempDir::new().expect("should create temp dir");
        let file_path = tmp.path().join("test.txt");
        fs::write(&file_path, "hello world").expect("should write test file");

        let cache = IncrementalCache::new(tmp.path());
        let hash1 = cache.file_hash(&file_path);
        let hash2 = cache.file_hash(&file_path);

        // Same content should have same hash
        assert_eq!(hash1, hash2);

        // Different content should have different hash
        fs::write(&file_path, "changed content").expect("should write test file");
        let hash3 = cache.file_hash(&file_path);
        assert_ne!(hash1, hash3);
    }

    #[test]
    fn test_cache_findings() {
        let tmp = TempDir::new().expect("should create temp dir");
        let file_path = tmp.path().join("test.py");
        fs::write(&file_path, "def test(): pass").expect("should write test file");

        let mut cache = IncrementalCache::new(tmp.path());
        let finding = create_test_finding(&file_path.to_string_lossy());

        // Cache findings
        cache.cache_findings(&file_path, std::slice::from_ref(&finding));

        // Retrieve cached findings
        let cached = cache.cached_findings(&file_path);
        assert_eq!(cached.len(), 1);
        assert_eq!(cached[0].id, finding.id);
    }

    #[test]
    fn test_changed_files() {
        let tmp = TempDir::new().expect("should create temp dir");
        let file1 = tmp.path().join("file1.py");
        let file2 = tmp.path().join("file2.py");
        fs::write(&file1, "content1").expect("should write test file");
        fs::write(&file2, "content2").expect("should write test file");

        let mut cache = IncrementalCache::new(tmp.path());

        // Cache file1
        cache.cache_findings(&file1, &[]);

        // Check changed files
        let all_files = vec![file1.clone(), file2.clone()];
        let changed = cache.changed_files(&all_files);

        // Only file2 should be marked as changed (not in cache)
        assert_eq!(changed.len(), 1);
        assert_eq!(changed[0], file2);
    }

    #[test]
    fn test_graph_cache() {
        let tmp = TempDir::new().expect("should create temp dir");
        let mut cache = IncrementalCache::new(tmp.path());

        // Cache graph findings
        let finding = create_test_finding("test.py");
        cache.cache_graph_findings("TestDetector", &[finding]);
        cache.update_graph_hash("hash123");

        // Check graph cache
        assert!(!cache.is_graph_changed("hash123"));
        assert!(cache.is_graph_changed("different_hash"));

        let cached = cache.cached_graph_findings("TestDetector");
        assert_eq!(cached.len(), 1);
    }

    #[test]
    fn test_invalidation() {
        let tmp = TempDir::new().expect("should create temp dir");
        let file_path = tmp.path().join("test.py");
        fs::write(&file_path, "content").expect("should write test file");

        let mut cache = IncrementalCache::new(tmp.path());
        cache.cache_findings(&file_path, &[create_test_finding("test.py")]);

        assert_eq!(cache.stats().cached_files, 1);

        cache.invalidate_file(&file_path);
        assert_eq!(cache.stats().cached_files, 0);

        cache.cache_findings(&file_path, &[create_test_finding("test.py")]);
        cache.invalidate_all();
        assert_eq!(cache.stats().cached_files, 0);
    }

    #[test]
    fn test_incremental_cache_implements_cache_layer() {
        use crate::cache::CacheLayer;

        let tmp = TempDir::new().expect("should create temp dir");
        let mut cache = IncrementalCache::new(tmp.path());

        // Verify trait name
        assert_eq!(cache.name(), "incremental-findings");

        // Empty cache should not be populated
        assert!(!cache.is_populated());

        // Add file-level and parse-level entries to populate the cache
        let file_a = tmp.path().join("a.py");
        let file_b = tmp.path().join("b.py");
        fs::write(&file_a, "def foo(): pass").expect("should write test file");
        fs::write(&file_b, "def bar(): pass").expect("should write test file");

        cache.cache_findings(&file_a, &[create_test_finding("a.py")]);
        cache.cache_findings(&file_b, &[create_test_finding("b.py")]);

        // Now it should be populated
        assert!(cache.is_populated());
        assert_eq!(cache.stats().cached_files, 2);

        // invalidate_files should remove only the specified file
        let path_a_ref: &Path = &file_a;
        cache.invalidate_files(&[path_a_ref]);
        assert_eq!(cache.stats().cached_files, 1);
        // file_b should still be present
        assert!(cache.is_populated());

        // invalidate_all should clear everything
        cache.invalidate_all();
        assert!(!cache.is_populated());
        assert_eq!(cache.stats().cached_files, 0);
    }

    #[test]
    fn test_cached_finding_round_trip_preserves_threshold_metadata() {
        use crate::models::{Finding, Severity};
        use std::collections::BTreeMap;

        let mut meta = BTreeMap::new();
        meta.insert("threshold_source".to_string(), "adaptive".to_string());
        meta.insert("effective_threshold".to_string(), "15".to_string());

        let finding = Finding {
            id: "rt-1".into(),
            detector: "TestDetector".into(),
            severity: Severity::High,
            title: "Test".into(),
            description: "Desc".into(),
            confidence: Some(0.85),
            threshold_metadata: meta,
            ..Default::default()
        };

        let cached = CachedFinding::from(&finding);
        assert_eq!(
            cached.threshold_metadata.get("threshold_source").expect("key should exist"),
            "adaptive"
        );

        let restored = cached.to_finding();
        assert_eq!(restored.id, "rt-1");
        assert_eq!(restored.confidence, Some(0.85));
        assert_eq!(
            restored
                .threshold_metadata
                .get("effective_threshold")
                .expect("metadata key should exist"),
            "15"
        );
    }

    #[test]
    fn test_cache_value_dependencies_valid() {
        let dir = TempDir::new().unwrap();
        let cache_dir = dir.path().join("cache");
        std::fs::create_dir_all(&cache_dir).unwrap();
        let mut cache = IncrementalCache::new(&cache_dir);

        let file = dir.path().join("handler.py");
        fs::write(&file, "import config").unwrap();

        // Cache the file first
        cache.cache_findings(&file, &[]);

        // Set dependencies
        let deps = vec!["config.TIMEOUT".to_string()];
        let mut hashes = HashMap::new();
        hashes.insert("config.TIMEOUT".to_string(), 12345u64);
        cache.set_value_dependencies(&file, deps, hashes);

        // Check with same hash — should be valid
        let mut current = HashMap::new();
        current.insert("config.TIMEOUT".to_string(), 12345u64);
        assert!(cache.value_deps_valid(&file, &current));
    }

    #[test]
    fn test_cache_invalidates_on_value_dependency_change() {
        let dir = TempDir::new().unwrap();
        let cache_dir = dir.path().join("cache");
        std::fs::create_dir_all(&cache_dir).unwrap();
        let mut cache = IncrementalCache::new(&cache_dir);

        let file = dir.path().join("handler.py");
        fs::write(&file, "import config").unwrap();

        cache.cache_findings(&file, &[]);

        let deps = vec!["config.TIMEOUT".to_string()];
        let mut hashes = HashMap::new();
        hashes.insert("config.TIMEOUT".to_string(), 12345u64);
        cache.set_value_dependencies(&file, deps, hashes);

        // Check with different hash — should be invalid
        let mut current = HashMap::new();
        current.insert("config.TIMEOUT".to_string(), 99999u64);
        assert!(!cache.value_deps_valid(&file, &current));
    }

    #[test]
    fn test_cache_no_dependencies_always_valid() {
        let dir = TempDir::new().unwrap();
        let cache_dir = dir.path().join("cache");
        std::fs::create_dir_all(&cache_dir).unwrap();
        let mut cache = IncrementalCache::new(&cache_dir);

        let file = dir.path().join("simple.py");
        fs::write(&file, "x = 1").unwrap();

        cache.cache_findings(&file, &[]);

        // No dependencies set — should always be valid
        let current = HashMap::new();
        assert!(cache.value_deps_valid(&file, &current));
    }

    #[test]
    fn test_cache_missing_dependency_in_current_invalidates() {
        let dir = TempDir::new().unwrap();
        let cache_dir = dir.path().join("cache");
        std::fs::create_dir_all(&cache_dir).unwrap();
        let mut cache = IncrementalCache::new(&cache_dir);

        let file = dir.path().join("handler.py");
        fs::write(&file, "import config").unwrap();

        cache.cache_findings(&file, &[]);

        let deps = vec!["config.TIMEOUT".to_string()];
        let mut hashes = HashMap::new();
        hashes.insert("config.TIMEOUT".to_string(), 12345u64);
        cache.set_value_dependencies(&file, deps, hashes);

        // Dependency no longer in current hashes (e.g., constant was removed)
        let current = HashMap::new();
        assert!(!cache.value_deps_valid(&file, &current));
    }
}
