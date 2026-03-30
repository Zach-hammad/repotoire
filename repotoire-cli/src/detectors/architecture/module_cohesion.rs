//! Module cohesion detector — pass-through file detection
//!
//! Detects files that act as pure pass-throughs: they make no internal
//! calls (zero calls to functions in the same file) but many external
//! calls. These files may belong in a different module or need
//! restructuring.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphQueryExt;
use crate::models::{Finding, Severity};
use anyhow::Result;
use rustc_hash::FxHashMap;
use tracing::debug;

/// Detects modularity issues using Louvain community detection.
///
/// The algorithm identifies natural module boundaries by maximizing
/// modularity - finding groups of files that are densely connected internally
/// but sparsely connected externally.
///
/// Detects:
/// - Poor global modularity (monolithic architecture)
/// - God modules (communities > 20% of codebase)
/// - Misplaced files (files in wrong community)
/// - High inter-community coupling
pub struct ModuleCohesionDetector {
    #[allow(dead_code)] // Stored for future config access
    config: DetectorConfig,
    /// Modularity threshold for "poor"
    #[allow(dead_code)] // Config field
    modularity_poor: f64,
    /// God module threshold (% of total files)
    #[allow(dead_code)] // Config field
    god_module_threshold: f64,
    /// Resolution parameter for Louvain
    #[allow(dead_code)] // Config field
    resolution: f64,
}

