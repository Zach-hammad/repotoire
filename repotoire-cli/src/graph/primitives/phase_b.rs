//! Phase B: Weighted overlay graph algorithms using git co-change data.
//!
//! Contains 6 algorithms + OrderedF64 helper computed on the weighted overlay graph:
//! weighted phase orchestrator, overlay builder, weighted PageRank, weighted betweenness,
//! Louvain community detection, modularity.

use std::collections::HashMap;
use petgraph::stable_graph::{NodeIndex, StableGraph};
use petgraph::visit::EdgeRef;
use rayon::prelude::*;
use std::cmp::Reverse;
use std::collections::{BTreeMap, BinaryHeap, HashSet};

use crate::git::co_change::CoChangeMatrix;
use crate::graph::interner::StrKey;
use crate::graph::store_models::{CodeEdge, CodeNode};

// ═══════════════════════════════════════════════════════════════════════════════
// Phase B: Weighted overlay + community detection
// ═══════════════════════════════════════════════════════════════════════════════

/// Phase B: Compute weighted graph algorithms using co-change overlay.
pub(super) fn compute_weighted_phase(
    functions: &[NodeIndex],
    files: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    all_import_edges: &[(NodeIndex, NodeIndex)],
    co_change: &CoChangeMatrix,
    graph: &StableGraph<CodeNode, CodeEdge>,
    edge_fingerprint: u64,
) -> (
    HashMap<NodeIndex, f64>,
    HashMap<NodeIndex, f64>,
    HashMap<NodeIndex, usize>,
    f64,
    Vec<(NodeIndex, NodeIndex, f32, f32, f32)>,
) {
    let (overlay, hidden_coupling) = build_weighted_overlay(
        functions,
        files,
        all_call_edges,
        all_import_edges,
        co_change,
        graph,
    );

    if overlay.node_count() == 0 {
        return (
            HashMap::default(),
            HashMap::default(),
            HashMap::default(),
            0.0,
            hidden_coupling,
        );
    }

    // Run weighted algorithms in parallel
    let (weighted_pr, (weighted_bw, (community, modularity))) = rayon::join(
        || compute_weighted_page_rank(&overlay, 20, 0.85, 1e-6),
        || {
            rayon::join(
                || compute_weighted_betweenness(&overlay, 200, edge_fingerprint),
                || compute_communities(&overlay, 1.0),
            )
        },
    );

    (
        weighted_pr,
        weighted_bw,
        community,
        modularity,
        hidden_coupling,
    )
}

