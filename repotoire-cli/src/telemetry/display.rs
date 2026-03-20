//! Benchmark display formatting for ecosystem context output.

use serde::Serialize;

#[derive(Serialize)]
pub struct EcosystemContext {
    pub score_percentile: f64,
    pub comparison_group: String,
    pub sample_size: u64,
    pub pillar_percentiles: Option<PillarPercentiles>,
    pub modularity_percentile: Option<f64>,
    pub coupling_percentile: Option<f64>,
    pub trend: Option<TrendInfo>,
}

#[derive(Serialize)]
pub struct PillarPercentiles {
    pub structure: f64,
    pub quality: f64,
    pub architecture: f64,
}

#[derive(Serialize)]
pub struct TrendInfo {
    pub score_delta: f64,
    pub ecosystem_avg_improvement: f64,
}

/// Format a number with comma separators (e.g. 1247 → "1,247").
fn format_number(n: u64) -> String {
    let s = n.to_string();
    let mut result = String::with_capacity(s.len() + s.len() / 3);
    for (i, ch) in s.chars().rev().enumerate() {
        if i > 0 && i % 3 == 0 {
            result.push(',');
        }
        result.push(ch);
    }
    result.chars().rev().collect()
}

/// Convert a percentile value to a "top N%" string.
/// A percentile of 70.0 means "better than 70%" → top 30%.
fn percentile_to_top(p: f64) -> String {
    let top = 100.0 - p;
    let rounded = top.round() as u64;
    format!("top {}%", rounded)
}

/// Render the compact "Ecosystem Context" box shown after analysis.
pub fn format_ecosystem_context(ctx: &EcosystemContext) -> String {
    let border = "─".repeat(51);
    let header = format!("── Ecosystem Context ──{}", "─".repeat(29));
    let footer = border.clone();

    let score_line = format!(
        "  Score:         better than {}% of {}",
        ctx.score_percentile.round() as u64,
        ctx.comparison_group
    );

    let mut lines = vec![header, score_line];

    if let Some(ref pillars) = ctx.pillar_percentiles {
        let pillar_line = format!(
            "  Structure:     {}  |  Quality: {}  |  Architecture: {}",
            percentile_to_top(pillars.structure),
            percentile_to_top(pillars.quality),
            percentile_to_top(pillars.architecture),
        );
        lines.push(pillar_line);
    }

    if let Some(mod_p) = ctx.modularity_percentile {
        let mod_line = format!(
            "  Modularity:    {} for projects your size",
            percentile_to_top(mod_p)
        );
        lines.push(mod_line);
    }

    if let Some(coup_p) = ctx.coupling_percentile {
        let coup_line = format!(
            "  Coupling:      lower than {}% — well-decoupled",
            coup_p.round() as u64
        );
        lines.push(coup_line);
    }

    if let Some(ref trend) = ctx.trend {
        let sign = if trend.score_delta >= 0.0 { "+" } else { "" };
        let trend_line = format!(
            "  Trend:         {}{:.1} since last analysis (avg across ecosystem: {:.1})",
            sign, trend.score_delta, trend.ecosystem_avg_improvement
        );
        lines.push(trend_line);
    }

    lines.push(String::new());
    lines.push(format!(
        "  Compared against {} {} (last 90 days)",
        format_number(ctx.sample_size),
        ctx.comparison_group
    ));
    lines.push(footer);

    lines.join("\n")
}

/// Shown when fewer than 50 repos exist in the segment.
pub fn format_insufficient_data(segment_name: &str) -> String {
    let header = format!("── Ecosystem Context ──{}", "─".repeat(29));
    let footer = "─".repeat(51);
    format!(
        "{}\n  Not enough data for {} yet.\n  Your analyses help build these benchmarks.\n{}",
        header, segment_name, footer
    )
}

/// Shown when telemetry is off.
pub fn format_telemetry_tip() -> String {
    "  Tip: Enable telemetry to see how your project compares\n       to the ecosystem. Run: repotoire config telemetry on"
        .to_string()
}

/// Footer line shown when telemetry is enabled.
pub fn format_telemetry_footer() -> String {
    "  telemetry: on (repotoire config telemetry off to disable)".to_string()
}

