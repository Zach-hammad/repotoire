//! AST node to `SymbolicValue` conversion.
//!
//! Contains the core `node_to_symbolic()` function that dispatches on
//! tree-sitter node kinds via the language config.

use super::super::configs::LanguageValueConfig;
use super::super::types::{LiteralValue, SymbolicValue};
use super::helpers::{
    extract_binary_op, node_text, parse_float, parse_integer, strip_numeric_suffix, unquote,
};

/// Extract the callee function name from a call expression node.
///
/// Handles simple identifiers (`foo(...)`) and attribute/member access
/// (`obj.method(...)`) across all supported languages.
///
/// Different grammars use different field names:
/// - Python: `function`
/// - JS/TS/Rust/Go/C/C++: `function`
/// - Java: `name` + `object` for method invocations
pub(super) fn extract_callee_name(node: tree_sitter::Node, source: &[u8]) -> String {
    // Most languages use "function" as the field name for the callee
    if let Some(func_node) = node.child_by_field_name("function") {
        return node_text(func_node, source).to_string();
    }

    // Java method_invocation: uses "name" for the method and "object" for the receiver
    if let Some(name_node) = node.child_by_field_name("name") {
        let name = node_text(name_node, source);
        if let Some(obj_node) = node.child_by_field_name("object") {
            let obj = node_text(obj_node, source);
            return format!("{obj}.{name}");
        }
        return name.to_string();
    }

    // Fallback: first named child
    if let Some(first) = node.named_child(0) {
        return node_text(first, source).to_string();
    }

    "<unknown>".to_string()
}

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
    // Some languages (Rust, C#) use the same node kind for both true and false
    // (e.g. `boolean_literal`). When true and false share a kind, disambiguate
    // by inspecting the node text.
    let matches_true = LanguageValueConfig::matches(config.bool_true_kinds, kind);
    let matches_false = LanguageValueConfig::matches(config.bool_false_kinds, kind);
    if matches_true || matches_false {
        if matches_true && matches_false {
            // Shared kind (e.g. Rust boolean_literal) — check text
            let text = node_text(node, source);
            return SymbolicValue::Literal(LiteralValue::Boolean(text == "true"));
        }
        if matches_true {
            return SymbolicValue::Literal(LiteralValue::Boolean(true));
        }
        return SymbolicValue::Literal(LiteralValue::Boolean(false));
    }

    // --- Null ---
    if LanguageValueConfig::matches(config.null_kinds, kind) {
        return SymbolicValue::Literal(LiteralValue::Null);
    }

    // --- Numeric literals ---
    // Some languages (C, C++, JS/TS) use a single node kind for both integers
    // and floats (e.g. `number_literal`, `number`). When the kind appears in
    // both lists, try integer parsing first, then fall back to float.
    let matches_int = LanguageValueConfig::matches(config.integer_literal_kinds, kind);
    let matches_float = LanguageValueConfig::matches(config.float_literal_kinds, kind);
    if matches_int || matches_float {
        let text = node_text(node, source);
        // Strip type suffixes common in C/C++/Rust/Java (e.g. 42L, 3.14f, 100u64)
        let cleaned = strip_numeric_suffix(text);
        if matches_int {
            if let Some(n) = parse_integer(cleaned) {
                return SymbolicValue::Literal(LiteralValue::Integer(n));
            }
        }
        if matches_float {
            if let Some(f) = parse_float(cleaned) {
                return SymbolicValue::Literal(LiteralValue::Float(f));
            }
        }
        return SymbolicValue::Unknown;
    }

    // --- String literals ---
    if LanguageValueConfig::matches(config.string_literal_kinds, kind) {
        return convert_string_literal(node, source, config, _func_qn);
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
        return convert_call_expr(node, source, config, _func_qn);
    }

    // --- Field/attribute access ---
    if LanguageValueConfig::matches(config.field_access_kinds, kind) {
        return convert_field_access(node, source, config, _func_qn);
    }

    // --- Subscript/index access ---
    if LanguageValueConfig::matches(config.subscript_kinds, kind) {
        return convert_subscript(node, source, config, _func_qn);
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
    if kind == "unary_operator"
        || kind == "not_operator"
        || kind == "unary_expression"
        || kind == "prefix_unary_expression"
    {
        // For negation of a literal, produce the negated value
        if let Some(operand) = node
            .child_by_field_name("argument")
            .or_else(|| node.child_by_field_name("operand"))
            .or(node.named_child(0))
        {
            let op_text = node.child(0).map(|c| node_text(c, source)).unwrap_or("");
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

/// Convert a string literal node (including concatenated and interpolated strings).
fn convert_string_literal(
    node: tree_sitter::Node,
    source: &[u8],
    config: &LanguageValueConfig,
    func_qn: &str,
) -> SymbolicValue {
    let kind = node.kind();

    // Concatenated string: join child strings
    if kind == "concatenated_string" {
        let mut parts = Vec::new();
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            parts.push(node_to_symbolic(child, source, config, func_qn));
        }
        return if parts.len() == 1 {
            parts.into_iter().next().expect("checked len == 1")
        } else {
            SymbolicValue::Concat(parts)
        };
    }

    // F-string (string with interpolation children)
    if has_interpolation_children(node) {
        let mut parts = Vec::new();
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            let ck = child.kind();
            if ck == "interpolation" {
                if let Some(expr) = child.named_child(0) {
                    parts.push(node_to_symbolic(expr, source, config, func_qn));
                }
            } else if ck == "string_content" {
                parts.push(SymbolicValue::Literal(LiteralValue::String(
                    node_text(child, source).to_string(),
                )));
            }
        }
        return if parts.is_empty() {
            SymbolicValue::Literal(LiteralValue::String(unquote(node_text(node, source))))
        } else {
            SymbolicValue::Concat(parts)
        };
    }

    let text = node_text(node, source);
    SymbolicValue::Literal(LiteralValue::String(unquote(text)))
}

/// Convert a function call expression node.
fn convert_call_expr(
    node: tree_sitter::Node,
    source: &[u8],
    config: &LanguageValueConfig,
    func_qn: &str,
) -> SymbolicValue {
    let callee = extract_callee_name(node, source);
    let mut args = Vec::new();

    if let Some(args_node) = node.child_by_field_name("arguments") {
        let mut cursor = args_node.walk();
        for child in args_node.named_children(&mut cursor) {
            let ck = child.kind();
            if ck == "keyword_argument" || ck == "named_argument" {
                if let Some(val) = child.child_by_field_name("value") {
                    args.push(node_to_symbolic(val, source, config, func_qn));
                }
            } else if ck == "spread_element" || ck == "rest_pattern" {
                args.push(SymbolicValue::Unknown);
            } else {
                args.push(node_to_symbolic(child, source, config, func_qn));
            }
        }
    }

    SymbolicValue::Call(callee, args)
}

/// Convert a field/attribute access node.
fn convert_field_access(
    node: tree_sitter::Node,
    source: &[u8],
    config: &LanguageValueConfig,
    func_qn: &str,
) -> SymbolicValue {
    let obj = node
        .child_by_field_name("object")
        .or_else(|| node.child_by_field_name("value"))
        .or_else(|| node.child_by_field_name("operand"))
        .or_else(|| node.child_by_field_name("argument"))
        .or_else(|| node.child_by_field_name("expression"))
        .or_else(|| node.named_child(0))
        .map(|n| node_to_symbolic(n, source, config, func_qn))
        .unwrap_or(SymbolicValue::Unknown);

    let attr_name = node
        .child_by_field_name("attribute")
        .or_else(|| node.child_by_field_name("property"))
        .or_else(|| node.child_by_field_name("field"))
        .or_else(|| node.child_by_field_name("name"))
        .or_else(|| {
            let count = node.named_child_count();
            if count >= 2 {
                node.named_child(count - 1)
            } else {
                None
            }
        })
        .map(|n| node_text(n, source).to_string())
        .unwrap_or_default();

    SymbolicValue::FieldAccess(Box::new(obj), attr_name)
}

/// Convert a subscript/index access node.
fn convert_subscript(
    node: tree_sitter::Node,
    source: &[u8],
    config: &LanguageValueConfig,
    func_qn: &str,
) -> SymbolicValue {
    let obj = node
        .child_by_field_name("value")
        .or_else(|| node.child_by_field_name("object"))
        .or_else(|| node.child_by_field_name("operand"))
        .or_else(|| node.child_by_field_name("array"))
        .or_else(|| node.child_by_field_name("argument"))
        .or_else(|| node.child_by_field_name("expression"))
        .or_else(|| node.named_child(0))
        .map(|n| node_to_symbolic(n, source, config, func_qn))
        .unwrap_or(SymbolicValue::Unknown);

    let key = node
        .child_by_field_name("subscript")
        .or_else(|| node.child_by_field_name("index"))
        .or_else(|| {
            let count = node.named_child_count();
            if count >= 2 {
                node.named_child(count - 1)
            } else {
                None
            }
        })
        .map(|n| node_to_symbolic(n, source, config, func_qn))
        .unwrap_or(SymbolicValue::Unknown);

    SymbolicValue::Index(Box::new(obj), Box::new(key))
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
