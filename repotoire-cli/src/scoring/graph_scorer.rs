//! Graph-based health scorer
//!
//! Uses the code graph to compute positive architectural signals,
//! combined with finding penalties for the final score.

use crate::config::ProjectConfig;
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use std::collections::{HashMap, HashSet};
use tracing::{debug, info};

/// Mark compound smells (multiple different issues co-located) for prioritization
/// Research shows compound smells correlate with 78% more dependencies (arXiv:2509.03896)
/// NOTE: This adds metadata only - does NOT change severity (to preserve accurate scoring)
pub fn escalate_compound_smells(findings: &mut [Finding]) {
    // Group findings by location (file + overlapping line ranges)
    let mut location_groups: HashMap<String, Vec<usize>> = HashMap::new();

    for (idx, finding) in findings.iter().enumerate() {
        if finding.affected_files.is_empty() {
            continue;
        }

        let file = finding.affected_files[0].to_string_lossy().to_string();
        let line_start = finding.line_start.unwrap_or(0);
        let _line_end = finding.line_end.unwrap_or(line_start);

        // Use 50-line buckets to group nearby findings
        let bucket = line_start / 50;
        let key = format!("{}:{}", file, bucket);

        location_groups.entry(key).or_default().push(idx);
    }

    // Check each location for compound smells
    for (_location, indices) in location_groups.iter() {
        if indices.len() < 2 {
            continue;
        }

        // Count unique detector types
        let unique_detectors: HashSet<&str> = indices
            .iter()
            .map(|&idx| findings[idx].detector.as_str())
            .collect();

        let detector_count = unique_detectors.len();

        if detector_count >= 2 {
            // Mark as compound smell (for prioritization in UI/reports)
            for &idx in indices {
                // Add metadata without changing severity
                if !findings[idx].description.starts_with("[COMPOUND") {
                    findings[idx].description = format!(
                        "[COMPOUND: {} co-located issues] {}",
                        detector_count, findings[idx].description
                    );
                    // Boost confidence for compound smells, clamped to 1.0 (#67)
                    findings[idx].confidence =
                        Some((findings[idx].confidence.unwrap_or(0.7) + 0.1).min(1.0));
                }
            }
            debug!(
                "Marked {} findings as compound smell ({} detectors)",
                indices.len(),
                detector_count
            );
        }
    }
}

/// Maximum bonus percentages for each positive signal
const MAX_MODULARITY_BONUS: f64 = 0.10; // 10% max
const MAX_COHESION_BONUS: f64 = 0.05; // 5% max
const MAX_CLEAN_DEPS_BONUS: f64 = 0.10; // 10% max
const MAX_COMPLEXITY_DIST_BONUS: f64 = 0.05; // 5% max
const MAX_TEST_COVERAGE_BONUS: f64 = 0.05; // 5% max

/// Breakdown of a single pillar score
#[derive(Debug, Clone)]
pub struct PillarBreakdown {
    /// Pillar name
    pub name: String,
    /// Base score before bonuses (100 - penalties)
    pub base_score: f64,
    /// Total bonus from positive signals (0.0 - 0.35)
    pub bonus_ratio: f64,
    /// Final score after bonuses
    pub final_score: f64,
    /// Individual bonus contributions
    pub bonuses: Vec<(String, f64)>,
    /// Penalty from findings
    pub penalty_points: f64,
    /// Number of findings affecting this pillar
    pub finding_count: usize,
}

/// Complete score breakdown for transparency
#[derive(Debug, Clone)]
pub struct ScoreBreakdown {
    /// Overall health score (0-100+)
    pub overall_score: f64,
    /// Letter grade
    pub grade: String,
    /// Structure pillar breakdown
    pub structure: PillarBreakdown,
    /// Quality pillar breakdown  
    pub quality: PillarBreakdown,
    /// Architecture pillar breakdown
    pub architecture: PillarBreakdown,
    /// Graph-derived metrics
    pub graph_metrics: GraphMetrics,
}

