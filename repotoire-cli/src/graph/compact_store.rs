//! Memory-efficient graph store using string interning
//!
//! This store uses ~60-70% less memory than the standard GraphStore
//! by interning all strings and using compact node representations.

use super::interner::{
    CompactEdge, CompactEdgeKind, CompactNode, CompactNodeKind, StrKey, StringInterner,
};
use super::{CodeEdge, CodeNode, EdgeKind, NodeKind};
use anyhow::Result;
use petgraph::graph::{DiGraph, NodeIndex};

use std::collections::HashMap;

use std::sync::RwLock;

/// Memory-efficient graph store
pub struct CompactGraphStore {
    /// String interner - stores each unique string once
    interner: StringInterner,

    /// Node storage - just indices and keys, no strings
    nodes: Vec<CompactNode>,

    /// Qualified name -> node index mapping
    qn_to_index: HashMap<StrKey, usize>,

    /// Edge storage - compact representation
    edges: Vec<CompactEdge>,

    /// Petgraph for algorithms (uses indices, not strings)
    graph: RwLock<DiGraph<usize, CompactEdgeKind>>,

    /// Node index in petgraph -> our index
    petgraph_to_idx: RwLock<HashMap<NodeIndex, usize>>,

    /// Our index -> petgraph node index (reverse mapping for O(1) edge insertion, #11)
    idx_to_petgraph: RwLock<HashMap<usize, NodeIndex>>,
}

impl CompactGraphStore {
    /// Create a new compact store
    pub fn new() -> Self {
        Self {
            interner: StringInterner::new(),
            nodes: Vec::new(),
            qn_to_index: HashMap::new(),
            edges: Vec::new(),
            graph: RwLock::new(DiGraph::new()),
            petgraph_to_idx: RwLock::new(HashMap::new()),
            idx_to_petgraph: RwLock::new(HashMap::new()),
        }
    }

    /// Create with estimated capacity
    pub fn with_capacity(files: usize, functions: usize, classes: usize) -> Self {
        let total_nodes = files + functions + classes;
        let estimated_strings = files + functions * 2 + classes * 2; // file paths + names + qns
        let estimated_bytes = estimated_strings * 60; // ~60 bytes per string average

        Self {
            interner: StringInterner::with_capacity(estimated_strings, estimated_bytes),
            nodes: Vec::with_capacity(total_nodes),
            qn_to_index: HashMap::with_capacity(total_nodes),
            edges: Vec::with_capacity(total_nodes * 3), // ~3 edges per node
            graph: RwLock::new(DiGraph::with_capacity(total_nodes, total_nodes * 3)),
            petgraph_to_idx: RwLock::new(HashMap::with_capacity(total_nodes)),
            idx_to_petgraph: RwLock::new(HashMap::with_capacity(total_nodes)),
        }
    }

    /// Add a file node
    pub fn add_file(&mut self, path: &str, _loc: u32, _language: Option<&str>) -> usize {
        let node = CompactNode::file(&self.interner, path);
        self.add_node_internal(node)
    }

    /// Add a function node
    pub fn add_function(
        &mut self,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        is_async: bool,
        complexity: u16,
    ) -> usize {
        let node = CompactNode::function(
            &self.interner,
            name,
            qualified_name,
            file_path,
            line_start,
            line_end,
            is_async,
            complexity,
        );
        self.add_node_internal(node)
    }

    /// Add a class node
    pub fn add_class(
        &mut self,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        method_count: u16,
    ) -> usize {
        let node = CompactNode::class(
            &self.interner,
            name,
            qualified_name,
            file_path,
            line_start,
            line_end,
            method_count,
        );
        self.add_node_internal(node)
    }

    fn add_node_internal(&mut self, node: CompactNode) -> usize {
        let idx = self.nodes.len();
        self.qn_to_index.insert(node.qualified_name, idx);
        self.nodes.push(node);

        // Add to petgraph
        let mut graph = self.graph.write().unwrap();
        let pg_idx = graph.add_node(idx);
        self.petgraph_to_idx.write().unwrap().insert(pg_idx, idx);
        self.idx_to_petgraph.write().unwrap().insert(idx, pg_idx);

        idx
    }

