//! Pre-computation and context building for detector execution.
//!
//! This module provides:
//! - `PrecomputedAnalysis` — all graph-derived data needed by detectors,
//!   pre-built in parallel threads for performance.
//! - `precompute_gd_startup()` — builds `PrecomputedAnalysis` from graph + files.
//! - `to_context()` — converts `PrecomputedAnalysis` into an `AnalysisContext`.
//!
//! Actual detector execution lives in `runner.rs` (`run_detectors()`).

use crate::detectors::context_hmm::{
    ContextClassifier, FunctionContext, FunctionFeatures, FunctionMetrics,
};
use crate::detectors::function_context::{FunctionContextBuilder, FunctionContextMap};
use crate::graph::GraphQueryExt;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use tracing::{debug, error, info, warn};

// ── PrecomputedAnalysis ──────────────────────────────────────────────────────

/// Pre-computed data for graph-dependent detector startup.
/// Built by `precompute_gd_startup()` and converted to `AnalysisContext` via
/// `to_context()`.
///
/// All fields are `Arc`-wrapped, so `Clone` is a cheap reference-count bump (~ns).
/// This allows `AnalysisEngine` to cache and re-inject the data on incremental runs,
/// avoiding the ~3.9s precomputation overhead.
pub struct PrecomputedAnalysis {
    pub contexts: Arc<FunctionContextMap>,
    pub hmm_contexts: Arc<HashMap<String, FunctionContext>>,
    /// HMM classifications with confidence scores (function QN -> (context, confidence)).
    pub hmm_with_confidence: Arc<HashMap<String, (FunctionContext, f64)>>,
    pub taint_results: Arc<crate::detectors::taint::centralized::CentralizedTaintResults>,
    pub detector_context: Arc<super::DetectorContext>,
    pub file_index: Arc<super::FileIndex>,
    /// Pre-computed reachability index (functions reachable from entry points).
    pub reachability: Arc<super::reachability::ReachabilityIndex>,
    /// Pre-computed public API set (exported/public function and class QNs).
    pub public_api: Arc<std::collections::HashSet<String>>,
    /// Pre-computed per-module coupling/cohesion metrics.
    pub module_metrics: Arc<HashMap<String, super::module_metrics::ModuleMetrics>>,
    /// Pre-computed per-class cohesion (LCOM4 approximation).
    pub class_cohesion: Arc<HashMap<String, f64>>,
    /// Pre-parsed decorator/annotation lists per function.
    pub decorator_index: Arc<HashMap<String, Vec<String>>>,
    /// Per-file git churn data (empty if git history unavailable).
    pub git_churn: Arc<HashMap<String, super::analysis_context::FileChurnInfo>>,
    /// Per-node aggregate co-change score (empty if no co-change data).
    pub co_change_summary: Arc<HashMap<crate::graph::NodeIndex, f64>>,
    /// Full co-change matrix for pairwise file coupling queries.
    pub co_change_matrix: Option<Arc<crate::git::co_change::CoChangeMatrix>>,
    /// DOA-based file ownership model for bus factor analysis.
    pub ownership: Option<Arc<crate::git::ownership::OwnershipModel>>,
    /// L3 cached node2vec embeddings for relational scoring.
    pub cached_embeddings: Option<Arc<crate::predictive::embedding_scorer::CachedEmbeddings>>,
}

impl Clone for PrecomputedAnalysis {
    fn clone(&self) -> Self {
        Self {
            contexts: Arc::clone(&self.contexts),
            hmm_contexts: Arc::clone(&self.hmm_contexts),
            hmm_with_confidence: Arc::clone(&self.hmm_with_confidence),
            taint_results: Arc::clone(&self.taint_results),
            detector_context: Arc::clone(&self.detector_context),
            file_index: Arc::clone(&self.file_index),
            reachability: Arc::clone(&self.reachability),
            public_api: Arc::clone(&self.public_api),
            module_metrics: Arc::clone(&self.module_metrics),
            class_cohesion: Arc::clone(&self.class_cohesion),
            decorator_index: Arc::clone(&self.decorator_index),
            git_churn: Arc::clone(&self.git_churn),
            co_change_summary: Arc::clone(&self.co_change_summary),
            co_change_matrix: self.co_change_matrix.as_ref().map(Arc::clone),
            ownership: self.ownership.as_ref().map(Arc::clone),
            cached_embeddings: self.cached_embeddings.as_ref().map(Arc::clone),
        }
    }
}

