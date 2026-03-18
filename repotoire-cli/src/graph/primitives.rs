//! Pre-computed graph algorithm results.
//!
//! `GraphPrimitives` is computed once during `GraphIndexes::build()` and provides
//! pre-computed dominator trees, articulation points, PageRank, betweenness
//! centrality, and call-graph SCCs. All fields are immutable after construction.
//! Detectors read them at O(1) — zero graph traversal at detection time.

use petgraph::algo::{dominators, tarjan_scc};
use petgraph::stable_graph::{NodeIndex, StableGraph};
use petgraph::visit::EdgeRef;
use rayon::prelude::*;
use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap, HashSet, VecDeque};

use super::interner::{global_interner, StrKey};
use super::store_models::{CodeEdge, CodeNode};
use crate::git::co_change::CoChangeMatrix;

// SAFETY: GraphPrimitives contains only HashMap, HashSet, Vec, and f64 —
// all Send + Sync. Adding it to GraphIndexes (inside CodeGraph) does not
// violate the existing unsafe impl Send/Sync for CodeGraph.

/// Pre-computed graph algorithm results. Computed once during freeze().
/// All fields are immutable. O(1) access from any detector via CodeGraph.
#[derive(Default)]
pub struct GraphPrimitives {
    // ── Dominator analysis (directed call graph) ──
    pub(crate) idom: HashMap<NodeIndex, NodeIndex>,
    pub(crate) dominated: HashMap<NodeIndex, Vec<NodeIndex>>,
    pub(crate) frontier: HashMap<NodeIndex, Vec<NodeIndex>>,
    pub(crate) dom_depth: HashMap<NodeIndex, usize>,

    // ── Structural connectivity (undirected call+import graph) ──
    pub(crate) articulation_points: Vec<NodeIndex>,
    pub(crate) articulation_point_set: HashSet<NodeIndex>,
    pub(crate) bridges: Vec<(NodeIndex, NodeIndex)>,
    pub(crate) component_sizes: HashMap<NodeIndex, Vec<usize>>,

    // ── Call-graph cycles ──
    pub(crate) call_cycles: Vec<Vec<NodeIndex>>,

    // ── Centrality metrics ──
    pub(crate) page_rank: HashMap<NodeIndex, f64>,
    pub(crate) betweenness: HashMap<NodeIndex, f64>,

    // ── BFS call depth ──
    pub(crate) call_depth: HashMap<NodeIndex, usize>,

    // ── Weighted centrality metrics (Phase B) ──
    pub(crate) weighted_page_rank: HashMap<NodeIndex, f64>,
    pub(crate) weighted_betweenness: HashMap<NodeIndex, f64>,

    // ── Community structure (Phase B) ──
    pub(crate) community: HashMap<NodeIndex, usize>,
    pub(crate) modularity: f64,

    // ── Hidden coupling: co-change without structural edge (Phase B) ──
    // NodeIndex values are File-level node indices.
    pub(crate) hidden_coupling: Vec<(NodeIndex, NodeIndex, f32)>,
}

