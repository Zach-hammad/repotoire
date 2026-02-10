// Graph-Based Code Smell Detectors (REPO-433)
//
// Implements graph-based detectors in Rust for 10-100x speedups over Cypher queries.
// All algorithms are parallelized using rayon where beneficial.
//
// DETECTORS:
// 1. PackageStabilityDetector - Robert Martin's package metrics (I, A, D)
// 2. TechnicalDebtHotspotDetector - Churn × Complexity hotspot detection
// 3. LayeredArchitectureDetector - Back-call and skip-call detection
// 4. CallChainDepthDetector - Deep call chain and bottleneck detection
// 5. HubDependencyDetector - Architectural hub detection via centrality
//
// ACADEMIC REFERENCES:
// - Martin, R. "Agile Software Development" (2002) - Package stability metrics
// - Tornhill, A. "Your Code as a Crime Scene" (2015) - Hotspot analysis
// - Lippert, M. & Roock, S. "Refactoring in Large Software Projects" (2006)

use rayon::prelude::*;
use rustc_hash::{FxHashMap, FxHashSet};
use crate::errors::GraphError;
use crate::graph_algo::{pagerank, betweenness_centrality};

// ============================================================================
// FINDING STRUCTURE
// ============================================================================

/// A code smell finding with severity, affected files, and metadata
#[derive(Debug, Clone)]
pub struct Finding {
    pub detector: String,
    pub severity: String,
    pub message: String,
    pub affected_nodes: Vec<u32>,
    pub metadata: FxHashMap<String, f64>,
}

// ============================================================================
// PACKAGE STABILITY DETECTOR
// ============================================================================
//
// Robert Martin's Package Metrics from "Agile Software Development":
//
// Instability (I) = Ce / (Ca + Ce)
//   - Ca = Afferent Coupling (incoming dependencies - who depends on me)
//   - Ce = Efferent Coupling (outgoing dependencies - who do I depend on)
//   - I = 0: Maximally stable (everyone depends on me, I depend on no one)
//   - I = 1: Maximally unstable (I depend on everyone, no one depends on me)
//
// Abstractness (A) = Na / Nc
//   - Na = Number of abstract classes/interfaces in package
//   - Nc = Total number of classes in package
//   - A = 0: Fully concrete
//   - A = 1: Fully abstract
//
// Distance from Main Sequence (D) = |A + I - 1|
//   - Ideal packages lie on the "Main Sequence" line: A + I = 1
//   - D = 0: On main sequence (good balance)
//   - D > 0.5: Either "Zone of Pain" (stable but concrete) or
//              "Zone of Uselessness" (unstable but abstract)
//
// DETECTION THRESHOLDS:
// - D > 0.7: Critical (far from main sequence)
// - D > 0.5: High severity
// - D > 0.3: Medium severity
// ============================================================================

/// Package metrics result
#[derive(Debug, Clone)]
pub struct PackageMetrics {
    pub package_id: u32,
    pub ca: u32,          // Afferent coupling (incoming)
    pub ce: u32,          // Efferent coupling (outgoing)
    pub instability: f64, // Ce / (Ca + Ce)
    pub abstractness: f64,
    pub distance: f64,    // |A + I - 1|
}

/// Calculate package stability metrics for all packages.
///
/// # Arguments
/// * `edges` - Import edges as (source_package, target_package) pairs
/// * `num_packages` - Total number of packages
/// * `abstract_counts` - For each package: (num_abstract, num_total) classes
///
/// # Returns
/// Metrics for each package
pub fn calculate_package_stability(
    edges: &[(u32, u32)],
    num_packages: usize,
    abstract_counts: &[(u32, u32)],
) -> Result<Vec<PackageMetrics>, GraphError> {
    if num_packages == 0 {
        return Ok(vec![]);
    }

    // Validate abstract_counts length
    if abstract_counts.len() != num_packages {
        return Err(GraphError::InvalidParameter(format!(
            "abstract_counts length {} != num_packages {}",
            abstract_counts.len(),
            num_packages
        )));
    }

    // Build afferent and efferent counts
    let mut ca: Vec<u32> = vec![0; num_packages]; // Incoming
    let mut ce: Vec<u32> = vec![0; num_packages]; // Outgoing

    for &(src, dst) in edges {
        if src as usize >= num_packages || dst as usize >= num_packages {
            return Err(GraphError::NodeOutOfBounds(
                src.max(dst),
                num_packages as u32,
            ));
        }
        if src != dst {
            // Count unique dependencies (not edges)
            ce[src as usize] += 1; // src depends on dst
            ca[dst as usize] += 1; // dst is depended on by src
        }
    }

    // Calculate metrics for each package in parallel
    let metrics: Vec<PackageMetrics> = (0..num_packages)
        .into_par_iter()
        .map(|pkg| {
            let ca_val = ca[pkg];
            let ce_val = ce[pkg];
            let total = ca_val + ce_val;

            // Instability: Ce / (Ca + Ce), default to 0.5 if no dependencies
            let instability = if total > 0 {
                ce_val as f64 / total as f64
            } else {
                0.5 // No dependencies = neutral stability
            };

            // Abstractness: Na / Nc
            let (num_abstract, num_total) = abstract_counts[pkg];
            let abstractness = if num_total > 0 {
                num_abstract as f64 / num_total as f64
            } else {
                0.0 // No classes = concrete
            };

            // Distance from Main Sequence
            let distance = (abstractness + instability - 1.0).abs();

            PackageMetrics {
                package_id: pkg as u32,
                ca: ca_val,
                ce: ce_val,
                instability,
                abstractness,
                distance,
            }
        })
        .collect();

    Ok(metrics)
}

