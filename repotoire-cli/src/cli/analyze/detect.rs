//! Detection functions for the analyze command
//!
//! This module contains all detector-related logic:
//! - Running detectors on the code graph
//! - Git history enrichment
//! - Voting and consolidation
//! - Incremental caching

use crate::config::ProjectConfig;
use crate::detectors::{
    ConfidenceMethod, DetectorEngine, IncrementalCache, SeverityResolution, SourceFiles,
    VotingEngine, VotingStats, VotingStrategy,
};
use crate::git;
use crate::graph::GraphStore;
use crate::models::Finding;
use anyhow::Result;
use console::style;
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Start git enrichment in background thread
pub(super) fn start_git_enrichment(
    no_git: bool,
    quiet_mode: bool,
    repo_path: &Path,
    graph: Arc<GraphStore>,
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
) -> Option<(
    std::thread::JoinHandle<Result<git::enrichment::EnrichmentStats, anyhow::Error>>,
    ProgressBar,
)> {
    if no_git {
        if !quiet_mode {
            eprintln!("{}Skipping git enrichment (--no-git)", style("⏭ ").dim());
        }
        return None;
    }

    let git_spinner = multi.add(ProgressBar::new_spinner());
    git_spinner.set_style(spinner_style.clone());
    git_spinner.set_message("Enriching with git history (async)...");
    git_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

    let repo_path_clone = repo_path.to_path_buf();
    let git_handle = std::thread::spawn(move || {
        git::enrichment::enrich_graph_with_git(&repo_path_clone, &graph, None)
    });

    Some((git_handle, git_spinner))
}

/// Wait for git enrichment to complete
pub(super) fn finish_git_enrichment(
    git_result: Option<(
        std::thread::JoinHandle<Result<git::enrichment::EnrichmentStats, anyhow::Error>>,
        ProgressBar,
    )>,
) {
    let Some((git_handle, git_spinner)) = git_result else {
        return;
    };
    match git_handle.join() {
        Ok(Ok(stats)) if stats.functions_enriched > 0 || stats.classes_enriched > 0 => {
            let cache_info = if stats.cache_hits > 0 {
                format!(" ({} cached)", stats.cache_hits)
            } else {
                String::new()
            };
            git_spinner.finish_with_message(format!(
                "{}Enriched {} functions, {} classes{}",
                style("✓ ").green(),
                style(stats.functions_enriched).cyan(),
                style(stats.classes_enriched).cyan(),
                style(cache_info).dim(),
            ));
        }
        Ok(Ok(_)) => {
            git_spinner
                .finish_with_message(format!("{}No git history to enrich", style("- ").dim(),));
        }
        Ok(Err(e)) => {
            git_spinner.finish_with_message(format!(
                "{}Git enrichment skipped: {}",
                style("⚠ ").yellow(),
                e
            ));
        }
        Err(_) => {
            git_spinner
                .finish_with_message(format!("{}Git enrichment failed", style("⚠ ").yellow(),));
        }
    }
}

