//! Phase A: Structural graph algorithms (no temporal weighting).
//!
//! Contains 8 algorithms computed on the raw call/import graph:
//! SCCs, PageRank, dominators, articulation points, call depths, betweenness.

use std::collections::HashMap;
use rayon::prelude::*;
use std::collections::{HashSet, VecDeque};

use crate::graph::frozen::CodeGraph;
use crate::graph::interner::global_interner;
use crate::graph::node_index::NodeIndex;

// ═══════════════════════════════════════════════════════════════════════════════
// Algorithm 1: Call-graph SCCs
// ═══════════════════════════════════════════════════════════════════════════════

/// Build a filtered call-only subgraph and run Tarjan SCC.
/// Returns SCCs with >1 node (actual cycles), sorted by size descending.
pub(super) fn compute_call_cycles(
    all_call_edges: &[(NodeIndex, NodeIndex)],
    code_graph: &CodeGraph,
) -> Vec<Vec<NodeIndex>> {
    let si = global_interner();

    // Collect all nodes involved in call edges
    let mut relevant_nodes: HashSet<NodeIndex> = HashSet::new();
    for &(src, tgt) in all_call_edges {
        relevant_nodes.insert(src);
        relevant_nodes.insert(tgt);
    }

    // Build filtered subgraph with idx_map/reverse_map pattern
    let mut sorted_nodes: Vec<NodeIndex> = relevant_nodes.into_iter().collect();
    sorted_nodes.sort_by_key(|idx| idx.index());

    let idx_map: HashMap<NodeIndex, u32> = sorted_nodes
        .iter()
        .enumerate()
        .map(|(i, &ni)| (ni, i as u32))
        .collect();
    let n = sorted_nodes.len();

    // Build adjacency list for the filtered subgraph
    let mut adj: Vec<Vec<u32>> = vec![vec![]; n];
    for &(src, tgt) in all_call_edges {
        if let (Some(&from), Some(&to)) = (idx_map.get(&src), idx_map.get(&tgt)) {
            adj[from as usize].push(to);
        }
    }

    // Run hand-rolled Tarjan SCC
    let sccs = crate::graph::algo::tarjan_scc(n, |v| &adj[v as usize]);

    // Convert back to original NodeIndexes, keep only cycles (>1 node)
    let mut cycles: Vec<Vec<NodeIndex>> = sccs
        .into_iter()
        .filter(|scc| scc.len() > 1)
        .map(|scc| {
            let mut orig_indices: Vec<NodeIndex> = scc
                .iter()
                .filter_map(|&i| sorted_nodes.get(i as usize).copied())
                .collect();

            // Sort by qualified name for consistent ordering
            orig_indices.sort_by(|a, b| {
                let a_qn = code_graph
                    .node(*a)
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                let b_qn = code_graph
                    .node(*b)
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                a_qn.cmp(b_qn)
            });
            orig_indices
        })
        .collect();

    // Sort cycles: largest first, then by first node's QN for determinism
    cycles.sort_by(|a, b| {
        b.len().cmp(&a.len()).then_with(|| {
            let a_qn = a
                .first()
                .and_then(|idx| code_graph.node(*idx))
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            let b_qn = b
                .first()
                .and_then(|idx| code_graph.node(*idx))
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            a_qn.cmp(b_qn)
        })
    });

    cycles.dedup();
    cycles
}

// ═══════════════════════════════════════════════════════════════════════════════
// Algorithm 2: Sparse PageRank
// ═══════════════════════════════════════════════════════════════════════════════

