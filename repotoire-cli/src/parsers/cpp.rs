//! C++ parser using tree-sitter
//!
//! Extracts functions, classes, methods, structs, namespaces, imports, and call relationships from C++ source code.

use crate::models::{Class, Function};
use crate::parsers::{ImportInfo, ParseResult};
use anyhow::{Context, Result};
use std::path::Path;
use tree_sitter::{Node, Parser, Query, QueryCursor};

/// Parse a C++ file and extract all code entities
pub fn parse(path: &Path) -> Result<ParseResult> {
    let source = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read file: {}", path.display()))?;

    parse_source(&source, path)
}

/// Parse C++ source code directly (useful for testing)
pub fn parse_source(source: &str, path: &Path) -> Result<ParseResult> {
    let mut parser = Parser::new();
    let language = tree_sitter_cpp::LANGUAGE;
    parser
        .set_language(&language.into())
        .context("Failed to set C++ language")?;

    let tree = parser
        .parse(source, None)
        .context("Failed to parse C++ source")?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();

    let mut result = ParseResult::default();

    extract_functions(&root, source_bytes, path, &mut result)?;
    extract_classes(&root, source_bytes, path, &mut result)?;
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

    let language = tree_sitter_cpp::LANGUAGE;
    let query =
        Query::new(&language.into(), query_str).context("Failed to create function query")?;

    let mut cursor = QueryCursor::new();
    let matches = cursor.matches(&query, *root, source);

    for m in matches {
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
                    // Handle qualified names like ClassName::methodName
                    name = name_text.trim_start_matches('*').to_string();
                    if name.contains("::") {
                        // Skip class method definitions here, they're handled in extract_classes
                        continue;
                    }
                }
                "params" => params_node = Some(capture.node),
                "return_type" => return_type_node = Some(capture.node),
                _ => {}
            }
        }

        if name.is_empty() {
            continue;
        }

        if let Some(node) = func_node {
            let parameters = extract_parameters(params_node, source);
            let return_type =
                return_type_node.map(|n| n.utf8_text(source).unwrap_or("").to_string());

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

/// Extract C++ class definitions
fn extract_classes(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    let query_str = r#"
        (class_specifier
            name: (type_identifier) @class_name
            body: (field_declaration_list) @body
        ) @class
    "#;

    let language = tree_sitter_cpp::LANGUAGE;
    let query = Query::new(&language.into(), query_str).context("Failed to create class query")?;

    let mut cursor = QueryCursor::new();
    let matches = cursor.matches(&query, *root, source);

    for m in matches {
        let mut class_node = None;
        let mut name = String::new();
        let mut body_node = None;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "class" => class_node = Some(capture.node),
                "class_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                "body" => body_node = Some(capture.node),
                _ => {}
            }
        }

        if let Some(node) = class_node {
            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;
            let qualified_name = format!("{}::{}", path.display(), name);

            // Extract methods from class body
            let methods = if let Some(body) = body_node {
                extract_class_methods(&body, source, path, &name)?
            } else {
                vec![]
            };

            // Add methods to functions list
            for method in &methods {
                result.functions.push(method.clone());
            }

            result.classes.push(Class {
                name: name.clone(),
                qualified_name,
                file_path: path.to_path_buf(),
                line_start,
                line_end,
                bases: vec![], // TODO: extract base classes
                methods: methods.iter().map(|m| m.name.clone()).collect(),
            });
        }
    }

    Ok(())
}

