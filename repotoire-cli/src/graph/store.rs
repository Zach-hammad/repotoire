//! Pure Rust graph storage using petgraph + sled
//!
//! Replaces Kuzu for better cross-platform compatibility.
//! No C++ dependencies, builds everywhere.

use anyhow::{Context, Result};
use petgraph::algo::tarjan_scc;
use petgraph::graph::{DiGraph, NodeIndex};
use petgraph::visit::EdgeRef;
use petgraph::Direction;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::RwLock;

/// Node types in the code graph
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum NodeKind {
    File,
    Function,
    Class,
    Module,
    Variable,
    Commit,
}

/// A node in the code graph
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodeNode {
    pub kind: NodeKind,
    pub name: String,
    pub qualified_name: String,
    pub file_path: String,
    pub line_start: u32,
    pub line_end: u32,
    pub language: Option<String>,
    pub properties: HashMap<String, serde_json::Value>,
}

impl CodeNode {
    pub fn new(kind: NodeKind, name: &str, file_path: &str) -> Self {
        Self {
            kind,
            name: name.to_string(),
            qualified_name: name.to_string(),
            file_path: file_path.to_string(),
            line_start: 0,
            line_end: 0,
            language: None,
            properties: HashMap::new(),
        }
    }

    pub fn file(path: &str) -> Self {
        Self::new(NodeKind::File, path, path)
    }

    pub fn function(name: &str, file_path: &str) -> Self {
        Self::new(NodeKind::Function, name, file_path)
    }

    pub fn class(name: &str, file_path: &str) -> Self {
        Self::new(NodeKind::Class, name, file_path)
    }

    pub fn with_qualified_name(mut self, qn: &str) -> Self {
        self.qualified_name = qn.to_string();
        self
    }

    pub fn with_lines(mut self, start: u32, end: u32) -> Self {
        self.line_start = start;
        self.line_end = end;
        self
    }

    pub fn with_language(mut self, lang: &str) -> Self {
        self.language = Some(lang.to_string());
        self
    }

    pub fn with_property(mut self, key: &str, value: impl Into<serde_json::Value>) -> Self {
        self.properties.insert(key.to_string(), value.into());
        self
    }

    pub fn set_property(&mut self, key: &str, value: impl Into<serde_json::Value>) {
        self.properties.insert(key.to_string(), value.into());
    }

    pub fn get_i64(&self, key: &str) -> Option<i64> {
        self.properties.get(key).and_then(|v| v.as_i64())
    }

    pub fn get_f64(&self, key: &str) -> Option<f64> {
        self.properties.get(key).and_then(|v| v.as_f64())
    }

    pub fn get_str(&self, key: &str) -> Option<&str> {
        self.properties.get(key).and_then(|v| v.as_str())
    }

    pub fn get_bool(&self, key: &str) -> Option<bool> {
        self.properties.get(key).and_then(|v| v.as_bool())
    }

    /// Lines of code
    pub fn loc(&self) -> u32 {
        if self.line_end >= self.line_start {
            self.line_end - self.line_start + 1
        } else {
            0
        }
    }

    /// Cyclomatic complexity (if stored)
    pub fn complexity(&self) -> Option<i64> {
        self.get_i64("complexity")
    }

    /// Parameter count (for functions)
    pub fn param_count(&self) -> Option<i64> {
        self.get_i64("paramCount")
    }
}

/// Edge types in the code graph
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum EdgeKind {
    Calls,
    Imports,
    Contains,
    Inherits,
    Uses,
    ModifiedIn,
}

/// An edge in the code graph
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodeEdge {
    pub kind: EdgeKind,
    pub properties: HashMap<String, serde_json::Value>,
}

impl CodeEdge {
    pub fn new(kind: EdgeKind) -> Self {
        Self {
            kind,
            properties: HashMap::new(),
        }
    }

    pub fn calls() -> Self {
        Self::new(EdgeKind::Calls)
    }

    pub fn imports() -> Self {
        Self::new(EdgeKind::Imports)
    }

    pub fn contains() -> Self {
        Self::new(EdgeKind::Contains)
    }

