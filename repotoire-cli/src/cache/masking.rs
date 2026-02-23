//! Non-code masking using tree-sitter
//!
//! Replaces comments, docstrings, and string literals with spaces
//! to prevent regex-based detectors from matching inside non-code regions.
//! Preserves newlines so line numbers and column offsets remain stable.

use std::ops::Range;
use tree_sitter::{Node, Parser};

/// Main entry point: mask non-code regions in source code.
///
/// Parses `source` using the tree-sitter grammar for `language` (file extension),
/// identifies comments, string literals, and docstrings, and replaces their
/// content with spaces (preserving newlines for line-count stability).
///
/// Returns the original source unchanged if the language is unsupported or
/// parsing fails.
pub fn mask_non_code(source: &str, language: &str) -> String {
    if source.is_empty() {
        return String::new();
    }

    let ts_lang = match get_ts_language(language) {
        Some(lang) => lang,
        None => return source.to_string(),
    };

    let mut parser = Parser::new();
    if parser.set_language(&ts_lang).is_err() {
        return source.to_string();
    }

    let tree = match parser.parse(source, None) {
        Some(t) => t,
        None => return source.to_string(),
    };

    let ranges = get_non_code_ranges(source.as_bytes(), language, &tree.root_node());
    if ranges.is_empty() {
        return source.to_string();
    }

    mask_ranges(source, &ranges)
}

/// Replace bytes in the given ranges with spaces, preserving `\n` characters.
///
/// This is safe for UTF-8 because:
/// - We only replace non-newline bytes with ASCII space (0x20)
/// - We preserve newlines (0x0A)
/// - The ranges come from tree-sitter which works on valid byte boundaries
fn mask_ranges(source: &str, ranges: &[Range<usize>]) -> String {
    let mut bytes = source.as_bytes().to_vec();
    for range in ranges {
        for i in range.start..range.end.min(bytes.len()) {
            if bytes[i] != b'\n' {
                bytes[i] = b' ';
            }
        }
    }
    // Safety: we only replaced non-newline bytes with spaces (valid ASCII/UTF-8)
    String::from_utf8(bytes).unwrap_or_else(|_| source.to_string())
}

/// Parse with tree-sitter and collect byte ranges for non-code regions.
fn get_non_code_ranges(source: &[u8], language: &str, root: &Node) -> Vec<Range<usize>> {
    let mut ranges = Vec::new();
    collect_non_code_ranges(root, source, language, &mut ranges);
    ranges
}

/// Recursively walk the CST and collect byte ranges for comments, strings,
/// and Python docstrings.
fn collect_non_code_ranges(
    node: &Node,
    source: &[u8],
    language: &str,
    ranges: &mut Vec<Range<usize>>,
) {
    let kind = node.kind();

    if is_comment_node(kind) {
        ranges.push(node.start_byte()..node.end_byte());
        return; // Comments have no interesting children
    }

    if is_string_node(kind) {
        // For Python, check if this is a docstring (expression_statement > string
        // as the first statement in a block or module)
        if language == "py" && is_python_docstring(node, source) {
            ranges.push(node.start_byte()..node.end_byte());
            return;
        }

        // All string literals get masked
        ranges.push(node.start_byte()..node.end_byte());
        return;
    }

    // For TypeScript/JavaScript template strings (template_string)
    if kind == "template_string" {
        ranges.push(node.start_byte()..node.end_byte());
        return;
    }

    // Recurse into children
    let child_count = node.child_count();
    for i in 0..child_count {
        if let Some(child) = node.child(i) {
            collect_non_code_ranges(&child, source, language, ranges);
        }
    }
}

/// Check if a node kind represents a comment.
fn is_comment_node(kind: &str) -> bool {
    matches!(kind, "comment" | "line_comment" | "block_comment")
}

/// Check if a node kind represents a string literal.
fn is_string_node(kind: &str) -> bool {
    matches!(
        kind,
        "string"
            | "string_literal"
            | "raw_string_literal"
            | "interpreted_string_literal"
            | "char_literal"
            | "verbatim_string_literal"
            | "string_content"
    )
}

/// Check if a string node is a Python docstring.
///
/// A docstring is an `expression_statement` containing a `string` that is
/// the first statement in a `block` (function/class body) or `module`.
fn is_python_docstring(node: &Node, _source: &[u8]) -> bool {
    // The string itself should be inside an expression_statement
    let parent = match node.parent() {
        Some(p) => p,
        None => return false,
    };

    if parent.kind() != "expression_statement" {
        return false;
    }

    // The expression_statement should be the first statement in a block or module
    let grandparent = match parent.parent() {
        Some(gp) => gp,
        None => return false,
    };

    match grandparent.kind() {
        "module" => {
            // First statement in module
            is_first_statement(&parent, &grandparent)
        }
        "block" => {
            // First statement in function/class body
            is_first_statement(&parent, &grandparent)
        }
        _ => false,
    }
}

/// Check if `stmt` is the first non-comment statement in `container`.
fn is_first_statement(stmt: &Node, container: &Node) -> bool {
    let child_count = container.child_count();
    for i in 0..child_count {
        if let Some(child) = container.child(i) {
            let kind = child.kind();
            // Skip comments and whitespace-only nodes
            if is_comment_node(kind) || kind == "newline" || kind == "\n" {
                continue;
            }
            // The first real statement should be our expression_statement
            return child.id() == stmt.id();
        }
    }
    false
}

