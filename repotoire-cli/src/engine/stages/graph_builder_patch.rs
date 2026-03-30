//! GraphBuilder-based incremental graph patching.
//!
//! Used when the engine loads a persisted session from disk and needs to
//! patch the graph for changed files. The loaded `CodeGraph` is round-tripped
//! via `clone_into_builder()`, patched here, then re-frozen.

use crate::graph::builder::GraphBuilder;
use crate::graph::store_models::{CodeEdge, CodeNode};
use crate::parsers::ParseResult;
use std::path::PathBuf;
use std::sync::Arc;

/// Patch a `GraphBuilder` with incremental file changes.
///
/// Steps:
/// 1. Remove all nodes/edges for changed + removed files
/// 2. Re-insert nodes/edges from fresh parse results for changed files
pub fn patch_builder(
    builder: &mut GraphBuilder,
    changed_files: &[PathBuf],
    removed_files: &[PathBuf],
    new_parse_results: &[(PathBuf, Arc<ParseResult>)],
) {
    let files_to_remove: Vec<PathBuf> = changed_files
        .iter()
        .chain(removed_files.iter())
        .cloned()
        .collect();

    if !files_to_remove.is_empty() {
        builder.remove_file_entities(&files_to_remove);
    }

    for (file_path, parse_result) in new_parse_results {
        let file_str = file_path.to_string_lossy();

        // Add file node
        builder.add_node(CodeNode::file(&file_str));

        // Add function nodes
        for func in &parse_result.functions {
            let qn = if func.qualified_name.is_empty() {
                format!("{}::{}", file_str, func.name)
            } else {
                func.qualified_name.clone()
            };
            let mut node = CodeNode::function(&func.name, &file_str)
                .with_qualified_name(&qn)
                .with_lines(func.line_start, func.line_end);
            node.complexity = func.complexity.unwrap_or(0) as u16;
            node.max_nesting = func.max_nesting.unwrap_or(0) as u8;
            node.param_count = func.parameters.len() as u8;
            let func_idx = builder.add_node(node);

            if let Some(file_idx) = builder.get_node_index(&file_str) {
                builder.add_edge(file_idx, func_idx, CodeEdge::contains());
            }
        }

        // Add class nodes
        for class in &parse_result.classes {
            let qn = if class.qualified_name.is_empty() {
                format!("{}::{}", file_str, class.name)
            } else {
                class.qualified_name.clone()
            };
            let node = CodeNode::class(&class.name, &file_str)
                .with_qualified_name(&qn)
                .with_lines(class.line_start, class.line_end);
            let class_idx = builder.add_node(node);

            if let Some(file_idx) = builder.get_node_index(&file_str) {
                builder.add_edge(file_idx, class_idx, CodeEdge::contains());
            }
        }

        // Add import edges (best-effort)
        for import in &parse_result.imports {
            if let Some(src_idx) = builder.get_node_index(&file_str) {
                let target = &import.path;
                if let Some(tgt_idx) = builder.get_node_index(target) {
                    builder.add_edge(src_idx, tgt_idx, CodeEdge::imports());
                }
            }
        }

        // Add call edges
        for (caller_qn, callee_qn) in &parse_result.calls {
            if let Some(caller_idx) = builder.get_node_index(caller_qn) {
                if let Some(callee_idx) = builder.get_node_index(callee_qn) {
                    builder.add_edge(caller_idx, callee_idx, CodeEdge::calls());
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::interner::global_interner;
    use crate::graph::store_models::{CodeEdge, CodeNode};
    use std::path::PathBuf;
    use std::sync::Arc;

    #[test]
    fn test_patch_builder_replaces_changed_file() {
        let mut builder = GraphBuilder::new();
        let f1 = builder.add_node(
            CodeNode::function("foo", "a.py")
                .with_qualified_name("a.py::foo")
                .with_lines(1, 10),
        );
        let f2 = builder.add_node(
            CodeNode::function("bar", "b.py")
                .with_qualified_name("b.py::bar")
                .with_lines(1, 10),
        );
        builder.add_edge(f1, f2, CodeEdge::calls());

        let graph = builder.freeze();
        assert_eq!(graph.functions().len(), 2);
        let mut builder2 = graph.clone_into_builder();

        let mut parse_result = ParseResult::default();
        parse_result.functions.push(crate::models::Function {
            name: "foo_v2".to_string(),
            qualified_name: "a.py::foo_v2".to_string(),
            file_path: PathBuf::from("a.py"),
            line_start: 1,
            line_end: 15,
            parameters: Vec::new(),
            return_type: None,
            is_async: false,
            complexity: None,
            max_nesting: None,
            doc_comment: None,
            annotations: Vec::new(),
        });

        patch_builder(
            &mut builder2,
            &[PathBuf::from("a.py")],
            &[],
            &[(PathBuf::from("a.py"), Arc::new(parse_result))],
        );

        let graph2 = builder2.freeze();
        let funcs = graph2.functions();
        assert_eq!(funcs.len(), 2);

        let si = global_interner();
        let names: Vec<&str> = funcs
            .iter()
            .map(|&idx| {
                let node = graph2.node(idx).unwrap();
                si.resolve(node.name)
            })
            .collect();
        assert!(names.contains(&"bar"));
        assert!(names.contains(&"foo_v2"));
        assert!(!names.contains(&"foo"));
    }

    #[test]
    fn test_patch_builder_handles_removed_file() {
        let mut builder = GraphBuilder::new();
        builder.add_node(
            CodeNode::function("foo", "a.py")
                .with_qualified_name("a.py::foo")
                .with_lines(1, 10),
        );
        builder.add_node(
            CodeNode::function("bar", "b.py")
                .with_qualified_name("b.py::bar")
                .with_lines(1, 10),
        );

        let graph = builder.freeze();
        let mut builder2 = graph.clone_into_builder();

        patch_builder(&mut builder2, &[], &[PathBuf::from("a.py")], &[]);

        let graph2 = builder2.freeze();
        assert_eq!(graph2.functions().len(), 1);

        let si = global_interner();
        let node = graph2.node(graph2.functions()[0]).unwrap();
        assert_eq!(si.resolve(node.name), "bar");
    }

    #[test]
    fn test_patch_builder_no_changes_is_identity() {
        let mut builder = GraphBuilder::new();
        builder.add_node(
            CodeNode::function("foo", "a.py")
                .with_qualified_name("a.py::foo")
                .with_lines(1, 10),
        );
        let graph = builder.freeze();
        let node_count = graph.node_count();

        let mut builder2 = graph.clone_into_builder();
        patch_builder(&mut builder2, &[], &[], &[]);
        let graph2 = builder2.freeze();

        assert_eq!(graph2.node_count(), node_count);
    }
}