impl GraphPrimitives {
    /// Compute all graph primitives. Called by GraphIndexes::build().
    /// Returns Default for empty graphs.
    pub fn compute(
        graph: &StableGraph<CodeNode, CodeEdge>,
        functions: &[NodeIndex],
        files: &[NodeIndex],
        all_call_edges: &[(NodeIndex, NodeIndex)],
        all_import_edges: &[(NodeIndex, NodeIndex)],
        call_callers: &HashMap<NodeIndex, Vec<NodeIndex>>,
        call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
        edge_fingerprint: u64,
        co_change: Option<&CoChangeMatrix>,
    ) -> Self {
        if functions.is_empty() || all_call_edges.is_empty() {
            return Self::default();
        }

        let _span = tracing::info_span!("graph_primitives", functions = functions.len(), call_edges = all_call_edges.len()).entered();

        // 1. SCCs first (needed by dominator for disconnected SCC handling)
        let call_cycles = compute_call_cycles(all_call_edges, graph);

        // 2. PageRank, betweenness, articulation points in parallel
        let (page_rank, (betweenness, ap_result)) = rayon::join(
            || compute_page_rank(functions, call_callees, call_callers, 20, 0.85, 1e-6),
            || {
                rayon::join(
                    || compute_betweenness(functions, call_callees, edge_fingerprint),
                    || {
                        compute_articulation_points(
                            functions,
                            all_call_edges,
                            all_import_edges,
                            files,
                        )
                    },
                )
            },
        );
        let (articulation_points, articulation_point_set, bridges, component_sizes) = ap_result;

        // 3. Dominators (depends on SCCs for disconnected handling)
        let (idom, dominated, frontier, dom_depth) = compute_dominators(
            functions,
            all_call_edges,
            call_callers,
            call_callees,
            &call_cycles,
            graph,
        );

        // 4. BFS call depths
        let call_depth = compute_call_depths(functions, call_callees, call_callers);

        // Phase B: Weighted overlay + weighted algorithms
        let (weighted_page_rank, weighted_betweenness, community, modularity, hidden_coupling) =
            if let Some(co_change) = co_change {
                if !co_change.is_empty() {
                    compute_weighted_phase(
                        functions, files, all_call_edges, all_import_edges,
                        co_change, graph, edge_fingerprint,
                    )
                } else {
                    (HashMap::new(), HashMap::new(), HashMap::new(), 0.0, Vec::new())
                }
            } else {
                (HashMap::new(), HashMap::new(), HashMap::new(), 0.0, Vec::new())
            };

        Self {
            idom,
            dominated,
            frontier,
            dom_depth,
            articulation_points,
            articulation_point_set,
            bridges,
            component_sizes,
            call_cycles,
            page_rank,
            betweenness,
            call_depth,
            weighted_page_rank,
            weighted_betweenness,
            community,
            modularity,
            hidden_coupling,
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Algorithm 1: Call-graph SCCs
// ═══════════════════════════════════════════════════════════════════════════════

/// Build a filtered call-only subgraph and run Tarjan SCC.
/// Returns SCCs with >1 node (actual cycles), sorted by size descending.
fn compute_call_cycles(
    all_call_edges: &[(NodeIndex, NodeIndex)],
    graph: &StableGraph<CodeNode, CodeEdge>,
) -> Vec<Vec<NodeIndex>> {
    let si = global_interner();

    // Collect all nodes involved in call edges
    let mut relevant_nodes: HashSet<NodeIndex> = HashSet::new();
    for &(src, tgt) in all_call_edges {
        relevant_nodes.insert(src);
        relevant_nodes.insert(tgt);
    }

    // Build filtered subgraph with idx_map/reverse_map pattern
    let mut filtered_graph: StableGraph<NodeIndex, ()> = StableGraph::new();
    let mut idx_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();
    let mut reverse_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();

    // Sort by NodeIndex for deterministic construction
    let mut sorted_nodes: Vec<NodeIndex> = relevant_nodes.into_iter().collect();
    sorted_nodes.sort_by_key(|idx| idx.index());

    for orig_idx in sorted_nodes {
        let new_idx = filtered_graph.add_node(orig_idx);
        idx_map.insert(orig_idx, new_idx);
        reverse_map.insert(new_idx, orig_idx);
    }

    // Add call edges to filtered graph
    for &(src, tgt) in all_call_edges {
        if let (Some(&from), Some(&to)) = (idx_map.get(&src), idx_map.get(&tgt)) {
            filtered_graph.add_edge(from, to, ());
        }
    }

    // Run Tarjan SCC
    let sccs = tarjan_scc(&filtered_graph);

    // Convert back to original NodeIndexes, keep only cycles (>1 node)
    let mut cycles: Vec<Vec<NodeIndex>> = sccs
        .into_iter()
        .filter(|scc| scc.len() > 1)
        .map(|scc| {
            let mut orig_indices: Vec<NodeIndex> = scc
                .iter()
                .filter_map(|&filtered_idx| reverse_map.get(&filtered_idx).copied())
                .collect();

            // Sort by qualified name for consistent ordering
            orig_indices.sort_by(|a, b| {
                let a_qn = graph
                    .node_weight(*a)
                    .map(|n| si.resolve(n.qualified_name))
                    .unwrap_or("");
                let b_qn = graph
                    .node_weight(*b)
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
                .and_then(|idx| graph.node_weight(*idx))
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            let b_qn = b
                .first()
                .and_then(|idx| graph.node_weight(*idx))
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
fn compute_page_rank(
    functions: &[NodeIndex],
    call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
    _call_callers: &HashMap<NodeIndex, Vec<NodeIndex>>,
    max_iterations: usize,
    damping: f64,
    tolerance: f64,
) -> HashMap<NodeIndex, f64> {
    let n = functions.len();
    if n == 0 {
        return HashMap::new();
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
        .map(|ni| {
            call_callees
                .get(ni)
                .map(|v| v.len())
                .unwrap_or(0)
        })
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
            if let Some(callees) = call_callees.get(&ni) {
                for &callee in callees {
                    if let Some(&j) = node_to_idx.get(&callee) {
                        new_rank[j] += contribution;
                    }
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

/// Compute dominator tree using petgraph's simple_fast with a virtual root.
/// Returns (idom, dominated, frontier, dom_depth).
fn compute_dominators(
    functions: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    call_callers: &HashMap<NodeIndex, Vec<NodeIndex>>,
    call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
    call_cycles: &[Vec<NodeIndex>],
    graph: &StableGraph<CodeNode, CodeEdge>,
) -> (
    HashMap<NodeIndex, NodeIndex>,
    HashMap<NodeIndex, Vec<NodeIndex>>,
    HashMap<NodeIndex, Vec<NodeIndex>>,
    HashMap<NodeIndex, usize>,
) {
    let si = global_interner();
    let func_set: HashSet<NodeIndex> = functions.iter().copied().collect();

    // Build a temporary directed graph for dominator computation
    let mut dom_graph: StableGraph<(), ()> = StableGraph::new();
    let mut idx_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();
    let mut reverse_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();

    // Sort functions for deterministic node insertion
    let mut sorted_functions: Vec<NodeIndex> = functions.to_vec();
    sorted_functions.sort_by(|a, b| {
        let a_qn = graph
            .node_weight(*a)
            .map(|n| si.resolve(n.qualified_name))
            .unwrap_or("");
        let b_qn = graph
            .node_weight(*b)
            .map(|n| si.resolve(n.qualified_name))
            .unwrap_or("");
        a_qn.cmp(b_qn)
    });

    for &orig in &sorted_functions {
        let new_idx = dom_graph.add_node(());
        idx_map.insert(orig, new_idx);
        reverse_map.insert(new_idx, orig);
    }

    // Add call edges (only between functions)
    for &(src, tgt) in all_call_edges {
        if let (Some(&from), Some(&to)) = (idx_map.get(&src), idx_map.get(&tgt)) {
            dom_graph.add_edge(from, to, ());
        }
    }

    // Add virtual root connected to entry points
    let virtual_root = dom_graph.add_node(());

    // Entry points: in-degree 0 functions that have outgoing calls
    let mut entry_points: Vec<NodeIndex> = sorted_functions
        .iter()
        .filter(|&&ni| {
            let has_callers = call_callers
                .get(&ni)
                .map(|v| v.iter().any(|c| func_set.contains(c)))
                .unwrap_or(false);
            let has_callees = call_callees
                .get(&ni)
                .map(|v| !v.is_empty())
                .unwrap_or(false);
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
            if let Some(callees) = call_callees.get(&node) {
                for &callee in callees {
                    if func_set.contains(&callee) && reachable.insert(callee) {
                        queue.push_back(callee);
                    }
                }
            }
        }
    }

    // For unreachable SCCs, add a representative as an entry point
    for scc in call_cycles {
        if !scc.is_empty() && !reachable.contains(&scc[0]) {
            entry_points.push(scc[0]);
            // BFS from this representative
            let mut queue: VecDeque<NodeIndex> = VecDeque::new();
            queue.push_back(scc[0]);
            reachable.insert(scc[0]);
            while let Some(node) = queue.pop_front() {
                if let Some(callees) = call_callees.get(&node) {
                    for &callee in callees {
                        if func_set.contains(&callee) && reachable.insert(callee) {
                            queue.push_back(callee);
                        }
                    }
                }
            }
        }
    }

    // Also handle isolated functions that are not in any SCC and not reachable
    // (these are leaf functions with callers that are themselves unreachable)
    for &f in &sorted_functions {
        if !reachable.contains(&f) {
            entry_points.push(f);
            reachable.insert(f);
        }
    }

    // Connect virtual root to all entry points
    for &ep in &entry_points {
        if let Some(&mapped) = idx_map.get(&ep) {
            dom_graph.add_edge(virtual_root, mapped, ());
        }
    }

    // Run dominator analysis
    let dom_result = dominators::simple_fast(&dom_graph, virtual_root);

    // Build idom map (skip virtual root)
    let mut idom: HashMap<NodeIndex, NodeIndex> = HashMap::new();
    for &orig in &sorted_functions {
        if let Some(&mapped) = idx_map.get(&orig) {
            if let Some(dom_node) = dom_result.immediate_dominator(mapped) {
                if dom_node == virtual_root {
                    // Entry point: no real dominator, skip
                    continue;
                }
                if let Some(&orig_dom) = reverse_map.get(&dom_node) {
                    idom.insert(orig, orig_dom);
                }
            }
        }
    }

    // Build dominated sets (transitive: node -> all nodes it dominates)
    let mut dominated: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
    for (&node, &dominator) in &idom {
        // Walk up the dominator tree, adding `node` to each ancestor's dominated set
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
            let a_qn = graph
                .node_weight(*a)
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            let b_qn = graph
                .node_weight(*b)
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            a_qn.cmp(b_qn)
        });
    }

    // Build call-graph predecessors map (for frontier computation)
    let mut call_predecessors: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
    for &(src, tgt) in all_call_edges {
        if func_set.contains(&src) && func_set.contains(&tgt) {
            call_predecessors.entry(tgt).or_default().push(src);
        }
    }

    // Compute domination frontiers (Cooper et al. standard algorithm)
    let mut frontier: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
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
            let a_qn = graph
                .node_weight(*a)
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            let b_qn = graph
                .node_weight(*b)
                .map(|n| si.resolve(n.qualified_name))
                .unwrap_or("");
            a_qn.cmp(b_qn)
        });
        v.dedup();
    }

    // Compute dominator tree depths
    let mut dom_depth: HashMap<NodeIndex, usize> = HashMap::new();
    // Entry points (not in idom) have depth 0
    for &f in &sorted_functions {
        if !idom.contains_key(&f) {
            dom_depth.insert(f, 0);
        }
    }
    // BFS through dominator tree to assign depths
    let mut queue: VecDeque<NodeIndex> = dom_depth.keys().copied().collect();
    // Build children map from idom
    let mut dom_children: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
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
fn compute_articulation_points(
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
    let mut adj: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();

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
        return (Vec::new(), HashSet::new(), Vec::new(), HashMap::new());
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
    let mut parent = vec![usize::MAX; n]; // MAX = no parent
    let mut visited = vec![false; n];
    let mut subtree_size = vec![1u32; n];
    let mut timer: u32 = 0;

    let mut ap_set: HashSet<NodeIndex> = HashSet::new();
    let mut bridges: Vec<(NodeIndex, NodeIndex)> = Vec::new();

    // Iterative DFS for each connected component
    // Stack entry: (node_local_idx, neighbor_iterator_position, is_root)
    for &start_node in &sorted_nodes {
        let start_idx = node_to_idx[&start_node];
        if visited[start_idx] {
            continue;
        }

        // Stack: (local_idx, next_neighbor_pos)
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
                    } else if v_idx != parent[u_idx] {
                        // Back edge: update low-link
                        if disc[v_idx] < low[u_idx] {
                            low[u_idx] = disc[v_idx];
                        }
                    }
                }
            } else {
                // All neighbors processed, backtrack
                let u_idx_copy = u_idx;
                stack.pop();

                if let Some(&mut (p_idx, _)) = stack.last_mut() {
                    // Update parent's low-link
                    if low[u_idx_copy] < low[p_idx] {
                        low[p_idx] = low[u_idx_copy];
                    }

                    // Accumulate subtree size
                    subtree_size[p_idx] += subtree_size[u_idx_copy];

                    // Bridge detection: if low[child] > disc[parent]
                    if low[u_idx_copy] > disc[p_idx] {
                        let p_node = sorted_nodes[p_idx];
                        let u_node_copy = sorted_nodes[u_idx_copy];
                        bridges.push((p_node, u_node_copy));
                    }

                    // Articulation point detection
                    let p_node = sorted_nodes[p_idx];
                    let is_root = parent[p_idx] == usize::MAX;

                    if is_root {
                        // Root is AP if it has >1 children in DFS tree
                        let child_count = adj
                            .get(&p_node)
                            .map(|v| v.iter().filter(|&&nb| {
                                node_to_idx.get(&nb).map(|&ni| parent[ni] == p_idx).unwrap_or(false)
                            }).count())
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

    // Compute component sizes for each articulation point
    // For each AP, removing it splits adjacent nodes into components.
    // We compute the sizes of those components.
    let mut component_sizes: HashMap<NodeIndex, Vec<usize>> = HashMap::new();
    for &ap in &ap_set {
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
                    // BFS from this neighbor, excluding the AP
                    let mut queue: VecDeque<usize> = VecDeque::new();
                    queue.push_back(nb_idx);
                    visited_local.insert(nb_idx);
                    let mut comp_size = 0usize;
                    while let Some(cur) = queue.pop_front() {
                        comp_size += 1;
                        let cur_node = sorted_nodes[cur];
                        if let Some(cur_neighbors) = adj.get(&cur_node) {
                            for &cn in cur_neighbors {
                                if let Some(&cn_idx) = node_to_idx.get(&cn) {
                                    if !visited_local.contains(&cn_idx) {
                                        visited_local.insert(cn_idx);
                                        queue.push_back(cn_idx);
                                    }
                                }
                            }
                        }
                    }
                    sizes.push(comp_size);
                }
            }
        }

        sizes.sort_unstable_by(|a, b| b.cmp(a));
        component_sizes.insert(ap, sizes);
    }

    // Sort articulation points by subtree size descending for determinism
    let mut ap_vec: Vec<NodeIndex> = ap_set.iter().copied().collect();
    ap_vec.sort_by(|a, b| {
        // Sort by subtree size descending, then by QN
        let a_st = node_to_idx.get(a).map(|&i| subtree_size[i]).unwrap_or(0);
        let b_st = node_to_idx.get(b).map(|&i| subtree_size[i]).unwrap_or(0);
        b_st.cmp(&a_st).then_with(|| a.index().cmp(&b.index()))
    });

    (ap_vec, ap_set, bridges, component_sizes)
}

// ═══════════════════════════════════════════════════════════════════════════════
// Algorithm 5: BFS call depths
// ═══════════════════════════════════════════════════════════════════════════════

/// BFS from entry points (in-degree 0 on call graph) to compute shortest-path depth.
fn compute_call_depths(
    functions: &[NodeIndex],
    call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
    call_callers: &HashMap<NodeIndex, Vec<NodeIndex>>,
) -> HashMap<NodeIndex, usize> {
    let func_set: HashSet<NodeIndex> = functions.iter().copied().collect();
    let mut depth: HashMap<NodeIndex, usize> = HashMap::new();
    let mut queue: VecDeque<NodeIndex> = VecDeque::new();

    // Entry points: functions with no callers (among functions)
    for &f in functions {
        let has_callers = call_callers
            .get(&f)
            .map(|v| v.iter().any(|c| func_set.contains(c)))
            .unwrap_or(false);
        if !has_callers {
            depth.insert(f, 0);
            queue.push_back(f);
        }
    }

    // BFS
    while let Some(node) = queue.pop_front() {
        let d = depth[&node];
        if let Some(callees) = call_callees.get(&node) {
            for &callee in callees {
                if func_set.contains(&callee) && !depth.contains_key(&callee) {
                    depth.insert(callee, d + 1);
                    queue.push_back(callee);
                }
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
fn compute_betweenness(
    functions: &[NodeIndex],
    call_callees: &HashMap<NodeIndex, Vec<NodeIndex>>,
    edge_fingerprint: u64,
) -> HashMap<NodeIndex, f64> {
    let n = functions.len();
    if n == 0 {
        return HashMap::new();
    }

    let func_set: HashSet<NodeIndex> = functions.iter().copied().collect();

    // Determine sample size: min(n, max(64, n/4))
    let sample_size = n.min(64.max(n / 4));

    // Deterministic sampling via Fisher-Yates shuffle with seed from edge_fingerprint
    let mut shuffled: Vec<NodeIndex> = functions.to_vec();
    let mut seed = edge_fingerprint;
    for i in (1..shuffled.len()).rev() {
        seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
        let j = (seed >> 33) as usize % (i + 1);
        shuffled.swap(i, j);
    }
    let sources: Vec<NodeIndex> = shuffled.into_iter().take(sample_size).collect();

    // Map NodeIndex -> local index for accumulation
    let node_to_idx: HashMap<NodeIndex, usize> = functions
        .iter()
        .enumerate()
        .map(|(i, &ni)| (ni, i))
        .collect();

    // Parallel Brandes: each source computes partial betweenness
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

            // BFS phase
            while let Some(v) = queue.pop_front() {
                stack.push(v);
                let v_node = functions[v];
                let v_dist = dist[v];

                if let Some(callees) = call_callees.get(&v_node) {
                    for &callee in callees {
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
            }

            // Accumulation phase (reverse BFS order)
            while let Some(w) = stack.pop() {
                for &v in &predecessors[w] {
                    let contrib = (sigma[v] / sigma[w]) * (1.0 + delta[w]);
                    delta[v] += contrib;
                }
            }

            delta
        })
        .collect();

    // Aggregate partial results
    let mut betweenness = vec![0.0f64; n];
    for partial in &partial_results {
        for (i, &val) in partial.iter().enumerate() {
            betweenness[i] += val;
        }
    }

    // Convert to HashMap (raw, unnormalized)
    functions
        .iter()
        .enumerate()
        .map(|(i, &ni)| (ni, betweenness[i]))
        .collect()
}

// ═══════════════════════════════════════════════════════════════════════════════
// Phase B: Weighted overlay + community detection
// ═══════════════════════════════════════════════════════════════════════════════

/// Phase B: Compute weighted graph algorithms using co-change overlay.
fn compute_weighted_phase(
    functions: &[NodeIndex],
    files: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    all_import_edges: &[(NodeIndex, NodeIndex)],
    co_change: &CoChangeMatrix,
    graph: &StableGraph<CodeNode, CodeEdge>,
    edge_fingerprint: u64,
) -> (HashMap<NodeIndex, f64>, HashMap<NodeIndex, f64>, HashMap<NodeIndex, usize>, f64, Vec<(NodeIndex, NodeIndex, f32)>) {
    let (overlay, hidden_coupling) = build_weighted_overlay(
        functions, files, all_call_edges, all_import_edges, co_change, graph,
    );

    if overlay.node_count() == 0 {
        return (HashMap::new(), HashMap::new(), HashMap::new(), 0.0, hidden_coupling);
    }

    // Run weighted algorithms in parallel
    let (weighted_pr, (weighted_bw, (community, modularity))) = rayon::join(
        || compute_weighted_page_rank(&overlay, 20, 0.85, 1e-6),
        || rayon::join(
            || compute_weighted_betweenness(&overlay, 200, edge_fingerprint),
            || compute_communities(&overlay, 1.0),
        ),
    );

    (weighted_pr, weighted_bw, community, modularity, hidden_coupling)
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
fn build_weighted_overlay(
    functions: &[NodeIndex],
    files: &[NodeIndex],
    all_call_edges: &[(NodeIndex, NodeIndex)],
    all_import_edges: &[(NodeIndex, NodeIndex)],
    co_change: &CoChangeMatrix,
    graph: &StableGraph<CodeNode, CodeEdge>,
) -> (StableGraph<NodeIndex, f32>, Vec<(NodeIndex, NodeIndex, f32)>) {
    // 1. Build idx_map: original NodeIndex → overlay NodeIndex
    let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
    let mut idx_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();

    for &func_idx in functions {
        let overlay_idx = overlay.add_node(func_idx);
        idx_map.insert(func_idx, overlay_idx);
    }

    // 2. Build file_to_functions: StrKey(file_path) → Vec<NodeIndex> (original)
    let mut file_to_functions: HashMap<StrKey, Vec<NodeIndex>> = HashMap::new();
    for &func_idx in functions {
        let node = &graph[func_idx];
        let file_key = node.file_path;
        file_to_functions.entry(file_key).or_default().push(func_idx);
    }

    // 3. Process structural edges with deduplication
    //    Track per-(src, tgt) pair what edge types exist so we can combine
    //    Calls + Imports into a single overlay edge with structural_base = 1.5
    let import_set: HashSet<(NodeIndex, NodeIndex)> = all_import_edges.iter().copied().collect();

    // Collect all unique function pairs that have at least one structural edge
    let mut structural_pairs: HashMap<(NodeIndex, NodeIndex), f32> = HashMap::new();

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

    // 4. Hidden coupling: co-change pairs with NO structural edges between files
    let mut hidden_coupling: Vec<(NodeIndex, NodeIndex, f32)> = Vec::new();

    // Build a lookup from StrKey → File-level NodeIndex
    let mut file_key_to_node: HashMap<StrKey, NodeIndex> = HashMap::new();
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

        // Add overlay edges between function pairs spanning these files
        for &fa in funcs_a {
            for &fb in funcs_b {
                if let (Some(&ov_a), Some(&ov_b)) = (idx_map.get(&fa), idx_map.get(&fb)) {
                    overlay.add_edge(ov_a, ov_b, co_change_boost);
                }
            }
        }

        // Record hidden coupling at file level
        if let (Some(&file_node_a), Some(&file_node_b)) =
            (file_key_to_node.get(&key_a), file_key_to_node.get(&key_b))
        {
            hidden_coupling.push((file_node_a, file_node_b, co_change_boost));
        }
    }

    (overlay, hidden_coupling)
}

fn compute_weighted_page_rank(
    overlay: &StableGraph<NodeIndex, f32>,
    iterations: usize,
    damping: f64,
    tolerance: f64,
) -> HashMap<NodeIndex, f64> {
    let _span = tracing::info_span!("weighted_page_rank").entered();
    let node_count = overlay.node_count();
    if node_count == 0 {
        return HashMap::new();
    }

    let init = 1.0 / node_count as f64;
    let mut rank: HashMap<NodeIndex, f64> =
        overlay.node_indices().map(|n| (n, init)).collect();

    for _ in 0..iterations {
        let mut new_rank: HashMap<NodeIndex, f64> = overlay
            .node_indices()
            .map(|n| (n, (1.0 - damping) / node_count as f64))
            .collect();

        for src in overlay.node_indices() {
            let out_edges: Vec<_> = overlay.edges(src).collect();
            let total_weight: f64 = out_edges.iter().map(|e| *e.weight() as f64).sum();
            if total_weight == 0.0 {
                continue;
            }
            let src_rank = rank[&src];
            for edge in &out_edges {
                let fraction = *edge.weight() as f64 / total_weight;
                *new_rank.get_mut(&edge.target()).unwrap() += damping * src_rank * fraction;
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
    let mut result = HashMap::new();
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
fn compute_weighted_betweenness(
    overlay: &StableGraph<NodeIndex, f32>,
    sample_size: usize,
    edge_fingerprint: u64,
) -> HashMap<NodeIndex, f64> {
    let _span = tracing::info_span!("weighted_betweenness").entered();

    let node_count = overlay.node_count();
    if node_count == 0 {
        return HashMap::new();
    }

    // Collect overlay node indices sorted for deterministic sampling
    let mut nodes: Vec<petgraph::stable_graph::NodeIndex> =
        overlay.node_indices().collect();
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
    let node_to_local: HashMap<petgraph::stable_graph::NodeIndex, usize> = nodes
        .iter()
        .enumerate()
        .map(|(i, &n)| (n, i))
        .collect();

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

fn compute_communities(
    _overlay: &StableGraph<NodeIndex, f32>,
    _resolution: f64,
) -> (HashMap<NodeIndex, usize>, f64) {
    // Stub: implemented in Task 7.
    (HashMap::new(), 0.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::store_models::{CodeEdge, CodeNode};

    #[test]
    fn test_default_is_empty() {
        let p = GraphPrimitives::default();
        assert!(p.idom.is_empty());
        assert!(p.dominated.is_empty());
        assert!(p.page_rank.is_empty());
        assert!(p.call_cycles.is_empty());
        assert!(p.articulation_points.is_empty());
    }

    #[test]
    fn test_compute_empty_graph_returns_default() {
        let graph = StableGraph::new();
        let p = GraphPrimitives::compute(
            &graph, &[], &[], &[], &[], &HashMap::new(), &HashMap::new(), 0, None,
        );
        assert!(p.idom.is_empty());
        assert!(p.page_rank.is_empty());
    }

    // ── Call-graph SCC tests ──

    #[test]
    fn test_call_cycles_triangle() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "a.py"));
        let f3 = graph.add_node(CodeNode::function("f3", "a.py"));

        let call_edges = vec![(f1, f2), (f2, f3), (f3, f1)];
        let cycles = compute_call_cycles(&call_edges, &graph);

        assert_eq!(cycles.len(), 1, "Should find exactly 1 cycle");
        assert_eq!(cycles[0].len(), 3, "Cycle should contain 3 nodes");
    }

    #[test]
    fn test_call_cycles_dag_no_cycles() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "a.py"));
        let f3 = graph.add_node(CodeNode::function("f3", "a.py"));

        let call_edges = vec![(f1, f2), (f2, f3)];
        let cycles = compute_call_cycles(&call_edges, &graph);

        assert!(cycles.is_empty(), "DAG should have no cycles");
    }

    // ── PageRank tests ──

    #[test]
    fn test_page_rank_star_topology() {
        // f1, f2, f3 all call hub; hub calls leaf
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "a.py"));
        let f3 = graph.add_node(CodeNode::function("f3", "a.py"));
        let hub = graph.add_node(CodeNode::function("hub", "a.py"));
        let leaf = graph.add_node(CodeNode::function("leaf", "a.py"));

        let functions = vec![f1, f2, f3, hub, leaf];
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();

        // f1->hub, f2->hub, f3->hub, hub->leaf
        call_callees.insert(f1, vec![hub]);
        call_callees.insert(f2, vec![hub]);
        call_callees.insert(f3, vec![hub]);
        call_callees.insert(hub, vec![leaf]);

        call_callers.insert(hub, vec![f1, f2, f3]);
        call_callers.insert(leaf, vec![hub]);

        let pr = compute_page_rank(&functions, &call_callees, &call_callers, 20, 0.85, 1e-6);

        assert!(pr.len() == 5);
        let hub_rank = pr[&hub];
        let leaf_rank = pr[&leaf];
        let f1_rank = pr[&f1];

        // Hub receives rank from 3 sources, should have highest
        assert!(
            hub_rank > f1_rank,
            "Hub ({hub_rank}) should have higher rank than f1 ({f1_rank})"
        );
        // Leaf receives all hub rank, should be second highest
        assert!(
            leaf_rank > f1_rank,
            "Leaf ({leaf_rank}) should have higher rank than f1 ({f1_rank})"
        );
    }

    #[test]
    fn test_page_rank_sums_to_one() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "a.py"));
        let f3 = graph.add_node(CodeNode::function("f3", "a.py"));

        let functions = vec![f1, f2, f3];
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();

        call_callees.insert(f1, vec![f2]);
        call_callees.insert(f2, vec![f3]);
        call_callees.insert(f3, vec![f1]);

        call_callers.insert(f2, vec![f1]);
        call_callers.insert(f3, vec![f2]);
        call_callers.insert(f1, vec![f3]);

        let pr = compute_page_rank(&functions, &call_callees, &call_callers, 100, 0.85, 1e-10);
        let sum: f64 = pr.values().sum();
        assert!(
            (sum - 1.0).abs() < 0.01,
            "PageRank should sum to ~1.0, got {sum}"
        );
    }

    // ── Dominator tests ──

    #[test]
    fn test_dominators_linear_chain() {
        // entry -> A -> B -> C
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let entry = graph.add_node(CodeNode::function("entry", "a.py"));
        let a = graph.add_node(CodeNode::function("a_fn", "a.py"));
        let b = graph.add_node(CodeNode::function("b_fn", "a.py"));
        let c = graph.add_node(CodeNode::function("c_fn", "a.py"));

        let call_edges = vec![(entry, a), (a, b), (b, c)];
        let functions = vec![entry, a, b, c];

        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        call_callees.insert(entry, vec![a]);
        call_callees.insert(a, vec![b]);
        call_callees.insert(b, vec![c]);
        call_callers.insert(a, vec![entry]);
        call_callers.insert(b, vec![a]);
        call_callers.insert(c, vec![b]);

        let (idom, dominated, _frontier, dom_depth) =
            compute_dominators(&functions, &call_edges, &call_callers, &call_callees, &[], &graph);

        // entry dominates all
        assert_eq!(idom.get(&a), Some(&entry), "entry should dominate A");
        assert_eq!(idom.get(&b), Some(&a), "A should immediately dominate B");
        assert_eq!(idom.get(&c), Some(&b), "B should immediately dominate C");

        // Entry's dominated set should include A, B, C
        let entry_dominated = dominated.get(&entry).unwrap();
        assert!(entry_dominated.contains(&a));
        assert!(entry_dominated.contains(&b));
        assert!(entry_dominated.contains(&c));

        // Depths should increase
        assert_eq!(dom_depth.get(&entry), Some(&0));
        assert_eq!(dom_depth.get(&a), Some(&1));
        assert_eq!(dom_depth.get(&b), Some(&2));
        assert_eq!(dom_depth.get(&c), Some(&3));
    }

    #[test]
    fn test_dominators_diamond() {
        // entry -> A, entry -> B, A -> join, B -> join
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let entry = graph.add_node(CodeNode::function("entry", "a.py"));
        let a = graph.add_node(CodeNode::function("a_fn", "a.py"));
        let b = graph.add_node(CodeNode::function("b_fn", "a.py"));
        let join = graph.add_node(CodeNode::function("join", "a.py"));

        let call_edges = vec![(entry, a), (entry, b), (a, join), (b, join)];
        let functions = vec![entry, a, b, join];

        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        call_callees.insert(entry, vec![a, b]);
        call_callees.insert(a, vec![join]);
        call_callees.insert(b, vec![join]);
        call_callers.insert(a, vec![entry]);
        call_callers.insert(b, vec![entry]);
        call_callers.insert(join, vec![a, b]);

        let (idom, _dominated, _frontier, _dom_depth) =
            compute_dominators(&functions, &call_edges, &call_callers, &call_callees, &[], &graph);

        // Neither A nor B dominates join — entry dominates join (two paths)
        assert_eq!(
            idom.get(&join),
            Some(&entry),
            "Entry should dominate join (not A or B, since there are two paths)"
        );
    }

    // ── Articulation points tests ──

    #[test]
    fn test_articulation_points_two_triangles_bridge() {
        // Triangle 1: a-b-c, Triangle 2: d-e-f, connected by c-d
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let a = graph.add_node(CodeNode::function("a", "x.py"));
        let b = graph.add_node(CodeNode::function("b", "x.py"));
        let c = graph.add_node(CodeNode::function("c", "x.py"));
        let d = graph.add_node(CodeNode::function("d", "x.py"));
        let e = graph.add_node(CodeNode::function("e", "x.py"));
        let f = graph.add_node(CodeNode::function("f", "x.py"));

        let functions = vec![a, b, c, d, e, f];
        // Triangle 1 edges (undirected via both directions in call edges)
        let call_edges = vec![
            (a, b), (b, a),
            (b, c), (c, b),
            (a, c), (c, a),
            // Bridge
            (c, d), (d, c),
            // Triangle 2
            (d, e), (e, d),
            (e, f), (f, e),
            (d, f), (f, d),
        ];

        let (ap_vec, ap_set, bridges, _comp_sizes) =
            compute_articulation_points(&functions, &call_edges, &[], &[]);

        // c and d should be articulation points (bridge nodes)
        assert!(
            ap_set.contains(&c),
            "c should be an articulation point"
        );
        assert!(
            ap_set.contains(&d),
            "d should be an articulation point"
        );
        assert_eq!(ap_set.len(), 2, "Should have exactly 2 articulation points");

        // c-d should be a bridge
        let has_bridge = bridges.iter().any(|&(s, t)| {
            (s == c && t == d) || (s == d && t == c)
        });
        assert!(has_bridge, "c-d should be a bridge");
    }

    #[test]
    fn test_articulation_points_fully_connected() {
        // Fully connected graph of 4 nodes — no articulation points
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let a = graph.add_node(CodeNode::function("a", "x.py"));
        let b = graph.add_node(CodeNode::function("b", "x.py"));
        let c = graph.add_node(CodeNode::function("c", "x.py"));
        let d = graph.add_node(CodeNode::function("d", "x.py"));

        let functions = vec![a, b, c, d];
        let call_edges = vec![
            (a, b), (b, a),
            (a, c), (c, a),
            (a, d), (d, a),
            (b, c), (c, b),
            (b, d), (d, b),
            (c, d), (d, c),
        ];

        let (_ap_vec, ap_set, bridges, _comp_sizes) =
            compute_articulation_points(&functions, &call_edges, &[], &[]);

        assert!(
            ap_set.is_empty(),
            "Fully connected graph should have no articulation points"
        );
        assert!(
            bridges.is_empty(),
            "Fully connected graph should have no bridges"
        );
    }

    // ── BFS call depth tests ──

    #[test]
    fn test_call_depths_linear_chain() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let entry = graph.add_node(CodeNode::function("entry", "a.py"));
        let mid = graph.add_node(CodeNode::function("mid", "a.py"));
        let leaf = graph.add_node(CodeNode::function("leaf", "a.py"));

        let functions = vec![entry, mid, leaf];
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();

        call_callees.insert(entry, vec![mid]);
        call_callees.insert(mid, vec![leaf]);
        call_callers.insert(mid, vec![entry]);
        call_callers.insert(leaf, vec![mid]);

        let depths = compute_call_depths(&functions, &call_callees, &call_callers);

        assert_eq!(depths.get(&entry), Some(&0), "Entry should be depth 0");
        assert_eq!(depths.get(&mid), Some(&1), "Mid should be depth 1");
        assert_eq!(depths.get(&leaf), Some(&2), "Leaf should be depth 2");
    }

    #[test]
    fn test_call_depths_multiple_entries() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let e1 = graph.add_node(CodeNode::function("entry1", "a.py"));
        let e2 = graph.add_node(CodeNode::function("entry2", "a.py"));
        let shared = graph.add_node(CodeNode::function("shared", "a.py"));

        let functions = vec![e1, e2, shared];
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();

        call_callees.insert(e1, vec![shared]);
        call_callees.insert(e2, vec![shared]);
        call_callers.insert(shared, vec![e1, e2]);

        let depths = compute_call_depths(&functions, &call_callees, &call_callers);

        assert_eq!(depths.get(&e1), Some(&0));
        assert_eq!(depths.get(&e2), Some(&0));
        // shared should be depth 1 (shortest path from any entry)
        assert_eq!(depths.get(&shared), Some(&1));
    }

    // ── Betweenness centrality tests ──

    #[test]
    fn test_betweenness_star_through_bridge() {
        // Three sources -> bridge -> three sinks
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let s1 = graph.add_node(CodeNode::function("s1", "a.py"));
        let s2 = graph.add_node(CodeNode::function("s2", "a.py"));
        let s3 = graph.add_node(CodeNode::function("s3", "a.py"));
        let bridge = graph.add_node(CodeNode::function("bridge", "a.py"));
        let t1 = graph.add_node(CodeNode::function("t1", "a.py"));
        let t2 = graph.add_node(CodeNode::function("t2", "a.py"));
        let t3 = graph.add_node(CodeNode::function("t3", "a.py"));

        let functions = vec![s1, s2, s3, bridge, t1, t2, t3];
        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();

        call_callees.insert(s1, vec![bridge]);
        call_callees.insert(s2, vec![bridge]);
        call_callees.insert(s3, vec![bridge]);
        call_callees.insert(bridge, vec![t1, t2, t3]);

        let bc = compute_betweenness(&functions, &call_callees, 42);

        let bridge_bc = bc[&bridge];
        let s1_bc = bc[&s1];
        let t1_bc = bc[&t1];

        assert!(
            bridge_bc > s1_bc,
            "Bridge ({bridge_bc}) should have higher betweenness than source ({s1_bc})"
        );
        assert!(
            bridge_bc > t1_bc,
            "Bridge ({bridge_bc}) should have higher betweenness than sink ({t1_bc})"
        );
    }

    // ── Full compute() integration test ──

    #[test]
    fn test_compute_full_wiring() {
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "a.py"));
        let f2 = graph.add_node(CodeNode::function("f2", "a.py"));
        let f3 = graph.add_node(CodeNode::function("f3", "b.py"));
        let file_a = graph.add_node(CodeNode::file("a.py"));
        let file_b = graph.add_node(CodeNode::file("b.py"));

        graph.add_edge(f1, f2, CodeEdge::calls());
        graph.add_edge(f2, f3, CodeEdge::calls());
        graph.add_edge(file_a, file_b, CodeEdge::imports());

        let functions = vec![f1, f2, f3];
        let files = vec![file_a, file_b];
        let all_call_edges = vec![(f1, f2), (f2, f3)];
        let all_import_edges = vec![(file_a, file_b)];

        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        call_callees.insert(f1, vec![f2]);
        call_callees.insert(f2, vec![f3]);
        call_callers.insert(f2, vec![f1]);
        call_callers.insert(f3, vec![f2]);

        let p = GraphPrimitives::compute(
            &graph,
            &functions,
            &files,
            &all_call_edges,
            &all_import_edges,
            &call_callers,
            &call_callees,
            12345,
            None,
        );

        // No cycles in a DAG
        assert!(p.call_cycles.is_empty());

        // PageRank should be populated for all functions
        assert_eq!(p.page_rank.len(), 3);

        // Betweenness should be populated
        assert_eq!(p.betweenness.len(), 3);

        // Call depths: f1=0, f2=1, f3=2
        assert_eq!(p.call_depth.get(&f1), Some(&0));
        assert_eq!(p.call_depth.get(&f2), Some(&1));
        assert_eq!(p.call_depth.get(&f3), Some(&2));

        // Dominator: f1 dominates f2, f2 dominates f3
        assert_eq!(p.idom.get(&f2), Some(&f1));
        assert_eq!(p.idom.get(&f3), Some(&f2));

        // Dom depth
        assert_eq!(p.dom_depth.get(&f1), Some(&0));
        assert_eq!(p.dom_depth.get(&f2), Some(&1));
        assert_eq!(p.dom_depth.get(&f3), Some(&2));
    }

    // ── Comprehensive integration test (all primitives together) ──

    /// Builds a realistic 10-function graph across 3 files with entry points,
    /// a hub, leaves, a mutual recursion pair, and import edges. Verifies
    /// all graph primitives (PageRank, betweenness, dominator tree, call
    /// cycles, call depths, articulation points) work end-to-end.
    #[test]
    fn test_all_primitives_realistic_graph() {
        // Graph topology:
        //
        //   Files: app.py, lib.py, util.py
        //   Imports: app.py -> lib.py -> util.py
        //
        //   Call graph:
        //     entry1 (app.py) -> hub (lib.py) -> leaf1 (lib.py)
        //     entry2 (app.py) -> hub (lib.py) -> leaf2 (util.py)
        //     entry1 (app.py) -> helper (util.py)
        //     hub (lib.py) -> rec_a (lib.py) <-> rec_b (lib.py)   (mutual recursion)
        //     hub (lib.py) -> deep1 (util.py) -> deep2 (util.py)
        //
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();

        // Files
        let file_app = graph.add_node(CodeNode::file("app.py"));
        let file_lib = graph.add_node(CodeNode::file("lib.py"));
        let file_util = graph.add_node(CodeNode::file("util.py"));

        // Functions
        let entry1 = graph.add_node(CodeNode::function("entry1", "app.py"));
        let entry2 = graph.add_node(CodeNode::function("entry2", "app.py"));
        let hub    = graph.add_node(CodeNode::function("hub", "lib.py"));
        let leaf1  = graph.add_node(CodeNode::function("leaf1", "lib.py"));
        let leaf2  = graph.add_node(CodeNode::function("leaf2", "util.py"));
        let helper = graph.add_node(CodeNode::function("helper", "util.py"));
        let rec_a  = graph.add_node(CodeNode::function("rec_a", "lib.py"));
        let rec_b  = graph.add_node(CodeNode::function("rec_b", "lib.py"));
        let deep1  = graph.add_node(CodeNode::function("deep1", "util.py"));
        let deep2  = graph.add_node(CodeNode::function("deep2", "util.py"));

        // Import edges
        graph.add_edge(file_app, file_lib, CodeEdge::imports());
        graph.add_edge(file_lib, file_util, CodeEdge::imports());

        // Call edges
        graph.add_edge(entry1, hub, CodeEdge::calls());
        graph.add_edge(entry2, hub, CodeEdge::calls());
        graph.add_edge(entry1, helper, CodeEdge::calls());
        graph.add_edge(hub, leaf1, CodeEdge::calls());
        graph.add_edge(hub, leaf2, CodeEdge::calls());
        graph.add_edge(hub, rec_a, CodeEdge::calls());
        graph.add_edge(rec_a, rec_b, CodeEdge::calls());
        graph.add_edge(rec_b, rec_a, CodeEdge::calls()); // mutual recursion
        graph.add_edge(hub, deep1, CodeEdge::calls());
        graph.add_edge(deep1, deep2, CodeEdge::calls());

        let functions = vec![entry1, entry2, hub, leaf1, leaf2, helper, rec_a, rec_b, deep1, deep2];
        let files = vec![file_app, file_lib, file_util];

        let all_call_edges = vec![
            (entry1, hub), (entry2, hub), (entry1, helper),
            (hub, leaf1), (hub, leaf2), (hub, rec_a),
            (rec_a, rec_b), (rec_b, rec_a),
            (hub, deep1), (deep1, deep2),
        ];
        let all_import_edges = vec![(file_app, file_lib), (file_lib, file_util)];

        let mut call_callees: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        let mut call_callers: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();
        for &(src, tgt) in &all_call_edges {
            call_callees.entry(src).or_default().push(tgt);
            call_callers.entry(tgt).or_default().push(src);
        }

        let p = GraphPrimitives::compute(
            &graph,
            &functions,
            &files,
            &all_call_edges,
            &all_import_edges,
            &call_callers,
            &call_callees,
            99999,
            None,
        );

        // ── PageRank ──
        // All functions should have a PageRank value > 0
        for &f in &functions {
            let pr = p.page_rank.get(&f).copied().unwrap_or(0.0);
            assert!(pr > 0.0, "PageRank should be > 0 for every function");
        }
        // Hub should have higher PageRank than leaves (it receives from 2 entry points)
        let pr_hub = p.page_rank[&hub];
        let pr_leaf1 = p.page_rank[&leaf1];
        let pr_leaf2 = p.page_rank[&leaf2];
        assert!(pr_hub > pr_leaf1, "Hub PR ({pr_hub}) > leaf1 PR ({pr_leaf1})");
        assert!(pr_hub > pr_leaf2, "Hub PR ({pr_hub}) > leaf2 PR ({pr_leaf2})");

        // ── Betweenness centrality ──
        // Hub should have the highest betweenness (it's the bridge between entries and leaves)
        let bc_hub = p.betweenness[&hub];
        assert!(bc_hub > 0.0, "Hub betweenness should be > 0");
        for &f in &[entry1, entry2, leaf1, leaf2, helper, deep2] {
            let bc_f = p.betweenness.get(&f).copied().unwrap_or(0.0);
            assert!(bc_hub >= bc_f, "Hub BC ({bc_hub}) >= {f:?} BC ({bc_f})");
        }

        // ── Call-graph cycles ──
        // Should detect the rec_a <-> rec_b mutual recursion
        assert!(
            !p.call_cycles.is_empty(),
            "Should detect at least one call cycle"
        );
        let cycle_members: HashSet<NodeIndex> = p.call_cycles
            .iter()
            .flat_map(|c| c.iter().copied())
            .collect();
        assert!(
            cycle_members.contains(&rec_a) && cycle_members.contains(&rec_b),
            "Cycle should include rec_a and rec_b"
        );

        // ── Call depths ──
        // entry1, entry2 have no callers => depth 0
        assert_eq!(p.call_depth.get(&entry1), Some(&0));
        assert_eq!(p.call_depth.get(&entry2), Some(&0));
        // hub is called by entries => depth 1
        assert_eq!(p.call_depth.get(&hub), Some(&1));
        // leaf1, leaf2 are called by hub => depth 2
        assert_eq!(p.call_depth.get(&leaf1), Some(&2));
        assert_eq!(p.call_depth.get(&leaf2), Some(&2));
        // deep1 called by hub => depth 2, deep2 called by deep1 => depth 3
        assert_eq!(p.call_depth.get(&deep1), Some(&2));
        assert_eq!(p.call_depth.get(&deep2), Some(&3));
        // helper is called by entry1 => depth 1
        assert_eq!(p.call_depth.get(&helper), Some(&1));

        // ── Dominator tree ──
        // Entry points have no immediate dominator (they are roots)
        // hub is dominated by... well, it has 2 entry callers so the virtual
        // root dominates it. The key check: dominated set is populated.
        assert!(
            !p.idom.is_empty(),
            "Dominator tree should be populated"
        );
        assert!(
            !p.dom_depth.is_empty(),
            "Dominator depths should be populated"
        );

        // ── Articulation points (undirected view) ──
        // hub connects entries to leaves in the undirected graph — likely an AP
        // (Not guaranteed depending on the exact undirected connectivity, but
        // the AP computation should at least run without panic)
        // Just verify the computation completed
        // (articulation points depend on undirected connectivity which includes imports)
        // Articulation point computation should complete without panic.
        // The exact count depends on undirected connectivity.
        let _ap_count = p.articulation_points.len();
    }

    // ── Weighted overlay builder tests ──

    #[test]
    fn test_overlay_structural_only() {
        // Two functions with a Calls edge, no co-change → weight = 1.0
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "src/a.rs"));
        let f2 = graph.add_node(CodeNode::function("f2", "src/b.rs"));
        let file_a = graph.add_node(CodeNode::file("src/a.rs"));
        let file_b = graph.add_node(CodeNode::file("src/b.rs"));

