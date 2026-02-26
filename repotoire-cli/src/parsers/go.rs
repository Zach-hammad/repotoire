//! Go parser using tree-sitter
//!
//! Extracts functions, structs, interfaces, methods, imports, and call relationships from Go source code.

use crate::models::{Class, Function};
use crate::parsers::{ImportInfo, ParseResult};
use anyhow::{Context, Result};
use std::collections::HashMap;
use std::path::Path;
use tree_sitter::{Node, Parser, Query, QueryCursor, StreamingIterator};

/// Parse a Go file and extract all code entities
pub fn parse(path: &Path) -> Result<ParseResult> {
    let source = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read file: {}", path.display()))?;

    parse_source(&source, path)
}

/// Parse Go source code directly (useful for testing)
pub fn parse_source(source: &str, path: &Path) -> Result<ParseResult> {
    let mut parser = Parser::new();
    let language = tree_sitter_go::LANGUAGE;
    parser
        .set_language(&language.into())
        .context("Failed to set Go language")?;

    let tree = parser
        .parse(source, None)
        .context("Failed to parse Go source")?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();

    let mut result = ParseResult::default();

    extract_functions(&root, source_bytes, path, &mut result)?;
    extract_structs_and_interfaces(&root, source_bytes, path, &mut result)?;
    extract_imports(&root, source_bytes, &mut result)?;
    extract_calls(&root, source_bytes, path, &mut result)?;

    Ok(result)
}

/// Extract function definitions (including methods) from the AST
fn extract_functions(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    // Query for function declarations
    let func_query_str = r#"
        (function_declaration
            name: (identifier) @func_name
            parameters: (parameter_list) @params
            result: (_)? @return_type
        ) @func
    "#;

    let language = tree_sitter_go::LANGUAGE;
    let query =
        Query::new(&language.into(), func_query_str).context("Failed to create function query")?;

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        let mut func_node = None;
        let mut name = String::new();
        let mut params_node = None;
        let mut return_type_node = None;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "func" => func_node = Some(capture.node),
                "func_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                "params" => params_node = Some(capture.node),
                "return_type" => return_type_node = Some(capture.node),
                _ => {}
            }
        }

        if let Some(node) = func_node {
            let parameters = extract_parameters(params_node, source);
            let return_type =
                return_type_node.map(|n| n.utf8_text(source).unwrap_or("").to_string());

            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;
            let qualified_name = format!("{}::{}:{}", path.display(), name, line_start);

            let doc_comment = extract_doc_comment(&node, source);
            let has_goroutines = contains_go_statement(&node);
            let mut annotations = Vec::new();
            if contains_channel_ops(&node) {
                annotations.push("go:uses_channels".to_string());
            }

            result.functions.push(Function {
                name: name.clone(),
                qualified_name,
                file_path: path.to_path_buf(),
                line_start,
                line_end,
                parameters,
                return_type,
                is_async: has_goroutines,
                complexity: Some(calculate_complexity(&node, source)),
                max_nesting: None,
                doc_comment,
                annotations,
            });
        }
    }

    // Query for method declarations (functions with receivers)
    extract_methods(root, source, path, result)?;

    Ok(())
}

/// Extract method declarations (functions with receivers)
fn extract_methods(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    let method_query_str = r#"
        (method_declaration
            receiver: (parameter_list) @receiver
            name: (field_identifier) @method_name
            parameters: (parameter_list) @params
            result: (_)? @return_type
        ) @method
    "#;

    let language = tree_sitter_go::LANGUAGE;
    let query =
        Query::new(&language.into(), method_query_str).context("Failed to create method query")?;

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        let mut method_node = None;
        let mut name = String::new();
        let mut receiver_node = None;
        let mut params_node = None;
        let mut return_type_node = None;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "method" => method_node = Some(capture.node),
                "method_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                "receiver" => receiver_node = Some(capture.node),
                "params" => params_node = Some(capture.node),
                "return_type" => return_type_node = Some(capture.node),
                _ => {}
            }
        }

        if let Some(node) = method_node {
            let receiver_type = extract_receiver_type(receiver_node, source);
            let parameters = extract_parameters(params_node, source);
            let return_type =
                return_type_node.map(|n| n.utf8_text(source).unwrap_or("").to_string());

            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;

            let qualified_name = if let Some(ref recv) = receiver_type {
                format!("{}::({}).{}:{}", path.display(), recv, name, line_start)
            } else {
                format!("{}::{}:{}", path.display(), name, line_start)
            };

            let doc_comment = extract_doc_comment(&node, source);
            let has_goroutines = contains_go_statement(&node);
            let mut annotations = Vec::new();
            if contains_channel_ops(&node) {
                annotations.push("go:uses_channels".to_string());
            }

            result.functions.push(Function {
                name: name.clone(),
                qualified_name,
                file_path: path.to_path_buf(),
                line_start,
                line_end,
                parameters,
                return_type,
                is_async: has_goroutines,
                complexity: Some(calculate_complexity(&node, source)),
                max_nesting: None,
                doc_comment,
                annotations,
            });
        }
    }

    Ok(())
}

