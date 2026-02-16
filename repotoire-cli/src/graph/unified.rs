//! Unified graph store that dispatches to either GraphStore or CompactGraphStore
//!
//! This allows analyze.rs to use either backend seamlessly.

use super::compact_store::CompactGraphStore;
use super::store::GraphStore;
use super::traits::GraphQuery;
use super::{CodeEdge, CodeNode};
use anyhow::Result;
use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

/// Unified graph store - dispatches to either regular or compact backend
pub enum UnifiedGraph {
    Standard(Arc<GraphStore>),
    Compact(CompactGraphStore),
}

impl UnifiedGraph {
    /// Create a standard graph store
    pub fn standard(path: &Path) -> Result<Self> {
        Ok(Self::Standard(Arc::new(GraphStore::new(path)?)))
    }
    
    /// Create a standard in-memory graph store  
    pub fn standard_in_memory() -> Self {
        Self::Standard(Arc::new(GraphStore::in_memory()))
    }
    
    /// Create a compact graph store (memory efficient)
    pub fn compact() -> Self {
        Self::Compact(CompactGraphStore::new())
    }
    
    /// Create a compact graph store with capacity hints
    pub fn compact_with_capacity(files: usize, functions: usize, classes: usize) -> Self {
        Self::Compact(CompactGraphStore::with_capacity(files, functions, classes))
    }
    
    /// Get as standard GraphStore (for legacy code that needs it)
    pub fn as_standard(&self) -> Option<&Arc<GraphStore>> {
        match self {
            Self::Standard(g) => Some(g),
            Self::Compact(_) => None,
        }
    }
    
    /// Get mutable compact store for building
    pub fn as_compact_mut(&mut self) -> Option<&mut CompactGraphStore> {
        match self {
            Self::Standard(_) => None,
            Self::Compact(g) => Some(g),
        }
    }
    
    /// Check if using compact mode
    pub fn is_compact(&self) -> bool {
        matches!(self, Self::Compact(_))
    }
    
    /// Add node (for graph building)
    pub fn add_node(&mut self, node: CodeNode) {
        match self {
            Self::Standard(g) => { g.add_node(node); }
            Self::Compact(g) => {
                // Convert to compact format
                match node.kind {
                    super::NodeKind::File => {
                        let loc = node.get_i64("loc").unwrap_or(0) as u32;
                        g.add_file(&node.qualified_name, loc, node.language.as_deref());
                    }
                    super::NodeKind::Function => {
                        let is_async = node.properties.get("is_async")
                            .and_then(|v| v.as_bool())
                            .unwrap_or(false);
                        let complexity = node.get_i64("complexity").unwrap_or(1) as u16;
                        g.add_function(
                            &node.name,
                            &node.qualified_name,
                            &node.file_path,
                            node.line_start,
                            node.line_end,
                            is_async,
                            complexity,
                        );
                    }
                    super::NodeKind::Class => {
                        let method_count = node.get_i64("methodCount").unwrap_or(0) as u16;
                        g.add_class(
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
    }
    
    /// Add edges batch
    pub fn add_edges_batch(&mut self, edges: Vec<(String, String, CodeEdge)>) {
        match self {
            Self::Standard(g) => { g.add_edges_batch(edges); }
            Self::Compact(g) => { g.add_edges_batch(edges); }
        }
    }
    
    /// Save graph
    pub fn save(&self) -> Result<()> {
        match self {
            Self::Standard(g) => g.save(),
            Self::Compact(g) => g.save(),
        }
    }
    
    /// Memory usage info
    pub fn memory_info(&self) -> String {
        match self {
            Self::Standard(g) => {
                let stats = g.stats();
                format!("{} nodes, {} edges", 
                    stats.get("functions").unwrap_or(&0) + stats.get("files").unwrap_or(&0) + stats.get("classes").unwrap_or(&0),
                    stats.get("calls").unwrap_or(&0) + stats.get("imports").unwrap_or(&0)
                )
            }
            Self::Compact(g) => {
                let mem = g.memory_usage();
                format!("{} nodes, {} edges, {} (interned: {} strings)",
                    g.node_count(),
                    g.edge_count(),
                    mem.human_readable(),
                    g.unique_string_count()
                )
            }
        }
    }
}

impl GraphQuery for UnifiedGraph {
    fn get_functions(&self) -> Vec<CodeNode> {
        match self {
            Self::Standard(g) => g.get_functions(),
            Self::Compact(g) => g.get_functions(),
        }
    }
    
    fn get_classes(&self) -> Vec<CodeNode> {
        match self {
            Self::Standard(g) => g.get_classes(),
            Self::Compact(g) => g.get_classes(),
        }
    }
    
    fn get_files(&self) -> Vec<CodeNode> {
        match self {
            Self::Standard(g) => g.get_files(),
            Self::Compact(g) => g.get_files(),
        }
    }
    
    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        match self {
            Self::Standard(g) => g.get_functions_in_file(file_path),
            Self::Compact(g) => g.get_functions_in_file(file_path),
        }
    }
    
    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        match self {
            Self::Standard(g) => g.get_classes_in_file(file_path),
            Self::Compact(g) => g.get_classes_in_file(file_path),
        }
    }
    
    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        match self {
            Self::Standard(g) => g.get_node(qn),
            Self::Compact(g) => g.get_node(qn),
        }
    }
    
    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        match self {
            Self::Standard(g) => g.get_callers(qn),
            Self::Compact(g) => g.get_callers(qn),
        }
    }
    
    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        match self {
            Self::Standard(g) => g.get_callees(qn),
            Self::Compact(g) => g.get_callees(qn),
        }
    }
    
