//! GraphQuery trait implementation for CodeGraph.
//!
//! Delegates the core trait methods to CodeGraph's native indexed methods.
//! String-based convenience methods (get_functions, get_callers, etc.) are
//! provided automatically by the `GraphQueryExt` blanket impl.

use crate::graph::node_index::NodeIndex;

use super::frozen::CodeGraph;
use super::interner::StrKey;
use super::store_models::{CodeNode, ExtraProps};

// ==================== GraphQuery for CodeGraph ====================

impl super::traits::GraphQuery for CodeGraph {
    fn primitives(&self) -> &crate::graph::primitives::GraphPrimitives {
        CodeGraph::primitives(self)
    }

    fn interner(&self) -> &super::interner::StringInterner {
        self.interner()
    }

    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps> {
        CodeGraph::extra_props(self, qn).cloned()
    }

    fn stats(&self) -> std::collections::BTreeMap<String, i64> {
        CodeGraph::stats(self)
    }

    // ==================== NodeIndex-based overrides ====================

    fn node_idx(&self, idx: NodeIndex) -> Option<&CodeNode> {
        self.node(idx)
    }

    fn node_by_name_idx(&self, qn: &str) -> Option<(NodeIndex, &CodeNode)> {
        self.node_by_name(qn)
    }

    fn functions_idx(&self) -> &[NodeIndex] {
        self.functions()
    }

    fn classes_idx(&self) -> &[NodeIndex] {
        self.classes()
    }

    fn files_idx(&self) -> &[NodeIndex] {
        self.files()
    }

    fn callers_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.callers(idx)
    }

    fn callees_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.callees(idx)
    }

    fn importers_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.importers(idx)
    }

    fn importees_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.importees(idx)
    }

    fn parent_classes_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.parent_classes(idx)
    }

    fn child_classes_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        self.child_classes(idx)
    }

    fn call_fan_in_idx(&self, idx: NodeIndex) -> usize {
        CodeGraph::call_fan_in(self, idx)
    }

    fn call_fan_out_idx(&self, idx: NodeIndex) -> usize {
        CodeGraph::call_fan_out(self, idx)
    }

    fn functions_in_file_idx(&self, file_path: &str) -> &[NodeIndex] {
        self.functions_in_file(file_path)
    }

    fn classes_in_file_idx(&self, file_path: &str) -> &[NodeIndex] {
        self.classes_in_file(file_path)
    }

    fn function_at_idx(&self, file_path: &str, line: u32) -> Option<NodeIndex> {
        self.function_at(file_path, line)
    }

    fn all_call_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        CodeGraph::all_call_edges(self)
    }

    fn all_import_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        CodeGraph::all_import_edges(self)
    }

    fn all_inheritance_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        CodeGraph::all_inheritance_edges(self)
    }

    fn import_cycles_idx(&self) -> &[Vec<NodeIndex>] {
        self.import_cycles()
    }

    fn edge_fingerprint_idx(&self) -> u64 {
        self.edge_fingerprint()
    }

    fn extra_props_ref(&self, qn: StrKey) -> Option<&ExtraProps> {
        CodeGraph::extra_props(self, qn)
    }
}

// ==================== GraphQuery for Arc<CodeGraph> ====================

impl super::traits::GraphQuery for std::sync::Arc<CodeGraph> {
    fn primitives(&self) -> &crate::graph::primitives::GraphPrimitives {
        <CodeGraph as super::traits::GraphQuery>::primitives(self)
    }

    fn interner(&self) -> &super::interner::StringInterner {
        (**self).interner()
    }

    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps> {
        <CodeGraph as super::traits::GraphQuery>::extra_props(self, qn)
    }

    fn stats(&self) -> std::collections::BTreeMap<String, i64> {
        <CodeGraph as super::traits::GraphQuery>::stats(self)
    }

    // ---- _idx methods delegated to inner CodeGraph ----