    pub fn inherits() -> Self {
        Self::new(EdgeKind::Inherits)
    }

    pub fn uses() -> Self {
        Self::new(EdgeKind::Uses)
    }

    pub fn with_property(mut self, key: &str, value: impl Into<serde_json::Value>) -> Self {
        self.properties.insert(key.to_string(), value.into());
        self
    }
}

/// Pure Rust graph store - replaces Kuzu
pub struct GraphStore {
    /// In-memory graph
    graph: RwLock<DiGraph<CodeNode, CodeEdge>>,
    /// Node lookup by qualified name
    node_index: RwLock<HashMap<String, NodeIndex>>,
    /// Persistence layer (optional)
    db: Option<sled::Db>,
    /// Database path for lazy loading
    #[allow(dead_code)] // Stored for future lazy loading support
    db_path: Option<std::path::PathBuf>,
}

impl GraphStore {
    /// Create or open a graph store at the given path
    pub fn new(db_path: &Path) -> Result<Self> {
        // Ensure parent directory exists
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let db = sled::open(db_path).context("Failed to open sled database")?;

        let store = Self {
            graph: RwLock::new(DiGraph::new()),
            node_index: RwLock::new(HashMap::new()),
            db: Some(db),
            db_path: Some(db_path.to_path_buf()),
        };

        // Load existing data
        store.load()?;

        Ok(store)
    }

    /// Create an in-memory only store (no persistence)
    pub fn in_memory() -> Self {
        Self {
            graph: RwLock::new(DiGraph::new()),
            node_index: RwLock::new(HashMap::new()),
            db: None,
            db_path: None,
        }
    }

    /// Clear all data
    pub fn clear(&self) -> Result<()> {
        let mut graph = self.graph.write().unwrap();
        let mut index = self.node_index.write().unwrap();

        graph.clear();
        index.clear();

        if let Some(ref db) = self.db {
            db.clear()?;
        }

        Ok(())
    }

    // ==================== Node Operations ====================

    /// Add a node to the graph
    pub fn add_node(&self, node: CodeNode) -> NodeIndex {
        let mut graph = self.graph.write().unwrap();
        let mut index = self.node_index.write().unwrap();

        let qn = node.qualified_name.clone();

        // Check if node already exists
        if let Some(&idx) = index.get(&qn) {
            // Update existing node
            if let Some(existing) = graph.node_weight_mut(idx) {
                *existing = node;
            }
            return idx;
        }

        let idx = graph.add_node(node);
        index.insert(qn, idx);
        idx
    }

    /// Add multiple nodes at once (batch operation, single lock acquisition)
    pub fn add_nodes_batch(&self, nodes: Vec<CodeNode>) -> Vec<NodeIndex> {
        let mut graph = self.graph.write().unwrap();
        let mut index = self.node_index.write().unwrap();
        let mut indices = Vec::with_capacity(nodes.len());

        for node in nodes {
            let qn = node.qualified_name.clone();

            if let Some(&idx) = index.get(&qn) {
                if let Some(existing) = graph.node_weight_mut(idx) {
                    *existing = node;
                }
                indices.push(idx);
            } else {
                let idx = graph.add_node(node);
                index.insert(qn, idx);
                indices.push(idx);
            }
        }

        indices
    }

    /// Get node index by qualified name
    pub fn get_node_index(&self, qn: &str) -> Option<NodeIndex> {
        self.node_index.read().unwrap().get(qn).copied()
    }

    /// Get node by qualified name
    pub fn get_node(&self, qn: &str) -> Option<CodeNode> {
        let index = self.node_index.read().unwrap();
        let graph = self.graph.read().unwrap();

        index
            .get(qn)
            .and_then(|&idx| graph.node_weight(idx).cloned())
    }

    /// Update a node's property
    pub fn update_node_property(
        &self,
        qn: &str,
        key: &str,
        value: impl Into<serde_json::Value>,
    ) -> bool {
        let index = self.node_index.read().unwrap();
        if let Some(&idx) = index.get(qn) {
            drop(index);
            let mut graph = self.graph.write().unwrap();
            if let Some(node) = graph.node_weight_mut(idx) {
                node.set_property(key, value);
                return true;
            }
        }
        false
    }

