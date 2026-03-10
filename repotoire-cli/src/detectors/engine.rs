//! Detector execution engine with parallel support
//!
//! The DetectorEngine orchestrates the execution of all registered detectors:
//! - Runs independent detectors in parallel using rayon
//! - Runs dependent detectors sequentially in dependency order
//! - Collects and aggregates findings
//! - Reports progress through callbacks
//!
//! # Architecture
//!
//! ```text
//! ┌─────────────────────────────────────────────────────────┐
//! │                    DetectorEngine                       │
//! ├─────────────────────────────────────────────────────────┤
//! │  1. Register detectors                                  │
//! │  2. Partition into independent/dependent                │
//! │  3. Run independent in parallel (rayon)                 │
//! │  4. Run dependent sequentially                          │
//! │  5. Collect and return findings                         │
//! └─────────────────────────────────────────────────────────┘
//! ```

use crate::detectors::base::{
    is_test_file, DetectionSummary, Detector, DetectorResult, ProgressCallback,
};
use crate::detectors::context_hmm::{ContextClassifier, FunctionContext, FunctionFeatures};
use crate::detectors::function_context::{FunctionContextBuilder, FunctionContextMap};
use crate::graph::CachedGraphQuery;
use crate::graph::GraphStore;
use crate::models::Finding;
use anyhow::Result;
use rayon::prelude::*;
use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Instant;
use tracing::{debug, error, info, warn};

/// Maximum findings to keep to prevent memory exhaustion
const MAX_FINDINGS_LIMIT: usize = 10_000;

/// Pre-computed data for graph-dependent detector startup.
/// Built by `precompute_gd_startup()` and injected via `inject_gd_precomputed()`.
pub struct GdPrecomputed {
    pub contexts: Arc<FunctionContextMap>,
    pub hmm_contexts: Arc<HashMap<String, FunctionContext>>,
    pub taint_results: crate::detectors::taint::centralized::CentralizedTaintResults,
    pub detector_context: Arc<super::DetectorContext>,
}

/// Build all GD pre-compute data (contexts + HMM + taint) as a standalone computation.
///
/// This does NOT require `&mut DetectorEngine` — it only reads the graph and files.
/// Can run in parallel with GI detectors via `thread::scope`.
pub fn precompute_gd_startup(
    graph: &dyn crate::graph::GraphQuery,
    repo_path: &std::path::Path,
    hmm_cache_path: Option<&std::path::PathBuf>,
    source_files: &[std::path::PathBuf],
    value_store: Option<Arc<crate::values::store::ValueStore>>,
) -> GdPrecomputed {
    let i = graph.interner();
    // Four-way parallel: contexts, taint, HMM, and DetectorContext are all independent.
    //   Thread 1: taint (1.5s)           — cross-function + intra-function taint
    //   Thread 2: HMM (0.4s)             — Hidden Markov Model context extraction
    //   Thread 3: DetectorContext (~0.3s) — callers/callees maps, file contents, class hierarchy
    //   Main:     contexts (1.5s)         — adjacency + betweenness + context map
    //   Total:    max(1.5, 1.5, 0.4, 0.3) ≈ 1.5s (zero additional wall-clock cost)
    let (contexts, hmm_contexts, taint_results, detector_context) = std::thread::scope(|s| {
        // Thread 1: Taint analysis
        let taint_handle = s.spawn(|| {
            crate::detectors::taint::centralized::run_centralized_taint(
                graph, repo_path, None,
            )
        });

        // Thread 2: HMM context extraction
        let hmm_handle = s.spawn(|| {
            build_hmm_contexts_standalone(graph, hmm_cache_path)
        });

        // Thread 3: DetectorContext (callers/callees maps, file contents, class hierarchy)
        let vs_clone = value_store.clone();
        let ctx_handle = s.spawn(move || {
            Arc::new(super::DetectorContext::build(graph, source_files, vs_clone))
        });

        // Main thread: Function contexts (adjacency + betweenness + context map)
        let ctx = FunctionContextBuilder::new(graph).build();

        let taint = taint_handle.join().expect("taint thread panicked");
        let hmm = hmm_handle.join().expect("HMM thread panicked");
        let det_ctx = ctx_handle.join().expect("DetectorContext thread panicked");
        (ctx, hmm, taint, det_ctx)
    });

    GdPrecomputed {
        contexts: Arc::new(contexts),
        hmm_contexts: Arc::new(hmm_contexts),
        taint_results,
        detector_context,
    }
}

/// Standalone HMM context building (no &mut self needed).
/// Same logic as `DetectorEngine::build_hmm_contexts` but callable from free function.
fn build_hmm_contexts_standalone(
    graph: &dyn crate::graph::GraphQuery,
    hmm_cache_path: Option<&std::path::PathBuf>,
) -> HashMap<String, FunctionContext> {
    let i = graph.interner();
    // Try to load cached HMM+CRF model
    let mut classifier = if let Some(path) = hmm_cache_path {
        let model_path = path.join("hmm_model.json");
        if model_path.exists() {
            info!("Loading cached HMM+CRF model from {:?}", model_path);
            ContextClassifier::load(&model_path).unwrap_or_else(|| {
                ContextClassifier::for_codebase(Some(&model_path))
            })
        } else {
            ContextClassifier::new()
        }
    } else {
        ContextClassifier::new()
    };

    info!("Building HMM function contexts from graph (standalone)...");
    let mut functions = graph.get_functions();

    if functions.is_empty() {
        return HashMap::new();
    }

    // Build call data from index-based adjacency
    let (adj, rev_adj, qn_to_idx) = graph.get_call_adjacency();
    let file_paths: Vec<String> = functions.iter().map(|f| f.path(i).to_string()).collect();

    // Limit function count to prevent OOM on huge codebases
    const MAX_FUNCTIONS_FOR_HMM: usize = 20_000;
    if functions.len() > MAX_FUNCTIONS_FOR_HMM {
        warn!(
            "Limiting HMM analysis to {} functions (codebase has {})",
            MAX_FUNCTIONS_FOR_HMM, functions.len()
        );
        functions.sort_by(|a, b| {
            let a_fi = qn_to_idx.get(&a.qualified_name).and_then(|&idx| rev_adj.get(idx)).map_or(0, |v| v.len());
            let b_fi = qn_to_idx.get(&b.qualified_name).and_then(|&idx| rev_adj.get(idx)).map_or(0, |v| v.len());
            b_fi.cmp(&a_fi)
        });
        functions.truncate(MAX_FUNCTIONS_FOR_HMM);
    }

    // Compute graph statistics for normalization
    let mut max_fan_in = 1usize;
    let mut max_fan_out = 1usize;
    let mut total_complexity = 0i64;
    let mut complexity_count = 0usize;
    let mut total_loc = 0u32;
    let mut total_params = 0usize;

    for func in &functions {
        let idx = qn_to_idx.get(&func.qualified_name).copied().unwrap_or(0);
        let fan_in = rev_adj.get(idx).map_or(0, |v| v.len());
        let fan_out = adj.get(idx).map_or(0, |v| v.len());
        max_fan_in = max_fan_in.max(fan_in);
        max_fan_out = max_fan_out.max(fan_out);
        if let Some(c) = func.complexity_opt() {
            total_complexity += c;
            complexity_count += 1;
        }
        total_loc += func.line_end.saturating_sub(func.line_start) + 1;
        total_params += 3;
    }

    let avg_complexity = if complexity_count > 0 { total_complexity as f64 / complexity_count as f64 } else { 10.0 };
    let avg_loc = total_loc as f64 / functions.len().max(1) as f64;
    let avg_params = total_params as f64 / functions.len().max(1) as f64;

    // Extract features
    let mut function_data = Vec::new();
    for func in &functions {
        let idx = qn_to_idx.get(&func.qualified_name).copied().unwrap_or(0);
        let fan_in = rev_adj.get(idx).map_or(0, |v| v.len());
        let fan_out = adj.get(idx).map_or(0, |v| v.len());
        let caller_files_count = rev_adj.get(idx).map_or(0, |callers| {
            callers.iter()
                .filter_map(|&ci| file_paths.get(ci).map(|s| s.as_str()))
                .collect::<std::collections::HashSet<&str>>()
                .len()
        });
        let loc = func.line_end.saturating_sub(func.line_start) + 1;
        let address_taken = func.address_taken();
        let features = FunctionFeatures::extract(
            func.node_name(i), func.path(i), fan_in, fan_out, max_fan_in, max_fan_out,
            caller_files_count, func.complexity_opt(), avg_complexity, loc, avg_loc,
            3, avg_params, address_taken,
        );
        function_data.push((features, fan_in, fan_out, address_taken));
    }

    classifier.train(&function_data);

    // Save trained model to cache
    if let Some(path) = hmm_cache_path {
        if let Err(e) = std::fs::create_dir_all(path) {
            warn!("Failed to create HMM cache directory: {}", e);
        } else {
            let model_path = path.join("hmm_model.json");
            if let Err(e) = classifier.save(&model_path) {
                warn!("Failed to save HMM model: {}", e);
            }
        }
    }

    // Classify all functions
    let mut contexts = HashMap::new();
    for (func, (features, _, _, _)) in functions.iter().zip(function_data.iter()) {
        let context = classifier.classify(func.qn(i), features);
        contexts.insert(func.qn(i).to_string(), context);
    }

    info!("Classified {} functions using HMM (standalone)", contexts.len());
    contexts
}

