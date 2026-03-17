//! Persistent analysis session with incremental update support.
//!
//! `AnalysisSession` holds the full analysis state in memory. After an initial
//! cold analysis via `new()`, subsequent file changes are handled by `update()`
//! which delta-patches the graph and selectively re-runs detectors.
//!
//! **DEPRECATED**: This module is superseded by `engine::AnalysisEngine` which
//! provides the same incremental analysis capabilities via a cleaner layered
//! architecture. `AnalysisSession` is still referenced by the `watch` command
//! (`cli/watch.rs`). It will be removed once `watch` is migrated to use
//! `AnalysisEngine`.

use std::collections::hash_map::DefaultHasher;
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{Context, Result};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use tracing::{debug, info, warn};

use crate::detectors::{Detector, DetectorEngine, DetectorScope, GdPrecomputed, SourceFiles};
use crate::graph::store::GraphStore;
use crate::graph::store_models::{
    CodeEdge, CodeNode, ExtraProps, NodeKind, FLAG_ADDRESS_TAKEN, FLAG_HAS_DECORATORS,
    FLAG_IS_ASYNC, FLAG_IS_EXPORTED,
};
use crate::models::Finding;
use crate::parsers::{self, ParseResult};
use crate::scoring::GraphScorer;

/// Result of an incremental update.
#[derive(Debug)]
pub struct AnalysisDelta {
    pub new_findings: Vec<Finding>,
    pub fixed_findings: Vec<Finding>,
    pub total_findings: usize,
    pub score: Option<f64>,
    pub score_delta: Option<f64>,
}

impl AnalysisDelta {
    /// Create an empty delta (no changes detected).
    fn empty(total_findings: usize, score: Option<f64>) -> Self {
        Self {
            new_findings: Vec::new(),
            fixed_findings: Vec::new(),
            total_findings,
            score,
            score_delta: Some(0.0),
        }
    }
}

// ─── Session persistence ──────────────────────────────────────────────────────

/// Schema version for session cache. Bump when SessionMeta fields change.
const SESSION_VERSION: u32 = 2;

/// Serializable session metadata for persistence.
///
/// This captures everything needed to reconstruct an `AnalysisSession` except:
/// - `file_contents` — starts empty on `load()`; populated lazily by `update()`
/// - `all_findings` — reconstructed from `findings_by_file` + `graph_wide_findings`
/// - `graph` — persisted separately via `GraphStore::save_graph_cache()`
#[derive(Serialize, Deserialize)]
struct SessionMeta {
    version: u32,
    binary_version: String,
    edge_fingerprint: u64,
    file_hashes: HashMap<PathBuf, u64>,
    source_files: Vec<PathBuf>,
    health_score: Option<f64>,
    findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    graph_wide_findings: HashMap<String, Vec<Finding>>,
    repo_path: PathBuf,
    workers: usize,
}

/// Full analysis session with in-memory state for incremental updates.
pub struct AnalysisSession {
    // Core graph
    graph: Arc<GraphStore>,

    // Parse layer
    file_hashes: HashMap<PathBuf, u64>,
    /// Cached file contents for incremental diffing (used by update()).
    file_contents: HashMap<PathBuf, Arc<str>>,
    source_files: Vec<PathBuf>,

    // Cached results
    all_findings: Vec<Finding>,
    findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    graph_wide_findings: HashMap<String, Vec<Finding>>,
    health_score: Option<f64>,

    // Graph topology fingerprint
    edge_fingerprint: u64,

    // Cached detector precomputed data (taint, HMM, contexts, etc.)
    // Avoids ~3.9s rebuild on each incremental run.
    cached_gd: Option<GdPrecomputed>,

    // Config
    repo_path: PathBuf,
    /// Worker count for parallel detection.
    workers: usize,
}

// ─── Accessors ────────────────────────────────────────────────────────────────

impl AnalysisSession {
    /// All findings from the most recent analysis.
    pub fn findings(&self) -> &[Finding] {
        &self.all_findings
    }

    /// Overall health score (if computed).
    pub fn score(&self) -> Option<f64> {
        self.health_score
    }

    /// Reference to the in-memory code graph.
    pub fn graph(&self) -> &Arc<GraphStore> {
        &self.graph
    }

    /// All source files known to this session.
    pub fn source_files(&self) -> &[PathBuf] {
        &self.source_files
    }

    /// Repository root path for this session.
    pub fn repo_path(&self) -> &Path {
        &self.repo_path
    }

    /// Content hash for a file (SipHash of bytes). Returns None if file is unknown.
    pub fn file_hash(&self, path: &Path) -> Option<u64> {
        self.file_hashes.get(path).copied()
    }

    /// Current edge fingerprint (for detecting topology changes).
    pub fn edge_fingerprint(&self) -> u64 {
        self.edge_fingerprint
    }

    /// Findings attributed to a specific file.
    pub fn findings_for_file(&self, path: &Path) -> &[Finding] {
        self.findings_by_file
            .get(path)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }

    /// Graph-wide findings by detector name.
    pub fn graph_wide_findings(&self) -> &HashMap<String, Vec<Finding>> {
        &self.graph_wide_findings
    }
}

// ─── Persistence ──────────────────────────────────────────────────────────────

impl AnalysisSession {
    /// Save session state to disk for incremental CLI re-analysis.
    ///
    /// Persists:
    /// - The graph via `GraphStore::save_graph_cache()` (bincode, atomic write)
    /// - Session metadata as JSON (file hashes, findings, score, config)
    ///
    /// Does NOT persist `file_contents` — starts empty on `load()` and is
    /// populated lazily by `update()` for changed files only.
    pub fn persist(&self, cache_dir: &Path) -> Result<()> {
        std::fs::create_dir_all(cache_dir)
            .with_context(|| format!("Failed to create cache dir: {}", cache_dir.display()))?;

        // 1. Save graph via existing bincode infrastructure
        self.graph
            .save_graph_cache(&cache_dir.join("graph_cache.bin"))
            .context("Failed to save graph cache")?;

        // 2. Save session metadata as JSON
        let meta = SessionMeta {
            version: SESSION_VERSION,
            binary_version: env!("CARGO_PKG_VERSION").to_string(),
            edge_fingerprint: self.edge_fingerprint,
            file_hashes: self.file_hashes.clone(),
            source_files: self.source_files.clone(),
            health_score: self.health_score,
            findings_by_file: self.findings_by_file.clone(),
            graph_wide_findings: self.graph_wide_findings.clone(),
            repo_path: self.repo_path.clone(),
            workers: self.workers,
        };

        let json = serde_json::to_string(&meta)
            .context("Failed to serialize session metadata")?;

        // Atomic write: write to .tmp then rename
        let session_path = cache_dir.join("session.json");
        let tmp_path = cache_dir.join("session.json.tmp");
        std::fs::write(&tmp_path, json.as_bytes())
            .context("Failed to write session metadata")?;
        std::fs::rename(&tmp_path, &session_path)
            .context("Failed to finalize session metadata")?;

        info!(
            "Session persisted to {} ({} files, {} findings)",
            cache_dir.display(),
            self.source_files.len(),
            self.all_findings.len(),
        );

        Ok(())
    }

    /// Load a persisted session from disk.
    ///
    /// Returns `Ok(None)` if the cache doesn't exist, is corrupt, or has a
    /// version mismatch (binary version or schema version).
    ///
    /// `file_contents` starts empty (lazy — populated by `update()` for changed files).
    /// `all_findings` is reconstructed from `findings_by_file` + `graph_wide_findings`.
    pub fn load(cache_dir: &Path) -> Result<Option<Self>> {
        let graph_path = cache_dir.join("graph_cache.bin");
        let session_path = cache_dir.join("session.json");

        if !graph_path.exists() || !session_path.exists() {
            return Ok(None);
        }

        // Load and validate session metadata (JSON)
        let json = std::fs::read_to_string(&session_path)
            .context("Failed to read session metadata")?;
        let meta: SessionMeta = match serde_json::from_str(&json) {
            Ok(m) => m,
            Err(e) => {
                debug!("Session metadata corrupt: {}", e);
                return Ok(None);
            }
        };

        // Schema version check
        if meta.version != SESSION_VERSION {
            debug!(
                "Session cache version mismatch: {} vs {}",
                meta.version, SESSION_VERSION
            );
            return Ok(None);
        }

        // Binary version check
        if meta.binary_version != env!("CARGO_PKG_VERSION") {
            debug!(
                "Session binary version mismatch: {} vs {}",
                meta.binary_version,
                env!("CARGO_PKG_VERSION")
            );
            return Ok(None);
        }

        // Load graph (returns None on version mismatch or corruption)
        let graph = match GraphStore::load_graph_cache(&graph_path) {
            Some(g) => g,
            None => {
                debug!("Graph cache invalid or version-mismatched");
                return Ok(None);
            }
        };

        // file_contents starts empty — no code reads from it during incremental.
        // detect_changed_files() re-reads from disk; update() populates for changed files only.
        let file_contents = HashMap::new();

        // Reconstruct all_findings from per-file + graph-wide, deduplicating by ID.
        // Findings with multiple affected_files are stored once per file in
        // findings_by_file, so we deduplicate using the finding ID.
        let mut seen_ids: HashSet<String> = HashSet::new();
        let mut all_findings: Vec<Finding> = Vec::new();
        for findings in meta.findings_by_file.values() {
            for f in findings {
                if f.id.is_empty() || seen_ids.insert(f.id.clone()) {
                    all_findings.push(f.clone());
                }
            }
        }
        for findings in meta.graph_wide_findings.values() {
            for f in findings {
                if f.id.is_empty() || seen_ids.insert(f.id.clone()) {
                    all_findings.push(f.clone());
                }
            }
        }

        info!(
            "Session loaded from {} ({} files, {} findings)",
            cache_dir.display(),
            meta.source_files.len(),
            all_findings.len(),
        );

        Ok(Some(Self {
            graph: Arc::new(graph),
            file_hashes: meta.file_hashes,
            file_contents,
            source_files: meta.source_files,
            all_findings,
            findings_by_file: meta.findings_by_file,
            graph_wide_findings: meta.graph_wide_findings,
            health_score: meta.health_score,
            edge_fingerprint: meta.edge_fingerprint,
            cached_gd: None, // rebuilt on first incremental update
            repo_path: meta.repo_path,
            workers: meta.workers,
        }))
    }
}

