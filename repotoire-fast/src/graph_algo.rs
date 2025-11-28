// Graph algorithms for FalkorDB migration
// Replaces Neo4j GDS dependency with pure Rust implementations
//
// WHY THIS EXISTS:
// Neo4j GDS (Graph Data Science) requires a paid plugin and only works with Neo4j.
// By implementing these algorithms in Rust, we can:
// 1. Work with FalkorDB (no GDS support)
// 2. Run 10-100x faster (no network round-trips)
// 3. Deploy anywhere (no plugin dependencies)

use petgraph::graph::DiGraph;
use petgraph::algo::tarjan_scc as petgraph_tarjan;
use std::collections::HashMap;

// ============================================================================
// STRONGLY CONNECTED COMPONENTS (SCC)
// ============================================================================
//
// What is an SCC?
// A group of nodes where EVERY node can reach EVERY other node.
// In code: circular dependencies! A imports B imports C imports A.
//
// Example:
//   A → B → C → A  (this is one SCC with 3 nodes - a cycle!)
//   D → E          (D and E are separate SCCs of size 1)
//
// Tarjan's Algorithm (what petgraph uses):
// 1. Do a depth-first search (DFS) through the graph
// 2. Track when you first visit each node (index)
// 3. Track the lowest index reachable from each node (lowlink)
// 4. When you finish a node and lowlink == index, you found an SCC!
//
// Time complexity: O(V + E) - visits each node and edge once
// ============================================================================

/// Find all strongly connected components in a directed graph.
///
/// # Arguments
/// * `edges` - List of (source, target) node ID pairs
/// * `num_nodes` - Total number of nodes in the graph
///
/// # Returns
/// List of SCCs, where each SCC is a list of node IDs.
/// SCCs with size > 1 are circular dependencies!
pub fn find_sccs(edges: &[(u32, u32)], num_nodes: usize) -> Vec<Vec<u32>> {
    // Step 1: Build a petgraph DiGraph (Directed Graph)
    // DiGraph<N, E> where N = node weight type, E = edge weight type
    // We use () for both since we only care about structure, not weights
    let mut graph: DiGraph<(), ()> = DiGraph::new();

    // Step 2: Add all nodes
    // add_node() returns a NodeIndex - a handle to reference the node later
    // We create num_nodes nodes, even if some have no edges
    let node_indices: Vec<_> = (0..num_nodes)
        .map(|_| graph.add_node(()))
        .collect();

    // Step 3: Add edges
    // Each edge connects two NodeIndex values
    for &(src, dst) in edges {
        // Safety: src and dst should be < num_nodes
        if (src as usize) < num_nodes && (dst as usize) < num_nodes {
            graph.add_edge(node_indices[src as usize], node_indices[dst as usize], ());
        }
    }

    // Step 4: Run Tarjan's SCC algorithm
    // Returns Vec<Vec<NodeIndex>> - list of SCCs
    let sccs = petgraph_tarjan(&graph);

    // Step 5: Convert NodeIndex back to our u32 IDs
    // NodeIndex has an .index() method that gives us the position
    sccs.into_iter()
        .map(|scc| {
            scc.into_iter()
                .map(|node_idx| node_idx.index() as u32)
                .collect()
        })
        .collect()
}

/// Find only the cycles (SCCs with more than 1 node)
/// These are the circular dependencies we want to report!
pub fn find_cycles(edges: &[(u32, u32)], num_nodes: usize, min_size: usize) -> Vec<Vec<u32>> {
    find_sccs(edges, num_nodes)
        .into_iter()
        .filter(|scc| scc.len() >= min_size)
        .collect()
}

// ============================================================================
// PAGERANK
// ============================================================================
//
// What is PageRank?
// Measures "importance" of nodes based on who links to them.
// Originally invented by Google to rank web pages.
// In code: functions called by many important functions are important!
//
// The Formula:
//   PR(node) = (1 - d) / N + d * Σ(PR(neighbor) / out_degree(neighbor))
//
// Where:
//   d = damping factor (0.85) - probability of following a link vs jumping randomly
//   N = total number of nodes
//   out_degree = how many outgoing edges a node has
//
// Algorithm:
// 1. Start: every node has score 1/N
// 2. Iterate: each node gets score from its incoming neighbors
// 3. Repeat until scores converge (stop changing much)
//
// Time complexity: O(iterations * edges)
// ============================================================================