/// Orchestrates code smell detection across all registered detectors
pub struct DetectorEngine {
    /// Registered detectors
    detectors: Vec<Arc<dyn Detector>>,
    /// Number of worker threads for parallel execution
    workers: usize,
    /// Maximum findings to return (prevents memory issues on large codebases)
    max_findings: usize,
    /// Progress callback for reporting execution status
    progress_callback: Option<ProgressCallback>,
    /// Pre-computed function contexts (built from graph on first run)
    function_contexts: Option<Arc<FunctionContextMap>>,
    /// HMM-based context classification for adaptive detection
    hmm_contexts: Option<Arc<HashMap<String, FunctionContext>>>,
    /// Skip findings from test files (default: true)
    /// This filters out findings where all affected_files are test files
    skip_test_files: bool,
    /// Path to cache HMM model for faster subsequent runs
    hmm_cache_path: Option<std::path::PathBuf>,
    /// Whether to print per-detector timing report
    timings_enabled: bool,
    /// Whether GD pre-compute data has been injected via `inject_gd_precomputed`
    gd_precomputed: bool,
}

impl DetectorEngine {
    /// Create a new detector engine
    ///
    /// # Arguments
    /// * `workers` - Number of worker threads (0 = auto-detect)
    pub fn new(workers: usize) -> Self {
        let actual_workers = if workers == 0 {
            std::thread::available_parallelism()
                .map(|p| p.get())
                .unwrap_or(4)
                .min(16) // Cap at 16 threads
        } else {
            workers
        };

        Self {
            detectors: Vec::new(),
            workers: actual_workers,
            max_findings: MAX_FINDINGS_LIMIT,
            progress_callback: None,
            function_contexts: None,
            hmm_contexts: None,
            skip_test_files: true, // Skip test files by default
            hmm_cache_path: None,
            timings_enabled: false,
            gd_precomputed: false,
        }
    }

    /// Set path for HMM model caching
    pub fn with_hmm_cache(mut self, path: std::path::PathBuf) -> Self {
        self.hmm_cache_path = Some(path);
        self
    }

    /// Enable per-detector timing report (printed to stdout)
    pub fn with_timings(mut self, enabled: bool) -> Self {
        self.timings_enabled = enabled;
        self
    }

    /// Create engine with default settings
    #[allow(dead_code)] // Public API
    pub fn default() -> Self {
        Self::new(0)
    }

    /// Set the maximum number of findings to return
    pub fn with_max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Set a progress callback
    pub fn with_progress_callback(mut self, callback: ProgressCallback) -> Self {
        self.progress_callback = Some(callback);
        self
    }

    /// Set pre-computed function contexts
    #[allow(dead_code)] // Public API
    pub fn with_function_contexts(mut self, contexts: Arc<FunctionContextMap>) -> Self {
        self.function_contexts = Some(contexts);
        self
    }

    /// Set whether to skip test files (default: true)
    /// When true, findings from test files are filtered out
    pub fn with_skip_test_files(mut self, skip: bool) -> Self {
        self.skip_test_files = skip;
        self
    }

    /// Get function contexts (builds them from graph if not already set)
    pub fn get_or_build_contexts(
        &mut self,
        graph: &dyn crate::graph::GraphQuery,
    ) -> Arc<FunctionContextMap> {
        let i = graph.interner();
        if let Some(ref ctx) = self.function_contexts {
            return Arc::clone(ctx);
        }

        info!("Building function contexts from graph...");
        let contexts = FunctionContextBuilder::new(graph).build();
        let arc = Arc::new(contexts);
        self.function_contexts = Some(Arc::clone(&arc));
        arc
    }

    /// Get function contexts (returns None if not built)
    #[allow(dead_code)] // Public API
    pub fn function_contexts(&self) -> Option<&Arc<FunctionContextMap>> {
        self.function_contexts.as_ref()
    }