/// Build a temporary weighted overlay graph merging structural edges with
/// co-change weights. Returns `(overlay_graph, hidden_coupling)`.
///
/// The overlay graph has the same node set as the original call/import graph
/// (mapped via `idx_map`). Edge weights combine:
///   - structural_base: 1.0 (Calls), 0.5 (Imports), 1.5 (both)
///   - co_change_boost: min(co_change_weight, 2.0) for files sharing an edge
///
/// Hidden coupling: file pairs with co-change signal but NO structural edges
/// between any of their functions. These get overlay edges with weight =
/// co_change_boost (no structural base).
pub(super) fn build_weighted_overlay(
    functions: &[NodeIndex],
    files: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    all_import_edges: &[(NodeIndex, NodeIndex)],
    co_change: &CoChangeMatrix,
    graph: &StableGraph<CodeNode, CodeEdge>,
) -> (
    StableGraph<NodeIndex, f32>,
    Vec<(NodeIndex, NodeIndex, f32, f32, f32)>,
) {
    // 1. Build idx_map: original NodeIndex → overlay NodeIndex
    let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
    let mut idx_map: HashMap<NodeIndex, NodeIndex> = HashMap::default();

    for &func_idx in functions {
        let overlay_idx = overlay.add_node(func_idx);
        idx_map.insert(func_idx, overlay_idx);
    }

    // 2. Build file_to_functions: StrKey(file_path) → Vec<NodeIndex> (original)
    let mut file_to_functions: HashMap<StrKey, Vec<NodeIndex>> = HashMap::default();
    for &func_idx in functions {
        let node = &graph[func_idx];
        let file_key = node.file_path;
        file_to_functions
            .entry(file_key)
            .or_default()
            .push(func_idx);
    }

    // 3. Process structural edges with deduplication
    //    Track per-(src, tgt) pair what edge types exist so we can combine
    //    Calls + Imports into a single overlay edge with structural_base = 1.5
    let import_set: HashSet<(NodeIndex, NodeIndex)> = all_import_edges.iter().copied().collect();

    // Collect all unique function pairs that have at least one structural edge
    let mut structural_pairs: HashMap<(NodeIndex, NodeIndex), f32> = HashMap::default();

    for &(src, tgt) in all_call_edges {
        // Only include pairs where both endpoints are in our function set
        if !idx_map.contains_key(&src) || !idx_map.contains_key(&tgt) {
            continue;
        }
        let has_import = import_set.contains(&(src, tgt));
        let structural_base = if has_import { 1.5 } else { 1.0 };
        // Use max in case of duplicate edges in the same direction
        let entry = structural_pairs.entry((src, tgt)).or_insert(0.0);
        if structural_base > *entry {
            *entry = structural_base;
        }
    }

    for &(src, tgt) in all_import_edges {
        if !idx_map.contains_key(&src) || !idx_map.contains_key(&tgt) {
            continue;
        }
        // Only add if not already covered by a call edge (which would have set 1.5)
        structural_pairs.entry((src, tgt)).or_insert(0.5);
    }

    // Track which file pairs have structural edges between their functions
    let mut structurally_connected_files: HashSet<(StrKey, StrKey)> = HashSet::new();

    // Add overlay edges for structural pairs, boosted by co-change
    for (&(src, tgt), &structural_base) in &structural_pairs {
        let src_file_key = graph[src].file_path;
        let tgt_file_key = graph[tgt].file_path;

        // Record that these files are structurally connected
        let (lo, hi) = if src_file_key <= tgt_file_key {
            (src_file_key, tgt_file_key)
        } else {
            (tgt_file_key, src_file_key)
        };
        structurally_connected_files.insert((lo, hi));

        // Look up co-change boost between the two files
        let co_change_boost = co_change
            .weight(src_file_key, tgt_file_key)
            .map(|w| w.min(2.0))
            .unwrap_or(0.0);

        let weight = structural_base + co_change_boost;

        let overlay_src = idx_map[&src];
        let overlay_tgt = idx_map[&tgt];
        overlay.add_edge(overlay_src, overlay_tgt, weight);
    }

    // 4a. Extend structural connectivity with containment hierarchy.
    //     Files sharing a parent module (e.g., src/detectors/eval.rs and src/detectors/mod.rs)
    //     are structurally related even without explicit call/import edges.
    //     This is language-agnostic: Rust mod.rs, Python __init__.py, JS index.ts, etc.
    {
        // Group files by parent directory (StrKey of dir path)
        let si = crate::graph::interner::global_interner();
        let mut dir_to_files: HashMap<String, Vec<StrKey>> = HashMap::default();
        for &file_idx in files {
            let node = &graph[file_idx];
            let path = si.resolve(node.file_path);
            if let Some((dir, _)) = path.rsplit_once('/') {
                dir_to_files
                    .entry(dir.to_string())
                    .or_default()
                    .push(node.file_path);
            }
        }

        // For each directory, find the index/mod/init file and connect it to siblings
        for file_keys in dir_to_files.values() {
            // Find aggregator files (mod.rs, __init__.py, index.ts, etc.)
            let aggregators: Vec<StrKey> = file_keys
                .iter()
                .copied()
                .filter(|&k| {
                    let name = si.resolve(k);
                    let basename = name.rsplit('/').next().unwrap_or(name);
                    matches!(
                        basename,
                        "mod.rs"
                            | "__init__.py"
                            | "index.ts"
                            | "index.js"
                            | "index.tsx"
                            | "index.jsx"
                            | "index.mjs"
                            | "mod.go"
                    )
                })
                .collect();

            // Connect each aggregator to all other files in the same directory
            for &agg in &aggregators {
                for &sibling in file_keys {
                    if agg != sibling {
                        let (lo, hi) = if agg <= sibling {
                            (agg, sibling)
                        } else {
                            (sibling, agg)
                        };
                        structurally_connected_files.insert((lo, hi));
                    }
                }
            }
        }
    }

    // 4b. Extend structural connectivity with 2-hop transitive connections.
    //     If file A is structurally connected to file C, and C is connected to B,
    //     then A and B are transitively connected — not "hidden" coupling.
    {
        // Build adjacency: file_key → set of connected file_keys
        let mut adjacency: HashMap<StrKey, HashSet<StrKey>> = HashMap::default();
        for &(lo, hi) in &structurally_connected_files {
            adjacency.entry(lo).or_default().insert(hi);
            adjacency.entry(hi).or_default().insert(lo);
        }

        // For each file, connect all pairs of its 1-hop neighbors (creating 2-hop connections)
        let mut two_hop: Vec<(StrKey, StrKey)> = Vec::new();
        for (&file, neighbors) in &adjacency {
            for &neighbor in neighbors {
                // file → neighbor is 1-hop. neighbor → neighbor's neighbors is 2-hop from file.
                if let Some(second_hop) = adjacency.get(&neighbor) {
                    for &distant in second_hop {
                        if distant != file {
                            let (lo, hi) = if file <= distant {
                                (file, distant)
                            } else {
                                (distant, file)
                            };
                            two_hop.push((lo, hi));
                        }
                    }
                }
            }
        }

        for (lo, hi) in two_hop {
            structurally_connected_files.insert((lo, hi));
        }
    }

    // 5. Hidden coupling: co-change pairs with NO structural edges between files
    //    (after containment + 2-hop filtering)
    let mut hidden_coupling: Vec<(NodeIndex, NodeIndex, f32, f32, f32)> = Vec::new(); // (file_a, file_b, weight, lift, confidence)

    // Compute adaptive hub threshold: p90 of coupling degrees.
    // Files in the top 10% by coupling degree are hubs (infrastructure files
    // that co-change with everything). Adapts to repo size automatically.
    let hub_threshold = {
        let mut degrees: Vec<usize> = co_change
            .iter()
            .flat_map(|(&(a, b), _)| [co_change.coupling_degree(a), co_change.coupling_degree(b)])
            .collect();
        degrees.sort_unstable();
        degrees.dedup();
        if degrees.is_empty() {
            20 // fallback
        } else {
            let p90_idx = (degrees.len() as f32 * 0.9) as usize;
            degrees[p90_idx.min(degrees.len() - 1)].max(10) // floor at 10
        }
    };

    // Build a lookup from StrKey → File-level NodeIndex
    let mut file_key_to_node: HashMap<StrKey, NodeIndex> = HashMap::default();
    for &file_idx in files {
        let node = &graph[file_idx];
        file_key_to_node.insert(node.file_path, file_idx);
    }

    for (&(key_a, key_b), &weight) in co_change.iter() {
        // Canonical pair: key_a < key_b (enforced by CoChangeMatrix)
        let (lo, hi) = if key_a <= key_b {
            (key_a, key_b)
        } else {
            (key_b, key_a)
        };

        // Skip if there's already a structural edge between these files
        if structurally_connected_files.contains(&(lo, hi)) {
            continue;
        }

        // Filter 1: Minimum co-change count (support) — need enough evidence
        let pair_count = co_change.pair_commit_count(key_a, key_b);
        if pair_count < 3 {
            continue;
        }

        // Filter 2: Minimum confidence — must co-change meaningfully often
        let confidence = co_change.confidence(key_a, key_b);
        if confidence < 0.15 {
            continue;
        }

        // Filter 3: Hub penalty — skip if either file is in the top 10% by coupling degree.
        // Adapts to repo size: absolute threshold (20) is too strict for large repos.
        let degree_a = co_change.coupling_degree(key_a);
        let degree_b = co_change.coupling_degree(key_b);
        if degree_a > hub_threshold || degree_b > hub_threshold {
            continue;
        }

        // Get functions in each file
        let funcs_a = match file_to_functions.get(&key_a) {
            Some(f) => f,
            None => continue,
        };
        let funcs_b = match file_to_functions.get(&key_b) {
            Some(f) => f,
            None => continue,
        };

        let co_change_boost = weight.min(2.0);

        // Add at most ONE representative overlay edge per hidden-coupling file pair.
        // Pick the first function (by NodeIndex) in each file to avoid O(|f_a|×|f_b|) explosion.
        let rep_a = funcs_a.iter().copied().min();
        let rep_b = funcs_b.iter().copied().min();
        if let (Some(fa), Some(fb)) = (rep_a, rep_b) {
            if let (Some(&ov_a), Some(&ov_b)) = (idx_map.get(&fa), idx_map.get(&fb)) {
                overlay.add_edge(ov_a, ov_b, co_change_boost);
            }
        }

        // Record hidden coupling at file level with lift
        if let (Some(&file_node_a), Some(&file_node_b)) =
            (file_key_to_node.get(&key_a), file_key_to_node.get(&key_b))
        {
            let lift = co_change.lift(key_a, key_b).unwrap_or(1.0);
            let confidence = co_change.confidence(key_a, key_b);
            hidden_coupling.push((file_node_a, file_node_b, co_change_boost, lift, confidence));
        }
    }

    (overlay, hidden_coupling)
}