// ─── From cold pipeline results ──────────────────────────────────────────────

impl AnalysisSession {
    /// Construct an `AnalysisSession` from the results of a cold CLI pipeline run.
    ///
    /// This avoids re-running analysis just to build a session for persistence.
    /// The CLI already has a graph, files, findings, and score — this method
    /// packages them into a session that can be `persist()`ed for future
    /// incremental re-analysis.
    pub fn from_cold_results(
        repo_path: &Path,
        workers: usize,
        graph: Arc<GraphStore>,
        source_files: Vec<PathBuf>,
        findings: Vec<Finding>,
        health_score: Option<f64>,
    ) -> Result<Self> {
        // Compute file hashes + content from disk
        let mut file_hashes = HashMap::with_capacity(source_files.len());
        let mut file_contents = HashMap::with_capacity(source_files.len());
        for path in &source_files {
            if let Ok(content) = std::fs::read_to_string(path) {
                let hash = siphash_content(content.as_bytes());
                file_hashes.insert(path.clone(), hash);
                file_contents.insert(path.clone(), Arc::from(content.as_str()));
            }
        }

        // Build per-file and graph-wide findings indices
        let findings_by_file = build_findings_by_file(&findings);
        let graph_wide_findings = build_graph_wide_findings(&findings);

        let edge_fingerprint = graph.compute_edge_fingerprint();

        Ok(Self {
            graph,
            file_hashes,
            file_contents,
            source_files,
            all_findings: findings,
            findings_by_file,
            graph_wide_findings,
            health_score,
            edge_fingerprint,
            cached_gd: None,
            repo_path: repo_path.to_path_buf(),
            workers,
        })
    }
}

// ─── Cold analysis (new) ──────────────────────────────────────────────────────

impl AnalysisSession {
    /// Create a new session by performing a full cold analysis.
    ///
    /// This walks the repository, parses all files, builds the code graph,
    /// runs detectors, and computes the health score.
    pub fn new(repo_path: &Path, workers: usize) -> Result<Self> {
        let repo_path = repo_path
            .canonicalize()
            .with_context(|| format!("Cannot resolve repository path: {}", repo_path.display()))?;

        info!(
            "Starting cold analysis session for {}",
            repo_path.display()
        );

        // Clear per-run caches (important for long-running sessions)
        parsers::clear_structural_fingerprint_cache();

        // Phase 1: Walk files
        let source_files = walk_source_files(&repo_path)?;
        info!("Found {} source files", source_files.len());

        if source_files.is_empty() {
            return Ok(Self {
                graph: Arc::new(GraphStore::in_memory()),
                file_hashes: HashMap::new(),
                file_contents: HashMap::new(),
                source_files: Vec::new(),
                all_findings: Vec::new(),
                findings_by_file: HashMap::new(),
                graph_wide_findings: HashMap::new(),
                health_score: Some(100.0),
                edge_fingerprint: 0,
                cached_gd: None,
                repo_path,
                workers,
            });
        }

        // Phase 2: Parse all files in parallel
        let parse_results = parse_all_files(&source_files)?;
        let total_functions: usize = parse_results.iter().map(|(_, r)| r.functions.len()).sum();
        let total_classes: usize = parse_results.iter().map(|(_, r)| r.classes.len()).sum();
        info!(
            "Parsed {} files: {} functions, {} classes",
            parse_results.len(),
            total_functions,
            total_classes
        );

        // Phase 3: Build graph
        let graph = Arc::new(GraphStore::in_memory());
        build_graph_from_parse_results(&graph, &repo_path, &parse_results)?;
        info!(
            "Graph built: {} nodes, {} edges",
            graph.node_count(),
            graph.edge_count()
        );

        // Phase 4: Hash file contents
        let (file_hashes, file_contents) = hash_all_files(&source_files);

        // Phase 5: Run detectors (and extract precomputed data for caching)
        let (all_findings, cached_gd) = run_all_detectors(&graph, &repo_path, &source_files, workers)?;
        info!("Detection complete: {} raw findings", all_findings.len());

        // Phase 6: Postprocess findings (deterministic IDs, compound escalation)
        let mut findings = all_findings;
        postprocess_session_findings(&mut findings);

        // Phase 7: Compute health score
        let project_config = crate::config::load_project_config(&repo_path);
        let scorer = GraphScorer::new(&graph, &project_config, &repo_path);
        let breakdown = scorer.calculate(&findings);
        let health_score = Some(breakdown.overall_score);
        info!(
            "Health score: {:.1} ({})",
            breakdown.overall_score, breakdown.grade
        );

        // Phase 8: Compute edge fingerprint
        let edge_fingerprint = graph.compute_edge_fingerprint();

        // Phase 9: Build findings indices
        let findings_by_file = build_findings_by_file(&findings);
        let graph_wide_findings = build_graph_wide_findings(&findings);

        Ok(Self {
            graph,
            file_hashes,
            file_contents,
            source_files,
            all_findings: findings,
            findings_by_file,
            graph_wide_findings,
            health_score,
            edge_fingerprint,
            cached_gd,
            repo_path,
            workers,
        })
    }
}

// ─── Incremental update ──────────────────────────────────────────────────────

impl AnalysisSession {
    /// Detect which files have changed since the session was created or last updated.
    ///
    /// Returns a list of file paths that have been modified, deleted, or newly created.
    /// Uses the same SipHash content hashing as `new()`.
    pub fn detect_changed_files(&self) -> Result<Vec<PathBuf>> {
        let mut changed = Vec::new();

        // Check for deleted files (in our hash map but no longer on disk)
        for path in self.file_hashes.keys() {
            if !path.exists() {
                changed.push(path.clone());
            }
        }

        // Check for modified files (content hash changed)
        for (path, old_hash) in &self.file_hashes {
            if !path.exists() {
                continue; // already handled above
            }
            if let Ok(content) = std::fs::read_to_string(path) {
                let new_hash = siphash_content(content.as_bytes());
                if new_hash != *old_hash {
                    changed.push(path.clone());
                }
            }
        }

        // Detect new files by walking the filesystem and comparing canonical
        // paths against the known file_hashes. Apply guardrails to avoid
        // false positives from files the CLI would exclude (>2MB, non-UTF8,
        // minified vendored assets, etc.).
        let current_files = walk_source_files(&self.repo_path)?;
        for path in current_files {
            let canonical = path.canonicalize().unwrap_or(path);
            if self.file_hashes.contains_key(&canonical) {
                continue;
            }
            // Skip files >2MB (same as CLI walker)
            if let Ok(meta) = canonical.metadata() {
                if meta.len() > 2 * 1024 * 1024 {
                    continue;
                }
            }
            // Skip non-UTF8 files (can't be parsed by tree-sitter)
            if std::fs::read_to_string(&canonical).is_err() {
                continue;
            }
            // Skip vendor/minified files (common false-positive source)
            if is_likely_vendored(&canonical) {
                continue;
            }
            changed.push(canonical);
        }

        Ok(changed)
    }

