//! Analyze command implementation.
//!
//! The primary entry point is `run_engine()`, which uses the `AnalysisEngine`
//! to run all 8 analysis stages (collect, parse, graph, git_enrich, calibrate,
//! detect, postprocess, score) and then applies consumer-side presentation
//! (filtering, pagination, formatting, timings, fail-on threshold).
//!
//! Sub-modules provide reusable building blocks for both the engine stages
//! and this consumer layer:
//! - `files` — file discovery and filtering
//! - `graph` — graph construction from parse results
//! - `postprocess` — finding deduplication, suppression, and filtering
//! - `output` — report formatting and caching

mod export;
pub(crate) mod files;
pub(crate) mod graph;
pub(crate) mod output;
pub(crate) mod postprocess;
use output::{cache_results, check_fail_threshold, format_and_output};
use crate::reporters;

use anyhow::Result;
use console::style;
use std::path::{Path, PathBuf};
use std::str::FromStr;
use std::time::Instant;

/// Cache directory for a repository (legacy .repotoire path).
///
/// Used by the `feedback` command to locate cached findings.
pub fn cache_path(repo_path: &Path) -> PathBuf {
    repo_path.join(".repotoire")
}

/// Run analysis via the `AnalysisEngine` pipeline.
///
/// This is the primary analysis entry point. The engine handles all 8 stages
/// (collect, parse, graph, git_enrich, calibrate, detect, postprocess, score).
/// This function applies consumer-side presentation (filtering, pagination,
/// formatting, timings, fail-on threshold).
pub fn run_engine(
    path: &Path,
    config: crate::engine::AnalysisConfig,
    output: crate::engine::OutputOptions,
    telemetry: &crate::telemetry::Telemetry,
) -> Result<()> {
    let start_time = Instant::now();
    let quiet_mode = output.format == "json" || output.format == "sarif";

    // Clear per-run caches (important for long-running server modes)
    crate::parsers::clear_structural_fingerprint_cache();

    // Try to load a previously saved session for incremental analysis;
    // fall back to a fresh engine on any failure (version mismatch, missing files, etc.)
    let canon_for_session = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    let session_dir = crate::cache::paths::cache_dir(&canon_for_session).join("session");
    let mut engine = match crate::engine::AnalysisEngine::load(&session_dir, path) {
        Ok(e) => e,
        Err(_) => crate::engine::AnalysisEngine::new(path)?,
    };
    let result = engine.analyze(&config)?;

    let mode_label = match &result.stats.mode {
        crate::engine::AnalysisMode::Cold => "cold",
        crate::engine::AnalysisMode::Incremental { files_changed } => {
            if !quiet_mode {
                let icon = if output.no_emoji { "" } else { "⚡ " };
                eprintln!(
                    "\n{}Incremental update: {} files changed\n",
                    style(icon).bold(),
                    files_changed,
                );
            }
            "incremental"
        }
        crate::engine::AnalysisMode::Cached => {
            if !quiet_mode {
                let icon = if output.no_emoji { "" } else { "⚡ " };
                eprintln!(
                    "\n{}Using cached results (no changes detected)\n",
                    style(icon).bold(),
                );
            }
            "cached"
        }
    };

    let prepared = prepare_report(&mut engine, &result, result.findings.clone(), path, &output, quiet_mode)?;
    let PreparedReport {
        report,
        all_findings,
        paginated_findings,
        pagination_info,
        repotoire_dir,
        format_enum,
        report_ctx,
        canon_path,
    } = &prepared;

    // Save health report for score delta on NEXT run (after loading previous)
    if let Ok(json) = serde_json::to_string(report) {
        let health_path = crate::cache::paths::health_cache_path(canon_path);
        let _ = std::fs::write(&health_path, &json);
    }

    // Format and output — text/HTML use report_with_context for themed output;
    // JSON/SARIF/Markdown use the old path (they handle pagination differently).
    format_and_display_report(
        *format_enum,
        report_ctx,
        report,
        all_findings,
        &output,
        repotoire_dir,
        *pagination_info,
        paginated_findings.len(),
    )?;

    // Compute language stats from findings (reused by display + telemetry)
    let lang_loc_precomputed = {
        let mut lang_loc: std::collections::HashMap<String, u64> = std::collections::HashMap::new();
        for f in all_findings {
            if let Some(file) = f.affected_files.first() {
                let ext = std::path::Path::new(file)
                    .extension()
                    .and_then(|e| e.to_str())
                    .unwrap_or("");
                if let Some(lang) = crate::parsers::language_for_extension(ext) {
                    *lang_loc.entry(lang.to_lowercase()).or_insert(0) += 1;
                }
            }
        }
        lang_loc
    };
    let precomputed_primary_language = lang_loc_precomputed.iter()
        .max_by_key(|(_, count)| *count)
        .map(|(lang, _)| lang.to_lowercase())
        .unwrap_or_else(|| "unknown".to_string());

    // Display ecosystem context (telemetry users only)
    display_ecosystem_context(
        telemetry,
        quiet_mode,
        &output.format,
        &precomputed_primary_language,
        &result.score,
        result.stats.total_loc,
    );

    // Optional outputs, telemetry, caching, and session persistence
    emit_optional_output(&output, &all_findings, report, &result, &engine, quiet_mode, start_time, mode_label)?;
    send_telemetry(
        telemetry, path, &result.score, &result.stats, &all_findings,
        &lang_loc_precomputed, &precomputed_primary_language, &engine, mode_label, start_time,
    );
    {
        let repotoire_dir = repotoire_dir.clone();
        let health_report = report.clone();
        let all_findings_clone = all_findings.clone();
        std::thread::spawn(move || { let _ = cache_results(&repotoire_dir, &health_report, &all_findings_clone); });
    }
    let _ = engine.save(&session_dir);
    check_fail_threshold(&output.fail_on, report)?;

    Ok(())
}

