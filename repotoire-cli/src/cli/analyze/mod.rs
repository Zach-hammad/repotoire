//! Analyze command implementation
//!
//! Orchestrates the full codebase analysis pipeline:
//! 1. Setup environment and validate inputs       (setup.rs)
//! 2. Collect and filter source files              (files.rs)
//! 3. Parse files and build the code graph         (parse.rs, graph.rs)
//! 4. Run detectors                                (detect.rs)
//! 5. Post-process findings (FP filter, escalate)  (postprocess.rs)
//! 6. Calculate health scores                      (scoring.rs)
//! 7. Output results (text, json, sarif, html, md) (output.rs)

mod detect;
pub(crate) mod files;
mod graph;
mod output;
mod parse;
mod postprocess;
mod scoring;
mod setup;

use detect::{
    apply_voting, finish_git_enrichment, run_detectors, run_detectors_streaming,
    start_git_enrichment,
};
use files::{collect_file_list, collect_files_for_analysis};
use graph::{build_graph, build_graph_chunked, parse_and_build_streaming};
use output::{
    cache_results, check_fail_threshold, format_and_output, load_cached_findings,
    output_cached_results,
};
use parse::{parse_files, parse_files_chunked, parse_files_lite, ParsePhaseResult};
use postprocess::postprocess_findings;
use scoring::{build_health_report, calculate_scores};
use setup::{
    create_bar_style, create_spinner_style, setup_environment, EnvironmentSetup,
    FileCollectionResult, ScoreResult, SUPPORTED_EXTENSIONS,
};

use crate::detectors::IncrementalCache;
use crate::graph::GraphStore;
use crate::models::{Finding, HealthReport};

use anyhow::{Context, Result};
use console::style;
use indicatif::{MultiProgress, ProgressStyle};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

/// Get the cache directory for a repository
pub fn get_cache_path(repo_path: &Path) -> PathBuf {
    repo_path.join(".repotoire")
}