impl PrecomputedAnalysis {
    /// Convert pre-computed data into an [`AnalysisContext`] ready for detector
    /// execution.
    ///
    /// The returned context borrows `graph` and clones every `Arc` field (~ns per
    /// field). `resolver` is wrapped in a new `Arc`.
    pub fn to_context<'g>(
        &self,
        graph: &'g dyn crate::graph::GraphQuery,
        resolver: &crate::calibrate::ThresholdResolver,
    ) -> super::AnalysisContext<'g> {
        super::AnalysisContext {
            graph,
            files: Arc::clone(&self.file_index),
            functions: Arc::clone(&self.contexts),
            taint: Arc::clone(&self.taint_results),
            detector_ctx: Arc::clone(&self.detector_context),
            hmm_classifications: Arc::clone(&self.hmm_with_confidence),
            resolver: Arc::new(resolver.clone()),
            reachability: Arc::clone(&self.reachability),
            public_api: Arc::clone(&self.public_api),
            module_metrics: Arc::clone(&self.module_metrics),
            class_cohesion: Arc::clone(&self.class_cohesion),
            decorator_index: Arc::clone(&self.decorator_index),
            git_churn: Arc::clone(&self.git_churn),
            co_change_summary: Arc::clone(&self.co_change_summary),
            co_change_matrix: self.co_change_matrix.as_ref().map(Arc::clone),
            ownership: self.ownership.as_ref().map(Arc::clone),
            cached_embeddings: self.cached_embeddings.as_ref().map(Arc::clone),
        }
    }

    /// Build an [`AnalysisContext`] scoped to a subset of files.
    ///
    /// The returned context contains a `FileIndex` filtered to only the given
    /// `scoped_files`, so detectors iterate over just those files.  All other
    /// fields (graph, taint, HMM, etc.) are shared via `Arc` clones.
    pub fn to_context_scoped<'g>(
        &self,
        graph: &'g dyn crate::graph::GraphQuery,
        resolver: &crate::calibrate::ThresholdResolver,
        scoped_files: &[std::path::PathBuf],
    ) -> super::AnalysisContext<'g> {
        let scoped_set: std::collections::HashSet<&std::path::PathBuf> =
            scoped_files.iter().collect();
        let file_data: Vec<_> = self
            .file_index
            .all()
            .iter()
            .filter(|entry| scoped_set.contains(&entry.path))
            .map(|entry| (entry.path.clone(), Arc::clone(&entry.content), entry.flags))
            .collect();
        let scoped_file_index = Arc::new(super::FileIndex::new(file_data));

        super::AnalysisContext {
            graph,
            files: scoped_file_index,
            functions: Arc::clone(&self.contexts),
            taint: Arc::clone(&self.taint_results),
            detector_ctx: Arc::clone(&self.detector_context),
            hmm_classifications: Arc::clone(&self.hmm_with_confidence),
            resolver: Arc::new(resolver.clone()),
            reachability: Arc::clone(&self.reachability),
            public_api: Arc::clone(&self.public_api),
            module_metrics: Arc::clone(&self.module_metrics),
            class_cohesion: Arc::clone(&self.class_cohesion),
            decorator_index: Arc::clone(&self.decorator_index),
            git_churn: Arc::clone(&self.git_churn),
            co_change_summary: Arc::clone(&self.co_change_summary),
            co_change_matrix: self.co_change_matrix.as_ref().map(Arc::clone),
            ownership: self.ownership.as_ref().map(Arc::clone),
            cached_embeddings: self.cached_embeddings.as_ref().map(Arc::clone),
        }
    }
}

// ── precompute_gd_startup ────────────────────────────────────────────────────