pub(super) fn compute_weighted_page_rank(
    overlay: &StableGraph<NodeIndex, f32>,
    iterations: usize,
    damping: f64,
    tolerance: f64,
) -> HashMap<NodeIndex, f64> {
    let _span = tracing::info_span!("weighted_page_rank").entered();
    let node_count = overlay.node_count();
    if node_count == 0 {
        return HashMap::default();
    }

    let init = 1.0 / node_count as f64;
    let mut rank: HashMap<NodeIndex, f64> = overlay.node_indices().map(|n| (n, init)).collect();

    for _ in 0..iterations {
        let mut new_rank: HashMap<NodeIndex, f64> = overlay
            .node_indices()
            .map(|n| (n, (1.0 - damping) / node_count as f64))
            .collect();

        // Accumulate dangling node mass (nodes with zero out-weight)
        let mut dangling_sum = 0.0;
        for src in overlay.node_indices() {
            let total_weight: f64 = overlay.edges(src).map(|e| *e.weight() as f64).sum();
            if total_weight == 0.0 {
                dangling_sum += rank[&src];
            }
        }
        let dangling_contribution = damping * dangling_sum / node_count as f64;
        for r in new_rank.values_mut() {
            *r += dangling_contribution;
        }

        for src in overlay.node_indices() {
            let out_edges: Vec<_> = overlay.edges(src).collect();
            let total_weight: f64 = out_edges.iter().map(|e| *e.weight() as f64).sum();
            if total_weight == 0.0 {
                continue;
            }
            let src_rank = rank[&src];
            for edge in &out_edges {
                let fraction = *edge.weight() as f64 / total_weight;
                *new_rank
                    .get_mut(&edge.target())
                    .expect("overlay node must exist in rank map") += damping * src_rank * fraction;
            }
        }

        let diff: f64 = overlay
            .node_indices()
            .map(|n| (new_rank[&n] - rank[&n]).abs())
            .sum();
        rank = new_rank;
        if diff < tolerance {
            break;
        }
    }

    // Map overlay NodeIndex → original NodeIndex stored as node weight
    let mut result = HashMap::default();
    for n in overlay.node_indices() {
        let original_idx = overlay[n]; // node weight is the original NodeIndex
        result.insert(original_idx, rank[&n]);
    }
    result
}

