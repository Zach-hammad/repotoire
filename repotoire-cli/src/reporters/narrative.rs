use crate::models::Severity;
use crate::reporters::report_context::ReportContext;

/// Format a number with comma separators (e.g. 10000 → "10,000").
fn format_number(n: usize) -> String {
    let s = n.to_string();
    let mut result = String::new();
    let chars: Vec<char> = s.chars().collect();
    let len = chars.len();
    for (i, &c) in chars.iter().enumerate() {
        if i > 0 && (len - i).is_multiple_of(3) {
            result.push(',');
        }
        result.push(c);
    }
    result
}

/// Generate a prose narrative summary from report context.
/// 3-5 sentences, conditionally including insights based on available data.
pub fn generate_narrative(ctx: &ReportContext) -> String {
    let h = &ctx.health;
    let mut sentences: Vec<String> = Vec::new();

    // 1. Always: intro sentence with LOC, file count, grade, score.
    sentences.push(format!(
        "This is a {} LOC project with {} files. It scored {} ({}/100).",
        format_number(h.total_loc),
        format_number(h.total_files),
        h.grade,
        h.overall_score as u32,
    ));

    // 2. If critical findings > 0: surface the top critical finding.
    if h.findings_summary.critical > 0 {
        if let Some(top) = h.findings.iter().find(|f| f.severity == Severity::Critical) {
            let file = top
                .affected_files
                .first()
                .map(|p| p.to_string_lossy().into_owned())
                .unwrap_or_else(|| "unknown".to_string());
            sentences.push(format!(
                "Your most urgent issue: {} in {}.",
                top.title, file
            ));
        }
    }

    // 3. If architecture_score exists and is > 10 points below quality_score,
    //    and graph_data is available.
    if let (Some(arch_score), Some(graph)) = (h.architecture_score, ctx.graph_data.as_ref()) {
        if h.quality_score - arch_score > 10.0 {
            let cycle_count = graph.call_cycles.len();
            let art_point_count = graph.articulation_points.len();
            sentences.push(format!(
                "Architecture is your weakest area — {} circular {} and {} single {} of failure.",
                cycle_count,
                if cycle_count == 1 {
                    "dependency"
                } else {
                    "dependencies"
                },
                art_point_count,
                if art_point_count == 1 {
                    "point"
                } else {
                    "points"
                },
            ));
        }
    }

    // 4. Knowledge risk — expanded bus factor analysis
    if let Some(git) = ctx.git_data.as_ref() {
        let bus_count = git.bus_factor_files.len();
        if h.total_files > 0 && bus_count > 0 {
            let pct = bus_count * 100 / h.total_files;
            let orphaned = git
                .bus_factor_files
                .iter()
                .filter(|(_, bf)| *bf == 0)
                .count();

            if pct > 30 {
                sentences.push(format!(
                    "Knowledge risk is elevated: {}% of files have only 1-2 contributors.",
                    pct
                ));
            } else if pct > 10 {
                sentences.push(format!(
                    "Some knowledge concentration detected: {}% of files have limited contributor diversity.",
                    pct
                ));
            }

            if orphaned > 0 {
                sentences.push(format!(
                    "{} file{} ha{} no active maintainer \u{2014} all contributing authors are inactive.",
                    orphaned,
                    if orphaned == 1 { "" } else { "s" },
                    if orphaned == 1 { "s" } else { "ve" },
                ));
            }
        }
    }

    // 5. If git_data.top_co_change is not empty.
    if let Some(git) = ctx.git_data.as_ref() {
        if let Some((a, b, _)) = git.top_co_change.first() {
            sentences.push(format!(
                "The most coupled files are {} and {}, with high co-change frequency.",
                a, b
            ));
        }
    }

    sentences.join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::*;

    #[test]
    fn test_narrative_basic() {
        let ctx = ReportContext {
            health: HealthReport {
                overall_score: 82.5,
                grade: Grade::B,
                structure_score: 85.0,
                quality_score: 80.0,
                architecture_score: Some(82.0),
                findings: vec![],
                findings_summary: FindingsSummary {
                    critical: 0,
                    high: 0,
                    medium: 0,
                    low: 0,
                    info: 0,
                    total: 0,
                },
                total_files: 100,
                total_functions: 500,
                total_classes: 50,
                total_loc: 10000,
            },
            graph_data: None,
            git_data: None,
            source_snippets: vec![],
            previous_health: None,
            style_profile: None,
        };
        let story = generate_narrative(&ctx);
        assert!(
            story.contains("10,000") || story.contains("10000"),
            "should mention LOC"
        );
        assert!(story.contains("B"), "should mention grade");
    }

    #[test]
    fn test_narrative_with_critical() {
        let findings = vec![Finding {
            id: "f1".into(),
            detector: "test".into(),
            severity: Severity::Critical,
            title: "SQL injection".into(),
            description: String::new(),
            affected_files: vec!["api/db.rs".into()],
            line_start: Some(10),
            ..Default::default()
        }];
        let ctx = ReportContext {
            health: HealthReport {
                overall_score: 60.0,
                grade: Grade::D,
                structure_score: 70.0,
                quality_score: 65.0,
                architecture_score: Some(45.0),
                findings_summary: FindingsSummary::from_findings(&findings),
                findings,
                total_files: 50,
                total_functions: 200,
                total_classes: 20,
                total_loc: 5000,
            },
            graph_data: None,
            git_data: None,
            source_snippets: vec![],
            previous_health: None,
            style_profile: None,
        };
        let story = generate_narrative(&ctx);
        assert!(
            story.contains("urgent") || story.contains("SQL injection"),
            "should mention critical finding"
        );
    }

    #[test]
    fn test_narrative_without_graph_skips_architecture() {
        let ctx = ReportContext {
            health: HealthReport {
                overall_score: 82.5,
                grade: Grade::B,
                structure_score: 85.0,
                quality_score: 80.0,
                architecture_score: Some(82.0),
                findings: vec![],
                findings_summary: FindingsSummary {
                    critical: 0,
                    high: 0,
                    medium: 0,
                    low: 0,
                    info: 0,
                    total: 0,
                },
                total_files: 100,
                total_functions: 500,
                total_classes: 50,
                total_loc: 10000,
            },
            graph_data: None,
            git_data: None,
            source_snippets: vec![],
            previous_health: None,
            style_profile: None,
        };
        let story = generate_narrative(&ctx);
        assert!(
            !story.contains("circular dependencies"),
            "should skip arch insights without graph data"
        );
    }
}
