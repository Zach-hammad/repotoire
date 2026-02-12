//! Rust parser using tree-sitter
//!
//! Extracts functions, structs, impls, traits, imports, and call relationships from Rust source code.

use crate::models::{Class, Function};
use crate::parsers::{ImportInfo, ParseResult};
use anyhow::{Context, Result};
use std::collections::HashMap;
use std::path::Path;
use tree_sitter::{Node, Parser, Query, QueryCursor};

/// Parse a Rust file and extract all code entities
pub fn parse(path: &Path) -> Result<ParseResult> {
    let source = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read file: {}", path.display()))?;

    parse_source(&source, path)
}

/// Parse Rust source code directly (useful for testing)
pub fn parse_source(source: &str, path: &Path) -> Result<ParseResult> {
    let mut parser = Parser::new();
    let language = tree_sitter_rust::LANGUAGE;
    parser
        .set_language(&language.into())
        .context("Failed to set Rust language")?;

    let tree = parser
        .parse(source, None)
        .context("Failed to parse Rust source")?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();

    let mut result = ParseResult::default();

    extract_functions(&root, source_bytes, path, &mut result)?;
    extract_structs_and_traits(&root, source_bytes, path, &mut result)?;
    extract_imports(&root, source_bytes, &mut result)?;
    extract_calls(&root, source_bytes, path, &mut result)?;

    Ok(result)
}

/// Extract function definitions from the AST
fn extract_functions(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    let query_str = r#"
        (function_item
            name: (identifier) @func_name
            parameters: (parameters) @params
            return_type: (_)? @return_type
        ) @func

        (function_signature_item
            name: (identifier) @func_name
            parameters: (parameters) @params
            return_type: (_)? @return_type
        ) @func
    "#;

    let language = tree_sitter_rust::LANGUAGE;
    let query = Query::new(&language.into(), query_str).context("Failed to create function query")?;

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
            // Skip if this is inside an impl block (handled separately)
            if is_inside_impl(&node) {
                continue;
            }

            let is_async = has_async_modifier(&node, source);
            let parameters = extract_parameters(params_node, source);
            let return_type = return_type_node
                .map(|n| n.utf8_text(source).unwrap_or("").to_string());

            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;
            let qualified_name = format!("{}::{}:{}", path.display(), name, line_start);

            result.functions.push(Function {
                name: name.clone(),
                qualified_name,
                file_path: path.to_path_buf(),
                line_start,
                line_end,
                parameters,
                return_type,
                is_async,
                complexity: Some(calculate_complexity(&node, source)),
            });
        }
    }

    Ok(())
}

/// Check if a node has async modifier
fn has_async_modifier(node: &Node, source: &[u8]) -> bool {
    for child in node.children(&mut node.walk()) {
        if child.kind() == "async" {
            return true;
        }
        // Also check the text representation
        if let Ok(text) = child.utf8_text(source) {
            if text == "async" {
                return true;
            }
        }
    }
    false
}

/// Check if a node is inside an impl block
fn is_inside_impl(node: &Node) -> bool {
    let mut current = node.parent();
    while let Some(parent) = current {
        if parent.kind() == "impl_item" {
            return true;
        }
        current = parent.parent();
    }
    false
}

/// Extract parameter names from a parameters node
fn extract_parameters(params_node: Option<Node>, source: &[u8]) -> Vec<String> {
    let Some(node) = params_node else {
        return vec![];
    };

    let mut params = Vec::new();
    let mut cursor = node.walk();

    for child in node.children(&mut cursor) {
        match child.kind() {
            "parameter" => {
                // Get pattern (name) from parameter
                if let Some(pattern) = child.child_by_field_name("pattern") {
                    if let Ok(text) = pattern.utf8_text(source) {
                        params.push(text.to_string());
                    }
                }
            }
            "self_parameter" => {
                if let Ok(text) = child.utf8_text(source) {
                    params.push(text.to_string());
                }
            }
            _ => {}
        }
    }

    params
}

/// Extract structs, enums, traits, and impl blocks from the AST
fn extract_structs_and_traits(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    let mut cursor = root.walk();

    for node in root.children(&mut cursor) {
        match node.kind() {
            "struct_item" => {
                if let Some(class) = parse_struct_node(&node, source, path) {
                    result.classes.push(class);
                }
            }
            "enum_item" => {
                if let Some(class) = parse_enum_node(&node, source, path) {
                    result.classes.push(class);
                }
            }
            "trait_item" => {
                if let Some(class) = parse_trait_node(&node, source, path) {
                    result.classes.push(class);
                }
            }
            "impl_item" => {
                // Extract methods from impl blocks as functions
                extract_impl_methods(&node, source, path, result)?;
            }
            _ => {}
        }
    }

    Ok(())
}