/// Dijkstra-based Brandes algorithm for weighted betweenness centrality.
///
/// Brandes (2001) "A Faster Algorithm for Betweenness Centrality" — weighted variant.
/// Uses `1.0 / weight` as Dijkstra distance so that higher edge weight (stronger
/// coupling) means shorter distance (closer in the shortest-path sense).
///
/// Returns a map from original `NodeIndex` (stored as overlay node weight) to
/// betweenness score, normalized by sampling ratio.
pub(super) fn compute_weighted_betweenness(
    overlay: &StableGraph<NodeIndex, f32>,
    sample_size: usize,
    edge_fingerprint: u64,
) -> HashMap<NodeIndex, f64> {
    let _span = tracing::info_span!("weighted_betweenness").entered();

    let node_count = overlay.node_count();
    if node_count == 0 {
        return HashMap::default();
    }

    // Collect overlay node indices sorted for deterministic sampling
    let mut nodes: Vec<petgraph::stable_graph::NodeIndex> = overlay.node_indices().collect();
    nodes.sort_by_key(|n| n.index());

    let actual_sample = sample_size.min(node_count);

    // Deterministic sampling: select every (node_count / sample_size)-th node
    // starting at fingerprint % step
    let sources: Vec<petgraph::stable_graph::NodeIndex> = if actual_sample >= node_count {
        nodes.clone()
    } else {
        let step = node_count / actual_sample;
        let start = (edge_fingerprint as usize) % step;
        nodes
            .iter()
            .skip(start)
            .step_by(step)
            .take(actual_sample)
            .copied()
            .collect()
    };

    // Map overlay NodeIndex → local dense index for array-based accumulation
    let node_to_local: HashMap<petgraph::stable_graph::NodeIndex, usize> =
        nodes.iter().enumerate().map(|(i, &n)| (n, i)).collect();

    // Parallel Brandes: each source computes partial betweenness via Dijkstra
    let partial_results: Vec<Vec<f64>> = sources
        .par_iter()
        .map(|&source| {
            let n = node_count;
            let mut delta = vec![0.0f64; n];
            let mut sigma = vec![0.0f64; n]; // shortest-path counts
            let mut dist = vec![f64::INFINITY; n];
            let mut predecessors: Vec<Vec<usize>> = vec![Vec::new(); n];
            let mut stack: Vec<usize> = Vec::new(); // nodes in order of discovery

            let s_local = node_to_local[&source];
            sigma[s_local] = 1.0;
            dist[s_local] = 0.0;

            // Min-heap: (Reverse(distance), local_index)
            // Using u64 bits of f64 for total ordering via Reverse
            let mut heap: BinaryHeap<Reverse<(OrderedF64, usize)>> = BinaryHeap::new();
            heap.push(Reverse((OrderedF64(0.0), s_local)));

            // Dijkstra phase
            while let Some(Reverse((OrderedF64(d_v), v))) = heap.pop() {
                // Skip stale entries
                if d_v > dist[v] {
                    continue;
                }
                stack.push(v);

                let v_overlay = nodes[v];
                for edge in overlay.edges(v_overlay) {
                    let w_overlay = edge.target();
                    if let Some(&w) = node_to_local.get(&w_overlay) {
                        let edge_weight = *edge.weight() as f64;
                        // Higher weight = stronger coupling = shorter distance
                        let edge_dist = if edge_weight > 0.0 {
                            1.0 / edge_weight
                        } else {
                            f64::INFINITY
                        };
                        let new_dist = d_v + edge_dist;

                        if new_dist < dist[w] - 1e-10 {
                            // Found a strictly shorter path
                            dist[w] = new_dist;
                            sigma[w] = sigma[v];
                            predecessors[w].clear();
                            predecessors[w].push(v);
                            heap.push(Reverse((OrderedF64(new_dist), w)));
                        } else if (new_dist - dist[w]).abs() < 1e-10 {
                            // Found an equal-length shortest path
                            sigma[w] += sigma[v];
                            predecessors[w].push(v);
                        }
                    }
                }
            }

            // Accumulation phase (reverse order of discovery)
            while let Some(w) = stack.pop() {
                for &v in &predecessors[w] {
                    if sigma[w] > 0.0 {
                        let contrib = (sigma[v] / sigma[w]) * (1.0 + delta[w]);
                        delta[v] += contrib;
                    }
                }
            }

            delta
        })
        .collect();

    // Aggregate partial results
    let mut betweenness = vec![0.0f64; node_count];
    for partial in &partial_results {
        for (i, &val) in partial.iter().enumerate() {
            betweenness[i] += val;
        }
    }

    // Normalization: scale by (node_count / actual_sample) to approximate full computation
    let scale = if actual_sample > 0 {
        node_count as f64 / actual_sample as f64
    } else {
        1.0
    };

    // Map back to original NodeIndex (stored as overlay node weight)
    let mut result = HashMap::with_capacity(node_count);
    for (i, &overlay_node) in nodes.iter().enumerate() {
        let original_idx = overlay[overlay_node];
        result.insert(original_idx, betweenness[i] * scale);
    }
    result
}

