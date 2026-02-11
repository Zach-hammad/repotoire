//! C parser using tree-sitter
//!
//! Extracts functions, structs, typedefs, imports, and call relationships from C source code.

use crate::models::{Class, Function};
use crate::parsers::{ImportInfo, ParseResult};
use anyhow::{Context, Result};
use std::collections::HashMap;
use std::path::Path;
use tree_sitter::{Node, Parser, Query, QueryCursor};

/// Parse a C file and extract all code entities
pub fn parse(path: &Path) -> Result<ParseResult> {
    let source = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read file: {}", path.display()))?;

    parse_source(&source, path)
}

/// Parse C source code directly (useful for testing)
pub fn parse_source(source: &str, path: &Path) -> Result<ParseResult> {
    let mut parser = Parser::new();
    let language = tree_sitter_c::LANGUAGE;
    parser
        .set_language(&language.into())
        .context("Failed to set C language")?;

    let tree = parser
        .parse(source, None)
        .context("Failed to parse C source")?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();

    let mut result = ParseResult::default();

    extract_functions(&root, source_bytes, path, &mut result)?;
    extract_structs(&root, source_bytes, path, &mut result)?;
    extract_includes(&root, source_bytes, &mut result)?;
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
        (function_definition
            type: (_) @return_type
            declarator: (function_declarator
                declarator: (_) @func_name
                parameters: (parameter_list) @params
            )
        ) @func
    "#;

    let language = tree_sitter_c::LANGUAGE;
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
                    let name_text = capture.node.utf8_text(source).unwrap_or("");
                    name = name_text.trim_start_matches('*').to_string();
                }
                "params" => params_node = Some(capture.node),
                "return_type" => return_type_node = Some(capture.node),
                _ => {}
            }
        }

        if let Some(node) = func_node {
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
                is_async: false,
                complexity: Some(calculate_complexity(&node, source)),
            });
        }
    }

    Ok(())
}

/// Extract parameter names from a parameter list
fn extract_parameters(params_node: Option<Node>, source: &[u8]) -> Vec<String> {
    let Some(node) = params_node else {
        return vec![];
    };

    let mut params = Vec::new();

    for child in node.children(&mut node.walk()) {
        if child.kind() == "parameter_declaration" {
            // Try to find the declarator (parameter name)
            if let Some(name) = find_parameter_name(&child, source) {
                params.push(name);
            }
        }
    }

    params
}

/// Find the parameter name from a parameter declaration
fn find_parameter_name(param_node: &Node, source: &[u8]) -> Option<String> {
    // Look for identifier in declarator
    for child in param_node.children(&mut param_node.walk()) {
        match child.kind() {
            "identifier" => {
                return child.utf8_text(source).ok().map(|s| s.to_string());
            }
            "pointer_declarator" | "array_declarator" => {
                return find_declarator_name(&child, source);
            }
            _ => {}
        }
    }
    None
}

/// Find the name from a declarator node
fn find_declarator_name(node: &Node, source: &[u8]) -> Option<String> {
    for child in node.children(&mut node.walk()) {
        if child.kind() == "identifier" {
            return child.utf8_text(source).ok().map(|s| s.to_string());
        }
        if child.kind() == "pointer_declarator" || child.kind() == "array_declarator" {
            return find_declarator_name(&child, source);
        }
    }
    None
}

/// Extract struct definitions from the AST
fn extract_structs(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    let query_str = r#"
        (struct_specifier
            name: (type_identifier) @struct_name
        ) @struct_def

        (type_definition
            type: (struct_specifier
                name: (type_identifier)? @struct_name
            )
            declarator: (type_identifier) @typedef_name
        ) @typedef_struct

        (enum_specifier
            name: (type_identifier) @enum_name
        ) @enum_def
    "#;

    let language = tree_sitter_c::LANGUAGE;
    let query = Query::new(&language.into(), query_str).context("Failed to create struct query")?;

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        let mut node = None;
        let mut name = String::new();
        let mut is_typedef = false;
        let mut is_enum = false;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "struct_def" => node = Some(capture.node),
                "typedef_struct" => {
                    node = Some(capture.node);
                    is_typedef = true;
                }
                "enum_def" => {
                    node = Some(capture.node);
                    is_enum = true;
                }
                "struct_name" | "enum_name" => {
                    if name.is_empty() {
                        name = capture.node.utf8_text(source).unwrap_or("").to_string();
                    }
                }
                "typedef_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                _ => {}
            }
        }

        if let Some(def_node) = node {
            if !name.is_empty() {
                let line_start = def_node.start_position().row as u32 + 1;
                let line_end = def_node.end_position().row as u32 + 1;

                let kind = if is_enum {
                    "enum"
                } else if is_typedef {
                    "typedef"
                } else {
                    "struct"
                };

                let qualified_name = format!("{}::{}::{}:{}", path.display(), kind, name, line_start);

                result.classes.push(Class {
                    name: name.clone(),
                    qualified_name,
                    file_path: path.to_path_buf(),
                    line_start,
                    line_end,
                    methods: vec![],
                    bases: vec![],
                });
            }
        }
    }

    Ok(())
}