/// Extract methods from a class body
fn extract_class_methods(
    body: &Node,
    source: &[u8],
    path: &Path,
    class_name: &str,
) -> Result<Vec<Function>> {
    let mut methods = vec![];

    let query_str = r#"
        (function_definition
            type: (_) @return_type
            declarator: (function_declarator
                declarator: (_) @method_name
                parameters: (parameter_list) @params
            )
        ) @method
    "#;

    let language = tree_sitter_cpp::LANGUAGE;
    let query = Query::new(&language.into(), query_str).context("Failed to create method query")?;

    let mut cursor = QueryCursor::new();
    let matches = cursor.matches(&query, *body, source);

    for m in matches {
        let mut method_node = None;
        let mut name = String::new();
        let mut params_node = None;
        let mut return_type_node = None;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "method" => method_node = Some(capture.node),
                "method_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                "params" => params_node = Some(capture.node),
                "return_type" => return_type_node = Some(capture.node),
                _ => {}
            }
        }

        if let Some(node) = method_node {
            let parameters = extract_parameters(params_node, source);
            let return_type =
                return_type_node.map(|n| n.utf8_text(source).unwrap_or("").to_string());

            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;
            let qualified_name = format!(
                "{}::{}::{}:{}",
                path.display(),
                class_name,
                name,
                line_start
            );

            methods.push(Function {
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

    Ok(methods)
}

/// Extract struct definitions (similar to classes in C++)
fn extract_structs(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
) -> Result<()> {
    let query_str = r#"
        (struct_specifier
            name: (type_identifier) @struct_name
            body: (field_declaration_list)? @body
        ) @struct
    "#;

    let language = tree_sitter_cpp::LANGUAGE;
    let query = Query::new(&language.into(), query_str).context("Failed to create struct query")?;

    let mut cursor = QueryCursor::new();
    let matches = cursor.matches(&query, *root, source);

    for m in matches {
        let mut struct_node = None;
        let mut name = String::new();

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "struct" => struct_node = Some(capture.node),
                "struct_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                _ => {}
            }
        }

        if let Some(node) = struct_node {
            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;
            let qualified_name = format!("{}::{}", path.display(), name);

            result.classes.push(Class {
                name: name.clone(),
                qualified_name,
                file_path: path.to_path_buf(),
                line_start,
                line_end,
                bases: vec![],
                methods: vec![],
            });
        }
    }

    Ok(())
}

/// Extract #include statements
fn extract_includes(root: &Node, source: &[u8], result: &mut ParseResult) -> Result<()> {
    let query_str = r#"
        (preproc_include
            path: [
                (string_literal) @path
                (system_lib_string) @system_path
            ]
        )
    "#;

    let language = tree_sitter_cpp::LANGUAGE;
    let query =
        Query::new(&language.into(), query_str).context("Failed to create include query")?;

    let mut cursor = QueryCursor::new();
    let matches = cursor.matches(&query, *root, source);

    for m in matches {
        for capture in m.captures.iter() {
            let path_text = capture.node.utf8_text(source).unwrap_or("");
            // Remove quotes or angle brackets
            let import_path = path_text
                .trim_matches('"')
                .trim_matches('<')
                .trim_matches('>')
                .to_string();
            result.imports.push(ImportInfo::runtime(import_path));
        }
    }

    Ok(())
}

/// Extract function calls
fn extract_calls(root: &Node, source: &[u8], path: &Path, result: &mut ParseResult) -> Result<()> {
    let query_str = r#"
        (call_expression
            function: [
                (identifier) @func_name
                (field_expression
                    field: (field_identifier) @method_name
                )
                (qualified_identifier) @qualified_name
            ]
        ) @call
    "#;

    let language = tree_sitter_cpp::LANGUAGE;
    let query = Query::new(&language.into(), query_str).context("Failed to create call query")?;

    let mut cursor = QueryCursor::new();
    let matches = cursor.matches(&query, *root, source);

    for m in matches {
        let mut call_node = None;
        let mut callee_name = String::new();

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "call" => call_node = Some(capture.node),
                "func_name" | "method_name" | "qualified_name" => {
                    callee_name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                _ => {}
            }
        }

        if let Some(node) = call_node {
            // Find the enclosing function
            let caller = find_enclosing_function(&node, source, path);
            let _callee_line = node.start_position().row as u32 + 1;

            result.calls.push((
                caller,
                callee_name.clone(), // Use just the name, not path:name:line (#9)
            ));
        }
    }

    Ok(())
}

