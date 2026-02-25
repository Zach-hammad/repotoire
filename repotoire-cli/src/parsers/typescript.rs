//! TypeScript/JavaScript parser using tree-sitter
//!
//! Extracts functions, classes, interfaces, imports, and call relationships from TypeScript/JavaScript source code.

use crate::models::{Class, Function};
use crate::parsers::ParseResult;
use anyhow::{Context, Result};
use std::collections::HashMap;
use std::path::Path;
use std::sync::OnceLock;
use tree_sitter::{Language, Node, Parser, Query, QueryCursor, StreamingIterator};

/// Function query string (shared across languages)
const FUNC_QUERY_STR: &str = r#"
    (function_declaration
        name: (identifier) @func_name
        parameters: (formal_parameters) @params
    ) @func

    (generator_function_declaration
        name: (identifier) @func_name
        parameters: (formal_parameters) @params
    ) @func

    (arrow_function
        parameters: [(formal_parameters) (identifier)] @params
    ) @arrow_func

    (variable_declarator
        name: (identifier) @var_name
        value: (arrow_function
            parameters: [(formal_parameters) (identifier)] @params
        ) @arrow_func
    )

    (variable_declarator
        name: (identifier) @var_name
        value: (function_expression
            parameters: (formal_parameters) @params
        ) @func_expr
    )

    (export_statement
        declaration: (function_declaration
            name: (identifier) @func_name
            parameters: (formal_parameters) @params
        ) @func
    )
"#;

/// Cached queries for TypeScript
static TS_FUNC_QUERY: OnceLock<Query> = OnceLock::new();
static TS_CLASS_QUERY: OnceLock<Query> = OnceLock::new();
#[allow(dead_code)] // Prepared for import query caching
static TS_IMPORT_QUERY: OnceLock<Query> = OnceLock::new();

/// Cached queries for TSX
static TSX_FUNC_QUERY: OnceLock<Query> = OnceLock::new();
static TSX_CLASS_QUERY: OnceLock<Query> = OnceLock::new();
#[allow(dead_code)]
static TSX_IMPORT_QUERY: OnceLock<Query> = OnceLock::new();

/// Cached queries for JavaScript
static JS_FUNC_QUERY: OnceLock<Query> = OnceLock::new();
static JS_CLASS_QUERY: OnceLock<Query> = OnceLock::new();
#[allow(dead_code)]
static JS_IMPORT_QUERY: OnceLock<Query> = OnceLock::new();

/// Class query string for TypeScript
const TS_CLASS_QUERY_STR: &str = r#"
    (class_declaration
        name: (type_identifier) @class_name
    ) @class

    (interface_declaration
        name: (type_identifier) @iface_name
    ) @interface

    (type_alias_declaration
        name: (type_identifier) @type_name
    ) @type_alias

    (export_statement
        declaration: (class_declaration
            name: (type_identifier) @class_name
        ) @class
    )
"#;

/// Class query string for JavaScript
const JS_CLASS_QUERY_STR: &str = r#"
    (class_declaration
        name: (identifier) @class_name
    ) @class

    (export_statement
        declaration: (class_declaration
            name: (identifier) @class_name
        ) @class
    )
"#;

/// Import query string (shared)
#[allow(dead_code)] // Prepared for import resolution
const IMPORT_QUERY_STR: &str = r#"
    (import_statement
        source: (string) @import_path
    )
    
    (import_statement
        (import_clause
            (named_imports
                (import_specifier
                    name: (identifier) @import_name
                )
            )
        )
        source: (string) @import_path
    )
"#;

/// Get or create cached function query for an extension
fn get_func_query(ext: &str, language: &Language) -> &'static Query {
    match ext {
        "ts" => TS_FUNC_QUERY.get_or_init(|| Query::new(language, FUNC_QUERY_STR).unwrap()),
        "tsx" => TSX_FUNC_QUERY.get_or_init(|| Query::new(language, FUNC_QUERY_STR).unwrap()),
        _ => JS_FUNC_QUERY.get_or_init(|| Query::new(language, FUNC_QUERY_STR).unwrap()),
    }
}

/// Get or create cached class query for an extension
fn get_class_query(ext: &str, language: &Language) -> &'static Query {
    match ext {
        "ts" => TS_CLASS_QUERY.get_or_init(|| {
            Query::new(language, TS_CLASS_QUERY_STR)
                .unwrap_or_else(|_| Query::new(language, JS_CLASS_QUERY_STR).unwrap())
        }),
        "tsx" => TSX_CLASS_QUERY.get_or_init(|| {
            Query::new(language, TS_CLASS_QUERY_STR)
                .unwrap_or_else(|_| Query::new(language, JS_CLASS_QUERY_STR).unwrap())
        }),
        _ => JS_CLASS_QUERY.get_or_init(|| Query::new(language, JS_CLASS_QUERY_STR).unwrap()),
    }
}

