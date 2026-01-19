//! Fast batch graph traversal (REPO-407)
//!
//! Replaces N+1 database queries with batch fetching and in-memory traversal.
//! Pre-fetches all reachable nodes in 1-2 queries, then traverses in memory.

use rayon::prelude::*;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::{HashMap, HashSet, VecDeque};

/// Direction for graph traversal
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    Outgoing,
    Incoming,
    Both,
}

impl Direction {
    pub fn from_str(s: &str) -> Self {
        match s.to_uppercase().as_str() {
            "INCOMING" => Direction::Incoming,
            "BOTH" => Direction::Both,
            _ => Direction::Outgoing,
        }
    }
}

/// Node properties from the graph
#[derive(Debug, Clone, Default)]
pub struct NodeProperties {
    pub id: String,
    pub labels: Vec<String>,
    pub properties: HashMap<String, String>,
}

/// Graph traverser with pre-fetched data for in-memory traversal
#[derive(Debug, Default)]
pub struct GraphTraverser {
    /// Node ID to properties
    nodes: FxHashMap<String, NodeProperties>,

    /// Outgoing adjacency: node_id -> [(neighbor_id, rel_type)]
    outgoing: FxHashMap<String, Vec<(String, String)>>,

    /// Incoming adjacency: node_id -> [(neighbor_id, rel_type)]
    incoming: FxHashMap<String, Vec<(String, String)>>,
}

/// Result of a traversal
#[derive(Debug, Clone, Default)]
pub struct TraversalResult {
    /// Nodes visited in order
    pub visited_nodes: Vec<String>,

    /// Nodes with their properties
    pub node_properties: HashMap<String, NodeProperties>,

    /// Edges traversed: (source, target, rel_type)
    pub edges: Vec<(String, String, String)>,

    /// Depth at which each node was found
    pub depths: HashMap<String, usize>,
}

impl GraphTraverser {
    /// Create a new traverser from pre-fetched graph data.
    ///
    /// # Arguments
    /// * `nodes` - Vec of (node_id, labels, properties)
    /// * `edges` - Vec of (source_id, target_id, rel_type)
    pub fn new(
        nodes: Vec<(String, Vec<String>, HashMap<String, String>)>,
        edges: Vec<(String, String, String)>,
    ) -> Self {
        let mut traverser = GraphTraverser::default();

        // Index nodes
        for (id, labels, properties) in nodes {
            traverser.nodes.insert(
                id.clone(),
                NodeProperties {
                    id: id.clone(),
                    labels,
                    properties,
                },
            );
        }

        // Index edges (both directions)
        for (source, target, rel_type) in edges {
            traverser
                .outgoing
                .entry(source.clone())
                .or_default()
                .push((target.clone(), rel_type.clone()));

            traverser
                .incoming
                .entry(target)
                .or_default()
                .push((source, rel_type));
        }

        traverser
    }

    /// Perform BFS traversal starting from given nodes.
    ///
    /// # Arguments
    /// * `starts` - Starting node IDs
    /// * `max_depth` - Maximum traversal depth (0 = unlimited)
    /// * `direction` - Traversal direction
    /// * `rel_type_filter` - Optional relationship type filter
    ///
    /// # Returns
    /// TraversalResult with visited nodes and edges
    pub fn bfs(
        &self,
        starts: &[String],
        max_depth: usize,
        direction: Direction,
        rel_type_filter: Option<&str>,
    ) -> TraversalResult {
        let mut result = TraversalResult::default();
        let mut visited: FxHashSet<String> = FxHashSet::default();
        let mut queue: VecDeque<(String, usize)> = VecDeque::new();

        // Initialize with start nodes
        for start in starts {
            if self.nodes.contains_key(start) && !visited.contains(start) {
                visited.insert(start.clone());
                queue.push_back((start.clone(), 0));
                result.depths.insert(start.clone(), 0);
            }
        }

        // BFS loop
        while let Some((node_id, depth)) = queue.pop_front() {
            result.visited_nodes.push(node_id.clone());

            // Get node properties
            if let Some(props) = self.nodes.get(&node_id) {
                result.node_properties.insert(node_id.clone(), props.clone());
            }

            // Check depth limit
            if max_depth > 0 && depth >= max_depth {
                continue;
            }

            // Get neighbors based on direction
            let neighbors = self.get_neighbors(&node_id, direction, rel_type_filter);

            for (neighbor_id, rel_type) in neighbors {
                // Record edge
                result.edges.push((node_id.clone(), neighbor_id.clone(), rel_type));

                // Visit if not already visited
                if !visited.contains(&neighbor_id) {
                    visited.insert(neighbor_id.clone());
                    result.depths.insert(neighbor_id.clone(), depth + 1);
                    queue.push_back((neighbor_id, depth + 1));
                }
            }
        }

        result
    }