/// Custom sparse power iteration PageRank using adjacency indexes directly.
/// NOT petgraph's dense O(V^2) built-in.
pub(super) fn compute_page_rank(
    functions: &[NodeIndex],
    code_graph: &CodeGraph,
    max_iterations: usize,
    damping: f64,
    tolerance: f64,
) -> HashMap<NodeIndex, f64> {
    let n = functions.len();
    if n == 0 {
        return HashMap::default();
    }

    let inv_n = 1.0 / n as f64;

    // Map NodeIndex -> local index for fast array access
    let node_to_idx: HashMap<NodeIndex, usize> = functions
        .iter()
        .enumerate()
        .map(|(i, &ni)| (ni, i))
        .collect();

    let mut rank = vec![inv_n; n];
    let mut new_rank = vec![0.0f64; n];

    // Pre-compute out-degrees for each function
    let out_degree: Vec<usize> = functions
        .iter()
        .map(|ni| code_graph.callees(*ni).len())
        .collect();

    let teleport = (1.0 - damping) * inv_n;

    for _iter in 0..max_iterations {
        // Reset new_rank with teleport base
        for r in new_rank.iter_mut() {
            *r = teleport;
        }

        // Accumulate dangling node mass (nodes with no outgoing calls)
        let mut dangling_sum = 0.0;
        for (i, &deg) in out_degree.iter().enumerate() {
            if deg == 0 {
                dangling_sum += rank[i];
            }
        }
        let dangling_contribution = damping * dangling_sum * inv_n;
        for r in new_rank.iter_mut() {
            *r += dangling_contribution;
        }

        // Distribute rank from each node to its callees
        for (i, &ni) in functions.iter().enumerate() {
            if out_degree[i] == 0 {
                continue;
            }
            let contribution = damping * rank[i] / out_degree[i] as f64;
            let callees = code_graph.callees(ni);
            for &callee in callees {
                if let Some(&j) = node_to_idx.get(&callee) {
                    new_rank[j] += contribution;
                }
            }
        }

        // Check convergence (L1 norm)
        let diff: f64 = rank
            .iter()
            .zip(new_rank.iter())
            .map(|(old, new)| (old - new).abs())
            .sum();

        std::mem::swap(&mut rank, &mut new_rank);

        if diff < tolerance {
            break;
        }
    }

    // Convert back to HashMap
    functions
        .iter()
        .enumerate()
        .map(|(i, &ni)| (ni, rank[i]))
        .collect()
}

// ═══════════════════════════════════════════════════════════════════════════════
// Algorithm 3: Dominator tree + frontiers
// ═══════════════════════════════════════════════════════════════════════════════