/// Get or create cached import query for an extension
#[allow(dead_code)] // Prepared for import resolution
fn get_import_query(ext: &str, language: &Language) -> &'static Query {
    match ext {
        "ts" => TS_IMPORT_QUERY.get_or_init(|| Query::new(language, IMPORT_QUERY_STR).unwrap()),
        "tsx" => TSX_IMPORT_QUERY.get_or_init(|| Query::new(language, IMPORT_QUERY_STR).unwrap()),
        _ => JS_IMPORT_QUERY.get_or_init(|| Query::new(language, IMPORT_QUERY_STR).unwrap()),
    }
}

/// Parse a TypeScript/JavaScript file and extract all code entities
pub fn parse(path: &Path) -> Result<ParseResult> {
    let source = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read file: {}", path.display()))?;

    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
    parse_source(&source, path, ext)
}

/// Parse TypeScript/JavaScript source code directly
pub fn parse_source(source: &str, path: &Path, ext: &str) -> Result<ParseResult> {
    let mut parser = Parser::new();

    // Choose language based on extension
    let language: Language = match ext {
        "ts" => tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
        "tsx" => tree_sitter_typescript::LANGUAGE_TSX.into(),
        "js" | "jsx" | "mjs" | "cjs" => tree_sitter_javascript::LANGUAGE.into(),
        _ => tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
    };

    parser
        .set_language(&language)
        .context("Failed to set TypeScript/JavaScript language")?;

    let tree = parser
        .parse(source, None)
        .context("Failed to parse source")?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();

    let mut result = ParseResult::default();

    extract_functions(&root, source_bytes, path, &mut result, &language, ext)?;
    extract_classes(&root, source_bytes, path, &mut result, &language, ext)?;
    extract_imports(&root, source_bytes, &mut result, &language, ext)?;
    extract_calls(&root, source_bytes, path, &mut result)?;

    Ok(result)
}

/// Extract function definitions from the AST
fn extract_functions(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
    language: &Language,
    ext: &str,
) -> Result<()> {
    let query = get_func_query(ext, language);

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(query, *root, source);

    while let Some(m) = matches.next() {
        let mut func_node = None;
        let mut name = String::new();
        let mut params_node = None;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "func" | "arrow_func" | "func_expr" => func_node = Some(capture.node),
                "func_name" | "var_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                "params" => params_node = Some(capture.node),
                _ => {}
            }
        }

        if let Some(node) = func_node {
            // Skip if inside a class (handled separately)
            if is_inside_class(&node) {
                continue;
            }

            // For arrow functions without explicit names, try to find the variable name
            if name.is_empty() {
                if let Some(parent) = node.parent() {
                    if parent.kind() == "variable_declarator" {
                        if let Some(name_node) = parent.child_by_field_name("name") {
                            name = name_node.utf8_text(source).unwrap_or("").to_string();
                        }
                    }
                }
            }

            // Skip anonymous functions
            if name.is_empty() {
                continue;
            }

            let is_async = is_async_function(&node, source);
            let parameters = extract_parameters(params_node, source);
            let return_type = extract_return_type(&node, source);

            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;
            let qualified_name = format!("{}::{}:{}", path.display(), name, line_start);

            let doc_comment = extract_jsdoc_comment(&node, source);
            let mut annotations = Vec::new();
            if is_react_component(&node, source, &name) {
                annotations.push("react:component".to_string());
            }
            collect_hook_calls(&node, source, &mut annotations);

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
                max_nesting: None,
                doc_comment,
                annotations,
            });
        }
    }

    Ok(())
}

/// Check if a node is inside a class body
fn is_inside_class(node: &Node) -> bool {
    let mut current = node.parent();
    while let Some(parent) = current {
        if parent.kind() == "class_body" {
            return true;
        }
        current = parent.parent();
    }
    false
}

/// Check if function has async modifier
fn is_async_function(node: &Node, source: &[u8]) -> bool {
    // Check for async keyword in function text
    if let Ok(text) = node.utf8_text(source) {
        if text.starts_with("async ") || text.starts_with("async\n") {
            return true;
        }
    }

    // Check children for async keyword
    for child in node.children(&mut node.walk()) {
        if child.kind() == "async" {
            return true;
        }
    }

    false
}