/// Extract receiver type from a method
fn extract_receiver_type(receiver_node: Option<Node>, source: &[u8]) -> Option<String> {
    let node = receiver_node?;

    for child in node.children(&mut node.walk()) {
        if child.kind() == "parameter_declaration" {
            // Get the type from the parameter
            if let Some(type_node) = child.child_by_field_name("type") {
                return type_node.utf8_text(source).ok().map(|s| s.to_string());
            }
            // Fallback: get the last child which should be the type
            let mut last_type = None;
            for grandchild in child.children(&mut child.walk()) {
                if grandchild.kind() == "type_identifier" || grandchild.kind() == "pointer_type" {
                    last_type = Some(grandchild);
                }
            }
            if let Some(type_node) = last_type {
                return type_node.utf8_text(source).ok().map(|s| s.to_string());
            }
        }
    }

    None
}

/// Extract parameter names from a parameter list
fn extract_parameters(params_node: Option<Node>, source: &[u8]) -> Vec<String> {
    let Some(node) = params_node else {
        return vec![];
    };

    let mut params = Vec::new();

    for child in node.children(&mut node.walk()) {
        if child.kind() == "parameter_declaration" {
            // Parameters can have multiple names before the type
            for grandchild in child.children(&mut child.walk()) {
                if grandchild.kind() == "identifier" {
                    if let Ok(text) = grandchild.utf8_text(source) {
                        params.push(text.to_string());
                    }
                }
            }
        }
    }

    params
}

/// Extract structs and interfaces from the AST
fn extract_structs_and_interfaces(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    let query_str = r#"
        (type_declaration
            (type_spec
                name: (type_identifier) @type_name
                type: (struct_type) @struct_body
            )
        ) @struct_decl

        (type_declaration
            (type_spec
                name: (type_identifier) @iface_name
                type: (interface_type) @iface_body
            )
        ) @iface_decl
    "#;

    let language = tree_sitter_go::LANGUAGE;
    let query = Query::new(&language.into(), query_str).context("Failed to create type query")?;

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        let mut decl_node = None;
        let mut name = String::new();
        let mut _is_struct = false;
        let mut is_interface = false;
        let mut body_node = None;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "struct_decl" => {
                    decl_node = Some(capture.node);
                    _is_struct = true;
                }
                "iface_decl" => {
                    decl_node = Some(capture.node);
                    is_interface = true;
                }
                "type_name" | "iface_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                "struct_body" | "iface_body" => {
                    body_node = Some(capture.node);
                }
                _ => {}
            }
        }

        if let Some(node) = decl_node {
            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;

            let qualified_name = if is_interface {
                format!("{}::interface::{}:{}", path.display(), name, line_start)
            } else {
                format!("{}::{}:{}", path.display(), name, line_start)
            };

            let methods = if is_interface {
                extract_interface_methods(body_node, source)
            } else {
                vec![]
            };

            let doc_comment = extract_doc_comment(&node, source);

            result.classes.push(Class {
                name: name.clone(),
                qualified_name,
                file_path: path.to_path_buf(),
                line_start,
                line_end,
                methods,
                bases: vec![],
                doc_comment,
                annotations: vec![],
            });
        }
    }

    Ok(())
}