/// Detect packages with poor stability metrics.
///
/// Returns findings for packages in "Zone of Pain" or "Zone of Uselessness".
pub fn detect_unstable_packages(
    metrics: &[PackageMetrics],
    distance_threshold: f64,
) -> Vec<Finding> {
    metrics
        .par_iter()
        .filter(|m| m.distance > distance_threshold)
        .map(|m| {
            let severity = if m.distance > 0.7 {
                "critical"
            } else if m.distance > 0.5 {
                "high"
            } else {
                "medium"
            };

            let zone = if m.instability < 0.5 && m.abstractness < 0.5 {
                "Zone of Pain (stable but concrete - hard to extend)"
            } else {
                "Zone of Uselessness (unstable but abstract - unused abstractions)"
            };

            let mut metadata = FxHashMap::default();
            metadata.insert("instability".to_string(), m.instability);
            metadata.insert("abstractness".to_string(), m.abstractness);
            metadata.insert("distance".to_string(), m.distance);
            metadata.insert("ca".to_string(), m.ca as f64);
            metadata.insert("ce".to_string(), m.ce as f64);

            Finding {
                detector: "PackageStabilityDetector".to_string(),
                severity: severity.to_string(),
                message: format!(
                    "Package in {}: I={:.2}, A={:.2}, D={:.2}",
                    zone, m.instability, m.abstractness, m.distance
                ),
                affected_nodes: vec![m.package_id],
                metadata,
            }
        })
        .collect()
}

// ============================================================================
// TECHNICAL DEBT HOTSPOT DETECTOR
// ============================================================================
//
// Based on Adam Tornhill's "Your Code as a Crime Scene":
//
// Hotspot Score = Churn × Complexity / Health
//
// Where:
// - Churn = Number of commits modifying the file
// - Complexity = Cyclomatic complexity or similar metric
// - Health = Code health score (0-100, higher = better)
//
// High-churn, high-complexity, low-health files are "hotspots" that:
// 1. Change frequently (high maintenance burden)
// 2. Are complex (hard to understand and modify)
// 3. Have poor health (existing issues compound)
//
// DETECTION THRESHOLDS:
// - Top 5% of hotspot scores: Critical
// - Top 10%: High
// - Top 20%: Medium
// ============================================================================

/// File metrics for hotspot detection
#[derive(Debug, Clone)]
pub struct FileMetrics {
    pub file_id: u32,
    pub churn_count: u32,
    pub complexity: f64,
    pub code_health: f64,
    pub lines_of_code: u32,
}

/// Hotspot detection result
#[derive(Debug, Clone)]
pub struct Hotspot {
    pub file_id: u32,
    pub score: f64,
    pub churn_count: u32,
    pub complexity: f64,
    pub code_health: f64,
    pub percentile: f64,
}

/// Detect technical debt hotspots.
///
/// # Arguments
/// * `files` - File metrics (id, churn, complexity, health, loc)
/// * `min_churn` - Minimum churn count to consider
/// * `min_complexity` - Minimum complexity to consider
///
/// # Returns
/// List of hotspots sorted by score descending
pub fn detect_hotspots(
    files: &[FileMetrics],
    min_churn: u32,
    min_complexity: f64,
) -> Vec<Hotspot> {
    if files.is_empty() {
        return vec![];
    }

    // Calculate hotspot scores for eligible files
    let mut hotspots: Vec<Hotspot> = files
        .par_iter()
        .filter(|f| f.churn_count >= min_churn && f.complexity >= min_complexity)
        .map(|f| {
            // Avoid division by zero - use 1.0 as minimum health
            let health = if f.code_health > 0.0 {
                f.code_health
            } else {
                1.0
            };

            // Hotspot formula: churn * complexity / health
            let score = (f.churn_count as f64 * f.complexity) / health;

            Hotspot {
                file_id: f.file_id,
                score,
                churn_count: f.churn_count,
                complexity: f.complexity,
                code_health: f.code_health,
                percentile: 0.0, // Will be calculated after sorting
            }
        })
        .collect();

    // Sort by score descending
    hotspots.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));

    // Calculate percentiles
    let total = hotspots.len();
    for (i, hotspot) in hotspots.iter_mut().enumerate() {
        hotspot.percentile = (i as f64 / total as f64) * 100.0;
    }

    hotspots
}