/// Compute dominator tree using hand-rolled iterative dominator with a virtual root.
/// Returns (idom, dominated, frontier, dom_depth).
pub(super) fn compute_dominators(
    functions: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    call_cycles: &[Vec<NodeIndex>],
    code_graph: &CodeGraph,
) -> (
    HashMap<NodeIndex, NodeIndex>,
    HashMap<NodeIndex, Vec<NodeIndex>>,
    HashMap<NodeIndex, Vec<NodeIndex>>,
    HashMap<NodeIndex, usize>,
) {
    let si = global_interner();
    let func_set: HashSet<NodeIndex> = functions.iter().copied().collect();

    // Sort functions for deterministic node insertion
    let mut sorted_functions: Vec<NodeIndex> = functions.to_vec();
    sorted_functions.sort_by(|a, b| {
        let a_qn = code_graph
            .node(*a)
            .map(|n| si.resolve(n.qualified_name))
            .unwrap_or("");
        let b_qn = code_graph
            .node(*b)
            .map(|n| si.resolve(n.qualified_name))
            .unwrap_or("");
        a_qn.cmp(b_qn)
    });

    // Build idx_map: original NodeIndex -> dense index (0..n-1)
    let mut idx_map: HashMap<NodeIndex, u32> = HashMap::default();
    let mut reverse_map: Vec<NodeIndex> = Vec::with_capacity(sorted_functions.len());
    for (i, &orig) in sorted_functions.iter().enumerate() {
        idx_map.insert(orig, i as u32);
        reverse_map.push(orig);
    }

    let n = sorted_functions.len();

    // Build forward adjacency for the dominator subgraph (only between functions)
    // +1 for virtual root
    let mut fwd_adj: Vec<Vec<u32>> = vec![vec![]; n + 1];
    for &(src, tgt) in all_call_edges {
        if let (Some(&from), Some(&to)) = (idx_map.get(&src), idx_map.get(&tgt)) {
            fwd_adj[from as usize].push(to);
        }
    }

    // Determine entry points
    let mut entry_points: Vec<NodeIndex> = sorted_functions
        .iter()
        .filter(|&&ni| {
            let callers = code_graph.callers(ni);
            let has_callers = callers.iter().any(|c| func_set.contains(c));
            let has_callees = !code_graph.callees(ni).is_empty();
            !has_callers && has_callees
        })
        .copied()
        .collect();

    // Handle disconnected SCCs: BFS from entry points to find reachable set,
    // then add representatives from unreachable SCCs
    let mut reachable: HashSet<NodeIndex> = HashSet::new();
    {
        let mut queue: VecDeque<NodeIndex> = VecDeque::new();
        for &ep in &entry_points {
            queue.push_back(ep);
            reachable.insert(ep);
        }
        while let Some(node) = queue.pop_front() {
            for &callee in code_graph.callees(node) {
                if func_set.contains(&callee) && reachable.insert(callee) {
                    queue.push_back(callee);
                }
            }
        }
    }

    // For unreachable SCCs, add a representative as an entry point
    for scc in call_cycles {
        if !scc.is_empty() && !reachable.contains(&scc[0]) {
            entry_points.push(scc[0]);
            let mut queue: VecDeque<NodeIndex> = VecDeque::new();
            queue.push_back(scc[0]);
            reachable.insert(scc[0]);
            while let Some(node) = queue.pop_front() {
                for &callee in code_graph.callees(node) {
                    if func_set.contains(&callee) && reachable.insert(callee) {
                        queue.push_back(callee);
                    }
                }
            }
        }
    }

    // Also handle isolated functions
    for &f in &sorted_functions {
        if !reachable.contains(&f) {
            entry_points.push(f);
            reachable.insert(f);
        }
    }

    // Add virtual root = n, connected to all entry points
    let virtual_root = n as u32;
    for &ep in &entry_points {
        if let Some(&mapped) = idx_map.get(&ep) {
            fwd_adj[virtual_root as usize].push(mapped);
        }
    }

    // Build reverse adjacency for the dominator computation
    let total_nodes = n + 1;
    let mut rev_adj: Vec<Vec<u32>> = vec![vec![]; total_nodes];
    for (src, succs) in fwd_adj.iter().enumerate() {
        for &tgt in succs {
            if (tgt as usize) < total_nodes {
                rev_adj[tgt as usize].push(src as u32);
            }
        }
    }

    // Run hand-rolled dominator computation
    let idom_raw = crate::graph::algo::compute_dominators(
        total_nodes,
        virtual_root,
        |v| &fwd_adj[v as usize],
        |v| &rev_adj[v as usize],
    );

    // Build idom map (skip virtual root)
    let mut idom: HashMap<NodeIndex, NodeIndex> = HashMap::default();
    for (dense_idx, orig) in reverse_map.iter().enumerate() {
        if let Some(Some(dom_dense)) = idom_raw.get(dense_idx) {
            let dom_dense = *dom_dense;
            if dom_dense == virtual_root {
                continue;
            }
            if let Some(&orig_dom) = reverse_map.get(dom_dense as usize) {
                idom.insert(*orig, orig_dom);
            }
        }
    }

    // Build dominated sets (transitive)
    let mut dominated: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
    for (&node, &dominator) in &idom {
        let mut current = Some(dominator);
        let mut visited: HashSet<NodeIndex> = HashSet::new();
        while let Some(dom) = current {
            if !visited.insert(dom) {
                break;
            }
            dominated.entry(dom).or_default().push(node);
            current = idom.get(&dom).copied();
        }
    }

    // Sort dominated sets by qualified name for determinism
    for v in dominated.values_mut() {
        v.sort_by(|a, b| {
            let a_qn = code_graph
                .node(*a)
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            let b_qn = code_graph
                .node(*b)
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            a_qn.cmp(b_qn)
        });
    }

    // Build call-graph predecessors map (for frontier computation)
    let mut call_predecessors: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
    for &(src, tgt) in all_call_edges {
        if func_set.contains(&src) && func_set.contains(&tgt) {
            call_predecessors.entry(tgt).or_default().push(src);
        }
    }

    // Compute domination frontiers (Cooper et al. standard algorithm)
    let mut frontier: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
    for &b in &sorted_functions {
        let preds = match call_predecessors.get(&b) {
            Some(p) if p.len() >= 2 => p,
            _ => continue,
        };
        let b_idom = idom.get(&b).copied();
        for &p in preds {
            let mut runner = p;
            loop {
                if Some(runner) == b_idom {
                    break;
                }
                frontier.entry(runner).or_default().push(b);
                match idom.get(&runner) {
                    Some(&next) if next != runner => runner = next,
                    _ => break,
                }
            }
        }
    }

    // Dedup frontier entries
    for v in frontier.values_mut() {
        v.sort_by(|a, b| {
            let a_qn = code_graph
                .node(*a)
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            let b_qn = code_graph
                .node(*b)
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            a_qn.cmp(b_qn)
        });
        v.dedup();
    }

    // Compute dominator tree depths
    let mut dom_depth: HashMap<NodeIndex, usize> = HashMap::default();
    for &f in &sorted_functions {
        if !idom.contains_key(&f) {
            dom_depth.insert(f, 0);
        }
    }
    let mut queue: VecDeque<NodeIndex> = dom_depth.keys().copied().collect();
    let mut dom_children: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();
    for (&child, &parent) in &idom {
        dom_children.entry(parent).or_default().push(child);
    }
    while let Some(node) = queue.pop_front() {
        let depth = dom_depth[&node];
        if let Some(children) = dom_children.get(&node) {
            for &child in children {
                dom_depth.insert(child, depth + 1);
                queue.push_back(child);
            }
        }
    }

    (idom, dominated, frontier, dom_depth)
}