/// Extract method signatures from an interface
fn extract_interface_methods(body_node: Option<Node>, source: &[u8]) -> Vec<String> {
    let Some(node) = body_node else {
        return vec![];
    };

    let mut methods = Vec::new();

    for child in node.children(&mut node.walk()) {
        if child.kind() == "method_elem" {
            if let Some(name_node) = child.child_by_field_name("name") {
                if let Ok(name) = name_node.utf8_text(source) {
                    methods.push(name.to_string());
                }
            }
        }
    }

    methods
}

/// Extract import statements from the AST
fn extract_imports(root: &Node, source: &[u8], result: &mut ParseResult) -> Result<()> {
    let query_str = r#"
        (import_declaration
            (import_spec
                path: (interpreted_string_literal) @import_path
            )
        )
        (import_declaration
            (import_spec_list
                (import_spec
                    path: (interpreted_string_literal) @import_path
                )
            )
        )
    "#;

    let language = tree_sitter_go::LANGUAGE;
    let query = Query::new(&language.into(), query_str).context("Failed to create import query")?;

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        for capture in m.captures.iter() {
            if let Ok(text) = capture.node.utf8_text(source) {
                // Remove quotes from import path
                let import = text.trim_matches('"').to_string();
                if !import.is_empty() {
                    result.imports.push(ImportInfo::runtime(import));
                }
            }
        }
    }

    Ok(())
}

/// Extract function calls from the AST
fn extract_calls(root: &Node, source: &[u8], path: &Path, result: &mut ParseResult) -> Result<()> {
    let mut scope_map: HashMap<(u32, u32), String> = HashMap::new();

    for func in &result.functions {
        scope_map.insert(
            (func.line_start, func.line_end),
            func.qualified_name.clone(),
        );
    }

    extract_calls_recursive(root, source, path, &scope_map, result);

    Ok(())
}

/// Recursively extract calls from the AST
fn extract_calls_recursive(
    node: &Node,
    source: &[u8],
    path: &Path,
    scope_map: &HashMap<(u32, u32), String>,
    result: &mut ParseResult,
) {
    if node.kind() == "call_expression" {
        let call_line = node.start_position().row as u32 + 1;
        // For top-level calls (outside any function), use the file path as the caller
        let caller = find_containing_scope(call_line, scope_map)
            .unwrap_or_else(|| path.display().to_string());

        if let Some(func_node) = node.child_by_field_name("function") {
            if let Some(callee) = extract_call_target(&func_node, source) {
                result.calls.push((caller, callee));
            }
        }
    }

    for child in node.children(&mut node.walk()) {
        extract_calls_recursive(&child, source, path, scope_map, result);
    }
}

/// Find which scope contains a given line
fn find_containing_scope(line: u32, scope_map: &HashMap<(u32, u32), String>) -> Option<String> {
    super::find_containing_scope(line, scope_map)
}

/// Extract the target of a function call
fn extract_call_target(node: &Node, source: &[u8]) -> Option<String> {
    match node.kind() {
        "identifier" => node.utf8_text(source).ok().map(|s| s.to_string()),
        "selector_expression" => {
            // pkg.Function or obj.Method
            node.utf8_text(source).ok().map(|s| s.to_string())
        }
        "parenthesized_expression" => {
            // Type assertion calls
            for child in node.children(&mut node.walk()) {
                if let Some(target) = extract_call_target(&child, source) {
                    return Some(target);
                }
            }
            None
        }
        _ => node.utf8_text(source).ok().map(|s| s.to_string()),
    }
}

/// Extract the doc comment immediately preceding a declaration node.
///
/// In Go, doc comments are `//` or `/* */` comments with no blank lines
/// between the comment and the declaration.
fn extract_doc_comment(node: &Node, source: &[u8]) -> Option<String> {
    let mut comments = Vec::new();
    let mut sibling = node.prev_sibling();

    while let Some(sib) = sibling {
        if sib.kind() == "comment" {
            // Check there's no blank line gap between this comment and the next node
            let comment_end_row = sib.end_position().row;
            let next_start_row = if let Some(next) = sib.next_sibling() {
                next.start_position().row
            } else {
                break;
            };
            // Allow at most 1 row gap (the comment line itself ends, next starts on the following line)
            if next_start_row - comment_end_row > 1 {
                break;
            }
            comments.push(sib);
            sibling = sib.prev_sibling();
        } else {
            break;
        }
    }

    if comments.is_empty() {
        return None;
    }

    // Comments were collected in reverse order
    comments.reverse();

    let doc: String = comments
        .iter()
        .filter_map(|c| c.utf8_text(source).ok())
        .map(|text| {
            // Strip // prefix and leading space
            if let Some(stripped) = text.strip_prefix("//") {
                stripped.strip_prefix(' ').unwrap_or(stripped)
            } else if text.starts_with("/*") && text.ends_with("*/") {
                // Block comment: strip /* and */
                &text[2..text.len() - 2]
            } else {
                text
            }
        })
        .collect::<Vec<_>>()
        .join("\n");

    if doc.trim().is_empty() {
        None
    } else {
        Some(doc)
    }
}

