//! AST walking functions and file-level extraction.
//!
//! Contains `extract_file_values()` — the main public entry point — plus
//! helper functions for walking function bodies, class bodies, and
//! extracting assignments.

use super::super::configs::LanguageValueConfig;
use super::super::store::{Assignment, RawParseValues};
use super::super::types::SymbolicValue;
use super::helpers::node_text;
use super::symbolic::node_to_symbolic;
use crate::models::Function;

/// Extract all assignments and return values from a file's tree-sitter tree.
///
/// Walks the AST looking for:
/// - **Module-level assignments** (not inside any function) -> `module_constants`
/// - **Function-body assignments** -> `function_assignments[func_qn]`
/// - **Return expressions** -> `return_expressions[func_qn]`
///
/// # Arguments
/// * `tree` - The tree-sitter parse tree.
/// * `source` - The full source as a string.
/// * `config` - The language-specific configuration.
/// * `functions` - Parsed function metadata (used to match function boundaries).
/// * `file_qualified_prefix` - The module prefix for qualified names (e.g. `"mypackage.module"`).
pub fn extract_file_values(
    tree: &tree_sitter::Tree,
    source: &str,
    config: &LanguageValueConfig,
    functions: &[Function],
    file_qualified_prefix: &str,
) -> RawParseValues {
    let source_bytes = source.as_bytes();
    let root = tree.root_node();
    let mut raw = RawParseValues::default();

    // Build a lookup from line ranges to function qualified names
    let func_lookup: Vec<(u32, u32, &str)> = functions
        .iter()
        .map(|f| (f.line_start, f.line_end, f.qualified_name.as_str()))
        .collect();

    // Walk top-level children
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        process_top_level_node(
            child,
            source_bytes,
            config,
            file_qualified_prefix,
            &func_lookup,
            &mut raw,
        );
    }

    raw
}

// ---------------------------------------------------------------------------
// AST kind constants (shared across all languages)
// ---------------------------------------------------------------------------

/// Node kinds that represent function definitions across supported languages.
const FUNCTION_DEF_KINDS: &[&str] = &[
    "function_definition",            // Python, C, C++
    "function_declaration",           // JS/TS, C, C++, Go
    "function_item",                  // Rust
    "method_definition",              // JS/TS class methods
    "method_declaration",             // Java, C#
    "arrow_function",                 // JS/TS
    "generator_function_declaration", // JS/TS
    "constructor_declaration",        // Java, C#
];

/// Node kinds that represent class/struct/impl definitions across supported languages.
const CLASS_DEF_KINDS: &[&str] = &[
    "class_definition",      // Python
    "class_declaration",     // JS/TS, Java, C#, C++
    "class_body",            // used in some grammars
    "struct_item",           // Rust
    "impl_item",             // Rust
    "interface_declaration", // Java, C#, TS
    "struct_specifier",      // C, C++
    "class_specifier",       // C++
];

/// Node kinds that wrap inner declarations (decorators, export, etc.).
const WRAPPER_KINDS: &[&str] = &[
    "decorated_definition",       // Python
    "export_statement",           // JS/TS
    "export_default_declaration", // JS/TS ESM
];

/// Check if a node kind matches any entry in a static slice.
fn is_kind_in(kind: &str, kinds: &[&str]) -> bool {
    kinds.contains(&kind)
}