    /// Perform DFS traversal starting from given nodes.
    pub fn dfs(
        &self,
        starts: &[String],
        max_depth: usize,
        direction: Direction,
        rel_type_filter: Option<&str>,
    ) -> TraversalResult {
        let mut result = TraversalResult::default();
        let mut visited: FxHashSet<String> = FxHashSet::default();
        let mut stack: Vec<(String, usize)> = Vec::new();

        // Initialize with start nodes (reverse order for correct DFS order)
        for start in starts.iter().rev() {
            if self.nodes.contains_key(start) {
                stack.push((start.clone(), 0));
            }
        }

        // DFS loop
        while let Some((node_id, depth)) = stack.pop() {
            if visited.contains(&node_id) {
                continue;
            }

            visited.insert(node_id.clone());
            result.visited_nodes.push(node_id.clone());
            result.depths.insert(node_id.clone(), depth);

            // Get node properties
            if let Some(props) = self.nodes.get(&node_id) {
                result.node_properties.insert(node_id.clone(), props.clone());
            }

            // Check depth limit
            if max_depth > 0 && depth >= max_depth {
                continue;
            }

            // Get neighbors and push in reverse order for correct DFS
            let neighbors = self.get_neighbors(&node_id, direction, rel_type_filter);

            for (neighbor_id, rel_type) in neighbors.into_iter().rev() {
                if !visited.contains(&neighbor_id) {
                    result.edges.push((node_id.clone(), neighbor_id.clone(), rel_type));
                    stack.push((neighbor_id, depth + 1));
                }
            }
        }

        result
    }

    /// Get neighbors of a node based on direction and optional filter
    fn get_neighbors(
        &self,
        node_id: &str,
        direction: Direction,
        rel_type_filter: Option<&str>,
    ) -> Vec<(String, String)> {
        let mut neighbors = Vec::new();

        // Get outgoing neighbors
        if matches!(direction, Direction::Outgoing | Direction::Both) {
            if let Some(edges) = self.outgoing.get(node_id) {
                for (neighbor, rel_type) in edges {
                    if rel_type_filter.map_or(true, |f| rel_type == f) {
                        neighbors.push((neighbor.clone(), rel_type.clone()));
                    }
                }
            }
        }

        // Get incoming neighbors
        if matches!(direction, Direction::Incoming | Direction::Both) {
            if let Some(edges) = self.incoming.get(node_id) {
                for (neighbor, rel_type) in edges {
                    if rel_type_filter.map_or(true, |f| rel_type == f) {
                        neighbors.push((neighbor.clone(), rel_type.clone()));
                    }
                }
            }
        }

        neighbors
    }

    /// Get all nodes reachable within max_depth hops (for batch pre-fetching)
    pub fn get_reachable_nodes(
        &self,
        starts: &[String],
        max_depth: usize,
        direction: Direction,
    ) -> FxHashSet<String> {
        let result = self.bfs(starts, max_depth, direction, None);
        result.visited_nodes.into_iter().collect()
    }
}

/// Perform BFS traversal on pre-loaded graph data.
/// This is the main Python-facing function.
pub fn batch_traverse_bfs(
    nodes: Vec<(String, Vec<String>, HashMap<String, String>)>,
    edges: Vec<(String, String, String)>,
    starts: Vec<String>,
    max_depth: usize,
    direction: &str,
    rel_type_filter: Option<String>,
) -> (
    Vec<String>,                              // visited_nodes
    HashMap<String, HashMap<String, String>>, // node_properties (simplified)
    Vec<(String, String, String)>,            // edges
    HashMap<String, usize>,                   // depths
) {
    let traverser = GraphTraverser::new(nodes, edges);
    let dir = Direction::from_str(direction);
    let result = traverser.bfs(&starts, max_depth, dir, rel_type_filter.as_deref());

    // Convert NodeProperties to simple HashMap for Python
    let props: HashMap<String, HashMap<String, String>> = result
        .node_properties
        .into_iter()
        .map(|(k, v)| (k, v.properties))
        .collect();

    (result.visited_nodes, props, result.edges, result.depths)
}

/// Perform DFS traversal on pre-loaded graph data.
pub fn batch_traverse_dfs(
    nodes: Vec<(String, Vec<String>, HashMap<String, String>)>,
    edges: Vec<(String, String, String)>,
    starts: Vec<String>,
    max_depth: usize,
    direction: &str,
    rel_type_filter: Option<String>,
) -> (
    Vec<String>,
    HashMap<String, HashMap<String, String>>,
    Vec<(String, String, String)>,
    HashMap<String, usize>,
) {
    let traverser = GraphTraverser::new(nodes, edges);
    let dir = Direction::from_str(direction);
    let result = traverser.dfs(&starts, max_depth, dir, rel_type_filter.as_deref());

    let props: HashMap<String, HashMap<String, String>> = result
        .node_properties
        .into_iter()
        .map(|(k, v)| (k, v.properties))
        .collect();

    (result.visited_nodes, props, result.edges, result.depths)
}

