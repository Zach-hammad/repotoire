//! Pure Rust graph storage using petgraph + sled
//!
//! Replaces Kuzu for better cross-platform compatibility.
//! No C++ dependencies, builds everywhere.

use anyhow::{Context, Result};
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

    /// Get node index by qualified name
    pub fn get_node_index(&self, qn: &str) -> Option<NodeIndex> {
        self.node_index.read().unwrap().get(qn).copied()
    }

    /// Get node by qualified name
    pub fn get_node(&self, qn: &str) -> Option<CodeNode> {
        let index = self.node_index.read().unwrap();
        let graph = self.graph.read().unwrap();

        index.get(qn).and_then(|&idx| graph.node_weight(idx).cloned())
    }

    /// Update a node's property
    pub fn update_node_property(&self, qn: &str, key: &str, value: impl Into<serde_json::Value>) -> bool {
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
                n.kind == NodeKind::Function
                    && n.complexity().map_or(false, |c| c >= min_complexity)
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
                n.kind == NodeKind::Function
                    && n.param_count().map_or(false, |p| p >= min_params)
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

    /// Find circular dependencies in imports
    pub fn find_import_cycles(&self) -> Vec<Vec<String>> {
        self.find_cycles(EdgeKind::Imports)
    }

    /// Find circular dependencies in calls
    pub fn find_call_cycles(&self) -> Vec<Vec<String>> {
        self.find_cycles(EdgeKind::Calls)
    }

    /// Find cycles for a specific edge kind
    fn find_cycles(&self, edge_kind: EdgeKind) -> Vec<Vec<String>> {
        let graph = self.graph.read().unwrap();
        let mut cycles = Vec::new();
        let mut visited = HashSet::new();
        let mut rec_stack = HashSet::new();
        let mut path = Vec::new();

        for node_idx in graph.node_indices() {
            if !visited.contains(&node_idx) {
                self.find_cycles_dfs(
                    node_idx,
                    &edge_kind,
                    &graph,
                    &mut visited,
                    &mut rec_stack,
                    &mut path,
                    &mut cycles,
                );
            }
        }

        cycles
    }

    fn find_cycles_dfs(
        &self,
        node: NodeIndex,
        edge_kind: &EdgeKind,
        graph: &DiGraph<CodeNode, CodeEdge>,
        visited: &mut HashSet<NodeIndex>,
        rec_stack: &mut HashSet<NodeIndex>,
        path: &mut Vec<NodeIndex>,
        cycles: &mut Vec<Vec<String>>,
    ) {
        visited.insert(node);
        rec_stack.insert(node);
        path.push(node);

        for edge in graph.edges_directed(node, Direction::Outgoing) {
            if edge.weight().kind != *edge_kind {
                continue;
            }

            let target = edge.target();

            if !visited.contains(&target) {
                self.find_cycles_dfs(target, edge_kind, graph, visited, rec_stack, path, cycles);
            } else if rec_stack.contains(&target) {
                // Found a cycle
                let cycle_start = path.iter().position(|&n| n == target).unwrap();
                let cycle: Vec<String> = path[cycle_start..]
                    .iter()
                    .filter_map(|&idx| graph.node_weight(idx))
                    .map(|n| n.qualified_name.clone())
                    .collect();
                if cycle.len() >= 2 {
                    cycles.push(cycle);
                }
            }
        }

        path.pop();
        rec_stack.remove(&node);
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
}