/// Emit optional outputs: JSON sidecar, score explanation, timing breakdown, summary.
fn emit_optional_output(
    output: &crate::engine::OutputOptions,
    all_findings: &[crate::models::Finding],
    report: &crate::models::HealthReport,
    result: &crate::engine::AnalysisResult,
    engine: &crate::engine::AnalysisEngine,
    quiet_mode: bool,
    start_time: Instant,
    mode_label: &str,
) -> Result<()> {
    // JSON sidecar
    if let Some(ref sidecar_path) = output.json_sidecar {
        let mut sidecar_report = report.clone();
        sidecar_report.findings = all_findings.to_vec();
        sidecar_report.findings_summary =
            crate::models::FindingsSummary::from_findings(all_findings);
        let json_output = crate::reporters::report(&sidecar_report, "json")?;
        std::fs::write(sidecar_path, &json_output)?;
        eprintln!("JSON sidecar written to: {}", sidecar_path.display());
    }

    // Score explanation
    if output.explain_score {
        if let Some(graph) = engine.graph() {
            let scorer = crate::scoring::GraphScorer::new(
                graph, engine.project_config(), engine.repo_path(),
            );
            let explanation = scorer.explain(&result.score.breakdown);
            match output.format.as_str() {
                "json" => {
                    let explain_json = build_explain_json(&explanation, &result.score.breakdown);
                    eprintln!("{}", serde_json::to_string_pretty(&explain_json).unwrap_or_default());
                }
                _ => {
                    println!("\n{}", style("─".repeat(60)).dim());
                    println!("{}", explanation);
                }
            }
        }
    }

    // Timing breakdown
    if output.timings {
        let total = start_time.elapsed();
        println!("\nPhase timings ({}):", mode_label);
        for (name, dur) in &result.stats.timings {
            let pct = dur.as_secs_f64() / total.as_secs_f64() * 100.0;
            println!("  {:<16} {:.3}s  ({:.1}%)", name, dur.as_secs_f64(), pct);
        }
        println!("  {:<16} {:.3}s", "TOTAL", total.as_secs_f64());
    }

    // Summary
    if !quiet_mode {
        let elapsed = start_time.elapsed();
        let icon_done = if output.no_emoji { "" } else { "✨ " };
        eprintln!("\n{}Analysis complete in {:.2}s", style(icon_done).bold(), elapsed.as_secs_f64());
    }

    Ok(())
}

// ============================================================================
// Internal helpers
// ============================================================================

/// Intermediate result from `prepare_report()`, carrying all the data the
/// caller needs for display, caching, and telemetry.
struct PreparedReport {
    report: crate::models::HealthReport,
    all_findings: Vec<crate::models::Finding>,
    paginated_findings: Vec<crate::models::Finding>,
    pagination_info: Option<(usize, usize, usize, usize)>,
    repotoire_dir: PathBuf,
    format_enum: crate::reporters::OutputFormat,
    report_ctx: crate::reporters::report_context::ReportContext,
    canon_path: PathBuf,
}