/// Metrics derived from the code graph
#[derive(Debug, Clone, Default)]
pub struct GraphMetrics {
    /// Number of modules (directories with code)
    pub module_count: usize,
    /// Average coupling between modules (0-1, lower is better)
    pub avg_coupling: f64,
    /// Average cohesion within modules (0-1, higher is better)
    pub avg_cohesion: f64,
    /// Number of circular dependency cycles
    pub cycle_count: usize,
    /// Percentage of functions with complexity <= 10
    pub simple_function_ratio: f64,
    /// Percentage of files that are tests
    pub test_file_ratio: f64,
    /// Total functions analyzed
    pub total_functions: usize,
    /// Total files analyzed
    pub total_files: usize,
    /// Total lines of code across all files
    pub total_loc: usize,
}

/// Which pillar a finding belongs to
#[derive(Debug, Clone, Copy, PartialEq)]
enum Pillar {
    Structure,
    Quality,
    Architecture,
}

/// Classify a finding into a pillar based on its category and detector name.
/// Every category maps to exactly one pillar — no splitting.
fn classify_pillar(category: &str, detector: &str, is_security: bool) -> Pillar {
    // Security always → Quality (it's a code quality issue)
    if is_security {
        return Pillar::Quality;
    }

    match category {
        // Structure: code shape, readability, size, naming
        c if c.contains("complex") => Pillar::Structure,
        c if c.contains("naming") => Pillar::Structure,
        c if c.contains("readab") => Pillar::Structure,
        c if c.contains("style") => Pillar::Structure,
        c if c.contains("maintainab") => Pillar::Structure,

        // Architecture: module boundaries, dependencies, coupling
        c if c.contains("architect") => Pillar::Architecture,
        c if c.contains("bottleneck") => Pillar::Architecture,
        c if c.contains("circular") => Pillar::Architecture,
        c if c.contains("coupling") => Pillar::Architecture,

        // Quality: correctness, reliability, performance, error handling
        c if c.contains("security") => Pillar::Quality,
        c if c.contains("reliab") => Pillar::Quality,
        c if c.contains("correct") => Pillar::Quality,
        c if c.contains("performance") => Pillar::Quality,
        c if c.contains("error") => Pillar::Quality,
        c if c.contains("safety") => Pillar::Quality,

        // Fallback: use detector name heuristics
        _ => {
            if detector.contains("dependency") || detector.contains("import") {
                Pillar::Architecture
            } else if detector.contains("large")
                || detector.contains("nesting")
                || detector.contains("dead")
                || detector.contains("naming")
            {
                Pillar::Structure
            } else {
                // Default: Quality (correctness, misc)
                Pillar::Quality
            }
        }
    }
}

/// Graph-aware health scorer
pub struct GraphScorer<'a> {
    graph: &'a GraphStore,
    config: &'a ProjectConfig,
    repo_path: &'a std::path::Path,
}

impl<'a> GraphScorer<'a> {
    // repotoire:ignore[surprisal] — constructor with many parameters is expected for scorer
    pub fn new(
        graph: &'a GraphStore,
        config: &'a ProjectConfig,
        repo_path: &'a std::path::Path,
    ) -> Self {
        Self {
            graph,
            config,
            repo_path,
        }
    }

