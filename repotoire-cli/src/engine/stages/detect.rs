//! Stage 6: Detector execution.

use crate::calibrate::{NgramModel, StyleProfile};
use crate::config::ProjectConfig;
use crate::detectors::analysis_context::FileChurnInfo;
use crate::detectors::base::DetectorScope;
use crate::detectors::{
    apply_hmm_context_filter, build_threshold_resolver, create_all_detectors,
    create_default_detectors, filter_test_file_findings, inject_taint_precomputed,
    precompute_gd_startup, run_detectors, sort_findings_deterministic, DetectorInit,
    PrecomputedAnalysis,
};
use crate::engine::ProgressFn;
use crate::graph::GraphQuery;
use crate::models::Finding;
use crate::values::store::ValueStore;
use anyhow::Result;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

/// Input for the detect stage.
pub struct DetectInput<'a> {
    pub graph: &'a dyn GraphQuery,
    pub source_files: &'a [PathBuf],
    pub repo_path: &'a Path,
    pub project_config: &'a ProjectConfig,
    pub style_profile: Option<&'a StyleProfile>,
    pub ngram_model: Option<&'a NgramModel>,
    pub value_store: Option<&'a Arc<ValueStore>>,
    pub skip_detectors: &'a [String],
    pub workers: usize,
    pub progress: Option<ProgressFn>,

    /// Per-file git churn data from git enrichment stage.
    pub file_churn: Arc<HashMap<String, FileChurnInfo>>,

    /// Run all detectors including deep-scan detectors (default: false).
    pub all_detectors: bool,

    // Incremental optimization hints (engine provides these)
    pub changed_files: Option<&'a [PathBuf]>,
    pub topology_changed: bool,
    pub cached_gd_precomputed: Option<&'a PrecomputedAnalysis>,
    pub cached_file_findings: Option<&'a HashMap<PathBuf, Vec<Finding>>>,
    pub cached_graph_wide_findings: Option<&'a HashMap<String, Vec<Finding>>>,
}

/// Statistics from the detect stage.
pub struct DetectStats {
    pub detectors_run: usize,
    pub detectors_skipped: usize,
    pub gi_findings: usize,
    pub gd_findings: usize,
    pub precompute_duration: Duration,
}

/// Output from the detect stage.
pub struct DetectOutput {
    pub findings: Vec<Finding>,
    pub precomputed: PrecomputedAnalysis,
    pub findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    /// Keyed by detector name for selective invalidation on incremental runs.
    pub graph_wide_findings: HashMap<String, Vec<Finding>>,
    /// Detector names that opt out of GBDT postprocessor filtering.
    pub bypass_set: HashSet<String>,
    pub stats: DetectStats,
}

/// Build detectors, precompute shared data, run all detectors in parallel.
pub fn detect_stage(input: &DetectInput) -> Result<DetectOutput> {
    let skip_set: HashSet<&str> = input.skip_detectors.iter().map(|s| s.as_str()).collect();

    // Build DetectorInit and create all detectors via the registry
    let resolver = build_threshold_resolver(input.style_profile);

    let init = DetectorInit {
        repo_path: input.repo_path,
        project_config: input.project_config,
        resolver: resolver.clone(),
        ngram_model: input.ngram_model,
    };

    let detectors: Vec<Arc<dyn crate::detectors::Detector>> = if input.all_detectors {
        create_all_detectors(&init)
    } else {
        create_default_detectors(&init)
    }
        .into_iter()
        .filter(|d| !skip_set.contains(d.name()))
        .collect();

    let detectors_run = detectors.len();
    let detectors_skipped = skip_set.len();

    let graph = input.graph;

    // Precompute GD data (contexts, HMM, taint, etc.)
    let precompute_start = Instant::now();
    let hmm_cache_path = input.repo_path.join(".repotoire");
    let vs_clone = input.value_store.cloned();

    let mut precomputed = precompute_gd_startup(
        graph,
        input.repo_path,
        Some(&hmm_cache_path),
        input.source_files,
        vs_clone,
        &detectors,
    );
    let precompute_duration = precompute_start.elapsed();

    // Inject pre-computed taint results into security detectors
    inject_taint_precomputed(&detectors, &precomputed.taint_results);

    // Inject git churn data into precomputed analysis
    precomputed.git_churn = Arc::clone(&input.file_churn);

    // Build analysis context from precomputed data
    let ctx = precomputed.to_context(graph, &resolver);

    // Run all detectors in parallel
    let (mut findings, bypass_set) = run_detectors(&detectors, &ctx, input.workers);

    let total_findings = findings.len();

    // Post-detection filters
    findings = apply_hmm_context_filter(findings, &ctx);
    filter_test_file_findings(&mut findings);
    sort_findings_deterministic(&mut findings);

    // Build a scope lookup so we route by detector_scope(), not affected_files.
    // Graph-wide detectors (e.g. MutualRecursionDetector, SinglePointOfFailureDetector)
    // may set affected_files but should still be keyed by detector name for selective
    // invalidation on incremental runs.
    let scope_map: HashMap<String, DetectorScope> = detectors
        .iter()
        .map(|d| (d.name().to_string(), d.detector_scope()))
        .collect();

    // Partition findings into per-file and graph-wide
    let mut findings_by_file: HashMap<PathBuf, Vec<Finding>> = HashMap::new();
    let mut graph_wide_findings: HashMap<String, Vec<Finding>> = HashMap::new();

    for finding in &findings {
        let scope = scope_map
            .get(&finding.detector)
            .copied()
            .unwrap_or(DetectorScope::FileScopedGraph);
        if scope == DetectorScope::GraphWide {
            // Graph-wide finding — key by detector name
            graph_wide_findings
                .entry(finding.detector.clone())
                .or_default()
                .push(finding.clone());
        } else {
            // File-specific finding
            for file in &finding.affected_files {
                findings_by_file
                    .entry(file.clone())
                    .or_default()
                    .push(finding.clone());
            }
        }
    }

    Ok(DetectOutput {
        findings,
        precomputed,
        findings_by_file,
        graph_wide_findings,
        bypass_set,
        stats: DetectStats {
            detectors_run,
            detectors_skipped,
            gi_findings: 0,       // unified run — no GI/GD split
            gd_findings: total_findings, // all findings from unified run
            precompute_duration,
        },
    })
}
