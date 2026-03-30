//! Node construction factories for the code graph.

use crate::graph::interner::global_interner;
use crate::graph::store_models::{
    FLAG_ADDRESS_TAKEN, FLAG_HAS_DECORATORS, FLAG_IS_ASYNC, FLAG_IS_EXPORTED,
};
use crate::graph::{CodeEdge, CodeNode, NodeKind};
use crate::models::{Class, Function};

pub(super) fn build_func_node(
    func: &Function,
    relative_str: &str,
    complexity: u32,
    address_taken: bool,
) -> CodeNode {
    let i = global_interner();
    let file_key = i.intern(relative_str);
    let lang_key = i.intern(super::detect_language_from_path_str(relative_str));

    let mut flags: u8 = 0;
    if func.is_async {
        flags |= FLAG_IS_ASYNC;
    }
    if address_taken {
        flags |= FLAG_ADDRESS_TAKEN;
    }
    if !func.annotations.is_empty() {
        flags |= FLAG_HAS_DECORATORS;
    }
    if func.annotations.iter().any(|a| a == "exported") {
        flags |= FLAG_IS_EXPORTED;
    }

    CodeNode {
        kind: NodeKind::Function,
        name: i.intern(&func.name),
        qualified_name: i.intern(&func.qualified_name),
        file_path: file_key,
        language: lang_key,
        line_start: func.line_start,
        line_end: func.line_end,
        complexity: complexity as u16,
        param_count: func.parameters.len().min(255) as u8,
        method_count: 0,
        field_count: 0,
        max_nesting: func.max_nesting.unwrap_or(0).min(255) as u8,
        return_count: 0,
        commit_count: 0,
        flags,
    }
}

/// Build a CodeNode for a single class, attaching optional doc_comment and annotations.
///
/// String properties (doc_comment) are written to the ExtraProps side table
/// via `graph.update_node_properties()` after the node is inserted into the graph.
#[allow(dead_code)]
pub(super) fn build_class_node(class: &Class, relative_str: &str) -> CodeNode {
    let i = global_interner();
    let file_key = i.intern(relative_str);
    let lang_key = i.intern(super::detect_language_from_path_str(relative_str));

    let mut flags: u8 = 0;
    if !class.annotations.is_empty() {
        flags |= FLAG_HAS_DECORATORS;
    }

    CodeNode {
        kind: NodeKind::Class,
        name: i.intern(&class.name),
        qualified_name: i.intern(&class.qualified_name),
        file_path: file_key,
        language: lang_key,
        line_start: class.line_start,
        line_end: class.line_end,
        complexity: 0,
        param_count: 0,
        method_count: class.methods.len().min(65535) as u16,
        field_count: class.field_count.min(65535) as u16,
        max_nesting: 0,
        return_count: 0,
        commit_count: 0,
        flags,
    }
}

/// Derive a module qualified name from a relative file path.
///
/// e.g. "src/app/routes.py" -> "src.app.routes"
///      "src/lib.rs" -> "src::lib"
#[cfg(test)]
pub(super) fn module_qn_from_path(relative_str: &str) -> String {
    // Strip common extensions and convert path separators to language-appropriate delimiters
    let base = relative_str
        .trim_end_matches(".py")
        .trim_end_matches(".pyi")
        .trim_end_matches(".ts")
        .trim_end_matches(".tsx")
        .trim_end_matches(".js")
        .trim_end_matches(".jsx")
        .trim_end_matches(".mjs")
        .trim_end_matches(".rs")
        .trim_end_matches(".go")
        .trim_end_matches(".java")
        .trim_end_matches(".cs")
        .trim_end_matches(".c")
        .trim_end_matches(".cpp")
        .trim_end_matches(".cc")
        .trim_end_matches(".hpp");

    // Use dots as delimiter (works for Python-style qualified names)
    base.replace('/', ".")
}

/// Emit a Calls edge from the module (file node) to a decorated function.
///
/// A decorator invocation (`@app.route(...)`) executes at module load time,
/// meaning the module itself is the caller. Using the existing file node as
/// the caller avoids creating synthetic function nodes that would pollute
/// `get_functions()` results and need filtering in every detector.
pub(super) fn emit_decorator_call_edge(
    func_qn: &str,
    file_qn: &str,
    has_annotations: bool,
    edges: &mut Vec<(String, String, CodeEdge)>,
) {
    if !has_annotations {
        return;
    }
    // Module (file node) calls the decorated function at load time
    edges.push((file_qn.to_string(), func_qn.to_string(), CodeEdge::calls()));
}