    /// Add a contains edge (file contains function/class)
    pub fn add_contains(&mut self, container: &str, contained: &str) {
        let edge = CompactEdge::contains(&self.interner, container, contained);
        self.add_edge_internal(edge);
    }

    /// Add a calls edge
    pub fn add_call(&mut self, caller: &str, callee: &str) {
        let edge = CompactEdge::calls(&self.interner, caller, callee);
        self.add_edge_internal(edge);
    }

    /// Add an imports edge
    pub fn add_import(&mut self, importer: &str, imported: &str, is_type_only: bool) {
        let edge = CompactEdge::imports(&self.interner, importer, imported, is_type_only);
        self.add_edge_internal(edge);
    }

    fn add_edge_internal(&mut self, edge: CompactEdge) {
        // Add to petgraph for algorithms — O(1) lookup via reverse index (#11)
        if let (Some(&src_idx), Some(&dst_idx)) = (
            self.qn_to_index.get(&edge.source),
            self.qn_to_index.get(&edge.target),
        ) {
            let mut graph = self.graph.write().unwrap();
            let idx_to_pg = self.idx_to_petgraph.read().unwrap();

            if let (Some(&src_pg), Some(&dst_pg)) =
                (idx_to_pg.get(&src_idx), idx_to_pg.get(&dst_idx))
            {
                graph.add_edge(src_pg, dst_pg, edge.kind);
            }
        }

        self.edges.push(edge);
    }