// ═══════════════════════════════════════════════════════════════════════════════
// Algorithm 4: Articulation points + bridges (iterative Tarjan)
// ═══════════════════════════════════════════════════════════════════════════════

/// Compute articulation points and bridges on undirected projection of Calls+Imports.
/// Uses iterative DFS (not recursive) to handle 50k+ node graphs without stack overflow.
#[allow(clippy::cognitive_complexity)] // repotoire:ignore[DeepNestingDetector] — iterative DFS requires deep nesting
pub(super) fn compute_articulation_points(
    functions: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    all_import_edges: &[(NodeIndex, NodeIndex)],
    files: &[NodeIndex],
) -> (
    Vec<NodeIndex>,
    HashSet<NodeIndex>,
    Vec<(NodeIndex, NodeIndex)>,
    HashMap<NodeIndex, Vec<usize>>,
) {
    // Build undirected adjacency list from Calls + Imports
    let mut all_nodes: HashSet<NodeIndex> = HashSet::new();
    let mut adj: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::default();

    for &f in functions {
        all_nodes.insert(f);
    }
    for &f in files {
        all_nodes.insert(f);
    }

    // Add edges as undirected
    for &(src, tgt) in all_call_edges.iter().chain(all_import_edges.iter()) {
        all_nodes.insert(src);
        all_nodes.insert(tgt);
        adj.entry(src).or_default().push(tgt);
        adj.entry(tgt).or_default().push(src);
    }

    // Dedup adjacency lists
    for v in adj.values_mut() {
        v.sort_by_key(|ni| ni.index());
        v.dedup();
    }

    let n = all_nodes.len();
    if n == 0 {
        return (Vec::new(), HashSet::new(), Vec::new(), HashMap::default());
    }

    // Sort all nodes for deterministic traversal
    let mut sorted_nodes: Vec<NodeIndex> = all_nodes.iter().copied().collect();
    sorted_nodes.sort_by_key(|ni| ni.index());

    let node_to_idx: HashMap<NodeIndex, usize> = sorted_nodes
        .iter()
        .enumerate()
        .map(|(i, &ni)| (ni, i))
        .collect();

    let mut disc = vec![0u32; n];
    let mut low = vec![0u32; n];
    let mut parent = vec![usize::MAX; n];
    let mut visited = vec![false; n];
    let mut subtree_size = vec![1u32; n];
    let mut timer: u32 = 0;

    let mut ap_set: HashSet<NodeIndex> = HashSet::new();
    let mut bridges: Vec<(NodeIndex, NodeIndex)> = Vec::new();

    for &start_node in &sorted_nodes {
        let start_idx = node_to_idx[&start_node];
        if visited[start_idx] {
            continue;
        }

        let mut stack: Vec<(usize, usize)> = Vec::new();
        visited[start_idx] = true;
        timer += 1;
        disc[start_idx] = timer;
        low[start_idx] = timer;
        stack.push((start_idx, 0));

        while let Some(&mut (u_idx, ref mut pos)) = stack.last_mut() {
            let u_node = sorted_nodes[u_idx];
            let neighbors = adj.get(&u_node).map(|v| v.as_slice()).unwrap_or(&[]);

            if *pos < neighbors.len() {
                let v_node = neighbors[*pos];
                *pos += 1;

                if let Some(&v_idx) = node_to_idx.get(&v_node) {
                    if !visited[v_idx] {
                        visited[v_idx] = true;
                        parent[v_idx] = u_idx;
                        timer += 1;
                        disc[v_idx] = timer;
                        low[v_idx] = timer;
                        stack.push((v_idx, 0));
                    } else if v_idx != parent[u_idx] && disc[v_idx] < low[u_idx] {
                        low[u_idx] = disc[v_idx];
                    }
                }
            } else {
                let u_idx_copy = u_idx;
                stack.pop();

                if let Some(&mut (p_idx, _)) = stack.last_mut() {
                    if low[u_idx_copy] < low[p_idx] {
                        low[p_idx] = low[u_idx_copy];
                    }

                    subtree_size[p_idx] += subtree_size[u_idx_copy];

                    if low[u_idx_copy] > disc[p_idx] {
                        let p_node = sorted_nodes[p_idx];
                        let u_node_copy = sorted_nodes[u_idx_copy];
                        bridges.push((p_node, u_node_copy));
                    }

                    let p_node = sorted_nodes[p_idx];
                    let is_root = parent[p_idx] == usize::MAX;

                    if is_root {
                        let child_count = adj
                            .get(&p_node)
                            .map(|v| {
                                v.iter()
                                    .filter(|&&nb| {
                                        node_to_idx
                                            .get(&nb)
                                            .map(|&ni| parent[ni] == p_idx)
                                            .unwrap_or(false)
                                    })
                                    .count()
                            })
                            .unwrap_or(0);
                        if child_count > 1 {
                            ap_set.insert(p_node);
                        }
                    } else if low[u_idx_copy] >= disc[p_idx] {
                        ap_set.insert(p_node);
                    }
                }
            }
        }
    }

    let component_sizes = compute_ap_component_sizes(&ap_set, &adj, &node_to_idx, &sorted_nodes);

    let mut ap_vec: Vec<NodeIndex> = ap_set.iter().copied().collect();
    ap_vec.sort_by(|a, b| {
        let a_st = node_to_idx.get(a).map(|&i| subtree_size[i]).unwrap_or(0);
        let b_st = node_to_idx.get(b).map(|&i| subtree_size[i]).unwrap_or(0);
        b_st.cmp(&a_st).then_with(|| a.index().cmp(&b.index()))
    });

    (ap_vec, ap_set, bridges, component_sizes)
}

