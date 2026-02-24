//! Shared AST fingerprinting utility for AI detectors.
//!
//! Provides tree-sitter based function extraction and AST fingerprinting,
//! used by `AIBoilerplateDetector` and `AIDuplicateBlockDetector`.
//!
//! # Functions
//!
//! - [`parse_functions`] — extract function definitions from source code
//! - [`structural_fingerprint`] — collect AST node kinds (for boilerplate clustering)
//! - [`normalized_fingerprint`] — structure-preserving bigrams with identifiers replaced (for duplicate detection)
//! - [`extract_identifiers`] — collect all identifier names from a code snippet
//! - [`detect_patterns`] — detect boilerplate patterns (try/except, validation, etc.)

use crate::parsers::lightweight::Language;
use std::collections::HashSet;
use tree_sitter::{Node, Parser};

/// Info about a function extracted from source.
#[derive(Debug, Clone)]
pub struct FunctionInfo {
    /// Function name.
    pub name: String,
    /// 1-based start line.
    pub line_start: u32,
    /// 1-based end line.
    pub line_end: u32,
    /// Full text of the function body.
    pub body_text: String,
    /// Language the function was parsed from.
    pub language: Language,
}

// ---------------------------------------------------------------------------
// tree-sitter language resolution (mirrors ssa_flow.rs)
// ---------------------------------------------------------------------------

/// Get the tree-sitter language grammar for a [`Language`] enum value.
fn get_ts_language(lang: Language) -> Option<tree_sitter::Language> {
    match lang {
        Language::Python => Some(tree_sitter_python::LANGUAGE.into()),
        Language::JavaScript => Some(tree_sitter_javascript::LANGUAGE.into()),
        Language::TypeScript => Some(tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()),
        Language::Rust => Some(tree_sitter_rust::LANGUAGE.into()),
        Language::Go => Some(tree_sitter_go::LANGUAGE.into()),
        Language::Java => Some(tree_sitter_java::LANGUAGE.into()),
        Language::CSharp => Some(tree_sitter_c_sharp::LANGUAGE.into()),
        Language::C => Some(tree_sitter_c::LANGUAGE.into()),
        Language::Cpp => Some(tree_sitter_cpp::LANGUAGE.into()),
        _ => None,
    }
}

/// Helper: create a tree-sitter parser for the given language.
/// Returns `None` if the language is unsupported or the parser fails to initialize.
fn make_parser(lang: Language) -> Option<(Parser, tree_sitter::Language)> {
    let ts_lang = get_ts_language(lang)?;
    let mut parser = Parser::new();
    parser.set_language(&ts_lang).ok()?;
    Some((parser, ts_lang))
}

/// Helper: parse `content` with tree-sitter and return the root node.
fn parse_root(content: &str, lang: Language) -> Option<tree_sitter::Tree> {
    let (mut parser, _) = make_parser(lang)?;
    parser.parse(content, None)
}

/// Extract text from a tree-sitter node.
fn node_text<'a>(node: Node<'a>, source: &'a str) -> &'a str {
    &source[node.start_byte()..node.end_byte()]
}

// ---------------------------------------------------------------------------
// Function node kinds per language
// ---------------------------------------------------------------------------

/// Return the set of tree-sitter node kinds that represent function/method
/// definitions in the given language.
fn function_node_kinds(lang: Language) -> &'static [&'static str] {
    match lang {
        Language::Python => &["function_definition"],
        Language::JavaScript => &["function_declaration", "method_definition"],
        Language::TypeScript => &["function_declaration", "method_definition"],
        Language::Rust => &["function_item"],
        Language::Go => &["function_declaration", "method_declaration"],
        Language::Java => &["method_declaration"],
        Language::CSharp => &["method_declaration"],
        Language::C | Language::Cpp => &["function_definition"],
        _ => &[],
    }
}

/// Return the field name used for the function name identifier in the given language.
fn name_field(lang: Language) -> &'static str {
    match lang {
        Language::Python | Language::JavaScript | Language::TypeScript | Language::Go
        | Language::Java | Language::CSharp | Language::C | Language::Cpp => "name",
        Language::Rust => "name",
        _ => "name",
    }
}

/// Return the field name used for the function body in the given language.
fn body_field(lang: Language) -> &'static str {
    match lang {
        Language::Python => "body",
        Language::JavaScript | Language::TypeScript => "body",
        Language::Rust => "body",
        Language::Go => "body",
        Language::Java | Language::CSharp => "body",
        Language::C | Language::Cpp => "body",
        _ => "body",
    }
}

