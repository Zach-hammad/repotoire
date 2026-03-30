//! Main parse functions and AST walking logic for TypeScript/JavaScript.
//!
//! Contains the entry-point parse functions, tree-sitter parser/query caching,
//! call extraction, and complexity calculation.

use crate::parsers::ParseResult;
use anyhow::{Context, Result};
use std::cell::RefCell;
use std::collections::HashMap;
use std::path::Path;
use std::sync::OnceLock;
use tree_sitter::{Language, Node, Parser, Query};

use super::extractors::{extract_classes, extract_functions, extract_imports};

thread_local! {
    static TS_PARSER: RefCell<Parser> = RefCell::new({
        let mut p = Parser::new();
        p.set_language(&tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()).expect("TypeScript language");
        p
    });
    static TSX_PARSER: RefCell<Parser> = RefCell::new({
        let mut p = Parser::new();
        p.set_language(&tree_sitter_typescript::LANGUAGE_TSX.into()).expect("TSX language");
        p
    });
    static JS_PARSER: RefCell<Parser> = RefCell::new({
        let mut p = Parser::new();
        p.set_language(&tree_sitter_javascript::LANGUAGE.into()).expect("JavaScript language");
        p
    });
}

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
pub(crate) fn get_func_query(ext: &str, language: &Language) -> &'static Query {
    match ext {
        "ts" => TS_FUNC_QUERY
            .get_or_init(|| Query::new(language, FUNC_QUERY_STR).expect("valid function query")),
        "tsx" => TSX_FUNC_QUERY
            .get_or_init(|| Query::new(language, FUNC_QUERY_STR).expect("valid function query")),
        _ => JS_FUNC_QUERY
            .get_or_init(|| Query::new(language, FUNC_QUERY_STR).expect("valid function query")),
    }
}

/// Get or create cached class query for an extension
pub(crate) fn get_class_query(ext: &str, language: &Language) -> &'static Query {
    match ext {
        "ts" => TS_CLASS_QUERY.get_or_init(|| {
            Query::new(language, TS_CLASS_QUERY_STR).unwrap_or_else(|_| {
                Query::new(language, JS_CLASS_QUERY_STR).expect("valid JS class query fallback")
            })
        }),
        "tsx" => TSX_CLASS_QUERY.get_or_init(|| {
            Query::new(language, TS_CLASS_QUERY_STR).unwrap_or_else(|_| {
                Query::new(language, JS_CLASS_QUERY_STR).expect("valid JS class query fallback")
            })
        }),
        _ => JS_CLASS_QUERY.get_or_init(|| {
            Query::new(language, JS_CLASS_QUERY_STR).expect("valid JS class query")
        }),
    }
}

/// Get or create cached import query for an extension
#[allow(dead_code)] // Prepared for import resolution
pub(crate) fn get_import_query(ext: &str, language: &Language) -> &'static Query {
    match ext {
        "ts" => TS_IMPORT_QUERY
            .get_or_init(|| Query::new(language, IMPORT_QUERY_STR).expect("valid import query")),
        "tsx" => TSX_IMPORT_QUERY
            .get_or_init(|| Query::new(language, IMPORT_QUERY_STR).expect("valid import query")),
        _ => JS_IMPORT_QUERY
            .get_or_init(|| Query::new(language, IMPORT_QUERY_STR).expect("valid import query")),
    }
}

/// Parse a TypeScript/JavaScript file and extract all code entities
#[allow(dead_code)]
pub fn parse(path: &Path) -> Result<ParseResult> {
    let source = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read file: {}", path.display()))?;

    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
    parse_source(&source, path, ext)
}

/// Parse TypeScript/JavaScript source code directly
pub fn parse_source(source: &str, path: &Path, ext: &str) -> Result<ParseResult> {
    parse_source_with_tree(source, path, ext).map(|(r, _)| r)
}

/// Parse TypeScript/JavaScript source code and return both the ParseResult and the tree-sitter Tree.
/// Used by the pipeline to extract structural fingerprints without re-parsing.
pub fn parse_source_with_tree(
    source: &str,
    path: &Path,
    ext: &str,
) -> Result<(ParseResult, tree_sitter::Tree)> {
    // Choose language and parser based on extension
    let language: Language = match ext {
        "ts" => tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
        "tsx" => tree_sitter_typescript::LANGUAGE_TSX.into(),
        "js" | "jsx" | "mjs" | "cjs" => tree_sitter_javascript::LANGUAGE.into(),
        _ => tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
    };

    let tree = match ext {
        "tsx" => TSX_PARSER.with(|cell| cell.borrow_mut().parse(source, None)),
        "js" | "jsx" | "mjs" | "cjs" => {
            JS_PARSER.with(|cell| cell.borrow_mut().parse(source, None))
        }
        _ => TS_PARSER.with(|cell| cell.borrow_mut().parse(source, None)),
    }
    .context("Failed to parse source")?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();

    let mut result = ParseResult::default();

    extract_functions(&root, source_bytes, path, &mut result, &language, ext)?;
    extract_classes(&root, source_bytes, path, &mut result, &language, ext)?;
    extract_imports(&root, source_bytes, &mut result, &language, ext)?;
    extract_calls(&root, source_bytes, path, &mut result)?;

    Ok((result, tree))
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
                result.calls.push((caller.clone(), callee));
            }
        }

        // Check arguments for function references (callbacks)
        // e.g., app.get('/path', handler) -> handler is a callback call edge
        if let Some(args_node) = node.child_by_field_name("arguments") {
            let mut arg_cursor = args_node.walk();
            for arg in args_node.children(&mut arg_cursor) {
                if arg.kind() == "identifier" {
                    let arg_name = arg.utf8_text(source).unwrap_or("");
                    // Skip common non-function identifiers
                    if !arg_name.is_empty()
                        && arg_name != "undefined"
                        && arg_name != "null"
                        && arg_name != "true"
                        && arg_name != "false"
                        && arg_name != "this"
                    {
                        result.calls.push((caller.clone(), arg_name.to_string()));
                    }
                }
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
    super::super::find_containing_scope(line, scope_map)
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

/// Calculate cyclomatic complexity of a function
pub(crate) fn calculate_complexity(node: &Node, _source: &[u8]) -> u32 {
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
