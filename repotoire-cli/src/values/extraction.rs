//! Core extraction logic — converts tree-sitter AST nodes to `SymbolicValue`.
//!
//! The extraction is driven by a [`LanguageValueConfig`] table so the same
//! generic code works for every language; only the config changes.

use super::configs::LanguageValueConfig;
use super::store::{Assignment, RawParseValues};
use super::types::{BinOp, LiteralValue, SymbolicValue};
use crate::models::Function;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Extract the UTF-8 source text of a tree-sitter node.
///
/// Returns `""` if the byte range is not valid UTF-8.
fn node_text<'a>(node: tree_sitter::Node, source: &'a [u8]) -> &'a str {
    let range = node.byte_range();
    std::str::from_utf8(&source[range.start..range.end]).unwrap_or("")
}

/// Strip surrounding quotes from a string literal.
///
/// Handles:
/// - Single, double, and triple quotes (`'`, `"`, `'''`, `"""`)
/// - Prefix characters: `f`, `b`, `r`, `u`, `F`, `B`, `R`, `U` and combinations
///   like `rb`, `fr`, `Rf`, etc.
fn unquote(s: &str) -> String {
    let s = s.trim();
    // Strip optional prefixes (f, b, r, u and combinations like rb, fr, Rf, etc.)
    let stripped = strip_string_prefix(s);

    // Triple-quoted strings
    if stripped.starts_with("\"\"\"") && stripped.ends_with("\"\"\"") && stripped.len() >= 6 {
        return stripped[3..stripped.len() - 3].to_string();
    }
    if stripped.starts_with("'''") && stripped.ends_with("'''") && stripped.len() >= 6 {
        return stripped[3..stripped.len() - 3].to_string();
    }

    // Single/double-quoted strings
    if ((stripped.starts_with('"') && stripped.ends_with('"'))
        || (stripped.starts_with('\'') && stripped.ends_with('\'')))
        && stripped.len() >= 2
    {
        return stripped[1..stripped.len() - 1].to_string();
    }

    // Fallback: return as-is
    s.to_string()
}

/// Strip string literal prefix characters (f, b, r, u and combinations).
///
/// Returns the remaining string starting from the quote character.
fn strip_string_prefix(s: &str) -> &str {
    let bytes = s.as_bytes();
    let mut i = 0;
    // Prefix chars: f F b B r R u U
    while i < bytes.len() {
        match bytes[i] {
            b'f' | b'F' | b'b' | b'B' | b'r' | b'R' | b'u' | b'U' => i += 1,
            _ => break,
        }
    }
    &s[i..]
}

/// Parse a Python integer literal string to `i64`.
///
/// Handles:
/// - Decimal: `42`, `1_000_000`
/// - Hexadecimal: `0x1A`, `0X1a`
/// - Octal: `0o17`, `0O17`
/// - Binary: `0b1010`, `0B1010`
/// - Underscore separators: `1_000`, `0xFF_FF`
fn parse_integer(s: &str) -> Option<i64> {
    let s = s.replace('_', "");
    if s.starts_with("0x") || s.starts_with("0X") {
        i64::from_str_radix(&s[2..], 16).ok()
    } else if s.starts_with("0o") || s.starts_with("0O") {
        i64::from_str_radix(&s[2..], 8).ok()
    } else if s.starts_with("0b") || s.starts_with("0B") {
        i64::from_str_radix(&s[2..], 2).ok()
    } else {
        s.parse::<i64>().ok()
    }
}

/// Parse a float literal string to `f64`, handling underscore separators.
fn parse_float(s: &str) -> Option<f64> {
    let s = s.replace('_', "");
    s.parse::<f64>().ok()
}