/// Wrapper for f64 that implements Ord for use in BinaryHeap.
/// Uses total_cmp for consistent ordering (NaN-safe).
#[derive(Clone, Copy, PartialEq)]
struct OrderedF64(f64);

impl Eq for OrderedF64 {}

impl PartialOrd for OrderedF64 {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for OrderedF64 {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.0.total_cmp(&other.0)
    }
}

pub(super) fn compute_communities(
    overlay: &StableGraph<NodeIndex, f32>,
    resolution: f64,
) -> (HashMap<NodeIndex, usize>, f64) {
    let _span = tracing::info_span!("louvain_communities").entered();

    let node_indices: Vec<NodeIndex> = {
        let mut v: Vec<_> = overlay.node_indices().collect();
        v.sort();
        v
    };

    if node_indices.is_empty() {
        return (HashMap::default(), 0.0);
    }

    // Treat as undirected: for each directed edge (u->v, w), both u and v get
    // weight w contributed. We pre-compute symmetric neighbor weights.
    // neighbor_weights[n] = map of neighbor overlay NodeIndex → sum of weights
    // between n and that neighbor (both directions).
    let mut neighbor_weights: HashMap<NodeIndex, HashMap<NodeIndex, f64>> = HashMap::default();
    for n in &node_indices {
        neighbor_weights.insert(*n, HashMap::default());
    }
    for edge_id in overlay.edge_indices() {
        let (u, v) = overlay
            .edge_endpoints(edge_id)
            .expect("edge must have endpoints");
        let w = overlay[edge_id] as f64;
        // Treat directed graph as undirected: each directed edge u→v with weight w
        // contributes w to both u's and v's neighborhoods.
        *neighbor_weights
            .entry(u)
            .or_default()
            .entry(v)
            .or_insert(0.0) += w;
        *neighbor_weights
            .entry(v)
            .or_default()
            .entry(u)
            .or_insert(0.0) += w;
    }

    // node strength k_i = sum of all incident edge weights (undirected treatment).
    // k_i = sum of neighbor_weights[i] values.
    let mut strength: HashMap<NodeIndex, f64> = HashMap::default();
    for &n in &node_indices {
        let k: f64 = neighbor_weights
            .get(&n)
            .map(|nw| nw.values().sum())
            .unwrap_or(0.0);
        strength.insert(n, k);
    }

    // Standard modularity identity: 2m = Σ k_i = total undirected edge weight.
    // Each directed edge (u→v, w) contributes w to both k_u and k_v, so
    // Σ k_i = 2 * (sum of directed edge weights).
    let total_2m: f64 = strength.values().sum();

    if total_2m <= 0.0 {
        // No edges: each node is its own community, modularity 0
        let mut community_map = HashMap::default();
        for (i, &n) in node_indices.iter().enumerate() {
            community_map.insert(overlay[n], i);
        }
        return (community_map, 0.0);
    }

    let m = total_2m / 2.0;

    // Community assignment: overlay NodeIndex → community ID
    let mut community: HashMap<NodeIndex, usize> = HashMap::default();
    for (i, &n) in node_indices.iter().enumerate() {
        community.insert(n, i);
    }

    // sigma_tot[c] = sum of strengths of all nodes in community c
    let mut sigma_tot: HashMap<usize, f64> = HashMap::default();
    for &n in &node_indices {
        let c = community[&n];
        *sigma_tot.entry(c).or_insert(0.0) += strength[&n];
    }

    // Phase 1: Local moves until convergence
    loop {
        let mut improved = false;

        for &n in &node_indices {
            let k_i = strength[&n];
            let current_comm = community[&n];

            // Compute sum of weights from n to each neighboring community.
            // BTreeMap ensures deterministic iteration order (by community ID).
            let mut comm_weights: BTreeMap<usize, f64> = BTreeMap::new();
            if let Some(nw) = neighbor_weights.get(&n) {
                for (&neighbor, &w) in nw {
                    let nc = community[&neighbor];
                    *comm_weights.entry(nc).or_insert(0.0) += w;
                }
            }

            // Weight from n to its own community (excluding self)
            let k_i_current = comm_weights.get(&current_comm).copied().unwrap_or(0.0);

            // Remove node from its current community for delta computation
            let sigma_tot_current = sigma_tot[&current_comm] - k_i;

            // Evaluate moving to each neighboring community
            let mut best_comm = current_comm;
            let mut best_delta = 0.0;

            for (&target_comm, &k_i_target) in &comm_weights {
                if target_comm == current_comm {
                    continue;
                }
                let sigma_tot_target = sigma_tot[&target_comm];

                // Delta Q = [k_i_target / m - resolution * sigma_tot_target * k_i / (2 * m^2)]
                //         - [k_i_current / m - resolution * sigma_tot_current * k_i / (2 * m^2)]
                let delta = (k_i_target - k_i_current) / m
                    - resolution * k_i * (sigma_tot_target - sigma_tot_current) / (2.0 * m * m);

                // Deterministic tiebreaker: prefer strictly better delta,
                // or on tie pick the smaller community ID.
                if delta > best_delta || (delta == best_delta && target_comm < best_comm) {
                    best_delta = delta;
                    best_comm = target_comm;
                }
            }

            if best_comm != current_comm {
                // Move node n from current_comm to best_comm
                sigma_tot.entry(current_comm).and_modify(|v| *v -= k_i);
                *sigma_tot.entry(best_comm).or_insert(0.0) += k_i;
                community.insert(n, best_comm);
                improved = true;
            }
        }

        if !improved {
            break;
        }
    }

    // Compute final modularity Q
    let modularity = compute_modularity(overlay, &community, &strength, m, resolution);

    // Map from overlay NodeIndex → original NodeIndex (stored as node weight),
    // and renumber communities to be contiguous 0..k
    let mut comm_renumber: HashMap<usize, usize> = HashMap::default();
    let mut next_id = 0usize;
    let mut result: HashMap<NodeIndex, usize> = HashMap::default();
    // Process in sorted order for deterministic renumbering
    for &n in &node_indices {
        let c = community[&n];
        let new_c = *comm_renumber.entry(c).or_insert_with(|| {
            let id = next_id;
            next_id += 1;
            id
        });
        let original_idx = overlay[n];
        result.insert(original_idx, new_c);
    }

    (result, modularity)
}