/// Filter, rank, paginate, and build the report + context from raw engine findings.
///
/// Extracted from `run_engine` to keep that function focused on orchestration.
fn prepare_report(
    engine: &mut crate::engine::AnalysisEngine,
    result: &crate::engine::AnalysisResult,
    mut findings: Vec<crate::models::Finding>,
    path: &Path,
    output: &crate::engine::OutputOptions,
    quiet_mode: bool,
) -> Result<PreparedReport> {
    // Consumer-side filtering: min_confidence (engine postprocess skips this)
    postprocess::filter_by_min_confidence(
        &mut findings,
        output.min_confidence,
        output.show_all,
    );

    // Consumer-side ranking (engine postprocess skips this)
    if output.rank {
        if let Some(graph) = engine.graph() {
            postprocess::rank_findings(&mut findings, graph);
        }
    }

    // Export training data (if requested) — needs graph access
    if let Some(ref export_path) = output.export_training {
        if let Some(graph) = engine.graph() {
            let repo_path = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
            match export::export_training_data(&findings, graph, &repo_path, export_path) {
                Ok(count) => {
                    if !quiet_mode {
                        let icon = if output.no_emoji { "" } else { "\u{1f4ca} " };
                        println!("{}Exported {} training samples to {}", icon, count, export_path.display());
                    }
                }
                Err(e) => {
                    eprintln!("Warning: failed to export training data: {}", e);
                }
            }
        }
    }

    // Apply severity filter and top-N limit
    output::filter_findings(&mut findings, &output.severity_filter, output.top);
    let all_findings = findings.clone();

    // Paginate — structured formats (JSON, SARIF) default to all findings
    let effective_per_page = match output.format.as_str() {
        "json" | "sarif" if output.per_page == 20 => 0,
        _ => output.per_page,
    };
    let (paginated_findings, pagination_info) =
        output::paginate_findings(findings, output.page, effective_per_page);

    // Build HealthReport from engine results
    let findings_summary = crate::models::FindingsSummary::from_findings(&paginated_findings);
    let report = crate::models::HealthReport {
        overall_score: result.score.overall,
        grade: result.score.grade.clone(),
        structure_score: result.score.breakdown.structure.final_score,
        quality_score: result.score.breakdown.quality.final_score,
        architecture_score: Some(result.score.breakdown.architecture.final_score),
        findings: paginated_findings.clone(),
        findings_summary,
        total_files: result.stats.files_analyzed,
        total_functions: result.stats.total_functions,
        total_classes: result.stats.total_classes,
        total_loc: result.stats.total_loc,
    };

    // Ensure cache dir exists
    let canon_path = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    let repotoire_dir = crate::cache::ensure_cache_dir(&canon_path)
        .unwrap_or_else(|_| path.join(".repotoire"));

    // Build rich report context (graph + git + snippets)
    let format_enum = crate::reporters::OutputFormat::from_str(&output.format)?;
    let report_ctx = engine.build_report_context(report.clone(), format_enum)?;

    Ok(PreparedReport {
        report,
        all_findings,
        paginated_findings,
        pagination_info,
        repotoire_dir,
        format_enum,
        report_ctx,
        canon_path,
    })
}

/// Format the report and write to output (file or stdout).
///
/// Text/HTML use `report_with_context` for themed output; JSON/SARIF/Markdown
/// use the legacy `format_and_output` path with separate pagination handling.
fn format_and_display_report(
    format_enum: reporters::OutputFormat,
    report_ctx: &crate::reporters::report_context::ReportContext,
    report: &crate::models::HealthReport,
    all_findings: &[crate::models::Finding],
    output: &crate::engine::OutputOptions,
    repotoire_dir: &Path,
    pagination_info: Option<(usize, usize, usize, usize)>,
    paginated_count: usize,
) -> Result<()> {
    match format_enum {
        reporters::OutputFormat::Text | reporters::OutputFormat::Html => {
            let rendered = reporters::report_with_context(report_ctx, format_enum)?;

            if let Some(out_path) = output.output_path.as_deref() {
                std::fs::write(out_path, &rendered)?;
                let file_icon = if output.no_emoji { "" } else { "\u{1f4c4} " };
                eprintln!(
                    "\n{}Report written to: {}",
                    style(file_icon).bold(),
                    style(out_path.display()).cyan()
                );
            } else {
                println!();
                println!("{}", rendered);
            }

            // Cache results
            cache_results(repotoire_dir, report, all_findings)?;

            // Show pagination info (text only)
            if let Some((current_page, total_pages, per_page, total)) = pagination_info {
                let page_icon = if output.no_emoji { "" } else { "\u{1f4d1} " };
                println!(
                    "\n{}Showing page {} of {} ({} findings per page, {} total)",
                    style(page_icon).bold(),
                    style(current_page).cyan(),
                    style(total_pages).cyan(),
                    style(per_page).dim(),
                    style(total).cyan(),
                );
                if current_page < total_pages {
                    println!(
                        "   Use {} to see more",
                        style(format!("--page {}", current_page + 1)).yellow()
                    );
                }
            }
        }
        _ => {
            // JSON, SARIF, Markdown — use the old format_and_output path
            format_and_output(
                report,
                all_findings,
                &output.format,
                output.output_path.as_deref(),
                repotoire_dir,
                pagination_info,
                paginated_count,
                output.no_emoji,
            )?;
        }
    }
    Ok(())
}

