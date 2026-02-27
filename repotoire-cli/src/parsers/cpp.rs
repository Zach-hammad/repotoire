//! C++ parser using tree-sitter
//!
//! Extracts functions, classes, methods, structs, namespaces, imports, and call relationships from C++ source code.

use crate::models::{Class, Function};
use crate::parsers::{ImportInfo, ParseResult};
use anyhow::{Context, Result};
use std::path::Path;
use tree_sitter::{Node, Parser, Query, QueryCursor, StreamingIterator};

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

/// Check if a function definition has a specific storage class specifier (e.g., "extern", "static")
fn has_storage_class(func_node: &Node, source: &[u8], specifier: &str) -> bool {
    for child in func_node.children(&mut func_node.walk()) {
        if child.kind() == "storage_class_specifier" {
            if let Ok(text) = child.utf8_text(source) {
                if text == specifier {
                    return true;
                }
            }
        }
    }
    false
}

/// Check if a node is inside a class or struct body (field_declaration_list)
fn is_inside_class_body(node: &Node) -> bool {
    let mut current = node.parent();
    while let Some(parent) = current {
        if parent.kind() == "field_declaration_list" {
            return true;
        }
        current = parent.parent();
    }
    false
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
            // Skip methods inside class/struct bodies — handled by extract_class_methods
            if is_inside_class_body(&node) {
                continue;
            }
            let parameters = extract_parameters(params_node, source);
            let return_type =
                return_type_node.map(|n| n.utf8_text(source).unwrap_or("").to_string());

            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;
            let qualified_name = format!("{}::{}:{}", path.display(), name, line_start);

            let annotations = if has_storage_class(&node, source, "extern") {
                vec!["exported".to_string()]
            } else {
                vec![]
            };

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
                max_nesting: None,
                doc_comment: None,
                annotations,
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
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
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

            // Extract base classes
            let bases = extract_base_classes(&node, source);

            // Extract methods from class body (class default = private)
            let methods = if let Some(body) = body_node {
                extract_class_methods(&body, source, path, &name, "private")?
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
                bases,
                methods: methods.iter().map(|m| m.name.clone()).collect(),
                doc_comment: None,
                annotations: vec![],
            });
        }
    }

    Ok(())
}

/// Extract base classes from a class/struct specifier node
fn extract_base_classes(class_node: &Node, source: &[u8]) -> Vec<String> {
    let mut bases = vec![];
    for child in class_node.children(&mut class_node.walk()) {
        if child.kind() == "base_class_clause" {
            // base_class_clause children include access specifiers and type identifiers
            for base_child in child.children(&mut child.walk()) {
                if base_child.kind() == "type_identifier"
                    || base_child.kind() == "qualified_identifier"
                {
                    if let Ok(text) = base_child.utf8_text(source) {
                        bases.push(text.to_string());
                    }
                }
            }
        }
    }
    bases
}

/// Build a map from method start byte to its access level by walking the field_declaration_list
fn build_access_map(body: &Node, source: &[u8], default_access: &str) -> std::collections::HashMap<usize, String> {
    let mut access_map = std::collections::HashMap::new();
    let mut current_access = default_access.to_string();

    for child in body.children(&mut body.walk()) {
        if child.kind() == "access_specifier" {
            // Text is e.g. "public:" — strip the colon
            if let Ok(text) = child.utf8_text(source) {
                current_access = text.trim_end_matches(':').trim().to_string();
            }
        } else if child.kind() == "function_definition" || child.kind() == "declaration" {
            access_map.insert(child.start_byte(), current_access.clone());
        }
    }

    access_map
}

/// Extract methods from a class body
fn extract_class_methods(
    body: &Node,
    source: &[u8],
    path: &Path,
    class_name: &str,
    default_access: &str,
) -> Result<Vec<Function>> {
    let mut methods = vec![];

    // Build access map by walking siblings sequentially
    let access_map = build_access_map(body, source, default_access);

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
    let mut matches = cursor.matches(&query, *body, source);

    while let Some(m) = matches.next() {
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

            // Determine access level from pre-built map
            let access = access_map
                .get(&node.start_byte())
                .map(|s| s.as_str())
                .unwrap_or(default_access);

            let annotations = if access == "public" {
                vec!["exported".to_string()]
            } else {
                vec![]
            };

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
                max_nesting: None,
                doc_comment: None,
                annotations,
            });
        }
    }

    Ok(methods)
}