/// Run all detectors on the graph (non-speculative fallback)
#[allow(dead_code)]
pub(super) fn run_detectors(
    graph: &Arc<GraphStore>,
    repo_path: &Path,
    project_config: &ProjectConfig,
    skip_detector: &[String],
    run_external: bool,
    workers: usize,
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
    quiet_mode: bool,
    no_emoji: bool,
    cache: &mut IncrementalCache,
    all_files: &[std::path::PathBuf],
    style_profile: Option<&crate::calibrate::StyleProfile>,
    ngram_model: Option<crate::calibrate::NgramModel>,
    timings: bool,
) -> Result<Vec<Finding>> {
    // Check if we can use cached detector results
    if cache.can_use_cached_detectors(all_files) {
        let cached_findings = cache.all_cached_graph_findings();
        if !cached_findings.is_empty() && !quiet_mode {
            let icon = if no_emoji { "" } else { "⚡ " };
            println!(
                "\n{}Using cached detector results ({} findings)",
                style(icon).bold(),
                cached_findings.len()
            );
            return Ok(cached_findings);
        }
    }

    if !quiet_mode {
        let det_icon = if no_emoji { "" } else { "🕵️  " };
        println!("\n{}Running detectors...", style(det_icon).bold());
    }

    // Set up HMM cache in .repotoire directory
    let hmm_cache_path = repo_path.join(".repotoire");
    let mut engine = DetectorEngine::new(workers)
        .with_hmm_cache(hmm_cache_path)
        .with_timings(timings);

    // Wire adaptive threshold resolver into the engine for AnalysisContext propagation
    engine.set_threshold_resolver(crate::detectors::build_threshold_resolver(style_profile));

    let skip_set: HashSet<&str> = skip_detector.iter().map(|s| s.as_str()).collect();

    // Register default detectors
    for detector in
        crate::detectors::default_detectors_with_ngram(repo_path, project_config, style_profile, ngram_model)
    {
        let name = detector.name();
        if !skip_set.contains(name) {
            engine.register(detector);
        }
    }

    // All detectors are now built-in pure Rust — no external tools
    let _ = run_external;

    let detector_bar = multi.add(ProgressBar::new_spinner());
    detector_bar.set_style(spinner_style.clone());
    detector_bar.set_message("Running detectors...");
    detector_bar.enable_steady_tick(std::time::Duration::from_millis(100));

    // Build centralized file provider from collected file list
    let source_files = SourceFiles::new(all_files.to_vec(), repo_path.to_path_buf());
    let findings = engine.run(graph, &source_files)?;

    detector_bar.finish_with_message(format!(
        "{}Ran {} detectors, found {} raw issues",
        style("✓ ").green(),
        style(engine.detector_count()).cyan(),
        style(findings.len()).cyan(),
    ));

    // Update graph hash for cache validation (findings cached after postprocessing, #65)
    let graph_hash = cache.compute_all_files_hash(all_files);
    cache.update_graph_hash(&graph_hash);
    let _ = cache.save_cache();

    Ok(findings)
}

/// Run GI detectors standalone — used for Parse ∥ GI overlap.
///
/// Creates its own DetectorEngine, registers all detectors, and runs only the
/// graph-independent subset. Uses a dummy empty graph since GI detectors don't
/// query the graph for meaningful data.
pub(super) fn run_gi_detectors(
    files: &[PathBuf],
    repo_path: &Path,
    project_config: &ProjectConfig,
    skip_detector: &[String],
    style_profile: Option<&crate::calibrate::StyleProfile>,
    ngram_model: Option<crate::calibrate::NgramModel>,
    workers: usize,
    timings: bool,
) -> Result<Vec<Finding>> {
    let skip_set: HashSet<&str> = skip_detector.iter().map(|s| s.as_str()).collect();
    let hmm_cache_path = repo_path.join(".repotoire");
    let mut engine = DetectorEngine::new(workers)
        .with_hmm_cache(hmm_cache_path)
        .with_timings(timings);

    // Wire adaptive threshold resolver into the engine
    engine.set_threshold_resolver(crate::detectors::build_threshold_resolver(style_profile));

    for detector in crate::detectors::default_detectors_with_ngram(
        repo_path,
        project_config,
        style_profile,
        ngram_model,
    ) {
        let name = detector.name();
        if !skip_set.contains(name) {
            engine.register(detector);
        }
    }

    let source_files = SourceFiles::new(files.to_vec(), repo_path.to_path_buf());

    // GI detectors don't use the graph — pass a dummy empty one to avoid
    // any locking contention with the concurrent parse+graph pipeline.
    let dummy_graph = GraphStore::in_memory();
    engine.run_graph_independent(&dummy_graph, &source_files)
}

