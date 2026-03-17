//! Persistence layer for CodeGraph — bincode save/load.
//!
//! Serializes the StableGraph, node_index, and extra_props to a bincode file.
//! Indexes are NOT serialized — they are rebuilt via `GraphIndexes::build()`
//! on load. This matches the existing `GraphStore` cache format for compatibility.

use anyhow::{Context, Result};
use petgraph::stable_graph::{NodeIndex, StableGraph};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;

use super::frozen::CodeGraph;
use super::indexes::GraphIndexes;
use super::interner::{global_interner, StrKey};
use super::store_models::{CodeEdge, CodeNode, ExtraProps};

/// Schema version for CodeGraph persistence.
/// Bump when the serialization format changes.
const CODEGRAPH_CACHE_VERSION: u32 = 1;

/// Serializable cache format for CodeGraph.
///
/// StrKey values are process-local (lasso::Spur), so we serialize a string_table
/// mapping raw Spur u32 → String, and re-intern on load.
#[derive(Serialize, Deserialize)]
struct CodeGraphCache {
    version: u32,
    binary_version: String,
    graph: StableGraph<CodeNode, CodeEdge>,
    /// Qualified-name → NodeIndex, with keys as strings (not StrKeys).
    node_index: HashMap<String, NodeIndex>,
    /// String table: raw Spur u32 → interned string for cross-process re-interning.
    string_table: HashMap<u32, String>,
    /// ExtraProps serialized with string values (StrKeys are process-local).
    extra_props: Vec<(String, SerializableExtraProps)>,
}

/// ExtraProps with string values for serialization (StrKeys are process-local).
#[derive(Serialize, Deserialize)]
struct SerializableExtraProps {
    params: Option<String>,
    doc_comment: Option<String>,
    decorators: Option<String>,
    author: Option<String>,
    last_modified: Option<String>,
}

impl CodeGraph {
    /// Save the code graph to a bincode cache file.
    ///
    /// Serializes the StableGraph + node_index + extra_props. Indexes are NOT
    /// serialized — they are rebuilt on load via `GraphIndexes::build()`.
    /// The file is written atomically via write-to-temp-then-rename.
    pub fn save_cache(&self, cache_path: &Path) -> Result<()> {
        let i = global_interner();

        // Build string table: raw Spur u32 → interned string for all StrKeys in nodes
        let mut string_table: HashMap<u32, String> = HashMap::new();
        for node in self.raw_graph().node_weights() {
            for &key in &[node.name, node.qualified_name, node.file_path, node.language] {
                let raw = key.into_inner().get();
                string_table
                    .entry(raw)
                    .or_insert_with(|| i.resolve(key).to_string());
            }
        }

        // Serialize node_index with string keys
        let node_index: HashMap<String, NodeIndex> = self
            .node_index_map()
            .iter()
            .map(|(&key, &idx)| (i.resolve(key).to_string(), idx))
            .collect();

        // Serialize ExtraProps with resolved strings
        let extra_props_ser: Vec<(String, SerializableExtraProps)> = self
            .node_index_map()
            .keys()
            .filter_map(|&qn_key| {
                let ep = self.extra_props(qn_key)?;
                let qn_str = i.resolve(qn_key).to_string();
                let ser = SerializableExtraProps {
                    params: ep.params.map(|k| i.resolve(k).to_string()),
                    doc_comment: ep.doc_comment.map(|k| i.resolve(k).to_string()),
                    decorators: ep.decorators.map(|k| i.resolve(k).to_string()),
                    author: ep.author.map(|k| i.resolve(k).to_string()),
                    last_modified: ep.last_modified.map(|k| i.resolve(k).to_string()),
                };
                Some((qn_str, ser))
            })
            .collect();

        let cache = CodeGraphCache {
            version: CODEGRAPH_CACHE_VERSION,
            binary_version: env!("CARGO_PKG_VERSION").to_string(),
            graph: self.raw_graph().clone(),
            node_index,
            string_table,
            extra_props: extra_props_ser,
        };

        let bytes = bincode::serialize(&cache).context("Failed to serialize CodeGraph cache")?;

        if let Some(parent) = cache_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        // Atomic write: write to .tmp then rename
        let tmp_path = cache_path.with_extension("bin.tmp");
        std::fs::write(&tmp_path, &bytes).context("Failed to write CodeGraph cache")?;
        std::fs::rename(&tmp_path, cache_path).context("Failed to finalize CodeGraph cache")?;

        Ok(())
    }