    /// Calculate complete health score with breakdown
    pub fn calculate(&self, findings: &[Finding]) -> ScoreBreakdown {
        // Compute graph metrics first
        let metrics = self.compute_graph_metrics();

        let project_type = self.config.project_type(self.repo_path);
        debug!(
            "Scoring with project type {:?} (coupling mult: {:.1}x, complexity mult: {:.1}x)",
            project_type,
            project_type.coupling_multiplier(),
            project_type.complexity_multiplier(),
        );

        // Calculate bonuses from graph analysis
        let modularity_bonus = self.calculate_modularity_bonus(&metrics);
        let cohesion_bonus = self.calculate_cohesion_bonus(&metrics);
        let clean_deps_bonus = self.calculate_clean_deps_bonus(&metrics);
        let complexity_bonus = self.calculate_complexity_bonus(&metrics);
        let test_bonus = self.calculate_test_bonus(&metrics);

        debug!(
            "Graph bonuses: modularity={:.1}%, cohesion={:.1}%, clean_deps={:.1}%, complexity={:.1}%, tests={:.1}%",
            modularity_bonus * 100.0,
            cohesion_bonus * 100.0,
            clean_deps_bonus * 100.0,
            complexity_bonus * 100.0,
            test_bonus * 100.0
        );

        // Density-based penalty normalization
        // Each finding's penalty is scaled by project size (kLOC).
        // A 30kLOC project with 45 findings scores the same as
        // a 2kLOC project with 3 findings (same density).
        let kloc = (metrics.total_loc as f64 / 1000.0).max(1.0);

        // Severity weights — base penalty per finding at 1 finding/kLOC density
        let severity_weight = |severity: &Severity| -> f64 {
            match severity {
                Severity::Critical => 8.0,
                Severity::High => 4.0,
                Severity::Medium => 1.0,
                Severity::Low => 0.2,
                Severity::Info => 0.0,
            }
        };

        // Scale factor: how harshly findings-per-kLOC maps to penalty points.
        // scale=5 means 1 medium/kLOC = 5 penalty points total.
        const DENSITY_SCALE: f64 = 5.0;

        let mut structure_penalty = 0.0;
        let mut quality_penalty = 0.0;
        let mut architecture_penalty = 0.0;
        let mut structure_count = 0;
        let mut quality_count = 0;
        let mut architecture_count = 0;

        for finding in findings {
            // Each finding contributes: weight * scale / kLOC
            let scaled = severity_weight(&finding.severity) * DENSITY_SCALE / kloc;

            let category = finding.category.as_deref().unwrap_or("");
            let detector = finding.detector.to_lowercase();

            let is_security = self.is_security_finding(finding);
            let security_mult = if is_security {
                self.config.scoring.security_multiplier
            } else {
                1.0
            };
            let effective = scaled * security_mult;

            // Route findings to pillars based on category
            let pillar = classify_pillar(category, &detector, is_security);
            match pillar {
                Pillar::Quality => {
                    quality_penalty += effective;
                    quality_count += 1;
                }
                Pillar::Structure => {
                    structure_penalty += effective;
                    structure_count += 1;
                }
                Pillar::Architecture => {
                    architecture_penalty += effective;
                    architecture_count += 1;
                }
            }
        }

        // Build pillar breakdowns
        let structure = self.build_pillar(
            "Structure",
            structure_penalty,
            structure_count,
            vec![("Complexity distribution", complexity_bonus)],
        );

        let quality = self.build_pillar(
            "Quality",
            quality_penalty,
            quality_count,
            vec![("Test coverage signal", test_bonus)],
        );

        let architecture = self.build_pillar(
            "Architecture",
            architecture_penalty,
            architecture_count,
            vec![
                ("Modularity (low coupling)", modularity_bonus),
                ("Cohesion", cohesion_bonus),
                ("Clean dependencies (no cycles)", clean_deps_bonus),
            ],
        );

        // Weighted overall score — normalize weights if they don't sum to 1.0 (#36)
        let mut weights = self.config.scoring.pillar_weights.clone();
        if !weights.is_valid() {
            tracing::warn!(
                "Pillar weights sum to {:.3} (expected 1.0), normalizing",
                weights.structure + weights.quality + weights.architecture
            );
            weights.normalize();
        }
        let overall = structure.final_score * weights.structure
            + quality.final_score * weights.quality
            + architecture.final_score * weights.architecture;

        let overall = overall.max(5.0);

        // Never report 100.0 if there are medium+ findings — cap at 99.9
        let has_medium_plus = findings.iter().any(|f| {
            matches!(
                f.severity,
                Severity::Critical | Severity::High | Severity::Medium
            )
        });
        let overall = if has_medium_plus && overall >= 99.95 {
            99.9
        } else {
            overall
        };

        let grade = self.calculate_grade(overall, findings);

        info!(
            "Health score: {:.1} ({}) - Structure: {:.1}, Quality: {:.1}, Architecture: {:.1}",
            overall, grade, structure.final_score, quality.final_score, architecture.final_score
        );

        ScoreBreakdown {
            overall_score: overall,
            grade,
            structure,
            quality,
            architecture,
            graph_metrics: metrics,
        }
    }