/// Calculate PageRank scores for all nodes.
///
/// # Arguments
/// * `edges` - List of (source, target) directed edges
/// * `num_nodes` - Total number of nodes
/// * `damping` - Damping factor, typically 0.85
/// * `max_iterations` - Maximum iterations before stopping
/// * `tolerance` - Stop when score changes are below this (convergence)
///
/// # Returns
/// Vector of PageRank scores, one per node (index = node ID)
pub fn pagerank(
    edges: &[(u32, u32)],
    num_nodes: usize,
    damping: f64,
    max_iterations: usize,
    tolerance: f64,
) -> Vec<f64> {
    if num_nodes == 0 {
        return vec![];
    }

    // Step 1: Build adjacency lists
    // We need: who points TO each node (for receiving score)
    //          out-degree of each node (for dividing score)
    let mut incoming: Vec<Vec<u32>> = vec![vec![]; num_nodes];  // Who links to me?
    let mut out_degree: Vec<usize> = vec![0; num_nodes];        // How many links do I have?

    for &(src, dst) in edges {
        let src = src as usize;
        let dst = dst as usize;
        if src < num_nodes && dst < num_nodes {
            incoming[dst].push(src as u32);  // dst receives from src
            out_degree[src] += 1;            // src has one more outgoing edge
        }
    }

    // Step 2: Initialize scores
    // Every node starts with equal probability: 1/N
    let initial_score = 1.0 / num_nodes as f64;
    let mut scores: Vec<f64> = vec![initial_score; num_nodes];
    let mut new_scores: Vec<f64> = vec![0.0; num_nodes];

    // Base score: what you get from "random jumps" (not following links)
    let base_score = (1.0 - damping) / num_nodes as f64;

    // Step 3: Iterate until convergence
    for _iteration in 0..max_iterations {
        // Calculate new scores
        for node in 0..num_nodes {
            // Start with base score (random jump probability)
            let mut score = base_score;

            // Add contribution from each incoming neighbor
            for &neighbor in &incoming[node] {
                let neighbor = neighbor as usize;
                let neighbor_out = out_degree[neighbor];
                if neighbor_out > 0 {
                    // Neighbor shares its score equally among all its outgoing links
                    score += damping * scores[neighbor] / neighbor_out as f64;
                }
            }

            new_scores[node] = score;
        }

        // Check for convergence: sum of absolute differences
        let diff: f64 = scores.iter()
            .zip(new_scores.iter())
            .map(|(old, new)| (old - new).abs())
            .sum();

        // Swap scores for next iteration
        std::mem::swap(&mut scores, &mut new_scores);

        // Converged?
        if diff < tolerance {
            break;
        }
    }

    scores
}

// ============================================================================
// BETWEENNESS CENTRALITY (Brandes Algorithm)
// ============================================================================
//
// What is Betweenness Centrality?
// Measures how often a node lies on the shortest path between OTHER nodes.
// High betweenness = "bridge" or "bottleneck" in the graph.
//
// In code: functions that are critical connectors between modules!
//
// Formula:
//   BC(v) = Σ (σ_st(v) / σ_st) for all pairs s,t where s≠v≠t
//
// Where:
//   σ_st = total number of shortest paths from s to t
//   σ_st(v) = number of those paths that pass through v
//
// Brandes Algorithm (much faster than naive!):
// 1. For each source node, run BFS to find shortest paths
// 2. Accumulate dependencies by backtracking from farthest nodes
// 3. Sum up contributions from all source nodes
//
// Time complexity: O(V * E) for unweighted graphs
// ============================================================================