/// Extract struct definitions (similar to classes in C++, but default access is public)
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
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        let mut struct_node = None;
        let mut name = String::new();
        let mut body_node = None;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "struct" => struct_node = Some(capture.node),
                "struct_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                "body" => body_node = Some(capture.node),
                _ => {}
            }
        }

        if let Some(node) = struct_node {
            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;
            let qualified_name = format!("{}::{}", path.display(), name);

            // Extract base classes
            let bases = extract_base_classes(&node, source);

            // Extract methods (struct default = public)
            let methods = if let Some(body) = body_node {
                extract_class_methods(&body, source, path, &name, "public")?
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
                bases,
                methods: methods.iter().map(|m| m.name.clone()).collect(),
                doc_comment: None,
                annotations: vec![],
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
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
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
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
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
        let result = parse_source(source, &path).expect("should parse C++ source");

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
        let result = parse_source(source, &path).expect("should parse C++ source");

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
        let result = parse_source(source, &path).expect("should parse C++ source");

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
        let result = parse_source(source, &path).expect("should parse C++ source");
        let c = result.functions[0].complexity.unwrap_or(0);
        // Base + switch branches should be counted (at least cases/default)
        assert!(
            c >= 3,
            "expected switch branches to increase complexity, got {c}"
        );
    }

    #[test]
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
        let result = parse_source(source, &path).expect("should parse C++ source");

        assert_eq!(result.functions.len(), 1);
        assert!(result.functions[0].complexity.unwrap_or(0) >= 5); // Multiple branches
    }

    #[test]
    fn test_public_methods_exported() {
        let source = r#"
class MyClass {
public:
    int public_method(int x) {
        return x;
    }

private:
    int private_method(int x) {
        return x;
    }

protected:
    int protected_method(int x) {
        return x;
    }
};
"#;
        let path = PathBuf::from("test.cpp");
        let result = parse_source(source, &path).expect("should parse C++ source");

        let public_fn = result
            .functions
            .iter()
            .find(|f| f.name == "public_method")
            .expect("should find public_method");
        assert!(
            public_fn.annotations.contains(&"exported".to_string()),
            "public method should be exported"
        );

        let private_fn = result
            .functions
            .iter()
            .find(|f| f.name == "private_method")
            .expect("should find private_method");
        assert!(
            private_fn.annotations.is_empty(),
            "private method should not be exported"
        );

        let protected_fn = result
            .functions
            .iter()
            .find(|f| f.name == "protected_method")
            .expect("should find protected_method");
        assert!(
            protected_fn.annotations.is_empty(),
            "protected method should not be exported"
        );
    }

    #[test]
    fn test_class_default_private() {
        let source = r#"
class Foo {
    int implicit_private(int x) {
        return x;
    }
};
"#;
        let path = PathBuf::from("test.cpp");
        let result = parse_source(source, &path).expect("should parse C++ source");

        let func = result
            .functions
            .iter()
            .find(|f| f.name == "implicit_private")
            .expect("should find implicit_private");
        assert!(
            func.annotations.is_empty(),
            "class methods without access specifier should default to private (not exported)"
        );
    }

    #[test]
    fn test_struct_methods_default_public() {
        let source = r#"
struct Bar {
    int implicit_public(int x) {
        return x;
    }

private:
    int explicit_private(int x) {
        return x;
    }
};
"#;
        let path = PathBuf::from("test.cpp");
        let result = parse_source(source, &path).expect("should parse C++ source");

        let public_fn = result
            .functions
            .iter()
            .find(|f| f.name == "implicit_public")
            .expect("should find implicit_public");
        assert!(
            public_fn.annotations.contains(&"exported".to_string()),
            "struct methods without access specifier should default to public (exported)"
        );

        let private_fn = result
            .functions
            .iter()
            .find(|f| f.name == "explicit_private")
            .expect("should find explicit_private");
        assert!(
            private_fn.annotations.is_empty(),
            "struct method after private: should not be exported"
        );
    }

    #[test]
    fn test_base_class_extraction() {
        let source = r#"
class Base {};

class Derived : public Base {
public:
    int method() {
        return 0;
    }
};
"#;
        let path = PathBuf::from("test.cpp");
        let result = parse_source(source, &path).expect("should parse C++ source");

        let derived = result
            .classes
            .iter()
            .find(|c| c.name == "Derived")
            .expect("should find Derived class");
        assert_eq!(derived.bases, vec!["Base"]);
    }

    #[test]
    fn test_extern_free_function_exported() {
        let source = r#"
extern int api_func(int x) {
    return x;
}

int internal_func(int x) {
    return x;
}
"#;
        let path = PathBuf::from("test.cpp");
        let result = parse_source(source, &path).expect("should parse C++ source");

        let api = result
            .functions
            .iter()
            .find(|f| f.name == "api_func")
            .expect("should find api_func");
        assert!(
            api.annotations.contains(&"exported".to_string()),
            "extern free function should be exported"
        );

        let internal = result
            .functions
            .iter()
            .find(|f| f.name == "internal_func")
            .expect("should find internal_func");
        assert!(
            internal.annotations.is_empty(),
            "plain free function should not be exported"
        );
    }
}