/// Map operator text from a binary expression to a `BinOp`.
fn text_to_binop(op: &str) -> BinOp {
    match op.trim() {
        "+" => BinOp::Add,
        "-" => BinOp::Sub,
        "*" => BinOp::Mul,
        "/" => BinOp::Div,
        "%" | "//" => BinOp::Mod,
        "==" => BinOp::Eq,
        "!=" => BinOp::NotEq,
        "<" => BinOp::Lt,
        ">" => BinOp::Gt,
        "<=" => BinOp::LtEq,
        ">=" => BinOp::GtEq,
        "and" => BinOp::And,
        "or" => BinOp::Or,
        "&&" => BinOp::And,
        "||" => BinOp::Or,
        _ => BinOp::Add, // fallback for unsupported operators
    }
}

/// Extract the binary operator from a binary expression node.
///
/// Binary expression nodes typically have three children: left, operator, right.
/// The operator is the unnamed middle child.
fn extract_binary_op(node: tree_sitter::Node, source: &[u8]) -> BinOp {
    // Try named child "operator" first
    if let Some(op_node) = node.child_by_field_name("operator") {
        return text_to_binop(node_text(op_node, source));
    }

    // Fallback: iterate children to find the operator token (usually index 1)
    let child_count = node.child_count();
    if child_count >= 3 {
        // The operator is typically the second child (index 1) and unnamed
        for i in 0..child_count {
            if let Some(child) = node.child(i) {
                if !child.is_named() {
                    let text = node_text(child, source);
                    // Check if it looks like an operator
                    if matches!(
                        text,
                        "+" | "-"
                            | "*"
                            | "/"
                            | "%"
                            | "//"
                            | "=="
                            | "!="
                            | "<"
                            | ">"
                            | "<="
                            | ">="
                            | "and"
                            | "or"
                            | "&&"
                            | "||"
                    ) {
                        return text_to_binop(text);
                    }
                }
            }
        }
    }

    // Last resort for boolean operators (Python `and`/`or` are named children)
    for i in 0..child_count {
        if let Some(child) = node.child(i) {
            let text = node_text(child, source);
            if text == "and" || text == "or" {
                return text_to_binop(text);
            }
        }
    }

    BinOp::Add // fallback
}

/// Extract the callee function name from a `call` node.
///
/// Handles simple identifiers (`foo(...)`) and attribute access (`obj.method(...)`).
fn extract_callee_name(node: tree_sitter::Node, source: &[u8]) -> String {
    // Python call: `function` is a named field
    if let Some(func_node) = node.child_by_field_name("function") {
        let kind = func_node.kind();
        if kind == "identifier" {
            return node_text(func_node, source).to_string();
        }
        if kind == "attribute" {
            // obj.method -> "obj.method"
            return node_text(func_node, source).to_string();
        }
        // Fallback: use the full text
        return node_text(func_node, source).to_string();
    }

    // Fallback: first named child
    if let Some(first) = node.named_child(0) {
        return node_text(first, source).to_string();
    }

    "<unknown>".to_string()
}

// ---------------------------------------------------------------------------
// Core conversion
// ---------------------------------------------------------------------------