/// Find the enclosing function for a node
fn find_enclosing_function(node: &Node, source: &[u8], path: &Path) -> String {
    let mut current = node.parent();
    while let Some(parent) = current {
        if parent.kind() == "function_definition" {
            // Get function name from declarator
            if let Some(declarator) = parent.child_by_field_name("declarator") {
                if let Some(name_node) = declarator.child_by_field_name("declarator") {
                    let name = name_node.utf8_text(source).unwrap_or("unknown");
                    let line = parent.start_position().row as u32 + 1;
                    return format!("{}::{}:{}", path.display(), name, line);
                }
            }
        }
        current = parent.parent();
    }
    format!("{}::<global>", path.display())
}

/// Extract function parameters
fn extract_parameters(params_node: Option<Node>, source: &[u8]) -> Vec<String> {
    let Some(params) = params_node else {
        return vec![];
    };

    let mut parameters = vec![];
    let mut cursor = params.walk();

    for child in params.children(&mut cursor) {
        match child.kind() {
            "parameter_declaration" | "optional_parameter_declaration" => {
                // Try to get the parameter name
                if let Some(declarator) = child.child_by_field_name("declarator") {
                    let name = declarator.utf8_text(source).unwrap_or("");
                    let name = name.trim_start_matches('*').trim_start_matches('&');
                    if !name.is_empty() {
                        parameters.push(name.to_string());
                    }
                }
            }
            _ => {}
        }
    }

    parameters
}

/// Calculate cyclomatic complexity of a function
fn calculate_complexity(node: &Node, _source: &[u8]) -> u32 {
    // Keep logic consistent with other language parsers (#32)
    let mut complexity = 1u32;

    fn count_branches(node: &Node, complexity: &mut u32) {
        match node.kind() {
            "if_statement" | "for_statement" | "while_statement" | "do_statement" => {
                *complexity += 1;
            }
            "case_statement" | "default_statement" | "case_label" | "default_label" => {
                *complexity += 1;
            }
            "conditional_expression" | "catch_clause" => {
                *complexity += 1;
            }
            "binary_expression" => {
                for child in node.children(&mut node.walk()) {
                    if child.kind() == "&&" || child.kind() == "||" {
                        *complexity += 1;
                    }
                }
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
        let path = PathBuf::from("test.cpp");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.functions.len(), 1);
        assert_eq!(result.functions[0].name, "add");
        assert_eq!(result.functions[0].parameters, vec!["a", "b"]);
    }

    #[test]
    fn test_parse_class() {
        let source = r#"
class Calculator {
public:
    int add(int a, int b) {
        return a + b;
    }

    int subtract(int a, int b) {
        return a - b;
    }
};
"#;
        let path = PathBuf::from("test.cpp");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.classes.len(), 1);
        assert_eq!(result.classes[0].name, "Calculator");
    }

    #[test]
    fn test_parse_includes() {
        let source = r#"
#include <iostream>
#include <vector>
#include "myheader.h"

int main() {
    return 0;
}
"#;
        let path = PathBuf::from("test.cpp");
        let result = parse_source(source, &path).unwrap();

        assert!(result.imports.iter().any(|i| i.path == "iostream"));
        assert!(result.imports.iter().any(|i| i.path == "vector"));
        assert!(result.imports.iter().any(|i| i.path == "myheader.h"));
    }

    #[test]
    fn test_complexity_switch_counts_cases_not_switch() {
        let source = r#"
int classify(int x) {
    switch (x) {
        case 1: return 1;
        case 2: return 2;
        default: return 0;
    }
}
"#;
        let path = PathBuf::from("test.cpp");
        let result = parse_source(source, &path).unwrap();
        let c = result.functions[0].complexity.unwrap_or(0);
        // Base + switch branches should be counted (at least cases/default)
        assert!(
            c >= 3,
            "expected switch branches to increase complexity, got {c}"
        );
    }

    fn test_complexity() {
        let source = r#"
int complex(int x) {
    if (x > 0) {
        for (int i = 0; i < x; i++) {
            if (i % 2 == 0) {
                x++;
            }
        }
    } else if (x < 0) {
        while (x < 0) {
            x++;
        }
    }
    return x;
}
"#;
        let path = PathBuf::from("test.cpp");
        let result = parse_source(source, &path).unwrap();

        assert_eq!(result.functions.len(), 1);
        assert!(result.functions[0].complexity.unwrap_or(0) >= 5); // Multiple branches
    }
}