    /// Batch add nodes from CodeNode (compatibility with existing code)
    pub fn add_nodes_batch(&mut self, nodes: Vec<CodeNode>) {
        for node in nodes {
            match node.kind {
                NodeKind::File => {
                    let loc = node.get_i64("loc").unwrap_or(0) as u32;
                    self.add_file(&node.qualified_name, loc, node.language.as_deref());
                }
                NodeKind::Function => {
                    let is_async = node
                        .properties
                        .get("is_async")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false);
                    let complexity = node.get_i64("complexity").unwrap_or(1) as u16;
                    self.add_function(
                        &node.name,
                        &node.qualified_name,
                        &node.file_path,
                        node.line_start,
                        node.line_end,
                        is_async,
                        complexity,
                    );
                }
                NodeKind::Class => {
                    let method_count = node.get_i64("methodCount").unwrap_or(0) as u16;
                    self.add_class(
                        &node.name,
                        &node.qualified_name,
                        &node.file_path,
                        node.line_start,
                        node.line_end,
                        method_count,
                    );
                }
                _ => {}
            }
        }
    }

    /// Batch add edges (compatibility)
    pub fn add_edges_batch(&mut self, edges: Vec<(String, String, CodeEdge)>) {
        for (src, dst, edge) in edges {
            match edge.kind {
                EdgeKind::Contains => self.add_contains(&src, &dst),
                EdgeKind::Calls => self.add_call(&src, &dst),
                EdgeKind::Imports => {
                    let is_type = edge
                        .properties
                        .get("is_type_only")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false);
                    self.add_import(&src, &dst, is_type);
                }
                EdgeKind::Inherits => {}   // Not tracked in compact store
                EdgeKind::Uses => {}       // Not tracked in compact store
                EdgeKind::ModifiedIn => {} // Not tracked in compact store
            }
        }
    }

    /// Get node count
    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    /// Get edge count
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    /// Get unique string count (shows deduplication)
    pub fn unique_string_count(&self) -> usize {
        self.interner.len()
    }

    /// Estimate memory usage
    pub fn memory_usage(&self) -> MemoryStats {
        let node_bytes = self.nodes.len() * std::mem::size_of::<CompactNode>();
        let edge_bytes = self.edges.len() * std::mem::size_of::<CompactEdge>();
        let interner_bytes = self.interner.memory_usage();
        let index_bytes =
            self.qn_to_index.len() * (std::mem::size_of::<StrKey>() + std::mem::size_of::<usize>());

        MemoryStats {
            nodes: node_bytes,
            edges: edge_bytes,
            strings: interner_bytes,
            indices: index_bytes,
            total: node_bytes + edge_bytes + interner_bytes + index_bytes,
        }
    }

    // === Query methods for detector compatibility ===

    /// Get all functions
    pub fn get_functions(&self) -> Vec<CodeNode> {
        self.nodes
            .iter()
            .filter(|n| n.kind == CompactNodeKind::Function)
            .map(|n| self.expand_node(n))
            .collect()
    }

    /// Get all classes
    pub fn get_classes(&self) -> Vec<CodeNode> {
        self.nodes
            .iter()
            .filter(|n| n.kind == CompactNodeKind::Class)
            .map(|n| self.expand_node(n))
            .collect()
    }

    /// Get all files
    pub fn get_files(&self) -> Vec<CodeNode> {
        self.nodes
            .iter()
            .filter(|n| n.kind == CompactNodeKind::File)
            .map(|n| self.expand_node(n))
            .collect()
    }

    /// Get functions in a specific file
    pub fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        let path_key = match self.interner.get(file_path) {
            Some(k) => k,
            None => return Vec::new(),
        };

        self.nodes
            .iter()
            .filter(|n| n.kind == CompactNodeKind::Function && n.file_path == path_key)
            .map(|n| self.expand_node(n))
            .collect()
    }

    /// Get all call edges
    pub fn get_calls(&self) -> Vec<(String, String)> {
        self.edges
            .iter()
            .filter(|e| e.kind == CompactEdgeKind::Calls)
            .map(|e| {
                (
                    self.interner.resolve(e.source).to_string(),
                    self.interner.resolve(e.target).to_string(),
                )
            })
            .collect()
    }

    /// Get all import edges  
    pub fn get_imports(&self) -> Vec<(String, String)> {
        self.edges
            .iter()
            .filter(|e| e.kind == CompactEdgeKind::Imports)
            .map(|e| {
                (
                    self.interner.resolve(e.source).to_string(),
                    self.interner.resolve(e.target).to_string(),
                )
            })
            .collect()
    }

    /// Get node by qualified name
    pub fn get_node(&self, qn: &str) -> Option<CodeNode> {
        let key = self.interner.get(qn)?;
        let idx = self.qn_to_index.get(&key)?;
        Some(self.expand_node(&self.nodes[*idx]))
    }

    /// Get functions that call this function
    pub fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        let target_key = match self.interner.get(qn) {
            Some(k) => k,
            None => return Vec::new(),
        };

        self.edges
            .iter()
            .filter(|e| e.kind == CompactEdgeKind::Calls && e.target == target_key)
            .filter_map(|e| {
                let idx = self.qn_to_index.get(&e.source)?;
                Some(self.expand_node(&self.nodes[*idx]))
            })
            .collect()
    }

    /// Get functions this function calls
    pub fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        let source_key = match self.interner.get(qn) {
            Some(k) => k,
            None => return Vec::new(),
        };

        self.edges
            .iter()
            .filter(|e| e.kind == CompactEdgeKind::Calls && e.source == source_key)
            .filter_map(|e| {
                let idx = self.qn_to_index.get(&e.target)?;
                Some(self.expand_node(&self.nodes[*idx]))
            })
            .collect()
    }

    /// Count of callers (fan-in)
    pub fn call_fan_in(&self, qn: &str) -> usize {
        let target_key = match self.interner.get(qn) {
            Some(k) => k,
            None => return 0,
        };

        self.edges
            .iter()
            .filter(|e| e.kind == CompactEdgeKind::Calls && e.target == target_key)
            .count()
    }

    /// Count of callees (fan-out)
    pub fn call_fan_out(&self, qn: &str) -> usize {
        let source_key = match self.interner.get(qn) {
            Some(k) => k,
            None => return 0,
        };

        self.edges
            .iter()
            .filter(|e| e.kind == CompactEdgeKind::Calls && e.source == source_key)
            .count()
    }

    /// Get inheritance edges
    pub fn get_inheritance(&self) -> Vec<(String, String)> {
        self.edges
            .iter()
            .filter(|e| e.kind == CompactEdgeKind::Inherits)
            .map(|e| {
                (
                    self.interner.resolve(e.source).to_string(),
                    self.interner.resolve(e.target).to_string(),
                )
            })
            .collect()
    }

    /// Get child classes
    pub fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        let parent_key = match self.interner.get(qn) {
            Some(k) => k,
            None => return Vec::new(),
        };

        self.edges
            .iter()
            .filter(|e| e.kind == CompactEdgeKind::Inherits && e.target == parent_key)
            .filter_map(|e| {
                let idx = self.qn_to_index.get(&e.source)?;
                Some(self.expand_node(&self.nodes[*idx]))
            })
            .collect()
    }

    /// Get files that import this file
    pub fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        let target_key = match self.interner.get(qn) {
            Some(k) => k,
            None => return Vec::new(),
        };

        self.edges
            .iter()
            .filter(|e| e.kind == CompactEdgeKind::Imports && e.target == target_key)
            .filter_map(|e| {
                let idx = self.qn_to_index.get(&e.source)?;
                Some(self.expand_node(&self.nodes[*idx]))
            })
            .collect()
    }

    /// Find import cycles (simplified)
    pub fn find_import_cycles(&self) -> Vec<Vec<String>> {
        // Use petgraph's SCC for cycle detection, but only on runtime import edges.
        // (#43) Previously this ran SCC on the full graph (calls/contains/inherits too),
        // which could report non-import cycles as import cycles.
        use petgraph::algo::tarjan_scc;

        let mut filtered_graph: DiGraph<usize, ()> = DiGraph::new();
        let mut idx_to_filtered: HashMap<usize, NodeIndex> = HashMap::new();

        // Build a subgraph containing only non-type-only import edges.
        for edge in self
            .edges
            .iter()
            .filter(|e| e.kind == CompactEdgeKind::Imports && !e.is_type_only())
        {
            let (Some(&src_idx), Some(&dst_idx)) = (
                self.qn_to_index.get(&edge.source),
                self.qn_to_index.get(&edge.target),
            ) else {
                continue;
            };

            let src_pg = *idx_to_filtered
                .entry(src_idx)
                .or_insert_with(|| filtered_graph.add_node(src_idx));
            let dst_pg = *idx_to_filtered
                .entry(dst_idx)
                .or_insert_with(|| filtered_graph.add_node(dst_idx));

            filtered_graph.add_edge(src_pg, dst_pg, ());
        }

        let sccs = tarjan_scc(&filtered_graph);

        let mut cycles: Vec<Vec<String>> = sccs
            .into_iter()
            .filter(|scc| scc.len() > 1)
            .map(|scc| {
                let mut names: Vec<String> = scc
                    .into_iter()
                    .filter_map(|pg_idx| {
                        let idx = filtered_graph.node_weight(pg_idx)?;
                        let node = &self.nodes[*idx];
                        Some(self.interner.resolve(node.qualified_name).to_string())
                    })
                    .collect();
                names.sort();
                names
            })
            .collect();

        cycles.sort();
        cycles.dedup();
        cycles.sort_by_key(|c| std::cmp::Reverse(c.len()));
        cycles
    }

    /// Stats for compatibility
    pub fn stats(&self) -> HashMap<String, i64> {
        let mut stats = HashMap::new();
        stats.insert(
            "files".to_string(),
            self.nodes
                .iter()
                .filter(|n| n.kind == CompactNodeKind::File)
                .count() as i64,
        );
        stats.insert(
            "functions".to_string(),
            self.nodes
                .iter()
                .filter(|n| n.kind == CompactNodeKind::Function)
                .count() as i64,
        );
        stats.insert(
            "classes".to_string(),
            self.nodes
                .iter()
                .filter(|n| n.kind == CompactNodeKind::Class)
                .count() as i64,
        );
        stats.insert(
            "calls".to_string(),
            self.edges
                .iter()
                .filter(|e| e.kind == CompactEdgeKind::Calls)
                .count() as i64,
        );
        stats.insert(
            "imports".to_string(),
            self.edges
                .iter()
                .filter(|e| e.kind == CompactEdgeKind::Imports)
                .count() as i64,
        );
        // Required by UnifiedGraph::memory_info() (#63)
        stats.insert("total_nodes".to_string(), self.nodes.len() as i64);
        stats.insert("total_edges".to_string(), self.edges.len() as i64);
        stats
    }

    /// Expand a compact node to full CodeNode (for compatibility)
    fn expand_node(&self, n: &CompactNode) -> CodeNode {
        let kind = match n.kind {
            CompactNodeKind::File => NodeKind::File,
            CompactNodeKind::Function => NodeKind::Function,
            CompactNodeKind::Class => NodeKind::Class,
            CompactNodeKind::Module => NodeKind::Module,
        };

        let mut node = CodeNode {
            kind,
            name: self.interner.resolve(n.name).to_string(),
            qualified_name: self.interner.resolve(n.qualified_name).to_string(),
            file_path: self.interner.resolve(n.file_path).to_string(),
            line_start: n.line_start,
            line_end: n.line_end,
            language: None,
            properties: HashMap::new(),
        };

        // Add properties based on type
        match n.kind {
            CompactNodeKind::Function => {
                node.properties
                    .insert("is_async".to_string(), n.is_async().into());
                node.properties
                    .insert("complexity".to_string(), (n.complexity() as i64).into());
                node.properties
                    .insert("loc".to_string(), (n.loc() as i64).into());
            }
            CompactNodeKind::Class => {
                node.properties
                    .insert("methodCount".to_string(), (n.method_count() as i64).into());
            }
            _ => {}
        }

        node
    }

    /// Save to disk (placeholder)
    pub fn save(&self) -> Result<()> {
        // CompactGraphStore is ephemeral — built fresh each analysis run from source files.
        // Graph persistence is handled by the incremental cache (findings + scores),
        // not by serializing the full graph. This is intentionally a no-op. (#42)
        //
        // If graph persistence becomes needed (e.g. for incremental re-analysis),
        // implement serde for CompactNode/CompactEdge + the StringInterner.
        Ok(())
    }
}