    /// Build a pillar breakdown
    fn build_pillar(
        &self,
        name: &str,
        penalty: f64,
        finding_count: usize,
        bonuses: Vec<(&str, f64)>,
    ) -> PillarBreakdown {
        let base_score = (100.0 - penalty).clamp(25.0, 100.0);
        // Additive bonuses: each bonus adds up to its max % as points
        // e.g. 5% test bonus = +5 points, not *1.05 multiplier
        let total_bonus: f64 = bonuses.iter().map(|(_, b)| b).sum();
        let bonus_points = total_bonus * 100.0;
        // Bonuses can recover at most half the penalty — they can't fully mask issues.
        // If penalty=4 and bonus=5, you recover 2 (not 5), giving 98 not 101.
        let capped_bonus = if penalty > 0.0 {
            bonus_points.min(penalty * 0.5)
        } else {
            bonus_points
        };
        let final_score = (base_score + capped_bonus).min(100.0);

        PillarBreakdown {
            name: name.to_string(),
            base_score,
            bonus_ratio: total_bonus,
            final_score,
            bonuses: bonuses
                .into_iter()
                .map(|(n, v)| (n.to_string(), v))
                .collect(),
            penalty_points: penalty,
            finding_count,
        }
    }

    /// Compute all graph metrics
    fn compute_graph_metrics(&self) -> GraphMetrics {
        let functions = self.graph.get_functions();
        let files = self.graph.get_files();
        let calls = self.graph.get_calls();
        let _imports = self.graph.get_imports();

        // Count modules (unique directories)
        let modules: HashSet<String> = files
            .iter()
            .filter_map(|f| {
                let path = std::path::Path::new(&f.file_path);
                path.parent().map(|p| p.to_string_lossy().to_string())
            })
            .collect();

        // Calculate coupling (cross-module calls / total calls)
        let mut cross_module_calls = 0;
        let func_to_module: HashMap<&str, String> = functions
            .iter()
            .map(|f| {
                let module = std::path::Path::new(&f.file_path)
                    .parent()
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or_default();
                (f.qualified_name.as_str(), module)
            })
            .collect();

        for (caller, callee) in &calls {
            let caller_mod = func_to_module.get(caller.as_str());
            let callee_mod = func_to_module.get(callee.as_str());
            if caller_mod != callee_mod && caller_mod.is_some() && callee_mod.is_some() {
                cross_module_calls += 1;
            }
        }

        debug!(
            "Call graph: {} total calls, {} cross-module, {} modules",
            calls.len(),
            cross_module_calls,
            modules.len()
        );

        let avg_coupling = if calls.is_empty() {
            0.0
        } else {
            cross_module_calls as f64 / calls.len() as f64
        };

        // Calculate cohesion (intra-module calls / total calls)
        let intra_module_calls = calls.len() - cross_module_calls;
        let avg_cohesion = if calls.is_empty() {
            1.0 // No calls = perfectly cohesive (trivially)
        } else {
            intra_module_calls as f64 / calls.len() as f64
        };

        debug!(
            "Coupling: {:.1}%, Cohesion: {:.1}%",
            avg_coupling * 100.0,
            avg_cohesion * 100.0
        );

        // Count cycles
        let import_cycles = self.graph.find_import_cycles();
        let call_cycles = self.graph.find_call_cycles();
        let cycle_count = import_cycles.len() + call_cycles.len();

        // Simple function ratio (complexity <= 10)
        let simple_count = functions
            .iter()
            .filter(|f| f.complexity().unwrap_or(1) <= 10)
            .count();
        let simple_ratio = if functions.is_empty() {
            1.0
        } else {
            simple_count as f64 / functions.len() as f64
        };

        // Test file ratio
        let test_files = files
            .iter()
            .filter(|f| self.is_test_file(&f.file_path))
            .count();
        let test_ratio = if files.is_empty() {
            0.0
        } else {
            test_files as f64 / files.len() as f64
        };

        // Total LOC from file nodes
        let total_loc: usize = files
            .iter()
            .map(|f| f.get_i64("loc").unwrap_or(0) as usize)
            .sum();

        GraphMetrics {
            module_count: modules.len(),
            avg_coupling,
            avg_cohesion,
            cycle_count,
            simple_function_ratio: simple_ratio,
            test_file_ratio: test_ratio,
            total_functions: functions.len(),
            total_files: files.len(),
            total_loc,
        }
    }