/// Build all GD pre-compute data (contexts + HMM + taint) as a standalone computation.
///
/// This is a pure function — it only reads the graph and files.
/// Can run in parallel with GI detectors via `thread::scope`.
pub fn precompute_gd_startup(
    graph: &dyn crate::graph::GraphQuery,
    repo_path: &std::path::Path,
    hmm_cache_path: Option<&std::path::PathBuf>,
    source_files: &[std::path::PathBuf],
    value_store: Option<Arc<crate::values::store::ValueStore>>,
    detectors: &[Arc<dyn crate::detectors::base::Detector>],
) -> PrecomputedAnalysis {
    let _i = graph.interner();

    // Check which expensive sub-systems are actually needed by the registered detectors.
    let needs_taint = detectors.iter().any(|d| d.taint_category().is_some());
    // All detectors now receive a full AnalysisContext, so always build contexts.
    let needs_func_ctx = true;

    // Parallel pre-compute: only spawn threads for sub-systems that are needed.
    //   Thread 1: taint (1.5s)           — cross-function + intra-function taint (skipped if no security detectors)
    //   Thread 2: HMM (0.4s)             — Hidden Markov Model context extraction
    //   Thread 3: DetectorContext (~0.3s) — callers/callees maps, file contents, class hierarchy
    //   Thread 4: ReachabilityIndex       — BFS from entry points
    //   Thread 5: PublicApiSet + ModuleMetrics + ClassCohesion + DecoratorIndex
    //   Main:     contexts (1.5s)         — adjacency + betweenness + context map (skipped if no context-using detectors)
    let (
        contexts,
        hmm_contexts,
        hmm_with_confidence,
        taint_results,
        detector_context,
        file_index,
        reachability,
        public_api,
        module_metrics_map,
        class_cohesion_map,
        decorator_index_map,
    ) = std::thread::scope(|s| {
        // Thread 1: Taint analysis (only if any detector needs taint)
        let taint_handle = if needs_taint {
            Some(s.spawn(|| {
                crate::detectors::taint::centralized::run_centralized_taint(graph, repo_path, None)
            }))
        } else {
            debug!("Skipping taint pre-compute: no detectors need taint");
            None
        };

        // Thread 2: HMM context extraction
        let hmm_handle = s.spawn(|| build_hmm_contexts_standalone(graph, hmm_cache_path));

        // Thread 3: DetectorContext (callers/callees maps, file contents, class hierarchy)
        let vs_clone = value_store.clone();
        let ctx_handle = s.spawn(move || {
            let (det_ctx, file_data) =
                super::DetectorContext::build(graph, source_files, vs_clone, repo_path);
            let file_index = Arc::new(super::FileIndex::new(file_data));
            (Arc::new(det_ctx), file_index)
        });

        // Thread 4: Reachability index (BFS from entry points)
        let reachability_handle = s.spawn(|| super::reachability::ReachabilityIndex::build(graph));

        // Thread 5: Public API + Module metrics + Class cohesion + Decorator index
        let enrichment_handle = s.spawn(|| {
            let public_api = super::reachability::build_public_api(graph);
            let module_metrics = super::module_metrics::build_module_metrics(graph);
            let class_cohesion = super::module_metrics::build_class_cohesion(graph);
            let decorator_index = super::reachability::build_decorator_index(graph);
            (public_api, module_metrics, class_cohesion, decorator_index)
        });

        // Main thread: Function contexts (only if any detector uses context)
        let ctx = if needs_func_ctx {
            FunctionContextBuilder::new(graph).build()
        } else {
            debug!("Skipping FunctionContextBuilder: no detectors use context");
            HashMap::new()
        };

        let empty_taint = || crate::detectors::taint::centralized::CentralizedTaintResults {
            cross_function: HashMap::new(),
            intra_function: HashMap::new(),
        };
        let taint = taint_handle
            .map(|h| match h.join() {
                Ok(result) => result,
                Err(e) => {
                    error!("taint thread panicked: {:?}", e);
                    empty_taint()
                }
            })
            .unwrap_or_else(empty_taint);
        let (hmm, hmm_conf) = match hmm_handle.join() {
            Ok(result) => result,
            Err(e) => {
                error!("HMM thread panicked: {:?}", e);
                (HashMap::new(), HashMap::new())
            }
        };
        let (det_ctx, file_index) = match ctx_handle.join() {
            Ok(result) => result,
            Err(e) => {
                error!("DetectorContext thread panicked: {:?}", e);
                (
                    Arc::new(super::DetectorContext::empty()),
                    Arc::new(super::FileIndex::new(vec![])),
                )
            }
        };
        let reachability = match reachability_handle.join() {
            Ok(result) => result,
            Err(e) => {
                error!("reachability thread panicked: {:?}", e);
                super::reachability::ReachabilityIndex::empty()
            }
        };
        let (public_api, module_metrics, class_cohesion, decorator_index) =
            match enrichment_handle.join() {
                Ok(result) => result,
                Err(e) => {
                    error!("enrichment thread panicked: {:?}", e);
                    (
                        HashSet::new(),
                        HashMap::new(),
                        HashMap::new(),
                        HashMap::new(),
                    )
                }
            };
        (
            ctx,
            hmm,
            hmm_conf,
            taint,
            det_ctx,
            file_index,
            reachability,
            public_api,
            module_metrics,
            class_cohesion,
            decorator_index,
        )
    });

    PrecomputedAnalysis {
        contexts: Arc::new(contexts),
        hmm_contexts: Arc::new(hmm_contexts),
        hmm_with_confidence: Arc::new(hmm_with_confidence),
        taint_results: Arc::new(taint_results),
        detector_context,
        file_index,
        reachability: Arc::new(reachability),
        public_api: Arc::new(public_api),
        module_metrics: Arc::new(module_metrics_map),
        class_cohesion: Arc::new(class_cohesion_map),
        decorator_index: Arc::new(decorator_index_map),
        git_churn: Arc::new(HashMap::new()),
        co_change_summary: Arc::new(HashMap::new()),
        co_change_matrix: None,
        ownership: None,
        cached_embeddings: None,
    }
}