/// Run detectors in speculative mode: graph-independent first, then graph-dependent.
///
/// Graph-independent detectors run immediately (they only need file content).
/// Graph-dependent detectors run after git enrichment completes (they need the full graph).
///
/// This overlaps file-local detection with git enrichment, reducing total wall-clock time.
///
/// The `between_phases` callback is invoked between the two phases, allowing the caller
/// to finish git enrichment before graph-dependent detectors start.
///
/// If `pre_gi_findings` is Some, the GI phase was already executed during parse
/// (Parse ∥ GI overlap). In that case, we skip the GI phase and only run
/// GD pre-compute + GD parallel detectors.
pub(super) fn run_detectors_speculative(
    graph: &Arc<GraphStore>,
    repo_path: &Path,
    project_config: &ProjectConfig,
    skip_detector: &[String],
    run_external: bool,
    workers: usize,
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
    quiet_mode: bool,
    no_emoji: bool,
    cache: &mut IncrementalCache,
    all_files: &[PathBuf],
    style_profile: Option<&crate::calibrate::StyleProfile>,
    ngram_model: Option<crate::calibrate::NgramModel>,
    timings: bool,
    between_phases: impl FnOnce(),
    pre_gi_findings: Option<Vec<Finding>>,
    value_store: Option<Arc<crate::values::store::ValueStore>>,
) -> Result<Vec<Finding>> {
    // Check if we can use cached detector results (same fast path as run_detectors)
    if cache.can_use_cached_detectors(all_files) {
        let cached_findings = cache.all_cached_graph_findings();
        if !cached_findings.is_empty() && !quiet_mode {
            let icon = if no_emoji { "" } else { "⚡ " };
            println!(
                "\n{}Using cached detector results ({} findings)",
                style(icon).bold(),
                cached_findings.len()
            );
            // Still run between_phases so git enrichment finishes cleanly
            between_phases();
            return Ok(cached_findings);
        }
    }

    if !quiet_mode {
        let det_icon = if no_emoji { "" } else { "🕵️  " };
        println!(
            "\n{}Running detectors (speculative mode)...",
            style(det_icon).bold()
        );
    }

    // Set up engine (same as run_detectors)
    let hmm_cache_path = repo_path.join(".repotoire");
    let mut engine = DetectorEngine::new(workers)
        .with_hmm_cache(hmm_cache_path)
        .with_timings(timings);

    // Wire adaptive threshold resolver into the engine
    engine.set_threshold_resolver(crate::detectors::build_threshold_resolver(style_profile));

    let skip_set: HashSet<&str> = skip_detector.iter().map(|s| s.as_str()).collect();

    for detector in crate::detectors::default_detectors_with_ngram(
        repo_path,
        project_config,
        style_profile,
        ngram_model,
    ) {
        let name = detector.name();
        if !skip_set.contains(name) {
            engine.register(detector);
        }
    }

    // All detectors are now built-in pure Rust — no external tools
    let _ = run_external;

    let source_files = SourceFiles::new(all_files.to_vec(), repo_path.to_path_buf());

    // If GI findings were pre-computed during parse (Parse ∥ GI overlap),
    // skip the GI phase and only run GD pre-compute + GD parallel.
    let has_pre_gi = pre_gi_findings.is_some();
    let (gi_findings, gi_count) = if let Some(gi) = pre_gi_findings {
        let count = gi.len();
        (gi, count)
    } else {
        // Pipeline parallelism: overlap GI detectors with GD pre-compute.
        // GI detectors only need file content (no graph contexts, HMM, or taint).
        // GD pre-compute (contexts + HMM + taint) only reads the graph.
        // These are fully independent and can run simultaneously.
        //
        // Timeline:
        //   [GI detectors] ─────────┐
        //   [GD pre-compute] ───────┤→ [GD parallel detectors]
        //   [git enrichment] ───────┘

        let gi_bar = multi.add(ProgressBar::new_spinner());
        gi_bar.set_style(spinner_style.clone());
        gi_bar.set_message("Running file-local detectors + pre-computing graph contexts...");
        gi_bar.enable_steady_tick(std::time::Duration::from_millis(100));

        let hmm_cache_path_clone = repo_path.join(".repotoire");
        let repo_path_clone = repo_path.to_path_buf();
        let vs_clone = value_store.clone();

        // Run GI + GD pre-compute in parallel using thread::scope
        let detectors_ref = engine.detectors();
        let (gi_result, gd_precomputed) = std::thread::scope(|s| {
            // Background thread: GD pre-compute (contexts + HMM + taint + DetectorContext)
            let precompute_handle = s.spawn(|| {
                crate::detectors::precompute_gd_startup(
                    graph.as_ref(),
                    &repo_path_clone,
                    Some(&hmm_cache_path_clone),
                    all_files,
                    vs_clone,
                    detectors_ref,
                )
            });

            // Main thread: GI detectors
            let gi = engine.run_graph_independent(graph.as_ref(), &source_files);

            let pre = precompute_handle.join().expect("GD precompute thread panicked");
            (gi, pre)
        });

        let gi_findings = gi_result?;
        let gi_count = gi_findings.len();

        gi_bar.finish_with_message(format!(
            "{}File-local detectors: {} findings (+ graph contexts pre-computed)",
            style("✓ ").green(),
            style(gi_count).cyan(),
        ));

        // Inject pre-computed data into engine
        engine.inject_gd_precomputed(gd_precomputed);

        (gi_findings, gi_count)
    };

    // When GI was pre-computed (Parse ∥ GI), GD precompute hasn't run yet.
    // Overlap GD precompute with remaining git enrichment — they are independent:
    // GD precompute reads Call edges (unchanged by git), git enrichment writes
    // Commit nodes + ModifiedIn edges (irrelevant to GD precompute).
    if has_pre_gi {
        let pre_bar = multi.add(ProgressBar::new_spinner());
        pre_bar.set_style(spinner_style.clone());
        pre_bar.set_message("Pre-computing graph contexts...");
        pre_bar.enable_steady_tick(std::time::Duration::from_millis(100));

        let hmm_cache_path_clone = repo_path.join(".repotoire");
        let repo_path_clone = repo_path.to_path_buf();
        let vs_clone = value_store.clone();
        let detectors_ref = engine.detectors();
        // Run GD precompute ∥ git enrichment finish
        let gd_precomputed = std::thread::scope(|s| {
            let gd_handle = s.spawn(|| {
                crate::detectors::precompute_gd_startup(
                    graph.as_ref(),
                    &repo_path_clone,
                    Some(&hmm_cache_path_clone),
                    all_files,
                    vs_clone,
                    detectors_ref,
                )
            });
            // Wait for git enrichment while GD precompute runs in parallel
            between_phases();
            gd_handle.join().expect("GD precompute panicked")
        });

        engine.inject_gd_precomputed(gd_precomputed);

        pre_bar.finish_with_message(format!(
            "{}Graph contexts pre-computed",
            style("✓ ").green(),
        ));
    } else {
        // Finish git enrichment before GD detectors start (non-Parse∥GI path)
        between_phases();
    }

    let gd_bar = multi.add(ProgressBar::new_spinner());
    gd_bar.set_style(spinner_style.clone());
    gd_bar.set_message("Running graph-based detectors...");
    gd_bar.enable_steady_tick(std::time::Duration::from_millis(100));

    let gd_findings = engine.run_graph_dependent(graph, &source_files)?;
    let gd_count = gd_findings.len();

    gd_bar.finish_with_message(format!(
        "{}Graph-based detectors: {} findings",
        style("✓ ").green(),
        style(gd_count).cyan(),
    ));

    // Merge findings from both phases
    let mut findings = gi_findings;
    findings.extend(gd_findings);

    if !quiet_mode {
        println!(
            "  {} Ran {} detectors — {} raw issues ({} file-local + {} graph-based)",
            style("→").dim(),
            style(engine.detector_count()).cyan(),
            style(findings.len()).cyan(),
            style(gi_count).dim(),
            style(gd_count).dim(),
        );
    }

    // Update graph hash for cache validation
    let graph_hash = cache.compute_all_files_hash(all_files);
    cache.update_graph_hash(&graph_hash);
    let _ = cache.save_cache();

    Ok(findings)
}