impl ModuleCohesionDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            modularity_poor: 0.3,
            god_module_threshold: 20.0,
            resolution: 1.0,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        Self {
            modularity_poor: config.get_option_or("modularity_poor", 0.3),
            god_module_threshold: config.get_option_or("god_module_threshold", 20.0),
            resolution: config.get_option_or("resolution", 1.0),
            config,
        }
    }

    /// Run Louvain community detection algorithm
    #[allow(dead_code)] // Graph algorithm helper
    fn louvain(
        &self,
        neighbors: &[Vec<(usize, f64)>],
        degrees: &[f64],
        total_weight: f64,
        num_nodes: usize,
    ) -> (Vec<u32>, f64) {
        if num_nodes == 0 || total_weight == 0.0 {
            return (vec![], 0.0);
        }

        // Initialize: each node in its own community
        let mut communities: Vec<u32> = (0..num_nodes as u32).collect();

        // Track sum of degrees per community
        let mut community_weights: FxHashMap<u32, f64> = degrees
            .iter()
            .enumerate()
            .map(|(i, &d)| (i as u32, d))
            .collect();

        let mut improved = true;
        let mut max_iterations = 100;

        while improved && max_iterations > 0 {
            improved = false;
            max_iterations -= 1;

            for node in 0..num_nodes {
                let current_community = communities[node];
                let k_i = degrees[node];

                // Find neighboring communities and their edge weights
                let mut neighbor_communities: FxHashMap<u32, f64> = FxHashMap::default();
                for &(neighbor, weight) in &neighbors[node] {
                    let nc = communities[neighbor];
                    *neighbor_communities.entry(nc).or_insert(0.0) += weight;
                }

                // Remove node from current community temporarily
                if let Some(w) = community_weights.get_mut(&current_community) {
                    *w -= k_i;
                }

                // Find best community
                let mut best_community = current_community;
                let mut best_gain = 0.0;

                for (&target_community, &k_i_in) in &neighbor_communities {
                    let sigma_tot = community_weights
                        .get(&target_community)
                        .copied()
                        .unwrap_or(0.0);
                    let gain = k_i_in / total_weight
                        - (sigma_tot * k_i) / (2.0 * total_weight * total_weight);
                    let gain = gain * self.resolution;

                    if gain > best_gain {
                        best_gain = gain;
                        best_community = target_community;
                    }
                }

                // Also consider staying
                let stay_in = neighbor_communities
                    .get(&current_community)
                    .copied()
                    .unwrap_or(0.0);
                let sigma_tot = community_weights
                    .get(&current_community)
                    .copied()
                    .unwrap_or(0.0);
                let stay_gain = stay_in / total_weight
                    - (sigma_tot * k_i) / (2.0 * total_weight * total_weight);
                let stay_gain = stay_gain * self.resolution;

                if stay_gain >= best_gain {
                    best_community = current_community;
                }

                // Move node
                if best_community != current_community {
                    communities[node] = best_community;
                    *community_weights.entry(best_community).or_insert(0.0) += k_i;
                    improved = true;
                } else {
                    *community_weights.entry(current_community).or_insert(0.0) += k_i;
                }
            }
        }

        // Renumber communities
        let mut community_map: FxHashMap<u32, u32> = FxHashMap::default();
        let mut next_id = 0u32;
        for c in &mut communities {
            if let Some(&mapped) = community_map.get(c) {
                *c = mapped;
            } else {
                community_map.insert(*c, next_id);
                *c = next_id;
                next_id += 1;
            }
        }

        // Calculate modularity
        let modularity = self.calculate_modularity(&communities, neighbors, degrees, total_weight);

        (communities, modularity)
    }

    /// Calculate modularity score
    #[allow(dead_code)]
    fn calculate_modularity(
        &self,
        communities: &[u32],
        neighbors: &[Vec<(usize, f64)>],
        degrees: &[f64],
        total_weight: f64,
    ) -> f64 {
        if total_weight == 0.0 {
            return 0.0;
        }

        let mut q = 0.0;
        let m2 = 2.0 * total_weight;

        for (i, &c_i) in communities.iter().enumerate() {
            let k_i = degrees[i];
            for &(j, a_ij) in &neighbors[i] {
                if communities[j] == c_i {
                    let k_j = degrees[j];
                    q += a_ij - (k_i * k_j) / m2;
                }
            }
        }

        q / m2
    }

    /// Create finding for poor global modularity
    #[allow(dead_code)]
    fn create_poor_modularity_finding(
        &self,
        modularity_score: f64,
        community_count: usize,
    ) -> Finding {
        let (severity, level) = if modularity_score < 0.2 {
            (Severity::High, "very poor")
        } else {
            (Severity::Medium, "poor")
        };

        let description = format!(
            "The codebase has {} modularity (score: {:.3}). \
            Community detection found {} natural module boundaries.\n\n\
            **Modularity Score Interpretation:**\n\
            - < 0.3: Poor (monolithic, tightly coupled)\n\
            - 0.3-0.5: Moderate (some structure, room for improvement)\n\
            - 0.5-0.7: Good (well-organized)\n\
            - > 0.7: Excellent (clear boundaries)\n\n\
            **Impact:**\n\
            - Changes have high blast radius\n\
            - Difficult to test in isolation\n\
            - Hard to understand and navigate",
            level, modularity_score, community_count
        );

        let suggested_fix = "\
            **Improve modularity:**\n\n\
            1. **Identify coupling hotspots**: Use degree centrality to find \
            files with excessive cross-module dependencies\n\n\
            2. **Extract cohesive modules**: Group related functionality into \
            dedicated packages\n\n\
            3. **Define clear interfaces**: Create facade classes or APIs \
            between modules\n\n\
            4. **Apply dependency inversion**: Use abstractions to reduce \
            direct coupling\n\n\
            5. **Consider domain boundaries**: Align modules with business \
            domains (DDD approach)"
            .to_string();

        let estimated_effort = if severity == Severity::High {
            "Large (1-2 weeks)"
        } else {
            "Large (3-5 days)"
        };

        Finding {
            id: String::new(),
            detector: "ModuleCohesionDetector".to_string(),
            severity,
            title: format!("Poor codebase modularity (score: {:.2})", modularity_score),
            description,
            affected_files: vec![],
            line_start: None,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(estimated_effort.to_string()),
            category: Some("architecture".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Poor modularity means the codebase is a tangled mess of dependencies. \
                This makes changes risky, testing difficult, and onboarding painful."
                    .to_string(),
            ),
            ..Default::default()
        }
    }

    /// Create finding for god module
    #[allow(dead_code)]
    fn create_god_module_finding(
        &self,
        community_id: u32,
        size: usize,
        percentage: f64,
        total_files: usize,
    ) -> Finding {
        let severity = if percentage >= 40.0 {
            Severity::High
        } else {
            Severity::Medium
        };

        let description = format!(
            "Community {} contains {} files ({:.1}% of codebase).\n\n\
            A single module containing >20% of files indicates:\n\
            - Multiple responsibilities crammed together\n\
            - Missing abstraction layers\n\
            - Organic growth without refactoring\n\n\
            **Statistics:**\n\
            - Files in this community: {}\n\
            - Total files: {}\n\
            - Percentage: {:.1}%",
            community_id, size, percentage, size, total_files, percentage
        );

        let suggested_fix = "\
            **Split god module:**\n\n\
            1. **Analyze internal structure**: Look for natural sub-groupings\n\n\
            2. **Identify responsibility boundaries**: Each sub-module should \
            have a single purpose\n\n\
            3. **Extract incrementally**: Move cohesive file groups to new packages\n\n\
            4. **Update imports**: Establish clear dependency direction\n\n\
            5. **Add facade**: Create a module-level API if needed for backward \
            compatibility"
            .to_string();

        let estimated_effort = if severity == Severity::High {
            "Large (1-2 days)"
        } else {
            "Large (4-8 hours)"
        };

        Finding {
            id: String::new(),
            detector: "ModuleCohesionDetector".to_string(),
            severity,
            title: format!(
                "God module detected: Community {} ({:.0}% of files)",
                community_id, percentage
            ),
            description,
            affected_files: vec![],
            line_start: None,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(estimated_effort.to_string()),
            category: Some("architecture".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "God modules violate the principle of separation of concerns. \
                They become maintenance nightmares and bottlenecks for development."
                    .to_string(),
            ),
            ..Default::default()
        }
    }

    /// Create finding for misplaced file
    #[allow(dead_code)]
    fn create_misplaced_file_finding(
        &self,
        file_path: &str,
        _qualified_name: &str,
        same_imports: usize,
        other_imports: usize,
        external_ratio: f64,
    ) -> Finding {
        let severity = if external_ratio >= 0.8 {
            Severity::Medium
        } else {
            Severity::Low
        };

        let description = format!(
            "File `{}` imports more from other communities than its own.\n\n\
            **Import analysis:**\n\
            - Imports from same community: {}\n\
            - Imports from other communities: {}\n\
            - External ratio: {:.1}%\n\n\
            This suggests the file may be in the wrong location or has \
            responsibilities that belong elsewhere.",
            file_path,
            same_imports,
            other_imports,
            external_ratio * 100.0
        );

        let suggested_fix = "\
            **Options:**\n\n\
            1. **Move file**: Relocate to the package where most of its \
            dependencies live\n\n\
            2. **Refactor dependencies**: If the file should stay, refactor \
            to use local dependencies\n\n\
            3. **Extract shared code**: If multiple modules need this code, \
            extract to a shared utilities module\n\n\
            4. **Review design**: The file may be doing too much - consider \
            splitting responsibilities"
            .to_string();

        let estimated_effort = if severity == Severity::Medium {
            "Medium (1-2 hours)"
        } else {
            "Small (30-60 minutes)"
        };

        Finding {
            id: String::new(),
            detector: "ModuleCohesionDetector".to_string(),
            severity,
            title: format!("Potentially misplaced file: {}", file_path),
            description,
            affected_files: vec![file_path.into()],
            line_start: None,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some(estimated_effort.to_string()),
            category: Some("organization".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Misplaced files create confusing import patterns and suggest \
                poor module boundaries. They often indicate responsibilities \
                that should be elsewhere."
                    .to_string(),
            ),
            ..Default::default()
        }
    }

    /// Create finding for high inter-community coupling
    #[allow(dead_code)]
    fn create_coupling_finding(
        &self,
        high_coupling_edges: &[(u32, u32, usize)],
        total_cross_edges: usize,
    ) -> Finding {
        let top_pairs_desc: String = high_coupling_edges
            .iter()
            .take(3)
            .map(|(src, dst, count)| format!("- Communities {} ↔ {}: {} imports", src, dst, count))
            .collect::<Vec<_>>()
            .join("\n");

        let description = format!(
            "High coupling detected between module communities.\n\n\
            **Top coupled community pairs:**\n{}\n\n\
            Total cross-community imports: {}\n\n\
            High inter-module coupling indicates:\n\
            - Unclear module boundaries\n\
            - Potential circular dependencies\n\
            - Difficulty testing modules independently",
            top_pairs_desc, total_cross_edges
        );

        let suggested_fix = "\
            **Reduce coupling:**\n\n\
            1. **Introduce interfaces**: Define abstract APIs between modules\n\n\
            2. **Apply facade pattern**: Create single entry points to modules\n\n\
            3. **Use dependency injection**: Decouple modules through abstractions\n\n\
            4. **Consolidate shared code**: Move commonly-used code to a shared module\n\n\
            5. **Review boundaries**: Consider if modules should be merged or split"
            .to_string();

        Finding {
            id: String::new(),
            detector: "ModuleCohesionDetector".to_string(),
            severity: Severity::Medium,
            title: format!(
                "High inter-module coupling ({} cross-boundary imports)",
                total_cross_edges
            ),
            description,
            affected_files: vec![],
            line_start: None,
            line_end: None,
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some("Large (2-4 days)".to_string()),
            category: Some("architecture".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "High coupling between modules means changes in one module \
                ripple across many others. This makes the codebase fragile \
                and hard to evolve."
                    .to_string(),
            ),
            ..Default::default()
        }
    }
}