/// Extract a subgraph around given nodes using parallel BFS from multiple starts
pub fn extract_subgraph_parallel(
    nodes: Vec<(String, Vec<String>, HashMap<String, String>)>,
    edges: Vec<(String, String, String)>,
    starts: Vec<String>,
    max_depth: usize,
) -> (Vec<String>, Vec<(String, String, String)>) {
    let traverser = GraphTraverser::new(nodes, edges);

    // Parallel BFS from each start node
    let all_reachable: FxHashSet<String> = starts
        .par_iter()
        .flat_map(|start| {
            traverser.get_reachable_nodes(&[start.clone()], max_depth, Direction::Both)
        })
        .collect();

    // Filter edges to only those within the subgraph
    let subgraph_edges: Vec<(String, String, String)> = traverser
        .outgoing
        .iter()
        .flat_map(|(source, targets)| {
            targets
                .iter()
                .filter(|(target, _)| {
                    all_reachable.contains(source) && all_reachable.contains(target)
                })
                .map(|(target, rel_type)| {
                    (source.clone(), target.clone(), rel_type.clone())
                })
        })
        .collect();

    (all_reachable.into_iter().collect(), subgraph_edges)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn create_test_graph() -> GraphTraverser {
        let nodes = vec![
            ("A".to_string(), vec!["Node".to_string()], HashMap::new()),
            ("B".to_string(), vec!["Node".to_string()], HashMap::new()),
            ("C".to_string(), vec!["Node".to_string()], HashMap::new()),
            ("D".to_string(), vec!["Node".to_string()], HashMap::new()),
        ];
        let edges = vec![
            ("A".to_string(), "B".to_string(), "CALLS".to_string()),
            ("B".to_string(), "C".to_string(), "CALLS".to_string()),
            ("C".to_string(), "D".to_string(), "CALLS".to_string()),
        ];
        GraphTraverser::new(nodes, edges)
    }

    #[test]
    fn test_bfs_basic() {
        let traverser = create_test_graph();
        let result = traverser.bfs(&["A".to_string()], 0, Direction::Outgoing, None);

        assert_eq!(result.visited_nodes, vec!["A", "B", "C", "D"]);
        assert_eq!(result.depths.get("A"), Some(&0));
        assert_eq!(result.depths.get("B"), Some(&1));
        assert_eq!(result.depths.get("C"), Some(&2));
        assert_eq!(result.depths.get("D"), Some(&3));
    }

    #[test]
    fn test_bfs_with_depth_limit() {
        let traverser = create_test_graph();
        let result = traverser.bfs(&["A".to_string()], 2, Direction::Outgoing, None);

        assert!(result.visited_nodes.contains(&"A".to_string()));
        assert!(result.visited_nodes.contains(&"B".to_string()));
        assert!(result.visited_nodes.contains(&"C".to_string()));
        // D should not be visited (depth 3 > max_depth 2)
        assert!(!result.visited_nodes.contains(&"D".to_string()));
    }

    #[test]
    fn test_dfs_basic() {
        let traverser = create_test_graph();
        let result = traverser.dfs(&["A".to_string()], 0, Direction::Outgoing, None);

        // All nodes should be visited
        assert_eq!(result.visited_nodes.len(), 4);
        assert!(result.visited_nodes.contains(&"A".to_string()));
        assert!(result.visited_nodes.contains(&"D".to_string()));
    }

    #[test]
    fn test_incoming_direction() {
        let traverser = create_test_graph();
        let result = traverser.bfs(&["D".to_string()], 0, Direction::Incoming, None);

        // Should traverse backwards: D -> C -> B -> A
        assert_eq!(result.visited_nodes, vec!["D", "C", "B", "A"]);
    }

    #[test]
    fn test_rel_type_filter() {
        let nodes = vec![
            ("A".to_string(), vec![], HashMap::new()),
            ("B".to_string(), vec![], HashMap::new()),
            ("C".to_string(), vec![], HashMap::new()),
        ];
        let edges = vec![
            ("A".to_string(), "B".to_string(), "CALLS".to_string()),
            ("A".to_string(), "C".to_string(), "IMPORTS".to_string()),
        ];
        let traverser = GraphTraverser::new(nodes, edges);

        let result = traverser.bfs(&["A".to_string()], 0, Direction::Outgoing, Some("CALLS"));

        assert!(result.visited_nodes.contains(&"B".to_string()));
        assert!(!result.visited_nodes.contains(&"C".to_string()));
    }
}