/// Run the analyze command — main entry point.
pub fn run(
    path: &Path,
    format: &str,
    output_path: Option<&Path>,
    severity: Option<String>,
    top: Option<usize>,
    page: usize,
    per_page: usize,
    skip_detector: Vec<String>,
    run_external: bool,
    no_git: bool,
    workers: usize,
    fail_on: Option<String>,
    no_emoji: bool,
    incremental: bool,
    since: Option<String>,
    explain_score: bool,
    verify: bool,
    skip_graph: bool,
    max_files: usize,
    compact: bool,
) -> Result<()> {
    // Normalize skip_detector names to kebab-case so both "TodoScanner" and "todo-scanner" work
    let skip_detector: Vec<String> = skip_detector
        .into_iter()
        .map(|s| normalize_to_kebab(&s))
        .collect();

    // Note: compact mode uses CompactGraphStore via the --compact flag
    if compact {
        tracing::info!("Compact mode enabled (string interning)");
    }
    let start_time = Instant::now();

    // Phase 1: Validate repository and setup environment
    let mut env = setup_environment(
        path,
        format,
        no_emoji,
        run_external,
        no_git,
        workers,
        per_page,
        fail_on,
        incremental,
        since.is_some(),
        skip_graph,
        max_files,
    )?;

    // Fast path: fully cached results (no changes detected)
    if let Some(result) = try_cached_fast_path(
        &env,
        format,
        output_path,
        &severity,
        top,
        page,
        per_page,
        &skip_detector,
        start_time,
        explain_score,
    )? {
        // Show --verify warning even on cached path (#60)
        if verify {
            let has_claude = std::env::var("ANTHROPIC_API_KEY").is_ok();
            let has_ollama = std::process::Command::new("ollama")
                .arg("list")
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false);
            if !has_claude && !has_ollama {
                eprintln!(
                    "\n⚠️  --verify requires an AI backend but none is available.\n\
                     Set ANTHROPIC_API_KEY for Claude, or install Ollama (https://ollama.ai)."
                );
            }
        }
        return Ok(result);
    }

    // Phase 2: Initialize graph and collect files
    let (graph, file_result, parse_result) = initialize_graph(&env, &since, &MultiProgress::new())?;

    if file_result.all_files.is_empty() {
        if !env.quiet_mode {
            let warn_icon = if env.config.no_emoji { "" } else { "⚠️  " };
            println!(
                "\n{}No source files found to analyze.",
                style(warn_icon).yellow()
            );
        }
        return Ok(());
    }

    // Auto-calibrate if no style profile exists
    if env.style_profile.is_none() && !file_result.all_files.is_empty() {
        let parse_pairs: Vec<_> = parse_result
            .parse_results
            .iter()
            .map(|(path, pr)| {
                let loc = std::fs::read_to_string(path)
                    .map(|c| c.lines().count())
                    .unwrap_or(0);
                (pr.clone(), loc)
            })
            .collect();
        let commit_sha = std::process::Command::new("git")
            .args(["rev-parse", "HEAD"])
            .current_dir(&env.repo_path)
            .output()
            .ok()
            .and_then(|o| String::from_utf8(o.stdout).ok())
            .map(|s| s.trim().to_string());
        let profile = crate::calibrate::collect_metrics(
            &parse_pairs,
            file_result.all_files.len(),
            commit_sha,
        );
        let _ = profile.save(&env.repo_path);
        env.style_profile = Some(profile);
        if !env.quiet_mode {
            let icon = if env.config.no_emoji { "" } else { "📐 " };
            println!(
                "{}Auto-calibrated adaptive thresholds ({} functions)",
                icon,
                parse_pairs.len()
            );
        }
    }

    // Phase 3: Run detectors
    let multi = MultiProgress::new();
    let spinner_style = create_spinner_style();

    let mut findings = execute_detection_phase(
        &env,
        &graph,
        &file_result,
        &skip_detector,
        &multi,
        &spinner_style,
    )?;

    // Phase 4: Post-process findings
    postprocess_findings(
        &mut findings,
        &env.project_config,
        &mut env.incremental_cache,
        env.config.is_incremental_mode,
        &file_result.files_to_parse,
        &file_result.all_files,
        env.config.max_files,
        verify,
    );

    // Phase 5: Calculate scores and build report
    let score_result = calculate_scores(&graph, &env.project_config, &findings);

    let report = build_health_report(
        &score_result,
        &mut findings,
        &severity,
        top,
        page,
        per_page,
        file_result.all_files.len(),
        parse_result.total_functions,
        parse_result.total_classes,
    );

    // Cache scores for fast path on next run (use env.incremental_cache, not a new instance)
    env.incremental_cache.cache_score_with_subscores(
        score_result.overall_score,
        &score_result.grade,
        file_result.all_files.len(),
        parse_result.total_functions,
        parse_result.total_classes,
        Some(score_result.structure_score),
        Some(score_result.quality_score),
        Some(score_result.architecture_score),
        score_result.total_loc,
    );

    // Phase 6: Generate output
    generate_reports(
        &report,
        &findings,
        format,
        output_path,
        &env.repotoire_dir,
        report.1,
        env.config.no_emoji,
        explain_score,
        &score_result,
        &graph,
        &env.project_config,
    )?;

    // Cache results for fast path on next run (report.2 = all_findings, since findings was drained by build_health_report)
    let _ = cache_results(&env.repotoire_dir, &report.0, &report.2);

    // Prune stale entries for deleted/renamed files
    env.incremental_cache
        .prune_stale_entries(&file_result.all_files);

    // Cache postprocessed findings for both feedback and incremental fast path (#65)
    let graph_hash = env
        .incremental_cache
        .compute_all_files_hash(&file_result.all_files);
    env.incremental_cache.update_graph_hash(&graph_hash);
    env.incremental_cache
        .cache_graph_findings("__all__", &report.2);
    let _ = env.incremental_cache.save_cache();
    cache_findings(path, &report.2);

    // Final summary
    print_final_summary(env.quiet_mode, env.config.no_emoji, start_time);

    // CI/CD threshold check
    check_fail_threshold(&env.config.fail_on, &report.0)?;

    Ok(())
}

// ============================================================================
// Pipeline phases (private orchestration helpers)
// ============================================================================

/// Try the fast cache path — returns Some(()) if cache hit, None if cache miss.
fn try_cached_fast_path(
    env: &EnvironmentSetup,
    format: &str,
    output_path: Option<&Path>,
    severity: &Option<String>,
    top: Option<usize>,
    page: usize,
    per_page: usize,
    skip_detector: &[String],
    start_time: Instant,
    explain_score: bool,
) -> Result<Option<()>> {
    // Fast-path is only safe for full-repo analysis. With --max-files we must
    // run the normal pipeline so file limiting/filtering is applied correctly.
    if env.config.max_files > 0 {
        return Ok(None);
    }

    let mut cache = IncrementalCache::new(&env.repotoire_dir.join("incremental"));
    let all_files = collect_file_list(&env.repo_path)?;

    if !cache.has_complete_cache(&all_files) {
        return Ok(None);
    }

    let findings = load_cached_findings(&env.repotoire_dir)
        .unwrap_or_else(|| cache.get_all_cached_graph_findings());
    let cached_score = match cache.get_cached_score() {
        Some(s) => s,
        None => return Ok(None),
    };

    if !env.quiet_mode {
        let icon = if env.config.no_emoji { "" } else { "⚡ " };
        println!(
            "\n{}Using fully cached results (no changes detected)\n",
            style(icon).bold()
        );
    }

    output_cached_results(
        env.config.no_emoji,
        env.quiet_mode,
        &env.config.fail_on,
        findings,
        cached_score,
        format,
        output_path,
        start_time,
        explain_score,
        severity,
        top,
        page,
        per_page,
        skip_detector,
        &env.repotoire_dir,
    )?;

    Ok(Some(()))
}

