//! Score calculation and health report building for the analyze command.

use super::setup::ScoreResult;
use crate::config::ProjectConfig;
use crate::graph::GraphStore;
use crate::models::{Finding, FindingsSummary, HealthReport};
use crate::scoring::GraphScorer;

use std::sync::Arc;

use super::output::{filter_findings, paginate_findings};

/// Phase 5: Calculate health scores using graph-aware scorer
pub(super) fn calculate_scores(
    graph: &Arc<GraphStore>,
    project_config: &ProjectConfig,
    findings: &[Finding],
    repo_path: &std::path::Path,
) -> ScoreResult {
    let scorer = GraphScorer::new(graph, project_config, repo_path);
    let breakdown = scorer.calculate(findings);

    // Log graph metrics
    let metrics = &breakdown.graph_metrics;
    tracing::info!(
        "Graph metrics: {} modules, {:.1}% coupling, {:.1}% cohesion, {} cycles, {:.1}% simple fns",
        metrics.module_count,
        metrics.avg_coupling * 100.0,
        metrics.avg_cohesion * 100.0,
        metrics.cycle_count,
        metrics.simple_function_ratio * 100.0
    );

    let total_loc = breakdown.graph_metrics.total_loc;
    ScoreResult {
        overall_score: breakdown.overall_score,
        structure_score: breakdown.structure.final_score,
        quality_score: breakdown.quality.final_score,
        architecture_score: breakdown.architecture.final_score,
        grade: breakdown.grade.clone(),
        breakdown,
        total_loc,
    }
}

/// Build the health report with filtered and paginated findings
pub(super) fn build_health_report(
    score_result: &ScoreResult,
    findings: &mut Vec<Finding>,
    severity: &Option<String>,
    top: Option<usize>,
    page: usize,
    per_page: usize,
    total_files: usize,
    total_functions: usize,
    total_classes: usize,
) -> (
    HealthReport,
    Option<(usize, usize, usize, usize)>,
    Vec<Finding>,
) {
    filter_findings(findings, severity, top);
    let all_findings = findings.clone();
    let display_summary = FindingsSummary::from_findings(findings);

    let (paginated_findings, pagination_info) =
        paginate_findings(std::mem::take(findings), page, per_page);

    let report = HealthReport {
        overall_score: score_result.overall_score,
        grade: score_result.grade.clone(),
        structure_score: score_result.structure_score,
        quality_score: score_result.quality_score,
        architecture_score: Some(score_result.architecture_score),
        findings: paginated_findings,
        findings_summary: display_summary,
        total_files,
        total_functions,
        total_classes,
        total_loc: score_result.total_loc,
    };

    (report, pagination_info, all_findings)
}
