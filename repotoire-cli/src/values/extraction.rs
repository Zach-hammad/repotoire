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

/// Strip type suffixes from numeric literals.
///
/// Handles common suffixes across languages:
/// - C/C++: `42L`, `42LL`, `42UL`, `42ULL`, `3.14f`, `3.14F`, `3.14L`
/// - Rust: `42i32`, `42u64`, `3.14f64`, etc.
/// - Java: `42L`, `3.14f`, `3.14d`
///
/// Does NOT strip from hex literals (`0xFF`) since `f`, `F`, `d`, `D` are
/// valid hex digits.
fn strip_numeric_suffix(s: &str) -> &str {
    let bytes = s.as_bytes();
    if bytes.is_empty() {
        return s;
    }

    // Don't strip from hex literals — hex digits overlap with suffixes (f, F, d, D)
    if s.starts_with("0x") || s.starts_with("0X") {
        // For hex: only strip trailing L/LL/UL/ULL (not f/F/d/D since those are hex digits)
        let mut end = bytes.len();
        while end > 2 && matches!(bytes[end - 1], b'u' | b'U' | b'l' | b'L') {
            end -= 1;
        }
        if end > 2 && end < bytes.len() {
            return &s[..end];
        }
        return s;
    }

    // Rust-style suffixes: i8, i16, i32, i64, i128, isize, u8, u16, u32, u64, u128, usize, f32, f64
    for suffix in &[
        "i128", "i64", "i32", "i16", "i8", "isize", "u128", "u64", "u32", "u16", "u8", "usize",
        "f64", "f32",
    ] {
        if s.ends_with(suffix) {
            let end = s.len() - suffix.len();
            if end > 0 {
                return &s[..end];
            }
        }
    }

    // C/C++/Java suffixes: strip trailing [uUlLfFdD]+
    let mut end = bytes.len();
    while end > 0 && matches!(bytes[end - 1], b'u' | b'U' | b'l' | b'L' | b'f' | b'F' | b'd' | b'D')
    {
        end -= 1;
    }
    if end > 0 && end < bytes.len() {
        return &s[..end];
    }

    s
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

/// Extract the callee function name from a call expression node.
///
/// Handles simple identifiers (`foo(...)`) and attribute/member access
/// (`obj.method(...)`) across all supported languages.
///
/// Different grammars use different field names:
/// - Python: `function`
/// - JS/TS/Rust/Go/C/C++: `function`
/// - Java: `name` + `object` for method invocations
fn extract_callee_name(node: tree_sitter::Node, source: &[u8]) -> String {
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

        // Most grammars use "arguments" as the field name for the argument list
        if let Some(args_node) = node.child_by_field_name("arguments") {
            let mut cursor = args_node.walk();
            for child in args_node.named_children(&mut cursor) {
                let ck = child.kind();
                // Skip keyword/named argument wrappers — extract only the value
                if ck == "keyword_argument" || ck == "named_argument" {
                    if let Some(val) = child.child_by_field_name("value") {
                        args.push(node_to_symbolic(val, source, config, _func_qn));
                    }
                } else if ck == "spread_element" || ck == "rest_pattern" {
                    // Spread/rest: the whole call becomes partially unknown
                    args.push(SymbolicValue::Unknown);
                } else {
                    args.push(node_to_symbolic(child, source, config, _func_qn));
                }
            }
        }

        return SymbolicValue::Call(callee, args);
    }

    // --- Field/attribute access ---
    if LanguageValueConfig::matches(config.field_access_kinds, kind) {
        // Different grammars use different field names:
        //   Python:  object / attribute
        //   JS/TS:   object / property
        //   Rust:    value / field
        //   Go:      operand / field
        //   Java:    object / field
        //   C/C++:   argument / field
        //   C#:      expression / name
        let obj = node
            .child_by_field_name("object")
            .or_else(|| node.child_by_field_name("value"))
            .or_else(|| node.child_by_field_name("operand"))
            .or_else(|| node.child_by_field_name("argument"))
            .or_else(|| node.child_by_field_name("expression"))
            .or_else(|| node.named_child(0))
            .map(|n| node_to_symbolic(n, source, config, _func_qn))
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

        return SymbolicValue::FieldAccess(Box::new(obj), attr_name);
    }

    // --- Subscript/index access ---
    if LanguageValueConfig::matches(config.subscript_kinds, kind) {
        // Different grammars use different field names:
        //   Python:  value / subscript
        //   JS/TS:   object / index
        //   Rust:    value / index (index_expression)
        //   Go:      operand / index
        //   Java:    array / index (array_access)
        //   C/C++:   argument / index (subscript_expression)
        //   C#:      expression / argument_list
        let obj = node
            .child_by_field_name("value")
            .or_else(|| node.child_by_field_name("object"))
            .or_else(|| node.child_by_field_name("operand"))
            .or_else(|| node.child_by_field_name("array"))
            .or_else(|| node.child_by_field_name("argument"))
            .or_else(|| node.child_by_field_name("expression"))
            .or_else(|| node.named_child(0))
            .map(|n| node_to_symbolic(n, source, config, _func_qn))
            .unwrap_or(SymbolicValue::Unknown);

        let key = node
            .child_by_field_name("subscript")
            .or_else(|| node.child_by_field_name("index"))
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

/// Node kinds that represent function definitions across supported languages.
const FUNCTION_DEF_KINDS: &[&str] = &[
    "function_definition",   // Python, C, C++
    "function_declaration",  // JS/TS, C, C++, Go
    "function_item",         // Rust
    "method_definition",     // JS/TS class methods
    "method_declaration",    // Java, C#
    "arrow_function",        // JS/TS
    "generator_function_declaration", // JS/TS
    "constructor_declaration", // Java, C#
];

/// Node kinds that represent class/struct/impl definitions across supported languages.
const CLASS_DEF_KINDS: &[&str] = &[
    "class_definition",    // Python
    "class_declaration",   // JS/TS, Java, C#, C++
    "class_body",          // used in some grammars
    "struct_item",         // Rust
    "impl_item",           // Rust
    "interface_declaration", // Java, C#, TS
    "struct_specifier",    // C, C++
    "class_specifier",     // C++
];

/// Node kinds that wrap inner declarations (decorators, export, etc.).
const WRAPPER_KINDS: &[&str] = &[
    "decorated_definition",     // Python
    "export_statement",         // JS/TS
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
    if let (Some(left_node), Some(right_node)) =
        (node.child_by_field_name("left"), node.child_by_field_name("right"))
    {
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
    if let (Some(pat_node), Some(val_node)) =
        (node.child_by_field_name("pattern"), node.child_by_field_name("value"))
    {
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
                if let (Some(name_node), Some(val_node)) =
                    (child.child_by_field_name("name"), child.child_by_field_name("value"))
                {
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
                if let (Some(decl_node), Some(val_node)) =
                    (child.child_by_field_name("declarator"), child.child_by_field_name("value"))
                {
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
    let body_node = func_node
        .child_by_field_name("body")
        .or_else(|| {
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
                | "match_arm"             // Rust
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
        let expr = find_first_expression(tree.root_node());
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

    #[test]
    fn test_strip_numeric_suffix() {
        assert_eq!(strip_numeric_suffix("42"), "42");
        assert_eq!(strip_numeric_suffix("42L"), "42");
        assert_eq!(strip_numeric_suffix("42ULL"), "42");
        assert_eq!(strip_numeric_suffix("3.14f"), "3.14");
        assert_eq!(strip_numeric_suffix("3.14F"), "3.14");
        assert_eq!(strip_numeric_suffix("42i32"), "42");
        assert_eq!(strip_numeric_suffix("3.14f64"), "3.14");
        assert_eq!(strip_numeric_suffix("100u64"), "100");
        assert_eq!(strip_numeric_suffix("1000d"), "1000");
    }

    /// Generic helper: find the first meaningful expression node in a tree.
    ///
    /// Unwraps wrapper nodes like `source_file`, `program`, `translation_unit`,
    /// `expression_statement`, `ERROR`, etc., and returns the first "real"
    /// expression suitable for `node_to_symbolic`.
    fn find_first_expression(node: tree_sitter::Node) -> tree_sitter::Node {
        let kind = node.kind();
        // Top-level wrappers and error recovery nodes to unwrap
        if matches!(
            kind,
            "source_file"
                | "program"
                | "module"
                | "translation_unit"
                | "compilation_unit"
                | "expression_statement"
                | "ERROR"
                | "global_statement"
        ) {
            // Try named children first, then all children (some nodes like
            // Rust's `true` are unnamed children of ERROR nodes)
            if let Some(child) = node.named_child(0) {
                return find_first_expression(child);
            }
            // Fallback: try unnamed children (e.g. Rust boolean_literal `true`/`false`)
            if let Some(child) = node.child(0) {
                return find_first_expression(child);
            }
        }
        node
    }

    // -----------------------------------------------------------------------
    // JavaScript / TypeScript tests
    // -----------------------------------------------------------------------

    /// Parse a JavaScript expression and convert to SymbolicValue.
    fn parse_js_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::typescript_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_javascript::LANGUAGE.into())
            .expect("JS grammar");
        let tree = parser.parse(code, None).expect("parse");
        let expr = find_first_expression(tree.root_node());
        node_to_symbolic(expr, code.as_bytes(), &config, "test.func")
    }

    #[test]
    fn test_js_number_integer() {
        assert_eq!(
            parse_js_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_js_number_float() {
        assert_eq!(
            parse_js_expr("3.14"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_js_string_double_quote() {
        assert_eq!(
            parse_js_expr("\"hello\""),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_js_string_single_quote() {
        assert_eq!(
            parse_js_expr("'hello'"),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_js_boolean_true() {
        assert_eq!(
            parse_js_expr("true"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_js_boolean_false() {
        assert_eq!(
            parse_js_expr("false"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_js_null() {
        assert_eq!(
            parse_js_expr("null"),
            SymbolicValue::Literal(LiteralValue::Null)
        );
    }

    #[test]
    fn test_js_binary_add() {
        let r = parse_js_expr("1 + 2");
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
    fn test_js_identifier() {
        assert_eq!(
            parse_js_expr("myVar"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    #[test]
    fn test_js_call_expression() {
        let r = parse_js_expr("foo(1, 2)");
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
    fn test_js_array_literal() {
        let r = parse_js_expr("[1, 2, 3]");
        assert_eq!(
            r,
            SymbolicValue::Literal(LiteralValue::List(vec![
                SymbolicValue::Literal(LiteralValue::Integer(1)),
                SymbolicValue::Literal(LiteralValue::Integer(2)),
                SymbolicValue::Literal(LiteralValue::Integer(3)),
            ]))
        );
    }

    // -----------------------------------------------------------------------
    // Rust tests
    // -----------------------------------------------------------------------

    /// Parse a Rust expression and convert to SymbolicValue.
    ///
    /// Wraps the expression in `fn _() { let _ = <expr>; }` to get a valid
    /// AST since standalone expressions aren't valid top-level Rust.
    fn parse_rust_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::rust_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_rust::LANGUAGE.into())
            .expect("Rust grammar");
        let wrapped = format!("fn _() {{ let _ = {code}; }}");
        let tree = parser.parse(&wrapped, None).expect("parse");
        // Navigate: source_file -> function_item -> body -> block ->
        //   let_declaration -> value
        let root = tree.root_node();
        let func = root.named_child(0).expect("function_item");
        let body = func.child_by_field_name("body").expect("block body");
        // First named child of block should be the let_declaration
        let let_decl = body.named_child(0).expect("let_declaration");
        let value = let_decl
            .child_by_field_name("value")
            .expect("value field of let_declaration");
        node_to_symbolic(value, wrapped.as_bytes(), &config, "test::func")
    }

    #[test]
    fn test_rust_integer_literal() {
        assert_eq!(
            parse_rust_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_rust_float_literal() {
        assert_eq!(
            parse_rust_expr("3.14"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_rust_string_literal() {
        assert_eq!(
            parse_rust_expr("\"hello\""),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_rust_boolean_true() {
        assert_eq!(
            parse_rust_expr("true"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_rust_boolean_false() {
        assert_eq!(
            parse_rust_expr("false"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_rust_binary_add() {
        let r = parse_rust_expr("1 + 2");
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
    fn test_rust_identifier() {
        assert_eq!(
            parse_rust_expr("my_var"),
            SymbolicValue::Variable("my_var".into())
        );
    }

    // -----------------------------------------------------------------------
    // Go tests
    // -----------------------------------------------------------------------

    /// Parse a Go expression and convert to SymbolicValue.
    ///
    /// Go requires a `package` clause, so we wrap the expression in
    /// `package main; var _ = <expr>` and extract the value from
    /// the `var_spec -> expression_list` node.
    fn parse_go_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::go_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_go::LANGUAGE.into())
            .expect("Go grammar");
        let wrapped = format!("package main\nvar _ = {code}");
        let tree = parser.parse(&wrapped, None).expect("parse");
        let root = tree.root_node();
        // Navigate: source_file -> var_declaration -> var_spec ->
        //   expression_list -> <the actual expression>
        fn find_go_value(node: tree_sitter::Node) -> Option<tree_sitter::Node> {
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                if child.kind() == "var_declaration" {
                    let mut inner = child.walk();
                    for spec in child.named_children(&mut inner) {
                        if spec.kind() == "var_spec" {
                            let count = spec.named_child_count();
                            if count >= 2 {
                                let last = spec.named_child(count - 1)?;
                                // Unwrap expression_list wrapper
                                if last.kind() == "expression_list" {
                                    return last.named_child(0);
                                }
                                return Some(last);
                            }
                        }
                    }
                }
            }
            None
        }
        if let Some(expr) = find_go_value(root) {
            node_to_symbolic(expr, wrapped.as_bytes(), &config, "test.func")
        } else {
            SymbolicValue::Unknown
        }
    }

    #[test]
    fn test_go_int_literal() {
        assert_eq!(
            parse_go_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_go_float_literal() {
        assert_eq!(
            parse_go_expr("3.14"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_go_string_literal() {
        assert_eq!(
            parse_go_expr("\"hello\""),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_go_boolean_true() {
        assert_eq!(
            parse_go_expr("true"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_go_boolean_false() {
        assert_eq!(
            parse_go_expr("false"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_go_identifier() {
        assert_eq!(
            parse_go_expr("myVar"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    // -----------------------------------------------------------------------
    // Java tests
    // -----------------------------------------------------------------------

    /// Parse a Java expression and convert to SymbolicValue.
    ///
    /// Java tree-sitter expects `program` root; standalone expressions may
    /// need a semicolon to parse as `expression_statement`.
    fn parse_java_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::java_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_java::LANGUAGE.into())
            .expect("Java grammar");
        // Java needs at least an expression statement; add semicolon if needed
        let src = if code.ends_with(';') {
            code.to_string()
        } else {
            format!("{code};")
        };
        let tree = parser.parse(&src, None).expect("parse");
        let expr = find_first_expression(tree.root_node());
        node_to_symbolic(expr, src.as_bytes(), &config, "test.func")
    }

    #[test]
    fn test_java_decimal_integer() {
        assert_eq!(
            parse_java_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_java_hex_integer() {
        assert_eq!(
            parse_java_expr("0xFF"),
            SymbolicValue::Literal(LiteralValue::Integer(255))
        );
    }

    #[test]
    fn test_java_string_literal() {
        assert_eq!(
            parse_java_expr("\"hello\""),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_java_boolean_true() {
        assert_eq!(
            parse_java_expr("true"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_java_boolean_false() {
        assert_eq!(
            parse_java_expr("false"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_java_null() {
        assert_eq!(
            parse_java_expr("null"),
            SymbolicValue::Literal(LiteralValue::Null)
        );
    }

    #[test]
    fn test_java_identifier() {
        assert_eq!(
            parse_java_expr("myVar"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    #[test]
    fn test_java_binary_add() {
        let r = parse_java_expr("1 + 2");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
            )
        );
    }

    // -----------------------------------------------------------------------
    // C# tests
    // -----------------------------------------------------------------------

    /// Parse a C# expression and convert to SymbolicValue.
    ///
    /// Wraps in `class C { void M() { var _ = <expr>; } }` since standalone
    /// expressions aren't valid at the C# top level.
    fn parse_csharp_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::csharp_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_c_sharp::LANGUAGE.into())
            .expect("C# grammar");
        let wrapped = format!("class C {{ void M() {{ var _ = {code}; }} }}");
        let tree = parser.parse(&wrapped, None).expect("parse");
        let root = tree.root_node();
        // DFS to find variable_declarator, then take its last named child
        // (the initializer value, after identifier and `=`)
        fn find_csharp_value(node: tree_sitter::Node) -> Option<tree_sitter::Node> {
            if node.kind() == "variable_declarator" {
                let count = node.named_child_count();
                if count >= 2 {
                    return node.named_child(count - 1);
                }
            }
            // Also check for equals_value_clause (some grammar versions)
            if node.kind() == "equals_value_clause" {
                return node.named_child(0);
            }
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                if let Some(found) = find_csharp_value(child) {
                    return Some(found);
                }
            }
            None
        }
        if let Some(expr) = find_csharp_value(root) {
            node_to_symbolic(expr, wrapped.as_bytes(), &config, "test.func")
        } else {
            SymbolicValue::Unknown
        }
    }

    #[test]
    fn test_csharp_integer_literal() {
        assert_eq!(
            parse_csharp_expr("42"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_csharp_string_literal() {
        assert_eq!(
            parse_csharp_expr("\"hello\""),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_csharp_boolean_true() {
        assert_eq!(
            parse_csharp_expr("true"),
            SymbolicValue::Literal(LiteralValue::Boolean(true))
        );
    }

    #[test]
    fn test_csharp_boolean_false() {
        assert_eq!(
            parse_csharp_expr("false"),
            SymbolicValue::Literal(LiteralValue::Boolean(false))
        );
    }

    #[test]
    fn test_csharp_null() {
        assert_eq!(
            parse_csharp_expr("null"),
            SymbolicValue::Literal(LiteralValue::Null)
        );
    }

    #[test]
    fn test_csharp_identifier() {
        assert_eq!(
            parse_csharp_expr("myVar"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    // -----------------------------------------------------------------------
    // C tests
    // -----------------------------------------------------------------------

    /// Parse a C expression and convert to SymbolicValue.
    fn parse_c_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::c_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_c::LANGUAGE.into())
            .expect("C grammar");
        let tree = parser.parse(code, None).expect("parse");
        let expr = find_first_expression(tree.root_node());
        node_to_symbolic(expr, code.as_bytes(), &config, "test_func")
    }

    #[test]
    fn test_c_number_integer() {
        assert_eq!(
            parse_c_expr("42;"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_c_number_float() {
        assert_eq!(
            parse_c_expr("3.14;"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_c_string_literal() {
        assert_eq!(
            parse_c_expr("\"hello\";"),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_c_identifier() {
        assert_eq!(
            parse_c_expr("myVar;"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    #[test]
    fn test_c_binary_add() {
        let r = parse_c_expr("1 + 2;");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Add,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(1))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(2))),
            )
        );
    }

    // -----------------------------------------------------------------------
    // C++ tests
    // -----------------------------------------------------------------------

    /// Parse a C++ expression and convert to SymbolicValue.
    fn parse_cpp_expr(code: &str) -> SymbolicValue {
        let config = crate::values::configs::cpp_config();
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_cpp::LANGUAGE.into())
            .expect("C++ grammar");
        let tree = parser.parse(code, None).expect("parse");
        let expr = find_first_expression(tree.root_node());
        node_to_symbolic(expr, code.as_bytes(), &config, "test_func")
    }

    #[test]
    fn test_cpp_number_integer() {
        assert_eq!(
            parse_cpp_expr("42;"),
            SymbolicValue::Literal(LiteralValue::Integer(42))
        );
    }

    #[test]
    fn test_cpp_number_float() {
        assert_eq!(
            parse_cpp_expr("3.14;"),
            SymbolicValue::Literal(LiteralValue::Float(3.14))
        );
    }

    #[test]
    fn test_cpp_string_literal() {
        assert_eq!(
            parse_cpp_expr("\"hello\";"),
            SymbolicValue::Literal(LiteralValue::String("hello".into()))
        );
    }

    #[test]
    fn test_cpp_identifier() {
        assert_eq!(
            parse_cpp_expr("myVar;"),
            SymbolicValue::Variable("myVar".into())
        );
    }

    #[test]
    fn test_cpp_nullptr() {
        assert_eq!(
            parse_cpp_expr("nullptr;"),
            SymbolicValue::Literal(LiteralValue::Null)
        );
    }

    #[test]
    fn test_cpp_binary_mul() {
        let r = parse_cpp_expr("3 * 4;");
        assert_eq!(
            r,
            SymbolicValue::BinaryOp(
                BinOp::Mul,
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(3))),
                Box::new(SymbolicValue::Literal(LiteralValue::Integer(4))),
            )
        );
    }
}