// ---------------------------------------------------------------------------
// parse_functions
// ---------------------------------------------------------------------------

/// Extract function definitions from file content using tree-sitter.
///
/// Supported languages and their node kinds:
/// - Python: `function_definition`
/// - JavaScript/TypeScript: `function_declaration`, `method_definition`
/// - Rust: `function_item`
/// - Go: `function_declaration`, `method_declaration`
/// - Java: `method_declaration`
pub fn parse_functions(content: &str, lang: Language) -> Vec<FunctionInfo> {
    let tree = match parse_root(content, lang) {
        Some(t) => t,
        None => return vec![],
    };

    let kinds: HashSet<&str> = function_node_kinds(lang).iter().copied().collect();
    let mut functions = Vec::new();
    collect_functions(tree.root_node(), content, lang, &kinds, &mut functions);
    functions
}

fn collect_functions(
    node: Node,
    source: &str,
    lang: Language,
    kinds: &HashSet<&str>,
    out: &mut Vec<FunctionInfo>,
) {
    if kinds.contains(node.kind()) {
        let name = node
            .child_by_field_name(name_field(lang))
            .map(|n| node_text(n, source).to_string())
            .unwrap_or_else(|| "<anonymous>".to_string());

        let body_text = node
            .child_by_field_name(body_field(lang))
            .map(|n| node_text(n, source).to_string())
            .unwrap_or_else(|| node_text(node, source).to_string());

        out.push(FunctionInfo {
            name,
            line_start: node.start_position().row as u32 + 1,
            line_end: node.end_position().row as u32 + 1,
            body_text,
            language: lang,
        });
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_functions(child, source, lang, kinds, out);
        }
    }
}

// ---------------------------------------------------------------------------
// structural_fingerprint
// ---------------------------------------------------------------------------

/// Node kinds considered "structural" (statement-level and expression-level).
/// We exclude pure leaf tokens like identifiers, operators, and literals.
fn is_structural_kind(kind: &str) -> bool {
    // Exclude leaf token kinds (identifiers, literals, operators, punctuation).
    // We keep compound statement/expression kinds.
    !matches!(
        kind,
        "identifier"
            | "integer"
            | "float"
            | "string"
            | "true"
            | "false"
            | "none"
            | "comment"
            | "line_comment"
            | "block_comment"
            | "string_content"
            | "string_fragment"
            | "escape_sequence"
            | "number"
            | "number_literal"
            | ","
            | ";"
            | ":"
            | "."
            | "("
            | ")"
            | "{"
            | "}"
            | "["
            | "]"
            | "="
            | "+"
            | "-"
            | "*"
            | "/"
            | "<"
            | ">"
            | "!"
            | "&"
            | "|"
            | "^"
            | "~"
            | "%"
            | "?"
            | "=>"
            | "->"
            | "::"
            | "..."
            | "=="
            | "!="
            | "<="
            | ">="
            | "&&"
            | "||"
            | "+="
            | "-="
            | "*="
            | "/="
    )
}

/// Structural fingerprint: collect AST node kinds from a code snippet.
///
/// Walks all child nodes and collects the `kind()` string for every node
/// that is "structural" (statement-level or expression-level, not a leaf
/// token). Used by `AIBoilerplateDetector` for clustering.
pub fn structural_fingerprint(content: &str, lang: Language) -> HashSet<String> {
    let tree = match parse_root(content, lang) {
        Some(t) => t,
        None => return HashSet::new(),
    };

    let mut kinds = HashSet::new();
    collect_structural_kinds(tree.root_node(), &mut kinds);
    kinds
}

fn collect_structural_kinds(node: Node, out: &mut HashSet<String>) {
    let kind = node.kind();
    // Include if it has children (compound node) and is structural
    if node.child_count() > 0 && is_structural_kind(kind) {
        out.insert(kind.to_string());
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_structural_kinds(child, out);
        }
    }
}

// ---------------------------------------------------------------------------
// normalized_fingerprint
// ---------------------------------------------------------------------------

/// Normalized fingerprint: build bigrams of consecutive node kinds with
/// identifiers replaced by `$ID`.
///
/// This produces a set of pairs like `("if_statement", "binary_expression")`
/// that capture the structural flow of the code while ignoring variable names.
/// Used by `AIDuplicateBlockDetector` for near-duplicate detection.
pub fn normalized_fingerprint(content: &str, lang: Language) -> HashSet<String> {
    let tree = match parse_root(content, lang) {
        Some(t) => t,
        None => return HashSet::new(),
    };

    // Collect a sequence of normalized tokens (node kinds, with identifiers
    // replaced by $ID and literals by $LIT).
    let mut tokens = Vec::new();
    collect_normalized_tokens(tree.root_node(), content, &mut tokens);

    // Build bigrams
    let mut bigrams = HashSet::new();
    for pair in tokens.windows(2) {
        bigrams.insert(format!("{}:{}", pair[0], pair[1]));
    }
    bigrams
}

