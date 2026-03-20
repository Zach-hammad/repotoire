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
) -> Result<()> {
    let start_time = Instant::now();
    let quiet_mode = output.format == "json" || output.format == "sarif";

    // Clear per-run caches (important for MCP long-running server)
    crate::parsers::clear_structural_fingerprint_cache();

    // Create engine and run analysis
    let mut engine = crate::engine::AnalysisEngine::new(path)?;
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

    let mut findings = result.findings;

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
                        let icon = if output.no_emoji { "" } else { "📊 " };
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
        "json" | "sarif" if output.per_page == 20 => 0, // override default pagination
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

    // Ensure cache dir exists (needed for health save and output caching)
    let canon_path = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    let repotoire_dir = crate::cache::ensure_cache_dir(&canon_path)
        .unwrap_or_else(|_| path.join(".repotoire"));

    // Build rich report context (graph + git + snippets)
    // Must happen BEFORE saving health report so load_previous_health reads the OLD data
    let format_enum = crate::reporters::OutputFormat::from_str(&output.format)?;
    let report_ctx = engine.build_report_context(report.clone(), format_enum)?;

    // Save health report for score delta on NEXT run (after loading previous)
    if let Ok(json) = serde_json::to_string(&report) {
        let health_path = crate::cache::paths::health_cache_path(&canon_path);
        let _ = std::fs::write(&health_path, &json);
    }

    // Format and output — text/HTML use report_with_context for themed output;
    // JSON/SARIF/Markdown use the old path (they handle pagination differently).
    match format_enum {
        reporters::OutputFormat::Text | reporters::OutputFormat::Html => {
            let rendered = reporters::report_with_context(&report_ctx, format_enum)?;

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
            cache_results(&repotoire_dir, &report, &all_findings)?;

            // Show pagination info (text only)
            let quiet_mode = false;
            if let Some((current_page, total_pages, per_page, total)) =
                pagination_info.filter(|_| !quiet_mode)
            {
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
                &report,
                &all_findings,
                &output.format,
                output.output_path.as_deref(),
                &repotoire_dir,
                pagination_info,
                paginated_findings.len(),
                output.no_emoji,
            )?;
        }
    }

    // Write JSON sidecar if requested (single analysis run, two output files)
    if let Some(ref sidecar_path) = output.json_sidecar {
        let mut sidecar_report = report.clone();
        sidecar_report.findings = all_findings.clone();
        sidecar_report.findings_summary =
            crate::models::FindingsSummary::from_findings(&all_findings);
        let json_output = crate::reporters::report(&sidecar_report, "json")?;
        std::fs::write(sidecar_path, &json_output)?;
        eprintln!("JSON sidecar written to: {}", sidecar_path.display());
    }

    // Explain score (if requested)
    if output.explain_score {
        if let Some(graph) = engine.graph() {
            let scorer = crate::scoring::GraphScorer::new(
                graph,
                engine.project_config(),
                engine.repo_path(),
            );
            let explanation = scorer.explain(&result.score.breakdown);
            match output.format.as_str() {
                "json" => {
                    let explain_json = build_explain_json(&explanation, &result.score.breakdown);
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
    }

    // Print timing breakdown (if requested)
    if output.timings {
        let total = start_time.elapsed();
        println!("\nPhase timings ({}):", mode_label);
        for (name, dur) in &result.stats.timings {
            let pct = dur.as_secs_f64() / total.as_secs_f64() * 100.0;
            println!("  {:<16} {:.3}s  ({:.1}%)", name, dur.as_secs_f64(), pct);
        }
        println!("  {:<16} {:.3}s", "TOTAL", total.as_secs_f64());
    }

    // Final summary
    if !quiet_mode {
        let elapsed = start_time.elapsed();
        let icon_done = if output.no_emoji { "" } else { "✨ " };
        eprintln!(
            "\n{}Analysis complete in {:.2}s",
            style(icon_done).bold(),
            elapsed.as_secs_f64()
        );
    }

    // Cache results (fire-and-forget background)
    {
        let repotoire_dir = repotoire_dir.clone();
        let health_report = report.clone();
        let all_findings_clone = all_findings.clone();
        std::thread::spawn(move || {
            let _ = cache_results(&repotoire_dir, &health_report, &all_findings_clone);
        });
    }

    // CI/CD threshold check
    check_fail_threshold(&output.fail_on, &report)?;

    Ok(())
}

// ============================================================================
// Internal helpers
// ============================================================================

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