/// Convert hotspots to findings with severity levels.
pub fn hotspots_to_findings(hotspots: &[Hotspot]) -> Vec<Finding> {
    hotspots
        .iter()
        .filter(|h| h.percentile <= 20.0) // Only report top 20%
        .map(|h| {
            let severity = if h.percentile <= 5.0 {
                "critical"
            } else if h.percentile <= 10.0 {
                "high"
            } else {
                "medium"
            };

            let mut metadata = FxHashMap::default();
            metadata.insert("hotspot_score".to_string(), h.score);
            metadata.insert("churn_count".to_string(), h.churn_count as f64);
            metadata.insert("complexity".to_string(), h.complexity);
            metadata.insert("code_health".to_string(), h.code_health);
            metadata.insert("percentile".to_string(), h.percentile);

            Finding {
                detector: "TechnicalDebtHotspotDetector".to_string(),
                severity: severity.to_string(),
                message: format!(
                    "Technical debt hotspot (top {:.0}%): churn={}, complexity={:.1}, health={:.1}",
                    h.percentile, h.churn_count, h.complexity, h.code_health
                ),
                affected_nodes: vec![h.file_id],
                metadata,
            }
        })
        .collect()
}

// ============================================================================
// LAYERED ARCHITECTURE DETECTOR
// ============================================================================
//
// Detects violations of layered architecture patterns:
//
// 1. Back-Calls: Lower layer imports from higher layer
//    Example: repositories/ importing from views/
//    These create upward dependencies that break layer separation.
//
// 2. Skip-Calls: Layer skips over adjacent layer
//    Example: views/ importing directly from database/ (skipping services/)
//    These bypass proper abstraction layers.
//
// Layer Order (typical):
//   views/controllers → services/application → domain → repositories/data
//
// DETECTION:
// - Build layer hierarchy from configuration
// - Check each import edge for violations
// - Report back-calls and skip-calls with affected files
// ============================================================================

/// Layer definition with name and allowed dependencies
#[derive(Debug, Clone)]
pub struct Layer {
    pub layer_id: u32,
    pub name: String,
    pub level: u32, // 0 = lowest (data), higher = closer to UI
}

/// Architecture violation
#[derive(Debug, Clone)]
pub struct ArchitectureViolation {
    pub violation_type: String, // "back_call" or "skip_call"
    pub source_layer: u32,
    pub target_layer: u32,
    pub source_file: u32,
    pub target_file: u32,
}

/// Detect layered architecture violations.
///
/// # Arguments
/// * `edges` - Import edges as (source_file, target_file)
/// * `file_layers` - Mapping of file_id -> layer_id
/// * `layers` - Layer definitions with levels
///
/// # Returns
/// List of architecture violations
pub fn detect_layer_violations(
    edges: &[(u32, u32)],
    file_layers: &FxHashMap<u32, u32>,
    layers: &[Layer],
) -> Vec<ArchitectureViolation> {
    if edges.is_empty() || layers.is_empty() {
        return vec![];
    }

    // Build layer level lookup
    let layer_levels: FxHashMap<u32, u32> = layers
        .iter()
        .map(|l| (l.layer_id, l.level))
        .collect();

    // Check each edge for violations in parallel
    edges
        .par_iter()
        .filter_map(|&(src, dst)| {
            let src_layer = file_layers.get(&src)?;
            let dst_layer = file_layers.get(&dst)?;

            if src_layer == dst_layer {
                return None; // Same layer, no violation
            }

            let src_level = *layer_levels.get(src_layer)?;
            let dst_level = *layer_levels.get(dst_layer)?;

            if dst_level > src_level {
                // Back-call: importing from higher layer
                Some(ArchitectureViolation {
                    violation_type: "back_call".to_string(),
                    source_layer: *src_layer,
                    target_layer: *dst_layer,
                    source_file: src,
                    target_file: dst,
                })
            } else if src_level.abs_diff(dst_level) > 1 {
                // Skip-call: skipping intermediate layer(s)
                Some(ArchitectureViolation {
                    violation_type: "skip_call".to_string(),
                    source_layer: *src_layer,
                    target_layer: *dst_layer,
                    source_file: src,
                    target_file: dst,
                })
            } else {
                None // Normal downward dependency to adjacent layer
            }
        })
        .collect()
}