/// Process a single top-level AST node from the module root.
///
/// Handles `expression_statement` wrappers (multiple grammars wrap assignments
/// in these) by unwrapping and recursing. Recognizes function and class
/// definitions across all supported languages.
fn process_top_level_node(
    child: tree_sitter::Node,
    source_bytes: &[u8],
    config: &LanguageValueConfig,
    file_qualified_prefix: &str,
    func_lookup: &[(u32, u32, &str)],
    raw: &mut RawParseValues,
) {
    let kind = child.kind();

    // Unwrap expression_statement wrappers (Python, JS/TS, Go put assignments inside these)
    if kind == "expression_statement" {
        let mut inner_cursor = child.walk();
        for inner in child.named_children(&mut inner_cursor) {
            if LanguageValueConfig::matches(config.assignment_kinds, inner.kind()) {
                extract_assignment(
                    inner,
                    source_bytes,
                    config,
                    file_qualified_prefix,
                    &mut raw.module_constants,
                    None,
                );
            }
        }
        return;
    }

    // Direct module-level assignments (some grammars don't wrap in expression_statement)
    if LanguageValueConfig::matches(config.assignment_kinds, kind) {
        extract_assignment(
            child,
            source_bytes,
            config,
            file_qualified_prefix,
            &mut raw.module_constants,
            None,
        );
        return;
    }

    // Function definitions — walk their body
    if is_kind_in(kind, FUNCTION_DEF_KINDS) {
        process_function_node(child, source_bytes, config, func_lookup, raw);
        return;
    }

    // Class/struct/impl definitions — walk their methods
    if is_kind_in(kind, CLASS_DEF_KINDS) {
        extract_class_body(child, source_bytes, config, func_lookup, raw);
        return;
    }

    // Wrapper nodes (decorated_definition, export_statement, etc.) — unwrap and recurse
    if is_kind_in(kind, WRAPPER_KINDS) {
        let mut inner_cursor = child.walk();
        for inner in child.named_children(&mut inner_cursor) {
            let ik = inner.kind();
            if is_kind_in(ik, FUNCTION_DEF_KINDS) {
                process_function_node(inner, source_bytes, config, func_lookup, raw);
            } else if is_kind_in(ik, CLASS_DEF_KINDS) {
                extract_class_body(inner, source_bytes, config, func_lookup, raw);
            } else if LanguageValueConfig::matches(config.assignment_kinds, ik) {
                // Handle exported assignments (e.g. `export const X = 1;` in JS/TS)
                extract_assignment(
                    inner,
                    source_bytes,
                    config,
                    file_qualified_prefix,
                    &mut raw.module_constants,
                    None,
                );
            }
        }
    }
}

/// Process a function definition node — match it to parsed Function metadata
/// and extract its body.
fn process_function_node(
    func_node: tree_sitter::Node,
    source_bytes: &[u8],
    config: &LanguageValueConfig,
    func_lookup: &[(u32, u32, &str)],
    raw: &mut RawParseValues,
) {
    let func_line = func_node.start_position().row as u32 + 1; // 1-indexed

    let func_qn = func_lookup
        .iter()
        .find(|(start, end, _)| func_line >= *start && func_line <= *end)
        .map(|(_, _, qn)| *qn);

    if let Some(qn) = func_qn {
        extract_function_body(func_node, source_bytes, config, qn, raw);
    }
}