/// Convert a tree-sitter expression node to a `SymbolicValue`.
///
/// Walks the AST node and dispatches based on the language config's node kind
/// tables. Recurses into sub-expressions for compound values.
///
/// # Arguments
/// * `node` - The tree-sitter AST node to convert.
/// * `source` - The full source file as bytes.
/// * `config` - The language-specific configuration table.
/// * `_func_qn` - The qualified name of the enclosing function (for future use).
pub fn node_to_symbolic(
    node: tree_sitter::Node,
    source: &[u8],
    config: &LanguageValueConfig,
    _func_qn: &str,
) -> SymbolicValue {
    let kind = node.kind();

    // --- Identifiers ---
    if LanguageValueConfig::matches(config.identifier_kinds, kind) {
        return SymbolicValue::Variable(node_text(node, source).to_string());
    }

    // --- Boolean literals (check before string since `true`/`false` are simple) ---
    if LanguageValueConfig::matches(config.bool_true_kinds, kind) {
        return SymbolicValue::Literal(LiteralValue::Boolean(true));
    }
    if LanguageValueConfig::matches(config.bool_false_kinds, kind) {
        return SymbolicValue::Literal(LiteralValue::Boolean(false));
    }

    // --- Null ---
    if LanguageValueConfig::matches(config.null_kinds, kind) {
        return SymbolicValue::Literal(LiteralValue::Null);
    }

    // --- Integer literals ---
    if LanguageValueConfig::matches(config.integer_literal_kinds, kind) {
        let text = node_text(node, source);
        return match parse_integer(text) {
            Some(n) => SymbolicValue::Literal(LiteralValue::Integer(n)),
            None => SymbolicValue::Unknown,
        };
    }

    // --- Float literals ---
    if LanguageValueConfig::matches(config.float_literal_kinds, kind) {
        let text = node_text(node, source);
        return match parse_float(text) {
            Some(f) => SymbolicValue::Literal(LiteralValue::Float(f)),
            None => SymbolicValue::Unknown,
        };
    }

    // --- String literals ---
    if LanguageValueConfig::matches(config.string_literal_kinds, kind) {
        // For concatenated_string, join child strings
        if kind == "concatenated_string" {
            let mut parts = Vec::new();
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                parts.push(node_to_symbolic(child, source, config, _func_qn));
            }
            return if parts.len() == 1 {
                parts.into_iter().next().expect("checked len == 1")
            } else {
                SymbolicValue::Concat(parts)
            };
        }

        // Check for f-string (string with interpolation children)
        let has_interpolation = has_interpolation_children(node);
        if has_interpolation {
            let mut parts = Vec::new();
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                let ck = child.kind();
                if ck == "interpolation" || ck == "string_content" {
                    // Interpolation: extract the inner expression
                    if ck == "interpolation" {
                        if let Some(expr) = child.named_child(0) {
                            parts.push(node_to_symbolic(expr, source, config, _func_qn));
                        }
                    } else {
                        parts.push(SymbolicValue::Literal(LiteralValue::String(
                            node_text(child, source).to_string(),
                        )));
                    }
                }
            }
            return if parts.is_empty() {
                SymbolicValue::Literal(LiteralValue::String(unquote(node_text(node, source))))
            } else {
                SymbolicValue::Concat(parts)
            };
        }

        let text = node_text(node, source);
        return SymbolicValue::Literal(LiteralValue::String(unquote(text)));
    }

    // --- Binary operators ---
    if LanguageValueConfig::matches(config.binary_op_kinds, kind) {
        let op = extract_binary_op(node, source);

        // Left operand: named field "left" or first named child
        let lhs = node
            .child_by_field_name("left")
            .or_else(|| node.named_child(0))
            .map(|n| node_to_symbolic(n, source, config, _func_qn))
            .unwrap_or(SymbolicValue::Unknown);

        // Right operand: named field "right" or last named child
        let rhs = node
            .child_by_field_name("right")
            .or_else(|| {
                let count = node.named_child_count();
                if count >= 2 {
                    node.named_child(count - 1)
                } else {
                    None
                }
            })
            .map(|n| node_to_symbolic(n, source, config, _func_qn))
            .unwrap_or(SymbolicValue::Unknown);

        return SymbolicValue::BinaryOp(op, Box::new(lhs), Box::new(rhs));
    }

    // --- Function calls ---
    if LanguageValueConfig::matches(config.call_kinds, kind) {
        let callee = extract_callee_name(node, source);
        let mut args = Vec::new();

        if let Some(args_node) = node.child_by_field_name("arguments") {
            let mut cursor = args_node.walk();
            for child in args_node.named_children(&mut cursor) {
                // Skip keyword argument names, only extract values
                if child.kind() == "keyword_argument" {
                    if let Some(val) = child.child_by_field_name("value") {
                        args.push(node_to_symbolic(val, source, config, _func_qn));
                    }
                } else {
                    args.push(node_to_symbolic(child, source, config, _func_qn));
                }
            }
        }

        return SymbolicValue::Call(callee, args);
    }

    // --- Field/attribute access ---
    if LanguageValueConfig::matches(config.field_access_kinds, kind) {
        let obj = node
            .child_by_field_name("object")
            .map(|n| node_to_symbolic(n, source, config, _func_qn))
            .unwrap_or(SymbolicValue::Unknown);

        let attr_name = node
            .child_by_field_name("attribute")
            .map(|n| node_text(n, source).to_string())
            .unwrap_or_default();

        return SymbolicValue::FieldAccess(Box::new(obj), attr_name);
    }

    // --- Subscript/index access ---
    if LanguageValueConfig::matches(config.subscript_kinds, kind) {
        let obj = node
            .child_by_field_name("value")
            .map(|n| node_to_symbolic(n, source, config, _func_qn))
            .unwrap_or(SymbolicValue::Unknown);

        // Python subscript: the key is in the "subscript" field
        // but tree-sitter-python uses different field names depending on version
        let key = node
            .child_by_field_name("subscript")
            .or_else(|| {
                // Fallback: look for named children after the value
                let count = node.named_child_count();
                if count >= 2 {
                    node.named_child(count - 1)
                } else {
                    None
                }
            })
            .map(|n| node_to_symbolic(n, source, config, _func_qn))
            .unwrap_or(SymbolicValue::Unknown);

        return SymbolicValue::Index(Box::new(obj), Box::new(key));
    }

    // --- List literals ---
    if LanguageValueConfig::matches(config.list_kinds, kind) {
        let mut items = Vec::new();
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            items.push(node_to_symbolic(child, source, config, _func_qn));
        }
        return SymbolicValue::Literal(LiteralValue::list(items));
    }

    // --- Dict literals ---
    if LanguageValueConfig::matches(config.dict_kinds, kind) {
        let mut entries = Vec::new();
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            if child.kind() == "pair" {
                let key = child
                    .child_by_field_name("key")
                    .map(|n| node_to_symbolic(n, source, config, _func_qn))
                    .unwrap_or(SymbolicValue::Unknown);
                let value = child
                    .child_by_field_name("value")
                    .map(|n| node_to_symbolic(n, source, config, _func_qn))
                    .unwrap_or(SymbolicValue::Unknown);
                entries.push((key, value));
            }
        }
        return SymbolicValue::Literal(LiteralValue::dict(entries));
    }

    // --- Conditional expressions (ternary) ---
    if LanguageValueConfig::matches(config.conditional_kinds, kind) {
        // Python: `x if cond else y` — tree-sitter has named children for the branches
        let mut arms = Vec::new();
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            arms.push(node_to_symbolic(child, source, config, _func_qn));
        }
        return SymbolicValue::phi(arms);
    }

    // --- Parenthesized expression: unwrap ---
    if kind == "parenthesized_expression" {
        if let Some(inner) = node.named_child(0) {
            return node_to_symbolic(inner, source, config, _func_qn);
        }
    }

    // --- Unary operator: limited support ---
    if kind == "unary_operator" || kind == "not_operator" {
        // For negation of a literal, produce the negated value
        if let Some(operand) = node.child_by_field_name("argument").or(node.named_child(0)) {
            let op_text = node
                .child(0)
                .map(|c| node_text(c, source))
                .unwrap_or("");
            let inner = node_to_symbolic(operand, source, config, _func_qn);
            if op_text == "-" {
                if let SymbolicValue::Literal(LiteralValue::Integer(n)) = &inner {
                    return SymbolicValue::Literal(LiteralValue::Integer(-n));
                }
                if let SymbolicValue::Literal(LiteralValue::Float(f)) = &inner {
                    return SymbolicValue::Literal(LiteralValue::Float(-f));
                }
            }
            return inner;
        }
    }

    // --- Expression statement: unwrap ---
    if kind == "expression_statement" {
        if let Some(inner) = node.named_child(0) {
            return node_to_symbolic(inner, source, config, _func_qn);
        }
    }

    // --- Fallback ---
    SymbolicValue::Unknown
}