    /// Build HMM-based function contexts from the call graph
    /// This provides adaptive context classification per codebase
    pub fn build_hmm_contexts(
        &mut self,
        graph: &dyn crate::graph::GraphQuery,
    ) -> Arc<HashMap<String, FunctionContext>> {
        let i = graph.interner();
        if let Some(ref ctx) = self.hmm_contexts {
            return Arc::clone(ctx);
        }

        // Try to load cached HMM+CRF model (borrow, don't clone)
        let cache_path = self.hmm_cache_path.as_ref();
        let mut classifier = if let Some(path) = cache_path {
            let model_path = path.join("hmm_model.json");
            if model_path.exists() {
                info!("Loading cached HMM+CRF model from {:?}", model_path);
                ContextClassifier::load(&model_path).unwrap_or_else(|| {
                    // Fallback to legacy format
                    ContextClassifier::for_codebase(Some(&model_path))
                })
            } else {
                ContextClassifier::new()
            }
        } else {
            ContextClassifier::new()
        };

        info!("Building HMM function contexts from graph...");
        let mut functions = graph.get_functions();

        if functions.is_empty() {
            let empty = Arc::new(HashMap::new());
            self.hmm_contexts = Some(Arc::clone(&empty));
            return empty;
        }

        // Build call data from index-based adjacency (avoids 12.5M String pair clone)
        let (adj, rev_adj, qn_to_idx) = graph.get_call_adjacency();
        // Pre-extract file paths indexed by original function position (owned, since
        // functions may be sorted/truncated later but adj/rev_adj use original indices)
        let file_paths: Vec<String> = functions.iter().map(|f| f.path(i).to_string()).collect();

        // Limit function count to prevent OOM on huge codebases
        const MAX_FUNCTIONS_FOR_HMM: usize = 20_000;
        if functions.len() > MAX_FUNCTIONS_FOR_HMM {
            warn!(
                "Limiting HMM analysis to {} functions (codebase has {})",
                MAX_FUNCTIONS_FOR_HMM,
                functions.len()
            );
            // Keep functions with highest fan-in (most important to classify correctly)
            functions.sort_by(|a, b| {
                let a_fi = qn_to_idx
                    .get(&a.qualified_name)
                    .and_then(|&idx| rev_adj.get(idx))
                    .map_or(0, |v| v.len());
                let b_fi = qn_to_idx
                    .get(&b.qualified_name)
                    .and_then(|&idx| rev_adj.get(idx))
                    .map_or(0, |v| v.len());
                b_fi.cmp(&a_fi)
            });
            functions.truncate(MAX_FUNCTIONS_FOR_HMM);
        }

        // Compute graph statistics for normalization using adjacency lists
        let mut max_fan_in = 1usize;
        let mut max_fan_out = 1usize;
        let mut total_complexity = 0i64;
        let mut complexity_count = 0usize;
        let mut total_loc = 0u32;
        let mut total_params = 0usize;

        for func in &functions {
            let idx = qn_to_idx.get(&func.qualified_name).copied().unwrap_or(0);
            let fan_in = rev_adj.get(idx).map_or(0, |v| v.len());
            let fan_out = adj.get(idx).map_or(0, |v| v.len());
            max_fan_in = max_fan_in.max(fan_in);
            max_fan_out = max_fan_out.max(fan_out);

            if let Some(c) = func.complexity_opt() {
                total_complexity += c;
                complexity_count += 1;
            }
            total_loc += func.line_end.saturating_sub(func.line_start) + 1;
            // Assume 3 params average if not available
            total_params += 3;
        }

        let avg_complexity = if complexity_count > 0 {
            total_complexity as f64 / complexity_count as f64
        } else {
            10.0
        };
        let avg_loc = total_loc as f64 / functions.len().max(1) as f64;
        let avg_params = total_params as f64 / functions.len().max(1) as f64;

        // Extract features for training using adjacency-based fan-in/fan-out
        let mut function_data = Vec::new();

        for func in &functions {
            let idx = qn_to_idx.get(&func.qualified_name).copied().unwrap_or(0);
            let fan_in = rev_adj.get(idx).map_or(0, |v| v.len());
            let fan_out = adj.get(idx).map_or(0, |v| v.len());
            // Count unique caller file paths from adjacency indices
            let caller_files_count = rev_adj.get(idx).map_or(0, |callers| {
                callers
                    .iter()
                    .filter_map(|&ci| file_paths.get(ci).map(|s| s.as_str()))
                    .collect::<std::collections::HashSet<&str>>()
                    .len()
            });

            let loc = func.line_end.saturating_sub(func.line_start) + 1;
            let address_taken = func.address_taken();

            let features = FunctionFeatures::extract(
                func.node_name(i),
                func.path(i),
                fan_in,
                fan_out,
                max_fan_in,
                max_fan_out,
                caller_files_count,
                func.complexity_opt(),
                avg_complexity,
                loc,
                avg_loc,
                3, // Default param count
                avg_params,
                address_taken,
            );

            function_data.push((features, fan_in, fan_out, address_taken));
        }

        // Bootstrap training from call graph patterns
        classifier.train(&function_data);

        // Save trained model to cache
        if let Some(path) = cache_path {
            if let Err(e) = std::fs::create_dir_all(path) {
                warn!("Failed to create HMM cache directory: {}", e);
            } else {
                let model_path = path.join("hmm_model.json");
                if let Err(e) = classifier.save(&model_path) {
                    warn!("Failed to save HMM model: {}", e);
                } else {
                    info!("Saved HMM model to {:?}", model_path);
                }
            }
        }

        // Classify all functions
        let mut contexts = HashMap::new();
        for (func, (features, _, _, _)) in functions.iter().zip(function_data.iter()) {
            let context = classifier.classify(func.qn(i), features);
            contexts.insert(func.qn(i).to_string(), context);
        }

        info!("Classified {} functions using HMM", contexts.len());

        // Log distribution
        let mut counts = [0usize; 5];
        for ctx in contexts.values() {
            counts[ctx.index()] += 1;
        }
        info!(
            "Context distribution: Utility={}, Handler={}, Core={}, Internal={}, Test={}",
            counts[0], counts[1], counts[2], counts[3], counts[4]
        );

        let arc = Arc::new(contexts);
        self.hmm_contexts = Some(Arc::clone(&arc));
        arc
    }

    /// Get HMM contexts (returns None if not built)
    #[allow(dead_code)] // Public API
    pub fn hmm_contexts(&self) -> Option<&Arc<HashMap<String, FunctionContext>>> {
        self.hmm_contexts.as_ref()
    }

    /// Get context for a specific function
    #[allow(dead_code)] // Public API
    pub fn get_function_context(&self, qualified_name: &str) -> Option<FunctionContext> {
        self.hmm_contexts
            .as_ref()
            .and_then(|ctx| ctx.get(qualified_name).copied())
    }

    /// Inject pre-computed GD data (contexts, HMM, taint) into the engine.
    ///
    /// After calling this, `run_graph_dependent()` will skip the pre-compute phase
    /// and use the injected data directly.
    pub fn inject_gd_precomputed(&mut self, pre: GdPrecomputed) {
        self.function_contexts = Some(pre.contexts);
        self.hmm_contexts = Some(pre.hmm_contexts);

        // Inject taint results into each security detector
        for detector in &self.detectors {
            if let Some(category) = detector.taint_category() {
                let cross = pre.taint_results
                    .cross_function
                    .get(&category)
                    .cloned()
                    .unwrap_or_default();
                let intra = pre.taint_results
                    .intra_function
                    .get(&category)
                    .cloned()
                    .unwrap_or_default();
                detector.set_precomputed_taint(cross, intra);
            }
        }

        // Inject detector context into all detectors
        for detector in &self.detectors {
            detector.set_detector_context(Arc::clone(&pre.detector_context));
        }

        self.gd_precomputed = true;
    }

    /// Register a detector
    ///
    /// Detectors are partitioned into independent and dependent sets
    /// based on their `is_dependent()` method.
    pub fn register(&mut self, detector: Arc<dyn Detector>) {
        debug!("Registering detector: {}", detector.name());
        self.detectors.push(detector);
    }

    /// Register multiple detectors at once
    pub fn register_all(&mut self, detectors: impl IntoIterator<Item = Arc<dyn Detector>>) {
        for detector in detectors {
            self.register(detector);
        }
    }