    fn call_fan_in(&self, qn: &str) -> usize {
        match self {
            Self::Standard(g) => g.call_fan_in(qn),
            Self::Compact(g) => g.call_fan_in(qn),
        }
    }
    
    fn call_fan_out(&self, qn: &str) -> usize {
        match self {
            Self::Standard(g) => g.call_fan_out(qn),
            Self::Compact(g) => g.call_fan_out(qn),
        }
    }
    
    fn get_calls(&self) -> Vec<(String, String)> {
        match self {
            Self::Standard(g) => g.get_calls(),
            Self::Compact(g) => g.get_calls(),
        }
    }
    
    fn get_imports(&self) -> Vec<(String, String)> {
        match self {
            Self::Standard(g) => g.get_imports(),
            Self::Compact(g) => g.get_imports(),
        }
    }
    
    fn get_inheritance(&self) -> Vec<(String, String)> {
        match self {
            Self::Standard(g) => g.get_inheritance(),
            Self::Compact(g) => g.get_inheritance(),
        }
    }
    
    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        match self {
            Self::Standard(g) => g.get_child_classes(qn),
            Self::Compact(g) => g.get_child_classes(qn),
        }
    }
    
    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        match self {
            Self::Standard(g) => g.get_importers(qn),
            Self::Compact(g) => g.get_importers(qn),
        }
    }
    
    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        match self {
            Self::Standard(g) => g.find_import_cycles(),
            Self::Compact(g) => g.find_import_cycles(),
        }
    }
    
    fn find_call_cycles(&self) -> Vec<Vec<String>> {
        match self {
            Self::Standard(g) => g.find_call_cycles(),
            Self::Compact(g) => g.find_call_cycles(),
        }
    }
    
    fn stats(&self) -> HashMap<String, i64> {
        match self {
            Self::Standard(g) => g.stats(),
            Self::Compact(g) => g.stats(),
        }
    }
}

// UnifiedGraph is Send+Sync because:
// - Arc<GraphStore> is Send+Sync (Arc provides thread safety)
// - CompactGraphStore contains only plain data types (Vec, HashMap, String)
// No manual unsafe impl needed — the compiler derives these automatically.