    /// Update multiple properties on a node
    pub fn update_node_properties(&self, qn: &str, props: &[(&str, serde_json::Value)]) -> bool {
        let index = self.node_index.read().unwrap();
        if let Some(&idx) = index.get(qn) {
            drop(index);
            let mut graph = self.graph.write().unwrap();
            if let Some(node) = graph.node_weight_mut(idx) {
                for (key, value) in props {
                    node.set_property(key, value.clone());
                }
                return true;
            }
        }
        false
    }

    /// Get all nodes of a specific kind
    pub fn get_nodes_by_kind(&self, kind: NodeKind) -> Vec<CodeNode> {
        let graph = self.graph.read().unwrap();

        graph
            .node_weights()
            .filter(|n| n.kind == kind)
            .cloned()
            .collect()
    }

    /// Get all files
    pub fn get_files(&self) -> Vec<CodeNode> {
        self.get_nodes_by_kind(NodeKind::File)
    }

    /// Get all functions
    pub fn get_functions(&self) -> Vec<CodeNode> {
        self.get_nodes_by_kind(NodeKind::Function)
    }

    /// Get all classes
    pub fn get_classes(&self) -> Vec<CodeNode> {
        self.get_nodes_by_kind(NodeKind::Class)
    }

    /// Get functions in a specific file
    pub fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        let graph = self.graph.read().unwrap();