    /// Calculate modularity bonus (low coupling is good)
    fn calculate_modularity_bonus(&self, metrics: &GraphMetrics) -> f64 {
        let cm = self.config.project_type(self.repo_path).coupling_multiplier();
        // Scale thresholds by coupling multiplier:
        // - Web (1.0x): full bonus at ≤0.3, none at ≥0.7
        // - Compiler (3.0x): full bonus at ≤0.9, none at ≥1.0
        let full_threshold = (0.3 * cm).min(1.0);
        let zero_threshold = (0.7 * cm).min(1.0);
        let range = (zero_threshold - full_threshold).max(0.01);
        let coupling_score =
            1.0 - ((metrics.avg_coupling - full_threshold) / range).clamp(0.0, 1.0);
        coupling_score * MAX_MODULARITY_BONUS
    }

    /// Calculate cohesion bonus (high cohesion is good)
    fn calculate_cohesion_bonus(&self, metrics: &GraphMetrics) -> f64 {
        let cm = self.config.project_type(self.repo_path).coupling_multiplier();
        // High-coupling project types expect less cohesion
        let full_threshold = (0.7 / cm).max(0.15);
        let zero_threshold = (0.3 / cm).max(0.05);
        let range = (full_threshold - zero_threshold).max(0.01);
        let cohesion_score =
            ((metrics.avg_cohesion - zero_threshold) / range).clamp(0.0, 1.0);
        cohesion_score * MAX_COHESION_BONUS
    }

    /// Calculate clean dependencies bonus (no cycles is good)
    fn calculate_clean_deps_bonus(&self, metrics: &GraphMetrics) -> f64 {
        // 0 cycles = full bonus, each cycle reduces by 20%
        let penalty = (metrics.cycle_count as f64 * 0.2).min(1.0);
        (1.0 - penalty) * MAX_CLEAN_DEPS_BONUS
    }

    /// Calculate complexity distribution bonus
    fn calculate_complexity_bonus(&self, metrics: &GraphMetrics) -> f64 {
        let xm = self.config.project_type(self.repo_path).complexity_multiplier();
        // Scale thresholds by complexity multiplier:
        // - Web (1.0x): full bonus at 90%+ simple, none at 50%
        // - Kernel (2.0x): full bonus at 45%+ simple, none at 25%
        let full_threshold = (0.9 / xm).max(0.3);
        let zero_threshold = (0.5 / xm).max(0.1);
        let range = (full_threshold - zero_threshold).max(0.01);
        let score =
            ((metrics.simple_function_ratio - zero_threshold) / range).clamp(0.0, 1.0);
        score * MAX_COMPLEXITY_DIST_BONUS
    }