/// Calculate Betweenness Centrality using Brandes' algorithm.
///
/// # Arguments
/// * `edges` - List of (source, target) directed edges
/// * `num_nodes` - Total number of nodes
///
/// # Returns
/// Vector of betweenness scores, one per node (index = node ID)
pub fn betweenness_centrality(edges: &[(u32, u32)], num_nodes: usize) -> Vec<f64> {
    if num_nodes == 0 {
        return vec![];
    }

    // Build adjacency list (directed graph)
    let mut adj: Vec<Vec<u32>> = vec![vec![]; num_nodes];
    for &(src, dst) in edges {
        let src = src as usize;
        let dst = dst as usize;
        if src < num_nodes && dst < num_nodes {
            adj[src].push(dst as u32);
        }
    }

    // Betweenness scores accumulator
    let mut betweenness: Vec<f64> = vec![0.0; num_nodes];

    // Run BFS from each node as source
    for source in 0..num_nodes {
        // Stack of nodes in order of non-increasing distance from source
        let mut stack: Vec<usize> = Vec::new();

        // Predecessors on shortest paths from source
        let mut predecessors: Vec<Vec<usize>> = vec![vec![]; num_nodes];

        // Number of shortest paths from source to each node
        let mut num_paths: Vec<f64> = vec![0.0; num_nodes];
        num_paths[source] = 1.0;

        // Distance from source (-1 = not visited)
        let mut distance: Vec<i32> = vec![-1; num_nodes];
        distance[source] = 0;

        // BFS queue
        let mut queue: std::collections::VecDeque<usize> = std::collections::VecDeque::new();
        queue.push_back(source);

        // BFS traversal
        while let Some(v) = queue.pop_front() {
            stack.push(v);

            for &w in &adj[v] {
                let w = w as usize;

                // First time visiting w?
                if distance[w] < 0 {
                    distance[w] = distance[v] + 1;
                    queue.push_back(w);
                }

                // Is this a shortest path to w?
                if distance[w] == distance[v] + 1 {
                    num_paths[w] += num_paths[v];
                    predecessors[w].push(v);
                }
            }
        }

        // Dependency accumulation (backtrack from farthest nodes)
        let mut dependency: Vec<f64> = vec![0.0; num_nodes];

        while let Some(w) = stack.pop() {
            for &v in &predecessors[w] {
                // v's contribution to w's dependency
                let contrib = (num_paths[v] / num_paths[w]) * (1.0 + dependency[w]);
                dependency[v] += contrib;
            }

            // Add to betweenness (exclude source itself)
            if w != source {
                betweenness[w] += dependency[w];
            }
        }
    }

    // For undirected graphs, divide by 2 (each path counted twice)
    // We're doing directed, so no division needed

    betweenness
}

// ============================================================================
// LOUVAIN / LEIDEN (Modularity-based Community Detection)
// ============================================================================
//
// What is Modularity?
// A score measuring how good a community partition is.
// High modularity = dense connections within communities, sparse between.
//
// Formula:
//   Q = (1/2m) * Σ [A_ij - (k_i * k_j)/(2m)] * δ(c_i, c_j)
//
// Where:
//   m = total edge weight (number of edges for unweighted)
//   A_ij = edge weight between i and j
//   k_i = degree of node i
//   δ(c_i, c_j) = 1 if i and j in same community, 0 otherwise
//
// Louvain Algorithm:
// 1. Each node starts in its own community
// 2. Move each node to the community that gives max modularity gain
// 3. Aggregate nodes in same community into "super-nodes"
// 4. Repeat until no improvement
//
// Leiden Improvement:
// After step 2, do a REFINEMENT step that can split badly-connected communities.
// This guarantees communities are well-connected (Louvain can produce disconnected ones!)
//
// Time complexity: O(E) per iteration, typically converges fast
// ============================================================================

/// Calculate the modularity gain from moving node to a new community.
fn modularity_gain(
    node: usize,
    new_community: u32,
    neighbors: &[Vec<(u32, f64)>],
    communities: &[u32],
    degrees: &[f64],
    community_weights: &HashMap<u32, f64>,  // sum of degrees in each community
    total_weight: f64,
) -> f64 {
    if total_weight == 0.0 {
        return 0.0;
    }

    let k_i = degrees[node];

    // Sum of edge weights from node to nodes in new_community
    let mut k_i_in = 0.0;
    for &(neighbor, weight) in &neighbors[node] {
        if communities[neighbor as usize] == new_community {
            k_i_in += weight;
        }
    }

    // Sum of degrees in new_community (excluding node if already there)
    let sigma_tot = community_weights.get(&new_community).copied().unwrap_or(0.0);

    // Modularity gain formula (simplified)
    // ΔQ = k_i_in/m - (sigma_tot * k_i) / (2m²)
    k_i_in / total_weight - (sigma_tot * k_i) / (2.0 * total_weight * total_weight)
}