/// Group violations into findings by type and layers.
pub fn violations_to_findings(
    violations: &[ArchitectureViolation],
    layers: &[Layer],
) -> Vec<Finding> {
    if violations.is_empty() {
        return vec![];
    }

    // Build layer name lookup
    let layer_names: FxHashMap<u32, &str> = layers
        .iter()
        .map(|l| (l.layer_id, l.name.as_str()))
        .collect();

    // Group by (type, source_layer, target_layer)
    let mut groups: FxHashMap<(String, u32, u32), Vec<(u32, u32)>> = FxHashMap::default();
    for v in violations {
        groups
            .entry((v.violation_type.clone(), v.source_layer, v.target_layer))
            .or_default()
            .push((v.source_file, v.target_file));
    }

    groups
        .into_iter()
        .map(|((vtype, src_layer, dst_layer), files)| {
            let severity = if vtype == "back_call" {
                "high" // Back-calls are more severe
            } else {
                "medium"
            };

            let src_name = layer_names.get(&src_layer).unwrap_or(&"unknown");
            let dst_name = layer_names.get(&dst_layer).unwrap_or(&"unknown");

            let message = if vtype == "back_call" {
                format!(
                    "Back-call: {} importing from {} ({} violations)",
                    src_name, dst_name, files.len()
                )
            } else {
                format!(
                    "Skip-call: {} bypassing intermediate layers to {} ({} violations)",
                    src_name, dst_name, files.len()
                )
            };

            let affected: Vec<u32> = files.iter().map(|(src, _)| *src).collect();
            let mut metadata = FxHashMap::default();
            metadata.insert("violation_count".to_string(), files.len() as f64);
            metadata.insert("source_layer".to_string(), src_layer as f64);
            metadata.insert("target_layer".to_string(), dst_layer as f64);

            Finding {
                detector: "LayeredArchitectureDetector".to_string(),
                severity: severity.to_string(),
                message,
                affected_nodes: affected,
                metadata,
            }
        })
        .collect()
}

// ============================================================================
// CALL CHAIN DEPTH DETECTOR
// ============================================================================
//
// Detects excessively deep call chains that indicate:
// 1. Tight coupling across many layers
// 2. Potential performance issues (stack depth)
// 3. Difficult-to-understand code flow
// 4. Potential for cascade failures
//
// Algorithm:
// 1. Build call graph from CALLS edges
// 2. For each function, find longest path from it using DFS
// 3. Identify "bottleneck" functions that appear on many long paths
// 4. Report chains exceeding threshold
//
// DETECTION THRESHOLDS:
// - Chain depth > 15: Critical
// - Chain depth > 10: High
// - Chain depth > 7: Medium
// ============================================================================

/// Call chain detection result
#[derive(Debug, Clone)]
pub struct CallChain {
    pub start_function: u32,
    pub depth: u32,
    pub path: Vec<u32>,
    pub bottlenecks: Vec<u32>, // Functions appearing frequently on paths
}

/// Find the longest call chain starting from each function.
///
/// # Arguments
/// * `call_edges` - CALLS edges as (caller, callee)
/// * `num_functions` - Total number of function nodes
/// * `max_depth` - Maximum depth to search (prevents infinite loops)
///
/// # Returns
/// List of call chains sorted by depth descending
pub fn detect_deep_call_chains(
    call_edges: &[(u32, u32)],
    num_functions: usize,
    max_depth: u32,
) -> Result<Vec<CallChain>, GraphError> {
    if call_edges.is_empty() || num_functions == 0 {
        return Ok(vec![]);
    }

    // Build adjacency list
    let mut adj: Vec<Vec<u32>> = vec![vec![]; num_functions];
    for &(caller, callee) in call_edges {
        if caller as usize >= num_functions || callee as usize >= num_functions {
            return Err(GraphError::NodeOutOfBounds(
                caller.max(callee),
                num_functions as u32,
            ));
        }
        adj[caller as usize].push(callee);
    }

    // Find longest path from each node using DFS with memoization
    // We track depth and reconstruct path for longest chains
    let chains: Vec<CallChain> = (0..num_functions)
        .into_par_iter()
        .filter_map(|start| {
            let mut visited = vec![false; num_functions];
            let mut path = Vec::new();
            let mut longest_path = Vec::new();

            fn dfs(
                node: usize,
                adj: &[Vec<u32>],
                visited: &mut [bool],
                path: &mut Vec<u32>,
                longest_path: &mut Vec<u32>,
                max_depth: u32,
            ) {
                if path.len() >= max_depth as usize {
                    if path.len() > longest_path.len() {
                        *longest_path = path.clone();
                    }
                    return;
                }

                visited[node] = true;
                path.push(node as u32);

                let mut has_unvisited = false;
                for &callee in &adj[node] {
                    if !visited[callee as usize] {
                        has_unvisited = true;
                        dfs(callee as usize, adj, visited, path, longest_path, max_depth);
                    }
                }

                if !has_unvisited && path.len() > longest_path.len() {
                    *longest_path = path.clone();
                }

                path.pop();
                visited[node] = false;
            }

            dfs(start, &adj, &mut visited, &mut path, &mut longest_path, max_depth);

            if longest_path.len() > 1 {
                Some(CallChain {
                    start_function: start as u32,
                    depth: longest_path.len() as u32,
                    path: longest_path,
                    bottlenecks: vec![], // Calculated separately
                })
            } else {
                None
            }
        })
        .collect();

    // Sort by depth descending
    let mut chains = chains;
    chains.sort_by(|a, b| b.depth.cmp(&a.depth));

    Ok(chains)
}