/// Apply voting engine to consolidate findings
pub(super) fn apply_voting(
    findings: &mut Vec<Finding>,
    cached_findings: Vec<Finding>,
    is_incremental_mode: bool,
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
) -> (VotingStats, usize) {
    let voting_spinner = multi.add(ProgressBar::new_spinner());
    voting_spinner.set_style(spinner_style.clone());
    voting_spinner.set_message("Consolidating findings with voting engine...");
    voting_spinner.enable_steady_tick(std::time::Duration::from_millis(100));

    let voting_engine = VotingEngine::with_config(
        VotingStrategy::Weighted,
        ConfidenceMethod::Bayesian,
        SeverityResolution::Highest,
        0.5,
        2,
    );
    let (consolidated_findings, voting_stats) = voting_engine.vote(std::mem::take(findings));
    *findings = consolidated_findings;

    // Merge cached findings
    let cached_findings_count = cached_findings.len();
    if is_incremental_mode && !cached_findings.is_empty() {
        findings.extend(cached_findings);
        tracing::debug!(
            "Merged {} cached findings with {} new findings",
            cached_findings_count,
            voting_stats.total_output
        );
    }

    voting_spinner.finish_with_message(format!(
        "{}Consolidated {} -> {} findings ({} merged, {} rejected{})",
        style("✓ ").green(),
        style(voting_stats.total_input).cyan(),
        style(voting_stats.total_output).cyan(),
        style(voting_stats.boosted_by_consensus).dim(),
        style(voting_stats.rejected_low_confidence).dim(),
        if cached_findings_count > 0 {
            format!(", {} from cache", style(cached_findings_count).dim())
        } else {
            String::new()
        }
    ));

    (voting_stats, cached_findings_count)
}