fn collect_normalized_tokens(node: Node, source: &str, out: &mut Vec<String>) {
    let kind = node.kind();

    if node.child_count() == 0 {
        // Leaf node — normalize
        match kind {
            "identifier" | "property_identifier" | "field_identifier" | "type_identifier"
            | "shorthand_property_identifier" => {
                out.push("$ID".to_string());
            }
            "integer" | "float" | "number" | "number_literal" | "decimal_integer_literal"
            | "hex_integer_literal" => {
                out.push("$LIT".to_string());
            }
            "string" | "string_literal" | "template_string" | "raw_string_literal" => {
                out.push("$STR".to_string());
            }
            "true" | "false" | "none" | "null" | "undefined" => {
                out.push("$CONST".to_string());
            }
            // Skip punctuation/operators entirely
            "," | ";" | ":" | "." | "(" | ")" | "{" | "}" | "[" | "]" => {}
            _ => {
                // Include as-is for structural tokens (keywords, operators)
                out.push(kind.to_string());
            }
        }
    } else {
        // Internal node — emit the kind as a structural marker
        if is_structural_kind(kind) {
            out.push(kind.to_string());
        }
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_normalized_tokens(child, source, out);
        }
    }
}

// ---------------------------------------------------------------------------
// extract_identifiers
// ---------------------------------------------------------------------------

/// Extract all identifier names from a code snippet.
///
/// Walks the AST and collects the text of every `identifier` node.
pub fn extract_identifiers(content: &str, lang: Language) -> Vec<String> {
    let tree = match parse_root(content, lang) {
        Some(t) => t,
        None => return vec![],
    };

    let mut identifiers = Vec::new();
    collect_identifiers(tree.root_node(), content, &mut identifiers);
    identifiers
}

fn collect_identifiers(node: Node, source: &str, out: &mut Vec<String>) {
    if node.kind() == "identifier" && node.child_count() == 0 {
        let text = node_text(node, source);
        if !text.is_empty() {
            out.push(text.to_string());
        }
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_identifiers(child, source, out);
        }
    }
}

// ---------------------------------------------------------------------------
// detect_patterns
// ---------------------------------------------------------------------------

/// Detect boilerplate patterns from AST structure.
///
/// Walks the AST looking for characteristic node kinds that indicate
/// common boilerplate patterns (try/except, validation, HTTP methods, etc.).
pub fn detect_patterns(
    content: &str,
    lang: Language,
) -> Vec<super::ai_boilerplate::BoilerplatePattern> {
    use super::ai_boilerplate::BoilerplatePattern;

    let tree = match parse_root(content, lang) {
        Some(t) => t,
        None => return vec![],
    };

    let mut node_kinds = HashSet::new();
    collect_all_kinds(tree.root_node(), &mut node_kinds);

    let content_lower = content.to_lowercase();
    let mut patterns = Vec::new();

    // Try/Except — Python: try_statement, JS/TS: try_statement, Rust: (no direct equivalent)
    if node_kinds.contains("try_statement")
        || node_kinds.contains("except_clause")
        || node_kinds.contains("catch_clause")
    {
        patterns.push(BoilerplatePattern::TryExcept);
    }

    // Error handling — catch, raise, throw
    if node_kinds.contains("raise_statement")
        || node_kinds.contains("throw_statement")
        || content_lower.contains("error")
        || content_lower.contains("exception")
    {
        patterns.push(BoilerplatePattern::ErrorHandling);
    }

    // Validation — if statements with raise/return for input checking
    let has_if = node_kinds.contains("if_statement") || node_kinds.contains("if_expression");
    if has_if
        && (content_lower.contains("valid")
            || content_lower.contains("check")
            || content_lower.contains("assert")
            || content_lower.contains("isinstance"))
    {
        patterns.push(BoilerplatePattern::Validation);
    }

    // HTTP method patterns
    if content_lower.contains("get(")
        || content_lower.contains("post(")
        || content_lower.contains("put(")
        || content_lower.contains("delete(")
        || content_lower.contains("patch(")
        || content_lower.contains("@app.route")
        || content_lower.contains("@router.")
    {
        patterns.push(BoilerplatePattern::HttpMethod);
    }

    // Database patterns
    if content_lower.contains("execute")
        || content_lower.contains("query")
        || content_lower.contains("cursor")
        || content_lower.contains("session.")
        || content_lower.contains("commit(")
        || content_lower.contains("rollback(")
    {
        patterns.push(BoilerplatePattern::Database);
    }

    // CRUD patterns
    if content_lower.contains("create")
        || content_lower.contains("update")
        || content_lower.contains("delete")
        || content_lower.contains("find_by")
        || content_lower.contains("get_by")
    {
        patterns.push(BoilerplatePattern::Crud);
    }

    // Context manager — Python `with` statement
    if node_kinds.contains("with_statement") || node_kinds.contains("with_clause") {
        patterns.push(BoilerplatePattern::ContextManager);
    }

    // Loop patterns
    if node_kinds.contains("for_statement")
        || node_kinds.contains("while_statement")
        || node_kinds.contains("for_in_statement")
    {
        patterns.push(BoilerplatePattern::Loop);
    }

    // Async patterns
    if content_lower.contains("async ")
        || content_lower.contains("await ")
        || node_kinds.contains("await_expression")
    {
        patterns.push(BoilerplatePattern::Async);
    }

    patterns
}