/// Extract #include statements from the AST
fn extract_includes(root: &Node, source: &[u8], result: &mut ParseResult) -> Result<()> {
    let query_str = r#"
        (preproc_include
            path: (_) @include_path
        )
    "#;

    let language = tree_sitter_c::LANGUAGE;
    let query = Query::new(&language.into(), query_str).context("Failed to create include query")?;

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        for capture in m.captures.iter() {
            if let Ok(text) = capture.node.utf8_text(source) {
                // Remove quotes or angle brackets
                let import = text
                    .trim_start_matches(|c| c == '"' || c == '<')
                    .trim_end_matches(|c| c == '"' || c == '>')
                    .to_string();
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
        "field_expression" => {
            // struct->member or struct.member
            node.utf8_text(source).ok().map(|s| s.to_string())
        }
        "parenthesized_expression" => {
            // Function pointer calls
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

/// Calculate cyclomatic complexity of a function
fn calculate_complexity(node: &Node, _source: &[u8]) -> u32 {
    let mut complexity = 1;

    fn count_branches(node: &Node, complexity: &mut u32) {
        match node.kind() {
            "if_statement" | "while_statement" | "for_statement" | "do_statement" => {
                *complexity += 1;
            }
            "case_statement" | "default_statement" => {
                *complexity += 1;
            }
            "conditional_expression" => {
                *complexity += 1;
            }
            "binary_expression" => {
                for child in node.children(&mut node.walk()) {
                    if child.kind() == "&&" || child.kind() == "||" {
                        *complexity += 1;
                    }
                }
            }
            "goto_statement" => {
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
int add(int a, int b) {
    return a + b;
}
"#;
        let path = PathBuf::from("test.c");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.functions.len(), 1);
        let func = &result.functions[0];
        assert_eq!(func.name, "add");
        assert_eq!(func.parameters, vec!["a", "b"]);
    }

    #[test]
    fn test_parse_void_function() {
        let source = r#"
void print_message(const char* msg) {
    printf("%s\n", msg);
}
"#;
        let path = PathBuf::from("test.c");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.functions.len(), 1);
        let func = &result.functions[0];
        assert_eq!(func.name, "print_message");
    }

    #[test]
    fn test_parse_struct() {
        let source = r#"
struct Point {
    int x;
    int y;
};
"#;
        let path = PathBuf::from("test.c");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.classes.len(), 1);
        let class = &result.classes[0];
        assert_eq!(class.name, "Point");
    }

    #[test]
    fn test_parse_typedef_struct() {
        let source = r#"
typedef struct {
    int width;
    int height;
} Rectangle;
"#;
        let path = PathBuf::from("test.c");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.classes.len(), 1);
        let class = &result.classes[0];
        assert_eq!(class.name, "Rectangle");
    }

    #[test]
    fn test_parse_includes() {
        let source = r#"
#include <stdio.h>
#include <stdlib.h>
#include "myheader.h"

int main() {
    return 0;
}
"#;
        let path = PathBuf::from("test.c");
        let result = parse_source(source, &path).unwrap();

        assert!(result.imports.iter().any(|i| i.path == "stdio.h"));
        assert!(result.imports.iter().any(|i| i.path == "stdlib.h"));
        assert!(result.imports.iter().any(|i| i.path == "myheader.h"));
    }

    #[test]
    fn test_parse_calls() {
        let source = r#"
void helper() {}

void caller() {
    helper();
    printf("done");
}
"#;
        let path = PathBuf::from("test.c");
        let result = parse_source(source, &path).unwrap();

        assert!(!result.calls.is_empty());
        let call_targets: Vec<&str> = result.calls.iter().map(|(_, t)| t.as_str()).collect();
        assert!(call_targets.contains(&"helper"));
    }

    #[test]
    fn test_complexity() {
        let source = r#"
int complex_func(int x) {
    if (x > 0) {
        if (x > 10) {
            return 2;
        }
        return 1;
    } else if (x < 0) {
        return -1;
    }
    return 0;
}
"#;
        let path = PathBuf::from("test.c");
        let result = parse_source(source, &path).unwrap();

        let func = &result.functions[0];
        assert!(func.complexity.unwrap() >= 3);
    }
}