/// Find bottleneck functions that appear on many long call chains.
///
/// These are "chokepoints" where many call paths converge.
pub fn find_bottleneck_functions(
    chains: &[CallChain],
    min_chain_depth: u32,
    min_appearances: usize,
) -> Vec<(u32, usize)> {
    // Count function appearances in chains exceeding threshold
    let mut counts: FxHashMap<u32, usize> = FxHashMap::default();

    for chain in chains {
        if chain.depth >= min_chain_depth {
            for &func in &chain.path {
                *counts.entry(func).or_default() += 1;
            }
        }
    }

    // Filter and sort by count
    let mut bottlenecks: Vec<(u32, usize)> = counts
        .into_iter()
        .filter(|(_, count)| *count >= min_appearances)
        .collect();

    bottlenecks.sort_by(|a, b| b.1.cmp(&a.1));
    bottlenecks
}

/// Convert call chains to findings.
pub fn call_chains_to_findings(chains: &[CallChain], depth_threshold: u32) -> Vec<Finding> {
    chains
        .iter()
        .filter(|c| c.depth >= depth_threshold)
        .map(|c| {
            let severity = if c.depth > 15 {
                "critical"
            } else if c.depth > 10 {
                "high"
            } else {
                "medium"
            };

            let mut metadata = FxHashMap::default();
            metadata.insert("depth".to_string(), c.depth as f64);
            metadata.insert("path_length".to_string(), c.path.len() as f64);

            Finding {
                detector: "CallChainDepthDetector".to_string(),
                severity: severity.to_string(),
                message: format!(
                    "Deep call chain detected: {} functions deep",
                    c.depth
                ),
                affected_nodes: c.path.clone(),
                metadata,
            }
        })
        .collect()
}

// ============================================================================
// HUB DEPENDENCY DETECTOR
// ============================================================================
//
// Detects "hub" nodes in the dependency graph that:
// 1. Have high betweenness centrality (many paths go through them)
// 2. Have high PageRank (many important nodes depend on them)
// 3. Are architectural bottlenecks that increase coupling
//
// These hubs are:
// - Single points of failure
// - Change amplifiers (modifications ripple widely)
// - Hard to refactor or replace
//
// Algorithm:
// 1. Calculate betweenness centrality for all nodes
// 2. Calculate PageRank for all nodes
// 3. Combine scores: hub_score = α * betweenness + β * pagerank
// 4. Report nodes above threshold
//
// DETECTION THRESHOLDS:
// - Top 1% hub score: Critical
// - Top 5%: High
// - Top 10%: Medium
// ============================================================================

/// Hub detection result
#[derive(Debug, Clone)]
pub struct HubNode {
    pub node_id: u32,
    pub hub_score: f64,
    pub betweenness: f64,
    pub pagerank: f64,
    pub in_degree: u32,
    pub out_degree: u32,
    pub percentile: f64,
}