/// Check if a string node contains interpolation children (f-string).
fn has_interpolation_children(node: tree_sitter::Node) -> bool {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == "interpolation" {
            return true;
        }
    }
    false
}

// ---------------------------------------------------------------------------
// File-level extraction
// ---------------------------------------------------------------------------

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

/// Process a single top-level AST node from the module root.
///
/// Handles `expression_statement` wrappers (tree-sitter-python wraps module-level
/// assignments in `expression_statement` nodes) by unwrapping and recursing.
fn process_top_level_node(
    child: tree_sitter::Node,
    source_bytes: &[u8],
    config: &LanguageValueConfig,
    file_qualified_prefix: &str,
    func_lookup: &[(u32, u32, &str)],
    raw: &mut RawParseValues,
) {
    let kind = child.kind();

    // Unwrap expression_statement wrappers (Python puts assignments inside these)
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
    if kind == "function_definition" || kind == "decorated_definition" {
        let func_node = if kind == "decorated_definition" {
            find_child_by_kind(child, "function_definition")
        } else {
            Some(child)
        };

        if let Some(func_node) = func_node {
            let func_line = func_node.start_position().row as u32 + 1; // 1-indexed

            let func_qn = func_lookup
                .iter()
                .find(|(start, end, _)| func_line >= *start && func_line <= *end)
                .map(|(_, _, qn)| *qn);

            if let Some(qn) = func_qn {
                extract_function_body(func_node, source_bytes, config, qn, raw);
            }
        }
        return;
    }

    // Class definitions — walk their methods
    if kind == "class_definition" {
        extract_class_body(child, source_bytes, config, func_lookup, raw);
        return;
    }

    // Decorated definition could be either function or class
    if kind == "decorated_definition" {
        if let Some(class_node) = find_child_by_kind(child, "class_definition") {
            extract_class_body(class_node, source_bytes, config, func_lookup, raw);
        }
    }
}