    fn node_idx(&self, idx: NodeIndex) -> Option<&CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::node_idx(self, idx)
    }

    fn node_by_name_idx(&self, qn: &str) -> Option<(NodeIndex, &CodeNode)> {
        <CodeGraph as super::traits::GraphQuery>::node_by_name_idx(self, qn)
    }

    fn functions_idx(&self) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::functions_idx(self)
    }

    fn classes_idx(&self) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::classes_idx(self)
    }

    fn files_idx(&self) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::files_idx(self)
    }

    fn callers_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::callers_idx(self, idx)
    }

    fn callees_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::callees_idx(self, idx)
    }

    fn importers_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::importers_idx(self, idx)
    }

    fn importees_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::importees_idx(self, idx)
    }

    fn parent_classes_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::parent_classes_idx(self, idx)
    }

    fn child_classes_idx(&self, idx: NodeIndex) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::child_classes_idx(self, idx)
    }

    fn call_fan_in_idx(&self, idx: NodeIndex) -> usize {
        <CodeGraph as super::traits::GraphQuery>::call_fan_in_idx(self, idx)
    }

    fn call_fan_out_idx(&self, idx: NodeIndex) -> usize {
        <CodeGraph as super::traits::GraphQuery>::call_fan_out_idx(self, idx)
    }

    fn functions_in_file_idx(&self, file_path: &str) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::functions_in_file_idx(self, file_path)
    }

    fn classes_in_file_idx(&self, file_path: &str) -> &[NodeIndex] {
        <CodeGraph as super::traits::GraphQuery>::classes_in_file_idx(self, file_path)
    }

    fn function_at_idx(&self, file_path: &str, line: u32) -> Option<NodeIndex> {
        <CodeGraph as super::traits::GraphQuery>::function_at_idx(self, file_path, line)
    }

    fn all_call_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        <CodeGraph as super::traits::GraphQuery>::all_call_edges(self)
    }

    fn all_import_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        <CodeGraph as super::traits::GraphQuery>::all_import_edges(self)
    }

    fn all_inheritance_edges(&self) -> &[(NodeIndex, NodeIndex)] {
        <CodeGraph as super::traits::GraphQuery>::all_inheritance_edges(self)
    }

    fn import_cycles_idx(&self) -> &[Vec<NodeIndex>] {
        <CodeGraph as super::traits::GraphQuery>::import_cycles_idx(self)
    }

    fn edge_fingerprint_idx(&self) -> u64 {
        <CodeGraph as super::traits::GraphQuery>::edge_fingerprint_idx(self)
    }

    fn extra_props_ref(&self, qn: StrKey) -> Option<&ExtraProps> {
        <CodeGraph as super::traits::GraphQuery>::extra_props_ref(self, qn)
    }
}

// ==================== GraphQuery for GraphBuilder ====================
//
// Enables using GraphBuilder directly as `&dyn GraphQuery` in test code.
// Lazily builds a frozen CodeGraph snapshot on first trait method call.
// NOT invalidated by subsequent mutations — intended for test code.

use super::builder::GraphBuilder;

impl super::traits::GraphQuery for GraphBuilder {
    fn primitives(&self) -> &crate::graph::primitives::GraphPrimitives {
        self.snapshot().primitives()
    }

    fn interner(&self) -> &super::interner::StringInterner {
        super::interner::global_interner()
    }

    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps> {
        self.snapshot().extra_props(qn).cloned()
    }

    fn stats(&self) -> std::collections::BTreeMap<String, i64> {
        self.snapshot().stats()
    }

    fn node_idx(&self, idx: crate::graph::node_index::NodeIndex) -> Option<&CodeNode> {
        self.snapshot().node(idx)
    }

    fn node_by_name_idx(&self, qn: &str) -> Option<(crate::graph::node_index::NodeIndex, &CodeNode)> {
        self.snapshot().node_by_name(qn)
    }

    fn functions_idx(&self) -> &[crate::graph::node_index::NodeIndex] {
        self.snapshot().functions()
    }

    fn classes_idx(&self) -> &[crate::graph::node_index::NodeIndex] {
        self.snapshot().classes()
    }

    fn files_idx(&self) -> &[crate::graph::node_index::NodeIndex] {
        self.snapshot().files()
    }

    fn callers_idx(
        &self,
        idx: crate::graph::node_index::NodeIndex,
    ) -> &[crate::graph::node_index::NodeIndex] {
        self.snapshot().callers(idx)
    }

    fn callees_idx(
        &self,
        idx: crate::graph::node_index::NodeIndex,
    ) -> &[crate::graph::node_index::NodeIndex] {
        self.snapshot().callees(idx)
    }

    fn importers_idx(
        &self,
        idx: crate::graph::node_index::NodeIndex,
    ) -> &[crate::graph::node_index::NodeIndex] {
        self.snapshot().importers(idx)
    }

    fn importees_idx(
        &self,
        idx: crate::graph::node_index::NodeIndex,
    ) -> &[crate::graph::node_index::NodeIndex] {
        self.snapshot().importees(idx)
    }

    fn parent_classes_idx(
        &self,
        idx: crate::graph::node_index::NodeIndex,
    ) -> &[crate::graph::node_index::NodeIndex] {
        self.snapshot().parent_classes(idx)
    }

