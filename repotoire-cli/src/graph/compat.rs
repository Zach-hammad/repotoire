//! GraphQuery trait implementation for CodeGraph.
//!
//! Delegates the string-based trait methods to CodeGraph's native indexed
//! methods. This enables `&CodeGraph` and `Arc<CodeGraph>` to be used as
//! `&dyn GraphQuery` by detectors and scoring stages.

use petgraph::stable_graph::NodeIndex;
use std::collections::HashMap;

use super::frozen::CodeGraph;
use super::interner::StrKey;
use super::store_models::{CodeNode, ExtraProps};

// ==================== GraphQuery for CodeGraph ====================

impl super::traits::GraphQuery for CodeGraph {
    fn interner(&self) -> &super::interner::StringInterner {
        self.interner()
    }

    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps> {
        CodeGraph::extra_props(self, qn).cloned()
    }

    fn get_functions(&self) -> Vec<CodeNode> {
        self.functions()
            .iter()
            .filter_map(|&idx| self.node(idx).copied())
            .collect()
    }

    fn get_classes(&self) -> Vec<CodeNode> {
        self.classes()
            .iter()
            .filter_map(|&idx| self.node(idx).copied())
            .collect()
    }

    fn get_files(&self) -> Vec<CodeNode> {
        self.files()
            .iter()
            .filter_map(|&idx| self.node(idx).copied())
            .collect()
    }

    fn get_functions_shared(&self) -> std::sync::Arc<[CodeNode]> {
        std::sync::Arc::from(
            self.functions()
                .iter()
                .filter_map(|&idx| self.node(idx).copied())
                .collect::<Vec<_>>(),
        )
    }

    fn get_classes_shared(&self) -> std::sync::Arc<[CodeNode]> {
        std::sync::Arc::from(
            self.classes()
                .iter()
                .filter_map(|&idx| self.node(idx).copied())
                .collect::<Vec<_>>(),
        )
    }