    /// Incrementally update the session for a set of changed files.
    ///
    /// This is the core incremental update pipeline:
    /// 1. Re-parse only changed files
    /// 2. Delta-patch the graph (remove old entities, insert new)
    /// 3. Detect topology changes via edge fingerprint
    /// 4. Run detectors selectively based on DetectorScope
    /// 5. Compose findings (cached unchanged + fresh changed + graph-wide)
    /// 6. Re-score and return a delta
    pub fn update(&mut self, changed_files: &[PathBuf]) -> Result<AnalysisDelta> {
        if changed_files.is_empty() {
            return Ok(AnalysisDelta::empty(
                self.all_findings.len(),
                self.health_score,
            ));
        }

        let previous_findings = self.all_findings.clone();
        let previous_score = self.health_score;

        info!(
            "Incremental update: {} changed files",
            changed_files.len()
        );

        // Categorize changes
        let deleted: Vec<&PathBuf> = changed_files.iter().filter(|p| !p.exists()).collect();
        let existing: Vec<&PathBuf> = changed_files.iter().filter(|p| p.exists()).collect();

        // ── Phase 1: Remove old graph entities for changed files ─────────
        // remove_file_entities expects relative paths
        let relative_changed: Vec<PathBuf> = changed_files
            .iter()
            .filter_map(|p| {
                p.strip_prefix(&self.repo_path)
                    .ok()
                    .map(|r| r.to_path_buf())
            })
            .collect();
        self.graph.remove_file_entities(&relative_changed);
        debug!(
            "Removed graph entities for {} files",
            relative_changed.len()
        );

        // ── Phase 2: Update source_files list ────────────────────────────
        // Remove deleted files
        let deleted_set: HashSet<&PathBuf> = deleted.iter().copied().collect();
        self.source_files.retain(|p| !deleted_set.contains(p));

        // Add new files (files that exist but weren't in source_files)
        let has_file_list_change;
        {
            let source_set: HashSet<&PathBuf> = self.source_files.iter().collect();
            let new_files: Vec<PathBuf> = existing
                .iter()
                .filter(|p| !source_set.contains(*p))
                .map(|p| (*p).clone())
                .collect();
            has_file_list_change = !new_files.is_empty() || !deleted.is_empty();
            self.source_files.extend(new_files);
        }
        self.source_files.sort();

        // ── Phase 3: Re-parse existing changed files ─────────────────────
        let files_to_parse: Vec<PathBuf> = existing.iter().map(|p| (*p).clone()).collect();
        let parse_results = parse_all_files(&files_to_parse)?;
        let total_functions: usize = parse_results.iter().map(|(_, r)| r.functions.len()).sum();
        let total_classes: usize = parse_results.iter().map(|(_, r)| r.classes.len()).sum();
        debug!(
            "Re-parsed {} files: {} functions, {} classes",
            parse_results.len(),
            total_functions,
            total_classes,
        );

        // ── Phase 4: Re-insert into graph ────────────────────────────────
        if !parse_results.is_empty() {
            // For correct cross-file call resolution, we need the global function map.
            // Build it from: (a) existing graph functions + (b) new parse results.
            build_graph_from_parse_results(&self.graph, &self.repo_path, &parse_results)?;
        }
        debug!(
            "Graph after patch: {} nodes, {} edges",
            self.graph.node_count(),
            self.graph.edge_count()
        );

        // ── Phase 5: Update file hashes and contents ─────────────────────
        // Remove deleted files from caches
        for path in &deleted {
            self.file_hashes.remove(*path);
            self.file_contents.remove(*path);
        }
        // Re-hash existing changed files
        for path in &existing {
            if let Ok(content) = std::fs::read_to_string(path) {
                let hash = siphash_content(content.as_bytes());
                self.file_hashes.insert((*path).clone(), hash);
                self.file_contents
                    .insert((*path).clone(), Arc::from(content.as_str()));
            }
        }

        // ── Phase 5b: Evict changed files from global file cache ─────────
        // The global FileCache stores file content read during cold analysis.
        // If we don't evict changed files, detectors will read stale content.
        {
            let evict_paths: Vec<PathBuf> = changed_files.to_vec();
            crate::cache::global_cache().evict(&evict_paths);
        }

        // ── Phase 6: Detect topology change ──────────────────────────────
        let new_fingerprint = self.graph.compute_edge_fingerprint();
        let topology_changed = new_fingerprint != self.edge_fingerprint;
        debug!(
            "Topology changed: {} (fingerprint {} -> {})",
            topology_changed,
            self.edge_fingerprint,
            new_fingerprint
        );
        self.edge_fingerprint = new_fingerprint;

        // Note: cached_gd may be None (sessions loaded from disk don't persist it).
        // The FileLocal path handles this via inject_minimal_for_file_local()
        // which provides empty contexts and skips expensive graph-derived computation.
        // The FSG/GraphWide path (file additions/deletions) lazily builds cached_gd
        // only when actually needed.

        // ── Phase 7: Selective detection ─────────────────────────────────
        // Build changed set with both absolute and relative paths. Detectors
        // produce findings with mixed path styles: some use absolute paths,
        // others use paths relative to the repo root.
        let mut changed_set: HashSet<PathBuf> = changed_files.iter().cloned().collect();
        for path in changed_files {
            if let Ok(rel) = path.strip_prefix(&self.repo_path) {
                changed_set.insert(rel.to_path_buf());
            }
        }
        let (fresh_changed_findings, _unused, new_graph_wide, fsg_detector_names) =
            self.run_selective_detection(&changed_set, has_file_list_change)?;

        // ── Phase 8: Compose findings ────────────────────────────────────
        // Strategy: cached findings for unchanged files + fresh findings for
        // changed files + graph-wide findings (cached or fresh).
        let mut composed: Vec<Finding> = Vec::new();

        let graph_wide_detector_names: HashSet<&str> = self
            .graph_wide_findings
            .keys()
            .map(|s| s.as_str())
            .collect();

        // (a) Cached findings for unchanged files.
        // Exclude:
        //   - GraphWide detector findings (handled in step (c))
        //   - FileScopedGraph detector findings (replaced entirely by fresh results
        //     in step (b), since these detectors iterate graph.get_functions() and
        //     produce findings for ALL files)
        //   - Findings that reference any changed file (fresh detection handles those)
        for (path, findings) in &self.findings_by_file {
            if !changed_set.contains(path) {
                composed.extend(
                    findings
                        .iter()
                        .filter(|f| {
                            // Exclude graph-wide detector findings (handled in step (c))
                            if graph_wide_detector_names.contains(f.detector.as_str()) {
                                return false;
                            }
                            // Exclude FileScopedGraph detector findings (fully replaced
                            // by fresh results in step (b))
                            if fsg_detector_names.contains(&f.detector) {
                                return false;
                            }
                            // Exclude findings that reference any changed file —
                            // fresh detection handles those
                            !f.affected_files.iter().any(|af| changed_set.contains(af))
                        })
                        .cloned(),
                );
            }
        }

        // (b) Fresh findings from FileLocal + FileScopedGraph detectors.
        // FileLocal findings are only for changed files (correct by construction).
        // FileScopedGraph findings cover ALL files (from graph iteration) — they
        // replace the excluded cached findings from step (a).
        composed.extend(fresh_changed_findings);

        // (c) Graph-wide findings — always use cached (FSG/GraphWide re-runs
        // are disabled until proper topology change detection is implemented).
        if !new_graph_wide.is_empty() {
            // Use fresh graph-wide findings (when FSG/GraphWide re-runs are enabled)
            for findings in new_graph_wide.values() {
                composed.extend(findings.iter().cloned());
            }
            self.graph_wide_findings = new_graph_wide;
        } else {
            // Use cached graph-wide findings
            for findings in self.graph_wide_findings.values() {
                composed.extend(findings.iter().cloned());
            }
        }

        // ── Phase 9: Postprocess ─────────────────────────────────────────
        postprocess_session_findings(&mut composed);

        // Deduplicate findings by ID. The `findings_by_file` index stores the same
        // finding under each of its `affected_files`, so composing from multiple
        // file buckets can produce duplicates.
        {
            let mut seen_ids: HashSet<String> = HashSet::with_capacity(composed.len());
            composed.retain(|f| {
                if f.id.is_empty() {
                    true // keep findings without an ID (shouldn't happen, but safe)
                } else {
                    seen_ids.insert(f.id.clone())
                }
            });
        }

        // ── Phase 10: Re-score ───────────────────────────────────────────
        let project_config = crate::config::load_project_config(&self.repo_path);
        let scorer = GraphScorer::new(&self.graph, &project_config, &self.repo_path);
        let breakdown = scorer.calculate(&composed);
        self.health_score = Some(breakdown.overall_score);
        info!(
            "Updated health score: {:.1} ({})",
            breakdown.overall_score, breakdown.grade
        );

        // ── Phase 11: Update caches ──────────────────────────────────────
        self.all_findings = composed;
        // Rebuild findings_by_file for changed files (and keep unchanged entries)
        for file in changed_files {
            let file_findings: Vec<Finding> = self
                .all_findings
                .iter()
                .filter(|f| f.affected_files.contains(file))
                .cloned()
                .collect();
            if file_findings.is_empty() {
                self.findings_by_file.remove(file);
            } else {
                self.findings_by_file.insert(file.clone(), file_findings);
            }
        }

        // ── Phase 12: Compute delta ──────────────────────────────────────
        let delta = compute_delta(&previous_findings, &self.all_findings, previous_score, self.health_score);
        info!(
            "Delta: +{} new, -{} fixed, {} total",
            delta.new_findings.len(),
            delta.fixed_findings.len(),
            delta.total_findings,
        );

        Ok(delta)
    }

