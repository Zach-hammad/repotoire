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

    // Incremental fast path: if all three hints are present, dispatch to
    // the incremental function which only re-runs detectors on changed files.
    if let (Some(changed_files), Some(cached_file_findings), Some(cached_graph_wide_findings)) = (
        input.changed_files,
        input.cached_file_findings,
        input.cached_graph_wide_findings,
    ) {
        return detect_stage_incremental(
            input,
            &detectors,
            changed_files,
            cached_file_findings,
            cached_graph_wide_findings,
            &resolver,
        );
    }

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

// ── Incremental fast path ────────────────────────────────────────────────────

/// Run detectors incrementally: per-file detectors only on changed files,
/// graph-wide detectors reuse cached findings when topology is unchanged.
fn detect_stage_incremental(
    input: &DetectInput,
    detectors: &[Arc<dyn crate::detectors::Detector>],
    changed_files: &[PathBuf],
    cached_file_findings: &HashMap<PathBuf, Vec<Finding>>,
    cached_graph_wide_findings: &HashMap<String, Vec<Finding>>,
    resolver: &crate::calibrate::ThresholdResolver,
) -> Result<DetectOutput> {
    let graph = input.graph;
    let changed_set: HashSet<&PathBuf> = changed_files.iter().collect();

    // Partition detectors by scope
    let mut file_local = Vec::new();
    let mut file_scoped_graph = Vec::new();
    let mut graph_wide_detectors = Vec::new();

    for d in detectors {
        match d.detector_scope() {
            DetectorScope::FileLocal => file_local.push(Arc::clone(d)),
            DetectorScope::FileScopedGraph => file_scoped_graph.push(Arc::clone(d)),
            DetectorScope::GraphWide => graph_wide_detectors.push(Arc::clone(d)),
        }
    }

    // Precompute: reuse cached when topology unchanged, else full recompute
    let precompute_start = Instant::now();
    let precomputed = if !input.topology_changed && input.cached_gd_precomputed.is_some() {
        // Fast path: reuse cached PrecomputedAnalysis
        // Re-run TAINT because changed files may have new sinks/sources
        let cached = input.cached_gd_precomputed.unwrap();
        let mut reused = cached.clone(); // cheap: all Arc bumps
        reused.git_churn = Arc::clone(&input.file_churn);

        let needs_taint = detectors.iter().any(|d| d.taint_category().is_some());
        if needs_taint {
            let taint = crate::detectors::taint::centralized::run_centralized_taint(
                graph,
                input.repo_path,
                None,
            );
            reused.taint_results = Arc::new(taint);
        }

        // Rebuild file index: keep cached entries for unchanged files,
        // add fresh content for changed files
        let changed_set_fi: HashSet<&PathBuf> = changed_files.iter().collect();
        let mut file_data: Vec<_> = reused
            .file_index
            .all()
            .iter()
            .filter(|entry| !changed_set_fi.contains(&entry.path))
            .map(|entry| (entry.path.clone(), Arc::clone(&entry.content), entry.flags))
            .collect();
        for p in changed_files {
            if let Some(content_string) = crate::cache::global_cache().content(p) {
                let content: Arc<str> = Arc::from(content_string.as_str());
                let flags =
                    crate::detectors::detector_context::compute_content_flags(&content);
                file_data.push((p.clone(), content, flags));
            }
        }
        reused.file_index = Arc::new(crate::detectors::file_index::FileIndex::new(file_data));

        inject_taint_precomputed(detectors, &reused.taint_results);
        reused
    } else {
        // Slow path: full precompute
        let hmm_cache_path = input.repo_path.join(".repotoire");
        let vs_clone = input.value_store.cloned();
        let mut precomputed = precompute_gd_startup(
            graph,
            input.repo_path,
            Some(&hmm_cache_path),
            input.source_files,
            vs_clone,
            detectors,
        );
        inject_taint_precomputed(detectors, &precomputed.taint_results);
        precomputed.git_churn = Arc::clone(&input.file_churn);
        precomputed
    };
    let precompute_duration = precompute_start.elapsed();

    // Build contexts: scoped (changed files only) and full (all files)
    let scoped_ctx = precomputed.to_context_scoped(graph, resolver, changed_files);
    let full_ctx = precomputed.to_context(graph, resolver);

    let mut all_findings: Vec<Finding> = Vec::new();
    let mut findings_by_file: HashMap<PathBuf, Vec<Finding>> = HashMap::new();
    let mut graph_wide_findings_out: HashMap<String, Vec<Finding>> = HashMap::new();

    // Pre-build bypass_set from ALL detectors (not just those that run)
    let bypass_set: HashSet<String> = detectors
        .iter()
        .filter(|d| d.bypass_postprocessor())
        .map(|d| d.name().to_string())
        .collect();

    // 1. Carry forward cached findings for UNCHANGED files
    for (file, findings) in cached_file_findings {
        if !changed_set.contains(file) {
            findings_by_file.insert(file.clone(), findings.clone());
            all_findings.extend(findings.iter().cloned());
        }
    }

    // 2. Run FileLocal detectors on CHANGED files only
    if !file_local.is_empty() {
        let (mut fl_findings, _) = run_detectors(&file_local, &scoped_ctx, input.workers);
        fl_findings = apply_hmm_context_filter(fl_findings, &scoped_ctx);
        filter_test_file_findings(&mut fl_findings);
        for f in &fl_findings {
            for file in &f.affected_files {
                findings_by_file
                    .entry(file.clone())
                    .or_default()
                    .push(f.clone());
            }
        }
        all_findings.extend(fl_findings);
    }

    // 3. FileScopedGraph detectors: use scoped context if topology stable,
    //    full context if topology changed
    if !file_scoped_graph.is_empty() {
        let ctx = if input.topology_changed {
            &full_ctx
        } else {
            &scoped_ctx
        };
        let (mut fsg_findings, _) = run_detectors(&file_scoped_graph, ctx, input.workers);
        fsg_findings = apply_hmm_context_filter(fsg_findings, ctx);
        filter_test_file_findings(&mut fsg_findings);
        for f in &fsg_findings {
            for file in &f.affected_files {
                findings_by_file
                    .entry(file.clone())
                    .or_default()
                    .push(f.clone());
            }
        }
        all_findings.extend(fsg_findings);
    }

    // 4. GraphWide detectors: re-run if topology changed, else reuse cache
    if input.topology_changed {
        let (mut gw_findings, _) =
            run_detectors(&graph_wide_detectors, &full_ctx, input.workers);
        gw_findings = apply_hmm_context_filter(gw_findings, &full_ctx);
        filter_test_file_findings(&mut gw_findings);
        for f in &gw_findings {
            graph_wide_findings_out
                .entry(f.detector.clone())
                .or_default()
                .push(f.clone());
        }
        all_findings.extend(gw_findings);
    } else {
        for (detector, findings) in cached_graph_wide_findings {
            graph_wide_findings_out.insert(detector.clone(), findings.clone());
            all_findings.extend(findings.iter().cloned());
        }
    }

    sort_findings_deterministic(&mut all_findings);

    Ok(DetectOutput {
        findings: all_findings,
        precomputed,
        findings_by_file,
        graph_wide_findings: graph_wide_findings_out,
        bypass_set,
        stats: DetectStats {
            detectors_run: detectors.len(),
            detectors_skipped: 0,
            gi_findings: 0,
            gd_findings: 0,
            precompute_duration,
        },
    })
}
