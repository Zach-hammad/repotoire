//! Phase B: Weighted overlay graph algorithms using git co-change data.
//!
//! Contains 6 algorithms + OrderedF64 helper computed on the weighted overlay graph:
//! weighted phase orchestrator, overlay builder, weighted PageRank, weighted betweenness,
//! Louvain community detection, modularity.

use std::collections::HashMap;
use rayon::prelude::*;
use std::cmp::Reverse;
use std::collections::{BTreeMap, BinaryHeap, HashSet};

use crate::git::co_change::CoChangeMatrix;
use crate::graph::frozen::CodeGraph;
use crate::graph::interner::StrKey;
use crate::graph::node_index::NodeIndex;
use crate::graph::overlay::WeightedOverlay;

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
    code_graph: &CodeGraph,
    edge_fingerprint: u64,
) -> (
    HashMap<NodeIndex, f64>,
    HashMap<NodeIndex, f64>,
    HashMap<NodeIndex, usize>,
    f64,
    Vec<(NodeIndex, NodeIndex, f32, f32, f32)>,
) {
    let (overlay, idx_to_orig, hidden_coupling) = build_weighted_overlay(
        functions,
        files,
        all_call_edges,
        all_import_edges,
        co_change,
        code_graph,
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
        || compute_weighted_page_rank(&overlay, &idx_to_orig, 20, 0.85, 1e-6),
        || {
            rayon::join(
                || compute_weighted_betweenness(&overlay, &idx_to_orig, 200, edge_fingerprint),
                || compute_communities(&overlay, &idx_to_orig, 1.0),
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
/// co-change weights. Returns `(overlay, idx_to_orig, hidden_coupling)`.
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
    code_graph: &CodeGraph,
) -> (
    WeightedOverlay,
    Vec<NodeIndex>,
    Vec<(NodeIndex, NodeIndex, f32, f32, f32)>,
) {
    // 1. Build idx_map: original NodeIndex → overlay dense index (u32)
    let mut idx_map: HashMap<NodeIndex, u32> = HashMap::default();
    let mut idx_to_orig: Vec<NodeIndex> = Vec::with_capacity(functions.len());

    for &func_idx in functions {
        let overlay_idx = idx_to_orig.len() as u32;
        idx_map.insert(func_idx, overlay_idx);
        idx_to_orig.push(func_idx);
    }

    let mut overlay = WeightedOverlay::new(idx_to_orig.len());

    // 2. Build file_to_functions: StrKey(file_path) → Vec<NodeIndex> (original)
    let mut file_to_functions: HashMap<StrKey, Vec<NodeIndex>> = HashMap::default();
    for &func_idx in functions {
        if let Some(node) = code_graph.node(func_idx) {
            let file_key = node.file_path;
            file_to_functions
                .entry(file_key)
                .or_default()
                .push(func_idx);
        }
    }

    // 3. Process structural edges with deduplication
    let import_set: HashSet<(NodeIndex, NodeIndex)> = all_import_edges.iter().copied().collect();

    let mut structural_pairs: HashMap<(NodeIndex, NodeIndex), f32> = HashMap::default();

    for &(src, tgt) in all_call_edges {
        if !idx_map.contains_key(&src) || !idx_map.contains_key(&tgt) {
            continue;
        }
        let has_import = import_set.contains(&(src, tgt));
        let structural_base = if has_import { 1.5 } else { 1.0 };
        let entry = structural_pairs.entry((src, tgt)).or_insert(0.0);
        if structural_base > *entry {
            *entry = structural_base;
        }
    }

    for &(src, tgt) in all_import_edges {
        if !idx_map.contains_key(&src) || !idx_map.contains_key(&tgt) {
            continue;
        }
        structural_pairs.entry((src, tgt)).or_insert(0.5);
    }

    // Track which file pairs have structural edges between their functions
    let mut structurally_connected_files: HashSet<(StrKey, StrKey)> = HashSet::new();

    // Add overlay edges for structural pairs, boosted by co-change
    for (&(src, tgt), &structural_base) in &structural_pairs {
        let src_node = match code_graph.node(src) {
            Some(n) => n,
            None => continue,
        };
        let tgt_node = match code_graph.node(tgt) {
            Some(n) => n,
            None => continue,
        };
        let src_file_key = src_node.file_path;
        let tgt_file_key = tgt_node.file_path;

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
    {
        let si = crate::graph::interner::global_interner();
        let mut dir_to_files: HashMap<String, Vec<StrKey>> = HashMap::default();
        for &file_idx in files {
            if let Some(node) = code_graph.node(file_idx) {
                let path = si.resolve(node.file_path);
                if let Some((dir, _)) = path.rsplit_once('/') {
                    dir_to_files
                        .entry(dir.to_string())
                        .or_default()
                        .push(node.file_path);
                }
            }
        }

        for file_keys in dir_to_files.values() {
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
    {
        let mut adjacency: HashMap<StrKey, HashSet<StrKey>> = HashMap::default();
        for &(lo, hi) in &structurally_connected_files {
            adjacency.entry(lo).or_default().insert(hi);
            adjacency.entry(hi).or_default().insert(lo);
        }

        let mut two_hop: Vec<(StrKey, StrKey)> = Vec::new();
        for (&file, neighbors) in &adjacency {
            for &neighbor in neighbors {
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
    let mut hidden_coupling: Vec<(NodeIndex, NodeIndex, f32, f32, f32)> = Vec::new();

    let hub_threshold = {
        let mut degrees: Vec<usize> = co_change
            .iter()
            .flat_map(|(&(a, b), _)| [co_change.coupling_degree(a), co_change.coupling_degree(b)])
            .collect();
        degrees.sort_unstable();
        degrees.dedup();
        if degrees.is_empty() {
            20
        } else {
            let p90_idx = (degrees.len() as f32 * 0.9) as usize;
            degrees[p90_idx.min(degrees.len() - 1)].max(10)
        }
    };

    // Build a lookup from StrKey → File-level NodeIndex
    let mut file_key_to_node: HashMap<StrKey, NodeIndex> = HashMap::default();
    for &file_idx in files {
        if let Some(node) = code_graph.node(file_idx) {
            file_key_to_node.insert(node.file_path, file_idx);
        }
    }

    for (&(key_a, key_b), &weight) in co_change.iter() {
        let (lo, hi) = if key_a <= key_b {
            (key_a, key_b)
        } else {
            (key_b, key_a)
        };

        if structurally_connected_files.contains(&(lo, hi)) {
            continue;
        }

        let pair_count = co_change.pair_commit_count(key_a, key_b);
        if pair_count < 3 {
            continue;
        }

        let confidence = co_change.confidence(key_a, key_b);
        if confidence < 0.15 {
            continue;
        }

        let degree_a = co_change.coupling_degree(key_a);
        let degree_b = co_change.coupling_degree(key_b);
        if degree_a > hub_threshold || degree_b > hub_threshold {
            continue;
        }

        let funcs_a = match file_to_functions.get(&key_a) {
            Some(f) => f,
            None => continue,
        };
        let funcs_b = match file_to_functions.get(&key_b) {
            Some(f) => f,
            None => continue,
        };

        let co_change_boost = weight.min(2.0);

        let rep_a = funcs_a.iter().copied().min();
        let rep_b = funcs_b.iter().copied().min();
        if let (Some(fa), Some(fb)) = (rep_a, rep_b) {
            if let (Some(&ov_a), Some(&ov_b)) = (idx_map.get(&fa), idx_map.get(&fb)) {
                overlay.add_edge(ov_a, ov_b, co_change_boost);
            }
        }

        if let (Some(&file_node_a), Some(&file_node_b)) =
            (file_key_to_node.get(&key_a), file_key_to_node.get(&key_b))
        {
            let lift = co_change.lift(key_a, key_b).unwrap_or(1.0);
            let confidence = co_change.confidence(key_a, key_b);
            hidden_coupling.push((file_node_a, file_node_b, co_change_boost, lift, confidence));
        }
    }

    (overlay, idx_to_orig, hidden_coupling)
}

pub(super) fn compute_weighted_page_rank(
    overlay: &WeightedOverlay,
    idx_to_orig: &[NodeIndex],
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
    let mut rank = vec![init; node_count];

    for _ in 0..iterations {
        let mut new_rank = vec![(1.0 - damping) / node_count as f64; node_count];

        // Accumulate dangling node mass (nodes with zero out-weight)
        let mut dangling_sum = 0.0;
        for src in overlay.node_indices() {
            let total_weight: f64 = overlay.neighbors(src).map(|(_, w)| w as f64).sum();
            if total_weight == 0.0 {
                dangling_sum += rank[src as usize];
            }
        }
        let dangling_contribution = damping * dangling_sum / node_count as f64;
        for r in new_rank.iter_mut() {
            *r += dangling_contribution;
        }

        for src in overlay.node_indices() {
            let out_edges: Vec<_> = overlay.neighbors(src).collect();
            let total_weight: f64 = out_edges.iter().map(|(_, w)| *w as f64).sum();
            if total_weight == 0.0 {
                continue;
            }
            let src_rank = rank[src as usize];
            for &(tgt, w) in &out_edges {
                let fraction = w as f64 / total_weight;
                new_rank[tgt as usize] += damping * src_rank * fraction;
            }
        }

        let diff: f64 = rank
            .iter()
            .zip(new_rank.iter())
            .map(|(old, new)| (old - new).abs())
            .sum();
        rank = new_rank;
        if diff < tolerance {
            break;
        }
    }

    // Map overlay index → original NodeIndex
    let mut result = HashMap::default();
    for (i, &orig) in idx_to_orig.iter().enumerate() {
        result.insert(orig, rank[i]);
    }
    result
}

/// Dijkstra-based Brandes algorithm for weighted betweenness centrality.
pub(super) fn compute_weighted_betweenness(
    overlay: &WeightedOverlay,
    idx_to_orig: &[NodeIndex],
    sample_size: usize,
    edge_fingerprint: u64,
) -> HashMap<NodeIndex, f64> {
    let _span = tracing::info_span!("weighted_betweenness").entered();

    let node_count = overlay.node_count();
    if node_count == 0 {
        return HashMap::default();
    }

    // Collect sorted node indices for deterministic sampling
    let mut nodes: Vec<u32> = overlay.node_indices().collect();
    nodes.sort();

    let actual_sample = sample_size.min(node_count);

    let sources: Vec<u32> = if actual_sample >= node_count {
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

    // Parallel Brandes: each source computes partial betweenness via Dijkstra
    let partial_results: Vec<Vec<f64>> = sources
        .par_iter()
        .map(|&source| {
            let n = node_count;
            let mut delta = vec![0.0f64; n];
            let mut sigma = vec![0.0f64; n];
            let mut dist = vec![f64::INFINITY; n];
            let mut predecessors: Vec<Vec<usize>> = vec![Vec::new(); n];
            let mut stack: Vec<usize> = Vec::new();

            let s_local = source as usize;
            sigma[s_local] = 1.0;
            dist[s_local] = 0.0;

            let mut heap: BinaryHeap<Reverse<(OrderedF64, usize)>> = BinaryHeap::new();
            heap.push(Reverse((OrderedF64(0.0), s_local)));

            // Dijkstra phase
            while let Some(Reverse((OrderedF64(d_v), v))) = heap.pop() {
                if d_v > dist[v] {
                    continue;
                }
                stack.push(v);

                for (w_idx, edge_weight) in overlay.neighbors(v as u32) {
                    let w = w_idx as usize;
                    let edge_weight = edge_weight as f64;
                    let edge_dist = if edge_weight > 0.0 {
                        1.0 / edge_weight
                    } else {
                        f64::INFINITY
                    };
                    let new_dist = d_v + edge_dist;

                    if new_dist < dist[w] - 1e-10 {
                        dist[w] = new_dist;
                        sigma[w] = sigma[v];
                        predecessors[w].clear();
                        predecessors[w].push(v);
                        heap.push(Reverse((OrderedF64(new_dist), w)));
                    } else if (new_dist - dist[w]).abs() < 1e-10 {
                        sigma[w] += sigma[v];
                        predecessors[w].push(v);
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

    // Normalization
    let scale = if actual_sample > 0 {
        node_count as f64 / actual_sample as f64
    } else {
        1.0
    };

    // Map back to original NodeIndex
    let mut result = HashMap::with_capacity(node_count);
    for (i, &orig) in idx_to_orig.iter().enumerate() {
        result.insert(orig, betweenness[i] * scale);
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
    overlay: &WeightedOverlay,
    idx_to_orig: &[NodeIndex],
    resolution: f64,
) -> (HashMap<NodeIndex, usize>, f64) {
    let _span = tracing::info_span!("louvain_communities").entered();

    let node_count = overlay.node_count();
    if node_count == 0 {
        return (HashMap::default(), 0.0);
    }

    let node_indices: Vec<u32> = overlay.node_indices().collect();

    // Treat as undirected: for each directed edge (u->v, w), both u and v get
    // weight w contributed. We pre-compute symmetric neighbor weights.
    let mut neighbor_weights: Vec<HashMap<u32, f64>> = vec![HashMap::default(); node_count];
    for src in overlay.node_indices() {
        for (tgt, w) in overlay.neighbors(src) {
            let w = w as f64;
            *neighbor_weights[src as usize].entry(tgt).or_insert(0.0) += w;
            *neighbor_weights[tgt as usize].entry(src).or_insert(0.0) += w;
        }
    }

    // node strength k_i = sum of all incident edge weights (undirected treatment)
    let mut strength: Vec<f64> = vec![0.0; node_count];
    for i in 0..node_count {
        strength[i] = neighbor_weights[i].values().sum();
    }

    let total_2m: f64 = strength.iter().sum();

    if total_2m <= 0.0 {
        let mut community_map = HashMap::default();
        for (i, &orig) in idx_to_orig.iter().enumerate() {
            community_map.insert(orig, i);
        }
        return (community_map, 0.0);
    }

    let m = total_2m / 2.0;

    // Community assignment: overlay index → community ID
    let mut community: Vec<usize> = (0..node_count).collect();

    // sigma_tot[c] = sum of strengths of all nodes in community c
    let mut sigma_tot: HashMap<usize, f64> = HashMap::default();
    for i in 0..node_count {
        *sigma_tot.entry(community[i]).or_insert(0.0) += strength[i];
    }

    // Phase 1: Local moves until convergence
    loop {
        let mut improved = false;

        for &n in &node_indices {
            let n_idx = n as usize;
            let k_i = strength[n_idx];
            let current_comm = community[n_idx];

            // Compute sum of weights from n to each neighboring community
            let mut comm_weights: BTreeMap<usize, f64> = BTreeMap::new();
            for (&neighbor, &w) in &neighbor_weights[n_idx] {
                let nc = community[neighbor as usize];
                *comm_weights.entry(nc).or_insert(0.0) += w;
            }

            let k_i_current = comm_weights.get(&current_comm).copied().unwrap_or(0.0);
            let sigma_tot_current = sigma_tot[&current_comm] - k_i;

            let mut best_comm = current_comm;
            let mut best_delta = 0.0;

            for (&target_comm, &k_i_target) in &comm_weights {
                if target_comm == current_comm {
                    continue;
                }
                let sigma_tot_target = sigma_tot[&target_comm];

                let delta = (k_i_target - k_i_current) / m
                    - resolution * k_i * (sigma_tot_target - sigma_tot_current) / (2.0 * m * m);

                if delta > best_delta || (delta == best_delta && target_comm < best_comm) {
                    best_delta = delta;
                    best_comm = target_comm;
                }
            }

            if best_comm != current_comm {
                sigma_tot.entry(current_comm).and_modify(|v| *v -= k_i);
                *sigma_tot.entry(best_comm).or_insert(0.0) += k_i;
                community[n_idx] = best_comm;
                improved = true;
            }
        }

        if !improved {
            break;
        }
    }

    // Compute final modularity Q
    let modularity = compute_modularity(overlay, &community, &strength, m, resolution);

    // Map from overlay index → original NodeIndex, renumber communities
    let mut comm_renumber: HashMap<usize, usize> = HashMap::default();
    let mut next_id = 0usize;
    let mut result: HashMap<NodeIndex, usize> = HashMap::default();
    for &n in &node_indices {
        let c = community[n as usize];
        let new_c = *comm_renumber.entry(c).or_insert_with(|| {
            let id = next_id;
            next_id += 1;
            id
        });
        let orig = idx_to_orig[n as usize];
        result.insert(orig, new_c);
    }

    (result, modularity)
}

/// Compute modularity Q for a given community assignment.
pub(super) fn compute_modularity(
    overlay: &WeightedOverlay,
    community: &[usize],
    strength: &[f64],
    m: f64,
    resolution: f64,
) -> f64 {
    let two_m = 2.0 * m;

    let mut sigma_sq_sum = 0.0;
    let mut comm_sigma: HashMap<usize, f64> = HashMap::default();
    for (i, &c) in community.iter().enumerate() {
        *comm_sigma.entry(c).or_insert(0.0) += strength[i];
    }
    for &s in comm_sigma.values() {
        sigma_sq_sum += s * s;
    }

    let mut internal_weight = 0.0;
    for src in overlay.node_indices() {
        for (tgt, w) in overlay.neighbors(src) {
            if community[src as usize] == community[tgt as usize] {
                internal_weight += w as f64;
            }
        }
    }

    (2.0 * internal_weight) / two_m - resolution * sigma_sq_sum / (two_m * two_m)
}