/// Extract return type annotation if present
fn extract_return_type(func_node: &Node, source: &[u8]) -> Option<String> {
    // Look for type_annotation child
    for child in func_node.children(&mut func_node.walk()) {
        if child.kind() == "type_annotation" {
            return child.utf8_text(source).ok().map(|s| {
                // Remove the leading colon
                s.trim_start_matches(':').trim().to_string()
            });
        }
    }
    None
}

/// Extract parameter names from a formal_parameters node
fn extract_parameters(params_node: Option<Node>, source: &[u8]) -> Vec<String> {
    let Some(node) = params_node else {
        return vec![];
    };

    // If it's a single identifier (arrow function shorthand)
    if node.kind() == "identifier" {
        return node
            .utf8_text(source)
            .ok()
            .map(|s| vec![s.to_string()])
            .unwrap_or_default();
    }

    let mut params = Vec::new();

    for child in node.children(&mut node.walk()) {
        match child.kind() {
            "identifier" => {
                if let Ok(text) = child.utf8_text(source) {
                    params.push(text.to_string());
                }
            }
            "required_parameter" | "optional_parameter" => {
                // Get the pattern (name) from the parameter
                if let Some(pattern) = child.child_by_field_name("pattern") {
                    if let Ok(text) = pattern.utf8_text(source) {
                        let name = if child.kind() == "optional_parameter" {
                            format!("{}?", text)
                        } else {
                            text.to_string()
                        };
                        params.push(name);
                    }
                }
            }
            "rest_parameter" => {
                if let Some(pattern) = child.child_by_field_name("pattern") {
                    if let Ok(text) = pattern.utf8_text(source) {
                        params.push(format!("...{}", text));
                    }
                }
            }
            "assignment_pattern" => {
                // Default parameter
                if let Some(left) = child.child_by_field_name("left") {
                    if let Ok(text) = left.utf8_text(source) {
                        params.push(text.to_string());
                    }
                }
            }
            _ => {}
        }
    }

    params
}

/// Extract class and interface definitions from the AST
fn extract_classes(
    root: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
    language: &Language,
    ext: &str,
) -> Result<()> {
    let query = get_class_query(ext, language);

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(query, *root, source);

    while let Some(m) = matches.next() {
        let mut class_node = None;
        let mut name = String::new();
        let mut is_interface = false;
        let mut is_type_alias = false;

        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];
            match capture_name {
                "class" => class_node = Some(capture.node),
                "interface" => {
                    class_node = Some(capture.node);
                    is_interface = true;
                }
                "type_alias" => {
                    class_node = Some(capture.node);
                    is_type_alias = true;
                }
                "class_name" | "iface_name" | "type_name" => {
                    name = capture.node.utf8_text(source).unwrap_or("").to_string();
                }
                _ => {}
            }
        }

        if let Some(node) = class_node {
            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;

            let kind = if is_interface {
                "interface"
            } else if is_type_alias {
                "type"
            } else {
                "class"
            };

            let qualified_name = format!("{}::{}::{}:{}", path.display(), kind, name, line_start);

            let bases = if !is_type_alias {
                extract_class_heritage(&node, source)
            } else {
                vec![]
            };

            let methods = if !is_type_alias && !is_interface {
                extract_class_methods(&node, source, path, result, &name);
                extract_method_names(&node, source)
            } else if is_interface {
                extract_interface_methods(&node, source)
            } else {
                vec![]
            };

            let doc_comment = extract_jsdoc_comment(&node, source);

            result.classes.push(Class {
                name: name.clone(),
                qualified_name,
                file_path: path.to_path_buf(),
                line_start,
                line_end,
                methods,
                bases,
                doc_comment,
                annotations: vec![],
            });
        }
    }

    Ok(())
}

/// Extract heritage (extends/implements) from a class
fn extract_class_heritage(class_node: &Node, source: &[u8]) -> Vec<String> {
    let mut bases = Vec::new();

    for child in class_node.children(&mut class_node.walk()) {
        if child.kind() == "class_heritage" {
            for heritage_child in child.children(&mut child.walk()) {
                if heritage_child.kind() == "extends_clause"
                    || heritage_child.kind() == "implements_clause"
                {
                    for type_child in heritage_child.children(&mut heritage_child.walk()) {
                        if type_child.kind() == "type_identifier"
                            || type_child.kind() == "generic_type"
                        {
                            if let Ok(text) = type_child.utf8_text(source) {
                                bases.push(text.to_string());
                            }
                        }
                    }
                }
            }
        }
    }

    bases
}