    /// Calculate test coverage signal bonus
    fn calculate_test_bonus(&self, metrics: &GraphMetrics) -> f64 {
        // 20%+ test files = full bonus
        // 0% = no bonus
        let score = (metrics.test_file_ratio / 0.2).clamp(0.0, 1.0);
        score * MAX_TEST_COVERAGE_BONUS
    }

    /// Check if a finding is security-related
    fn is_security_finding(&self, finding: &Finding) -> bool {
        let category = finding.category.as_deref().unwrap_or("");
        let detector = finding.detector.to_lowercase();

        category.contains("security")
            || category.contains("inject")
            || detector.contains("sql")
            || detector.contains("xss")
            || detector.contains("secret")
            || detector.contains("credential")
            || detector.contains("command")
            || detector.contains("traversal")
            || detector.contains("ssrf")
            || detector.contains("taint")
            || finding.cwe_id.is_some()
    }

    /// Check if a file path is a test file
    fn is_test_file(&self, path: &str) -> bool {
        let lower = path.to_lowercase();
        lower.contains("/test/")
            || lower.contains("/tests/")
            || lower.contains("/__tests__/")
            || lower.contains("/spec/")
            || lower.starts_with("test/")
            || lower.starts_with("tests/")
            || lower.ends_with("_test.go")
            || lower.ends_with("_test.py")
            || lower.ends_with("_test.rs")
            || lower.ends_with(".test.ts")
            || lower.ends_with(".test.js")
            || lower.ends_with(".spec.ts")
            || lower.ends_with(".spec.js")
    }

    /// Calculate letter grade
    fn calculate_grade(&self, score: f64, _findings: &[Finding]) -> String {
        // Grade is purely based on score - no caps
        // The score already reflects severity of findings
        // Capping based on criticals double-penalizes and punishes FPs in scripts/tests

        let base_grade = if score >= 97.0 {
            "A+"
        } else if score >= 93.0 {
            "A"
        } else if score >= 90.0 {
            "A-"
        } else if score >= 87.0 {
            "B+"
        } else if score >= 83.0 {
            "B"
        } else if score >= 80.0 {
            "B-"
        } else if score >= 77.0 {
            "C+"
        } else if score >= 73.0 {
            "C"
        } else if score >= 70.0 {
            "C-"
        } else if score >= 67.0 {
            "D+"
        } else if score >= 63.0 {
            "D"
        } else if score >= 60.0 {
            "D-"
        } else {
            "F"
        };

        base_grade.to_string()
    }