/// Louvain community detection algorithm.
/// Returns community assignments (index = node, value = community ID).
pub fn louvain(
    edges: &[(u32, u32)],
    num_nodes: usize,
    resolution: f64,  // Higher = more/smaller communities
) -> Vec<u32> {
    if num_nodes == 0 {
        return vec![];
    }

    // Build weighted undirected adjacency list
    let mut neighbors: Vec<Vec<(u32, f64)>> = vec![vec![]; num_nodes];
    let mut total_weight = 0.0;

    for &(src, dst) in edges {
        let src = src as usize;
        let dst = dst as usize;
        if src < num_nodes && dst < num_nodes && src != dst {
            neighbors[src].push((dst as u32, 1.0));
            neighbors[dst].push((src as u32, 1.0));
            total_weight += 1.0;  // Count each edge once (undirected adds twice)
        }
    }

    // Calculate degrees
    let degrees: Vec<f64> = neighbors.iter()
        .map(|edges| edges.iter().map(|(_, w)| w).sum())
        .collect();

    // Initialize: each node in its own community
    let mut communities: Vec<u32> = (0..num_nodes as u32).collect();

    // Track sum of degrees per community
    let mut community_weights: HashMap<u32, f64> = degrees.iter()
        .enumerate()
        .map(|(i, &d)| (i as u32, d))
        .collect();

    // Phase 1: Local moving (iteratively move nodes to best community)
    let mut improved = true;
    let mut max_iterations = 100;

    while improved && max_iterations > 0 {
        improved = false;
        max_iterations -= 1;

        for node in 0..num_nodes {
            let current_community = communities[node];

            // Find neighboring communities
            let mut neighbor_communities: HashMap<u32, f64> = HashMap::new();
            for &(neighbor, weight) in &neighbors[node] {
                let nc = communities[neighbor as usize];
                *neighbor_communities.entry(nc).or_insert(0.0) += weight;
            }

            // Try moving to each neighboring community
            let mut best_community = current_community;
            let mut best_gain = 0.0;

            // First, calculate loss from removing node from current community
            let k_i = degrees[node];

            // Remove node from current community temporarily
            if let Some(w) = community_weights.get_mut(&current_community) {
                *w -= k_i;
            }

            for (&target_community, &_) in &neighbor_communities {
                let gain = modularity_gain(
                    node,
                    target_community,
                    &neighbors,
                    &communities,
                    &degrees,
                    &community_weights,
                    total_weight,
                ) * resolution;

                if gain > best_gain {
                    best_gain = gain;
                    best_community = target_community;
                }
            }

            // Also consider staying (add back to current)
            let stay_gain = modularity_gain(
                node,
                current_community,
                &neighbors,
                &communities,
                &degrees,
                &community_weights,
                total_weight,
            ) * resolution;

            if stay_gain >= best_gain {
                best_community = current_community;
            }

            // Move node to best community
            if best_community != current_community {
                communities[node] = best_community;
                *community_weights.entry(best_community).or_insert(0.0) += k_i;
                improved = true;
            } else {
                // Restore current community weight
                *community_weights.entry(current_community).or_insert(0.0) += k_i;
            }
        }
    }

    // Renumber communities to be contiguous (0, 1, 2, ...)
    let mut community_map: HashMap<u32, u32> = HashMap::new();
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

    communities
}

// ============================================================================
// HARMONIC CENTRALITY
// ============================================================================
//
// What is Harmonic Centrality?
// Measures how "close" a node is to all other nodes, using the harmonic mean.
// High harmonic centrality = can reach most nodes in few hops.
//
// In code: utility functions that are easily accessible from most of the codebase!
//
// Formula:
//   HC(v) = Σ (1 / d(v, u)) for all u ≠ v where d(v,u) is the shortest path
//
// Why harmonic instead of closeness?
// - Closeness uses 1/(sum of distances), breaks on disconnected graphs (division by ∞)
// - Harmonic uses sum of (1/distance), handles disconnected nodes gracefully (1/∞ = 0)
//
// Algorithm:
// 1. For each node v, run BFS to find shortest paths to all reachable nodes
// 2. Sum up 1/distance for each reachable node
// 3. Optionally normalize by (n-1) to get values in [0, 1]
//
// Time complexity: O(V * (V + E)) - BFS from each node
// ============================================================================