/// Phase 2: Initialize graph database, collect files, and parse.
fn initialize_graph(
    env: &EnvironmentSetup,
    since: &Option<String>,
    multi: &MultiProgress,
) -> Result<(Arc<GraphStore>, FileCollectionResult, ParsePhaseResult)> {
    let spinner_style = create_spinner_style();
    let bar_style = create_bar_style();

    // Collect files
    let mut cache_clone = IncrementalCache::new(&env.repotoire_dir.join("incremental"));
    let mut file_result = collect_files_for_analysis(
        &env.repo_path,
        since,
        env.config.is_incremental_mode,
        &mut cache_clone,
        multi,
        &spinner_style,
    )?;

    // Apply max_files limit
    apply_max_files_limit(
        &mut file_result,
        env.config.max_files,
        env.quiet_mode,
        env.config.no_emoji,
    );

    if file_result.all_files.is_empty() {
        return Ok((
            Arc::new(GraphStore::in_memory()),
            file_result,
            ParsePhaseResult {
                parse_results: vec![],
                total_functions: 0,
                total_classes: 0,
            },
        ));
    }

    if file_result.files_to_parse.is_empty() && env.config.is_incremental_mode && !env.quiet_mode {
        let check_icon = if env.config.no_emoji { "" } else { "✓ " };
        println!(
            "\n{}No files changed since last run. Using cached results.",
            style(check_icon).green()
        );
    }

    // Skip graph mode
    if env.config.skip_graph {
        if !env.quiet_mode {
            println!(
                "{}Skipping graph building (--skip-graph or --lite mode)",
                style("⏭ ").dim()
            );
        }

        let graph = Arc::new(GraphStore::in_memory());
        let _cache_mutex = std::sync::Mutex::new(IncrementalCache::new(
            &env.repotoire_dir.join("incremental"),
        ));
        let parse_result = parse_files_lite(&file_result.files_to_parse, multi, &bar_style)?;

        return Ok((graph, file_result, parse_result));
    }

    // Initialize graph database
    let graph = init_graph_db(env, &file_result, multi)?;

    // Parse files and build graph
    let parse_result = parse_and_build(env, &file_result, &graph, multi, &bar_style)?;

    // Pre-warm file cache (skip for huge repos)
    if file_result.all_files.len() < 20000 {
        // Clear stale data before re-warming (#13)
        crate::cache::global_cache().clear();
        crate::cache::warm_global_cache(&env.repo_path, SUPPORTED_EXTENSIONS);
    }

    Ok((graph, file_result, parse_result))
}

/// Phase 3: Run git enrichment and detectors.
fn execute_detection_phase(
    env: &EnvironmentSetup,
    graph: &Arc<GraphStore>,
    file_result: &FileCollectionResult,
    skip_detector: &[String],
    multi: &MultiProgress,
    spinner_style: &ProgressStyle,
) -> Result<Vec<Finding>> {
    let git_handle = start_git_enrichment(
        env.config.no_git,
        env.quiet_mode,
        &env.repo_path,
        Arc::clone(graph),
        multi,
        spinner_style,
    );

    let use_streaming = file_result.all_files.len() > 5000;

    let mut findings = if use_streaming {
        run_detectors_streaming(
            graph,
            &env.repo_path,
            &env.repotoire_dir,
            &env.project_config,
            skip_detector,
            env.config.run_external,
            multi,
            spinner_style,
            env.quiet_mode,
            env.config.no_emoji,
        )?
    } else {
        let mut detector_cache = IncrementalCache::new(&env.repotoire_dir.join("incremental"));
        run_detectors(
            graph,
            &env.repo_path,
            &env.project_config,
            skip_detector,
            env.config.run_external,
            env.config.workers,
            multi,
            spinner_style,
            env.quiet_mode,
            env.config.no_emoji,
            &mut detector_cache,
            &file_result.all_files,
            env.style_profile.as_ref(),
        )?
    };

    if !use_streaming {
        let (_voting_stats, _cached_count) = apply_voting(
            &mut findings,
            file_result.cached_findings.clone(),
            env.config.is_incremental_mode,
            multi,
            spinner_style,
        );
    }

    finish_git_enrichment(git_handle);

    Ok(findings)
}