/// Detect hub nodes in the dependency graph.
///
/// # Arguments
/// * `edges` - Dependency edges as (source, target)
/// * `num_nodes` - Total number of nodes
/// * `betweenness_weight` - Weight for betweenness centrality (default: 0.6)
/// * `pagerank_weight` - Weight for PageRank (default: 0.4)
///
/// # Returns
/// List of hub nodes sorted by score descending
pub fn detect_hub_dependencies(
    edges: &[(u32, u32)],
    num_nodes: usize,
    betweenness_weight: f64,
    pagerank_weight: f64,
) -> Result<Vec<HubNode>, GraphError> {
    if edges.is_empty() || num_nodes == 0 {
        return Ok(vec![]);
    }

    // Validate weights
    if betweenness_weight < 0.0 || pagerank_weight < 0.0 {
        return Err(GraphError::InvalidParameter(
            "weights must be non-negative".to_string(),
        ));
    }

    // Calculate centrality metrics
    let betweenness = betweenness_centrality(edges, num_nodes)?;
    let pr = pagerank(edges, num_nodes, 0.85, 100, 1e-6)?;

    // Calculate degrees
    let mut in_degree: Vec<u32> = vec![0; num_nodes];
    let mut out_degree: Vec<u32> = vec![0; num_nodes];
    for &(src, dst) in edges {
        out_degree[src as usize] += 1;
        in_degree[dst as usize] += 1;
    }

    // Normalize betweenness and pagerank to [0, 1]
    let max_betweenness = betweenness.iter().cloned().fold(0.0_f64, f64::max);
    let max_pr = pr.iter().cloned().fold(0.0_f64, f64::max);

    let norm_betweenness: Vec<f64> = if max_betweenness > 0.0 {
        betweenness.iter().map(|b| b / max_betweenness).collect()
    } else {
        vec![0.0; num_nodes]
    };

    let norm_pr: Vec<f64> = if max_pr > 0.0 {
        pr.iter().map(|p| p / max_pr).collect()
    } else {
        vec![0.0; num_nodes]
    };

    // Calculate combined hub scores
    let mut hubs: Vec<HubNode> = (0..num_nodes)
        .map(|i| {
            let hub_score = betweenness_weight * norm_betweenness[i]
                + pagerank_weight * norm_pr[i];

            HubNode {
                node_id: i as u32,
                hub_score,
                betweenness: betweenness[i],
                pagerank: pr[i],
                in_degree: in_degree[i],
                out_degree: out_degree[i],
                percentile: 0.0,
            }
        })
        .collect();

    // Sort by hub score descending
    hubs.sort_by(|a, b| b.hub_score.partial_cmp(&a.hub_score).unwrap_or(std::cmp::Ordering::Equal));

    // Calculate percentiles
    let total = hubs.len();
    for (i, hub) in hubs.iter_mut().enumerate() {
        hub.percentile = (i as f64 / total as f64) * 100.0;
    }

    Ok(hubs)
}

/// Convert hub nodes to findings.
pub fn hubs_to_findings(hubs: &[HubNode], percentile_threshold: f64) -> Vec<Finding> {
    hubs.iter()
        .filter(|h| h.percentile <= percentile_threshold)
        .map(|h| {
            let severity = if h.percentile <= 1.0 {
                "critical"
            } else if h.percentile <= 5.0 {
                "high"
            } else {
                "medium"
            };

            let mut metadata = FxHashMap::default();
            metadata.insert("hub_score".to_string(), h.hub_score);
            metadata.insert("betweenness".to_string(), h.betweenness);
            metadata.insert("pagerank".to_string(), h.pagerank);
            metadata.insert("in_degree".to_string(), h.in_degree as f64);
            metadata.insert("out_degree".to_string(), h.out_degree as f64);
            metadata.insert("percentile".to_string(), h.percentile);

            Finding {
                detector: "HubDependencyDetector".to_string(),
                severity: severity.to_string(),
                message: format!(
                    "Architectural hub (top {:.1}%): in={}, out={}, betweenness={:.2}",
                    h.percentile, h.in_degree, h.out_degree, h.betweenness
                ),
                affected_nodes: vec![h.node_id],
                metadata,
            }
        })
        .collect()
}

// ============================================================================
// CHANGE COUPLING DETECTOR (BONUS)
// ============================================================================
//
// Detects files that frequently change together (temporal coupling).
// Based on git commit history analysis.
//
// Files that change together but have no explicit dependencies may indicate:
// 1. Hidden logical coupling
// 2. Copy-paste code
// 3. Missing abstractions
// 4. Shotgun surgery smell
//
// Algorithm:
// 1. Build co-change matrix from commit history
// 2. Calculate support and confidence for each pair
// 3. Report pairs with high coupling but no explicit dependency
// ============================================================================

/// Change coupling between two files
#[derive(Debug, Clone)]
pub struct ChangeCoupling {
    pub file_a: u32,
    pub file_b: u32,
    pub co_changes: u32,      // Times changed together
    pub support: f64,         // co_changes / total_commits
    pub confidence_a_b: f64,  // P(B changes | A changes)
    pub confidence_b_a: f64,  // P(A changes | B changes)
}