        graph
            .node_weights()
            .filter(|n| n.kind == NodeKind::Function && n.file_path == file_path)
            .cloned()
            .collect()
    }

    /// Get classes in a specific file
    pub fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        let graph = self.graph.read().unwrap();

        graph
            .node_weights()
            .filter(|n| n.kind == NodeKind::Class && n.file_path == file_path)
            .cloned()
            .collect()
    }

    /// Get functions with complexity above threshold
    pub fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        let graph = self.graph.read().unwrap();

        graph
            .node_weights()
            .filter(|n| {
                n.kind == NodeKind::Function && n.complexity().is_some_and(|c| c >= min_complexity)
            })
            .cloned()
            .collect()
    }

    /// Get functions with many parameters
    pub fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        let graph = self.graph.read().unwrap();

        graph
            .node_weights()
            .filter(|n| {
                n.kind == NodeKind::Function && n.param_count().is_some_and(|p| p >= min_params)
            })
            .cloned()
            .collect()
    }

    // ==================== Edge Operations ====================

    /// Add an edge between nodes by index
    pub fn add_edge(&self, from: NodeIndex, to: NodeIndex, edge: CodeEdge) {
        let mut graph = self.graph.write().unwrap();
        graph.add_edge(from, to, edge);
    }

    /// Add edge by qualified names (returns false if either node doesn't exist)
    pub fn add_edge_by_name(&self, from_qn: &str, to_qn: &str, edge: CodeEdge) -> bool {
        let index = self.node_index.read().unwrap();

        if let (Some(&from), Some(&to)) = (index.get(from_qn), index.get(to_qn)) {
            drop(index);
            self.add_edge(from, to, edge);
            true
        } else {
            false
        }
    }

    /// Add multiple edges at once (batch operation)
    pub fn add_edges_batch(&self, edges: Vec<(String, String, CodeEdge)>) -> usize {
        let index = self.node_index.read().unwrap();
        let mut graph = self.graph.write().unwrap();
        let mut added = 0;

        for (from_qn, to_qn, edge) in edges {
            if let (Some(&from), Some(&to)) = (index.get(&from_qn), index.get(&to_qn)) {
                graph.add_edge(from, to, edge);
                added += 1;
            }
        }

        added
    }

    /// Get all edges of a specific kind as (source_qn, target_qn) pairs
    pub fn get_edges_by_kind(&self, kind: EdgeKind) -> Vec<(String, String)> {
        let graph = self.graph.read().unwrap();

        graph
            .edge_references()
            .filter(|e| e.weight().kind == kind)
            .filter_map(|e| {
                let src = graph.node_weight(e.source())?;
                let dst = graph.node_weight(e.target())?;
                Some((src.qualified_name.clone(), dst.qualified_name.clone()))
            })
            .collect()
    }

    /// Get all import edges (file -> file)
    pub fn get_imports(&self) -> Vec<(String, String)> {
        self.get_edges_by_kind(EdgeKind::Imports)
    }

    /// Get all call edges (function -> function)
    pub fn get_calls(&self) -> Vec<(String, String)> {
        self.get_edges_by_kind(EdgeKind::Calls)
    }

    /// Get all inheritance edges (child -> parent)
    pub fn get_inheritance(&self) -> Vec<(String, String)> {
        self.get_edges_by_kind(EdgeKind::Inherits)
    }

    /// Get callers of a function (who calls this?)
    pub fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        let index = self.node_index.read().unwrap();
        let graph = self.graph.read().unwrap();

        if let Some(&idx) = index.get(qn) {
            graph
                .edges_directed(idx, Direction::Incoming)
                .filter(|e| e.weight().kind == EdgeKind::Calls)
                .filter_map(|e| graph.node_weight(e.source()).cloned())
                .collect()
        } else {
            vec![]
        }
    }

    /// Get callees of a function (what does this call?)
    pub fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        let index = self.node_index.read().unwrap();
        let graph = self.graph.read().unwrap();

        if let Some(&idx) = index.get(qn) {
            graph
                .edges_directed(idx, Direction::Outgoing)
                .filter(|e| e.weight().kind == EdgeKind::Calls)
                .filter_map(|e| graph.node_weight(e.target()).cloned())
                .collect()
        } else {
            vec![]
        }
    }

    /// Get importers of a module/class (who imports this?)
    pub fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        let index = self.node_index.read().unwrap();
        let graph = self.graph.read().unwrap();

        if let Some(&idx) = index.get(qn) {
            graph
                .edges_directed(idx, Direction::Incoming)
                .filter(|e| e.weight().kind == EdgeKind::Imports)
                .filter_map(|e| graph.node_weight(e.source()).cloned())
                .collect()
        } else {
            vec![]
        }
    }

    /// Get parent classes (what does this inherit from?)
    pub fn get_parent_classes(&self, qn: &str) -> Vec<CodeNode> {
        let index = self.node_index.read().unwrap();
        let graph = self.graph.read().unwrap();

        if let Some(&idx) = index.get(qn) {
            graph
                .edges_directed(idx, Direction::Outgoing)
                .filter(|e| e.weight().kind == EdgeKind::Inherits)
                .filter_map(|e| graph.node_weight(e.target()).cloned())
                .collect()
        } else {
            vec![]
        }
    }

    /// Get child classes (what inherits from this?)
    pub fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        let index = self.node_index.read().unwrap();
        let graph = self.graph.read().unwrap();

        if let Some(&idx) = index.get(qn) {
            graph
                .edges_directed(idx, Direction::Incoming)
                .filter(|e| e.weight().kind == EdgeKind::Inherits)
                .filter_map(|e| graph.node_weight(e.source()).cloned())
                .collect()
        } else {
            vec![]
        }
    }

    // ==================== Graph Metrics ====================

    /// Get in-degree (fan-in) for a node
    pub fn fan_in(&self, qn: &str) -> usize {
        let index = self.node_index.read().unwrap();
        let graph = self.graph.read().unwrap();

        if let Some(&idx) = index.get(qn) {
            graph.edges_directed(idx, Direction::Incoming).count()
        } else {
            0
        }
    }

    /// Get out-degree (fan-out) for a node
    pub fn fan_out(&self, qn: &str) -> usize {
        let index = self.node_index.read().unwrap();
        let graph = self.graph.read().unwrap();

        if let Some(&idx) = index.get(qn) {
            graph.edges_directed(idx, Direction::Outgoing).count()
        } else {
            0
        }
    }

    /// Get call fan-in (how many functions call this?)
    pub fn call_fan_in(&self, qn: &str) -> usize {
        let index = self.node_index.read().unwrap();
        let graph = self.graph.read().unwrap();

        if let Some(&idx) = index.get(qn) {
            graph
                .edges_directed(idx, Direction::Incoming)
                .filter(|e| e.weight().kind == EdgeKind::Calls)
                .count()
        } else {
            0
        }
    }

    /// Get call fan-out (how many functions does this call?)
    pub fn call_fan_out(&self, qn: &str) -> usize {
        let index = self.node_index.read().unwrap();
        let graph = self.graph.read().unwrap();

        if let Some(&idx) = index.get(qn) {
            graph
                .edges_directed(idx, Direction::Outgoing)
                .filter(|e| e.weight().kind == EdgeKind::Calls)
                .count()
        } else {
            0
        }
    }

    /// Get node count
    pub fn node_count(&self) -> usize {
        self.graph.read().unwrap().node_count()
    }

    /// Get edge count
    pub fn edge_count(&self) -> usize {
        self.graph.read().unwrap().edge_count()
    }

    /// Get statistics
    pub fn stats(&self) -> HashMap<String, i64> {
        let graph = self.graph.read().unwrap();
        let mut stats = HashMap::new();

        let mut file_count = 0i64;
        let mut func_count = 0i64;
        let mut class_count = 0i64;

        for node in graph.node_weights() {
            match node.kind {
                NodeKind::File => file_count += 1,
                NodeKind::Function => func_count += 1,
                NodeKind::Class => class_count += 1,
                _ => {}
            }
        }

        stats.insert("total_files".to_string(), file_count);
        stats.insert("total_functions".to_string(), func_count);
        stats.insert("total_classes".to_string(), class_count);
        stats.insert("total_nodes".to_string(), graph.node_count() as i64);
        stats.insert("total_edges".to_string(), graph.edge_count() as i64);

        stats
    }

    // ==================== Cycle Detection ====================
    //
    // Uses Tarjan's SCC algorithm for efficient cycle detection.
    // Instead of finding all possible cycles (which can be exponential),
    // we find strongly connected components (SCCs) - any SCC with >1 node
    // represents a circular dependency.
    //
    // This approach:
    // 1. Runs in O(V + E) time
    // 2. Reports each cycle exactly once (no duplicates)
    // 3. Handles large codebases efficiently

    /// Find circular dependencies in imports using SCC
    pub fn find_import_cycles(&self) -> Vec<Vec<String>> {
        self.find_cycles_scc(EdgeKind::Imports)
    }

    /// Find circular dependencies in calls using SCC
    pub fn find_call_cycles(&self) -> Vec<Vec<String>> {
        self.find_cycles_scc(EdgeKind::Calls)
    }

    /// Find cycles using Tarjan's SCC algorithm
    ///
    /// Returns strongly connected components with >1 node, which represent cycles.
    /// Each SCC is returned as a list of qualified names.
    fn find_cycles_scc(&self, edge_kind: EdgeKind) -> Vec<Vec<String>> {
        let graph = self.graph.read().unwrap();

        // Build a filtered subgraph with only edges of the specified kind
        // This is more efficient than filtering during SCC traversal
        let mut filtered_graph: DiGraph<NodeIndex, ()> = DiGraph::new();
        let mut idx_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();
        let mut reverse_map: HashMap<NodeIndex, NodeIndex> = HashMap::new();

        // Add all nodes that have at least one edge of the specified kind
        let relevant_nodes: HashSet<NodeIndex> = graph
            .edge_references()
            .filter(|e| {
                if e.weight().kind != edge_kind {
                    return false;
                }
                // Skip type-only imports
                if edge_kind == EdgeKind::Imports {
                    if let Some(is_type_only) = e.weight().properties.get("is_type_only") {
                        if is_type_only == "true" {
                            return false;
                        }
                    }
                }
                true
            })
            .flat_map(|e| [e.source(), e.target()])
            .collect();

        for orig_idx in relevant_nodes {
            let new_idx = filtered_graph.add_node(orig_idx);
            idx_map.insert(orig_idx, new_idx);
            reverse_map.insert(new_idx, orig_idx);
        }

        // Add filtered edges
        for edge in graph.edge_references() {
            if edge.weight().kind != edge_kind {
                continue;
            }
            // Skip type-only imports
            if edge_kind == EdgeKind::Imports {
                if let Some(is_type_only) = edge.weight().properties.get("is_type_only") {
                    if is_type_only == "true" {
                        continue;
                    }
                }
            }

            if let (Some(&from), Some(&to)) =
                (idx_map.get(&edge.source()), idx_map.get(&edge.target()))
            {
                filtered_graph.add_edge(from, to, ());
            }
        }

        // Run Tarjan's SCC algorithm on the filtered graph
        let sccs = tarjan_scc(&filtered_graph);

        // Convert SCCs back to qualified names
        // Only keep SCCs with >1 node (actual cycles)
        let mut cycles: Vec<Vec<String>> = sccs
            .into_iter()
            .filter(|scc| scc.len() > 1)
            .map(|scc| {
                let mut names: Vec<String> = scc
                    .iter()
                    .filter_map(|&filtered_idx| reverse_map.get(&filtered_idx))
                    .filter_map(|&orig_idx| graph.node_weight(orig_idx))
                    .map(|n| n.qualified_name.clone())
                    .collect();

                // Sort for consistent ordering and deduplication
                names.sort();
                names
            })
            .collect();

        // Deduplicate (shouldn't be needed with SCC, but just in case)
        cycles.sort();
        cycles.dedup();

        // Sort by size (largest cycles first - they're usually most important)
        cycles.sort_by_key(|c| std::cmp::Reverse(c.len()));

        cycles
    }

    /// Find the minimal cycle through a specific node (for detailed reporting)
    ///
    /// This is useful when you want to show the shortest cycle involving a particular file.
    pub fn find_minimal_cycle(&self, start_qn: &str, edge_kind: EdgeKind) -> Option<Vec<String>> {
        let graph = self.graph.read().unwrap();
        let index = self.node_index.read().unwrap();

        let start_idx = index.get(start_qn)?;

        // BFS to find shortest cycle back to start
        let mut queue = std::collections::VecDeque::new();
        let mut visited: HashMap<NodeIndex, Vec<NodeIndex>> = HashMap::new();

        queue.push_back((*start_idx, vec![*start_idx]));
        visited.insert(*start_idx, vec![*start_idx]);

        while let Some((current, path)) = queue.pop_front() {
            for edge in graph.edges_directed(current, Direction::Outgoing) {
                if edge.weight().kind != edge_kind {
                    continue;
                }

                // Skip type-only imports
                if edge_kind == EdgeKind::Imports {
                    if let Some(is_type_only) = edge.weight().properties.get("is_type_only") {
                        if is_type_only == "true" {
                            continue;
                        }
                    }
                }

                let target = edge.target();

                // Found cycle back to start!
                if target == *start_idx && path.len() > 1 {
                    return Some(
                        path.iter()
                            .filter_map(|&idx| graph.node_weight(idx))
                            .map(|n| n.qualified_name.clone())
                            .collect(),
                    );
                }

                // Continue BFS if not visited
                if let std::collections::hash_map::Entry::Vacant(e) = visited.entry(target) {
                    let mut new_path = path.clone();
                    new_path.push(target);
                    e.insert(new_path.clone());
                    queue.push_back((target, new_path));
                }
            }
        }

        None
    }

    // ==================== Persistence ====================

    /// Persist graph to sled
    pub fn save(&self) -> Result<()> {
        let db = match &self.db {
            Some(db) => db,
            None => return Ok(()), // In-memory only, nothing to save
        };

        let graph = self.graph.read().unwrap();

        // Clear old data
        db.clear()?;

        // Save nodes
        for node in graph.node_weights() {
            let key = format!("node:{}", node.qualified_name);
            let value = serde_json::to_vec(node)?;
            db.insert(key.as_bytes(), value)?;
        }

        // Save edges
        let edges: Vec<_> = graph
            .edge_references()
            .filter_map(|e| {
                let src = graph.node_weight(e.source())?;
                let dst = graph.node_weight(e.target())?;
                Some((
                    src.qualified_name.clone(),
                    dst.qualified_name.clone(),
                    e.weight().clone(),
                ))
            })
            .collect();

        let edges_data = serde_json::to_vec(&edges)?;
        db.insert(b"__edges__", edges_data)?;

        db.flush()?;
        Ok(())
    }

    /// Load graph from sled
    fn load(&self) -> Result<()> {
        let db = match &self.db {
            Some(db) => db,
            None => return Ok(()),
        };

        let mut graph = self.graph.write().unwrap();
        let mut index = self.node_index.write().unwrap();

        // Load nodes
        for item in db.scan_prefix(b"node:") {
            let (_, value) = item?;
            let node: CodeNode = serde_json::from_slice(&value)?;
            let qn = node.qualified_name.clone();
            let idx = graph.add_node(node);
            index.insert(qn, idx);
        }

        // Load edges
        if let Some(edges_data) = db.get(b"__edges__")? {
            let edges: Vec<(String, String, CodeEdge)> = serde_json::from_slice(&edges_data)?;
            for (src_qn, dst_qn, edge) in edges {
                if let (Some(&src), Some(&dst)) = (index.get(&src_qn), index.get(&dst_qn)) {
                    graph.add_edge(src, dst, edge);
                }
            }
        }

        Ok(())
    }
}