/// Phase 6: Generate and output reports.
fn generate_reports(
    report_data: &(
        HealthReport,
        Option<(usize, usize, usize, usize)>,
        Vec<Finding>,
    ),
    findings: &[Finding],
    format: &str,
    output_path: Option<&Path>,
    repotoire_dir: &Path,
    pagination_info: Option<(usize, usize, usize, usize)>,
    no_emoji: bool,
    explain_score: bool,
    score_result: &ScoreResult,
    graph: &Arc<GraphStore>,
    project_config: &crate::config::ProjectConfig,
) -> Result<()> {
    let (report, _, all_findings) = report_data;
    let displayed_findings = findings.len();

    format_and_output(
        report,
        all_findings,
        format,
        output_path,
        repotoire_dir,
        pagination_info,
        displayed_findings,
        no_emoji,
    )?;

    if explain_score {
        let scorer = crate::scoring::GraphScorer::new(graph, project_config);
        let explanation = scorer.explain(&score_result.breakdown);
        match format {
            "json" => {
                let explain_json = build_explain_json(&explanation, &score_result.breakdown);
                eprintln!(
                    "{}",
                    serde_json::to_string_pretty(&explain_json).unwrap_or_default()
                );
            }
            _ => {
                println!("\n{}", style("─".repeat(60)).dim());
                println!("{}", explanation);
            }
        }
    }

    Ok(())
}

// ============================================================================
// Internal helpers
// ============================================================================

/// Apply max_files limit to file collection result.
fn apply_max_files_limit(
    file_result: &mut FileCollectionResult,
    max_files: usize,
    quiet_mode: bool,
    no_emoji: bool,
) {
    if max_files == 0 || file_result.all_files.len() <= max_files {
        return;
    }

    if !quiet_mode {
        let warn_icon = if no_emoji { "" } else { "⚠️  " };
        println!(
            "{}Limiting analysis to {} files (out of {} total) to reduce memory usage",
            style(warn_icon).yellow(),
            style(max_files).cyan(),
            style(file_result.all_files.len()).dim()
        );
    }

    file_result.all_files.truncate(max_files);
    let all_set: std::collections::HashSet<_> = file_result.all_files.iter().collect();
    file_result.files_to_parse.retain(|f| all_set.contains(f));
    if file_result.files_to_parse.len() > max_files {
        file_result.files_to_parse.truncate(max_files);
    }
    file_result
        .cached_findings
        .retain(|f| f.affected_files.iter().any(|p| all_set.contains(p)));
}

/// Initialize graph database (lazy mode for 20k+ files).
fn init_graph_db(
    env: &EnvironmentSetup,
    file_result: &FileCollectionResult,
    multi: &MultiProgress,
) -> Result<Arc<GraphStore>> {
    let db_path = env.repotoire_dir.join("graph_db");
    let use_lazy = file_result.all_files.len() > 20000;

    if !env.quiet_mode {
        let icon_graph = if env.config.no_emoji { "" } else { "🕸️  " };
        let mode_info = if use_lazy { " (lazy mode)" } else { "" };
        let _ = multi; // suppress unused warning
        println!(
            "{}Initializing graph database{}...",
            style(icon_graph).bold(),
            style(mode_info).dim()
        );
    }

    let graph = if use_lazy {
        Arc::new(
            GraphStore::new_lazy(&db_path)
                .with_context(|| "Failed to initialize graph database")?,
        )
    } else {
        Arc::new(GraphStore::new(&db_path).with_context(|| "Failed to initialize graph database")?)
    };

    Ok(graph)
}