impl Default for CompactGraphStore {
    fn default() -> Self {
        Self::new()
    }
}

// Implement the GraphQuery trait for detector compatibility
impl super::traits::GraphQuery for CompactGraphStore {
    fn get_functions(&self) -> Vec<CodeNode> {
        self.get_functions()
    }

    fn get_classes(&self) -> Vec<CodeNode> {
        self.get_classes()
    }

    fn get_files(&self) -> Vec<CodeNode> {
        self.get_files()
    }

    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.get_functions_in_file(file_path)
    }

    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        // Similar to get_functions_in_file
        let path_key = match self.interner.get(file_path) {
            Some(k) => k,
            None => return Vec::new(),
        };

        self.nodes
            .iter()
            .filter(|n| n.kind == CompactNodeKind::Class && n.file_path == path_key)
            .map(|n| self.expand_node(n))
            .collect()
    }

    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        self.get_node(qn)
    }

    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        self.get_callers(qn)
    }

    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        self.get_callees(qn)
    }

    fn call_fan_in(&self, qn: &str) -> usize {
        self.call_fan_in(qn)
    }

    fn call_fan_out(&self, qn: &str) -> usize {
        self.call_fan_out(qn)
    }

    fn get_calls(&self) -> Vec<(String, String)> {
        self.get_calls()
    }

    fn get_imports(&self) -> Vec<(String, String)> {
        self.get_imports()
    }

    fn get_inheritance(&self) -> Vec<(String, String)> {
        self.get_inheritance()
    }

    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        self.get_child_classes(qn)
    }

    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        self.get_importers(qn)
    }

    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        self.find_import_cycles()
    }

    fn stats(&self) -> HashMap<String, i64> {
        self.stats()
    }
}

