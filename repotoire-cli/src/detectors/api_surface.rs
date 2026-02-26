//! API surface detection utility.
//!
//! Determines whether a function at a given location is part of the project's
//! public API surface (exported + high fan-in), which affects how security
//! findings should be reported.

use crate::graph::GraphQuery;

/// Check if the function at the given file:line is part of the public API surface.
/// API surface = exported function with 3+ callers.
pub fn is_api_surface(graph: &dyn GraphQuery, file_path: &str, line: u32) -> bool {
    for func in graph.get_functions() {
        if func.file_path == file_path && func.line_start <= line && func.line_end >= line {
            // Check if exported (via annotation)
            let is_exported = func
                .properties
                .get("annotations")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().any(|a| a.as_str() == Some("exported")))
                .unwrap_or(false);

            if !is_exported {
                return false;
            }

            // Check fan-in (callers)
            let fan_in = graph.call_fan_in(&func.qualified_name);
            return fan_in >= 3;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::store_models::{CodeEdge, CodeNode};
    use crate::graph::GraphStore;

    #[test]
    fn test_non_exported_not_api_surface() {
        let store = GraphStore::in_memory();
        // No functions in the graph -> not API surface
        assert!(!is_api_surface(&store, "test.py", 5));
    }

    #[test]
    fn test_exported_but_low_fan_in_not_api_surface() {
        let store = GraphStore::in_memory();

        // Add an exported function with 0 callers
        let func = CodeNode::function("handler", "app.py")
            .with_qualified_name("app.handler")
            .with_lines(1, 10)
            .with_property(
                "annotations",
                serde_json::Value::Array(vec![serde_json::Value::String(
                    "exported".to_string(),
                )]),
            );
        store.add_node(func);

        // Exported but 0 callers -> not API surface
        assert!(!is_api_surface(&store, "app.py", 5));
    }

    #[test]
    fn test_exported_with_high_fan_in_is_api_surface() {
        let store = GraphStore::in_memory();

        // Add an exported function
        let func = CodeNode::function("handler", "app.py")
            .with_qualified_name("app.handler")
            .with_lines(1, 10)
            .with_property(
                "annotations",
                serde_json::Value::Array(vec![serde_json::Value::String(
                    "exported".to_string(),
                )]),
            );
        store.add_node(func);

        // Add 3 callers from different files
        for idx in 0..3 {
            let caller_name = format!("caller{}", idx);
            let caller_file = format!("client{}.py", idx);
            let caller_qn = format!("{}.{}", caller_file, caller_name);
            let caller = CodeNode::function(&caller_name, &caller_file)
                .with_qualified_name(&caller_qn)
                .with_lines(1, 5);
            store.add_node(caller);
            store.add_edge_by_name(&caller_qn, "app.handler", CodeEdge::calls());
        }

        // Exported + 3 callers -> API surface
        assert!(is_api_surface(&store, "app.py", 5));
    }

    #[test]
    fn test_not_exported_with_high_fan_in_not_api_surface() {
        let store = GraphStore::in_memory();

        // Add a non-exported function (no annotations)
        let func = CodeNode::function("internal_fn", "app.py")
            .with_qualified_name("app.internal_fn")
            .with_lines(1, 10);
        store.add_node(func);

        // Add 3 callers
        for idx in 0..3 {
            let caller_name = format!("caller{}", idx);
            let caller_file = format!("client{}.py", idx);
            let caller_qn = format!("{}.{}", caller_file, caller_name);
            let caller = CodeNode::function(&caller_name, &caller_file)
                .with_qualified_name(&caller_qn)
                .with_lines(1, 5);
            store.add_node(caller);
            store.add_edge_by_name(&caller_qn, "app.internal_fn", CodeEdge::calls());
        }

        // Not exported -> not API surface even with 3+ callers
        assert!(!is_api_surface(&store, "app.py", 5));
    }

    #[test]
    fn test_line_outside_function_not_api_surface() {
        let store = GraphStore::in_memory();

        let func = CodeNode::function("handler", "app.py")
            .with_qualified_name("app.handler")
            .with_lines(1, 10)
            .with_property(
                "annotations",
                serde_json::Value::Array(vec![serde_json::Value::String(
                    "exported".to_string(),
                )]),
            );
        store.add_node(func);

        // Line 20 is outside the function range 1-10
        assert!(!is_api_surface(&store, "app.py", 20));
    }
}