/// Extract an assignment node and push it to the appropriate collection.
///
/// For module-level, pushes to `module_constants`.
/// For function-level, pushes to `func_assignments`.
///
/// Handles different assignment structures across grammars:
/// - Python: `assignment` -> left / right
/// - JS/TS: `variable_declaration` / `lexical_declaration` -> child `variable_declarator` -> name / value
/// - Rust: `let_declaration` -> pattern / value
/// - Go: `short_var_declaration` -> left / right (expression_list)
/// - Java: `local_variable_declaration` -> child `variable_declarator` -> name / value
/// - C#: `variable_declaration` -> child `variable_declarator` -> name / value (via initializer)
/// - C/C++: `declaration` -> child `init_declarator` -> declarator / value
fn extract_assignment(
    node: tree_sitter::Node,
    source: &[u8],
    config: &LanguageValueConfig,
    prefix: &str,
    module_constants: &mut Vec<(String, SymbolicValue)>,
    mut func_assignments: Option<&mut Vec<Assignment>>,
) {
    let kind = node.kind();

    // Strategy 1: Direct left/right fields (Python assignment, Go short_var_declaration)
    if let (Some(left_node), Some(right_node)) = (
        node.child_by_field_name("left"),
        node.child_by_field_name("right"),
    ) {
        push_assignment(
            node_text(left_node, source),
            node_to_symbolic(right_node, source, config, prefix),
            node,
            prefix,
            module_constants,
            &mut func_assignments,
        );
        return;
    }

    // Strategy 2: pattern/value fields (Rust let_declaration)
    if let (Some(pat_node), Some(val_node)) = (
        node.child_by_field_name("pattern"),
        node.child_by_field_name("value"),
    ) {
        push_assignment(
            node_text(pat_node, source),
            node_to_symbolic(val_node, source, config, prefix),
            node,
            prefix,
            module_constants,
            &mut func_assignments,
        );
        return;
    }

    // Strategy 3: Child `variable_declarator` (JS/TS, Java, C#)
    // JS/TS `lexical_declaration` / `variable_declaration` contains one or more
    // `variable_declarator` children with name/value fields.
    if kind == "variable_declaration"
        || kind == "lexical_declaration"
        || kind == "local_variable_declaration"
    {
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            if child.kind() == "variable_declarator" {
                // JS/TS/Java: name + value
                if let (Some(name_node), Some(val_node)) = (
                    child.child_by_field_name("name"),
                    child.child_by_field_name("value"),
                ) {
                    push_assignment(
                        node_text(name_node, source),
                        node_to_symbolic(val_node, source, config, prefix),
                        node,
                        prefix,
                        module_constants,
                        &mut func_assignments,
                    );
                }
            }
        }
        return;
    }

    // Strategy 4: Child `init_declarator` (C/C++ declaration)
    if kind == "declaration" {
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            if child.kind() == "init_declarator" {
                if let (Some(decl_node), Some(val_node)) = (
                    child.child_by_field_name("declarator"),
                    child.child_by_field_name("value"),
                ) {
                    push_assignment(
                        node_text(decl_node, source),
                        node_to_symbolic(val_node, source, config, prefix),
                        node,
                        prefix,
                        module_constants,
                        &mut func_assignments,
                    );
                }
            }
        }
        return;
    }

    // Strategy 5: Go var_declaration -> var_spec children
    if kind == "var_declaration" {
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            if child.kind() == "var_spec" {
                if let Some(val_node) = child.child_by_field_name("value") {
                    if let Some(name_node) = child.child_by_field_name("name") {
                        push_assignment(
                            node_text(name_node, source),
                            node_to_symbolic(val_node, source, config, prefix),
                            node,
                            prefix,
                            module_constants,
                            &mut func_assignments,
                        );
                    }
                }
            }
        }
    }
}

/// Push an assignment to either module constants or function assignments.
fn push_assignment(
    var_name: &str,
    value: SymbolicValue,
    node: tree_sitter::Node,
    prefix: &str,
    module_constants: &mut Vec<(String, SymbolicValue)>,
    func_assignments: &mut Option<&mut Vec<Assignment>>,
) {
    let line = node.start_position().row as u32 + 1;
    let column = node.start_position().column as u32;

    if let Some(ref mut func_assigns) = func_assignments {
        func_assigns.push(Assignment {
            variable: var_name.to_string(),
            value,
            line,
            column,
        });
    } else {
        let qualified_name = format!("{prefix}.{var_name}");
        module_constants.push((qualified_name, value));
    }
}

/// Walk a function body and extract assignments and return statements.
fn extract_function_body(
    func_node: tree_sitter::Node,
    source: &[u8],
    config: &LanguageValueConfig,
    func_qn: &str,
    raw: &mut RawParseValues,
) {
    // Most grammars use "body" for the function body. Some (e.g. JS arrow
    // functions) might inline the expression directly.
    let body_node = func_node.child_by_field_name("body").or_else(|| {
        // For arrow functions or single-expression bodies, the entire
        // function node may be the body.
        if func_node.kind() == "arrow_function" {
            // Arrow functions might have a direct expression child instead of block
            func_node.named_child(func_node.named_child_count().saturating_sub(1))
        } else {
            None
        }
    });

    let body_node = match body_node {
        Some(b) => b,
        None => return,
    };

    let mut assignments: Vec<Assignment> = Vec::new();
    let mut last_return: Option<SymbolicValue> = None;

    walk_function_body(
        body_node,
        source,
        config,
        func_qn,
        &mut assignments,
        &mut last_return,
    );

    if !assignments.is_empty() {
        raw.function_assignments
            .insert(func_qn.to_string(), assignments);
    }
    if let Some(ret) = last_return {
        raw.return_expressions.insert(func_qn.to_string(), ret);
    }
}