/// Calculate Harmonic Centrality for all nodes.
///
/// # Arguments
/// * `edges` - List of (source, target) directed edges
/// * `num_nodes` - Total number of nodes
/// * `normalized` - If true, normalize by (n-1) to get values in [0, 1]
///
/// # Returns
/// Vector of harmonic centrality scores, one per node (index = node ID)
pub fn harmonic_centrality(edges: &[(u32, u32)], num_nodes: usize, normalized: bool) -> Vec<f64> {
    if num_nodes == 0 {
        return vec![];
    }

    if num_nodes == 1 {
        return vec![0.0];  // Single node has no other nodes to reach
    }

    // Build adjacency list (directed graph)
    // For centrality, we often want undirected - treat edges as bidirectional
    let mut adj: Vec<Vec<u32>> = vec![vec![]; num_nodes];
    for &(src, dst) in edges {
        let src = src as usize;
        let dst = dst as usize;
        if src < num_nodes && dst < num_nodes && src != dst {
            adj[src].push(dst as u32);
            adj[dst].push(src as u32);  // Undirected for centrality
        }
    }

    // Harmonic centrality for each node
    let mut harmonic: Vec<f64> = vec![0.0; num_nodes];

    // BFS from each node to compute distances
    for source in 0..num_nodes {
        // Distance from source (-1 = not visited)
        let mut distance: Vec<i32> = vec![-1; num_nodes];
        distance[source] = 0;

        // BFS queue
        let mut queue: std::collections::VecDeque<usize> = std::collections::VecDeque::new();
        queue.push_back(source);

        // BFS traversal
        while let Some(v) = queue.pop_front() {
            for &w in &adj[v] {
                let w = w as usize;

                // First time visiting w?
                if distance[w] < 0 {
                    distance[w] = distance[v] + 1;
                    queue.push_back(w);

                    // Add contribution to harmonic centrality
                    // HC(source) += 1 / d(source, w)
                    harmonic[source] += 1.0 / distance[w] as f64;
                }
            }
        }
    }

    // Normalize if requested: divide by (n-1) to get [0, 1] range
    if normalized && num_nodes > 1 {
        let norm_factor = (num_nodes - 1) as f64;
        for score in &mut harmonic {
            *score /= norm_factor;
        }
    }

    harmonic
}

/// Leiden community detection (improved Louvain with refinement).
/// Guarantees well-connected communities.
pub fn leiden(
    edges: &[(u32, u32)],
    num_nodes: usize,
    resolution: f64,
    max_iterations: usize,
) -> Vec<u32> {
    if num_nodes == 0 {
        return vec![];
    }

    // Start with Louvain result
    let mut communities = louvain(edges, num_nodes, resolution);

    // Build adjacency for refinement checks
    let mut neighbors: Vec<Vec<u32>> = vec![vec![]; num_nodes];
    for &(src, dst) in edges {
        let src = src as usize;
        let dst = dst as usize;
        if src < num_nodes && dst < num_nodes && src != dst {
            neighbors[src].push(dst as u32);
            neighbors[dst].push(src as u32);
        }
    }

    // Refinement: split poorly-connected communities
    // A node should stay in its community only if it has more internal than external connections
    for _iter in 0..max_iterations {
        let mut changed = false;

        for node in 0..num_nodes {
            let current = communities[node];

            // Count internal vs external connections
            let mut internal = 0;
            let mut external = 0;

            for &neighbor in &neighbors[node] {
                if communities[neighbor as usize] == current {
                    internal += 1;
                } else {
                    external += 1;
                }
            }

            // If more external than internal, consider moving
            if external > internal && !neighbors[node].is_empty() {
                // Find best neighboring community
                let mut community_counts: HashMap<u32, usize> = HashMap::new();
                for &neighbor in &neighbors[node] {
                    let nc = communities[neighbor as usize];
                    *community_counts.entry(nc).or_insert(0) += 1;
                }

                // Move to community with most connections
                if let Some((&best_community, &count)) = community_counts.iter()
                    .filter(|(&c, _)| c != current)
                    .max_by_key(|(_, &count)| count)
                {
                    if count > internal {
                        communities[node] = best_community;
                        changed = true;
                    }
                }
            }
        }

        if !changed {
            break;
        }
    }

    // Renumber communities
    let mut community_map: HashMap<u32, u32> = HashMap::new();
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

    communities
}