    fn get_files_shared(&self) -> std::sync::Arc<[CodeNode]> {
        std::sync::Arc::from(
            self.files()
                .iter()
                .filter_map(|&idx| self.node(idx).copied())
                .collect::<Vec<_>>(),
        )
    }

    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.functions_in_file(file_path)
            .iter()
            .filter_map(|&idx| self.node(idx).copied())
            .collect()
    }

    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        self.classes_in_file(file_path)
            .iter()
            .filter_map(|&idx| self.node(idx).copied())
            .collect()
    }

    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        self.node_by_name(qn).map(|(_, node)| *node)
    }

    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name(qn) else {
            return vec![];
        };
        self.callers(idx)
            .iter()
            .filter_map(|&ci| self.node(ci).copied())
            .collect()
    }

    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name(qn) else {
            return vec![];
        };
        self.callees(idx)
            .iter()
            .filter_map(|&ci| self.node(ci).copied())
            .collect()
    }

    fn call_fan_in(&self, qn: &str) -> usize {
        self.node_by_name(qn)
            .map(|(idx, _)| CodeGraph::call_fan_in(self, idx))
            .unwrap_or(0)
    }

    fn call_fan_out(&self, qn: &str) -> usize {
        self.node_by_name(qn)
            .map(|(idx, _)| CodeGraph::call_fan_out(self, idx))
            .unwrap_or(0)
    }

    fn get_calls(&self) -> Vec<(StrKey, StrKey)> {
        self.all_call_edges()
            .iter()
            .filter_map(|&(src, tgt)| {
                let s = self.node(src)?;
                let t = self.node(tgt)?;
                Some((s.qualified_name, t.qualified_name))
            })
            .collect()
    }

    fn get_calls_shared(&self) -> std::sync::Arc<[(StrKey, StrKey)]> {
        std::sync::Arc::from(
            <Self as super::traits::GraphQuery>::get_calls(self),
        )
    }

    fn get_imports(&self) -> Vec<(StrKey, StrKey)> {
        self.all_import_edges()
            .iter()
            .filter_map(|&(src, tgt)| {
                let s = self.node(src)?;
                let t = self.node(tgt)?;
                Some((s.qualified_name, t.qualified_name))
            })
            .collect()
    }

    fn get_inheritance(&self) -> Vec<(StrKey, StrKey)> {
        self.all_inheritance_edges()
            .iter()
            .filter_map(|&(src, tgt)| {
                let s = self.node(src)?;
                let t = self.node(tgt)?;
                Some((s.qualified_name, t.qualified_name))
            })
            .collect()
    }

    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name(qn) else {
            return vec![];
        };
        self.child_classes(idx)
            .iter()
            .filter_map(|&ci| self.node(ci).copied())
            .collect()
    }

    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        let Some((idx, _)) = self.node_by_name(qn) else {
            return vec![];
        };
        self.importers(idx)
            .iter()
            .filter_map(|&ci| self.node(ci).copied())
            .collect()
    }

    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        let si = self.interner();
        self.import_cycles()
            .iter()
            .map(|cycle| {
                let mut names: Vec<String> = cycle
                    .iter()
                    .filter_map(|&idx| self.node(idx))
                    .map(|n| si.resolve(n.qualified_name).to_string())
                    .collect();
                names.sort();
                names
            })
            .collect()
    }

    fn is_in_import_cycle(&self, file_path: &str) -> bool {
        let si = self.interner();
        self.import_cycles().iter().any(|cycle| {
            cycle.iter().any(|&idx| {
                if let Some(node) = self.node(idx) {
                    let qn = si.resolve(node.qualified_name);
                    qn == file_path || file_path.contains(qn)
                } else {
                    false
                }
            })
        })
    }

    fn stats(&self) -> std::collections::BTreeMap<String, i64> {
        CodeGraph::stats(self)
    }

    fn find_function_at(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        self.function_at(file_path, line)
            .and_then(|idx| self.node(idx).copied())
    }

    fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        self.functions()
            .iter()
            .filter_map(|&idx| {
                let node = self.node(idx)?;
                if node.complexity_opt().is_some_and(|c| c >= min_complexity) {
                    Some(*node)
                } else {
                    None
                }
            })
            .collect()
    }

    fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        self.functions()
            .iter()
            .filter_map(|&idx| {
                let node = self.node(idx)?;
                if node.param_count_opt().is_some_and(|p| p >= min_params) {
                    Some(*node)
                } else {
                    None
                }
            })
            .collect()
    }

    fn caller_file_spread(&self, qn: &str) -> usize {
        let Some((idx, _)) = self.node_by_name(qn) else {
            return 0;
        };
        let files: std::collections::HashSet<StrKey> = self
            .callers(idx)
            .iter()
            .filter_map(|&ci| self.node(ci))
            .map(|n| n.file_path)
            .collect();
        files.len()
    }

    fn count_external_callers_of(
        &self,
        qn: &str,
        class_file: &str,
        class_start: u32,
        class_end: u32,
    ) -> usize {
        let si = self.interner();
        let Some((idx, _)) = self.node_by_name(qn) else {
            return 0;
        };
        self.callers(idx)
            .iter()
            .filter_map(|&ci| self.node(ci))
            .filter(|c| {
                si.resolve(c.file_path) != class_file
                    || c.line_start < class_start
                    || c.line_end > class_end
            })
            .count()
    }

    fn caller_module_spread(&self, qn: &str) -> usize {
        let si = self.interner();
        let Some((idx, _)) = self.node_by_name(qn) else {
            return 0;
        };
        let modules: std::collections::HashSet<&str> = self
            .callers(idx)
            .iter()
            .filter_map(|&ci| self.node(ci))
            .map(|n| {
                std::path::Path::new(si.resolve(n.file_path))
                    .parent()
                    .and_then(|p| p.to_str())
                    .unwrap_or("root")
            })
            .collect();
        modules.len()
    }

    fn build_call_maps_raw(
        &self,
    ) -> (
        HashMap<StrKey, usize>,
        HashMap<usize, Vec<usize>>,
        HashMap<usize, Vec<usize>>,
    ) {
        let functions = self.functions();
        let qn_to_idx: HashMap<StrKey, usize> = functions
            .iter()
            .enumerate()
            .filter_map(|(i, &idx)| {
                let node = self.node(idx)?;
                Some((node.qualified_name, i))
            })
            .collect();

        let mut callers_map: HashMap<usize, Vec<usize>> = HashMap::new();
        let mut callees_map: HashMap<usize, Vec<usize>> = HashMap::new();

        for (func_list_idx, &node_idx) in functions.iter().enumerate() {
            for &callee_idx in self.callees(node_idx) {
                if let Some(node) = self.node(callee_idx) {
                    if let Some(&callee_list_idx) = qn_to_idx.get(&node.qualified_name) {
                        callees_map
                            .entry(func_list_idx)
                            .or_default()
                            .push(callee_list_idx);
                        callers_map
                            .entry(callee_list_idx)
                            .or_default()
                            .push(func_list_idx);
                    }
                }
            }
        }

        (qn_to_idx, callers_map, callees_map)
    }

    fn get_call_adjacency(
        &self,
    ) -> (
        Vec<Vec<usize>>,
        Vec<Vec<usize>>,
        HashMap<StrKey, usize>,
    ) {
        let functions = self.functions();
        let n = functions.len();
        let qn_to_idx: HashMap<StrKey, usize> = functions
            .iter()
            .enumerate()
            .filter_map(|(i, &idx)| {
                let node = self.node(idx)?;
                Some((node.qualified_name, i))
            })
            .collect();

        let mut adj = vec![vec![]; n];
        let mut rev_adj = vec![vec![]; n];

        for (func_list_idx, &node_idx) in functions.iter().enumerate() {
            for &callee_idx in self.callees(node_idx) {
                if let Some(node) = self.node(callee_idx) {
                    if let Some(&callee_list_idx) = qn_to_idx.get(&node.qualified_name) {
                        adj[func_list_idx].push(callee_list_idx);
                        rev_adj[callee_list_idx].push(func_list_idx);
                    }
                }
            }
        }

        (adj, rev_adj, qn_to_idx)
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
    fn interner(&self) -> &super::interner::StringInterner {
        (**self).interner()
    }

    fn extra_props(&self, qn: StrKey) -> Option<ExtraProps> {
        <CodeGraph as super::traits::GraphQuery>::extra_props(self, qn)
    }

    fn get_functions(&self) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_functions(self)
    }

    fn get_classes(&self) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_classes(self)
    }

    fn get_files(&self) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_files(self)
    }

    fn get_functions_shared(&self) -> std::sync::Arc<[CodeNode]> {
        <CodeGraph as super::traits::GraphQuery>::get_functions_shared(self)
    }

    fn get_classes_shared(&self) -> std::sync::Arc<[CodeNode]> {
        <CodeGraph as super::traits::GraphQuery>::get_classes_shared(self)
    }

    fn get_files_shared(&self) -> std::sync::Arc<[CodeNode]> {
        <CodeGraph as super::traits::GraphQuery>::get_files_shared(self)
    }

    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_functions_in_file(self, file_path)
    }

    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_classes_in_file(self, file_path)
    }

    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_node(self, qn)
    }

    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_callers(self, qn)
    }

    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_callees(self, qn)
    }

    fn call_fan_in(&self, qn: &str) -> usize {
        <CodeGraph as super::traits::GraphQuery>::call_fan_in(self, qn)
    }

    fn call_fan_out(&self, qn: &str) -> usize {
        <CodeGraph as super::traits::GraphQuery>::call_fan_out(self, qn)
    }

    fn get_calls(&self) -> Vec<(StrKey, StrKey)> {
        <CodeGraph as super::traits::GraphQuery>::get_calls(self)
    }

    fn get_calls_shared(&self) -> std::sync::Arc<[(StrKey, StrKey)]> {
        <CodeGraph as super::traits::GraphQuery>::get_calls_shared(self)
    }

    fn get_imports(&self) -> Vec<(StrKey, StrKey)> {
        <CodeGraph as super::traits::GraphQuery>::get_imports(self)
    }

    fn get_inheritance(&self) -> Vec<(StrKey, StrKey)> {
        <CodeGraph as super::traits::GraphQuery>::get_inheritance(self)
    }

    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_child_classes(self, qn)
    }

    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_importers(self, qn)
    }

    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        <CodeGraph as super::traits::GraphQuery>::find_import_cycles(self)
    }

    fn is_in_import_cycle(&self, file_path: &str) -> bool {
        <CodeGraph as super::traits::GraphQuery>::is_in_import_cycle(self, file_path)
    }

    fn stats(&self) -> std::collections::BTreeMap<String, i64> {
        <CodeGraph as super::traits::GraphQuery>::stats(self)
    }

    fn find_function_at(&self, file_path: &str, line: u32) -> Option<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::find_function_at(self, file_path, line)
    }

    fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_complex_functions(self, min_complexity)
    }

    fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        <CodeGraph as super::traits::GraphQuery>::get_long_param_functions(self, min_params)
    }

    fn caller_file_spread(&self, qn: &str) -> usize {
        <CodeGraph as super::traits::GraphQuery>::caller_file_spread(self, qn)
    }

    fn count_external_callers_of(
        &self,
        qn: &str,
        class_file: &str,
        class_start: u32,
        class_end: u32,
    ) -> usize {
        <CodeGraph as super::traits::GraphQuery>::count_external_callers_of(
            self,
            qn,
            class_file,
            class_start,
            class_end,
        )
    }

    fn caller_module_spread(&self, qn: &str) -> usize {
        <CodeGraph as super::traits::GraphQuery>::caller_module_spread(self, qn)
    }

    fn build_call_maps_raw(
        &self,
    ) -> (
        HashMap<StrKey, usize>,
        HashMap<usize, Vec<usize>>,
        HashMap<usize, Vec<usize>>,
    ) {
        <CodeGraph as super::traits::GraphQuery>::build_call_maps_raw(self)
    }

    fn get_call_adjacency(
        &self,
    ) -> (
        Vec<Vec<usize>>,
        Vec<Vec<usize>>,
        HashMap<StrKey, usize>,
    ) {
        <CodeGraph as super::traits::GraphQuery>::get_call_adjacency(self)
    }

    // ==================== NodeIndex-based overrides ====================

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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::store_models::{CodeEdge, NodeKind};
    use crate::graph::traits::GraphQuery;

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