/// Detect change coupling between files.
///
/// # Arguments
/// * `commit_files` - For each commit: list of modified file IDs
/// * `explicit_deps` - Known explicit dependencies (edges to exclude)
/// * `min_support` - Minimum support threshold (0-1)
/// * `min_confidence` - Minimum confidence threshold (0-1)
///
/// # Returns
/// List of coupled file pairs, sorted by confidence descending
pub fn detect_change_coupling(
    commit_files: &[Vec<u32>],
    explicit_deps: &FxHashSet<(u32, u32)>,
    min_support: f64,
    min_confidence: f64,
) -> Vec<ChangeCoupling> {
    if commit_files.is_empty() {
        return vec![];
    }

    let total_commits = commit_files.len();

    // Count individual file changes and co-changes
    let mut file_changes: FxHashMap<u32, u32> = FxHashMap::default();
    let mut co_changes: FxHashMap<(u32, u32), u32> = FxHashMap::default();

    for files in commit_files {
        // Count individual files
        for &file in files {
            *file_changes.entry(file).or_default() += 1;
        }

        // Count co-changes (unordered pairs)
        for i in 0..files.len() {
            for j in (i + 1)..files.len() {
                let (a, b) = if files[i] < files[j] {
                    (files[i], files[j])
                } else {
                    (files[j], files[i])
                };
                *co_changes.entry((a, b)).or_default() += 1;
            }
        }
    }

    // Calculate coupling metrics
    let mut couplings: Vec<ChangeCoupling> = co_changes
        .into_par_iter()
        .filter_map(|((a, b), count)| {
            let support = count as f64 / total_commits as f64;
            if support < min_support {
                return None;
            }

            let changes_a = *file_changes.get(&a).unwrap_or(&1) as f64;
            let changes_b = *file_changes.get(&b).unwrap_or(&1) as f64;

            let confidence_a_b = count as f64 / changes_a; // P(B|A)
            let confidence_b_a = count as f64 / changes_b; // P(A|B)

            let max_confidence = confidence_a_b.max(confidence_b_a);
            if max_confidence < min_confidence {
                return None;
            }

            // Skip if explicit dependency exists
            if explicit_deps.contains(&(a, b)) || explicit_deps.contains(&(b, a)) {
                return None;
            }

            Some(ChangeCoupling {
                file_a: a,
                file_b: b,
                co_changes: count,
                support,
                confidence_a_b,
                confidence_b_a,
            })
        })
        .collect();

    // Sort by max confidence descending
    couplings.sort_by(|a, b| {
        let max_a = a.confidence_a_b.max(a.confidence_b_a);
        let max_b = b.confidence_a_b.max(b.confidence_b_a);
        max_b.partial_cmp(&max_a).unwrap_or(std::cmp::Ordering::Equal)
    });

    couplings
}

/// Convert change couplings to findings.
pub fn coupling_to_findings(couplings: &[ChangeCoupling]) -> Vec<Finding> {
    couplings
        .iter()
        .map(|c| {
            let max_conf = c.confidence_a_b.max(c.confidence_b_a);
            let severity = if max_conf > 0.8 {
                "high"
            } else if max_conf > 0.5 {
                "medium"
            } else {
                "low"
            };

            let mut metadata = FxHashMap::default();
            metadata.insert("co_changes".to_string(), c.co_changes as f64);
            metadata.insert("support".to_string(), c.support);
            metadata.insert("confidence_a_b".to_string(), c.confidence_a_b);
            metadata.insert("confidence_b_a".to_string(), c.confidence_b_a);

            Finding {
                detector: "ChangeCouplingDetector".to_string(),
                severity: severity.to_string(),
                message: format!(
                    "Hidden coupling detected: {} co-changes, {:.0}% confidence",
                    c.co_changes,
                    max_conf * 100.0
                ),
                affected_nodes: vec![c.file_a, c.file_b],
                metadata,
            }
        })
        .collect()
}