/// Compute modularity Q for a given community assignment.
pub(super) fn compute_modularity(
    overlay: &StableGraph<NodeIndex, f32>,
    community: &HashMap<NodeIndex, usize>,
    strength: &HashMap<NodeIndex, f64>,
    m: f64,
    resolution: f64,
) -> f64 {
    // Q = (1/2m) * Σ_ij [A_ij - resolution * k_i * k_j / (2m)] * δ(c_i, c_j)
    // We compute this edge-by-edge for efficiency.
    let two_m = 2.0 * m;

    // Sum of (k_i * k_j / 2m) for all pairs in same community
    // = Σ_c (Σ_{i in c} k_i)^2 / (2m)
    let mut sigma_sq_sum = 0.0;
    let mut comm_sigma: HashMap<usize, f64> = HashMap::default();
    for (&n, &c) in community {
        *comm_sigma.entry(c).or_insert(0.0) += strength[&n];
    }
    for &s in comm_sigma.values() {
        sigma_sq_sum += s * s;
    }

    // Sum of A_ij for pairs in same community (undirected: count each directed edge once,
    // but contribute to both i-j directions, so add w for each directed edge where c_i == c_j)
    let mut internal_weight = 0.0;
    for edge_id in overlay.edge_indices() {
        let (u, v) = overlay
            .edge_endpoints(edge_id)
            .expect("edge must have endpoints");
        if community[&u] == community[&v] {
            // Each directed edge contributes w to the undirected sum
            // (the full A_ij matrix double-counts, and we sum over all i,j pairs)
            internal_weight += overlay[edge_id] as f64;
        }
    }

    // Q = internal_weight / (2m) - resolution * sigma_sq_sum / (2m)^2
    // Note: internal_weight counts each directed edge once. In the undirected A_ij matrix,
    // A_ij = A_ji, so the sum over all ordered pairs (i,j) where i!=j gives 2 * directed_sum.
    // But the modularity formula sums over all ordered (i,j) pairs including both directions.
    // Since our directed edges represent one direction, and we add both u->v and v->u neighbor
    // weights, the sum Σ_{ij} A_ij δ(c_i,c_j) = 2 * internal_weight.
    (2.0 * internal_weight) / two_m - resolution * sigma_sq_sum / (two_m * two_m)
}