    /// Load a CodeGraph from a bincode cache file, rebuilding all indexes.
    ///
    /// Returns None if cache is missing, corrupt, or version-mismatched.
    pub fn load_cache(cache_path: &Path) -> Option<Self> {
        let bytes = std::fs::read(cache_path).ok()?;
        let cache: CodeGraphCache = bincode::deserialize(&bytes).ok()?;

        // Version check
        if cache.version != CODEGRAPH_CACHE_VERSION
            || cache.binary_version != env!("CARGO_PKG_VERSION")
        {
            tracing::info!("CodeGraph cache version mismatch, rebuilding");
            return None;
        }

        let i = global_interner();

        // Re-intern StrKeys from the string table: old raw u32 → new StrKey
        let remap: HashMap<u32, StrKey> = cache
            .string_table
            .iter()
            .map(|(&raw, s)| (raw, i.intern(s)))
            .collect();

        // Remap all CodeNode StrKey fields in the deserialized graph
        let mut graph = cache.graph;
        for idx in graph.node_indices().collect::<Vec<_>>() {
            if let Some(node) = graph.node_weight_mut(idx) {
                if let Some(&new) = remap.get(&node.name.into_inner().get()) {
                    node.name = new;
                }
                if let Some(&new) = remap.get(&node.qualified_name.into_inner().get()) {
                    node.qualified_name = new;
                }
                if let Some(&new) = remap.get(&node.file_path.into_inner().get()) {
                    node.file_path = new;
                }
                if let Some(&new) = remap.get(&node.language.into_inner().get()) {
                    node.language = new;
                }
            }
        }

        // Rebuild node_index HashMap (intern string keys back to StrKeys)
        let node_index: HashMap<StrKey, NodeIndex> = cache
            .node_index
            .iter()
            .map(|(key_str, &idx)| (i.intern(key_str), idx))
            .collect();

        // Rebuild ExtraProps from serialized string values
        let mut extra_props: HashMap<StrKey, ExtraProps> = HashMap::new();
        for (qn_str, ser) in cache.extra_props {
            let qn_key = i.intern(&qn_str);
            let ep = ExtraProps {
                params: ser.params.as_deref().map(|s| i.intern(s)),
                doc_comment: ser.doc_comment.as_deref().map(|s| i.intern(s)),
                decorators: ser.decorators.as_deref().map(|s| i.intern(s)),
                author: ser.author.as_deref().map(|s| i.intern(s)),
                last_modified: ser.last_modified.as_deref().map(|s| i.intern(s)),
            };
            extra_props.insert(qn_key, ep);
        }

        // Rebuild indexes from the graph
        let indexes = GraphIndexes::build(&graph, &node_index);

        tracing::info!("Loaded CodeGraph cache ({} nodes)", node_index.len());
        Some(CodeGraph::from_parts(graph, node_index, extra_props, indexes))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::store_models::NodeKind;

    #[test]
    fn test_save_load_roundtrip() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::file("a.py"));
        let f1 = builder.add_node(CodeNode::function("foo", "a.py").with_lines(1, 10));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py").with_lines(12, 20));
        builder.add_node(CodeNode::class("MyClass", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        // Set extra props
        let si = builder.interner();
        let qn_key = si.intern("a.py::foo");
        let mut ep = ExtraProps::default();
        ep.author = Some(si.intern("alice"));
        builder.set_extra_props(qn_key, ep);

        let graph = builder.freeze();

        let dir = tempfile::tempdir().unwrap();
        let cache_path = dir.path().join("graph.bin");

        // Save
        graph.save_cache(&cache_path).unwrap();
        assert!(cache_path.exists());

        // Load
        let loaded = CodeGraph::load_cache(&cache_path).unwrap();

        // Verify structure
        assert_eq!(loaded.node_count(), graph.node_count());
        assert_eq!(loaded.edge_count(), graph.edge_count());
        assert_eq!(loaded.functions().len(), 2);
        assert_eq!(loaded.classes().len(), 1);
        assert_eq!(loaded.files().len(), 1);

        // Verify node data survives roundtrip
        let si = loaded.interner();
        let (_, foo_node) = loaded.node_by_name("a.py::foo").unwrap();
        assert_eq!(foo_node.kind, NodeKind::Function);
        assert_eq!(foo_node.line_start, 1);
        assert_eq!(foo_node.line_end, 10);

        // Verify extra props survive roundtrip
        let foo_key = si.intern("a.py::foo");
        let ep = loaded.extra_props(foo_key).unwrap();
        assert_eq!(si.resolve(ep.author.unwrap()), "alice");

        // Verify adjacency indexes are rebuilt
        let (foo_idx, _) = loaded.node_by_name("a.py::foo").unwrap();
        let (bar_idx, _) = loaded.node_by_name("a.py::bar").unwrap();
        assert_eq!(loaded.callees(foo_idx).len(), 1);
        assert_eq!(loaded.callers(bar_idx).len(), 1);
    }

    #[test]
    fn test_load_missing_file() {
        let result = CodeGraph::load_cache(Path::new("/nonexistent/path/graph.bin"));
        assert!(result.is_none());
    }

    #[test]
    fn test_load_corrupt_file() {
        let dir = tempfile::tempdir().unwrap();
        let cache_path = dir.path().join("graph.bin");
        std::fs::write(&cache_path, b"not valid bincode").unwrap();

        let result = CodeGraph::load_cache(&cache_path);
        assert!(result.is_none());
    }
}