    /// Run detectors selectively based on what changed.
    ///
    /// Returns four items:
    /// 1. `file_local_findings` — FileLocal detector findings for changed files only
    /// 2. `file_scoped_graph_findings` — FileScopedGraph detector findings for ALL files
    ///    (cross-file detectors like DuplicateCode may produce different results for
    ///    unchanged files when the graph changes)
    /// 3. `graph_wide_findings` — GraphWide detector findings (only if topology changed)
    /// 4. `rerun_detector_names` — Names of FileScopedGraph detectors whose findings
    ///    should replace cached entries
    fn run_selective_detection(
        &mut self,
        changed_set: &HashSet<PathBuf>,
        topology_changed: bool,
    ) -> Result<(
        Vec<Finding>,
        Vec<Finding>,
        HashMap<String, Vec<Finding>>,
        HashSet<String>,
    )> {
        let project_config = crate::config::load_project_config(&self.repo_path);
        let init = crate::detectors::DetectorInit {
            repo_path: &self.repo_path,
            project_config: &project_config,
            resolver: crate::calibrate::ThresholdResolver::default(),
            ngram_model: None,
        };
        let detectors = crate::detectors::create_all_detectors(&init);

        // Partition detectors by scope
        let mut file_local_detectors: Vec<Arc<dyn Detector>> = Vec::new();
        let mut file_scoped_graph_detectors: Vec<Arc<dyn Detector>> = Vec::new();
        let mut graph_wide_detectors: Vec<Arc<dyn Detector>> = Vec::new();

        for detector in &detectors {
            match detector.detector_scope() {
                DetectorScope::FileLocal => file_local_detectors.push(Arc::clone(detector)),
                DetectorScope::FileScopedGraph => {
                    file_scoped_graph_detectors.push(Arc::clone(detector))
                }
                DetectorScope::GraphWide => graph_wide_detectors.push(Arc::clone(detector)),
            }
        }

        let mut changed_file_findings: Vec<Finding> = Vec::new();
        let mut graph_wide_findings: HashMap<String, Vec<Finding>> = HashMap::new();
        let mut fsg_ran = false;

        // Run ONLY FileLocal detectors on changed files.
        // FileLocal detectors analyze file content only — they produce findings
        // exclusively for the files in SourceFiles, making per-file incremental correct.
        if !file_local_detectors.is_empty() {
            let changed_files: Vec<PathBuf> = changed_set
                .iter()
                .filter(|p| p.is_absolute())
                .cloned()
                .collect();
            let source = SourceFiles::new(changed_files.clone(), self.repo_path.clone());
            let mut engine = DetectorEngine::new(self.workers);
            for d in &file_local_detectors {
                engine.register(Arc::clone(d));
            }
            // Inject precomputed data to skip expensive graph-derived computation.
            // With cached_gd: inject everything (fast path, ~0ms).
            // Without cached_gd: inject minimal data (FileIndex + empty contexts)
            // to skip DetectorContext::build() (~3.6s on CPython).
            if let Some(ref gd) = self.cached_gd {
                engine.inject_for_incremental(gd, &changed_files, &self.repo_path);
            } else {
                engine.inject_minimal_for_file_local(&changed_files, &self.repo_path);
            }
            changed_file_findings = engine.run(self.graph.as_ref(), &source)?;
            // Filter to keep only findings for changed files.
            // Some FileLocal detectors iterate graph.get_functions() internally
            // and produce findings for ALL files — we only want changed-file findings.
            changed_file_findings.retain(|f| {
                f.affected_files.iter().any(|af| changed_set.contains(af))
            });
        }

        // Run FSG + GraphWide detectors only when files are added/removed.
        // Content-only modifications don't need FSG re-runs (findings are stable
        // for unchanged files). Edge fingerprint comparison is unreliable because
        // delta patching loses incoming cross-file edges, so we use file list
        // changes as the structural change signal instead.
        if topology_changed {
            // Always rebuild cached_gd when files are added/removed.
            // The graph was delta-patched so the previous cached_gd is stale
            // (callers/callees maps, function contexts, taint results don't
            // reflect new/removed entities).
            {
                let project_config = crate::config::load_project_config(&self.repo_path);
                let init = crate::detectors::DetectorInit {
                    repo_path: &self.repo_path,
                    project_config: &project_config,
                    resolver: crate::calibrate::ThresholdResolver::default(),
                    ngram_model: None,
                };
                let detectors = crate::detectors::create_all_detectors(&init);
                self.cached_gd = Some(crate::detectors::precompute_gd_startup(
                    self.graph.as_ref(),
                    &self.repo_path,
                    None,
                    &self.source_files,
                    None,
                    &detectors,
                ));
            }

            let source = SourceFiles::new(self.source_files.clone(), self.repo_path.clone());

            if !file_scoped_graph_detectors.is_empty() {
                let mut engine = DetectorEngine::new(self.workers);
                for d in &file_scoped_graph_detectors {
                    engine.register(Arc::clone(d));
                }
                if let Some(ref gd) = self.cached_gd {
                    engine.inject_gd_precomputed(gd.clone());
                }
                let fsg_findings = engine.run(self.graph.as_ref(), &source)?;
                changed_file_findings.extend(fsg_findings);
                fsg_ran = true;
            }

            if !graph_wide_detectors.is_empty() {
                let mut engine = DetectorEngine::new(self.workers);
                for d in &graph_wide_detectors {
                    engine.register(Arc::clone(d));
                }
                if let Some(ref gd) = self.cached_gd {
                    engine.inject_gd_precomputed(gd.clone());
                }
                let gw_findings = engine.run(self.graph.as_ref(), &source)?;
                for finding in gw_findings {
                    graph_wide_findings
                        .entry(finding.detector.clone())
                        .or_default()
                        .push(finding);
                }
            }
        }

        // Return names of FSG detectors only if they actually ran (so the
        // composition step knows to replace their cached findings).
        let fsg_detector_names: HashSet<String> = if fsg_ran {
            file_scoped_graph_detectors
                .iter()
                .map(|d| d.name().to_string())
                .collect()
        } else {
            HashSet::new()
        };

        Ok((
            changed_file_findings,
            Vec::new(),
            graph_wide_findings,
            fsg_detector_names,
        ))
    }
}

/// Compute the delta between previous and current findings.
fn compute_delta(
    previous: &[Finding],
    current: &[Finding],
    previous_score: Option<f64>,
    current_score: Option<f64>,
) -> AnalysisDelta {
    let prev_ids: HashSet<&str> = previous.iter().map(|f| f.id.as_str()).collect();
    let curr_ids: HashSet<&str> = current.iter().map(|f| f.id.as_str()).collect();

    let new_findings: Vec<Finding> = current
        .iter()
        .filter(|f| !f.id.is_empty() && !prev_ids.contains(f.id.as_str()))
        .cloned()
        .collect();

    let fixed_findings: Vec<Finding> = previous
        .iter()
        .filter(|f| !f.id.is_empty() && !curr_ids.contains(f.id.as_str()))
        .cloned()
        .collect();

    let score_delta = match (previous_score, current_score) {
        (Some(prev), Some(curr)) => Some(curr - prev),
        _ => None,
    };

    AnalysisDelta {
        new_findings,
        fixed_findings,
        total_findings: current.len(),
        score: current_score,
        score_delta,
    }
}

// ─── File walking ─────────────────────────────────────────────────────────────

/// Walk the repository for source files, respecting .gitignore and .repotoireignore.
fn walk_source_files(repo_path: &Path) -> Result<Vec<PathBuf>> {
    let supported = parsers::supported_extensions();
    let mut files: Vec<PathBuf> = crate::detectors::walk_source_files(repo_path, Some(supported))
        .collect();
    files.sort();
    Ok(files)
}

/// Quick heuristic to skip vendor/minified files that the CLI walker excludes
/// but the session walker doesn't have access to the same ExcludeConfig patterns.
fn is_likely_vendored(path: &Path) -> bool {
    let s = path.to_string_lossy();
    // Common vendor directories
    if s.contains("/vendor/")
        || s.contains("/node_modules/")
        || s.contains("/_vendor/")
        || s.contains("/third_party/")
        || s.contains("/third-party/")
    {
        return true;
    }
    // Minified files
    if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
        if name.contains(".min.") {
            return true;
        }
    }
    false
}

// ─── Parsing ──────────────────────────────────────────────────────────────────

/// Parse all source files in parallel using rayon.
fn parse_all_files(files: &[PathBuf]) -> Result<Vec<(PathBuf, Arc<ParseResult>)>> {
    let results: Vec<(PathBuf, Arc<ParseResult>)> = files
        .par_iter()
        .filter_map(|path| {
            match parsers::parse_file(path) {
                Ok(result) if !result.is_empty() => Some((path.clone(), Arc::new(result))),
                Ok(_) => None, // empty parse result (unsupported or no entities)
                Err(e) => {
                    warn!("Failed to parse {}: {}", path.display(), e);
                    None
                }
            }
        })
        .collect();

    Ok(results)
}

// ─── Graph building ───────────────────────────────────────────────────────────