/// Parse a struct into a Class struct
fn parse_struct_node(node: &Node, source: &[u8], path: &Path) -> Option<Class> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}:{}", path.display(), name, line_start);

    Some(Class {
        name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        methods: vec![],
        bases: vec![],
    })
}

/// Parse an enum into a Class struct
fn parse_enum_node(node: &Node, source: &[u8], path: &Path) -> Option<Class> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}:{}", path.display(), name, line_start);

    Some(Class {
        name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        methods: vec![],
        bases: vec![],
    })
}

/// Parse a trait into a Class struct
fn parse_trait_node(node: &Node, source: &[u8], path: &Path) -> Option<Class> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::trait::{}:{}", path.display(), name, line_start);

    // Extract method signatures from trait
    let methods = extract_trait_methods(node, source);

    // Extract supertraits (bounds)
    let bases = extract_trait_bounds(node, source);

    Some(Class {
        name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        methods,
        bases,
    })
}

/// Extract method names from a trait
fn extract_trait_methods(trait_node: &Node, source: &[u8]) -> Vec<String> {
    let mut methods = Vec::new();

    if let Some(body) = trait_node.child_by_field_name("body") {
        for child in body.children(&mut body.walk()) {
            if child.kind() == "function_item" || child.kind() == "function_signature_item" {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if let Ok(name) = name_node.utf8_text(source) {
                        methods.push(name.to_string());
                    }
                }
            }
        }
    }

    methods
}

/// Extract trait bounds (supertraits)
fn extract_trait_bounds(trait_node: &Node, source: &[u8]) -> Vec<String> {
    let mut bounds = Vec::new();

    if let Some(bounds_node) = trait_node.child_by_field_name("bounds") {
        for child in bounds_node.children(&mut bounds_node.walk()) {
            if child.kind() == "type_identifier" || child.kind() == "generic_type" {
                if let Ok(text) = child.utf8_text(source) {
                    bounds.push(text.to_string());
                }
            }
        }
    }

    bounds
}

/// Extract methods from impl blocks
fn extract_impl_methods(
    impl_node: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    // Get the type being implemented
    let type_name = impl_node
        .child_by_field_name("type")
        .and_then(|n| n.utf8_text(source).ok())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "Unknown".to_string());

    // Get the trait being implemented (if any)
    let trait_name = impl_node
        .child_by_field_name("trait")
        .and_then(|n| n.utf8_text(source).ok())
        .map(|s| s.to_string());

    let impl_line = impl_node.start_position().row as u32 + 1;

    // Extract methods from the impl body
    if let Some(body) = impl_node.child_by_field_name("body") {
        for child in body.children(&mut body.walk()) {
            if child.kind() == "function_item" {
                if let Some(func) = parse_impl_method(&child, source, path, &type_name, trait_name.as_deref(), impl_line) {
                    result.functions.push(func);
                }
            }
        }
    }

    Ok(())
}

/// Parse a method inside an impl block
fn parse_impl_method(
    node: &Node,
    source: &[u8],
    path: &Path,
    type_name: &str,
    trait_name: Option<&str>,
    _impl_line: u32,
) -> Option<Function> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let params_node = node.child_by_field_name("parameters");
    let parameters = extract_parameters(params_node, source);

    let return_type = node
        .child_by_field_name("return_type")
        .and_then(|n| n.utf8_text(source).ok())
        .map(|s| s.to_string());

    let is_async = has_async_modifier(node, source);

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;

    // Build qualified name including impl context
    let qualified_name = if let Some(trait_n) = trait_name {
        format!("{}::impl<{} for {}>::{}:{}", path.display(), trait_n, type_name, name, line_start)
    } else {
        format!("{}::impl<{}>::{}:{}", path.display(), type_name, name, line_start)
    };

    Some(Function {
        name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        parameters,
        return_type,
        is_async,
        complexity: Some(calculate_complexity(node, source)),
    })
}

/// Extract use statements from the AST
fn extract_imports(root: &Node, source: &[u8], result: &mut ParseResult) -> Result<()> {
    let query_str = r#"
        (use_declaration
            argument: (_) @import_path
        )
    "#;

    let language = tree_sitter_rust::LANGUAGE;
    let query = Query::new(&language.into(), query_str).context("Failed to create import query")?;

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        for capture in m.captures.iter() {
            if let Ok(text) = capture.node.utf8_text(source) {
                // Clean up the import path
                let import = text.trim().to_string();
                if !import.is_empty() {
                    result.imports.push(ImportInfo::runtime(import));
                }
            }
        }
    }

    Ok(())
}

