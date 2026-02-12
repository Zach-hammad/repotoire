//! Graph-based health scorer
//!
//! Uses the code graph to compute positive architectural signals,
//! combined with finding penalties for the final score.

use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use crate::config::ProjectConfig;
use std::collections::{HashMap, HashSet};
use tracing::{debug, info};

/// Maximum bonus percentages for each positive signal
const MAX_MODULARITY_BONUS: f64 = 0.10;      // 10% max
const MAX_COHESION_BONUS: f64 = 0.05;        // 5% max  
const MAX_CLEAN_DEPS_BONUS: f64 = 0.10;      // 10% max
const MAX_COMPLEXITY_DIST_BONUS: f64 = 0.05; // 5% max
const MAX_TEST_COVERAGE_BONUS: f64 = 0.05;   // 5% max

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
}

/// Graph-aware health scorer
pub struct GraphScorer<'a> {
    graph: &'a GraphStore,
    config: &'a ProjectConfig,
}

impl<'a> GraphScorer<'a> {
    pub fn new(graph: &'a GraphStore, config: &'a ProjectConfig) -> Self {
        Self { graph, config }
    }

    /// Calculate complete health score with breakdown
    pub fn calculate(&self, findings: &[Finding]) -> ScoreBreakdown {
        // Compute graph metrics first
        let metrics = self.compute_graph_metrics();
        
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

        // Calculate penalties from findings
        let size_factor = ((metrics.total_files + metrics.total_functions) as f64).sqrt().max(5.0);
        
        let mut structure_penalty = 0.0;
        let mut quality_penalty = 0.0;
        let mut architecture_penalty = 0.0;
        let mut structure_count = 0;
        let mut quality_count = 0;
        let mut architecture_count = 0;

        for finding in findings {
            let base_deduction = match finding.severity {
                Severity::Critical => 10.0,
                Severity::High => 5.0,
                Severity::Medium => 1.5,
                Severity::Low => 0.3,
                Severity::Info => 0.0,
            };

            let scaled = base_deduction / size_factor;
            
            let category = finding.category.as_deref().unwrap_or("");
            let detector = finding.detector.to_lowercase();
            
            let is_security = self.is_security_finding(finding);
            let security_mult = if is_security { self.config.scoring.security_multiplier } else { 1.0 };
            let effective = scaled * security_mult;

            // Assign to pillars based on category
            if is_security || category.contains("security") {
                quality_penalty += effective;
                quality_count += 1;
            } else if category.contains("architect") || category.contains("bottleneck") 
                || category.contains("circular") || category.contains("coupling")
                || detector.contains("dependency") {
                architecture_penalty += effective;
                architecture_count += 1;
            } else if category.contains("complex") || category.contains("naming") 
                || category.contains("readab") || category.contains("style") {
                structure_penalty += effective;
                structure_count += 1;
            } else {
                // Distribute evenly
                quality_penalty += effective / 3.0;
                structure_penalty += effective / 3.0;
                architecture_penalty += effective / 3.0;
                quality_count += 1;
            }
        }

        // Build pillar breakdowns
        let structure = self.build_pillar(
            "Structure",
            structure_penalty,
            structure_count,
            vec![
                ("Complexity distribution", complexity_bonus),
            ],
        );

        let quality = self.build_pillar(
            "Quality", 
            quality_penalty,
            quality_count,
            vec![
                ("Test coverage signal", test_bonus),
            ],
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

        // Weighted overall score
        let weights = &self.config.scoring.pillar_weights;
        let overall = structure.final_score * weights.structure
            + quality.final_score * weights.quality
            + architecture.final_score * weights.architecture;
        
        let overall = overall.max(5.0);
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
        let base_score = (100.0 - penalty).max(25.0).min(100.0);
        let total_bonus: f64 = bonuses.iter().map(|(_, b)| b).sum();
        let final_score = (base_score * (1.0 + total_bonus)).min(100.0);

        PillarBreakdown {
            name: name.to_string(),
            base_score,
            bonus_ratio: total_bonus,
            final_score,
            bonuses: bonuses.into_iter().map(|(n, v)| (n.to_string(), v)).collect(),
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
        let modules: HashSet<String> = files.iter()
            .filter_map(|f| {
                let path = std::path::Path::new(&f.file_path);
                path.parent().map(|p| p.to_string_lossy().to_string())
            })
            .collect();

        // Calculate coupling (cross-module calls / total calls)
        let mut cross_module_calls = 0;
        let func_to_module: HashMap<&str, String> = functions.iter()
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
            calls.len(), cross_module_calls, modules.len()
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
        
        debug!("Coupling: {:.1}%, Cohesion: {:.1}%", avg_coupling * 100.0, avg_cohesion * 100.0);

        // Count cycles
        let import_cycles = self.graph.find_import_cycles();
        let call_cycles = self.graph.find_call_cycles();
        let cycle_count = import_cycles.len() + call_cycles.len();

        // Simple function ratio (complexity <= 10)
        let simple_count = functions.iter()
            .filter(|f| f.complexity().unwrap_or(1) <= 10)
            .count();
        let simple_ratio = if functions.is_empty() {
            1.0
        } else {
            simple_count as f64 / functions.len() as f64
        };

        // Test file ratio
        let test_files = files.iter()
            .filter(|f| self.is_test_file(&f.file_path))
            .count();
        let test_ratio = if files.is_empty() {
            0.0
        } else {
            test_files as f64 / files.len() as f64
        };

        GraphMetrics {
            module_count: modules.len(),
            avg_coupling,
            avg_cohesion,
            cycle_count,
            simple_function_ratio: simple_ratio,
            test_file_ratio: test_ratio,
            total_functions: functions.len(),
            total_files: files.len(),
        }
    }

    /// Calculate modularity bonus (low coupling is good)
    fn calculate_modularity_bonus(&self, metrics: &GraphMetrics) -> f64 {
        // Coupling of 0.3 or less gets full bonus
        // Coupling of 0.7 or more gets no bonus
        let coupling_score = 1.0 - ((metrics.avg_coupling - 0.3) / 0.4).clamp(0.0, 1.0);
        coupling_score * MAX_MODULARITY_BONUS
    }

    /// Calculate cohesion bonus (high cohesion is good)
    fn calculate_cohesion_bonus(&self, metrics: &GraphMetrics) -> f64 {
        // Cohesion of 0.7 or more gets full bonus
        // Cohesion of 0.3 or less gets no bonus
        let cohesion_score = ((metrics.avg_cohesion - 0.3) / 0.4).clamp(0.0, 1.0);
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
        // 90%+ simple functions = full bonus
        // 50% simple = no bonus
        let score = ((metrics.simple_function_ratio - 0.5) / 0.4).clamp(0.0, 1.0);
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
    fn calculate_grade(&self, score: f64, findings: &[Finding]) -> String {
        let critical_count = findings.iter()
            .filter(|f| matches!(f.severity, Severity::Critical))
            .count();
        
        // Any critical finding caps grade at C
        let max_grade = if critical_count > 0 { "C" } else { "A+" };
        
        let base_grade = if score >= 97.0 { "A+" }
        else if score >= 93.0 { "A" }
        else if score >= 90.0 { "A-" }
        else if score >= 87.0 { "B+" }
        else if score >= 83.0 { "B" }
        else if score >= 80.0 { "B-" }
        else if score >= 77.0 { "C+" }
        else if score >= 73.0 { "C" }
        else if score >= 70.0 { "C-" }
        else if score >= 67.0 { "D+" }
        else if score >= 63.0 { "D" }
        else if score >= 60.0 { "D-" }
        else { "F" };

        // Cap at max grade
        if max_grade == "C" && base_grade.starts_with('A') || base_grade.starts_with('B') {
            "C".to_string()
        } else {
            base_grade.to_string()
        }
    }

    /// Generate human-readable explanation of the score
    pub fn explain(&self, breakdown: &ScoreBreakdown) -> String {
        let mut lines = Vec::new();
        
        lines.push(format!("# Health Score: {:.1} ({})\n", breakdown.overall_score, breakdown.grade));
        
        lines.push("## Scoring Formula\n".to_string());
        lines.push("```".to_string());
        lines.push("Overall = Structure × 0.33 + Quality × 0.34 + Architecture × 0.33".to_string());
        lines.push("Pillar  = (100 - penalties) × (1 + graph_bonuses)".to_string());
        lines.push("```\n".to_string());

        // Graph metrics
        lines.push("## Graph Analysis\n".to_string());
        let m = &breakdown.graph_metrics;
        lines.push(format!("- **Modules**: {}", m.module_count));
        lines.push(format!("- **Coupling**: {:.1}% cross-module calls (lower is better)", m.avg_coupling * 100.0));
        lines.push(format!("- **Cohesion**: {:.1}% intra-module calls (higher is better)", m.avg_cohesion * 100.0));
        lines.push(format!("- **Cycles**: {} circular dependencies", m.cycle_count));
        lines.push(format!("- **Simple functions**: {:.1}% have complexity ≤ 10", m.simple_function_ratio * 100.0));
        lines.push(format!("- **Test files**: {:.1}%\n", m.test_file_ratio * 100.0));

        // Pillar breakdowns
        for pillar in [&breakdown.structure, &breakdown.quality, &breakdown.architecture] {
            lines.push(format!("## {} Score: {:.1}\n", pillar.name, pillar.final_score));
            lines.push(format!("- Base: 100 - {:.1} penalties = {:.1}", pillar.penalty_points, pillar.base_score));
            if !pillar.bonuses.is_empty() {
                lines.push("- Bonuses:".to_string());
                for (name, value) in &pillar.bonuses {
                    if *value > 0.001 {
                        lines.push(format!("  - {}: +{:.1}%", name, value * 100.0));
                    }
                }
            }
            lines.push(format!("- Findings: {}\n", pillar.finding_count));
        }

        lines.join("\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_empty_codebase() {
        let graph = GraphStore::in_memory();
        let config = ProjectConfig::default();
        let scorer = GraphScorer::new(&graph, &config);
        
        let breakdown = scorer.calculate(&[]);
        
        // Empty codebase with no findings should score well
        assert!(breakdown.overall_score >= 90.0);
    }

    #[test]
    fn test_critical_finding_caps_grade() {
        let graph = GraphStore::in_memory();
        let config = ProjectConfig::default();
        let scorer = GraphScorer::new(&graph, &config);
        
        let findings = vec![Finding {
            severity: Severity::Critical,
            detector: "test".to_string(),
            title: "Critical issue".to_string(),
            ..Default::default()
        }];
        
        let breakdown = scorer.calculate(&findings);
        
        // Critical finding should cap grade at C
        assert!(breakdown.grade.starts_with('C') || breakdown.grade.starts_with('D') || breakdown.grade == "F");
    }

    #[test]
    fn test_graph_bonuses() {
        let graph = GraphStore::in_memory();
        
        // Add some test structure
        use crate::graph::CodeNode;
        graph.add_node(CodeNode::file("src/main.rs"));
        graph.add_node(CodeNode::file("src/lib.rs"));
        graph.add_node(CodeNode::file("tests/test_main.rs")); // Test file
        graph.add_node(CodeNode::function("main", "src/main.rs").with_property("complexity", 5i64));
        graph.add_node(CodeNode::function("helper", "src/lib.rs").with_property("complexity", 3i64));
        graph.add_node(CodeNode::function("test_main", "tests/test_main.rs").with_property("complexity", 2i64));
        
        let config = ProjectConfig::default();
        let scorer = GraphScorer::new(&graph, &config);
        
        let metrics = scorer.compute_graph_metrics();
        
        assert_eq!(metrics.total_files, 3);
        assert_eq!(metrics.total_functions, 3);
        // 1 out of 3 files is a test file
        assert!((metrics.test_file_ratio - 0.333).abs() < 0.01, "test_file_ratio={}", metrics.test_file_ratio);
        assert_eq!(metrics.simple_function_ratio, 1.0); // All functions have complexity < 10
    }
}