    /// Get the number of registered detectors
    pub fn detector_count(&self) -> usize {
        self.detectors.len()
    }

    /// Get names of all registered detectors
    #[allow(dead_code)] // Public API
    pub fn detector_names(&self) -> Vec<&'static str> {
        self.detectors.iter().map(|d| d.name()).collect()
    }

    /// Run all detectors and collect findings
    ///
    /// # Arguments
    /// * `graph` - Graph database client
    ///
    /// # Returns
    /// All findings from all detectors, sorted by severity (highest first)
    pub fn run(&mut self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let i = graph.interner();
        let start = Instant::now();
        info!(
            "Starting detection with {} detectors on {} workers",
            self.detectors.len(),
            self.workers
        );

        // Build function contexts (if not already set)
        let contexts = self.get_or_build_contexts(graph);

        // Build HMM-based contexts for adaptive detection
        let hmm_contexts = self.build_hmm_contexts(graph);

        // Pre-compute centralized taint analysis for all security detectors
        let repo_path = Some(files.repo_path());
        if let Some(repo_path) = repo_path {
            let taint_results = crate::detectors::taint::centralized::run_centralized_taint(
                graph,
                repo_path,
                None,
            );
            for detector in &self.detectors {
                if let Some(category) = detector.taint_category() {
                    let cross = taint_results
                        .cross_function
                        .get(&category)
                        .cloned()
                        .unwrap_or_default();
                    let intra = taint_results
                        .intra_function
                        .get(&category)
                        .cloned()
                        .unwrap_or_default();
                    detector.set_precomputed_taint(cross, intra);
                }
            }
        }

        // Build and inject DetectorContext for run() fallback path
        if !self.gd_precomputed {
            let source_file_paths: Vec<std::path::PathBuf> = files.files().to_vec();
            let det_ctx = Arc::new(super::DetectorContext::build(graph, &source_file_paths, None));
            for detector in &self.detectors {
                detector.set_detector_context(Arc::clone(&det_ctx));
            }
        }

        // Partition detectors into independent and dependent
        let (independent, dependent): (Vec<_>, Vec<_>) = self
            .detectors
            .iter()
            .cloned()
            .partition(|d| !d.is_dependent());

        info!(
            "Detectors: {} independent, {} dependent",
            independent.len(),
            dependent.len()
        );

        // Progress tracking
        let completed = Arc::new(AtomicUsize::new(0));
        let total = self.detectors.len();

        // Shared finding counter for early termination
        let finding_count = Arc::new(AtomicUsize::new(0));

        // Run independent detectors in parallel
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(self.workers)
            .stack_size(8 * 1024 * 1024) // 8MB stack for deeply nested C/C++ parsing
            .build()?;

        let contexts_for_parallel = Arc::clone(&contexts);
        let finding_count_parallel = Arc::clone(&finding_count);
        let independent_results: Vec<DetectorResult> = pool.install(|| {
            independent
                .par_iter()
                .map(|detector| {
                    // Skip if we've already hit the finding limit
                    if finding_count_parallel.load(Ordering::Relaxed) >= MAX_FINDINGS_LIMIT {
                        // Still update progress for skipped detectors
                        let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
                        if let Some(ref callback) = self.progress_callback {
                            callback(detector.name(), done, total);
                        }
                        return DetectorResult::skipped(detector.name());
                    }

                    let result = self.run_single_detector(detector, graph, files, &contexts_for_parallel);
                    finding_count_parallel.fetch_add(result.findings.len(), Ordering::Relaxed);

                    // Update progress
                    let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
                    if let Some(ref callback) = self.progress_callback {
                        callback(detector.name(), done, total);
                    }

                    result
                })
                .collect()
        });

        // Sort independent results by detector name for deterministic finding order
        let mut independent_results = independent_results;
        independent_results.sort_by(|a, b| a.detector_name.cmp(&b.detector_name));

        // Collect findings from independent detectors
        let mut all_findings: Vec<Finding> = Vec::new();
        let mut summary = DetectionSummary::default();
        let mut detector_timings: Vec<(String, u64)> = Vec::new();

        for result in independent_results {
            if self.timings_enabled {
                detector_timings.push((result.detector_name.clone(), result.duration_ms));
            }
            summary.add_result(&result);
            if result.success {
                all_findings.extend(result.findings);
            } else if let Some(err) = &result.error {
                warn!("Detector {} failed: {}", result.detector_name, err);
            }
        }

        // Run dependent detectors sequentially
        // Future: Build dependency graph and run in topological order
        for detector in dependent {
            // Skip if we've already hit the finding limit
            if finding_count.load(Ordering::Relaxed) >= MAX_FINDINGS_LIMIT {
                // Still update progress for skipped detectors
                let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
                if let Some(ref callback) = self.progress_callback {
                    callback(detector.name(), done, total);
                }
                summary.add_result(&DetectorResult::skipped(detector.name()));
                continue;
            }

            let result = self.run_single_detector(&detector, graph, files, &contexts);
            finding_count.fetch_add(result.findings.len(), Ordering::Relaxed);

            // Update progress
            let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
            if let Some(ref callback) = self.progress_callback {
                callback(detector.name(), done, total);
            }

            if self.timings_enabled {
                detector_timings.push((result.detector_name.clone(), result.duration_ms));
            }
            summary.add_result(&result);
            if result.success {
                all_findings.extend(result.findings);
            } else if let Some(err) = &result.error {
                warn!("Detector {} failed: {}", result.detector_name, err);
            }
        }

        // Log early termination if it triggered
        let final_count = finding_count.load(Ordering::Relaxed);
        if final_count >= MAX_FINDINGS_LIMIT {
            info!(
                "Early termination: {} findings reached limit of {}",
                final_count, MAX_FINDINGS_LIMIT
            );
        }

        // Print per-detector timing report (sorted by slowest first)
        if self.timings_enabled && !detector_timings.is_empty() {
            detector_timings.sort_by(|a, b| b.1.cmp(&a.1));
            println!("\nSlowest detectors:");
            for (i, (name, ms)) in detector_timings.iter().take(15).enumerate() {
                println!("  {:>2}. {:<40} {:>6}ms", i + 1, name, ms);
            }
            let total_ms: u64 = detector_timings.iter().map(|(_, ms)| *ms).sum();
            println!("      {:<40} {:>6}ms", "TOTAL (sum, parallel overlap)", total_ms);
        }

        // Filter out test file findings if enabled
        if self.skip_test_files {
            let before_count = all_findings.len();
            all_findings.retain(|finding| !self.is_test_file_finding(finding));
            let filtered = before_count - all_findings.len();
            if filtered > 0 {
                debug!("Filtered out {} findings from test files", filtered);
            }
        }

        // Apply HMM-based context filtering
        let all_functions = graph.get_functions_shared();
        let mut func_by_file: HashMap<&str, Vec<&crate::graph::CodeNode>> = HashMap::new();
        for func in all_functions.iter() {
            func_by_file.entry(func.path(i)).or_default().push(func);
        }
        let before_hmm = all_findings.len();
        all_findings = self.apply_hmm_context_filter(all_findings, &hmm_contexts, &func_by_file);
        let hmm_filtered = before_hmm - all_findings.len();
        if hmm_filtered > 0 {
            info!(
                "HMM context filter removed {} false positives",
                hmm_filtered
            );
        }

        // Sort by severity (highest first)
        all_findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        // Limit findings to prevent memory exhaustion
        if all_findings.len() > self.max_findings {
            warn!(
                "Truncating findings from {} to {} (max limit)",
                all_findings.len(),
                self.max_findings
            );
            all_findings.truncate(self.max_findings);
        }

        let duration = start.elapsed();
        info!(
            "Detection complete: {} findings from {}/{} detectors in {:?}",
            all_findings.len(),
            summary.detectors_succeeded,
            summary.detectors_run,
            duration
        );

        Ok(all_findings)
    }

    /// Run only graph-independent detectors.
    ///
    /// These can execute before graph building completes since they only
    /// analyze file content (AST patterns, security patterns, etc).
    ///
    /// Returns findings from file-local detectors.
    pub fn run_graph_independent(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
    ) -> Result<Vec<Finding>> {
        let cached = CachedGraphQuery::new(graph);
        let graph: &dyn crate::graph::GraphQuery = &cached;

        // Filter detectors to graph-independent only (not requires_graph and not dependent)
        let gi_detectors: Vec<_> = self
            .detectors
            .iter()
            .filter(|d| !d.requires_graph() && !d.is_dependent())
            .cloned()
            .collect();

        if gi_detectors.is_empty() {
            return Ok(vec![]);
        }

        info!("Running {} graph-independent detectors", gi_detectors.len());

        // Graph-independent detectors don't use function contexts, so pass an empty map.
        // Actual contexts will be built lazily when run_graph_dependent() is called.
        let contexts: Arc<FunctionContextMap> = Arc::new(HashMap::new());
        let finding_count = Arc::new(AtomicUsize::new(0));
        let completed = Arc::new(AtomicUsize::new(0));
        let total = gi_detectors.len();

        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(self.workers)
            .stack_size(8 * 1024 * 1024) // 8MB stack for deeply nested C/C++ parsing
            .build()?;

        let contexts_clone = Arc::clone(&contexts);
        let finding_count_clone = Arc::clone(&finding_count);
        let results: Vec<DetectorResult> = pool.install(|| {
            gi_detectors
                .par_iter()
                .map(|detector| {
                    if finding_count_clone.load(Ordering::Relaxed) >= MAX_FINDINGS_LIMIT {
                        let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
                        if let Some(ref callback) = self.progress_callback {
                            callback(detector.name(), done, total);
                        }
                        return DetectorResult::skipped(detector.name());
                    }

                    let result =
                        self.run_single_detector(detector, graph, files, &contexts_clone);
                    finding_count_clone.fetch_add(result.findings.len(), Ordering::Relaxed);

                    let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
                    if let Some(ref callback) = self.progress_callback {
                        callback(detector.name(), done, total);
                    }

                    result
                })
                .collect()
        });

        // Sort by detector name for deterministic finding order
        let mut results = results;
        results.sort_by(|a, b| a.detector_name.cmp(&b.detector_name));

        let mut findings = Vec::new();
        let mut detector_timings: Vec<(String, u64)> = Vec::new();

        for result in results {
            if self.timings_enabled {
                detector_timings.push((result.detector_name.clone(), result.duration_ms));
            }
            if result.success {
                findings.extend(result.findings);
            } else if let Some(err) = &result.error {
                warn!("Detector {} failed: {}", result.detector_name, err);
            }
        }

        // Filter out test file findings if enabled
        if self.skip_test_files {
            let before_count = findings.len();
            findings.retain(|finding| !self.is_test_file_finding(finding));
            let filtered = before_count - findings.len();
            if filtered > 0 {
                debug!("Filtered out {} findings from test files", filtered);
            }
        }

        if self.timings_enabled && !detector_timings.is_empty() {
            detector_timings.sort_by(|a, b| b.1.cmp(&a.1));
            println!("\nSlowest graph-independent detectors:");
            for (i, (name, ms)) in detector_timings.iter().take(10).enumerate() {
                println!("  {:>2}. {:<40} {:>6}ms", i + 1, name, ms);
            }
        }

        findings.sort_by(|a, b| b.severity.cmp(&a.severity));
        Ok(findings)
    }

    /// Run only graph-dependent detectors.
    ///
    /// Call after graph building and git enrichment complete.
    /// Runs graph-dependent detectors in parallel, then dependent detectors sequentially.
    ///
    /// Returns findings from graph-based detectors.
    pub fn run_graph_dependent(
        &mut self,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
    ) -> Result<Vec<Finding>> {
        let i = graph.interner();
        let cached = CachedGraphQuery::new(graph);
        let graph: &dyn crate::graph::GraphQuery = &cached;

        // Filter: detectors that require graph OR are dependent on other detectors
        let gd_detectors: Vec<_> = self
            .detectors
            .iter()
            .filter(|d| d.requires_graph() || d.is_dependent())
            .cloned()
            .collect();

        if gd_detectors.is_empty() {
            return Ok(vec![]);
        }

        info!("Running {} graph-dependent detectors", gd_detectors.len());

        // If GD data was pre-computed (overlapped with GI), skip the startup phase.
        // Otherwise, compute contexts + HMM + taint now (fallback for non-speculative paths).
        let (contexts, hmm_contexts) = if self.gd_precomputed {
            let ctx = self.function_contexts.clone().unwrap_or_else(|| Arc::new(HashMap::new()));
            let hmm = self.hmm_contexts.clone().unwrap_or_else(|| Arc::new(HashMap::new()));
            (ctx, hmm)
        } else {
            // Original pre-compute path (taint || context+HMM)
            let repo_path = files.repo_path().to_path_buf();

            let (ctx, hmm, taint_results) = std::thread::scope(|s| {
                let taint_handle = s.spawn(|| {
                    crate::detectors::taint::centralized::run_centralized_taint(
                        graph, &repo_path, None,
                    )
                });

                let ctx = self.get_or_build_contexts(graph);
                let hmm = self.build_hmm_contexts(graph);

                let taint = taint_handle.join().expect("taint thread panicked");
                (ctx, hmm, taint)
            });

            // Inject taint results into each security detector
            for detector in &gd_detectors {
                if let Some(category) = detector.taint_category() {
                    let cross = taint_results.cross_function.get(&category).cloned().unwrap_or_default();
                    let intra = taint_results.intra_function.get(&category).cloned().unwrap_or_default();
                    detector.set_precomputed_taint(cross, intra);
                }
            }

            // Build and inject DetectorContext for fallback path
            let source_file_paths: Vec<std::path::PathBuf> = files.files().to_vec();
            let det_ctx = Arc::new(super::DetectorContext::build(graph, &source_file_paths, None));
            for detector in &gd_detectors {
                detector.set_detector_context(Arc::clone(&det_ctx));
            }

            (ctx, hmm)
        };

        let finding_count = Arc::new(AtomicUsize::new(0));
        let completed = Arc::new(AtomicUsize::new(0));

        // Split into parallel (independent but graph-requiring) and sequential (dependent)
        let (parallel, sequential): (Vec<_>, Vec<_>) =
            gd_detectors.into_iter().partition(|d| !d.is_dependent());

        let total = parallel.len() + sequential.len();

        // Run parallel graph-dependent detectors
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(self.workers)
            .stack_size(8 * 1024 * 1024) // 8MB stack for deeply nested C/C++ parsing
            .build()?;

        let contexts_clone = Arc::clone(&contexts);
        let finding_count_clone = Arc::clone(&finding_count);
        let parallel_results: Vec<DetectorResult> = pool.install(|| {
            parallel
                .par_iter()
                .map(|detector| {
                    if finding_count_clone.load(Ordering::Relaxed) >= MAX_FINDINGS_LIMIT {
                        let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
                        if let Some(ref callback) = self.progress_callback {
                            callback(detector.name(), done, total);
                        }
                        return DetectorResult::skipped(detector.name());
                    }

                    let result =
                        self.run_single_detector(detector, graph, files, &contexts_clone);
                    finding_count_clone.fetch_add(result.findings.len(), Ordering::Relaxed);

                    let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
                    if let Some(ref callback) = self.progress_callback {
                        callback(detector.name(), done, total);
                    }

                    result
                })
                .collect()
        });

        // Sort by detector name for deterministic finding order
        let mut parallel_results = parallel_results;
        parallel_results.sort_by(|a, b| a.detector_name.cmp(&b.detector_name));

        let mut findings = Vec::new();
        let mut detector_timings: Vec<(String, u64)> = Vec::new();

        for result in parallel_results {
            if self.timings_enabled {
                detector_timings.push((result.detector_name.clone(), result.duration_ms));
            }
            if result.success {
                findings.extend(result.findings);
            } else if let Some(err) = &result.error {
                warn!("Detector {} failed: {}", result.detector_name, err);
            }
        }

        // Run sequential dependent detectors
        for detector in sequential {
            if finding_count.load(Ordering::Relaxed) >= MAX_FINDINGS_LIMIT {
                let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
                if let Some(ref callback) = self.progress_callback {
                    callback(detector.name(), done, total);
                }
                continue;
            }

            let result = self.run_single_detector(&detector, graph, files, &contexts);
            finding_count.fetch_add(result.findings.len(), Ordering::Relaxed);

            let done = completed.fetch_add(1, Ordering::SeqCst) + 1;
            if let Some(ref callback) = self.progress_callback {
                callback(detector.name(), done, total);
            }

            if self.timings_enabled {
                detector_timings.push((result.detector_name.clone(), result.duration_ms));
            }
            if result.success {
                findings.extend(result.findings);
            } else if let Some(err) = &result.error {
                warn!("Detector {} failed: {}", result.detector_name, err);
            }
        }

        // Filter out test file findings if enabled
        if self.skip_test_files {
            let before_count = findings.len();
            findings.retain(|finding| !self.is_test_file_finding(finding));
            let filtered = before_count - findings.len();
            if filtered > 0 {
                debug!("Filtered out {} findings from test files", filtered);
            }
        }

        // Apply HMM-based context filtering (only for graph-dependent since HMM needs graph)
        // Use get_functions_ref() to avoid cloning 71K CodeNodes from the cache.
        let cached_funcs = cached.get_functions_ref();
        let mut func_by_file: HashMap<&str, Vec<&crate::graph::CodeNode>> = HashMap::new();
        for func in cached_funcs {
            func_by_file.entry(func.path(i)).or_default().push(func);
        }
        let before_hmm = findings.len();
        findings = self.apply_hmm_context_filter(findings, &hmm_contexts, &func_by_file);
        let hmm_filtered = before_hmm - findings.len();
        if hmm_filtered > 0 {
            info!("HMM context filter removed {} false positives", hmm_filtered);
        }

        if self.timings_enabled && !detector_timings.is_empty() {
            detector_timings.sort_by(|a, b| b.1.cmp(&a.1));
            println!("\nSlowest graph-dependent detectors:");
            for (i, (name, ms)) in detector_timings.iter().take(15).enumerate() {
                println!("  {:>2}. {:<40} {:>6}ms", i + 1, name, ms);
            }
        }

        findings.sort_by(|a, b| b.severity.cmp(&a.severity));
        Ok(findings)
    }

    /// Run all detectors and return detailed results
    ///
    /// Unlike `run()`, this returns individual results for each detector,
    /// useful for debugging and detailed reporting.
    #[allow(dead_code)] // Public API for detailed reporting
    pub fn run_detailed(
        &mut self,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
    ) -> Result<(Vec<DetectorResult>, DetectionSummary)> {
        let start = Instant::now();

        // Build function contexts
        let contexts = self.get_or_build_contexts(graph);

        // Pre-compute centralized taint analysis
        let repo_path = Some(files.repo_path());
        if let Some(repo_path) = repo_path {
            let taint_results = crate::detectors::taint::centralized::run_centralized_taint(
                graph,
                repo_path,
                None,
            );
            for detector in &self.detectors {
                if let Some(category) = detector.taint_category() {
                    let cross = taint_results
                        .cross_function
                        .get(&category)
                        .cloned()
                        .unwrap_or_default();
                    let intra = taint_results
                        .intra_function
                        .get(&category)
                        .cloned()
                        .unwrap_or_default();
                    detector.set_precomputed_taint(cross, intra);
                }
            }
        }

        // Build and inject DetectorContext for run_detailed() fallback path
        if !self.gd_precomputed {
            let source_file_paths: Vec<std::path::PathBuf> = files.files().to_vec();
            let det_ctx = Arc::new(super::DetectorContext::build(graph, &source_file_paths, None));
            for detector in &self.detectors {
                detector.set_detector_context(Arc::clone(&det_ctx));
            }
        }

        // Partition detectors
        let (independent, dependent): (Vec<_>, Vec<_>) = self
            .detectors
            .iter()
            .cloned()
            .partition(|d| !d.is_dependent());

        // Run independent in parallel
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(self.workers)
            .stack_size(8 * 1024 * 1024) // 8MB stack for deeply nested C/C++ parsing
            .build()?;

        let contexts_for_parallel = Arc::clone(&contexts);
        let mut all_results: Vec<DetectorResult> = pool.install(|| {
            independent
                .par_iter()
                .map(|detector| self.run_single_detector(detector, graph, files, &contexts_for_parallel))
                .collect()
        });

        // Run dependent sequentially
        for detector in dependent {
            all_results.push(self.run_single_detector(&detector, graph, files, &contexts));
        }

        // Filter out test file findings if enabled
        if self.skip_test_files {
            for result in &mut all_results {
                let before_count = result.findings.len();
                result
                    .findings
                    .retain(|finding| !self.is_test_file_finding(finding));
                let filtered = before_count - result.findings.len();
                if filtered > 0 {
                    debug!(
                        "Filtered {} test file findings from {}",
                        filtered, result.detector_name
                    );
                }
            }
        }

        // Build summary
        let mut summary = DetectionSummary::default();
        for result in &all_results {
            summary.add_result(result);
        }
        summary.total_duration_ms = start.elapsed().as_millis() as u64;

        Ok((all_results, summary))
    }

    /// Apply HMM-based context filtering to reduce false positives
    ///
    /// - Skip coupling findings for UTILITY/HANDLER functions
    /// - Skip dead code findings for HANDLER functions
    /// - Downgrade severity for functions with lenient contexts
    fn apply_hmm_context_filter(
        &self,
        findings: Vec<Finding>,
        hmm_contexts: &HashMap<String, FunctionContext>,
        func_by_file: &HashMap<&str, Vec<&crate::graph::CodeNode>>,
    ) -> Vec<Finding> {
        use rustc_hash::FxHashSet;

        // Build O(1) lookup sets for relevant detector names
        static COUPLING_DETECTORS: &[&str] = &[
            "DegreeCentralityDetector",
            "ShotgunSurgeryDetector",
            "FeatureEnvyDetector",
            "InappropriateIntimacyDetector",
        ];
        static DEAD_CODE_DETECTORS: &[&str] = &["UnreachableCodeDetector", "DeadCodeDetector"];

        let coupling_set: FxHashSet<&str> = COUPLING_DETECTORS.iter().copied().collect();
        let dead_code_set: FxHashSet<&str> = DEAD_CODE_DETECTORS.iter().copied().collect();

        // Pre-sort functions by line_start for binary search (done once, shared by all findings)
        let sorted_by_file: HashMap<&str, Vec<&crate::graph::CodeNode>> = func_by_file
            .iter()
            .map(|(&file, funcs)| {
                let mut sorted = funcs.clone();
                sorted.sort_unstable_by_key(|f| f.line_start);
                (file, sorted)
            })
            .collect();

        // Parallel filter — each finding is independent
        findings
            .into_par_iter()
            .filter(|finding| {
                let is_coupling = coupling_set.contains(finding.detector.as_str());
                let is_dead_code = dead_code_set.contains(finding.detector.as_str());

                // Skip non-relevant detectors immediately (no function lookup needed)
                if !is_coupling && !is_dead_code {
                    return true;
                }

                // Look up function by file + line (binary search)
                let func_qn = Self::find_function_at_line(finding, &sorted_by_file);

                if let Some(qn) = func_qn {
                    if let Some(context) = hmm_contexts.get(qn) {
                        if is_coupling && context.skip_coupling() {
                            return false;
                        }
                        if is_dead_code && context.skip_dead_code() {
                            return false;
                        }
                    }
                }

                true
            })
            .collect()
    }

    /// Find function qualified name at a finding's file+line using binary search.
    /// Returns a reference to avoid String allocation.
    fn find_function_at_line<'b>(
        finding: &Finding,
        sorted_by_file: &HashMap<&str, Vec<&'b crate::graph::CodeNode>>,
    ) -> Option<&'b str> {
        let (file, line) = match (finding.affected_files.first(), finding.line_start) {
            (Some(f), Some(l)) => (f, l),
            _ => return None,
        };

        let file_str = file.to_string_lossy();
        let funcs = sorted_by_file.get(file_str.as_ref())?;

        // Binary search: find the last function whose line_start <= line
        let idx = funcs.partition_point(|f| f.line_start <= line);
        if idx > 0 {
            let func = funcs[idx - 1];
            if func.line_end >= line {
                return Some(func.qn(crate::graph::interner::global_interner()));
            }
        }

        None
    }

    /// Check if a finding is from test files only
    /// Returns true if ALL affected files are test files
    fn is_test_file_finding(&self, finding: &Finding) -> bool {
        // If no affected files, can't determine - don't filter
        if finding.affected_files.is_empty() {
            return false;
        }
        // Filter only if ALL affected files are test files
        finding.affected_files.iter().all(|path| is_test_file(path))
    }

    /// Run a single detector with error handling and timing
    fn run_single_detector(
        &self,
        detector: &Arc<dyn Detector>,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
        contexts: &Arc<FunctionContextMap>,
    ) -> DetectorResult {
        let name = detector.name().to_string();
        let start = Instant::now();

        debug!("Running detector: {}", name);

        // Wrap in catch_unwind to handle panics
        let contexts_clone = Arc::clone(contexts);
        let detect_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            if detector.uses_context() {
                detector.detect_with_context(graph, files, &contexts_clone)
            } else {
                detector.detect(graph, files)
            }
        }));

        match detect_result {
            Ok(Ok(mut findings)) => {
                let duration = start.elapsed().as_millis() as u64;

                // Apply per-detector finding limit if configured
                if let Some(config) = detector.config() {
                    if let Some(max) = config.max_findings {
                        if findings.len() > max {
                            findings.truncate(max);
                        }
                    }
                }

                debug!(
                    "Detector {} found {} findings in {}ms",
                    name,
                    findings.len(),
                    duration
                );

                DetectorResult::success(name, findings, duration)
            }
            Ok(Err(e)) => {
                let duration = start.elapsed().as_millis() as u64;
                // Downgrade to debug - many detectors fail due to missing graph properties
                debug!("Detector {} skipped (query error): {}", name, e);
                DetectorResult::failure(name, e.to_string(), duration)
            }
            Err(panic_info) => {
                let duration = start.elapsed().as_millis() as u64;
                let panic_msg = if let Some(s) = panic_info.downcast_ref::<&str>() {
                    s.to_string()
                } else if let Some(s) = panic_info.downcast_ref::<String>() {
                    s.clone()
                } else {
                    "Unknown panic".to_string()
                };
                error!("Detector {} panicked: {}", name, panic_msg);
                DetectorResult::failure(name, format!("Panic: {}", panic_msg), duration)
            }
        }
    }
}