/// Display ecosystem benchmark context when telemetry is active.
///
/// Shows how the repo's score compares to similar projects (by language and size)
/// using percentile data from the benchmark CDN. Shows a telemetry tip for
/// non-telemetry users on text output.
fn display_ecosystem_context(
    telemetry: &crate::telemetry::Telemetry,
    quiet_mode: bool,
    output_format: &str,
    primary_language: &str,
    score: &crate::engine::ScoreResult,
    total_loc: usize,
) {
    if let crate::telemetry::Telemetry::Active(ref _state) = telemetry {
        if !quiet_mode && output_format == "text" {
            let total_kloc = total_loc as f64 / 1000.0;

            if let Some(data) = crate::telemetry::benchmarks::fetch_benchmarks(primary_language, total_kloc) {
                let score_pct = crate::telemetry::benchmarks::interpolate_percentile(
                    score.overall, &data.score
                );
                let pillar_pcts = Some(crate::telemetry::display::PillarPercentiles {
                    structure: crate::telemetry::benchmarks::interpolate_percentile(
                        score.breakdown.structure.final_score, &data.pillar_structure
                    ),
                    quality: crate::telemetry::benchmarks::interpolate_percentile(
                        score.breakdown.quality.final_score, &data.pillar_quality
                    ),
                    architecture: crate::telemetry::benchmarks::interpolate_percentile(
                        score.breakdown.architecture.final_score, &data.pillar_architecture
                    ),
                });
                let ctx = crate::telemetry::display::EcosystemContext {
                    score_percentile: score_pct,
                    comparison_group: format!("{} projects", data.segment.language.as_deref().unwrap_or("all")),
                    sample_size: data.sample_size,
                    pillar_percentiles: pillar_pcts,
                    modularity_percentile: None,
                    coupling_percentile: None,
                    trend: None,
                };
                println!("{}", crate::telemetry::display::format_ecosystem_context(&ctx));
            }
            // Telemetry footer
            println!("  {}", style("telemetry: on (repotoire config telemetry off to disable)").dim());
        }
    } else if !quiet_mode && output_format == "text" {
        // Show tip once (only on text output)
        println!("{}", crate::telemetry::display::format_telemetry_tip());
    }
}