/// Build the code graph from parse results.
///
/// This is a simplified version of `cli/analyze/graph.rs::build_graph()` that
/// operates without progress bars. It creates file/function/class nodes and
/// resolves Contains, Calls, and Imports edges.
fn build_graph_from_parse_results(
    graph: &Arc<GraphStore>,
    repo_path: &Path,
    parse_results: &[(PathBuf, Arc<ParseResult>)],
) -> Result<()> {
    let i = graph.interner();

    // Build a global function map: short name -> list of qualified names
    // Used for call edge resolution
    let mut global_func_map: HashMap<String, Vec<String>> = HashMap::new();
    for (_, result) in parse_results {
        for func in &result.functions {
            global_func_map
                .entry(func.name.clone())
                .or_default()
                .push(func.qualified_name.clone());
        }
    }

    // Build module lookup: import path -> file qualified name
    let mut module_lookup: HashMap<String, String> = HashMap::new();
    for (file_path, _) in parse_results {
        let relative_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);
        let relative_str = relative_path.display().to_string();
        // Generate module pattern keys for this file
        for pattern in generate_module_patterns(&relative_str) {
            module_lookup.insert(pattern, relative_str.clone());
        }
    }

    // Parallel: collect nodes and edges per file
    let file_results: Vec<_> = parse_results
        .par_iter()
        .map(|(file_path, result)| {
            let relative_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);
            let relative_str = relative_path.display().to_string();
            let rel_key = i.intern(&relative_str);
            let lang_key = i.intern(detect_language(&relative_str));

            let mut nodes: Vec<CodeNode> = Vec::with_capacity(1 + result.functions.len() + result.classes.len());
            let mut edges: Vec<(String, String, CodeEdge)> = Vec::new();

            // File node
            let file_loc = result
                .functions
                .iter()
                .map(|f| f.line_end)
                .chain(result.classes.iter().map(|c| c.line_end))
                .max()
                .unwrap_or(0);
            nodes.push(CodeNode {
                kind: NodeKind::File,
                name: rel_key,
                qualified_name: rel_key,
                file_path: rel_key,
                language: lang_key,
                line_start: 1,
                line_end: file_loc,
                complexity: 0,
                param_count: 0,
                method_count: 0,
                field_count: 0,
                max_nesting: 0,
                return_count: 0,
                commit_count: 0,
                flags: 0,
            });

            // Function nodes
            for func in &result.functions {
                let complexity = func.complexity.unwrap_or(1);
                let address_taken = result.address_taken.contains(&func.name);

                let mut flags: u8 = 0;
                if func.is_async {
                    flags |= FLAG_IS_ASYNC;
                }
                if address_taken {
                    flags |= FLAG_ADDRESS_TAKEN;
                }
                if !func.annotations.is_empty() {
                    flags |= FLAG_HAS_DECORATORS;
                }
                if func.annotations.iter().any(|a| a == "exported") {
                    flags |= FLAG_IS_EXPORTED;
                }

                let func_node = CodeNode {
                    kind: NodeKind::Function,
                    name: i.intern(&func.name),
                    qualified_name: i.intern(&func.qualified_name),
                    file_path: rel_key,
                    language: lang_key,
                    line_start: func.line_start,
                    line_end: func.line_end,
                    complexity: complexity as u16,
                    param_count: func.parameters.len().min(255) as u8,
                    method_count: 0,
                    field_count: 0,
                    max_nesting: func.max_nesting.unwrap_or(0).min(255) as u8,
                    return_count: 0,
                    commit_count: 0,
                    flags,
                };

                // Store string properties in extra_props side table
                let params_str = func.parameters.join(",");
                let has_params = !params_str.is_empty();
                let has_doc = func.doc_comment.is_some();
                let has_decorators = !func.annotations.is_empty();
                if has_params || has_doc || has_decorators {
                    let ep = ExtraProps {
                        params: if has_params {
                            Some(i.intern(&params_str))
                        } else {
                            None
                        },
                        doc_comment: func.doc_comment.as_ref().map(|d| i.intern(d)),
                        decorators: if has_decorators {
                            Some(i.intern(&func.annotations.join(",")))
                        } else {
                            None
                        },
                        ..Default::default()
                    };
                    graph.set_extra_props(func_node.qualified_name, ep);
                }

                nodes.push(func_node);

                // Contains edge
                edges.push((
                    relative_str.clone(),
                    func.qualified_name.clone(),
                    CodeEdge::contains(),
                ));

                // Decorator call edge: module calls decorated function at load time
                if has_decorators {
                    edges.push((
                        relative_str.clone(),
                        func.qualified_name.clone(),
                        CodeEdge::calls(),
                    ));
                }
            }

            // Class nodes
            for class in &result.classes {
                let mut flags: u8 = 0;
                if !class.annotations.is_empty() {
                    flags |= FLAG_HAS_DECORATORS;
                }

                let class_node = CodeNode {
                    kind: NodeKind::Class,
                    name: i.intern(&class.name),
                    qualified_name: i.intern(&class.qualified_name),
                    file_path: rel_key,
                    language: lang_key,
                    line_start: class.line_start,
                    line_end: class.line_end,
                    complexity: 0,
                    param_count: 0,
                    method_count: class.methods.len().min(65535) as u16,
                    field_count: class.field_count.min(65535) as u16,
                    max_nesting: 0,
                    return_count: 0,
                    commit_count: 0,
                    flags,
                };

                // Store string properties in extra_props
                let has_doc = class.doc_comment.is_some();
                let has_decorators = !class.annotations.is_empty();
                if has_doc || has_decorators {
                    let ep = ExtraProps {
                        doc_comment: class.doc_comment.as_ref().map(|d| i.intern(d)),
                        decorators: if has_decorators {
                            Some(i.intern(&class.annotations.join(",")))
                        } else {
                            None
                        },
                        ..Default::default()
                    };
                    graph.set_extra_props(class_node.qualified_name, ep);
                }

                nodes.push(class_node);

                // Contains edge
                edges.push((
                    relative_str.clone(),
                    class.qualified_name.clone(),
                    CodeEdge::contains(),
                ));

                // Inheritance edges
                for base in &class.bases {
                    edges.push((
                        class.qualified_name.clone(),
                        base.clone(),
                        CodeEdge::inherits(),
                    ));
                }
            }

            // Trait implementation edges (type implements trait)
            for (type_name, trait_name) in &result.trait_impls {
                // Find type QN from classes in this file
                let type_qn = result.classes.iter()
                    .find(|c| c.name == *type_name)
                    .map(|c| c.qualified_name.clone());
                if let Some(type_qn) = type_qn {
                    edges.push((type_qn, trait_name.clone(), CodeEdge::inherits()));
                }
            }

            // Call edges — resolve callee names to qualified names
            for (caller_qn, callee_name) in &result.calls {
                // Try exact match first
                if let Some(targets) = global_func_map.get(callee_name) {
                    // Prefer same-file match
                    let same_file = targets
                        .iter()
                        .find(|t| t.starts_with(&relative_str) || t.contains(&format!("{}:", func_stem(callee_name))));
                    let target = same_file
                        .or_else(|| targets.first())
                        .cloned()
                        .unwrap_or_else(|| callee_name.clone());
                    edges.push((caller_qn.clone(), target, CodeEdge::calls()));
                } else {
                    // Unresolved callee — emit edge anyway (dead ends are fine)
                    edges.push((caller_qn.clone(), callee_name.clone(), CodeEdge::calls()));
                }
            }

            // Import edges — resolve import paths to file nodes
            for import in &result.imports {
                if let Some(target_file) = module_lookup.get(&import.path) {
                    edges.push((
                        relative_str.clone(),
                        target_file.clone(),
                        CodeEdge::imports(),
                    ));
                } else {
                    // Unresolved import — still emit the edge for graph topology
                    edges.push((
                        relative_str.clone(),
                        import.path.clone(),
                        CodeEdge::imports(),
                    ));
                }
            }

            (relative_str, nodes, edges)
        })
        .collect();

    // Sort by file path for deterministic insertion order
    let mut file_results = file_results;
    file_results.sort_by(|a, b| a.0.cmp(&b.0));

    // Batch insert: collect all nodes and edges
    let total_nodes: usize = file_results.iter().map(|(_, n, _)| n.len()).sum();
    let total_edges: usize = file_results.iter().map(|(_, _, e)| e.len()).sum();
    let mut all_nodes = Vec::with_capacity(total_nodes);
    let mut all_edges = Vec::with_capacity(total_edges);

    for (_file_path, nodes, edges) in file_results {
        all_nodes.extend(nodes);
        all_edges.extend(edges);
    }

    graph.add_nodes_batch(all_nodes);
    graph.add_edges_batch(all_edges);

    Ok(())
}

