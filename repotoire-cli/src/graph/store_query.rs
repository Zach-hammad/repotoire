use super::store::GraphStore;
use super::store_models::{CodeNode, NodeKind, EdgeKind, CodeEdge};
use std::collections::HashMap;
use super::traits::GraphQuery;
use petgraph::visit::EdgeRef;
use petgraph::Direction;
use petgraph::graph::NodeIndex;

impl super::traits::GraphQuery for std::sync::Arc<GraphStore> {
    fn get_functions(&self) -> Vec<CodeNode> {
        (**self).get_functions()
    }

    fn get_classes(&self) -> Vec<CodeNode> {
        (**self).get_classes()
    }

    fn get_files(&self) -> Vec<CodeNode> {
        (**self).get_files()
    }

    fn get_functions_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        (**self).get_functions_in_file(file_path)
    }

    fn get_classes_in_file(&self, file_path: &str) -> Vec<CodeNode> {
        (**self).get_classes_in_file(file_path)
    }

    fn get_node(&self, qn: &str) -> Option<CodeNode> {
        (**self).get_node(qn)
    }

    fn get_callers(&self, qn: &str) -> Vec<CodeNode> {
        (**self).get_callers(qn)
    }

    fn get_callees(&self, qn: &str) -> Vec<CodeNode> {
        (**self).get_callees(qn)
    }

    fn call_fan_in(&self, qn: &str) -> usize {
        (**self).call_fan_in(qn)
    }

    fn call_fan_out(&self, qn: &str) -> usize {
        (**self).call_fan_out(qn)
    }

    fn get_calls(&self) -> Vec<(String, String)> {
        (**self).get_calls()
    }

    fn get_imports(&self) -> Vec<(String, String)> {
        (**self).get_imports()
    }

    fn get_inheritance(&self) -> Vec<(String, String)> {
        (**self).get_inheritance()
    }

    fn get_child_classes(&self, qn: &str) -> Vec<CodeNode> {
        (**self).get_child_classes(qn)
    }

    fn get_importers(&self, qn: &str) -> Vec<CodeNode> {
        (**self).get_importers(qn)
    }

    fn find_import_cycles(&self) -> Vec<Vec<String>> {
        (**self).find_import_cycles()
    }

    fn stats(&self) -> HashMap<String, i64> {
        (**self).stats()
    }

    fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        (**self).get_complex_functions(min_complexity)
    }

    fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        (**self).get_long_param_functions(min_params)
    }
}

impl super::traits::GraphQuery for GraphStore {
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
        self.get_classes_in_file(file_path)
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

    fn get_complex_functions(&self, min_complexity: i64) -> Vec<CodeNode> {
        self.get_complex_functions(min_complexity)
    }

    fn get_long_param_functions(&self, min_params: i64) -> Vec<CodeNode> {
        self.get_long_param_functions(min_params)
    }
}