impl Drop for GraphStore {
    fn drop(&mut self) {
        // Explicitly flush and close the sled database to release the lock
        if let Some(ref db) = self.db {
            let _ = db.flush();
            // Trigger async flush (we don't need to await it)
            drop(db.flush_async());
        }
        // Taking ownership of db triggers sled's Drop which releases the lock
        let _ = self.db.take();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_basic_operations() {
        let store = GraphStore::in_memory();

        // Add nodes
        let file = CodeNode::file("main.py");
        let func = CodeNode::function("main", "main.py")
            .with_qualified_name("main.py::main")
            .with_lines(1, 10)
            .with_property("complexity", 5);

        store.add_node(file);
        store.add_node(func);

        // Verify
        assert_eq!(store.node_count(), 2);
        assert_eq!(store.get_files().len(), 1);
        assert_eq!(store.get_functions().len(), 1);

        let f = store.get_node("main.py::main").unwrap();
        assert_eq!(f.complexity(), Some(5));
    }

    #[test]
    fn test_edges() {
        let store = GraphStore::in_memory();

        store.add_node(CodeNode::function("a", "test.py").with_qualified_name("a"));
        store.add_node(CodeNode::function("b", "test.py").with_qualified_name("b"));

        store.add_edge_by_name("a", "b", CodeEdge::calls());

        assert_eq!(store.get_calls().len(), 1);
        assert_eq!(store.call_fan_out("a"), 1);
        assert_eq!(store.call_fan_in("b"), 1);
    }

    #[test]
    fn test_persistence() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("test.db");

        // Create and save
        {
            let store = GraphStore::new(&path).unwrap();
            store.add_node(CodeNode::file("test.py"));
            store.save().unwrap();
        }

        // Reload and verify
        {
            let store = GraphStore::new(&path).unwrap();
            assert_eq!(store.get_files().len(), 1);
        }
    }