/// Find a direct child node of a specific kind.
#[allow(clippy::manual_find)]
fn find_child_by_kind<'a>(
    node: tree_sitter::Node<'a>,
    kind: &str,
) -> Option<tree_sitter::Node<'a>> {
    // Cannot use Iterator::find here because the cursor borrow must outlive the
    // returned node, and the closure-based approach hits lifetime issues.
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.kind() == kind {
            return Some(child);
        }
    }
    None
}

/// Extract an assignment node and push it to the appropriate collection.
///
/// For module-level, pushes to `module_constants`.
/// For function-level, pushes to `func_assignments`.
fn extract_assignment(
    node: tree_sitter::Node,
    source: &[u8],
    config: &LanguageValueConfig,
    prefix: &str,
    module_constants: &mut Vec<(String, SymbolicValue)>,
    func_assignments: Option<&mut Vec<Assignment>>,
) {
    let left = node.child_by_field_name("left");
    let right = node.child_by_field_name("right");

    if let (Some(left_node), Some(right_node)) = (left, right) {
        let var_name = node_text(left_node, source).to_string();
        let value = node_to_symbolic(right_node, source, config, prefix);
        let line = node.start_position().row as u32 + 1;
        let column = node.start_position().column as u32;

        if let Some(func_assigns) = func_assignments {
            func_assigns.push(Assignment {
                variable: var_name,
                value,
                line,
                column,
            });
        } else {
            let qualified_name = format!("{prefix}.{var_name}");
            module_constants.push((qualified_name, value));
        }
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
    let body = func_node.child_by_field_name("body");
    let body_node = match body {
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

        // Recurse into compound statements
        if kind == "if_statement"
            || kind == "for_statement"
            || kind == "while_statement"
            || kind == "try_statement"
            || kind == "with_statement"
            || kind == "block"
        {
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

        if kind == "function_definition" || kind == "decorated_definition" {
            let func_node = if kind == "decorated_definition" {
                find_child_by_kind(child, "function_definition")
            } else {
                Some(child)
            };

            if let Some(func_node) = func_node {
                let func_line = func_node.start_position().row as u32 + 1;

                let func_qn = func_lookup
                    .iter()
                    .find(|(start, end, _)| func_line >= *start && func_line <= *end)
                    .map(|(_, _, qn)| *qn);

                if let Some(qn) = func_qn {
                    extract_function_body(func_node, source, config, qn, raw);
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::values::types::*;

    /// Parse a Python expression and convert to SymbolicValue.
    fn parse_python_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::python_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .expect("Python grammar");
        let tree = parser.parse(code, None).expect("parse");
        let root = tree.root_node();
        let first_child = root.child(0).expect("first child");
        let expr = if first_child.kind() == "expression_statement" {
            first_child.child(0).expect("expression")
        } else {
            first_child
        };
        node_to_symbolic(expr, code.as_bytes(), &config, "test.func")
    }

    #[test]
    fn test_python_string_literal() {
        let r = parse_python_expr("\"hello world\"");
        assert_eq!(
            r,
            SymbolicValue::Literal(LiteralValue::String("hello world".into()))
        );
    }

    #[test]
    fn test_python_single_quote_string() {
        let r = parse_python_expr("'single'");
        assert_eq!(
            r,
            SymbolicValue::Literal(LiteralValue::String("single".into()))
        );
    }

    #[test]
    fn test_python_integer_literal() {
        assert_eq!(
            parse_python_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_python_hex_integer() {
        assert_eq!(
            parse_python_expr("0xFF"),
            SymbolicValue::Literal(LiteralValue::Integer(255))
        );
    }

    #[test]
    fn test_python_underscore_integer() {
        assert_eq!(
            parse_python_expr("1_000_000"),
            SymbolicValue::Literal(LiteralValue::Integer(1_000_000))
        );
    }

    #[test]
    fn test_python_float_literal() {
        assert_eq!(
            parse_python_expr("3.14"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_python_boolean_true() {
        assert_eq!(
            parse_python_expr("True"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_python_boolean_false() {
        assert_eq!(
            parse_python_expr("False"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_python_none() {
        assert_eq!(
            parse_python_expr("None"),
            SymbolicValue::Literal(LiteralValue::Null)
        );
    }

    #[test]
    fn test_python_binary_add() {
        let r = parse_python_expr("1 + 2");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
            )
        );
    }

    #[test]
    fn test_python_binary_sub() {
        let r = parse_python_expr("10 - 3");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Sub,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(10))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(3))),
            )
        );
    }

    #[test]
    fn test_python_comparison() {
        let r = parse_python_expr("x == 5");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Eq,
                Box::new(SymbolicValue::Variable("x".into())),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(5))),
            )
        );
    }

    #[test]
    fn test_python_identifier() {
        assert_eq!(
            parse_python_expr("my_var"),
            SymbolicValue::Variable("my_var".into())
        );
    }

    #[test]
    fn test_python_call() {
        let r = parse_python_expr("foo(1, 2)");
        assert_eq!(
            r,
            SymbolicValue::Call(
                "foo".into(),
                vec![
                    SymbolicValue::Literal(LiteralValue::Integer(1)),
                    SymbolicValue::Literal(LiteralValue::Integer(2)),
                ]
            )
        );
    }

    #[test]
    fn test_python_call_no_args() {
        let r = parse_python_expr("bar()");
        assert_eq!(r, SymbolicValue::Call("bar".into(), vec![]));
    }

    #[test]
    fn test_python_attribute() {
        let r = parse_python_expr("obj.field");
        assert_eq!(
            r,
            SymbolicValue::FieldAccess(
                Box::new(SymbolicValue::Variable("obj".into())),
                "field".into(),
            )
        );
    }

    #[test]
    fn test_python_subscript() {
        let r = parse_python_expr("arr[0]");
        assert_eq!(
            r,
            SymbolicValue::Index(
                Box::new(SymbolicValue::Variable("arr".into())),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(0))),
            )
        );
    }

    #[test]
    fn test_python_list() {
        let r = parse_python_expr("[1, 2, 3]");
        assert_eq!(
            r,
            SymbolicValue::Literal(LiteralValue::List(vec![
                SymbolicValue::Literal(LiteralValue::Integer(1)),
                SymbolicValue::Literal(LiteralValue::Integer(2)),
                SymbolicValue::Literal(LiteralValue::Integer(3)),
            ]))
        );
    }

    #[test]
    fn test_python_dict() {
        let r = parse_python_expr("{\"a\": 1, \"b\": 2}");
        assert_eq!(
            r,
            SymbolicValue::Literal(LiteralValue::Dict(vec![
                (
                    SymbolicValue::Literal(LiteralValue::String("a".into())),
                    SymbolicValue::Literal(LiteralValue::Integer(1)),
                ),
                (
                    SymbolicValue::Literal(LiteralValue::String("b".into())),
                    SymbolicValue::Literal(LiteralValue::Integer(2)),
                ),
            ]))
        );
    }

    #[test]
    fn test_python_nested_call() {
        let r = parse_python_expr("outer(inner(1))");
        assert_eq!(
            r,
            SymbolicValue::Call(
                "outer".into(),
                vec![SymbolicValue::Call(
                    "inner".into(),
                    vec![SymbolicValue::Literal(LiteralValue::Integer(1))],
                )]
            )
        );
    }

    #[test]
    fn test_python_negative_integer() {
        let r = parse_python_expr("-42");
        assert_eq!(
            r,
            SymbolicValue::Literal(LiteralValue::Integer(-42))
        );
    }

    // --- File-level extraction tests ---

    #[test]
    fn test_extract_python_module_constants() {
        let source = "TIMEOUT = 3600\nDB_URL = \"postgres://localhost\"\n";
        let config = crate::values::configs::python_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .expect("Python grammar");
        let tree = parser.parse(source, None).expect("parse");
        let raw = extract_file_values(&tree, source, &config, &[], "module");
        assert!(
            raw.module_constants.len() >= 2,
            "Should extract TIMEOUT and DB_URL, got: {:?}",
            raw.module_constants
        );

        // Verify the values
        let timeout = raw
            .module_constants
            .iter()
            .find(|(name, _)| name == "module.TIMEOUT");
        assert!(timeout.is_some(), "Should find module.TIMEOUT");
        assert_eq!(
            timeout.unwrap().1,
            SymbolicValue::Literal(LiteralValue::Integer(3600))
        );

        let db_url = raw
            .module_constants
            .iter()
            .find(|(name, _)| name == "module.DB_URL");
        assert!(db_url.is_some(), "Should find module.DB_URL");
        assert_eq!(
            db_url.unwrap().1,
            SymbolicValue::Literal(LiteralValue::String("postgres://localhost".into()))
        );
    }

    #[test]
    fn test_extract_python_function_assignments() {
        let source = "def foo():\n    x = 42\n    y = \"hello\"\n    return x\n";
        let config = crate::values::configs::python_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .expect("Python grammar");
        let tree = parser.parse(source, None).expect("parse");
        let functions = vec![Function {
            name: "foo".into(),
            qualified_name: "module.foo".into(),
            file_path: "test.py".into(),
            line_start: 1,
            line_end: 4,
            parameters: vec![],
            return_type: None,
            is_async: false,
            complexity: None,
            max_nesting: None,
            doc_comment: None,
            annotations: vec![],
        }];
        let raw = extract_file_values(&tree, source, &config, &functions, "module");
        let assignments = raw
            .function_assignments
            .get("module.foo")
            .expect("should have foo's assignments");
        assert!(
            assignments.len() >= 2,
            "Should extract x and y assignments, got: {:?}",
            assignments
        );
        assert!(
            raw.return_expressions.contains_key("module.foo"),
            "Should extract return"
        );
        assert_eq!(
            raw.return_expressions.get("module.foo").unwrap(),
            &SymbolicValue::Variable("x".into())
        );
    }

    #[test]
    fn test_extract_python_multiple_functions() {
        let source = r#"
def add(a, b):
    result = a + b
    return result

def greet(name):
    msg = "hello " + name
    return msg
"#;
        let config = crate::values::configs::python_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .expect("Python grammar");
        let tree = parser.parse(source, None).expect("parse");
        let functions = vec![
            Function {
                name: "add".into(),
                qualified_name: "module.add".into(),
                file_path: "test.py".into(),
                line_start: 2,
                line_end: 4,
                parameters: vec!["a".into(), "b".into()],
                return_type: None,
                is_async: false,
                complexity: None,
                max_nesting: None,
                doc_comment: None,
                annotations: vec![],
            },
            Function {
                name: "greet".into(),
                qualified_name: "module.greet".into(),
                file_path: "test.py".into(),
                line_start: 6,
                line_end: 8,
                parameters: vec!["name".into()],
                return_type: None,
                is_async: false,
                complexity: None,
                max_nesting: None,
                doc_comment: None,
                annotations: vec![],
            },
        ];
        let raw = extract_file_values(&tree, source, &config, &functions, "module");
        assert!(
            raw.function_assignments.contains_key("module.add"),
            "Should extract add's assignments"
        );
        assert!(
            raw.function_assignments.contains_key("module.greet"),
            "Should extract greet's assignments"
        );
        assert!(
            raw.return_expressions.contains_key("module.add"),
            "Should extract add's return"
        );
        assert!(
            raw.return_expressions.contains_key("module.greet"),
            "Should extract greet's return"
        );
    }

    #[test]
    fn test_unquote_double() {
        assert_eq!(unquote("\"hello\""), "hello");
    }

    #[test]
    fn test_unquote_single() {
        assert_eq!(unquote("'hello'"), "hello");
    }

    #[test]
    fn test_unquote_triple_double() {
        assert_eq!(unquote("\"\"\"hello\"\"\""), "hello");
    }

    #[test]
    fn test_unquote_triple_single() {
        assert_eq!(unquote("'''hello'''"), "hello");
    }

    #[test]
    fn test_unquote_fstring() {
        assert_eq!(unquote("f\"hello\""), "hello");
    }

    #[test]
    fn test_unquote_byte_string() {
        assert_eq!(unquote("b\"hello\""), "hello");
    }

    #[test]
    fn test_unquote_raw_string() {
        assert_eq!(unquote("r\"hello\""), "hello");
    }

    #[test]
    fn test_unquote_combined_prefix() {
        assert_eq!(unquote("rb\"hello\""), "hello");
    }

    #[test]
    fn test_parse_integer_decimal() {
        assert_eq!(parse_integer("42"), Some(42));
    }

    #[test]
    fn test_parse_integer_hex() {
        assert_eq!(parse_integer("0xFF"), Some(255));
    }

    #[test]
    fn test_parse_integer_octal() {
        assert_eq!(parse_integer("0o17"), Some(15));
    }

    #[test]
    fn test_parse_integer_binary() {
        assert_eq!(parse_integer("0b1010"), Some(10));
    }

    #[test]
    fn test_parse_integer_underscores() {
        assert_eq!(parse_integer("1_000_000"), Some(1_000_000));
    }

    #[test]
    fn test_text_to_binop_all_variants() {
        assert_eq!(text_to_binop("+"), BinOp::Add);
        assert_eq!(text_to_binop("-"), BinOp::Sub);
        assert_eq!(text_to_binop("*"), BinOp::Mul);
        assert_eq!(text_to_binop("/"), BinOp::Div);
        assert_eq!(text_to_binop("%"), BinOp::Mod);
        assert_eq!(text_to_binop("=="), BinOp::Eq);
        assert_eq!(text_to_binop("!="), BinOp::NotEq);
        assert_eq!(text_to_binop("<"), BinOp::Lt);
        assert_eq!(text_to_binop(">"), BinOp::Gt);
        assert_eq!(text_to_binop("<="), BinOp::LtEq);
        assert_eq!(text_to_binop(">="), BinOp::GtEq);
        assert_eq!(text_to_binop("and"), BinOp::And);
        assert_eq!(text_to_binop("or"), BinOp::Or);
    }
}