/// Extract function calls from the AST
fn extract_calls(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    // Build a map of function locations for call extraction
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

    // Also handle method calls
    if node.kind() == "method_call_expression" {
        let call_line = node.start_position().row as u32 + 1;
        // For top-level calls (outside any function), use the file path as the caller
        let caller = find_containing_scope(call_line, scope_map)
            .unwrap_or_else(|| path.display().to_string());

        if let Some(name_node) = node.child_by_field_name("name") {
            if let Ok(callee) = name_node.utf8_text(source) {
                result.calls.push((caller, callee.to_string()));
            }
        }
    }

    for child in node.children(&mut node.walk()) {
        extract_calls_recursive(&child, source, path, scope_map, result);
    }
}

/// Find which scope contains a given line
fn find_containing_scope(line: u32, scope_map: &HashMap<(u32, u32), String>) -> Option<String> {
    let mut best_match: Option<(&(u32, u32), &String)> = None;

    for (range, name) in scope_map {
        if line >= range.0 && line <= range.1 {
            match best_match {
                None => best_match = Some((range, name)),
                Some((best_range, _)) => {
                    if (range.1 - range.0) < (best_range.1 - best_range.0) {
                        best_match = Some((range, name));
                    }
                }
            }
        }
    }

    best_match.map(|(_, name)| name.clone())
}

/// Extract the target of a function call
fn extract_call_target(node: &Node, source: &[u8]) -> Option<String> {
    match node.kind() {
        "identifier" => node.utf8_text(source).ok().map(|s| s.to_string()),
        "scoped_identifier" => node.utf8_text(source).ok().map(|s| s.to_string()),
        "field_expression" => node.utf8_text(source).ok().map(|s| s.to_string()),
        "generic_function" => {
            // func::<T>() - get the function name
            node.child_by_field_name("function")
                .and_then(|n| extract_call_target(&n, source))
        }
        _ => node.utf8_text(source).ok().map(|s| s.to_string()),
    }
}

/// Calculate cyclomatic complexity of a function
fn calculate_complexity(node: &Node, _source: &[u8]) -> u32 {
    let mut complexity = 1;

    fn count_branches(node: &Node, complexity: &mut u32) {
        match node.kind() {
            "if_expression" | "else_clause" | "while_expression" | "for_expression" | "loop_expression" => {
                *complexity += 1;
            }
            "match_arm" => {
                *complexity += 1;
            }
            "binary_expression" => {
                // Check for && or ||
                for child in node.children(&mut node.walk()) {
                    if child.kind() == "&&" || child.kind() == "||" {
                        *complexity += 1;
                    }
                }
            }
            "?" => {
                // ? operator for error handling
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
fn hello(name: &str) -> String {
    format!("Hello, {}!", name)
}
"#;
        let path = PathBuf::from("test.rs");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.functions.len(), 1);
        let func = &result.functions[0];
        assert_eq!(func.name, "hello");
        assert!(!func.is_async);
    }

    #[test]
    fn test_parse_async_function() {
        let source = r#"
async fn fetch_data(url: &str) -> Result<String, Error> {
    Ok(String::new())
}
"#;
        let path = PathBuf::from("test.rs");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.functions.len(), 1);
        let func = &result.functions[0];
        assert_eq!(func.name, "fetch_data");
        assert!(func.is_async);
    }

    #[test]
    fn test_parse_struct() {
        let source = r#"
struct MyStruct {
    field: i32,
}
"#;
        let path = PathBuf::from("test.rs");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.classes.len(), 1);
        let class = &result.classes[0];
        assert_eq!(class.name, "MyStruct");
    }

    #[test]
    fn test_parse_impl_methods() {
        let source = r#"
struct MyStruct;

impl MyStruct {
    fn new() -> Self {
        MyStruct
    }

    fn method(&self, x: i32) -> i32 {
        x * 2
    }
}
"#;
        let path = PathBuf::from("test.rs");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.functions.len(), 2);
        assert!(result.functions.iter().any(|f| f.name == "new"));
        assert!(result.functions.iter().any(|f| f.name == "method"));
    }

    #[test]
    fn test_parse_imports() {
        let source = r#"
use std::collections::HashMap;
use crate::models::Function;
use super::{ImportInfo, ParseResult};
"#;
        let path = PathBuf::from("test.rs");
        let result = parse_source(source, &path).unwrap();

        assert!(result.imports.len() >= 3);
    }

    #[test]
    fn test_parse_trait() {
        let source = r#"
trait MyTrait: Clone + Send {
    fn required_method(&self);
    fn provided_method(&self) -> i32 {
        42
    }
}
"#;
        let path = PathBuf::from("test.rs");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.classes.len(), 1);
        let trait_def = &result.classes[0];
        assert_eq!(trait_def.name, "MyTrait");
        assert!(trait_def.methods.contains(&"required_method".to_string()));
        assert!(trait_def.methods.contains(&"provided_method".to_string()));
    }
}