    #[test]
    fn test_scc_cycle_detection_simple() {
        // A -> B -> C -> A (simple cycle)
        let store = GraphStore::in_memory();

        store.add_node(CodeNode::file("a.py"));
        store.add_node(CodeNode::file("b.py"));
        store.add_node(CodeNode::file("c.py"));

        store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
        store.add_edge_by_name("b.py", "c.py", CodeEdge::imports());
        store.add_edge_by_name("c.py", "a.py", CodeEdge::imports());

        let cycles = store.find_import_cycles();
        assert_eq!(cycles.len(), 1, "Should find exactly 1 cycle");
        assert_eq!(cycles[0].len(), 3, "Cycle should have 3 nodes");
    }

    #[test]
    fn test_scc_cycle_detection_no_duplicate() {
        // The old algorithm would report this cycle multiple times
        // from different starting points. SCC reports it exactly once.
        let store = GraphStore::in_memory();

        // Create a larger cycle: A -> B -> C -> D -> E -> A
        for c in ['a', 'b', 'c', 'd', 'e'] {
            store.add_node(CodeNode::file(&format!("{}.py", c)));
        }

        store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
        store.add_edge_by_name("b.py", "c.py", CodeEdge::imports());
        store.add_edge_by_name("c.py", "d.py", CodeEdge::imports());
        store.add_edge_by_name("d.py", "e.py", CodeEdge::imports());
        store.add_edge_by_name("e.py", "a.py", CodeEdge::imports());

        let cycles = store.find_import_cycles();
        assert_eq!(cycles.len(), 1, "SCC should report exactly 1 cycle, not 5");
        assert_eq!(cycles[0].len(), 5, "Cycle should have 5 nodes");
    }