    fn child_classes_idx(
        &self,
        idx: crate::graph::node_index::NodeIndex,
    ) -> &[crate::graph::node_index::NodeIndex] {
        self.snapshot().child_classes(idx)
    }

    fn call_fan_in_idx(&self, idx: crate::graph::node_index::NodeIndex) -> usize {
        CodeGraph::call_fan_in(self.snapshot(), idx)
    }

    fn call_fan_out_idx(&self, idx: crate::graph::node_index::NodeIndex) -> usize {
        CodeGraph::call_fan_out(self.snapshot(), idx)
    }

    fn functions_in_file_idx(&self, file_path: &str) -> &[crate::graph::node_index::NodeIndex] {
        self.snapshot().functions_in_file(file_path)
    }

    fn classes_in_file_idx(&self, file_path: &str) -> &[crate::graph::node_index::NodeIndex] {
        self.snapshot().classes_in_file(file_path)
    }

    fn function_at_idx(
        &self,
        file_path: &str,
        line: u32,
    ) -> Option<crate::graph::node_index::NodeIndex> {
        self.snapshot().function_at(file_path, line)
    }

    fn all_call_edges(
        &self,
    ) -> &[(
        crate::graph::node_index::NodeIndex,
        crate::graph::node_index::NodeIndex,
    )] {
        CodeGraph::all_call_edges(self.snapshot())
    }

    fn all_import_edges(
        &self,
    ) -> &[(
        crate::graph::node_index::NodeIndex,
        crate::graph::node_index::NodeIndex,
    )] {
        CodeGraph::all_import_edges(self.snapshot())
    }

    fn all_inheritance_edges(
        &self,
    ) -> &[(
        crate::graph::node_index::NodeIndex,
        crate::graph::node_index::NodeIndex,
    )] {
        CodeGraph::all_inheritance_edges(self.snapshot())
    }

    fn import_cycles_idx(&self) -> &[Vec<crate::graph::node_index::NodeIndex>] {
        self.snapshot().import_cycles()
    }

    fn edge_fingerprint_idx(&self) -> u64 {
        self.snapshot().edge_fingerprint()
    }

    fn extra_props_ref(&self, qn: StrKey) -> Option<&ExtraProps> {
        CodeGraph::extra_props(self.snapshot(), qn)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::store_models::{CodeEdge, NodeKind};
    use crate::graph::traits::{GraphQuery, GraphQueryExt};

    #[test]
    fn test_get_functions_via_trait() {
        let mut builder = GraphBuilder::new();
        builder.add_node(CodeNode::function("foo", "a.py"));
        builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_node(CodeNode::class("MyClass", "a.py"));

        let graph = builder.freeze();
        let funcs = graph.get_functions();
        assert_eq!(funcs.len(), 2);
        assert!(funcs.iter().all(|f| f.kind == NodeKind::Function));
    }

    #[test]
    fn test_get_callers_via_trait() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        let callers = graph.get_callers("a.py::bar");
        assert_eq!(callers.len(), 1);

        let si = graph.interner();
        assert_eq!(si.resolve(callers[0].qualified_name), "a.py::foo");
    }

    #[test]
    fn test_find_import_cycles_via_trait() {
        let mut builder = GraphBuilder::new();
        let a = builder.add_node(CodeNode::file("a.py"));
        let b = builder.add_node(CodeNode::file("b.py"));
        builder.add_edge(a, b, CodeEdge::imports());
        builder.add_edge(b, a, CodeEdge::imports());

        let graph = builder.freeze();
        let cycles = graph.find_import_cycles();
        assert_eq!(cycles.len(), 1);
        assert_eq!(cycles[0].len(), 2);
    }

    #[test]
    fn test_build_call_maps_raw_via_trait() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        let (qn_to_idx, callers, callees) = graph.build_call_maps_raw();

        assert_eq!(qn_to_idx.len(), 2);
        assert!(!callees.is_empty());
        assert!(!callers.is_empty());
    }

    #[test]
    fn test_get_calls_via_trait() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        let calls = graph.get_calls();
        assert_eq!(calls.len(), 1);
    }

    #[test]
    fn test_idx_methods_via_trait() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(CodeNode::function("foo", "a.py"));
        let f2 = builder.add_node(CodeNode::function("bar", "a.py"));
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        let trait_ref: &dyn GraphQuery = &graph;

        assert_eq!(trait_ref.functions_idx().len(), 2);
        assert_eq!(trait_ref.all_call_edges().len(), 1);

        let (idx, node) = trait_ref.node_by_name_idx("a.py::foo").unwrap();
        assert_eq!(trait_ref.callees_idx(idx).len(), 1);
        assert_eq!(node.kind, NodeKind::Function);
    }
}