/// Parse files and build graph, choosing strategy based on repo size.
fn parse_and_build(
    env: &EnvironmentSetup,
    file_result: &FileCollectionResult,
    graph: &Arc<GraphStore>,
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
) -> Result<ParsePhaseResult> {
    let use_streaming = file_result.files_to_parse.len() > 2000;

    if use_streaming {
        if !env.quiet_mode {
            let stream_icon = if env.config.no_emoji { "" } else { "🌊 " };
            println!(
                "{}Using streaming mode for {} files (memory efficient)",
                style(stream_icon).bold(),
                style(file_result.files_to_parse.len()).cyan()
            );
        }

        let (total_functions, total_classes) = parse_and_build_streaming(
            &file_result.files_to_parse,
            &env.repo_path,
            Arc::clone(graph),
            multi,
            bar_style,
        )?;

        Ok(ParsePhaseResult {
            parse_results: vec![],
            total_functions,
            total_classes,
        })
    } else {
        let cache_mutex = std::sync::Mutex::new(IncrementalCache::new(
            &env.repotoire_dir.join("incremental"),
        ));

        let result = if file_result.files_to_parse.len() > 10000 {
            parse_files_chunked(
                &file_result.files_to_parse,
                multi,
                bar_style,
                env.config.is_incremental_mode,
                &cache_mutex,
                5000,
            )?
        } else {
            parse_files(
                &file_result.files_to_parse,
                multi,
                bar_style,
                env.config.is_incremental_mode,
                &cache_mutex,
            )?
        };

        if let Ok(mut cache) = cache_mutex.into_inner() {
            let _ = cache.save_cache();
        }

        if result.parse_results.len() > 10000 {
            build_graph_chunked(
                graph,
                &env.repo_path,
                &result.parse_results,
                multi,
                bar_style,
                5000,
            )?;
        } else {
            build_graph(
                graph,
                &env.repo_path,
                &result.parse_results,
                multi,
                bar_style,
            )?;
        }

        Ok(result)
    }
}

/// Cache scores for fast path on next run.
fn build_explain_json(explanation: &str, bd: &crate::scoring::ScoreBreakdown) -> serde_json::Value {
    fn pillar_json(p: &crate::scoring::PillarBreakdown) -> serde_json::Value {
        serde_json::json!({
            "score": p.final_score,
            "base": p.base_score,
            "penalty": p.penalty_points,
            "findings": p.finding_count,
        })
    }
    serde_json::json!({
        "explanation": explanation,
        "breakdown": {
            "overall_score": bd.overall_score,
            "grade": &bd.grade,
            "kloc": bd.graph_metrics.total_loc as f64 / 1000.0,
            "structure": pillar_json(&bd.structure),
            "quality": pillar_json(&bd.quality),
            "architecture": pillar_json(&bd.architecture),
        }
    })
}

/// Cache findings for the feedback command.
fn cache_findings(path: &Path, findings: &[Finding]) {
    let cache_path = get_cache_path(path);
    if let Err(e) = std::fs::create_dir_all(&cache_path) {
        tracing::warn!(
            "Failed to create cache directory {}: {}",
            cache_path.display(),
            e
        );
    }
    let findings_cache = cache_path.join("findings.json");
    if let Ok(json) = serde_json::to_string(findings) {
        if let Err(e) = std::fs::write(&findings_cache, &json) {
            tracing::warn!(
                "Failed to write findings cache {}: {}",
                findings_cache.display(),
                e
            );
        }
    }
}

/// Print final summary message.
fn print_final_summary(quiet_mode: bool, no_emoji: bool, start_time: Instant) {
    if !quiet_mode {
        let elapsed = start_time.elapsed();
        let icon_done = if no_emoji { "" } else { "✨ " };
        println!(
            "\n{}Analysis complete in {:.2}s",
            style(icon_done).bold(),
            elapsed.as_secs_f64()
        );
    }
}

/// Convert PascalCase or camelCase to kebab-case (e.g. "TodoScanner" → "todo-scanner").
fn normalize_to_kebab(s: &str) -> String {
    if s.contains('-') {
        return s.to_lowercase();
    }
    let mut result = String::with_capacity(s.len() + 4);
    for (i, ch) in s.chars().enumerate() {
        if ch.is_uppercase() && i > 0 {
            result.push('-');
        }
        result.push(ch.to_ascii_lowercase());
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_pascal_case() {
        assert_eq!(normalize_to_kebab("TodoScanner"), "todo-scanner");
        assert_eq!(normalize_to_kebab("DeadCodeDetector"), "dead-code-detector");
        assert_eq!(
            normalize_to_kebab("AIComplexitySpike"),
            "a-i-complexity-spike"
        );
    }

    #[test]
    fn test_normalize_already_kebab() {
        assert_eq!(normalize_to_kebab("todo-scanner"), "todo-scanner");
        assert_eq!(normalize_to_kebab("dead-code"), "dead-code");
    }

    #[test]
    fn test_normalize_lowercase() {
        assert_eq!(normalize_to_kebab("simple"), "simple");
    }

    #[test]
    fn test_normalize_mixed_case_kebab() {
        assert_eq!(normalize_to_kebab("Todo-Scanner"), "todo-scanner");
    }
}