    #[test]
    fn test_scc_multiple_independent_cycles() {
        // Two independent cycles
        let store = GraphStore::in_memory();

        // Cycle 1: A -> B -> A
        store.add_node(CodeNode::file("a.py"));
        store.add_node(CodeNode::file("b.py"));
        store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
        store.add_edge_by_name("b.py", "a.py", CodeEdge::imports());

        // Cycle 2: X -> Y -> Z -> X
        store.add_node(CodeNode::file("x.py"));
        store.add_node(CodeNode::file("y.py"));
        store.add_node(CodeNode::file("z.py"));
        store.add_edge_by_name("x.py", "y.py", CodeEdge::imports());
        store.add_edge_by_name("y.py", "z.py", CodeEdge::imports());
        store.add_edge_by_name("z.py", "x.py", CodeEdge::imports());

        let cycles = store.find_import_cycles();
        assert_eq!(cycles.len(), 2, "Should find 2 independent cycles");
    }

    #[test]
    fn test_scc_large_interconnected() {
        // Worst case for old algorithm: fully connected component
        // Old algo would find O(n!) cycles, SCC finds 1
        let store = GraphStore::in_memory();

        let names: Vec<String> = (0..5).map(|i| format!("file{}.py", i)).collect();
        for name in &names {
            store.add_node(CodeNode::file(name));
        }

        // Create edges making it fully connected (worst case for naive cycle detection)
        for src in &names {
            for dst in &names {
                if src != dst {
                    store.add_edge_by_name(src, dst, CodeEdge::imports());
                }
            }
        }

        let cycles = store.find_import_cycles();
        // SCC will find exactly 1 strongly connected component
        assert_eq!(cycles.len(), 1, "Fully connected graph = 1 SCC");
        assert_eq!(cycles[0].len(), 5, "SCC should have all 5 nodes");
    }