/// Collect all node kinds in the tree (helper for pattern detection).
fn collect_all_kinds(node: Node, out: &mut HashSet<String>) {
    out.insert(node.kind().to_string());
    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_all_kinds(child, out);
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_python_functions() {
        let code = r#"
def greet(name):
    print(f"Hello, {name}!")

def add(a, b):
    return a + b
"#;
        let funcs = parse_functions(code, Language::Python);
        assert_eq!(funcs.len(), 2, "Should find 2 functions, got {}", funcs.len());

        assert_eq!(funcs[0].name, "greet");
        assert_eq!(funcs[0].line_start, 2); // 1-based
        assert_eq!(funcs[0].line_end, 3);

        assert_eq!(funcs[1].name, "add");
        assert_eq!(funcs[1].line_start, 5);
        assert_eq!(funcs[1].line_end, 6);

        // Body text should contain the function body
        assert!(
            funcs[0].body_text.contains("print"),
            "greet body should contain 'print': {}",
            funcs[0].body_text
        );
        assert!(
            funcs[1].body_text.contains("return"),
            "add body should contain 'return': {}",
            funcs[1].body_text
        );
    }

    #[test]
    fn test_structural_fingerprint() {
        let code = r#"
if x > 0:
    for i in range(10):
        pass
try:
    do_something()
except Exception:
    pass
"#;
        let fp = structural_fingerprint(code, Language::Python);
        // Should contain structural kinds for if, for, try
        assert!(
            fp.contains("if_statement"),
            "Should contain if_statement. Got: {:?}",
            fp
        );
        assert!(
            fp.contains("for_statement"),
            "Should contain for_statement. Got: {:?}",
            fp
        );
        assert!(
            fp.contains("try_statement"),
            "Should contain try_statement. Got: {:?}",
            fp
        );
    }

    #[test]
    fn test_normalized_fingerprint_ignores_names() {
        // Two functions with identical structure but different variable names.
        let code_a = r#"
x = foo(1)
if x > 0:
    bar(x)
"#;
        let code_b = r#"
y = baz(1)
if y > 0:
    qux(y)
"#;
        let fp_a = normalized_fingerprint(code_a, Language::Python);
        let fp_b = normalized_fingerprint(code_b, Language::Python);

        // Both should produce the same bigram set since identifiers are replaced
        assert_eq!(
            fp_a, fp_b,
            "Same structure with different names should produce identical fingerprints.\n  A: {:?}\n  B: {:?}",
            fp_a, fp_b
        );
    }

    #[test]
    fn test_extract_identifiers() {
        let code = r#"
x = 10
y = x + 1
"#;
        let ids = extract_identifiers(code, Language::Python);
        assert!(
            ids.contains(&"x".to_string()),
            "Should contain 'x'. Got: {:?}",
            ids
        );
        assert!(
            ids.contains(&"y".to_string()),
            "Should contain 'y'. Got: {:?}",
            ids
        );
    }

    #[test]
    fn test_detect_patterns() {
        let code = r#"
try:
    result = do_something()
except Exception as e:
    handle_error(e)
"#;
        let patterns = detect_patterns(code, Language::Python);
        assert!(
            patterns.contains(&super::super::ai_boilerplate::BoilerplatePattern::TryExcept),
            "Should detect TryExcept pattern. Got: {:?}",
            patterns
        );
    }
}