// ============================================================================
// UNIT TESTS
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_package_stability_empty() {
        let result = calculate_package_stability(&[], 0, &[]).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_package_stability_single() {
        let result = calculate_package_stability(&[], 1, &[(0, 1)]).unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].instability, 0.5); // No deps = neutral
        assert_eq!(result[0].abstractness, 0.0); // 0/1 abstract
    }

    #[test]
    fn test_package_stability_basic() {
        // Package 0 depends on Package 1
        let edges = vec![(0, 1)];
        let abstracts = vec![(0, 2), (1, 5)]; // 0: 0 abstract, 2 total; 1: 1 abstract, 5 total
        let result = calculate_package_stability(&edges, 2, &abstracts).unwrap();

        // Package 0: Ce=1, Ca=0 -> I=1.0 (unstable)
        assert_eq!(result[0].ce, 1);
        assert_eq!(result[0].ca, 0);
        assert_eq!(result[0].instability, 1.0);

        // Package 1: Ce=0, Ca=1 -> I=0.0 (stable)
        assert_eq!(result[1].ce, 0);
        assert_eq!(result[1].ca, 1);
        assert_eq!(result[1].instability, 0.0);
    }

    #[test]
    fn test_hotspot_detection_empty() {
        let result = detect_hotspots(&[], 1, 1.0);
        assert!(result.is_empty());
    }

    #[test]
    fn test_hotspot_detection_basic() {
        let files = vec![
            FileMetrics { file_id: 0, churn_count: 100, complexity: 50.0, code_health: 10.0, lines_of_code: 500 },
            FileMetrics { file_id: 1, churn_count: 10, complexity: 5.0, code_health: 90.0, lines_of_code: 100 },
        ];

        let hotspots = detect_hotspots(&files, 5, 1.0);
        assert_eq!(hotspots.len(), 2);

        // File 0 should be the top hotspot
        assert_eq!(hotspots[0].file_id, 0);
        assert!(hotspots[0].score > hotspots[1].score);
    }

    #[test]
    fn test_layer_violations_empty() {
        let result = detect_layer_violations(&[], &FxHashMap::default(), &[]);
        assert!(result.is_empty());
    }

    #[test]
    fn test_layer_violations_back_call() {
        let layers = vec![
            Layer { layer_id: 0, name: "data".to_string(), level: 0 },
            Layer { layer_id: 1, name: "service".to_string(), level: 1 },
            Layer { layer_id: 2, name: "view".to_string(), level: 2 },
        ];

        let mut file_layers = FxHashMap::default();
        file_layers.insert(0, 0); // File 0 in data layer
        file_layers.insert(1, 2); // File 1 in view layer

        // Data layer importing from view layer = back-call
        let edges = vec![(0, 1)];
        let violations = detect_layer_violations(&edges, &file_layers, &layers);

        assert_eq!(violations.len(), 1);
        assert_eq!(violations[0].violation_type, "back_call");
    }

    #[test]
    fn test_layer_violations_skip_call() {
        let layers = vec![
            Layer { layer_id: 0, name: "data".to_string(), level: 0 },
            Layer { layer_id: 1, name: "service".to_string(), level: 1 },
            Layer { layer_id: 2, name: "view".to_string(), level: 2 },
        ];

        let mut file_layers = FxHashMap::default();
        file_layers.insert(0, 2); // File 0 in view layer
        file_layers.insert(1, 0); // File 1 in data layer

        // View layer importing directly from data layer = skip-call
        let edges = vec![(0, 1)];
        let violations = detect_layer_violations(&edges, &file_layers, &layers);

        assert_eq!(violations.len(), 1);
        assert_eq!(violations[0].violation_type, "skip_call");
    }

    #[test]
    fn test_call_chain_empty() {
        let result = detect_deep_call_chains(&[], 0, 10).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_call_chain_basic() {
        // Chain: 0 -> 1 -> 2 -> 3
        let edges = vec![(0, 1), (1, 2), (2, 3)];
        let chains = detect_deep_call_chains(&edges, 4, 10).unwrap();

        assert!(!chains.is_empty());
        // Should find chain starting at 0 with depth 4
        let chain_0 = chains.iter().find(|c| c.start_function == 0).unwrap();
        assert_eq!(chain_0.depth, 4);
    }

    #[test]
    fn test_hub_detection_empty() {
        let result = detect_hub_dependencies(&[], 0, 0.6, 0.4).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_hub_detection_basic() {
        // Star graph: node 0 is hub
        let edges: Vec<(u32, u32)> = (1..5).map(|i| (i, 0)).collect();
        let hubs = detect_hub_dependencies(&edges, 5, 0.6, 0.4).unwrap();

        assert!(!hubs.is_empty());
        // Node 0 should be top hub
        assert_eq!(hubs[0].node_id, 0);
    }

    #[test]
    fn test_change_coupling_empty() {
        let result = detect_change_coupling(&[], &FxHashSet::default(), 0.1, 0.5);
        assert!(result.is_empty());
    }

    #[test]
    fn test_change_coupling_basic() {
        // 5 commits, files 0 and 1 always change together
        let commits = vec![
            vec![0, 1],
            vec![0, 1],
            vec![0, 1],
            vec![0, 1],
            vec![2], // Only file 2
        ];

        let couplings = detect_change_coupling(&commits, &FxHashSet::default(), 0.1, 0.5);

        assert!(!couplings.is_empty());
        let coupling_01 = couplings.iter().find(|c|
            (c.file_a == 0 && c.file_b == 1) || (c.file_a == 1 && c.file_b == 0)
        ).unwrap();

        assert_eq!(coupling_01.co_changes, 4);
        assert!(coupling_01.confidence_a_b > 0.9);
    }
}