    #[test]
    fn test_scc_no_cycle() {
        // Linear chain: no cycle
        let store = GraphStore::in_memory();

        store.add_node(CodeNode::file("a.py"));
        store.add_node(CodeNode::file("b.py"));
        store.add_node(CodeNode::file("c.py"));

        store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
        store.add_edge_by_name("b.py", "c.py", CodeEdge::imports());

        let cycles = store.find_import_cycles();
        assert!(cycles.is_empty(), "Linear chain should have no cycles");
    }

    #[test]
    fn test_minimal_cycle() {
        let store = GraphStore::in_memory();

        store.add_node(CodeNode::file("a.py"));
        store.add_node(CodeNode::file("b.py"));
        store.add_node(CodeNode::file("c.py"));

        store.add_edge_by_name("a.py", "b.py", CodeEdge::imports());
        store.add_edge_by_name("b.py", "c.py", CodeEdge::imports());
        store.add_edge_by_name("c.py", "a.py", CodeEdge::imports());

        let cycle = store.find_minimal_cycle("a.py", EdgeKind::Imports);
        assert!(cycle.is_some(), "Should find cycle through a.py");
        let cycle = cycle.unwrap();
        assert_eq!(cycle.len(), 3, "Minimal cycle should have 3 nodes");
        assert_eq!(cycle[0], "a.py", "Cycle should start with a.py");
    }
}