/// Memory usage statistics
#[derive(Debug, Clone)]
pub struct MemoryStats {
    pub nodes: usize,
    pub edges: usize,
    pub strings: usize,
    pub indices: usize,
    pub total: usize,
}

impl MemoryStats {
    pub fn human_readable(&self) -> String {
        format!(
            "{:.1}MB total (nodes: {:.1}MB, edges: {:.1}MB, strings: {:.1}MB, idx: {:.1}MB)",
            self.total as f64 / 1024.0 / 1024.0,
            self.nodes as f64 / 1024.0 / 1024.0,
            self.edges as f64 / 1024.0 / 1024.0,
            self.strings as f64 / 1024.0 / 1024.0,
            self.indices as f64 / 1024.0 / 1024.0,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_usage() {
        let mut store = CompactGraphStore::new();

        store.add_file("src/main.rs", 100, Some("Rust"));
        store.add_function("main", "src/main.rs::main", "src/main.rs", 1, 10, false, 5);
        store.add_function(
            "helper",
            "src/main.rs::helper",
            "src/main.rs",
            12,
            20,
            true,
            3,
        );
        store.add_contains("src/main.rs", "src/main.rs::main");
        store.add_contains("src/main.rs", "src/main.rs::helper");
        store.add_call("src/main.rs::main", "src/main.rs::helper");

        assert_eq!(store.node_count(), 3);
        assert_eq!(store.edge_count(), 3);
        // "src/main.rs" should be interned once, not 3 times
        assert!(store.unique_string_count() < 6);
    }

    #[test]
    fn test_memory_savings() {
        let mut store = CompactGraphStore::new();

        // Simulate adding 1000 functions in 100 files
        for file_idx in 0..100 {
            let file_path = format!("src/module{}/file.rs", file_idx);
            store.add_file(&file_path, 500, Some("Rust"));

            for fn_idx in 0..10 {
                let fn_name = format!("func_{}", fn_idx);
                let qn = format!("{}::{}", file_path, fn_name);
                store.add_function(&fn_name, &qn, &file_path, 1, 10, false, 5);
                store.add_contains(&file_path, &qn);
            }
        }

        let stats = store.memory_usage();
        println!("Memory: {}", stats.human_readable());

        // With 1100 nodes, should be well under 1MB
        assert!(stats.total < 1024 * 1024);
    }

    #[test]
    fn test_find_import_cycles_ignores_non_import_edges() {
        let mut store = CompactGraphStore::new();

        // Import-cycle candidates
        store.add_file("a.py", 10, Some("Python"));
        store.add_file("b.py", 10, Some("Python"));

        // Functions with call cycle (should NOT be reported by import-cycle API)
        store.add_function("f", "a.py::f", "a.py", 1, 2, false, 1);
        store.add_function("g", "b.py::g", "b.py", 1, 2, false, 1);
        store.add_call("a.py::f", "b.py::g");
        store.add_call("b.py::g", "a.py::f");

        // Real import cycle
        store.add_import("a.py", "b.py", false);
        store.add_import("b.py", "a.py", false);

        let cycles = store.find_import_cycles();
        assert_eq!(cycles.len(), 1);
        let cycle = &cycles[0];
        assert!(cycle.contains(&"a.py".to_string()));
        assert!(cycle.contains(&"b.py".to_string()));
        assert!(!cycle.iter().any(|n| n.contains("::"))); // no function nodes
    }
}