/// Map file extensions to tree-sitter languages.
fn get_ts_language(ext: &str) -> Option<tree_sitter::Language> {
    match ext {
        "py" => Some(tree_sitter_python::LANGUAGE.into()),
        "js" | "jsx" | "mjs" | "cjs" => Some(tree_sitter_javascript::LANGUAGE.into()),
        "ts" => Some(tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()),
        "tsx" => Some(tree_sitter_typescript::LANGUAGE_TSX.into()),
        "rs" => Some(tree_sitter_rust::LANGUAGE.into()),
        "go" => Some(tree_sitter_go::LANGUAGE.into()),
        "java" => Some(tree_sitter_java::LANGUAGE.into()),
        "cs" => Some(tree_sitter_c_sharp::LANGUAGE.into()),
        "c" | "h" => Some(tree_sitter_c::LANGUAGE.into()),
        "cpp" | "cc" | "hpp" | "cxx" => Some(tree_sitter_cpp::LANGUAGE.into()),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mask_python_single_line_comment() {
        let source = "x = 1  # this is a comment\ny = 2\n";
        let result = mask_non_code(source, "py");

        // Code should be preserved
        assert!(result.contains("x = 1"));
        assert!(result.contains("y = 2"));

        // Comment should be masked
        assert!(!result.contains("this is a comment"));
    }

    #[test]
    fn test_mask_python_docstring() {
        let source = r#"def foo():
    """password=secret"""
    return 1
"#;
        let result = mask_non_code(source, "py");

        // Code should be preserved
        assert!(result.contains("def foo():"));
        assert!(result.contains("return 1"));

        // Docstring content should be masked
        assert!(!result.contains("password=secret"));
    }

    #[test]
    fn test_mask_python_multiline_docstring() {
        let source = r#"def analyze():
    """
    This function has debug keywords
    and hardcoded IP 192.168.1.1
    password = secret123
    """
    return True
"#;
        let result = mask_non_code(source, "py");

        // Code should be preserved
        assert!(result.contains("def analyze():"));
        assert!(result.contains("return True"));

        // Docstring content should be masked
        assert!(!result.contains("debug keywords"));
        assert!(!result.contains("192.168.1.1"));
        assert!(!result.contains("password = secret123"));
    }

    #[test]
    fn test_mask_python_string_literals() {
        let source = r#"x = "password=secret123"
y = 'another string'
"#;
        let result = mask_non_code(source, "py");

        // Variable assignments should be preserved
        assert!(result.contains("x ="));
        assert!(result.contains("y ="));

        // String contents should be masked
        assert!(!result.contains("password=secret123"));
        assert!(!result.contains("another string"));
    }

    #[test]
    fn test_mask_javascript_comments() {
        let source = r#"// single line comment
let x = 1;
/* multi-line
   comment with password=secret */
let y = 2;
"#;
        let result = mask_non_code(source, "js");

        // Code should be preserved
        assert!(result.contains("let x = 1;"));
        assert!(result.contains("let y = 2;"));

        // Comments should be masked
        assert!(!result.contains("single line comment"));
        assert!(!result.contains("password=secret"));
    }

    #[test]
    fn test_mask_typescript_template_literal() {
        let source = "let msg = `hello ${name} password=test`;\n";
        let result = mask_non_code(source, "ts");

        // Variable declaration should be preserved
        assert!(result.contains("let msg ="));

        // Template literal content should be masked
        assert!(!result.contains("password=test"));
    }

    #[test]
    fn test_mask_rust_comments() {
        let source = r#"// regular comment
/// doc comment with secret
fn main() {
    let x = 1;
}
"#;
        let result = mask_non_code(source, "rs");

        // Code should be preserved
        assert!(result.contains("fn main()"));
        assert!(result.contains("let x = 1;"));

        // Comments should be masked
        assert!(!result.contains("regular comment"));
        assert!(!result.contains("doc comment with secret"));
    }

    #[test]
    fn test_mask_go_comments() {
        let source = r#"// single line comment
package main
/* block comment
   with password */
func main() {}
"#;
        let result = mask_non_code(source, "go");

        // Code should be preserved
        assert!(result.contains("package main"));
        assert!(result.contains("func main()"));

        // Comments should be masked
        assert!(!result.contains("single line comment"));
        assert!(!result.contains("with password"));
    }

    #[test]
    fn test_mask_preserves_line_count() {
        let source = r#"# comment line 1
# comment line 2
x = 1
"""
multi
line
docstring
"""
y = 2
"#;
        let result = mask_non_code(source, "py");

        let original_lines = source.lines().count();
        let masked_lines = result.lines().count();
        assert_eq!(
            original_lines, masked_lines,
            "Line count should be preserved after masking"
        );
    }

    #[test]
    fn test_mask_unknown_language_returns_unchanged() {
        let source = "some code here # comment\n";
        let result = mask_non_code(source, "xyz");
        assert_eq!(result, source);
    }

    #[test]
    fn test_mask_empty_source() {
        let result = mask_non_code("", "py");
        assert_eq!(result, "");
    }

    #[test]
    fn test_mask_ranges_basic() {
        let source = "hello world";
        let ranges = [6..11]; // "world"
        let result = mask_ranges(source, &ranges);
        assert_eq!(result, "hello      ");
    }

    #[test]
    fn test_mask_ranges_preserves_newlines() {
        let source = "hello\nworld\nagain";
        let ranges = [0..17]; // entire string
        let result = mask_ranges(source, &ranges);
        assert_eq!(result, "     \n     \n     ");
    }
}