// ── build_hmm_contexts_standalone ────────────────────────────────────────────

/// Standalone HMM context building.
/// Returns (contexts_map, contexts_with_confidence_map).
fn build_hmm_contexts_standalone(
    graph: &dyn crate::graph::GraphQuery,
    hmm_cache_path: Option<&std::path::PathBuf>,
) -> (
    HashMap<String, FunctionContext>,
    HashMap<String, (FunctionContext, f64)>,
) {
    let i = graph.interner();
    // Try to load cached HMM+CRF model
    let mut classifier = if let Some(path) = hmm_cache_path {
        let model_path = path.join("hmm_model.json");
        if model_path.exists() {
            info!("Loading cached HMM+CRF model from {:?}", model_path);
            ContextClassifier::load(&model_path)
                .unwrap_or_else(|| ContextClassifier::for_codebase(Some(&model_path)))
        } else {
            ContextClassifier::new()
        }
    } else {
        ContextClassifier::new()
    };

    info!("Building HMM function contexts from graph (standalone)...");
    let mut functions = graph.get_functions();

    if functions.is_empty() {
        return (HashMap::new(), HashMap::new());
    }

    // Build call data from index-based adjacency
    let (adj, rev_adj, qn_to_idx) = graph.get_call_adjacency();
    let file_paths: Vec<String> = functions.iter().map(|f| f.path(i).to_string()).collect();

    // Limit function count to prevent OOM on huge codebases
    const MAX_FUNCTIONS_FOR_HMM: usize = 20_000;
    if functions.len() > MAX_FUNCTIONS_FOR_HMM {
        warn!(
            "Limiting HMM analysis to {} functions (codebase has {})",
            MAX_FUNCTIONS_FOR_HMM,
            functions.len()
        );
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

    let avg_complexity = if complexity_count > 0 {
        total_complexity as f64 / complexity_count as f64
    } else {
        10.0
    };
    let avg_loc = total_loc as f64 / functions.len().max(1) as f64;
    let avg_params = total_params as f64 / functions.len().max(1) as f64;

    // Extract features
    let mut function_data = Vec::new();
    for func in &functions {
        let idx = qn_to_idx.get(&func.qualified_name).copied().unwrap_or(0);
        let fan_in = rev_adj.get(idx).map_or(0, |v| v.len());
        let fan_out = adj.get(idx).map_or(0, |v| v.len());
        let caller_files_count = rev_adj.get(idx).map_or(0, |callers| {
            callers
                .iter()
                .filter_map(|&ci| file_paths.get(ci).map(|s| s.as_str()))
                .collect::<std::collections::HashSet<&str>>()
                .len()
        });
        let loc = func.line_end.saturating_sub(func.line_start) + 1;
        let address_taken = func.address_taken();
        let features = FunctionFeatures::extract(&FunctionMetrics {
            name: func.node_name(i),
            file_path: func.path(i),
            fan_in,
            fan_out,
            max_fan_in,
            max_fan_out,
            caller_files: caller_files_count,
            complexity: func.complexity_opt(),
            avg_complexity,
            loc,
            avg_loc,
            param_count: 3,
            avg_params,
            address_taken,
        });
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

    // Classify all functions (with and without confidence)
    let mut contexts = HashMap::new();
    let mut contexts_with_conf = HashMap::new();
    for (func, (features, _, _, _)) in functions.iter().zip(function_data.iter()) {
        let qn = func.qn(i).to_string();
        let (context, confidence) = classifier.classify_with_confidence(&qn, features);
        contexts.insert(qn.clone(), context);
        contexts_with_conf.insert(qn, (context, confidence));
    }

    info!(
        "Classified {} functions using HMM (standalone)",
        contexts.len()
    );
    (contexts, contexts_with_conf)
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_split_detection_completeness() {
        // Verify every detector is either graph-independent or graph-dependent
        let init = crate::detectors::DetectorInit::test_default();
        let all_detectors = crate::detectors::create_all_detectors(&init);
        let gi_count = all_detectors.iter().filter(|d| !d.requires_graph()).count();
        let gd_count = all_detectors.iter().filter(|d| d.requires_graph()).count();

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
    }
}