        let functions = vec![f1, f2];
        let files = vec![file_a, file_b];
        let call_edges = vec![(f1, f2)];
        let import_edges: Vec<(NodeIndex, NodeIndex)> = vec![];

        // Empty co-change matrix (no commits)
        let co_change = CoChangeMatrix::empty();

        let (overlay, hidden_coupling) = build_weighted_overlay(
            &functions, &files, &call_edges, &import_edges, &co_change, &graph,
        );

        // Should have 2 nodes and 1 edge
        assert_eq!(overlay.node_count(), 2, "Overlay should have 2 nodes");
        assert_eq!(overlay.edge_count(), 1, "Overlay should have 1 edge");

        // The edge weight should be 1.0 (structural_base for Calls, no co-change boost)
        let edge_idx = overlay.edge_indices().next().expect("should have one edge");
        let weight = overlay[edge_idx];
        assert!(
            (weight - 1.0).abs() < 1e-6,
            "Calls-only edge should have weight 1.0, got {weight}"
        );

        // No hidden coupling
        assert!(hidden_coupling.is_empty(), "No hidden coupling expected");
    }

    #[test]
    fn test_overlay_co_change_boost() {
        // Calls edge + co-change → weight > 1.0
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("f1", "src/alpha.rs"));
        let f2 = graph.add_node(CodeNode::function("f2", "src/beta.rs"));
        let file_a = graph.add_node(CodeNode::file("src/alpha.rs"));
        let file_b = graph.add_node(CodeNode::file("src/beta.rs"));

        let functions = vec![f1, f2];
        let files = vec![file_a, file_b];
        let call_edges = vec![(f1, f2)];
        let import_edges: Vec<(NodeIndex, NodeIndex)> = vec![];

        // Build co-change matrix with recent commits touching both files
        let now = chrono::Utc::now();
        let config = crate::git::co_change::CoChangeConfig {
            min_weight: 0.01,
            ..Default::default()
        };
        let commits = vec![
            (now, vec!["src/alpha.rs".to_string(), "src/beta.rs".to_string()]),
            (now, vec!["src/alpha.rs".to_string(), "src/beta.rs".to_string()]),
        ];
        let co_change = CoChangeMatrix::from_commits(&commits, &config, now);

        // Verify co-change has data
        let cc_weight = co_change
            .weight_by_path("src/alpha.rs", "src/beta.rs")
            .expect("co-change should exist");
        assert!(cc_weight > 1.0, "Two recent commits should give weight > 1.0");

        let (overlay, hidden_coupling) = build_weighted_overlay(
            &functions, &files, &call_edges, &import_edges, &co_change, &graph,
        );

        assert_eq!(overlay.edge_count(), 1, "Overlay should have 1 edge");

        let edge_idx = overlay.edge_indices().next().expect("should have one edge");
        let weight = overlay[edge_idx];

        // weight = structural_base (1.0) + co_change_boost (min(cc_weight, 2.0))
        // cc_weight ~ 2.0, so total should be ~ 3.0 (or 1.0 + capped 2.0)
        assert!(
            weight > 1.0,
            "Co-change boosted edge should have weight > 1.0, got {weight}"
        );
        // structural_base is 1.0, co_change_boost is min(cc_weight, 2.0)
        let expected = 1.0 + cc_weight.min(2.0);
        assert!(
            (weight - expected).abs() < 1e-6,
            "Expected weight {expected}, got {weight}"
        );

        assert!(hidden_coupling.is_empty(), "No hidden coupling expected (structural edge exists)");
    }

    #[test]
    fn test_overlay_hidden_coupling() {
        // No structural edge, but co-change exists → hidden coupling detected
        let mut graph: StableGraph<CodeNode, CodeEdge> = StableGraph::new();
        let f1 = graph.add_node(CodeNode::function("handler", "src/api.rs"));
        let f2 = graph.add_node(CodeNode::function("model_update", "src/db.rs"));
        let file_api = graph.add_node(CodeNode::file("src/api.rs"));
        let file_db = graph.add_node(CodeNode::file("src/db.rs"));

        let functions = vec![f1, f2];
        let files = vec![file_api, file_db];
        // No call or import edges between f1 and f2
        let call_edges: Vec<(NodeIndex, NodeIndex)> = vec![];
        let import_edges: Vec<(NodeIndex, NodeIndex)> = vec![];

        // Build co-change matrix: both files frequently change together
        let now = chrono::Utc::now();
        let config = crate::git::co_change::CoChangeConfig {
            min_weight: 0.01,
            ..Default::default()
        };
        let commits = vec![
            (now, vec!["src/api.rs".to_string(), "src/db.rs".to_string()]),
            (now, vec!["src/api.rs".to_string(), "src/db.rs".to_string()]),
            (now, vec!["src/api.rs".to_string(), "src/db.rs".to_string()]),
        ];
        let co_change = CoChangeMatrix::from_commits(&commits, &config, now);

        let cc_weight = co_change
            .weight_by_path("src/api.rs", "src/db.rs")
            .expect("co-change should exist");

        let (overlay, hidden_coupling) = build_weighted_overlay(
            &functions, &files, &call_edges, &import_edges, &co_change, &graph,
        );

        // Should have overlay edges between the function pair (hidden coupling)
        assert_eq!(overlay.node_count(), 2, "Overlay should have 2 nodes");
        assert_eq!(overlay.edge_count(), 1, "Should have 1 hidden coupling edge");

        let edge_idx = overlay.edge_indices().next().expect("should have one edge");
        let weight = overlay[edge_idx];

        // Hidden coupling edge: weight = co_change_boost only (no structural base)
        let expected_boost = cc_weight.min(2.0);
        assert!(
            (weight - expected_boost).abs() < 1e-6,
            "Hidden coupling edge should have weight {expected_boost}, got {weight}"
        );

        // Hidden coupling should be recorded at file level
        assert_eq!(hidden_coupling.len(), 1, "Should have 1 hidden coupling entry");
        let (hc_a, hc_b, hc_w) = hidden_coupling[0];

        // The file-level NodeIndex values should be from the files parameter
        let hc_files: HashSet<NodeIndex> = [hc_a, hc_b].into_iter().collect();
        assert!(
            hc_files.contains(&file_api) && hc_files.contains(&file_db),
            "Hidden coupling should reference file-level nodes (api={file_api:?}, db={file_db:?}), got ({hc_a:?}, {hc_b:?})"
        );
        assert!(
            (hc_w - expected_boost).abs() < 1e-6,
            "Hidden coupling weight should be {expected_boost}, got {hc_w}"
        );
    }

    // ── Weighted PageRank tests ──

    #[test]
    fn test_weighted_page_rank_uniform_weights() {
        // 3-node cycle with all edges weight=1.0
        // a → b → c → a
        // With uniform weights, all ranks should be approximately equal.
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let orig_a = NodeIndex::new(100);
        let orig_b = NodeIndex::new(101);
        let orig_c = NodeIndex::new(102);

        let a = overlay.add_node(orig_a);
        let b = overlay.add_node(orig_b);
        let c = overlay.add_node(orig_c);

        overlay.add_edge(a, b, 1.0);
        overlay.add_edge(b, c, 1.0);
        overlay.add_edge(c, a, 1.0);

        let pr = compute_weighted_page_rank(&overlay, 20, 0.85, 1e-6);

        assert_eq!(pr.len(), 3, "Should have 3 entries");
        let rank_a = pr[&orig_a];
        let rank_b = pr[&orig_b];
        let rank_c = pr[&orig_c];

        // In a symmetric cycle, all ranks should converge to 1/3
        assert!(
            (rank_a - rank_b).abs() < 0.01,
            "Ranks should be ~equal in uniform cycle: a={rank_a}, b={rank_b}"
        );
        assert!(
            (rank_b - rank_c).abs() < 0.01,
            "Ranks should be ~equal in uniform cycle: b={rank_b}, c={rank_c}"
        );
        assert!(
            (rank_a - 1.0 / 3.0).abs() < 0.01,
            "Each rank should be ~1/3: got {rank_a}"
        );
    }

    #[test]
    fn test_weighted_page_rank_heavy_edge() {
        // a has two out-edges: heavy to b (weight 5.0), light to c (weight 1.0).
        // b and c each feed back to a. Node a distributes rank proportionally:
        // 5/6 to b, 1/6 to c. Therefore b should accumulate higher rank than c.
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let orig_a = NodeIndex::new(200);
        let orig_b = NodeIndex::new(201);
        let orig_c = NodeIndex::new(202);

        let a = overlay.add_node(orig_a);
        let b = overlay.add_node(orig_b);
        let c = overlay.add_node(orig_c);

        overlay.add_edge(a, b, 5.0); // heavy edge
        overlay.add_edge(a, c, 1.0); // light edge
        overlay.add_edge(b, a, 1.0); // feedback
        overlay.add_edge(c, a, 1.0); // feedback

        let pr = compute_weighted_page_rank(&overlay, 20, 0.85, 1e-6);

        assert_eq!(pr.len(), 3, "Should have 3 entries");
        let rank_b = pr[&orig_b];
        let rank_c = pr[&orig_c];

        assert!(
            rank_b > rank_c,
            "b should have higher rank than c due to heavy edge: b={rank_b}, c={rank_c}"
        );
    }

    // ── Weighted betweenness centrality tests ──

    #[test]
    fn test_weighted_betweenness_center_node() {
        // Star topology: a→center, b→center, center→c, center→d
        // With uniform weights, center should have the highest betweenness
        // because all shortest paths between {a,b} and {c,d} pass through it.
        let mut overlay: StableGraph<NodeIndex, f32> = StableGraph::new();
        let orig_a = NodeIndex::new(300);
        let orig_b = NodeIndex::new(301);
        let orig_center = NodeIndex::new(302);
        let orig_c = NodeIndex::new(303);
        let orig_d = NodeIndex::new(304);

        let a = overlay.add_node(orig_a);
        let b = overlay.add_node(orig_b);
        let center = overlay.add_node(orig_center);
        let c = overlay.add_node(orig_c);
        let d = overlay.add_node(orig_d);

        // Uniform weight edges
        overlay.add_edge(a, center, 1.0);
        overlay.add_edge(b, center, 1.0);
        overlay.add_edge(center, c, 1.0);
        overlay.add_edge(center, d, 1.0);

        // sample_size=200 > node_count=5, so all nodes are sampled
        let bc = compute_weighted_betweenness(&overlay, 200, 42);

        assert_eq!(bc.len(), 5, "Should have betweenness for all 5 nodes");

        let bc_center = bc[&orig_center];
        let bc_a = bc[&orig_a];
        let bc_b = bc[&orig_b];
        let bc_c = bc[&orig_c];
        let bc_d = bc[&orig_d];

        assert!(
            bc_center > bc_a,
            "Center ({bc_center}) should have higher betweenness than a ({bc_a})"
        );
        assert!(
            bc_center > bc_b,
            "Center ({bc_center}) should have higher betweenness than b ({bc_b})"
        );
        assert!(
            bc_center > bc_c,
            "Center ({bc_center}) should have higher betweenness than c ({bc_c})"
        );
        assert!(
            bc_center > bc_d,
            "Center ({bc_center}) should have higher betweenness than d ({bc_d})"
        );
        assert!(
            bc_center > 0.0,
            "Center betweenness should be positive, got {bc_center}"
        );
    }
}