/// Full `repotoire benchmark` command output (longer form).
///
/// Renders ecosystem context plus detailed sections for graph health,
/// top findings, detector accuracy, and trend history.
pub fn format_benchmark_full(
    ctx: &EcosystemContext,
    data: &crate::telemetry::benchmarks::BenchmarkData,
    score_history: Option<&[crate::telemetry::cache::ScoreEntry]>,
) -> String {
    let mut sections = vec![format_ecosystem_context(ctx)];
    let border = "───────────────────────────────────────────────────";

    // Graph Health
    {
        let mod_pct = crate::telemetry::benchmarks::interpolate_percentile(
            ctx.modularity_percentile.unwrap_or(0.0),
            &data.graph_modularity,
        );
        let deg_pct = crate::telemetry::benchmarks::interpolate_percentile(
            ctx.coupling_percentile.unwrap_or(0.0),
            &data.graph_avg_degree,
        );
        let scc_pct = data.graph_scc_count.pct_zero;
        let mut lines = vec!["── Graph Health ───────────────────────────────────".to_string()];
        lines.push(format!("  Modularity:      {}", percentile_to_top(mod_pct)));
        lines.push(format!("  Avg degree:      {}", percentile_to_top(deg_pct)));
        lines.push(format!("  Cycle-free:      {:.0}% of projects have zero SCCs", scc_pct * 100.0));
        lines.push(border.to_string());
        sections.push(lines.join("\n"));
    }

    // Top Findings
    {
        let mut lines = vec!["── Top Findings ───────────────────────────────────".to_string()];
        if data.top_detectors.is_empty() {
            lines.push("  (no detector data available)".to_string());
        } else {
            for det in data.top_detectors.iter().take(10) {
                lines.push(format!("  {:<32} {:.0}% of repos", det.name, det.pct_repos_with_findings * 100.0));
            }
        }
        lines.push(border.to_string());
        sections.push(lines.join("\n"));
    }

    // Detector Accuracy
    {
        let mut lines = vec!["── Detector Accuracy ──────────────────────────────".to_string()];
        if data.detector_accuracy.is_empty() {
            lines.push("  (no accuracy data available)".to_string());
        } else {
            for acc in data.detector_accuracy.iter().take(10) {
                lines.push(format!(
                    "  {:<32} TP rate: {:.0}%  ({} feedback)",
                    acc.name, acc.true_positive_rate * 100.0, acc.feedback_count
                ));
            }
        }
        lines.push(border.to_string());
        sections.push(lines.join("\n"));
    }

    // Trend History
    {
        let mut lines = vec!["── Trend History ──────────────────────────────────".to_string()];
        match score_history {
            Some(entries) if !entries.is_empty() => {
                for entry in entries.iter().rev().take(10) {
                    lines.push(format!(
                        "  {}   score: {:.1}",
                        entry.timestamp.format("%Y-%m-%d"),
                        entry.score
                    ));
                }
                lines.push(format!(
                    "  Ecosystem avg improvement/analysis: {:.2}",
                    data.avg_improvement_per_analysis
                ));
            }
            _ => {
                lines.push("  (no trend history yet — run more analyses)".to_string());
            }
        }
        lines.push(border.to_string());
        sections.push(lines.join("\n"));
    }

    sections.push(format_telemetry_footer());

    sections.join("\n\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_format_number() {
        assert_eq!(format_number(0), "0");
        assert_eq!(format_number(999), "999");
        assert_eq!(format_number(1000), "1,000");
        assert_eq!(format_number(1247), "1,247");
        assert_eq!(format_number(1_000_000), "1,000,000");
    }

    #[test]
    fn test_percentile_to_top() {
        assert_eq!(percentile_to_top(70.0), "top 30%");
        assert_eq!(percentile_to_top(85.0), "top 15%");
        assert_eq!(percentile_to_top(80.0), "top 20%");
    }

    #[test]
    fn test_format_ecosystem_context_basic() {
        let ctx = EcosystemContext {
            score_percentile: 68.0,
            comparison_group: "Rust projects".into(),
            sample_size: 1247,
            pillar_percentiles: Some(PillarPercentiles {
                structure: 70.0,
                quality: 45.0,
                architecture: 80.0,
            }),
            modularity_percentile: Some(85.0),
            coupling_percentile: Some(60.0),
            trend: None,
        };
        let output = format_ecosystem_context(&ctx);
        assert!(output.contains("better than 68%"));
        assert!(output.contains("Rust projects"));
        assert!(output.contains("1,247"));
    }

    #[test]
    fn test_format_ecosystem_context_pillars() {
        let ctx = EcosystemContext {
            score_percentile: 68.0,
            comparison_group: "Rust projects".into(),
            sample_size: 500,
            pillar_percentiles: Some(PillarPercentiles {
                structure: 70.0,
                quality: 45.0,
                architecture: 80.0,
            }),
            modularity_percentile: None,
            coupling_percentile: None,
            trend: None,
        };
        let output = format_ecosystem_context(&ctx);
        assert!(output.contains("top 30%")); // structure: 100 - 70
        assert!(output.contains("top 55%")); // quality: 100 - 45
        assert!(output.contains("top 20%")); // architecture: 100 - 80
    }

    #[test]
    fn test_format_insufficient_data() {
        let output = format_insufficient_data("Rust workspace");
        assert!(output.contains("Not enough data"));
        assert!(output.contains("Rust workspace"));
    }

    #[test]
    fn test_format_telemetry_tip() {
        let output = format_telemetry_tip();
        assert!(output.contains("repotoire config telemetry on"));
    }

    #[test]
    fn test_format_telemetry_footer() {
        let output = format_telemetry_footer();
        assert!(output.contains("telemetry: on"));
        assert!(output.contains("repotoire config telemetry off"));
    }

    #[test]
    fn test_format_with_trend() {
        let ctx = EcosystemContext {
            score_percentile: 68.0,
            comparison_group: "Rust projects".into(),
            sample_size: 500,
            pillar_percentiles: None,
            modularity_percentile: None,
            coupling_percentile: None,
            trend: Some(TrendInfo {
                score_delta: 4.2,
                ecosystem_avg_improvement: 1.8,
            }),
        };
        let output = format_ecosystem_context(&ctx);
        assert!(output.contains("+4.2"));
        assert!(output.contains("1.8"));
    }

    #[test]
    fn test_format_with_negative_trend() {
        let ctx = EcosystemContext {
            score_percentile: 40.0,
            comparison_group: "Go projects".into(),
            sample_size: 200,
            pillar_percentiles: None,
            modularity_percentile: None,
            coupling_percentile: None,
            trend: Some(TrendInfo {
                score_delta: -2.5,
                ecosystem_avg_improvement: 0.5,
            }),
        };
        let output = format_ecosystem_context(&ctx);
        assert!(output.contains("-2.5"));
        // negative delta should not have a leading '+'
        assert!(!output.contains("+-2.5"));
    }

    #[test]
    fn test_format_benchmark_full_contains_ecosystem() {
        use crate::telemetry::benchmarks::*;
        let ctx = EcosystemContext {
            score_percentile: 75.0,
            comparison_group: "TypeScript projects".into(),
            sample_size: 3000,
            pillar_percentiles: None,
            modularity_percentile: None,
            coupling_percentile: None,
            trend: None,
        };
        let data = BenchmarkData {
            schema_version: 1,
            segment: BenchmarkSegment { language: Some("TypeScript".into()), kloc_bucket: None },
            sample_size: 3000,
            updated_at: "2026-01-01".into(),
            score: PercentileDistribution { p25: 40.0, p50: 55.0, p75: 70.0, p90: 85.0 },
            pillar_structure: PercentileDistribution { p25: 40.0, p50: 55.0, p75: 70.0, p90: 85.0 },
            pillar_quality: PercentileDistribution { p25: 40.0, p50: 55.0, p75: 70.0, p90: 85.0 },
            pillar_architecture: PercentileDistribution { p25: 40.0, p50: 55.0, p75: 70.0, p90: 85.0 },
            graph_modularity: PercentileDistribution { p25: 0.2, p50: 0.4, p75: 0.6, p90: 0.8 },
            graph_avg_degree: PercentileDistribution { p25: 1.0, p50: 2.0, p75: 3.0, p90: 5.0 },
            graph_scc_count: SccDistribution { pct_zero: 0.65, p50: 1.0, p75: 3.0, p90: 7.0 },
            grade_distribution: std::collections::HashMap::new(),
            top_detectors: vec![
                DetectorStat { name: "god-class".into(), pct_repos_with_findings: 0.42 },
            ],
            detector_accuracy: vec![
                DetectorAccuracy { name: "god-class".into(), true_positive_rate: 0.88, feedback_count: 150 },
            ],
            avg_improvement_per_analysis: 1.2,
        };
        let output = format_benchmark_full(&ctx, &data, None);
        assert!(output.contains("Ecosystem Context"));
        assert!(output.contains("better than 75%"));
        assert!(output.contains("3,000"));
        assert!(output.contains("telemetry: on"));
        assert!(output.contains("Graph Health"));
        assert!(output.contains("god-class"));
    }
}