/// Recursively walk a function body's statements to collect assignments and returns.
fn walk_function_body(
    node: tree_sitter::Node,
    source: &[u8],
    config: &LanguageValueConfig,
    func_qn: &str,
    assignments: &mut Vec<Assignment>,
    last_return: &mut Option<SymbolicValue>,
) {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        let kind = child.kind();

        // Direct assignments
        if LanguageValueConfig::matches(config.assignment_kinds, kind) {
            extract_assignment(
                child,
                source,
                config,
                func_qn,
                &mut Vec::new(), // dummy; we use func_assignments directly
                Some(assignments),
            );
            continue;
        }

        // Return statements
        if LanguageValueConfig::matches(config.return_kinds, kind) {
            // Extract return expression: first named child is the expression
            if let Some(expr) = child.named_child(0) {
                *last_return = Some(node_to_symbolic(expr, source, config, func_qn));
            }
            continue;
        }

        // expression_statement wrappers — unwrap and check for assignments inside
        if kind == "expression_statement" {
            let mut inner_cursor = child.walk();
            for inner in child.named_children(&mut inner_cursor) {
                if LanguageValueConfig::matches(config.assignment_kinds, inner.kind()) {
                    extract_assignment(
                        inner,
                        source,
                        config,
                        func_qn,
                        &mut Vec::new(),
                        Some(assignments),
                    );
                }
            }
            continue;
        }

        // Recurse into compound statements (all supported languages)
        if matches!(
            kind,
            "if_statement"
                | "for_statement"
                | "while_statement"
                | "try_statement"
                | "with_statement"
                | "block"
                | "statement_block"       // JS/TS
                | "if_expression"         // Rust
                | "match_expression"      // Rust
                | "loop_expression"       // Rust
                | "for_expression"        // Rust
                | "while_expression"      // Rust (old grammar name)
                | "if_let_expression"     // Rust
                | "else_clause"           // many languages
                | "elif_clause"           // Python
                | "else_if_clause"        // some grammars
                | "switch_statement"      // JS/TS, C, C++, Java, C#
                | "switch_case"           // JS/TS
                | "switch_section"        // C#
                | "case_clause"           // Go
                | "do_statement"          // C, C++, Java, C#
                | "for_in_statement"      // JS/TS
                | "for_of_statement"      // JS/TS
                | "enhanced_for_statement" // Java
                | "foreach_statement"     // C#
                | "try_expression"        // Rust
                | "catch_clause"          // JS/TS, Java, C#
                | "finally_clause"        // JS/TS, Java, C#
                | "except_clause"         // Python
                | "using_statement"       // C#
                | "unsafe_block"          // Rust
                | "match_arm" // Rust
        ) {
            walk_function_body(child, source, config, func_qn, assignments, last_return);
        }
    }
}

/// Walk a class body and extract methods' assignments/returns.
fn extract_class_body(
    class_node: tree_sitter::Node,
    source: &[u8],
    config: &LanguageValueConfig,
    func_lookup: &[(u32, u32, &str)],
    raw: &mut RawParseValues,
) {
    let body = class_node.child_by_field_name("body");
    let body_node = match body {
        Some(b) => b,
        None => return,
    };

    let mut cursor = body_node.walk();
    for child in body_node.children(&mut cursor) {
        let kind = child.kind();

        if is_kind_in(kind, FUNCTION_DEF_KINDS) {
            process_function_node(child, source, config, func_lookup, raw);
        } else if is_kind_in(kind, WRAPPER_KINDS) {
            // Decorated/exported method inside a class
            let mut inner_cursor = child.walk();
            for inner in child.named_children(&mut inner_cursor) {
                if is_kind_in(inner.kind(), FUNCTION_DEF_KINDS) {
                    process_function_node(inner, source, config, func_lookup, raw);
                }
            }
        }
    }
}