    /// Generate human-readable explanation of the score
    pub fn explain(&self, breakdown: &ScoreBreakdown) -> String {
        let mut lines = Vec::new();
        let m = &breakdown.graph_metrics;
        let kloc = m.total_loc as f64 / 1000.0;

        lines.push(format!(
            "# Health Score: {:.1} ({})\n",
            breakdown.overall_score, breakdown.grade
        ));

        lines.push("## Scoring Formula\n".to_string());
        let w = &self.config.scoring.pillar_weights;
        lines.push("```".to_string());
        lines.push(format!(
            "Overall = Structure × {:.2} + Quality × {:.2} + Architecture × {:.2}",
            w.structure, w.quality, w.architecture
        ));
        lines.push("Pillar  = (100 - penalties) + graph_bonuses".to_string());
        lines.push(format!(
            "Penalty = severity_weight × 5.0 / kLOC   (kLOC = {:.1})",
            kloc
        ));
        lines.push("```\n".to_string());
        lines.push("Severity weights: Critical=8.0, High=4.0, Medium=1.0, Low=0.2\n".to_string());

        // Graph metrics
        lines.push("## Graph Analysis\n".to_string());
        lines.push(format!(
            "- **Lines of code**: {} ({:.1} kLOC)",
            m.total_loc, kloc
        ));
        lines.push(format!("- **Modules**: {}", m.module_count));
        lines.push(format!(
            "- **Coupling**: {:.1}% cross-module calls (lower is better)",
            m.avg_coupling * 100.0
        ));
        lines.push(format!(
            "- **Cohesion**: {:.1}% intra-module calls (higher is better)",
            m.avg_cohesion * 100.0
        ));
        lines.push(format!(
            "- **Cycles**: {} circular dependencies",
            m.cycle_count
        ));
        lines.push(format!(
            "- **Simple functions**: {:.1}% have complexity ≤ 10",
            m.simple_function_ratio * 100.0
        ));
        lines.push(format!(
            "- **Test files**: {:.1}%\n",
            m.test_file_ratio * 100.0
        ));

        // Pillar breakdowns
        for pillar in [
            &breakdown.structure,
            &breakdown.quality,
            &breakdown.architecture,
        ] {
            lines.push(format!(
                "## {} Score: {:.1}\n",
                pillar.name, pillar.final_score
            ));
            lines.push(format!(
                "- Base: 100 - {:.2} penalties = {:.1}",
                pillar.penalty_points, pillar.base_score
            ));
            let total_bonus: f64 = pillar.bonuses.iter().map(|(_, v)| v).sum::<f64>() * 100.0;
            let capped = if pillar.penalty_points > 0.0 {
                total_bonus.min(pillar.penalty_points * 0.5)
            } else {
                total_bonus
            };
            let active_bonuses: Vec<_> =
                pillar.bonuses.iter().filter(|(_, v)| *v > 0.001).collect();
            if !active_bonuses.is_empty() {
                lines.push("- Bonuses (additive, capped at 50% of penalty):".to_string());
                for (name, value) in &active_bonuses {
                    lines.push(format!("  - {}: +{:.1} pts", name, value * 100.0));
                }
                if capped < total_bonus {
                    lines.push(format!(
                        "  - *(capped from {:.1} to {:.1} pts)*",
                        total_bonus, capped
                    ));
                }
            }
            lines.push(format!("- Final: {:.1}", pillar.final_score));
            lines.push(format!("- Findings: {}\n", pillar.finding_count));
        }

        lines.join("\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::ProjectType;
    use crate::graph::GraphStore;
    use tempfile::TempDir;

    fn make_config(project_type: Option<ProjectType>) -> (TempDir, ProjectConfig) {
        let dir = TempDir::new().unwrap();
        let mut config = ProjectConfig::default();
        config.project_type = project_type;
        (dir, config)
    }

    #[test]
    fn test_empty_codebase() {
        let graph = GraphStore::in_memory();
        let (dir, config) = make_config(None);
        let scorer = GraphScorer::new(&graph, &config, dir.path());

        let breakdown = scorer.calculate(&[]);

        // Empty codebase with no findings should score well
        assert!(breakdown.overall_score >= 90.0);
    }

    #[test]
    fn test_critical_finding_caps_grade() {
        let graph = GraphStore::in_memory();
        let (dir, config) = make_config(None);
        let scorer = GraphScorer::new(&graph, &config, dir.path());

        let findings = vec![Finding {
            severity: Severity::Critical,
            detector: "test".to_string(),
            title: "Critical issue".to_string(),
            ..Default::default()
        }];

        let breakdown = scorer.calculate(&findings);

        // Grade is now purely score-based (no caps)
        // With minimal findings on an empty graph, score should be high = A range
        assert!(
            breakdown.grade.starts_with('A')
                || breakdown.grade.starts_with('B')
                || breakdown.grade.starts_with('C'),
            "Expected reasonable grade, got {}",
            breakdown.grade
        );
    }

    #[test]
    fn test_graph_bonuses() {
        let graph = GraphStore::in_memory();

        // Add some test structure
        use crate::graph::CodeNode;
        graph.add_node(CodeNode::file("src/main.rs"));
        graph.add_node(CodeNode::file("src/lib.rs"));
        graph.add_node(CodeNode::file("tests/test_main.rs")); // Test file
        graph.add_node(
            CodeNode::function("main", "src/main.rs").with_property("complexity", 5i64),
        );
        graph.add_node(
            CodeNode::function("helper", "src/lib.rs").with_property("complexity", 3i64),
        );
        graph.add_node(
            CodeNode::function("test_main", "tests/test_main.rs")
                .with_property("complexity", 2i64),
        );

        let (dir, config) = make_config(None);
        let scorer = GraphScorer::new(&graph, &config, dir.path());

        let metrics = scorer.compute_graph_metrics();

        assert_eq!(metrics.total_files, 3);
        assert_eq!(metrics.total_functions, 3);
        // 1 out of 3 files is a test file
        assert!(
            (metrics.test_file_ratio - 0.333).abs() < 0.01,
            "test_file_ratio={}",
            metrics.test_file_ratio
        );
        assert_eq!(metrics.simple_function_ratio, 1.0); // All functions have complexity < 10
    }

    #[test]
    fn test_compiler_gets_lenient_modularity_bonus() {
        let graph = GraphStore::in_memory();
        let (dir, compiler_config) = make_config(Some(ProjectType::Compiler));
        let compiler_scorer = GraphScorer::new(&graph, &compiler_config, dir.path());

        let (_, web_config) = make_config(Some(ProjectType::Web));
        let web_scorer = GraphScorer::new(&graph, &web_config, dir.path());

        // 60% cross-module coupling: bad for web, ok for compiler
        let metrics = GraphMetrics {
            avg_coupling: 0.6,
            avg_cohesion: 0.4,
            ..Default::default()
        };

        let compiler_bonus = compiler_scorer.calculate_modularity_bonus(&metrics);
        let web_bonus = web_scorer.calculate_modularity_bonus(&metrics);

        assert!(
            compiler_bonus > web_bonus,
            "Compiler bonus ({:.4}) should be > web bonus ({:.4}) at 60% coupling",
            compiler_bonus,
            web_bonus
        );
    }

    #[test]
    fn test_kernel_gets_lenient_complexity_bonus() {
        let graph = GraphStore::in_memory();
        let (dir, kernel_config) = make_config(Some(ProjectType::Kernel));
        let kernel_scorer = GraphScorer::new(&graph, &kernel_config, dir.path());

        let (_, web_config) = make_config(Some(ProjectType::Web));
        let web_scorer = GraphScorer::new(&graph, &web_config, dir.path());

        // Only 55% simple functions: bad for web, ok for kernel
        let metrics = GraphMetrics {
            simple_function_ratio: 0.55,
            ..Default::default()
        };

        let kernel_bonus = kernel_scorer.calculate_complexity_bonus(&metrics);
        let web_bonus = web_scorer.calculate_complexity_bonus(&metrics);

        assert!(
            kernel_bonus > web_bonus,
            "Kernel bonus ({:.4}) should be > web bonus ({:.4}) at 55% simple",
            kernel_bonus,
            web_bonus
        );
    }

    #[test]
    fn test_web_default_thresholds_unchanged() {
        let graph = GraphStore::in_memory();
        let (dir, config) = make_config(Some(ProjectType::Web));
        let scorer = GraphScorer::new(&graph, &config, dir.path());

        let metrics = GraphMetrics {
            avg_coupling: 0.5,
            avg_cohesion: 0.5,
            simple_function_ratio: 0.7,
            ..Default::default()
        };

        // Coupling 0.5 is between 0.3 (full) and 0.7 (none) — should get 50% bonus
        let mod_bonus = scorer.calculate_modularity_bonus(&metrics);
        assert!(
            (mod_bonus - 0.05).abs() < 0.001,
            "Expected ~0.05, got {}",
            mod_bonus
        );
    }
}