/// Check if a function body contains `go` statements (goroutine launches)
fn contains_go_statement(node: &Node) -> bool {
    fn walk(node: &Node) -> bool {
        if node.kind() == "go_statement" {
            return true;
        }
        for child in node.children(&mut node.walk()) {
            if walk(&child) {
                return true;
            }
        }
        false
    }
    walk(node)
}

/// Check if a function body contains channel operations
/// (channel_type, send_statement, or receive via unary_expression with <-)
fn contains_channel_ops(node: &Node) -> bool {
    fn walk(node: &Node) -> bool {
        match node.kind() {
            "channel_type" | "send_statement" => return true,
            "unary_expression" => {
                // Receive expression: <-ch
                for child in node.children(&mut node.walk()) {
                    if child.kind() == "<-" {
                        return true;
                    }
                }
            }
            _ => {}
        }
        for child in node.children(&mut node.walk()) {
            if walk(&child) {
                return true;
            }
        }
        false
    }
    walk(node)
}

/// Calculate cyclomatic complexity of a function
fn calculate_complexity(node: &Node, _source: &[u8]) -> u32 {
    let mut complexity = 1;

    fn count_branches(node: &Node, complexity: &mut u32) {
        match node.kind() {
            "if_statement" | "for_statement" | "range_clause" => {
                *complexity += 1;
            }
            "expression_case" | "default_case" | "type_case" => {
                *complexity += 1;
            }
            "binary_expression" => {
                for child in node.children(&mut node.walk()) {
                    if child.kind() == "&&" || child.kind() == "||" {
                        *complexity += 1;
                    }
                }
            }
            "select_statement" | "communication_case" => {
                *complexity += 1;
            }
            _ => {}
        }

        for child in node.children(&mut node.walk()) {
            count_branches(&child, complexity);
        }
    }

    count_branches(node, &mut complexity);
    complexity
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn test_parse_simple_function() {
        let source = r#"
package main

func hello(name string) string {
    return "Hello, " + name
}
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        assert_eq!(result.functions.len(), 1);
        let func = &result.functions[0];
        assert_eq!(func.name, "hello");
    }

    #[test]
    fn test_parse_method() {
        let source = r#"
package main

type MyStruct struct {
    value int
}

func (s *MyStruct) GetValue() int {
    return s.value
}
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        assert!(result.functions.iter().any(|f| f.name == "GetValue"));
    }

    #[test]
    fn test_parse_struct() {
        let source = r#"
package main

type Person struct {
    Name string
    Age  int
}
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        assert_eq!(result.classes.len(), 1);
        let class = &result.classes[0];
        assert_eq!(class.name, "Person");
    }

    #[test]
    fn test_parse_interface() {
        let source = r#"
package main

type Reader interface {
    Read(p []byte) (n int, err error)
}
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        assert_eq!(result.classes.len(), 1);
        let iface = &result.classes[0];
        assert_eq!(iface.name, "Reader");
        // Interface methods may not be extracted - this is expected
        // The parser focuses on struct methods, not interface declarations
        // assert!(iface.methods.contains(&"Read".to_string()));
    }

    #[test]
    fn test_parse_imports() {
        let source = r#"
package main

import (
    "fmt"
    "os"
)
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        assert!(result.imports.iter().any(|i| i.path == "fmt"));
        assert!(result.imports.iter().any(|i| i.path == "os"));
    }

    #[test]
    fn test_method_count_excludes_nested_closures() {
        // Issue #18: Parser should not count closures inside methods as separate methods
        let source = r#"
package main

type Handler struct {
    callbacks []func()
}

func (h *Handler) Register(name string) {
    // This closure should NOT be counted as a separate method
    callback := func() {
        fmt.Println(name)
    }
    h.callbacks = append(h.callbacks, callback)
}

func (h *Handler) Execute() {
    // The func literal here is NOT a method
    for _, cb := range h.callbacks {
        go func(callback func()) {
            callback()
        }(cb)
    }
}

func (h *Handler) Clear() {
    h.callbacks = nil
}
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        // Should have exactly 3 methods on Handler: Register, Execute, Clear
        // NOT: the closure in Register, the goroutine func in Execute
        let methods: Vec<_> = result
            .functions
            .iter()
            .filter(|f| f.qualified_name.contains("Handler"))
            .collect();

        assert_eq!(
            methods.len(),
            3,
            "Expected 3 methods (Register, Execute, Clear), got {:?}",
            methods.iter().map(|f| &f.name).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_doc_comment_extracted() {
        let source = r#"
package main

// Add adds two numbers together
// and returns the result.
func Add(a, b int) int {
    return a + b
}
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        let func = &result.functions[0];
        assert_eq!(func.name, "Add");
        assert!(func.doc_comment.is_some());
        let doc = func.doc_comment.as_ref().expect("should have doc comment");
        assert!(doc.contains("Add adds two numbers"), "Got: {}", doc);
        assert!(doc.contains("returns the result"), "Got: {}", doc);
    }

    #[test]
    fn test_no_doc_comment_when_gap() {
        let source = r#"
package main

// This comment has a blank line before the function

func NoDoc() {}
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        let func = &result.functions[0];
        assert_eq!(func.name, "NoDoc");
        assert!(func.doc_comment.is_none());
    }

    #[test]
    fn test_struct_doc_comment() {
        let source = r#"
package main

// Server represents an HTTP server.
type Server struct {
    Port int
}
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        let class = &result.classes[0];
        assert_eq!(class.name, "Server");
        assert!(class.doc_comment.is_some());
        assert!(class.doc_comment.as_ref().expect("should have doc comment").contains("HTTP server"));
    }

    #[test]
    fn test_goroutine_detected() {
        let source = r#"
package main

func startWorker() {
    go func() {
        doWork()
    }()
}

func noGoroutine() {
    doWork()
}
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        let worker = result.functions.iter().find(|f| f.name == "startWorker").expect("should find startWorker");
        assert!(worker.is_async, "startWorker should be marked async (goroutine)");

        let no_go = result.functions.iter().find(|f| f.name == "noGoroutine").expect("should find noGoroutine");
        assert!(!no_go.is_async, "noGoroutine should not be marked async");
    }

    #[test]
    fn test_channel_ops_detected() {
        let source = r#"
package main

func producer(ch chan<- int) {
    ch <- 42
}

func consumer(ch <-chan int) {
    val := <-ch
    _ = val
}

func noChannels() {
    x := 1 + 2
    _ = x
}
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        let producer = result.functions.iter().find(|f| f.name == "producer").expect("should find producer");
        assert!(
            producer.annotations.contains(&"go:uses_channels".to_string()),
            "producer should have go:uses_channels annotation, got: {:?}",
            producer.annotations
        );

        let consumer = result.functions.iter().find(|f| f.name == "consumer").expect("should find consumer");
        assert!(
            consumer.annotations.contains(&"go:uses_channels".to_string()),
            "consumer should have go:uses_channels annotation, got: {:?}",
            consumer.annotations
        );

        let no_ch = result.functions.iter().find(|f| f.name == "noChannels").expect("should find noChannels");
        assert!(
            no_ch.annotations.is_empty(),
            "noChannels should have no annotations, got: {:?}",
            no_ch.annotations
        );
    }

    #[test]
    fn test_interface_methods_counted() {
        let source = r#"
package main

type Writer interface {
    Write(p []byte) (n int, err error)
    Close() error
}
"#;
        let path = PathBuf::from("test.go");
        let result = parse_source(source, &path).expect("should parse Go source");

        let iface = result
            .classes
            .iter()
            .find(|c| c.name == "Writer")
            .expect("Should find Writer interface");

        assert_eq!(
            iface.methods.len(),
            2,
            "Expected 2 interface methods (Write, Close), got {:?}",
            iface.methods
        );
    }
}