/// Build and send the telemetry event for a completed analysis.
///
/// Collects repo shape, findings breakdown, graph metrics, calibration data,
/// and language stats, then fires a PostHog event asynchronously.
fn send_telemetry(
    telemetry: &crate::telemetry::Telemetry,
    path: &Path,
    score: &crate::engine::ScoreResult,
    stats: &crate::engine::AnalysisStats,
    all_findings: &[crate::models::Finding],
    lang_loc_precomputed: &std::collections::HashMap<String, u64>,
    precomputed_primary_language: &str,
    engine: &crate::engine::AnalysisEngine,
    mode_label: &str,
    start_time: Instant,
) {
    let state = match telemetry {
        crate::telemetry::Telemetry::Active(ref s) => s,
        _ => return,
    };
    let distinct_id = match &state.distinct_id {
        Some(id) => id,
        None => return,
    };

    let canon = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    let repo_id = crate::telemetry::config::compute_repo_id(&canon);
    let repo_shape = crate::telemetry::repo_shape::detect_repo_shape(&canon);

    // Load and update per-repo telemetry state
    let cache_dir = crate::cache::paths::cache_dir(&canon);
    let mut telem_state = crate::telemetry::cache::TelemetryRepoState::load_or_default(&cache_dir);
    telem_state.record_analysis(score.overall);
    let _ = telem_state.save(&cache_dir);

    // Build findings maps
    let mut findings_by_severity = std::collections::HashMap::new();
    let mut findings_by_detector: std::collections::HashMap<String, std::collections::HashMap<String, u64>> = std::collections::HashMap::new();
    let mut findings_by_category = std::collections::HashMap::new();
    for f in all_findings {
        let sev = format!("{:?}", f.severity).to_lowercase();
        *findings_by_severity.entry(sev.clone()).or_insert(0u64) += 1;
        *findings_by_category.entry(f.category.clone().unwrap_or_default()).or_insert(0u64) += 1;
        findings_by_detector
            .entry(f.detector.clone())
            .or_default()
            .entry(sev)
            .and_modify(|c| *c += 1)
            .or_insert(1);
    }

    // Language stats
    let total_lang: u64 = lang_loc_precomputed.values().sum();
    let primary_language_ratio = if total_lang > 0 {
        *lang_loc_precomputed.get(precomputed_primary_language).unwrap_or(&0) as f64 / total_lang as f64
    } else {
        0.0
    };
    let language_count = lang_loc_precomputed.len() as u32;

    // Detect frameworks
    let frameworks: Vec<String> = crate::detectors::framework_detection::detect_frameworks(&canon)
        .into_iter()
        .map(|f| format!("{:?}", f).to_lowercase())
        .collect();

    // Graph primitives
    let (graph_nodes, graph_edges, graph_modularity, graph_scc_count, graph_avg_degree, graph_articulation_points) =
        if let Some(graph) = engine.code_graph() {
            let nodes = graph.node_count() as u64;
            let edges = graph.edge_count() as u64;
            let modularity = graph.graph_modularity();
            let scc_count = graph.call_cycles().len() as u64;
            let avg_degree = if nodes > 0 { edges as f64 / nodes as f64 } else { 0.0 };
            let artic = graph.articulation_points().len() as u64;
            (nodes, edges, modularity, scc_count, avg_degree, artic)
        } else {
            (0, 0, 0.0, 0, 0.0, 0)
        };

    // Calibration data
    let (calibration_total, calibration_at_default, calibration_outliers) =
        if let Some(profile) = engine.style_profile() {
            let total = profile.metrics.len() as u32;
            let at_default = profile.metrics.values()
                .filter(|d| !d.confident)
                .count() as u32;
            let mut deviations: Vec<(String, f64, f64)> = profile.metrics.iter()
                .filter(|(_, d)| d.confident && d.mean > 0.0)
                .map(|(kind, d)| {
                    let deviation = ((d.p95 - d.mean) / d.mean).abs();
                    (format!("{:?}", kind), d.p95, deviation)
                })
                .collect();
            deviations.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));
            let outliers: std::collections::HashMap<String, f64> = deviations.into_iter()
                .take(10)
                .map(|(k, v, _)| (k, v))
                .collect();
            (total, at_default, outliers)
        } else {
            (0, 0, std::collections::HashMap::new())
        };

    // Incremental files changed
    let incremental_files_changed = match &stats.mode {
        crate::engine::AnalysisMode::Incremental { files_changed } => *files_changed as u64,
        _ => 0,
    };

    let event = crate::telemetry::events::AnalysisComplete {
        repo_id,
        nth_analysis: Some(telem_state.nth_analysis),
        score: score.overall,
        grade: score.grade.clone(),
        pillar_structure: score.breakdown.structure.final_score,
        pillar_quality: score.breakdown.quality.final_score,
        pillar_architecture: score.breakdown.architecture.final_score,
        languages: lang_loc_precomputed.clone(),
        primary_language: precomputed_primary_language.to_string(),
        frameworks,
        total_files: stats.files_analyzed as u64,
        total_kloc: stats.total_loc as f64 / 1000.0,
        repo_shape: repo_shape.repo_shape.clone(),
        has_workspace: repo_shape.has_workspace,
        workspace_member_count: repo_shape.workspace_member_count,
        buildable_roots: repo_shape.buildable_roots,
        language_count,
        primary_language_ratio,
        findings_by_severity,
        findings_by_detector,
        findings_by_category,
        graph_nodes,
        graph_edges,
        graph_modularity,
        graph_scc_count,
        graph_avg_degree,
        graph_articulation_points,
        calibration_total,
        calibration_at_default,
        calibration_outliers,
        analysis_duration_ms: start_time.elapsed().as_millis() as u64,
        analysis_mode: mode_label.to_string(),
        incremental_files_changed,
        ci: std::env::var("CI").is_ok(),
        os: std::env::consts::OS.to_string(),
        version: env!("CARGO_PKG_VERSION").to_string(),
    };

    let props = serde_json::to_value(&event).unwrap_or_default();
    crate::telemetry::posthog::capture_queued("analysis_complete", distinct_id, props);
}

/// Build a JSON object for --explain-score output.
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

/// Convert PascalCase or camelCase to kebab-case (e.g. "TodoScanner" -> "todo-scanner").
pub(crate) fn normalize_to_kebab(s: &str) -> String {
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