impl Default for ModuleCohesionDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for ModuleCohesionDetector {
    fn name(&self) -> &'static str {
        "ModuleCohesionDetector"
    }

    fn description(&self) -> &'static str {
        "Detects modularity issues using Louvain community detection"
    }

    fn category(&self) -> &'static str {
        "architecture"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        let gi = ctx.graph.interner();

        for file in ctx.graph.get_files() {
            let file_path = gi.resolve(file.qualified_name);

            // Get all functions in this file
            let functions = ctx.graph.get_functions_in_file(file_path);
            if functions.is_empty() {
                continue;
            }

            let mut internal_calls = 0usize;
            let mut external_calls = 0usize;

            for func in &functions {
                for callee in ctx.graph.get_callees(gi.resolve(func.qualified_name)) {
                    // Is the callee in the same file?
                    if functions
                        .iter()
                        .any(|f| f.qualified_name == callee.qualified_name)
                    {
                        internal_calls += 1;
                    } else {
                        external_calls += 1;
                    }
                }
            }

            // Only flag pure pass-through files
            if internal_calls > 0 || external_calls < 5 {
                continue;
            }

            // Check module size: directory must have 5+ files
            let module_dir = std::path::Path::new(file_path)
                .parent()
                .and_then(|p| p.to_str())
                .unwrap_or("");
            let module_file_count = ctx
                .graph
                .get_files()
                .iter()
                .filter(|f| {
                    let f_path = gi.resolve(f.qualified_name);
                    std::path::Path::new(f_path)
                        .parent()
                        .and_then(|p| p.to_str())
                        .unwrap_or("")
                        == module_dir
                })
                .count();
            if module_file_count < 5 {
                continue;
            }

            let severity = if external_calls >= 10 {
                Severity::Medium
            } else {
                Severity::Low
            };

            debug!(
                "Pass-through file detected: {} ({} external calls, {} files in module)",
                file_path, external_calls, module_file_count
            );

            findings.push(Finding {
                id: String::new(),
                detector: "ModuleCohesionDetector".to_string(),
                severity,
                title: format!("Pass-Through Module: {}", file_path),
                description: format!(
                    "File has 0 internal calls and {} external calls. \
                    May belong in a different module or need restructuring.",
                    external_calls
                ),
                affected_files: vec![file_path.into()],
                line_start: None,
                line_end: None,
                suggested_fix: Some(
                    "Consider moving this file to the module it primarily depends on, \
                    or extract shared logic into a dedicated utility module."
                        .to_string(),
                ),
                estimated_effort: Some("Medium (1-2 hours)".to_string()),
                category: Some("architecture".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Pure pass-through files add indirection without cohesion, \
                    making the module structure harder to understand."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        Ok(findings)
    }
}

impl crate::detectors::RegisteredDetector for ModuleCohesionDetector {
    fn create(init: &crate::detectors::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::with_config(init.config_for("ModuleCohesionDetector")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::analysis_context::AnalysisContext;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::store_models::{CodeEdge, CodeNode};

    /// Build a GraphBuilder with `file_count` files in the same directory.
    ///
    /// The target file (`dir/file0.rs`) is given:
    /// - `internal_call_count` calls between functions inside itself
    /// - `external_call_count` calls from a function inside it to functions in `dir/file1.rs`
    fn build_graph(
        dir: &str,
        file_count: usize,
        internal_call_count: usize,
        external_call_count: usize,
    ) -> crate::graph::CodeGraph {
        let mut builder = GraphBuilder::new();

        // Create file_count files in the same directory
        for i in 0..file_count {
            let path = format!("{}/file{}.rs", dir, i);
            builder.add_node(CodeNode::file(&path));
        }

        // The target is the first file: dir/file0.rs
        let target_file = format!("{}/file0.rs", dir);

        // Add internal functions (in target file) and wire internal calls
        for j in 0..internal_call_count {
            let a_name = format!("internal_a{}", j);
            let b_name = format!("internal_b{}", j);
            // qualified_name = "file_path::func_name" by CodeNode::new convention
            let a_qn = format!("{}::{}", target_file, a_name);
            let b_qn = format!("{}::{}", target_file, b_name);
            builder.add_node(CodeNode::function(&a_name, &target_file));
            builder.add_node(CodeNode::function(&b_name, &target_file));
            builder.add_edge_by_name(&a_qn, &b_qn, CodeEdge::calls());
        }

        // Add external functions (in file1) and wire external calls from target
        let external_file = format!("{}/file1.rs", dir);
        let caller_qn = format!("{}::pass_caller", target_file);
        builder.add_node(CodeNode::function("pass_caller", &target_file));

        for k in 0..external_call_count {
            let ext_func_name = format!("ext_func{}", k);
            let ext_qn = format!("{}::{}", external_file, ext_func_name);
            builder.add_node(CodeNode::function(&ext_func_name, &external_file));
            builder.add_edge_by_name(&caller_qn, &ext_qn, CodeEdge::calls());
        }

        builder.freeze()
    }

    #[test]
    fn test_pass_through_flagged() {
        // File with 0 internal calls, 10 external calls, in a 5-file module → Medium
        let store = build_graph("src/mymodule", 5, 0, 10);
        let ctx = AnalysisContext::test(&store);
        let detector = ModuleCohesionDetector::new();
        let findings = detector.detect(&ctx).unwrap();

        assert_eq!(
            findings.len(),
            1,
            "Expected exactly 1 finding, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
        assert_eq!(findings[0].severity, Severity::Medium);
        assert!(
            findings[0].title.contains("Pass-Through Module"),
            "title: {}",
            findings[0].title
        );
        assert!(
            findings[0].description.contains("0 internal calls"),
            "desc: {}",
            findings[0].description
        );
        assert!(
            findings[0].description.contains("10 external calls"),
            "desc: {}",
            findings[0].description
        );
    }

    #[test]
    fn test_file_with_internal_calls_not_flagged() {
        // File with 1 internal call and 10 external calls → not flagged
        let store = build_graph("src/mymodule", 5, 1, 10);
        let ctx = AnalysisContext::test(&store);
        let detector = ModuleCohesionDetector::new();
        let findings = detector.detect(&ctx).unwrap();

        // The target file has internal calls, so it should NOT be flagged
        let target = "src/mymodule/file0.rs";
        assert!(
            findings.iter().all(|f| !f.title.contains(target)),
            "File with internal calls should not be flagged, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_small_module_not_flagged() {
        // File with 0 internal calls, 10 external calls, but only 2 files in module → not flagged
        let store = build_graph("src/smallmod", 2, 0, 10);
        let ctx = AnalysisContext::test(&store);
        let detector = ModuleCohesionDetector::new();
        let findings = detector.detect(&ctx).unwrap();

        assert!(
            findings.is_empty(),
            "Small module (< 5 files) should not be flagged, got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_low_external_calls_not_flagged() {
        // File with 0 internal calls but only 3 external calls → not flagged (below threshold of 5)
        let store = build_graph("src/mymodule", 5, 0, 3);
        let ctx = AnalysisContext::test(&store);
        let detector = ModuleCohesionDetector::new();
        let findings = detector.detect(&ctx).unwrap();

        assert!(
            findings.is_empty(),
            "File with fewer than 5 external calls should not be flagged"
        );
    }

    #[test]
    fn test_low_severity_five_to_nine_external() {
        // File with 0 internal calls, exactly 5 external calls, 5-file module → Low severity
        let store = build_graph("src/mymodule", 5, 0, 5);
        let ctx = AnalysisContext::test(&store);
        let detector = ModuleCohesionDetector::new();
        let findings = detector.detect(&ctx).unwrap();

        assert_eq!(findings.len(), 1, "Expected exactly 1 finding");
        assert_eq!(findings[0].severity, Severity::Low);
    }
}
