//! Pure utility functions for parsing and converting AST node text.

use super::super::types::BinOp;

/// Extract the UTF-8 source text of a tree-sitter node.
///
/// Returns `""` if the byte range is not valid UTF-8.
pub(super) fn node_text<'a>(node: tree_sitter::Node, source: &'a [u8]) -> &'a str {
    let range = node.byte_range();
    std::str::from_utf8(&source[range.start..range.end]).unwrap_or("")
}

/// Strip surrounding quotes from a string literal.
///
/// Handles:
/// - Single, double, and triple quotes (`'`, `"`, `'''`, `"""`)
/// - Prefix characters: `f`, `b`, `r`, `u`, `F`, `B`, `R`, `U` and combinations
///   like `rb`, `fr`, `Rf`, etc.
pub(super) fn unquote(s: &str) -> String {
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
pub(super) fn parse_integer(s: &str) -> Option<i64> {
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
pub(super) fn parse_float(s: &str) -> Option<f64> {
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
pub(super) fn strip_numeric_suffix(s: &str) -> &str {
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
pub(super) fn text_to_binop(op: &str) -> BinOp {
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
pub(super) fn extract_binary_op(node: tree_sitter::Node, source: &[u8]) -> BinOp {
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