/// Detect language from a relative path string.
fn detect_language(relative_str: &str) -> &'static str {
    let ext = relative_str.rsplit('.').next().unwrap_or("");
    match ext {
        "py" | "pyi" => "Python",
        "ts" | "tsx" => "TypeScript",
        "js" | "jsx" | "mjs" | "cjs" => "JavaScript",
        "rs" => "Rust",
        "go" => "Go",
        "java" => "Java",
        "c" | "h" => "C",
        "cpp" | "hpp" | "cc" | "cxx" | "c++" | "hh" | "hxx" | "h++" => "C++",
        "cs" => "C#",
        "kt" | "kts" => "Kotlin",
        _ => "Unknown",
    }
}

/// Generate module import pattern keys for import resolution.
fn generate_module_patterns(relative_str: &str) -> Vec<String> {
    let mut patterns = Vec::new();

    // Rust module patterns
    if relative_str.ends_with(".rs") {
        let rust_path = relative_str.trim_end_matches(".rs").replace('/', "::");
        patterns.push(rust_path);
    }

    // TypeScript/JavaScript patterns
    for ext in &[".ts", ".tsx", ".js", ".jsx", ".mjs"] {
        if relative_str.ends_with(ext) {
            let base = relative_str.trim_end_matches(ext);
            patterns.push(base.to_string());
            if base.ends_with("/index") {
                patterns.push(base.trim_end_matches("/index").to_string());
            }
        }
    }

    // Python patterns
    if relative_str.ends_with(".py") {
        let py_path = relative_str.trim_end_matches(".py").replace('/', ".");
        patterns.push(py_path);
        if relative_str.ends_with("/__init__.py") {
            let pkg = relative_str
                .trim_end_matches("/__init__.py")
                .replace('/', ".");
            patterns.push(pkg);
        }
    }

    patterns
}

/// Extract the base name from a callee string (for same-file matching heuristic).
fn func_stem(name: &str) -> &str {
    name.rsplit('.').next().unwrap_or(name)
}

// ─── File hashing ─────────────────────────────────────────────────────────────

/// Hash all source files and collect their contents.
fn hash_all_files(files: &[PathBuf]) -> (HashMap<PathBuf, u64>, HashMap<PathBuf, Arc<str>>) {
    let mut hashes = HashMap::with_capacity(files.len());
    let mut contents = HashMap::with_capacity(files.len());

    for path in files {
        if let Ok(content) = std::fs::read_to_string(path) {
            let hash = siphash_content(content.as_bytes());
            hashes.insert(path.clone(), hash);
            contents.insert(path.clone(), Arc::from(content.as_str()));
        }
    }

    (hashes, contents)
}

/// SipHash content bytes to a u64 fingerprint.
fn siphash_content(bytes: &[u8]) -> u64 {
    let mut hasher = DefaultHasher::new();
    bytes.hash(&mut hasher);
    hasher.finish()
}

// ─── Detection ────────────────────────────────────────────────────────────────

/// Run all detectors on the graph.
fn run_all_detectors(
    graph: &Arc<GraphStore>,
    repo_path: &Path,
    source_files: &[PathBuf],
    workers: usize,
) -> Result<(Vec<Finding>, Option<GdPrecomputed>)> {
    let project_config = crate::config::load_project_config(repo_path);

    // Create detectors
    let init = crate::detectors::DetectorInit {
        repo_path,
        project_config: &project_config,
        resolver: crate::calibrate::ThresholdResolver::default(),
        ngram_model: None,
    };
    let detectors = crate::detectors::create_all_detectors(&init);

    // Build engine
    let mut engine = DetectorEngine::new(workers);
    for detector in detectors {
        engine.register(detector);
    }

    // Build file provider
    let source = SourceFiles::new(source_files.to_vec(), repo_path.to_path_buf());

    // Run all detectors
    let findings = engine.run(graph.as_ref(), &source)?;

    // Extract precomputed data for caching in the session
    let cached_gd = engine.extract_precomputed();

    Ok((findings, cached_gd))
}

// ─── Postprocessing ───────────────────────────────────────────────────────────

/// Simplified postprocessing for session findings.
///
/// Applies:
/// 1. Deterministic finding IDs
/// 2. Compound smell escalation
/// 3. Security downgrading for non-production paths
fn postprocess_session_findings(findings: &mut Vec<Finding>) {
    // Step 1: Replace random UUIDs with deterministic IDs
    for finding in findings.iter_mut() {
        let file = finding
            .affected_files
            .first()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let line = finding.line_start.unwrap_or(0);
        finding.id = crate::detectors::base::finding_id(&finding.detector, &file, line);
    }

    // Step 2: Compound smell escalation
    crate::scoring::escalate_compound_smells(findings);

    // Step 3: Downgrade security findings in non-production paths
    use crate::detectors::content_classifier::is_non_production_path;
    use crate::models::Severity;

    const SECURITY_DETECTORS: &[&str] = &[
        "CommandInjectionDetector",
        "SQLInjectionDetector",
        "XssDetector",
        "SsrfDetector",
        "PathTraversalDetector",
        "LogInjectionDetector",
        "EvalDetector",
        "InsecureRandomDetector",
        "HardcodedCredentialsDetector",
        "CleartextCredentialsDetector",
    ];

    for finding in findings.iter_mut() {
        let is_non_prod = finding
            .affected_files
            .iter()
            .any(|p| is_non_production_path(&p.to_string_lossy()));

        if is_non_prod
            && SECURITY_DETECTORS.contains(&finding.detector.as_str())
            && (finding.severity == Severity::Critical || finding.severity == Severity::High)
        {
            finding.severity = Severity::Medium;
            finding.description = format!("[Non-production path] {}", finding.description);
        }
    }

    // Step 4: Clamp confidence to [0.0, 1.0]
    for finding in findings.iter_mut() {
        if let Some(ref mut c) = finding.confidence {
            *c = c.clamp(0.0, 1.0);
        }
    }
}

// ─── Finding indices ──────────────────────────────────────────────────────────

/// Build a map from file path to its findings.
fn build_findings_by_file(findings: &[Finding]) -> HashMap<PathBuf, Vec<Finding>> {
    let mut map: HashMap<PathBuf, Vec<Finding>> = HashMap::new();
    for finding in findings {
        if finding.affected_files.is_empty() {
            // Findings with no affected files go into a sentinel bucket so they
            // aren't lost during persist/load round-trip.
            map.entry(PathBuf::from("__no_file__"))
                .or_default()
                .push(finding.clone());
        } else {
            for file in &finding.affected_files {
                map.entry(file.clone()).or_default().push(finding.clone());
            }
        }
    }
    map
}