impl Default for DetectorEngine {
    fn default() -> Self {
        Self::new(0)
    }
}

/// Builder for DetectorEngine with fluent API
pub struct DetectorEngineBuilder {
    workers: usize,
    max_findings: usize,
    detectors: Vec<Arc<dyn Detector>>,
    progress_callback: Option<ProgressCallback>,
    skip_test_files: bool,
}

impl DetectorEngineBuilder {
    /// Create a new builder
    pub fn new() -> Self {
        Self {
            workers: 0,
            max_findings: MAX_FINDINGS_LIMIT,
            detectors: Vec::new(),
            progress_callback: None,
            skip_test_files: true,
        }
    }

    /// Set number of worker threads
    pub fn workers(mut self, workers: usize) -> Self {
        self.workers = workers;
        self
    }

    /// Set maximum findings
    #[allow(dead_code)] // Builder method
    pub fn max_findings(mut self, max: usize) -> Self {
        self.max_findings = max;
        self
    }

    /// Add a detector
    #[allow(dead_code)] // Builder method
    pub fn detector(mut self, detector: Arc<dyn Detector>) -> Self {
        self.detectors.push(detector);
        self
    }

    /// Add multiple detectors
    pub fn detectors(mut self, detectors: impl IntoIterator<Item = Arc<dyn Detector>>) -> Self {
        self.detectors.extend(detectors);
        self
    }