/// For each articulation point, compute the sizes of connected components that
/// would result from removing it (via BFS excluding the AP node).
pub(super) fn compute_ap_component_sizes(
    ap_set: &HashSet<NodeIndex>,
    adj: &HashMap<NodeIndex, Vec<NodeIndex>>,
    node_to_idx: &HashMap<NodeIndex, usize>,
    sorted_nodes: &[NodeIndex],
) -> HashMap<NodeIndex, Vec<usize>> {
    let mut component_sizes: HashMap<NodeIndex, Vec<usize>> = HashMap::default();
    for &ap in ap_set {
        let ap_idx = node_to_idx[&ap];
        let mut sizes: Vec<usize> = Vec::new();
        let mut visited_local: HashSet<usize> = HashSet::new();
        visited_local.insert(ap_idx);

        if let Some(neighbors) = adj.get(&ap) {
            for &nb in neighbors {
                if let Some(&nb_idx) = node_to_idx.get(&nb) {
                    if visited_local.contains(&nb_idx) {
                        continue;
                    }
                    let comp_size = bfs_component_size(
                        nb_idx,
                        &mut visited_local,
                        adj,
                        node_to_idx,
                        sorted_nodes,
                    );
                    sizes.push(comp_size);
                }
            }
        }

        sizes.sort_unstable_by(|a, b| b.cmp(a));
        component_sizes.insert(ap, sizes);
    }
    component_sizes
}

/// BFS from a start index, returning the number of reachable nodes.
/// `visited` is shared across calls to avoid revisiting nodes.
pub(super) fn bfs_component_size(
    start_idx: usize,
    visited: &mut HashSet<usize>,
    adj: &HashMap<NodeIndex, Vec<NodeIndex>>,
    node_to_idx: &HashMap<NodeIndex, usize>,
    sorted_nodes: &[NodeIndex],
) -> usize {
    let mut queue: VecDeque<usize> = VecDeque::new();
    queue.push_back(start_idx);
    visited.insert(start_idx);
    let mut comp_size = 0usize;
    while let Some(cur) = queue.pop_front() {
        comp_size += 1;
        let cur_node = sorted_nodes[cur];
        if let Some(cur_neighbors) = adj.get(&cur_node) {
            for &cn in cur_neighbors {
                if let Some(&cn_idx) = node_to_idx.get(&cn) {
                    if !visited.contains(&cn_idx) {
                        visited.insert(cn_idx);
                        queue.push_back(cn_idx);
                    }
                }
            }
        }
    }
    comp_size
}