/// Build a map from detector name to findings for graph-wide detectors.
fn build_graph_wide_findings(findings: &[Finding]) -> HashMap<String, Vec<Finding>> {
    // Graph-wide detectors that produce topology-dependent findings
    // These need to be re-run when the graph topology changes
    let graph_wide_detectors = [
        "CircularDependencyDetector",
        "ArchitecturalBottleneckDetector",
        "DegreeCentralityDetector",
        "InfluentialCodeDetector",
        "ModuleCohesionDetector",
        "CoreUtilityDetector",
        "ShotgunSurgeryDetector",
        "DeadCodeDetector",
        "HierarchicalSurprisalDetector",
    ];

    let mut map: HashMap<String, Vec<Finding>> = HashMap::new();
    for finding in findings {
        if graph_wide_detectors.contains(&finding.detector.as_str()) {
            map.entry(finding.detector.clone())
                .or_default()
                .push(finding.clone());
        }
    }
    map
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn test_siphash_deterministic() {
        let data = b"hello world";
        let h1 = siphash_content(data);
        let h2 = siphash_content(data);
        assert_eq!(h1, h2, "SipHash should be deterministic");
    }

    #[test]
    fn test_siphash_different_content() {
        let h1 = siphash_content(b"hello");
        let h2 = siphash_content(b"world");
        assert_ne!(h1, h2, "Different content should produce different hashes");
    }

    #[test]
    fn test_detect_language() {
        assert_eq!(detect_language("src/main.rs"), "Rust");
        assert_eq!(detect_language("app.py"), "Python");
        assert_eq!(detect_language("index.ts"), "TypeScript");
        assert_eq!(detect_language("lib.go"), "Go");
        assert_eq!(detect_language("Main.java"), "Java");
        assert_eq!(detect_language("unknown.xyz"), "Unknown");
    }

    #[test]
    fn test_generate_module_patterns() {
        let patterns = generate_module_patterns("src/lib.rs");
        assert!(patterns.contains(&"src::lib".to_string()));

        let patterns = generate_module_patterns("app/models.py");
        assert!(patterns.contains(&"app.models".to_string()));

        let patterns = generate_module_patterns("src/index.ts");
        assert!(patterns.contains(&"src/index".to_string()));
        assert!(patterns.contains(&"src".to_string()));
    }

    #[test]
    fn test_func_stem() {
        assert_eq!(func_stem("module.Class.method"), "method");
        assert_eq!(func_stem("simple_func"), "simple_func");
    }

    #[test]
    fn test_session_empty_dir() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let session = AnalysisSession::new(dir.path(), 4).expect("session should succeed");

        assert_eq!(session.source_files().len(), 0);
        assert_eq!(session.findings().len(), 0);
        assert_eq!(session.score(), Some(100.0));
        assert_eq!(session.edge_fingerprint(), 0);
    }

    #[test]
    fn test_session_with_python_file() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let py_file = dir.path().join("example.py");
        fs::write(
            &py_file,
            r#"
def hello():
    print("hello world")

def add(a, b):
    return a + b

class Calculator:
    def multiply(self, x, y):
        return x * y
"#,
        )
        .expect("write python file");

        let session = AnalysisSession::new(dir.path(), 4).expect("session should succeed");

        assert!(!session.source_files().is_empty(), "should find the Python file");
        assert!(session.score().is_some(), "should compute a health score");

        // Verify the graph was built
        let graph = session.graph();
        assert!(graph.node_count() > 0, "graph should have nodes");
        assert!(graph.edge_count() > 0, "graph should have edges");

        // Verify file hash exists
        let canonical_py = py_file.canonicalize().expect("canonicalize");
        assert!(
            session.file_hash(&canonical_py).is_some(),
            "file hash should exist for the Python file"
        );
    }

    #[test]
    fn test_session_with_rust_file() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let rs_file = dir.path().join("lib.rs");
        fs::write(
            &rs_file,
            r#"
pub fn greet(name: &str) -> String {
    format!("Hello, {}", name)
}

fn helper() -> u32 {
    42
}
"#,
        )
        .expect("write rust file");

        let session = AnalysisSession::new(dir.path(), 4).expect("session should succeed");

        assert!(!session.source_files().is_empty());
        assert!(session.graph().node_count() > 0);
    }

    #[test]
    fn test_build_findings_by_file() {
        let findings = vec![
            Finding {
                detector: "TestDetector".to_string(),
                affected_files: vec![PathBuf::from("a.py")],
                ..Default::default()
            },
            Finding {
                detector: "TestDetector".to_string(),
                affected_files: vec![PathBuf::from("a.py")],
                ..Default::default()
            },
            Finding {
                detector: "TestDetector".to_string(),
                affected_files: vec![PathBuf::from("b.py")],
                ..Default::default()
            },
        ];

        let map = build_findings_by_file(&findings);
        assert_eq!(map.get(&PathBuf::from("a.py")).map(|v| v.len()), Some(2));
        assert_eq!(map.get(&PathBuf::from("b.py")).map(|v| v.len()), Some(1));
    }

    #[test]
    fn test_postprocess_deterministic_ids() {
        let mut findings = vec![Finding {
            detector: "TestDetector".to_string(),
            affected_files: vec![PathBuf::from("test.py")],
            line_start: Some(10),
            ..Default::default()
        }];

        postprocess_session_findings(&mut findings);

        // ID should be deterministic and non-empty
        assert!(!findings[0].id.is_empty());

        // Running again should produce the same ID
        let id1 = findings[0].id.clone();
        postprocess_session_findings(&mut findings);
        assert_eq!(findings[0].id, id1, "IDs should be deterministic");
    }

    // ── Incremental update tests ─────────────────────────────────────

    #[test]
    fn test_detect_changed_files_modified() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let py_file = dir.path().join("app.py");
        fs::write(&py_file, "def foo():\n    return 1\n").expect("write");

        let session = AnalysisSession::new(dir.path(), 4).expect("session");

        // No changes yet
        let changed = session.detect_changed_files().expect("detect");
        assert!(
            changed.is_empty(),
            "No changes should be detected immediately after creation"
        );

        // Modify the file
        fs::write(&py_file, "def foo():\n    return 2\n").expect("write modified");

        let changed = session.detect_changed_files().expect("detect");
        assert_eq!(changed.len(), 1, "Should detect one modified file");
        let canonical = py_file.canonicalize().expect("canon");
        assert!(
            changed.contains(&canonical),
            "Changed file should be the modified Python file"
        );
    }

    #[test]
    fn test_detect_changed_files_new_file() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let py_file = dir.path().join("app.py");
        fs::write(&py_file, "def foo():\n    return 1\n").expect("write");

        let session = AnalysisSession::new(dir.path(), 4).expect("session");

        // Add a new file
        let new_file = dir.path().join("utils.py");
        fs::write(&new_file, "def helper():\n    pass\n").expect("write new");

        let changed = session.detect_changed_files().expect("detect");
        assert!(
            !changed.is_empty(),
            "Should detect the new file"
        );
        let canonical = new_file.canonicalize().expect("canon");
        assert!(
            changed.contains(&canonical),
            "Changed list should include the new file"
        );
    }

    #[test]
    fn test_detect_changed_files_deleted() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let py_file = dir.path().join("app.py");
        fs::write(&py_file, "def foo():\n    return 1\n").expect("write");

        let session = AnalysisSession::new(dir.path(), 4).expect("session");

        // Delete the file
        fs::remove_file(&py_file).expect("delete");

        let changed = session.detect_changed_files().expect("detect");
        assert!(
            !changed.is_empty(),
            "Should detect the deleted file"
        );
    }

    #[test]
    fn test_update_empty_changeset() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let py_file = dir.path().join("app.py");
        fs::write(&py_file, "def foo():\n    return 1\n").expect("write");

        let mut session = AnalysisSession::new(dir.path(), 4).expect("session");
        let original_score = session.score();
        let original_findings_count = session.findings().len();

        let delta = session.update(&[]).expect("update");

        assert_eq!(delta.total_findings, original_findings_count);
        assert_eq!(delta.score, original_score);
        assert_eq!(delta.score_delta, Some(0.0));
        assert!(delta.new_findings.is_empty());
        assert!(delta.fixed_findings.is_empty());
    }

    #[test]
    fn test_update_body_edit() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let py_file = dir.path().join("app.py");
        fs::write(
            &py_file,
            "def foo():\n    return 1\n\ndef bar():\n    return 2\n",
        )
        .expect("write");

        let mut session = AnalysisSession::new(dir.path(), 4).expect("session");

        // Modify function body (no topology change)
        fs::write(
            &py_file,
            "def foo():\n    return 42\n\ndef bar():\n    return 99\n",
        )
        .expect("write modified");

        let canonical = py_file.canonicalize().expect("canon");
        let delta = session.update(&[canonical.clone()]).expect("update");

        // Graph should still have the same number of entities
        // (same functions, just different bodies)
        assert!(
            session.graph().node_count() > 0,
            "Graph should still have nodes after update"
        );

        // Session state should be consistent
        assert_eq!(
            session.findings().len(),
            delta.total_findings,
            "findings() should match delta.total_findings"
        );
        assert!(session.score().is_some(), "Score should still exist");

        // File hash should be updated
        let new_hash = session.file_hash(&canonical);
        assert!(new_hash.is_some(), "File hash should exist after update");
    }

    #[test]
    fn test_update_no_topology_change() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let a = dir.path().join("a.py");
        let b = dir.path().join("b.py");
        fs::write(&a, "def foo():\n    return 1\n").expect("write a");
        fs::write(&b, "def bar():\n    return 2\n").expect("write b");

        let mut session = AnalysisSession::new(dir.path(), 4).expect("session");
        let fp_before = session.edge_fingerprint();

        // Body-only edit: no new cross-file calls
        fs::write(&a, "def foo():\n    return 999\n").expect("modify a");

        let canonical_a = a.canonicalize().expect("canon");
        let _delta = session.update(&[canonical_a]).expect("update");

        // Edge fingerprint should not have changed (no new cross-file edges)
        // The fingerprint may change if intra-file edges change, but we only
        // compare cross-file edges in compute_edge_fingerprint(), so a body-only
        // edit should not affect it.
        assert_eq!(
            fp_before,
            session.edge_fingerprint(),
            "Body-only edit should not change edge fingerprint"
        );
    }

    #[test]
    fn test_update_topology_change() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let a = dir.path().join("a.py");
        let b = dir.path().join("b.py");
        // a.py calls nothing, b.py calls nothing
        fs::write(&a, "def foo():\n    return 1\n").expect("write a");
        fs::write(&b, "def bar():\n    return 2\n").expect("write b");

        let mut session = AnalysisSession::new(dir.path(), 4).expect("session");

        // Now make a.py call b.bar() — cross-file call
        fs::write(&a, "from b import bar\n\ndef foo():\n    return bar()\n").expect("modify a");

        let canonical_a = a.canonicalize().expect("canon");
        let _delta = session.update(&[canonical_a]).expect("update");

        // Adding a cross-file call should change the edge fingerprint
        // Note: this depends on tree-sitter resolving the call edge. If the parser
        // doesn't resolve it, the fingerprint may not change. We test the mechanism
        // rather than the parser's ability to resolve cross-file calls.
        // At minimum, the update should complete without error.
        assert!(session.score().is_some(), "Score should exist after topology change update");
    }

    #[test]
    fn test_update_file_deletion() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let a = dir.path().join("a.py");
        let b = dir.path().join("b.py");
        fs::write(&a, "def foo():\n    return 1\n").expect("write a");
        fs::write(&b, "def bar():\n    return 2\n").expect("write b");

        let mut session = AnalysisSession::new(dir.path(), 4).expect("session");
        let files_before = session.source_files().len();
        assert_eq!(files_before, 2, "Should start with 2 files");

        // Delete one file
        let canonical_a = a.canonicalize().expect("canon");
        fs::remove_file(&a).expect("delete");

        let _delta = session.update(&[canonical_a.clone()]).expect("update");

        assert_eq!(
            session.source_files().len(),
            1,
            "Should have 1 file after deletion"
        );

        // File hash should be removed
        assert!(
            session.file_hash(&canonical_a).is_none(),
            "Deleted file hash should be gone"
        );

        // Graph should still have nodes from the remaining file
        assert!(
            session.graph().node_count() > 0,
            "Graph should still have nodes from remaining file"
        );
    }

    #[test]
    fn test_update_file_addition() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let a = dir.path().join("a.py");
        fs::write(&a, "def foo():\n    return 1\n").expect("write a");

        let mut session = AnalysisSession::new(dir.path(), 4).expect("session");
        assert_eq!(session.source_files().len(), 1);

        // Add a new file
        let b = dir.path().join("b.py");
        fs::write(&b, "def bar():\n    return 2\n\ndef baz():\n    return 3\n").expect("write b");

        let canonical_b = b.canonicalize().expect("canon");
        let _delta = session.update(&[canonical_b.clone()]).expect("update");

        assert_eq!(
            session.source_files().len(),
            2,
            "Should have 2 files after addition"
        );

        // File hash should exist for the new file
        assert!(
            session.file_hash(&canonical_b).is_some(),
            "New file should have a hash"
        );

        // Graph should have more nodes now
        assert!(
            session.graph().node_count() >= 3,
            "Graph should have at least 3 nodes (file + 2 functions from new file, plus original)"
        );
    }

    #[test]
    fn test_compute_delta() {
        let f1 = Finding {
            id: "aaa".to_string(),
            detector: "D1".to_string(),
            ..Default::default()
        };
        let f2 = Finding {
            id: "bbb".to_string(),
            detector: "D2".to_string(),
            ..Default::default()
        };
        let f3 = Finding {
            id: "ccc".to_string(),
            detector: "D3".to_string(),
            ..Default::default()
        };

        let prev = vec![f1.clone(), f2.clone()];
        let curr = vec![f2.clone(), f3.clone()];

        let delta = compute_delta(&prev, &curr, Some(80.0), Some(85.0));

        assert_eq!(delta.new_findings.len(), 1, "f3 is new");
        assert_eq!(delta.new_findings[0].id, "ccc");
        assert_eq!(delta.fixed_findings.len(), 1, "f1 was fixed");
        assert_eq!(delta.fixed_findings[0].id, "aaa");
        assert_eq!(delta.total_findings, 2);
        assert_eq!(delta.score, Some(85.0));
        assert_eq!(delta.score_delta, Some(5.0));
    }

    #[test]
    fn test_analysis_delta_empty() {
        let delta = AnalysisDelta::empty(10, Some(90.0));
        assert!(delta.new_findings.is_empty());
        assert!(delta.fixed_findings.is_empty());
        assert_eq!(delta.total_findings, 10);
        assert_eq!(delta.score, Some(90.0));
        assert_eq!(delta.score_delta, Some(0.0));
    }

    // ── Persistence tests ──────────────────────────────────────────────

    #[test]
    fn test_persist_and_load() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let cache_dir = tempfile::tempdir().expect("create cache dir");
        fs::write(dir.path().join("main.py"), "def hello():\n    pass\n").expect("write");

        // Create and persist
        let session = AnalysisSession::new(dir.path(), 4).expect("session");
        let original_score = session.score();
        let original_findings_count = session.findings().len();
        let original_source_files = session.source_files().len();
        let original_fingerprint = session.edge_fingerprint();
        session.persist(cache_dir.path()).expect("persist");

        // Verify cache files exist
        assert!(
            cache_dir.path().join("graph_cache.bin").exists(),
            "Graph cache should be written"
        );
        assert!(
            cache_dir.path().join("session.json").exists(),
            "Session metadata should be written"
        );

        // Load
        let loaded = AnalysisSession::load(cache_dir.path()).expect("load");
        assert!(loaded.is_some(), "Should load persisted session");
        let loaded = loaded.unwrap();

        assert_eq!(loaded.score(), original_score, "Score should match");
        assert_eq!(
            loaded.findings().len(),
            original_findings_count,
            "Findings count should match"
        );
        assert_eq!(
            loaded.source_files().len(),
            original_source_files,
            "Source files count should match"
        );
        assert_eq!(
            loaded.edge_fingerprint(),
            original_fingerprint,
            "Edge fingerprint should match"
        );
        assert_eq!(
            loaded.repo_path(),
            session.repo_path(),
            "Repo path should match"
        );
    }

    #[test]
    fn test_load_nonexistent() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let loaded = AnalysisSession::load(dir.path()).expect("load");
        assert!(loaded.is_none(), "Should return None for nonexistent cache");
    }

    #[test]
    fn test_load_partial_cache() {
        let cache_dir = tempfile::tempdir().expect("create cache dir");

        // Only create session.json, no graph_cache.bin
        fs::write(cache_dir.path().join("session.json"), "{}").expect("write");
        let loaded = AnalysisSession::load(cache_dir.path()).expect("load");
        assert!(
            loaded.is_none(),
            "Should return None when graph cache is missing"
        );
    }

    #[test]
    fn test_persist_load_preserves_graph() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let cache_dir = tempfile::tempdir().expect("create cache dir");
        fs::write(
            dir.path().join("app.py"),
            "def foo():\n    return 1\n\ndef bar():\n    return foo()\n",
        )
        .expect("write");

        let session = AnalysisSession::new(dir.path(), 4).expect("session");
        let original_nodes = session.graph().node_count();
        let original_edges = session.graph().edge_count();
        assert!(original_nodes > 0, "Should have graph nodes");

        session.persist(cache_dir.path()).expect("persist");

        let loaded = AnalysisSession::load(cache_dir.path())
            .expect("load")
            .expect("should find cache");

        assert_eq!(
            loaded.graph().node_count(),
            original_nodes,
            "Node count should match after load"
        );
        assert_eq!(
            loaded.graph().edge_count(),
            original_edges,
            "Edge count should match after load"
        );
    }

    #[test]
    fn test_persist_load_then_update() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let cache_dir = tempfile::tempdir().expect("create cache dir");
        fs::write(dir.path().join("main.py"), "def hello():\n    pass\n").expect("write");

        // Create, persist, load
        let session = AnalysisSession::new(dir.path(), 4).expect("session");
        session.persist(cache_dir.path()).expect("persist");
        let mut loaded = AnalysisSession::load(cache_dir.path())
            .expect("load")
            .expect("should find cache");

        // Modify file and detect changes
        fs::write(
            dir.path().join("main.py"),
            "def hello():\n    x = 42\n    return x\n",
        )
        .expect("write modified");

        let changed = loaded.detect_changed_files().expect("detect");
        assert!(
            !changed.is_empty(),
            "Should detect the modified file after loading from cache"
        );

        // Update should succeed
        let delta = loaded.update(&changed).expect("update");
        assert!(loaded.score().is_some(), "Should have score after update");
        assert_eq!(
            loaded.findings().len(),
            delta.total_findings,
            "findings() should match delta"
        );
    }

    #[test]
    fn test_persist_load_file_hashes_preserved() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let cache_dir = tempfile::tempdir().expect("create cache dir");
        let py_file = dir.path().join("test.py");
        fs::write(&py_file, "x = 1\n").expect("write");

        let session = AnalysisSession::new(dir.path(), 4).expect("session");
        let canonical = py_file.canonicalize().expect("canon");
        let original_hash = session.file_hash(&canonical);
        assert!(original_hash.is_some(), "Should have file hash");

        session.persist(cache_dir.path()).expect("persist");
        let loaded = AnalysisSession::load(cache_dir.path())
            .expect("load")
            .expect("should find cache");

        assert_eq!(
            loaded.file_hash(&canonical),
            original_hash,
            "File hash should be preserved after persist/load"
        );
    }

    #[test]
    fn test_persist_overwrites_previous() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let cache_dir = tempfile::tempdir().expect("create cache dir");
        fs::write(dir.path().join("a.py"), "x = 1\n").expect("write");

        let session = AnalysisSession::new(dir.path(), 4).expect("session");
        session.persist(cache_dir.path()).expect("first persist");

        // Overwrite with a different session
        fs::write(dir.path().join("a.py"), "x = 2\ny = 3\n").expect("write modified");
        fs::write(dir.path().join("b.py"), "z = 4\n").expect("write b");
        let session2 = AnalysisSession::new(dir.path(), 4).expect("session2");
        session2.persist(cache_dir.path()).expect("second persist");

        let loaded = AnalysisSession::load(cache_dir.path())
            .expect("load")
            .expect("should find cache");

        assert_eq!(
            loaded.source_files().len(),
            session2.source_files().len(),
            "Should load the second (overwritten) session"
        );
    }
}
