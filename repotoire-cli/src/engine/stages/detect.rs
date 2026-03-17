//! Stage 6: Detector execution.

use crate::calibrate::{NgramModel, StyleProfile};
use crate::config::ProjectConfig;
use crate::detectors::GdPrecomputed;
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

    // Incremental optimization hints (engine provides these)
    pub changed_files: Option<&'a [PathBuf]>,
    pub topology_changed: bool,
    pub cached_gd_precomputed: Option<&'a GdPrecomputed>,
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
    pub gd_precomputed: GdPrecomputed,
    pub findings_by_file: HashMap<PathBuf, Vec<Finding>>,
    /// Keyed by detector name for selective invalidation on incremental runs.
    pub graph_wide_findings: HashMap<String, Vec<Finding>>,
    pub stats: DetectStats,
}

/// Build detectors, precompute shared data, run all detectors in parallel.
pub fn detect_stage(input: &DetectInput) -> Result<DetectOutput> {
    let skip_set: HashSet<&str> = input.skip_detectors.iter().map(|s| s.as_str()).collect();

    // Build the detector engine
    let hmm_cache_path = input.repo_path.join(".repotoire");
    let mut engine = crate::detectors::DetectorEngine::new(input.workers)
        .with_hmm_cache(hmm_cache_path.clone());

    // Build DetectorInit and create all detectors via the registry
    let resolver = crate::detectors::build_threshold_resolver(input.style_profile);
    engine.set_threshold_resolver(resolver.clone());

    let init = crate::detectors::DetectorInit {
        repo_path: input.repo_path,
        project_config: input.project_config,
        resolver,
        ngram_model: input.ngram_model,
    };
    for detector in crate::detectors::create_all_detectors(&init) {
        let name = detector.name();
        if !skip_set.contains(name) {
            engine.register(detector);
        }
    }

    // Build file provider
    let source_files = crate::detectors::SourceFiles::new(
        input.source_files.to_vec(),
        input.repo_path.to_path_buf(),
    );

    // Precompute GD data (contexts, HMM, taint, etc.)
    let precompute_start = Instant::now();
    let vs_clone = input.value_store.cloned();
    let detectors_ref = engine.detectors();
    let gd_precomputed = crate::detectors::precompute_gd_startup(
        input.graph,
        input.repo_path,
        Some(&hmm_cache_path),
        input.source_files,
        vs_clone,
        detectors_ref,
    );
    let precompute_duration = precompute_start.elapsed();

    // Inject precomputed data and run GI + GD phases
    let gi_findings = engine.run_graph_independent(input.graph, &source_files)?;
    let gi_count = gi_findings.len();

    engine.inject_gd_precomputed(gd_precomputed.clone());
    let gd_findings = engine.run_graph_dependent(input.graph, &source_files)?;
    let gd_count = gd_findings.len();

    let detectors_run = engine.detector_count();

    // Merge findings
    let mut findings = gi_findings;
    findings.extend(gd_findings);

    // Partition findings into per-file and graph-wide
    let mut findings_by_file: HashMap<PathBuf, Vec<Finding>> = HashMap::new();
    let mut graph_wide_findings: HashMap<String, Vec<Finding>> = HashMap::new();

    for finding in &findings {
        if finding.affected_files.is_empty() {
            // Graph-wide finding (no specific file) — key by detector name
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
        gd_precomputed,
        findings_by_file,
        graph_wide_findings,
        stats: DetectStats {
            detectors_run,
            detectors_skipped: 0,
            gi_findings: gi_count,
            gd_findings: gd_count,
            precompute_duration,
        },
    })
}
