//! Module cohesion detector using Louvain/Leiden community detection
//!
//! Uses community detection algorithms to identify natural module boundaries
//! and detect modularity issues like misplaced files, god modules, and
//! poor overall architecture.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphClient;
use crate::models::{Finding, Severity};
use anyhow::Result;
use rustc_hash::FxHashMap;
use std::collections::HashMap;
use tracing::{debug, info};
use uuid::Uuid;

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
    config: DetectorConfig,
    /// Modularity threshold for "poor"
    modularity_poor: f64,
    /// God module threshold (% of total files)
    god_module_threshold: f64,
    /// Resolution parameter for Louvain
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
            id: Uuid::new_v4().to_string(),
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
        }
    }

    /// Create finding for god module
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
            id: Uuid::new_v4().to_string(),
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
        }
    }

    /// Create finding for misplaced file
    fn create_misplaced_file_finding(
        &self,
        file_path: &str,
        qualified_name: &str,
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
            id: Uuid::new_v4().to_string(),
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
        }
    }

    /// Create finding for high inter-community coupling
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
            id: Uuid::new_v4().to_string(),
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

    fn detect(&self, graph: &GraphClient) -> Result<Vec<Finding>> {
        debug!("Starting module cohesion detection");

        // Get all files
        let files_query = r#"
            MATCH (f:File)
            RETURN f.filePath AS file_path,
                   f.qualifiedName AS qualified_name
            ORDER BY file_path
        "#;
        let files_result = graph.execute(files_query)?;

        if files_result.is_empty() {
            debug!("No files found");
            return Ok(vec![]);
        }

        // Build file index
        let mut file_to_idx: HashMap<String, usize> = HashMap::new();
        let mut file_paths: Vec<String> = Vec::new();
        let mut file_qnames: Vec<String> = Vec::new();

        for (idx, row) in files_result.iter().enumerate() {
            if let Some(path) = row.get("file_path").and_then(|v| v.as_str()) {
                file_to_idx.insert(path.to_string(), idx);
                file_paths.push(path.to_string());
                file_qnames.push(
                    row.get("qualified_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or(path)
                        .to_string(),
                );
            }
        }

        let num_files = file_paths.len();
        debug!("Found {} files", num_files);

        if num_files < 3 {
            debug!("Too few files for community detection");
            return Ok(vec![]);
        }

        // Get import edges
        let imports_query = r#"
            MATCH (f1:File)-[:IMPORTS]->(f2:File)
            RETURN f1.filePath AS src, f2.filePath AS dst
        "#;
        let imports_result = graph.execute(imports_query)?;

        // Build undirected weighted adjacency list
        let mut neighbors: Vec<Vec<(usize, f64)>> = vec![vec![]; num_files];
        let mut total_weight = 0.0;

        for row in imports_result {
            if let (Some(src), Some(dst)) = (
                row.get("src").and_then(|v| v.as_str()),
                row.get("dst").and_then(|v| v.as_str()),
            ) {
                if let (Some(&src_idx), Some(&dst_idx)) =
                    (file_to_idx.get(src), file_to_idx.get(dst))
                {
                    if src_idx != dst_idx {
                        neighbors[src_idx].push((dst_idx, 1.0));
                        neighbors[dst_idx].push((src_idx, 1.0));
                        total_weight += 1.0;
                    }
                }
            }
        }

        if total_weight == 0.0 {
            debug!("No import edges found");
            return Ok(vec![]);
        }

        // Calculate degrees
        let degrees: Vec<f64> = neighbors
            .iter()
            .map(|edges| edges.iter().map(|(_, w)| w).sum())
            .collect();

        // Run Louvain community detection
        let (communities, modularity_score) =
            self.louvain(&neighbors, &degrees, total_weight, num_files);

        // Count communities
        let mut community_sizes: HashMap<u32, usize> = HashMap::new();
        for &c in &communities {
            *community_sizes.entry(c).or_insert(0) += 1;
        }
        let community_count = community_sizes.len();

        info!(
            "Louvain analysis: modularity={:.3}, communities={}",
            modularity_score, community_count
        );

        let mut findings = Vec::new();

        // Check global modularity
        if modularity_score < self.modularity_poor {
            findings.push(self.create_poor_modularity_finding(modularity_score, community_count));
        }

        // Check for god modules
        for (&community_id, &size) in &community_sizes {
            let percentage = (size as f64 / num_files as f64) * 100.0;
            if percentage >= self.god_module_threshold {
                findings.push(self.create_god_module_finding(
                    community_id,
                    size,
                    percentage,
                    num_files,
                ));
            }
        }

        // Check for misplaced files
        for (idx, &community) in communities.iter().enumerate() {
            let mut same_community_imports = 0usize;
            let mut other_community_imports = 0usize;

            for &(neighbor, _) in &neighbors[idx] {
                if communities[neighbor] == community {
                    same_community_imports += 1;
                } else {
                    other_community_imports += 1;
                }
            }

            let total_imports = same_community_imports + other_community_imports;
            if total_imports > 0 {
                let external_ratio = other_community_imports as f64 / total_imports as f64;
                if external_ratio > 0.5 && other_community_imports >= 3 {
                    findings.push(self.create_misplaced_file_finding(
                        &file_paths[idx],
                        &file_qnames[idx],
                        same_community_imports,
                        other_community_imports,
                        external_ratio,
                    ));
                }
            }
        }

        // Check inter-community coupling
        let mut cross_edges: HashMap<(u32, u32), usize> = HashMap::new();
        for (src_idx, src_neighbors) in neighbors.iter().enumerate() {
            let src_comm = communities[src_idx];
            for &(dst_idx, _) in src_neighbors {
                let dst_comm = communities[dst_idx];
                if src_comm != dst_comm {
                    let key = if src_comm < dst_comm {
                        (src_comm, dst_comm)
                    } else {
                        (dst_comm, src_comm)
                    };
                    *cross_edges.entry(key).or_insert(0) += 1;
                }
            }
        }

        let high_coupling: Vec<(u32, u32, usize)> = cross_edges
            .into_iter()
            .filter(|(_, count)| *count >= 10) // At least 10 cross-edges
            .map(|((a, b), c)| (a, b, c))
            .collect();

        if !high_coupling.is_empty() {
            let total_cross = high_coupling.iter().map(|(_, _, c)| c).sum();
            findings.push(self.create_coupling_finding(&high_coupling, total_cross));
        }

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        // Limit findings
        if let Some(max) = self.config.max_findings {
            findings.truncate(max);
        }

        info!("ModuleCohesionDetector found {} findings", findings.len());

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_louvain_simple() {
        let detector = ModuleCohesionDetector::new();

        // Two cliques connected by one edge
        // Clique 1: 0-1-2 (fully connected)
        // Clique 2: 3-4-5 (fully connected)
        // One edge: 2-3
        let neighbors = vec![
            vec![(1, 1.0), (2, 1.0)],           // 0
            vec![(0, 1.0), (2, 1.0)],           // 1
            vec![(0, 1.0), (1, 1.0), (3, 1.0)], // 2
            vec![(2, 1.0), (4, 1.0), (5, 1.0)], // 3
            vec![(3, 1.0), (5, 1.0)],           // 4
            vec![(3, 1.0), (4, 1.0)],           // 5
        ];

        let degrees: Vec<f64> = neighbors
            .iter()
            .map(|n| n.iter().map(|(_, w)| w).sum())
            .collect();
        let total_weight = 7.0; // 6 within-clique + 1 cross-clique

        let (communities, modularity) = detector.louvain(&neighbors, &degrees, total_weight, 6);

        // Should find 2 communities
        let unique_communities: std::collections::HashSet<_> = communities.iter().collect();
        assert!(unique_communities.len() <= 2);

        // Modularity should be positive
        assert!(modularity > 0.0);
    }

    #[test]
    fn test_modularity_calculation() {
        let detector = ModuleCohesionDetector::new();

        // Simple case: 2 disconnected nodes
        let neighbors = vec![vec![], vec![]];
        let degrees = vec![0.0, 0.0];
        let communities = vec![0, 1];

        let modularity = detector.calculate_modularity(&communities, &neighbors, &degrees, 0.0);
        assert_eq!(modularity, 0.0);
    }
}
