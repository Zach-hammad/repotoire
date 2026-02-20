//! Detection functions for the analyze command
//!
//! This module contains all detector-related logic:
//! - Running detectors on the code graph
//! - Streaming detection for huge repos
//! - Git history enrichment
//! - Voting and consolidation
//! - Incremental caching

use crate::config::ProjectConfig;
use crate::detectors::{
    ConfidenceMethod, DetectorEngine, IncrementalCache, SeverityResolution, VotingEngine,
    VotingStats, VotingStrategy,
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

/// Run all detectors on the graph
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
) -> Result<Vec<Finding>> {
    // Check if we can use cached detector results
    if cache.can_use_cached_detectors(all_files) {
        let cached_findings = cache.get_all_cached_graph_findings();
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
    let mut engine = DetectorEngine::new(workers).with_hmm_cache(hmm_cache_path);
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

    let findings = engine.run(graph)?;

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

/// Run detectors in streaming mode for large repos
///
/// Writes findings to disk as they're generated to prevent OOM.
/// Only loads high-severity findings for scoring.
pub(super) fn run_detectors_streaming(
    graph: &Arc<GraphStore>,
    repo_path: &Path,
    cache_dir: &Path,
    project_config: &ProjectConfig,
    skip_detector: &[String],
    run_external: bool,
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
    quiet_mode: bool,
    no_emoji: bool,
) -> Result<Vec<Finding>> {
    use crate::detectors::streaming_engine::{run_streaming_detection, StreamingDetectorEngine};

    if !quiet_mode {
        let stream_icon2 = if no_emoji { "" } else { "🌊 " };
        println!(
            "\n{}Running detectors (streaming mode for large repo)...",
            style(stream_icon2).bold()
        );
    }

    let detector_bar = multi.add(ProgressBar::new_spinner());
    detector_bar.set_style(spinner_style.clone());
    detector_bar.set_message("Streaming detection...");
    detector_bar.enable_steady_tick(std::time::Duration::from_millis(100));

    let (stats, findings_path) = run_streaming_detection(
        graph,
        repo_path,
        cache_dir,
        project_config,
        skip_detector,
        run_external,
        Some(&|name, done, total| {
            detector_bar.set_message(format!("[{}/{}] {}...", done, total, name));
        }),
    )?;

    detector_bar.finish_with_message(format!(
        "{}Streaming detection: {} detectors, {}",
        style("✓ ").green(),
        style(stats.detectors_run).cyan(),
        style(stats.summary()).cyan(),
    ));

    // For scoring, load high-severity findings only (keeps memory bounded)
    let engine = StreamingDetectorEngine::new(findings_path.clone());
    let high_findings = engine.read_high_severity()?;

    if !quiet_mode {
        println!(
            "  {} Loaded {} high+ findings for scoring (full results in {})",
            style("→").dim(),
            high_findings.len(),
            findings_path.display()
        );
    }

    Ok(high_findings)
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
) {
    if !is_incremental_mode {
        return;
    }

    for file_path in files {
        let file_findings: Vec<_> = findings
            .iter()
            .filter(|f| f.affected_files.iter().any(|af| af == file_path))
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