// ═══════════════════════════════════════════════════════════════════════════════
// Algorithm 5: BFS call depths
// ═══════════════════════════════════════════════════════════════════════════════

/// BFS from entry points (in-degree 0 on call graph) to compute shortest-path depth.
pub(super) fn compute_call_depths(
    functions: &[NodeIndex],
    code_graph: &CodeGraph,
) -> HashMap<NodeIndex, usize> {
    let func_set: HashSet<NodeIndex> = functions.iter().copied().collect();
    let mut depth: HashMap<NodeIndex, usize> = HashMap::default();
    let mut queue: VecDeque<NodeIndex> = VecDeque::new();

    for &f in functions {
        let callers = code_graph.callers(f);
        let has_callers = callers.iter().any(|c| func_set.contains(c));
        if !has_callers {
            depth.insert(f, 0);
            queue.push_back(f);
        }
    }

    while let Some(node) = queue.pop_front() {
        let d = depth[&node];
        for &callee in code_graph.callees(node) {
            if func_set.contains(&callee) && !depth.contains_key(&callee) {
                depth.insert(callee, d + 1);
                queue.push_back(callee);
            }
        }
    }

    depth
}

// ═══════════════════════════════════════════════════════════════════════════════
// Algorithm 6: Betweenness centrality (sampled Brandes with rayon)
// ═══════════════════════════════════════════════════════════════════════════════

/// Sampled Brandes betweenness centrality with deterministic seed from edge_fingerprint.
/// Uses rayon::par_iter for parallel BFS. Stores RAW (unnormalized) values.
pub(super) fn compute_betweenness(
    functions: &[NodeIndex],
    code_graph: &CodeGraph,
    edge_fingerprint: u64,
) -> HashMap<NodeIndex, f64> {
    let n = functions.len();
    if n == 0 {
        return HashMap::default();
    }

    let func_set: HashSet<NodeIndex> = functions.iter().copied().collect();

    let sample_size = n.min(64.max(n / 4));

    let mut shuffled: Vec<NodeIndex> = functions.to_vec();
    let mut seed = edge_fingerprint;
    for i in (1..shuffled.len()).rev() {
        seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
        let j = (seed >> 33) as usize % (i + 1);
        shuffled.swap(i, j);
    }
    let sources: Vec<NodeIndex> = shuffled.into_iter().take(sample_size).collect();

    let node_to_idx: HashMap<NodeIndex, usize> = functions
        .iter()
        .enumerate()
        .map(|(i, &ni)| (ni, i))
        .collect();

    let partial_results: Vec<Vec<f64>> = sources
        .par_iter()
        .map(|&source| {
            let mut delta = vec![0.0f64; n];
            let mut sigma = vec![0.0f64; n];
            let mut dist = vec![-1i64; n];
            let mut predecessors: Vec<Vec<usize>> = vec![Vec::new(); n];
            let mut stack: Vec<usize> = Vec::new();

            let s_idx = node_to_idx[&source];
            sigma[s_idx] = 1.0;
            dist[s_idx] = 0;

            let mut queue: VecDeque<usize> = VecDeque::new();
            queue.push_back(s_idx);

            while let Some(v) = queue.pop_front() {
                stack.push(v);
                let v_node = functions[v];
                let v_dist = dist[v];

                for &callee in code_graph.callees(v_node) {
                    if !func_set.contains(&callee) {
                        continue;
                    }
                    if let Some(&w) = node_to_idx.get(&callee) {
                        if dist[w] < 0 {
                            dist[w] = v_dist + 1;
                            queue.push_back(w);
                        }
                        if dist[w] == v_dist + 1 {
                            sigma[w] += sigma[v];
                            predecessors[w].push(v);
                        }
                    }
                }
            }

            while let Some(w) = stack.pop() {
                for &v in &predecessors[w] {
                    let contrib = (sigma[v] / sigma[w]) * (1.0 + delta[w]);
                    delta[v] += contrib;
                }
            }

            delta
        })
        .collect();

    let mut betweenness = vec![0.0f64; n];
    for partial in &partial_results {
        for (i, &val) in partial.iter().enumerate() {
            betweenness[i] += val;
        }
    }

    functions
        .iter()
        .enumerate()
        .map(|(i, &ni)| (ni, betweenness[i]))
        .collect()
}
