use super::interner::{StrKey, StringInterner};
use super::store::GraphStore;
use super::store_models::{CodeNode, ExtraProps};
use petgraph::stable_graph::NodeIndex;
use std::collections::BTreeMap;

// ==================== impl GraphQuery for Arc<GraphStore> ====================

impl super::traits::GraphQuery for std::sync::Arc<GraphStore> {
    fn primitives(&self) -> &crate::graph::primitives::GraphPrimitives {
        static EMPTY: std::sync::LazyLock<crate::graph::primitives::GraphPrimitives> = std::sync::LazyLock::new(crate::graph::primitives::GraphPrimitives::default);
        &EMPTY
    }

    fn interner(&self) -> &StringInterner {
        (**self).interner()
    }

    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps> {
        self.get_extra_props(qn)
    }

    fn stats(&self) -> BTreeMap<String, i64> {
        (**self).stats()
    }

    // ---- _idx methods delegated to inner GraphStore ----

    fn node_idx(&self, idx: NodeIndex) -> Option<&CodeNode> {
        <GraphStore as super::traits::GraphQuery>::node_idx(self, idx)
    }

    fn node_by_name_idx(&self, qn: &str) -> Option<(NodeIndex, &CodeNode)> {
        <GraphStore as super::traits::GraphQuery>::node_by_name_idx(self, qn)
    }

    fn functions_idx(&self) -> &[NodeIndex] {
        <GraphStore as super::traits::GraphQuery>::functions_idx(self)
    }

    fn classes_idx(&self) -> &[NodeIndex] {
        <GraphStore as super::traits::GraphQuery>::classes_idx(self)
    }

    fn files_idx(&self) -> &[NodeIndex] {
        <GraphStore as super::traits::GraphQuery>::files_idx(self)
    }

    fn callers_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <GraphStore as super::traits::GraphQuery>::callers_idx(self, idx)
    }

    fn callees_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <GraphStore as super::traits::GraphQuery>::callees_idx(self, idx)
    }

    fn importers_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <GraphStore as super::traits::GraphQuery>::importers_idx(self, idx)
    }

    fn child_classes_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <GraphStore as super::traits::GraphQuery>::child_classes_idx(self, idx)
    }

    fn functions_in_file_idx(&self, file_path: &str) -> &[NodeIndex] {
        <GraphStore as super::traits::GraphQuery>::functions_in_file_idx(self, file_path)
    }

    fn classes_in_file_idx(&self, file_path: &str) -> &[NodeIndex] {
        <GraphStore as super::traits::GraphQuery>::classes_in_file_idx(self, file_path)
    }

    fn all_call_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        <GraphStore as super::traits::GraphQuery>::all_call_edges(self)
    }

    fn all_import_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        <GraphStore as super::traits::GraphQuery>::all_import_edges(self)
    }

    fn all_inheritance_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        <GraphStore as super::traits::GraphQuery>::all_inheritance_edges(self)
    }

    fn import_cycles_idx(&self) -> &[Vec<NodeIndex>] {
        <GraphStore as super::traits::GraphQuery>::import_cycles_idx(self)
    }
}

// ==================== impl GraphQuery for GraphStore ====================

impl super::traits::GraphQuery for GraphStore {
    fn primitives(&self) -> &crate::graph::primitives::GraphPrimitives {
        static EMPTY: std::sync::LazyLock<crate::graph::primitives::GraphPrimitives> = std::sync::LazyLock::new(crate::graph::primitives::GraphPrimitives::default);
        &EMPTY
    }

    fn interner(&self) -> &StringInterner {
        self.interner()
    }

    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps> {
        self.get_extra_props(qn)
    }

    fn stats(&self) -> BTreeMap<String, i64> {
        self.stats()
    }

    // ---- _idx methods backed by QuerySnapshot ----

    fn node_idx(&self, idx: NodeIndex) -> Option<&CodeNode> {
        let snap = self.snapshot();
        snap.nodes.get(idx.index())?.as_ref()
    }

    fn node_by_name_idx(&self, qn: &str) -> Option<(NodeIndex, &CodeNode)> {
        let snap = self.snapshot();
        let key = self.interner().intern(qn);
        let &idx = snap.name_to_idx.get(&key)?;
        let node = snap.nodes.get(idx.index())?.as_ref()?;
        Some((idx, node))
    }

    fn functions_idx(&self) -> &[NodeIndex] {
        &self.snapshot().function_idxs
    }

    fn classes_idx(&self) -> &[NodeIndex] {
        &self.snapshot().class_idxs
    }

    fn files_idx(&self) -> &[NodeIndex] {
        &self.snapshot().file_idxs
    }

    fn callers_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.snapshot().callers_map.get(&idx).map(|v| v.as_slice()).unwrap_or(&[])
    }

    fn callees_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.snapshot().callees_map.get(&idx).map(|v| v.as_slice()).unwrap_or(&[])
    }

    fn importers_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.snapshot().importers_map.get(&idx).map(|v| v.as_slice()).unwrap_or(&[])
    }

    fn child_classes_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.snapshot().child_classes_map.get(&idx).map(|v| v.as_slice()).unwrap_or(&[])
    }

    fn functions_in_file_idx(&self, file_path: &str) -> &[NodeIndex] {
        let key = self.interner().intern(file_path);
        self.snapshot().file_functions.get(&key).map(|v| v.as_slice()).unwrap_or(&[])
    }

    fn classes_in_file_idx(&self, file_path: &str) -> &[NodeIndex] {
        let key = self.interner().intern(file_path);
        self.snapshot().file_classes.get(&key).map(|v| v.as_slice()).unwrap_or(&[])
    }

    fn all_call_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        &self.snapshot().call_edges
    }

    fn all_import_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        &self.snapshot().import_edges
    }

    fn all_inheritance_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        &self.snapshot().inheritance_edges
    }

    fn import_cycles_idx(&self) -> &[Vec<NodeIndex>] {
        &self.snapshot().import_cycles
    }
}