    /// Set progress callback
    #[allow(dead_code)] // Builder method
    pub fn on_progress(mut self, callback: ProgressCallback) -> Self {
        self.progress_callback = Some(callback);
        self
    }

    /// Set whether to skip test files (default: true)
    #[allow(dead_code)] // Builder method
    pub fn skip_test_files(mut self, skip: bool) -> Self {
        self.skip_test_files = skip;
        self
    }

    /// Build the engine
    pub fn build(self) -> DetectorEngine {
        let mut engine = DetectorEngine::new(self.workers)
            .with_max_findings(self.max_findings)
            .with_skip_test_files(self.skip_test_files);

        if let Some(callback) = self.progress_callback {
            engine = engine.with_progress_callback(callback);
        }

        engine.register_all(self.detectors);
        engine
    }
}

impl Default for DetectorEngineBuilder {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Severity;
    use std::path::PathBuf;

    // Mock detector for testing
    struct MockDetector {
        name: &'static str,
        findings_count: usize,
        dependent: bool,
        graph_required: bool,
    }

    impl Detector for MockDetector {
        fn name(&self) -> &'static str {
            self.name
        }

        fn description(&self) -> &'static str {
            "Mock detector for testing"
        }

        fn detect(&self, _graph: &dyn crate::graph::GraphQuery, _files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
            Ok((0..self.findings_count)
                .map(|i| Finding {
                    id: format!("{}-{}", self.name, i),
                    detector: self.name.to_string(),
                    severity: Severity::Medium,
                    title: format!("Finding {}", i),
                    description: "Test finding".to_string(),
                    affected_files: vec![PathBuf::from("test.py")],
                    line_start: Some(1),
                    line_end: Some(10),
                    suggested_fix: None,
                    estimated_effort: None,
                    category: None,
                    cwe_id: None,
                    why_it_matters: None,
                    ..Default::default()
                })
                .collect())
        }

        fn is_dependent(&self) -> bool {
            self.dependent
        }

        fn requires_graph(&self) -> bool {
            self.graph_required
        }
    }

    #[test]
    fn test_engine_creation() {
        let engine = DetectorEngine::new(4);
        assert_eq!(engine.workers, 4);
        assert_eq!(engine.detector_count(), 0);
    }

    #[test]
    fn test_engine_default_workers() {
        let engine = DetectorEngine::new(0);
        assert!(engine.workers > 0);
        assert!(engine.workers <= 16);
    }

    #[test]
    fn test_register_detectors() {
        let mut engine = DetectorEngine::new(2);

        engine.register(Arc::new(MockDetector {
            name: "Detector1",
            findings_count: 5,
            dependent: false,
            graph_required: false,
        }));

        engine.register(Arc::new(MockDetector {
            name: "Detector2",
            findings_count: 3,
            dependent: true,
            graph_required: true,
        }));

        assert_eq!(engine.detector_count(), 2);
        assert_eq!(engine.detector_names(), vec!["Detector1", "Detector2"]);
    }

    #[test]
    fn test_builder() {
        let engine = DetectorEngineBuilder::new()
            .workers(4)
            .max_findings(100)
            .detector(Arc::new(MockDetector {
                name: "Test",
                findings_count: 1,
                dependent: false,
                graph_required: false,
            }))
            .build();

        assert_eq!(engine.workers, 4);
        assert_eq!(engine.max_findings, 100);
        assert_eq!(engine.detector_count(), 1);
    }

    #[test]
    fn test_split_run_partitions_correctly() {
        use crate::detectors::file_provider::MockFileProvider;

        let store = GraphStore::in_memory();
        let file_provider = MockFileProvider::new(vec![("src/main.py", "x = 1")]);

        // Create detectors: 2 graph-independent, 2 graph-dependent, 1 dependent
        let gi_1 = Arc::new(MockDetector {
            name: "GI_Detector1",
            findings_count: 3,
            dependent: false,
            graph_required: false,
        });
        let gi_2 = Arc::new(MockDetector {
            name: "GI_Detector2",
            findings_count: 2,
            dependent: false,
            graph_required: false,
        });
        let gd_1 = Arc::new(MockDetector {
            name: "GD_Detector1",
            findings_count: 4,
            dependent: false,
            graph_required: true,
        });
        let gd_2 = Arc::new(MockDetector {
            name: "GD_Detector2",
            findings_count: 1,
            dependent: false,
            graph_required: true,
        });
        let dep = Arc::new(MockDetector {
            name: "Dep_Detector",
            findings_count: 2,
            dependent: true,
            graph_required: true,
        });

        // Run graph-independent split
        let mut engine_gi = DetectorEngine::new(2).with_skip_test_files(false);
        engine_gi.register(gi_1.clone());
        engine_gi.register(gi_2.clone());
        engine_gi.register(gd_1.clone());
        engine_gi.register(gd_2.clone());
        engine_gi.register(dep.clone());
        let independent = engine_gi
            .run_graph_independent(&store, &file_provider)
            .unwrap();

        // Run graph-dependent split
        let mut engine_gd = DetectorEngine::new(2).with_skip_test_files(false);
        engine_gd.register(gi_1.clone());
        engine_gd.register(gi_2.clone());
        engine_gd.register(gd_1.clone());
        engine_gd.register(gd_2.clone());
        engine_gd.register(dep.clone());
        let dependent = engine_gd
            .run_graph_dependent(&store, &file_provider)
            .unwrap();

        // Graph-independent should only have findings from GI detectors: 3 + 2 = 5
        assert_eq!(
            independent.len(),
            5,
            "Expected 5 graph-independent findings, got {}",
            independent.len()
        );

        // Graph-dependent should have findings from GD + dependent: 4 + 1 + 2 = 7
        assert_eq!(
            dependent.len(),
            7,
            "Expected 7 graph-dependent findings, got {}",
            dependent.len()
        );

        // Total should equal full run
        let split_total = independent.len() + dependent.len();
        let mut engine_full = DetectorEngine::new(2).with_skip_test_files(false);
        engine_full.register(gi_1);
        engine_full.register(gi_2);
        engine_full.register(gd_1);
        engine_full.register(gd_2);
        engine_full.register(dep);
        let full = engine_full.run(&store, &file_provider).unwrap();

        assert_eq!(
            split_total,
            full.len(),
            "Split total ({}) should equal full run ({})",
            split_total,
            full.len()
        );
    }

    #[test]
    fn test_split_detection_completeness() {
        // Verify every detector is either graph-independent or graph-dependent
        let all_detectors = crate::detectors::default_detectors(std::path::Path::new("/tmp"));
        let gi_count = all_detectors
            .iter()
            .filter(|d| !d.requires_graph())
            .count();
        let gd_count = all_detectors
            .iter()
            .filter(|d| d.requires_graph())
            .count();

        assert_eq!(
            gi_count + gd_count,
            all_detectors.len(),
            "Every detector must be either graph-independent or graph-dependent"
        );

        // Verify partitioning is exhaustive: no detector is missed by the split
        let gi_names: Vec<_> = all_detectors
            .iter()
            .filter(|d| !d.requires_graph() && !d.is_dependent())
            .map(|d| d.name())
            .collect();
        let gd_names: Vec<_> = all_detectors
            .iter()
            .filter(|d| d.requires_graph() || d.is_dependent())
            .map(|d| d.name())
            .collect();

        let total_covered = gi_names.len() + gd_names.len();
        assert_eq!(
            total_covered,
            all_detectors.len(),
            "Split must cover all {} detectors, but only covers {} ({} gi + {} gd)",
            all_detectors.len(),
            total_covered,
            gi_names.len(),
            gd_names.len()
        );

        println!(
            "Detector split: {} graph-independent, {} graph-dependent ({} total)",
            gi_names.len(),
            gd_names.len(),
            all_detectors.len()
        );
    }
}