/// Update incremental cache with new findings
pub(super) fn update_incremental_cache(
    is_incremental_mode: bool,
    incremental_cache: &mut IncrementalCache,
    files: &[PathBuf],
    findings: &[Finding],
    repo_path: &Path,
) {
    if !is_incremental_mode {
        return;
    }

    for file_path in files {
        let rel_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);
        let file_findings: Vec<_> = findings
            .iter()
            .filter(|f| {
                f.affected_files
                    .iter()
                    .any(|af| af == file_path || af == rel_path)
            })
            .cloned()
            .collect();
        incremental_cache.cache_findings(file_path, &file_findings);
    }

    if let Err(e) = incremental_cache.save_cache() {
        tracing::warn!("Failed to save incremental cache: {}", e);
    }
}

/// Apply detector config overrides from project config
pub(super) fn apply_detector_overrides(
    findings: &mut Vec<Finding>,
    project_config: &ProjectConfig,
) {
    if project_config.detectors.is_empty() {
        return;
    }

    let detector_configs = &project_config.detectors;

    // Filter out disabled detectors
    findings.retain(|f| {
        let detector_name = crate::config::normalize_detector_name(&f.detector);
        if let Some(config) = detector_configs.get(&detector_name) {
            if let Some(false) = config.enabled {
                return false;
            }
        }
        true
    });

    // Apply severity overrides
    for finding in findings.iter_mut() {
        let detector_name = crate::config::normalize_detector_name(&finding.detector);
        if let Some(config) = detector_configs.get(&detector_name) {
            if let Some(ref sev) = config.severity {
                finding.severity = parse_severity(sev);
            }
        }
    }
}

/// Parse a severity string
fn parse_severity(s: &str) -> crate::models::Severity {
    match s.to_lowercase().as_str() {
        "critical" => crate::models::Severity::Critical,
        "high" => crate::models::Severity::High,
        "medium" => crate::models::Severity::Medium,
        "low" => crate::models::Severity::Low,
        _ => crate::models::Severity::Info,
    }
}