/// Extract method names from a class body
/// NOTE: Only counts direct method definitions inside class_body, not nested closures/callbacks
fn extract_method_names(class_node: &Node, source: &[u8]) -> Vec<String> {
    let mut methods = Vec::new();

    if let Some(body) = class_node.child_by_field_name("body") {
        // Only iterate direct children of class_body - not nested functions
        for child in body.children(&mut body.walk()) {
            match child.kind() {
                "method_definition" => {
                    // Regular class method: foo() {} or async foo() {}
                    if let Some(name_node) = child.child_by_field_name("name") {
                        if let Ok(name) = name_node.utf8_text(source) {
                            methods.push(name.to_string());
                        }
                    }
                }
                "public_field_definition" => {
                    // Only count if the value is an arrow function (class field method pattern)
                    // e.g., foo = () => {} or foo = async () => {}
                    // Skip regular properties like: name = "value"
                    if let Some(value_node) = child.child_by_field_name("value") {
                        if value_node.kind() == "arrow_function" {
                            if let Some(name_node) = child.child_by_field_name("name") {
                                if let Ok(name) = name_node.utf8_text(source) {
                                    methods.push(name.to_string());
                                }
                            }
                        }
                    }
                }
                _ => {}
            }
        }
    }

    methods
}

/// Extract method signatures from an interface
/// NOTE: Only counts actual method signatures (with function type), NOT properties
/// Properties like `name: string` are data, not behavior - they shouldn't affect
/// the "method count" used by GodClass detection
fn extract_interface_methods(iface_node: &Node, source: &[u8]) -> Vec<String> {
    let mut methods = Vec::new();

    if let Some(body) = iface_node.child_by_field_name("body") {
        for child in body.children(&mut body.walk()) {
            // Only count method_signature (e.g., `getName(): string`)
            // Skip property_signature (e.g., `name: string`) - these are data, not methods
            if child.kind() == "method_signature" {
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

/// Extract methods from a class body as Function entities
/// NOTE: Only extracts direct method definitions, not nested closures/callbacks
fn extract_class_methods(
    class_node: &Node,
    source: &[u8],
    path: &Path,
    result: &mut ParseResult,
    class_name: &str,
) {
    if let Some(body) = class_node.child_by_field_name("body") {
        // Only iterate direct children of class_body
        for child in body.children(&mut body.walk()) {
            match child.kind() {
                "method_definition" => {
                    // Regular class method
                    if let Some(func) = parse_method_node(&child, source, path, class_name) {
                        result.functions.push(func);
                    }
                }
                "public_field_definition" => {
                    // Arrow function class field (e.g., foo = () => {})
                    if let Some(value_node) = child.child_by_field_name("value") {
                        if value_node.kind() == "arrow_function" {
                            if let Some(func) = parse_arrow_field_node(
                                &child,
                                &value_node,
                                source,
                                path,
                                class_name,
                            ) {
                                result.functions.push(func);
                            }
                        }
                    }
                }
                _ => {}
            }
        }
    }
}

/// Parse an arrow function class field into a Function struct
/// e.g., foo = () => {} or foo = async (x) => x * 2
fn parse_arrow_field_node(
    field_node: &Node,
    arrow_node: &Node,
    source: &[u8],
    path: &Path,
    class_name: &str,
) -> Option<Function> {
    let name_node = field_node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    // Arrow function parameters can be in formal_parameters or a single identifier
    let params_node = arrow_node.child_by_field_name("parameters").or_else(|| {
        // For single-param arrows like x => x, the parameter is the first child
        arrow_node
            .children(&mut arrow_node.walk())
            .find(|c| c.kind() == "identifier" || c.kind() == "formal_parameters")
    });
    let parameters = extract_parameters(params_node, source);

    let return_type = extract_return_type(arrow_node, source);
    let is_async = is_async_function(arrow_node, source);

    let line_start = field_node.start_position().row as u32 + 1;
    let line_end = field_node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}.{}:{}", path.display(), class_name, name, line_start);

    Some(Function {
        name,
        qualified_name,
        file_path: path.to_path_buf(),
        line_start,
        line_end,
        parameters,
        return_type,
        is_async,
        complexity: Some(calculate_complexity(arrow_node, source)),
        max_nesting: None,
        doc_comment: None,
        annotations: vec![],
    })
}

/// Parse a method definition into a Function struct
fn parse_method_node(
    node: &Node,
    source: &[u8],
    path: &Path,
    class_name: &str,
) -> Option<Function> {
    let name_node = node.child_by_field_name("name")?;
    let name = name_node.utf8_text(source).ok()?.to_string();

    let params_node = node.child_by_field_name("parameters");
    let parameters = extract_parameters(params_node, source);

    let return_type = extract_return_type(node, source);
    let is_async = is_async_function(node, source);

    let line_start = node.start_position().row as u32 + 1;
    let line_end = node.end_position().row as u32 + 1;
    let qualified_name = format!("{}::{}.{}:{}", path.display(), class_name, name, line_start);

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
        max_nesting: None,
        doc_comment: None,
        annotations: vec![],
    })
}

/// Extract import statements from the AST
fn extract_imports(
    root: &Node,
    source: &[u8],
    result: &mut ParseResult,
    language: &Language,
    _ext: &str,
) -> Result<()> {
    // Use a simple query that works for both TS and JS
    let query_str = r#"
        (import_statement) @import_stmt
        (export_statement
            source: (string) @export_source
        )
    "#;

    // Note: Import queries are simple enough we don't need complex caching
    let query = Query::new(language, query_str).context("Failed to create import query")?;

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(&query, *root, source);

    while let Some(m) = matches.next() {
        for capture in m.captures.iter() {
            let capture_name = query.capture_names()[capture.index as usize];

            if capture_name == "import_stmt" {
                // Get the full import statement text to check for "import type"
                let stmt_text = capture.node.utf8_text(source).unwrap_or("");
                let is_type_only = stmt_text.trim_start().starts_with("import type ");

                // Find the source string within this import statement
                if let Some(source_node) = capture.node.child_by_field_name("source") {
                    if let Ok(text) = source_node.utf8_text(source) {
                        let import = text
                            .trim_start_matches(['"', '\''])
                            .trim_end_matches(['"', '\''])
                            .to_string();
                        if !import.is_empty() && !result.imports.iter().any(|i| i.path == import) {
                            result.imports.push(if is_type_only {
                                super::ImportInfo::type_only(import)
                            } else {
                                super::ImportInfo::runtime(import)
                            });
                        }
                    }
                }
            } else if capture_name == "export_source" {
                if let Ok(text) = capture.node.utf8_text(source) {
                    let import = text
                        .trim_start_matches(['"', '\''])
                        .trim_end_matches(['"', '\''])
                        .to_string();
                    if !import.is_empty() && !result.imports.iter().any(|i| i.path == import) {
                        result.imports.push(super::ImportInfo::runtime(import));
                    }
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

    // Handle new expressions
    if node.kind() == "new_expression" {
        let call_line = node.start_position().row as u32 + 1;
        // For top-level calls (outside any function), use the file path as the caller
        let caller = find_containing_scope(call_line, scope_map)
            .unwrap_or_else(|| path.display().to_string());

        if let Some(constructor) = node.child_by_field_name("constructor") {
            if let Ok(callee) = constructor.utf8_text(source) {
                result.calls.push((caller, format!("new {}", callee)));
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
        "member_expression" => node.utf8_text(source).ok().map(|s| s.to_string()),
        "subscript_expression" => {
            // obj["method"]() - get the object
            node.child_by_field_name("object")
                .and_then(|n| n.utf8_text(source).ok())
                .map(|s| s.to_string())
        }
        _ => node.utf8_text(source).ok().map(|s| s.to_string()),
    }
}

/// Extract JSDoc comment preceding a declaration node.
///
/// JSDoc comments are `/** ... */` block comments immediately before a declaration.
fn extract_jsdoc_comment(node: &Node, source: &[u8]) -> Option<String> {
    let mut target = *node;

    // If this node is wrapped in an export_statement, check the export's siblings
    if let Some(parent) = node.parent() {
        if parent.kind() == "export_statement" {
            target = parent;
        }
    }

    // Also check if wrapped in variable_declarator -> variable_declaration -> export
    if let Some(parent) = node.parent() {
        if parent.kind() == "variable_declarator" {
            if let Some(grandparent) = parent.parent() {
                if grandparent.kind() == "lexical_declaration" || grandparent.kind() == "variable_declaration" {
                    target = grandparent;
                    if let Some(ggp) = grandparent.parent() {
                        if ggp.kind() == "export_statement" {
                            target = ggp;
                        }
                    }
                }
            }
        }
    }

    let mut sibling = target.prev_sibling();
    while let Some(sib) = sibling {
        if sib.kind() == "comment" {
            if let Ok(text) = sib.utf8_text(source) {
                if text.starts_with("/**") {
                    let doc = text
                        .trim_start_matches("/**")
                        .trim_end_matches("*/")
                        .lines()
                        .map(|line| {
                            let trimmed = line.trim();
                            trimmed
                                .strip_prefix("* ")
                                .unwrap_or(trimmed.strip_prefix('*').unwrap_or(trimmed))
                        })
                        .collect::<Vec<_>>()
                        .join("\n")
                        .trim()
                        .to_string();
                    if !doc.is_empty() {
                        return Some(doc);
                    }
                }
            }
            break;
        }
        // Skip decorator nodes
        if sib.kind() == "decorator" {
            sibling = sib.prev_sibling();
            continue;
        }
        break;
    }

    None
}

/// Check if a function is a React component.
///
/// Heuristic: function name starts with uppercase and the function body
/// contains JSX elements (jsx_element, jsx_self_closing_element, jsx_fragment).
fn is_react_component(node: &Node, _source: &[u8], name: &str) -> bool {
    // React components must start with an uppercase letter
    if !name.starts_with(|c: char| c.is_uppercase()) {
        return false;
    }

    // Check if the function body contains JSX
    fn contains_jsx(node: &Node) -> bool {
        match node.kind() {
            "jsx_element" | "jsx_self_closing_element" | "jsx_fragment" => return true,
            _ => {}
        }
        for child in node.children(&mut node.walk()) {
            if contains_jsx(&child) {
                return true;
            }
        }
        false
    }

    contains_jsx(node)
}

/// Collect React hook calls from a function body.
///
/// Detects calls to `useState`, `useEffect`, `useCallback`, `useMemo`, `useRef`,
/// `useContext`, `useReducer`, `useLayoutEffect`, and custom hooks (use* pattern).
fn collect_hook_calls(node: &Node, source: &[u8], annotations: &mut Vec<String>) {
    fn walk_for_hooks(node: &Node, source: &[u8], hooks: &mut Vec<String>) {
        if node.kind() == "call_expression" {
            if let Some(func_node) = node.child_by_field_name("function") {
                if let Ok(name) = func_node.utf8_text(source) {
                    // Match useXxx pattern (React hooks convention)
                    if name.starts_with("use") && name.len() > 3 {
                        let third_char = name.chars().nth(3).unwrap_or('a');
                        if third_char.is_uppercase() {
                            let hook_annotation = format!("react:hook:{}", name);
                            if !hooks.contains(&hook_annotation) {
                                hooks.push(hook_annotation);
                            }
                        }
                    }
                }
            }
        }
        for child in node.children(&mut node.walk()) {
            walk_for_hooks(&child, source, hooks);
        }
    }

    let mut hooks = Vec::new();
    walk_for_hooks(node, source, &mut hooks);
    annotations.extend(hooks);
}

/// Calculate cyclomatic complexity of a function
fn calculate_complexity(node: &Node, _source: &[u8]) -> u32 {
    let mut complexity = 1;

    fn count_branches(node: &Node, complexity: &mut u32) {
        match node.kind() {
            "if_statement" | "while_statement" | "for_statement" | "for_in_statement"
            | "do_statement" => {
                *complexity += 1;
            }
            "switch_case" | "switch_default" => {
                *complexity += 1;
            }
            "catch_clause" => {
                *complexity += 1;
            }
            "ternary_expression" => {
                *complexity += 1;
            }
            "binary_expression" => {
                for child in node.children(&mut node.walk()) {
                    if child.kind() == "&&" || child.kind() == "||" {
                        *complexity += 1;
                    }
                }
            }
            "arrow_function" | "function_expression" => {
                *complexity += 1;
            }
            "optional_chain" => {
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
function hello(name: string): string {
    return `Hello, ${name}!`;
}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        assert_eq!(result.functions.len(), 1);
        let func = &result.functions[0];
        assert_eq!(func.name, "hello");
    }

    #[test]
    fn test_parse_async_function() {
        let source = r#"
async function fetchData(url: string): Promise<string> {
    return await fetch(url);
}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        assert_eq!(result.functions.len(), 1);
        let func = &result.functions[0];
        assert!(func.is_async);
    }

    #[test]
    fn test_parse_arrow_function() {
        let source = r#"
const add = (a: number, b: number): number => a + b;
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        assert!(result.functions.iter().any(|f| f.name == "add"));
    }

    #[test]
    fn test_parse_class() {
        let source = r#"
class MyClass extends BaseClass implements Interface {
    constructor() {
        super();
    }

    method(): void {
        console.log("hello");
    }
}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        assert_eq!(result.classes.len(), 1);
        let class = &result.classes[0];
        assert_eq!(class.name, "MyClass");
    }

    #[test]
    fn test_parse_interface() {
        let source = r#"
interface MyInterface {
    name: string;
    doSomething(): void;
}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        assert_eq!(result.classes.len(), 1);
        let iface = &result.classes[0];
        assert_eq!(iface.name, "MyInterface");
    }

    #[test]
    fn test_parse_imports() {
        let source = r#"
import { Component } from 'react';
import axios from 'axios';
import * as fs from 'fs';

export function main() {}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        assert!(result.imports.iter().any(|i| i.path == "react"));
        assert!(result.imports.iter().any(|i| i.path == "axios"));
    }

    #[test]
    fn test_parse_javascript() {
        let source = r#"
function greet(name) {
    return "Hello, " + name;
}
"#;
        let path = PathBuf::from("test.js");
        let result = parse_source(source, &path, "js").unwrap();

        assert_eq!(result.functions.len(), 1);
        let func = &result.functions[0];
        assert_eq!(func.name, "greet");
    }

    #[test]
    fn test_complexity_simple() {
        let source = r#"
function simple(): number {
    return 42;
}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        let func = &result.functions[0];
        assert_eq!(func.name, "simple");
        // Simple function should have complexity 1
        assert_eq!(func.complexity, Some(1));
    }

    #[test]
    fn test_complexity_with_branches() {
        let source = r#"
function complex(x: number): string {
    if (x > 10) {
        return "big";
    } else if (x > 5) {
        return "medium";
    } else if (x > 0) {
        return "small";
    }
    return "zero";
}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        let func = &result.functions[0];
        assert_eq!(func.name, "complex");
        // if + else if + else if = 3 branches, base 1 = 4 total
        assert!(
            func.complexity.unwrap_or(0) >= 4,
            "Expected complexity >= 4, got {:?}",
            func.complexity
        );
    }

    #[test]
    fn test_complexity_with_loops_and_ternary() {
        let source = r#"
function loopy(items: string[]): number {
    let count = 0;
    for (const item of items) {
        if (item.length > 5) {
            count++;
        }
    }
    return count > 0 ? count : -1;
}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        let func = &result.functions[0];
        assert_eq!(func.name, "loopy");
        // for + if + ternary = 3 branches + base 1 = 4
        assert!(
            func.complexity.unwrap_or(0) >= 4,
            "Expected complexity >= 4, got {:?}",
            func.complexity
        );
    }

    #[test]
    fn test_parse_calls() {
        let source = r#"
function helperA() {
    console.log("hello");
}

function helperB() {
    helperA();
}

async function main() {
    helperB();
}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        assert_eq!(result.functions.len(), 3, "Expected 3 functions");

        // Debug: print what we got
        eprintln!(
            "Functions: {:?}",
            result
                .functions
                .iter()
                .map(|f| (&f.name, f.line_start, f.line_end))
                .collect::<Vec<_>>()
        );
        eprintln!("Calls: {:?}", result.calls);

        // helperB calls helperA
        assert!(
            result
                .calls
                .iter()
                .any(|(caller, callee)| caller.contains("helperB") && callee == "helperA"),
            "Expected helperB -> helperA call, got {:?}",
            result.calls
        );

        // main calls helperB
        assert!(
            result
                .calls
                .iter()
                .any(|(caller, callee)| caller.contains("main") && callee == "helperB"),
            "Expected main -> helperB call, got {:?}",
            result.calls
        );
    }

    #[test]
    fn test_method_count_excludes_nested() {
        // Issue #18: Parser should not count closures/callbacks as class methods
        let source = r#"
class Foo {
    bar() {
        const inner = () => {};  // NOT a method - nested arrow function
        items.map(x => x);       // NOT a method - callback
        function localHelper() {} // NOT a method - nested function
    }
    baz() {}  // IS a method
    qux = () => {};  // IS a method - arrow function class field
}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        assert_eq!(result.classes.len(), 1, "Expected 1 class");
        let class = &result.classes[0];
        assert_eq!(class.name, "Foo");

        // Should have exactly 3 methods: bar, baz, qux
        // NOT: inner, map callback, localHelper (these are nested)
        assert_eq!(
            class.methods.len(),
            3,
            "Expected 3 methods (bar, baz, qux), got {:?}",
            class.methods
        );
        assert!(
            class.methods.contains(&"bar".to_string()),
            "Missing 'bar' method"
        );
        assert!(
            class.methods.contains(&"baz".to_string()),
            "Missing 'baz' method"
        );
        assert!(
            class.methods.contains(&"qux".to_string()),
            "Missing 'qux' arrow field"
        );
    }

    #[test]
    fn test_method_count_excludes_property_values() {
        // Ensure non-function class fields are not counted as methods
        let source = r#"
class Config {
    name = "test";      // NOT a method - string property
    count = 42;         // NOT a method - number property
    items = [1, 2, 3];  // NOT a method - array property
    handler = () => {}; // IS a method - arrow function
    process() {}        // IS a method
}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        let class = &result.classes[0];
        assert_eq!(
            class.methods.len(),
            2,
            "Expected 2 methods (handler, process), got {:?}",
            class.methods
        );
        assert!(class.methods.contains(&"handler".to_string()));
        assert!(class.methods.contains(&"process".to_string()));
    }

    #[test]
    fn test_js_method_count_excludes_nested() {
        // Same test for JavaScript
        let source = r#"
class Service {
    constructor() {
        this.callbacks = [];
    }
    
    register(callback) {
        const wrapper = () => callback();  // nested, not a method
        this.callbacks.push(wrapper);
    }
    
    execute() {
        this.callbacks.forEach(cb => cb());  // callback, not a method
    }
}
"#;
        let path = PathBuf::from("test.js");
        let result = parse_source(source, &path, "js").unwrap();

        let class = &result.classes[0];
        assert_eq!(
            class.methods.len(),
            3,
            "Expected 3 methods (constructor, register, execute), got {:?}",
            class.methods
        );
    }

    #[test]
    fn test_jsdoc_extracted() {
        let source = r#"
/**
 * Adds two numbers together.
 * @param a - first number
 * @param b - second number
 * @returns the sum
 */
function add(a: number, b: number): number {
    return a + b;
}
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        let func = &result.functions[0];
        assert_eq!(func.name, "add");
        assert!(func.doc_comment.is_some(), "Should have JSDoc");
        let doc = func.doc_comment.as_ref().unwrap();
        assert!(doc.contains("Adds two numbers"), "Got: {}", doc);
    }

    #[test]
    fn test_jsdoc_on_arrow_function() {
        let source = r#"
/** Multiplies two values */
const multiply = (a: number, b: number): number => a * b;
"#;
        let path = PathBuf::from("test.ts");
        let result = parse_source(source, &path, "ts").unwrap();

        let func = result.functions.iter().find(|f| f.name == "multiply").unwrap();
        assert!(func.doc_comment.is_some(), "Arrow function should have JSDoc");
        assert!(func.doc_comment.as_ref().unwrap().contains("Multiplies"));
    }

    #[test]
    fn test_react_component_detected() {
        let source = r#"
function MyComponent({ name }: { name: string }) {
    return <div>Hello {name}</div>;
}

function helperFunction() {
    return 42;
}
"#;
        let path = PathBuf::from("test.tsx");
        let result = parse_source(source, &path, "tsx").unwrap();

        let component = result.functions.iter().find(|f| f.name == "MyComponent").unwrap();
        assert!(
            component.annotations.contains(&"react:component".to_string()),
            "Should detect React component, got: {:?}",
            component.annotations
        );

        let helper = result.functions.iter().find(|f| f.name == "helperFunction").unwrap();
        assert!(
            !helper.annotations.contains(&"react:component".to_string()),
            "helperFunction should not be a React component"
        );
    }

    #[test]
    fn test_react_hooks_detected() {
        let source = r#"
function Counter() {
    const [count, setCount] = useState(0);
    useEffect(() => {
        document.title = `Count: ${count}`;
    }, [count]);
    const ref = useRef(null);
    return <div>{count}</div>;
}
"#;
        let path = PathBuf::from("test.tsx");
        let result = parse_source(source, &path, "tsx").unwrap();

        let counter = result.functions.iter().find(|f| f.name == "Counter").unwrap();
        assert!(
            counter.annotations.iter().any(|a| a == "react:hook:useState"),
            "Should detect useState hook, got: {:?}",
            counter.annotations
        );
        assert!(
            counter.annotations.iter().any(|a| a == "react:hook:useEffect"),
            "Should detect useEffect hook, got: {:?}",
            counter.annotations
        );
        assert!(
            counter.annotations.iter().any(|a| a == "react:hook:useRef"),
            "Should detect useRef hook, got: {:?}",
            counter.annotations
        );
    }
}
