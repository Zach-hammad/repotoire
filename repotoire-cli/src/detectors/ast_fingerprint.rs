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
    #[allow(dead_code)] // Used in tests and kept for API completeness
    pub body_text: String,
    /// Language the function was parsed from.
    #[allow(dead_code)] // Included in function info
    pub language: Language,
}

/// Pre-computed fingerprints for a function body.
///
/// All fingerprints are extracted in a single AST walk via
/// [`compute_all_fingerprints`], avoiding redundant tree-sitter parses.
#[derive(Debug, Clone)]
pub struct FunctionFingerprints {
    /// Normalized bigram fingerprint (for duplicate detection).
    pub normalized_bigrams: HashSet<String>,
    /// Structural AST kinds (for boilerplate clustering).
    pub structural_kinds: HashSet<String>,
    /// All identifier names in the function body.
    pub identifiers: Vec<String>,
    /// Detected boilerplate patterns.
    pub patterns: Vec<crate::detectors::ai::ai_boilerplate::BoilerplatePattern>,
}

// ---------------------------------------------------------------------------
// tree-sitter language resolution
// ---------------------------------------------------------------------------

/// Get the tree-sitter language grammar for a [`Language`] enum value.
pub(crate) fn get_ts_language(lang: Language) -> Option<tree_sitter::Language> {
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

// Thread-local cache of tree-sitter parsers keyed by Language discriminant.
// Avoids re-creating Parser objects per file in rayon parallel iterators.
thread_local! {
    static TS_PARSER_CACHE: std::cell::RefCell<std::collections::HashMap<u8, Parser>> =
        std::cell::RefCell::new(std::collections::HashMap::new());
}

/// Helper: parse `content` with tree-sitter, reusing a thread-local parser.
pub(crate) fn parse_root(content: &str, lang: Language) -> Option<tree_sitter::Tree> {
    let lang_key = lang as u8;
    TS_PARSER_CACHE.with(|cache| {
        let mut cache = cache.borrow_mut();
        if let Some(parser) = cache.get_mut(&lang_key) {
            return parser.parse(content, None);
        }
        let ts_lang = get_ts_language(lang)?;
        let mut parser = Parser::new();
        parser.set_language(&ts_lang).ok()?;
        let tree = parser.parse(content, None);
        cache.insert(lang_key, parser);
        tree
    })
}

/// Cache key for TSX grammar (must not collide with Language discriminants).
/// Language enum values are 0..N; we pick 255 as a safe sentinel.
const TSX_CACHE_KEY: u8 = 255;

/// Like [`parse_root`] but picks the correct grammar based on file extension.
///
/// This handles the TSX case: `Language::TypeScript` normally selects the plain
/// TypeScript grammar, but `.tsx` files need `LANGUAGE_TSX` to parse JSX syntax
/// correctly. Without this, tree-sitter produces error nodes for JSX returns,
/// causing false positives in detectors that walk AST siblings.
pub(crate) fn parse_root_ext(
    content: &str,
    lang: Language,
    ext: &str,
) -> Option<tree_sitter::Tree> {
    if ext == "tsx" {
        return TS_PARSER_CACHE.with(|cache| {
            let mut cache = cache.borrow_mut();
            if let Some(parser) = cache.get_mut(&TSX_CACHE_KEY) {
                return parser.parse(content, None);
            }
            let tsx_lang: tree_sitter::Language = tree_sitter_typescript::LANGUAGE_TSX.into();
            let mut parser = Parser::new();
            parser.set_language(&tsx_lang).ok()?;
            let tree = parser.parse(content, None);
            cache.insert(TSX_CACHE_KEY, parser);
            tree
        });
    }
    parse_root(content, lang)
}

/// Extract text from a tree-sitter node.
fn node_text<'a>(node: Node<'a>, source: &'a str) -> &'a str {
    &source[node.start_byte()..node.end_byte()]
}

/// Decide whether to preserve or normalize an identifier based on its AST parent context.
///
/// **Preserve** (return actual text): call targets, method names, type names,
/// scoped identifiers (enum variants), field/property/attribute accesses.
///
/// **Normalize** (return `$ID`): local variables, parameters, assignments,
/// and any identifier without a recognizable semantic parent.
fn normalize_identifier<'a>(node: Node<'a>, source: &'a str) -> &'a str {
    if let Some(parent) = node.parent() {
        let parent_kind = parent.kind();
        match parent_kind {
            // Python: call(func, args) — preserve if this node is the `function` field
            "call" => {
                if parent.child_by_field_name("function") == Some(node) {
                    return node_text(node, source);
                }
            }
            // JS/TS: call_expression(function, arguments) — preserve call target
            "call_expression" => {
                if parent.child_by_field_name("function") == Some(node) {
                    return node_text(node, source);
                }
            }
            // Rust: call_expression(function, arguments) — preserve call target
            // Go: call_expression(function, arguments) — same field name
            "method_call_expression" => {
                // Rust method call: object.method(args) — preserve the method name
                if parent.child_by_field_name("name") == Some(node) {
                    return node_text(node, source);
                }
            }
            // Rust scoped identifiers: e.g., Severity::Critical, std::io::Error
            "scoped_identifier" | "scoped_type_identifier" => {
                return node_text(node, source);
            }
            // Field/member/attribute access — preserve field name
            // Rust: field_expression (object.field)
            "field_expression" => {
                if parent.child_by_field_name("field") == Some(node) {
                    return node_text(node, source);
                }
            }
            // JS/TS: member_expression (object.property)
            "member_expression" => {
                if parent.child_by_field_name("property") == Some(node) {
                    return node_text(node, source);
                }
            }
            // Python: attribute (object.attribute)
            "attribute" => {
                if parent.child_by_field_name("attribute") == Some(node) {
                    return node_text(node, source);
                }
            }
            // Java/C#: method_invocation(name, arguments)
            "method_invocation" => {
                if parent.child_by_field_name("name") == Some(node) {
                    return node_text(node, source);
                }
            }
            _ => {}
        }
    }
    "$ID"
}

// ---------------------------------------------------------------------------
// Function node kinds per language
// ---------------------------------------------------------------------------

/// Return the set of tree-sitter node kinds that represent function/method
/// definitions in the given language.
pub fn function_node_kinds(lang: Language) -> &'static [&'static str] {
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
        Language::Python
        | Language::JavaScript
        | Language::TypeScript
        | Language::Go
        | Language::Java
        | Language::CSharp
        | Language::C
        | Language::Cpp => "name",
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
#[cfg(test)]
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

#[cfg(test)]
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
// structural_fingerprint (kept for tests; production uses compute_all_fingerprints)
// ---------------------------------------------------------------------------

/// Node kinds considered "structural" (statement-level and expression-level).
/// We exclude pure leaf tokens like identifiers, operators, and literals.
pub fn is_structural_kind(kind: &str) -> bool {
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

#[cfg(test)]
pub fn structural_fingerprint(content: &str, lang: Language) -> HashSet<String> {
    let tree = match parse_root(content, lang) {
        Some(t) => t,
        None => return HashSet::new(),
    };

    let mut kinds = HashSet::new();
    collect_structural_kinds(tree.root_node(), &mut kinds);
    kinds
}

#[cfg(test)]
fn collect_structural_kinds(node: Node, out: &mut HashSet<String>) {
    let kind = node.kind();
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
// normalized_fingerprint (kept for tests; production uses compute_all_fingerprints)
// ---------------------------------------------------------------------------

#[cfg(test)]
pub fn normalized_fingerprint(content: &str, lang: Language) -> HashSet<String> {
    let tree = match parse_root(content, lang) {
        Some(t) => t,
        None => return HashSet::new(),
    };

    // Collect a sequence of normalized tokens (node kinds, with selective
    // identifier normalization based on AST context).
    let mut tokens = Vec::new();
    collect_normalized_tokens(tree.root_node(), content, &mut tokens);

    // Build bigrams
    let mut bigrams = HashSet::new();
    for pair in tokens.windows(2) {
        bigrams.insert(format!("{}:{}", pair[0], pair[1]));
    }
    bigrams
}

#[cfg(test)]
fn collect_normalized_tokens(node: Node, source: &str, out: &mut Vec<String>) {
    let kind = node.kind();

    if node.child_count() == 0 {
        match kind {
            "identifier" => {
                let normalized = normalize_identifier(node, source);
                out.push(normalized.to_string());
            }
            "type_identifier" => {
                // Always preserve type names — they carry semantic meaning
                out.push(node_text(node, source).to_string());
            }
            "property_identifier" | "field_identifier" => {
                // Preserve field/property names
                out.push(node_text(node, source).to_string());
            }
            "shorthand_property_identifier" => {
                out.push("$ID".to_string());
            }
            "integer"
            | "float"
            | "number"
            | "number_literal"
            | "decimal_integer_literal"
            | "hex_integer_literal" => {
                let text = node_text(node, source);
                out.push(format!("$LIT:{}", text));
            }
            "string" | "string_literal" | "template_string" | "raw_string_literal" => {
                let text = node_text(node, source);
                if text.len() <= 52 {
                    out.push(format!("$STR:{}", text));
                } else {
                    out.push("$STR".to_string());
                }
            }
            "true" | "false" | "none" | "null" | "undefined" => {
                out.push("$CONST".to_string());
            }
            "," | ";" | ":" | "." | "(" | ")" | "{" | "}" | "[" | "]" => {}
            _ => {
                out.push(kind.to_string());
            }
        }
    } else {
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
// extract_identifiers (kept for tests; production uses compute_all_fingerprints)
// ---------------------------------------------------------------------------

#[cfg(test)]
pub fn extract_identifiers(content: &str, lang: Language) -> Vec<String> {
    let tree = match parse_root(content, lang) {
        Some(t) => t,
        None => return vec![],
    };

    let mut identifiers = Vec::new();
    collect_identifiers(tree.root_node(), content, &mut identifiers);
    identifiers
}

#[cfg(test)]
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
// detect_patterns (kept for tests; production uses compute_all_fingerprints)
// ---------------------------------------------------------------------------

#[cfg(test)]
pub fn detect_patterns(
    content: &str,
    lang: Language,
) -> Vec<crate::detectors::ai::ai_boilerplate::BoilerplatePattern> {
    use crate::detectors::ai::ai_boilerplate::BoilerplatePattern;

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

#[cfg(test)]
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
// Single-pass fingerprinting (combines all feature extraction into one walk)
// ---------------------------------------------------------------------------

/// Compute all fingerprints for a function body in a single AST parse + walk.
///
/// Replaces calling `normalized_fingerprint`, `structural_fingerprint`,
/// `extract_identifiers`, and `detect_patterns` individually — each of which
/// re-parses the body with tree-sitter. This function parses once and collects
/// everything in a single tree traversal.
#[cfg(test)]
pub fn compute_all_fingerprints(body_text: &str, lang: Language) -> FunctionFingerprints {
    let tree = match parse_root(body_text, lang) {
        Some(t) => t,
        None => {
            return FunctionFingerprints {
                normalized_bigrams: HashSet::new(),
                structural_kinds: HashSet::new(),
                identifiers: Vec::new(),
                patterns: Vec::new(),
            };
        }
    };

    let mut normalized_tokens = Vec::new();
    let mut structural_kinds = HashSet::new();
    let mut identifiers = Vec::new();
    let mut all_kinds = HashSet::new();

    collect_all_features(
        tree.root_node(),
        body_text,
        &mut normalized_tokens,
        &mut structural_kinds,
        &mut identifiers,
        &mut all_kinds,
    );

    // Build bigrams from normalized tokens
    let mut normalized_bigrams = HashSet::new();
    for pair in normalized_tokens.windows(2) {
        normalized_bigrams.insert(format!("{}:{}", pair[0], pair[1]));
    }

    // Detect patterns from pre-computed kinds + content
    let patterns = detect_patterns_from_data(&all_kinds, body_text);

    FunctionFingerprints {
        normalized_bigrams,
        structural_kinds,
        identifiers,
        patterns,
    }
}

/// Parse functions AND compute all fingerprints in a single file parse.
///
/// This is the zero-reparse approach inspired by AST-T5 (arXiv:2401.03003):
/// instead of extracting function body text and re-parsing it, we walk the
/// body subtree directly during the initial file parse. Eliminates N redundant
/// tree-sitter parses per file (one per function).
pub fn parse_functions_with_fingerprints(
    content: &str,
    lang: Language,
) -> Vec<(FunctionInfo, FunctionFingerprints)> {
    let tree = match parse_root(content, lang) {
        Some(t) => t,
        None => return vec![],
    };

    let kinds: HashSet<&str> = function_node_kinds(lang).iter().copied().collect();
    let mut results = Vec::new();
    collect_functions_with_fp(tree.root_node(), content, lang, &kinds, &mut results);
    results
}

fn collect_functions_with_fp(
    node: Node,
    source: &str,
    lang: Language,
    func_kinds: &HashSet<&str>,
    out: &mut Vec<(FunctionInfo, FunctionFingerprints)>,
) {
    if func_kinds.contains(node.kind()) {
        let name = node
            .child_by_field_name(name_field(lang))
            .map(|n| node_text(n, source).to_string())
            .unwrap_or_else(|| "<anonymous>".to_string());

        let body_node = node.child_by_field_name(body_field(lang));
        let body_text = body_node
            .map(|n| node_text(n, source).to_string())
            .unwrap_or_else(|| node_text(node, source).to_string());

        // Walk body subtree directly — no re-parsing needed
        let walk_node = body_node.unwrap_or(node);
        let mut normalized_tokens = Vec::new();
        let mut structural_kinds = HashSet::new();
        let mut identifiers = Vec::new();
        let mut all_kinds = HashSet::new();

        collect_all_features(
            walk_node,
            source,
            &mut normalized_tokens,
            &mut structural_kinds,
            &mut identifiers,
            &mut all_kinds,
        );

        // Build bigrams
        let mut normalized_bigrams = HashSet::new();
        for pair in normalized_tokens.windows(2) {
            normalized_bigrams.insert(format!("{}:{}", pair[0], pair[1]));
        }

        // Detect patterns
        let patterns = detect_patterns_from_data(&all_kinds, &body_text);

        out.push((
            FunctionInfo {
                name,
                line_start: node.start_position().row as u32 + 1,
                line_end: node.end_position().row as u32 + 1,
                body_text,
                language: lang,
            },
            FunctionFingerprints {
                normalized_bigrams,
                structural_kinds,
                identifiers,
                patterns,
            },
        ));
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_functions_with_fp(child, source, lang, func_kinds, out);
        }
    }
}

/// Single-pass AST walker that collects all feature data simultaneously.
///
/// Combines the work of `collect_normalized_tokens`, `collect_structural_kinds`,
/// `collect_identifiers`, and `collect_all_kinds` into one tree walk.
pub fn collect_all_features(
    node: Node,
    source: &str,
    normalized_tokens: &mut Vec<String>,
    structural_kinds: &mut HashSet<String>,
    identifiers: &mut Vec<String>,
    all_kinds: &mut HashSet<String>,
) {
    let kind = node.kind();

    // For pattern detection: collect ALL node kinds
    all_kinds.insert(kind.to_string());

    if node.child_count() == 0 {
        // Leaf node — selectively normalize for fingerprinting
        match kind {
            "identifier" => {
                let normalized = normalize_identifier(node, source);
                normalized_tokens.push(normalized.to_string());
                // Also collect identifier text
                let text = node_text(node, source);
                if !text.is_empty() {
                    identifiers.push(text.to_string());
                }
            }
            "type_identifier" => {
                // Always preserve type names — they carry semantic meaning
                normalized_tokens.push(node_text(node, source).to_string());
            }
            "property_identifier" | "field_identifier" => {
                // Preserve field/property names
                normalized_tokens.push(node_text(node, source).to_string());
            }
            "shorthand_property_identifier" => {
                normalized_tokens.push("$ID".to_string());
            }
            "integer"
            | "float"
            | "number"
            | "number_literal"
            | "decimal_integer_literal"
            | "hex_integer_literal" => {
                let text = node_text(node, source);
                normalized_tokens.push(format!("$LIT:{}", text));
            }
            "string" | "string_literal" | "template_string" | "raw_string_literal" => {
                let text = node_text(node, source);
                if text.len() <= 52 {
                    // 50 chars + 2 for quotes
                    normalized_tokens.push(format!("$STR:{}", text));
                } else {
                    normalized_tokens.push("$STR".to_string());
                }
            }
            "true" | "false" | "none" | "null" | "undefined" => {
                normalized_tokens.push("$CONST".to_string());
            }
            "," | ";" | ":" | "." | "(" | ")" | "{" | "}" | "[" | "]" => {}
            _ => {
                normalized_tokens.push(kind.to_string());
            }
        }
    } else {
        // Internal node
        if is_structural_kind(kind) {
            normalized_tokens.push(kind.to_string());
            structural_kinds.insert(kind.to_string());
        }
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_all_features(
                child,
                source,
                normalized_tokens,
                structural_kinds,
                identifiers,
                all_kinds,
            );
        }
    }
}

/// Detect boilerplate patterns from pre-computed node kinds and content.
///
/// Same logic as [`detect_patterns`] but takes pre-computed data instead of
/// re-parsing the content.
pub fn detect_patterns_from_data(
    node_kinds: &HashSet<String>,
    content: &str,
) -> Vec<crate::detectors::ai::ai_boilerplate::BoilerplatePattern> {
    use crate::detectors::ai::ai_boilerplate::BoilerplatePattern;

    let content_lower = content.to_lowercase();
    let mut patterns = Vec::new();

    if node_kinds.contains("try_statement")
        || node_kinds.contains("except_clause")
        || node_kinds.contains("catch_clause")
    {
        patterns.push(BoilerplatePattern::TryExcept);
    }

    if node_kinds.contains("raise_statement")
        || node_kinds.contains("throw_statement")
        || content_lower.contains("error")
        || content_lower.contains("exception")
    {
        patterns.push(BoilerplatePattern::ErrorHandling);
    }

    let has_if = node_kinds.contains("if_statement") || node_kinds.contains("if_expression");
    if has_if
        && (content_lower.contains("valid")
            || content_lower.contains("check")
            || content_lower.contains("assert")
            || content_lower.contains("isinstance"))
    {
        patterns.push(BoilerplatePattern::Validation);
    }

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

    if content_lower.contains("execute")
        || content_lower.contains("query")
        || content_lower.contains("cursor")
        || content_lower.contains("session.")
        || content_lower.contains("commit(")
        || content_lower.contains("rollback(")
    {
        patterns.push(BoilerplatePattern::Database);
    }

    if content_lower.contains("create")
        || content_lower.contains("update")
        || content_lower.contains("delete")
        || content_lower.contains("find_by")
        || content_lower.contains("get_by")
    {
        patterns.push(BoilerplatePattern::Crud);
    }

    if node_kinds.contains("with_statement") || node_kinds.contains("with_clause") {
        patterns.push(BoilerplatePattern::ContextManager);
    }

    if node_kinds.contains("for_statement")
        || node_kinds.contains("while_statement")
        || node_kinds.contains("for_in_statement")
    {
        patterns.push(BoilerplatePattern::Loop);
    }

    if content_lower.contains("async ")
        || content_lower.contains("await ")
        || node_kinds.contains("await_expression")
    {
        patterns.push(BoilerplatePattern::Async);
    }

    patterns
}

// ---------------------------------------------------------------------------
// Lightweight boilerplate-only fingerprinting (skips bigrams/identifiers)
// ---------------------------------------------------------------------------

/// Lightweight fingerprint for AIBoilerplate — only structural kinds and patterns.
///
/// ~25x fewer allocations than full `FunctionFingerprints` because it skips
/// normalized token collection, bigram building, and identifier extraction.
#[derive(Debug, Clone)]
pub struct BoilerplateFingerprint {
    /// Structural AST kinds (for MinHash/LSH clustering).
    pub structural_kinds: HashSet<String>,
    /// Detected boilerplate patterns.
    pub patterns: Vec<crate::detectors::ai::ai_boilerplate::BoilerplatePattern>,
}

/// Parse functions and compute ONLY structural fingerprints (for AIBoilerplate).
///
/// Compared to `parse_functions_with_fingerprints`, this:
/// - Skips body_text allocation (biggest win: avoids storing full function body per function)
/// - Skips normalized token collection and bigram building
/// - Skips identifier extraction
/// - Uses inline pattern flags instead of collecting all node kinds into a HashSet
pub fn parse_functions_for_boilerplate(
    content: &str,
    lang: Language,
) -> Vec<(FunctionInfo, BoilerplateFingerprint)> {
    let tree = match parse_root(content, lang) {
        Some(t) => t,
        None => return vec![],
    };

    let kinds: HashSet<&str> = function_node_kinds(lang).iter().copied().collect();
    let mut results = Vec::new();
    collect_functions_boilerplate(tree.root_node(), content, lang, &kinds, &mut results);
    results
}

/// Pattern flags detected during AST walk — avoids HashSet<String> for all_kinds.
#[derive(Default)]
struct PatternFlags {
    has_try: bool,
    has_except: bool,
    has_catch: bool,
    has_raise: bool,
    has_throw: bool,
    has_if: bool,
    has_for: bool,
    has_while: bool,
    has_for_in: bool,
    has_with: bool,
    has_with_clause: bool,
    has_await: bool,
}

fn collect_functions_boilerplate(
    node: Node,
    source: &str,
    lang: Language,
    func_kinds: &HashSet<&str>,
    out: &mut Vec<(FunctionInfo, BoilerplateFingerprint)>,
) {
    if func_kinds.contains(node.kind()) {
        let name = node
            .child_by_field_name(name_field(lang))
            .map(|n| node_text(n, source).to_string())
            .unwrap_or_else(|| "<anonymous>".to_string());

        let body_node = node.child_by_field_name(body_field(lang));
        let walk_node = body_node.unwrap_or(node);

        // Lightweight walk: only structural kinds + pattern flags
        let mut structural_kinds = HashSet::new();
        let mut flags = PatternFlags::default();
        collect_structural_only(walk_node, &mut structural_kinds, &mut flags);

        // Detect patterns from flags + source slice (no body_text allocation)
        let body_start = walk_node.start_byte();
        let body_end = walk_node.end_byte();
        let body_slice = &source[body_start..body_end];
        let patterns = detect_patterns_from_flags(&flags, body_slice);

        out.push((
            FunctionInfo {
                name,
                line_start: node.start_position().row as u32 + 1,
                line_end: node.end_position().row as u32 + 1,
                body_text: String::new(), // Not needed by AIBoilerplate
                language: lang,
            },
            BoilerplateFingerprint {
                structural_kinds,
                patterns,
            },
        ));
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_functions_boilerplate(child, source, lang, func_kinds, out);
        }
    }
}

/// Lightweight AST walker: collects only structural kinds and sets pattern flags.
/// No String allocations for leaf nodes, no normalized tokens, no identifiers.
fn collect_structural_only(
    node: Node,
    structural_kinds: &mut HashSet<String>,
    flags: &mut PatternFlags,
) {
    let kind = node.kind();

    // Set pattern flags (replaces all_kinds HashSet)
    match kind {
        "try_statement" => flags.has_try = true,
        "except_clause" => flags.has_except = true,
        "catch_clause" => flags.has_catch = true,
        "raise_statement" => flags.has_raise = true,
        "throw_statement" => flags.has_throw = true,
        "if_statement" | "if_expression" => flags.has_if = true,
        "for_statement" => flags.has_for = true,
        "while_statement" => flags.has_while = true,
        "for_in_statement" => flags.has_for_in = true,
        "with_statement" => flags.has_with = true,
        "with_clause" => flags.has_with_clause = true,
        "await_expression" => flags.has_await = true,
        _ => {}
    }

    // Only collect structural kinds (internal nodes that pass the filter)
    if node.child_count() > 0 && is_structural_kind(kind) {
        structural_kinds.insert(kind.to_string());
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_structural_only(child, structural_kinds, flags);
        }
    }
}

/// Detect boilerplate patterns from pre-computed flags + source slice.
fn detect_patterns_from_flags(
    flags: &PatternFlags,
    body: &str,
) -> Vec<crate::detectors::ai::ai_boilerplate::BoilerplatePattern> {
    use crate::detectors::ai::ai_boilerplate::BoilerplatePattern;

    let content_lower = body.to_lowercase();
    let mut patterns = Vec::new();

    if flags.has_try || flags.has_except || flags.has_catch {
        patterns.push(BoilerplatePattern::TryExcept);
    }

    if flags.has_raise
        || flags.has_throw
        || content_lower.contains("error")
        || content_lower.contains("exception")
    {
        patterns.push(BoilerplatePattern::ErrorHandling);
    }

    if flags.has_if
        && (content_lower.contains("valid")
            || content_lower.contains("check")
            || content_lower.contains("assert")
            || content_lower.contains("isinstance"))
    {
        patterns.push(BoilerplatePattern::Validation);
    }

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

    if content_lower.contains("execute")
        || content_lower.contains("query")
        || content_lower.contains("cursor")
        || content_lower.contains("session.")
        || content_lower.contains("commit(")
        || content_lower.contains("rollback(")
    {
        patterns.push(BoilerplatePattern::Database);
    }

    if content_lower.contains("create")
        || content_lower.contains("update")
        || content_lower.contains("delete")
        || content_lower.contains("find_by")
        || content_lower.contains("get_by")
    {
        patterns.push(BoilerplatePattern::Crud);
    }

    if flags.has_with || flags.has_with_clause {
        patterns.push(BoilerplatePattern::ContextManager);
    }

    if flags.has_for || flags.has_while || flags.has_for_in {
        patterns.push(BoilerplatePattern::Loop);
    }

    if flags.has_await || content_lower.contains("async ") || content_lower.contains("await ") {
        patterns.push(BoilerplatePattern::Async);
    }

    patterns
}

// ---------------------------------------------------------------------------
// MinHash + LSH for approximate Jaccard similarity (arXiv:2102.08942)
// ---------------------------------------------------------------------------
//
// Instead of O(n²) pairwise Jaccard, MinHash signatures are computed in
// O(n·k·|set|) and LSH banding finds candidate pairs in near-linear time.
// Only candidates are verified with exact Jaccard — no false positives in output.
//
// Parameters for threshold ≈ 0.70:
//   b=20 bands, r=5 rows → k=100 hash functions
//   P(candidate | J=0.70) ≈ 97.5%
//   P(candidate | J=0.50) ≈ 47%
//   P(candidate | J=0.30) ≈ 4.8%

/// Number of hash functions for MinHash signatures.
const MINHASH_NUM_HASHES: usize = 100;
/// Number of LSH bands.
const LSH_BANDS: usize = 20;
/// Rows per band (MINHASH_NUM_HASHES / LSH_BANDS).
const LSH_ROWS: usize = 5;

/// Pre-computed hash function coefficients for MinHash.
struct MinHashCoeffs {
    a: [u64; MINHASH_NUM_HASHES],
    b: [u64; MINHASH_NUM_HASHES],
}

impl MinHashCoeffs {
    /// Generate deterministic coefficients from a fixed seed (reproducible results).
    fn new() -> Self {
        let mut state: u64 = 0x12345678_9abcdef0;
        let mut a = [0u64; MINHASH_NUM_HASHES];
        let mut b = [0u64; MINHASH_NUM_HASHES];
        for i in 0..MINHASH_NUM_HASHES {
            // LCG (Knuth's constants)
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            a[i] = state | 1; // Ensure odd (invertible mod 2^64)
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            b[i] = state;
        }
        Self { a, b }
    }
}

/// Fast hash for a string element (FxHash-like mixing).
fn hash_element(s: &str) -> u64 {
    let mut hash: u64 = 0;
    for byte in s.bytes() {
        hash = (hash.rotate_left(5) ^ (byte as u64)).wrapping_mul(0x517cc1b727220a95);
    }
    hash
}

/// Compute MinHash signature for a single set.
fn minhash_signature(set: &HashSet<String>, coeffs: &MinHashCoeffs) -> [u64; MINHASH_NUM_HASHES] {
    let mut sig = [u64::MAX; MINHASH_NUM_HASHES];
    for item in set {
        let h = hash_element(item);
        for k in 0..MINHASH_NUM_HASHES {
            let val = h.wrapping_mul(coeffs.a[k]).wrapping_add(coeffs.b[k]);
            if val < sig[k] {
                sig[k] = val;
            }
        }
    }
    sig
}

/// Hash an LSH band into a single bucket key.
fn hash_band(band: &[u64]) -> u64 {
    let mut hash: u64 = 0;
    for &val in band {
        hash = hash.wrapping_mul(0x9e3779b97f4a7c15).wrapping_add(val);
    }
    hash
}

/// Estimate Jaccard similarity from two MinHash signatures.
///
/// Standard error ≈ 1/√k ≈ 0.1 at k=100 — acceptable for code quality detection.
pub fn minhash_jaccard(sig1: &[u64; MINHASH_NUM_HASHES], sig2: &[u64; MINHASH_NUM_HASHES]) -> f64 {
    let matching = sig1.iter().zip(sig2.iter()).filter(|(a, b)| a == b).count();
    matching as f64 / MINHASH_NUM_HASHES as f64
}

/// Compute a MinHash signature for a set of strings (public wrapper).
pub fn compute_minhash_signature(set: &HashSet<String>) -> [u64; MINHASH_NUM_HASHES] {
    static COEFFS: std::sync::LazyLock<MinHashCoeffs> =
        std::sync::LazyLock::new(MinHashCoeffs::new);
    minhash_signature(set, &COEFFS)
}

/// Find candidate pairs likely to have Jaccard similarity >= ~0.70.
///
/// Uses MinHash signatures + LSH banding to reduce O(n²) pairwise
/// comparisons to near-linear. Returns index pairs (i, j) where i < j.
/// Callers should verify candidates with exact Jaccard — LSH produces
/// no false positives (only false negatives at low similarity).
pub fn lsh_candidate_pairs(sets: &[&HashSet<String>]) -> HashSet<(usize, usize)> {
    let n = sets.len();
    if n < 2 {
        return HashSet::new();
    }

    use rayon::prelude::*;

    let coeffs = MinHashCoeffs::new();

    // Compute all signatures in parallel: O(n · k · avg|set|)
    let signatures: Vec<[u64; MINHASH_NUM_HASHES]> = sets
        .par_iter()
        .map(|set| minhash_signature(set, &coeffs))
        .collect();

    lsh_banding(&signatures)
}

/// Find candidate pairs from pre-computed MinHash signatures.
///
/// Same LSH banding as `lsh_candidate_pairs` but skips signature computation.
/// Used when MinHash sigs are pre-computed during the parse phase.
pub fn lsh_candidate_pairs_from_sigs(
    sigs: &[[u64; MINHASH_NUM_HASHES]],
) -> HashSet<(usize, usize)> {
    if sigs.len() < 2 {
        return HashSet::new();
    }
    lsh_banding(sigs)
}

/// LSH banding on pre-computed signatures: O(n · b).
fn lsh_banding(signatures: &[[u64; MINHASH_NUM_HASHES]]) -> HashSet<(usize, usize)> {
    let mut candidates = HashSet::new();
    for band in 0..LSH_BANDS {
        let start = band * LSH_ROWS;
        let end = start + LSH_ROWS;

        let mut buckets: std::collections::HashMap<u64, Vec<usize>> =
            std::collections::HashMap::new();
        for (i, sig) in signatures.iter().enumerate() {
            let bucket_key = hash_band(&sig[start..end]);
            buckets.entry(bucket_key).or_default().push(i);
        }

        for bucket in buckets.values() {
            if bucket.len() < 2 {
                continue;
            }
            for (a_idx, &a) in bucket.iter().enumerate() {
                for &b in bucket.iter().skip(a_idx + 1) {
                    let pair = if a < b { (a, b) } else { (b, a) };
                    candidates.insert(pair);
                }
            }
        }
    }
    candidates
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
        assert_eq!(
            funcs.len(),
            2,
            "Should find 2 functions, got {}",
            funcs.len()
        );

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
    fn test_normalized_fingerprint_selective_normalization() {
        // Same call targets, different local variables — should match
        // (local variables are normalized to $ID, call targets are preserved).
        let code_a = r#"
x = validate(1)
if x > 0:
    transform(x)
"#;
        let code_b = r#"
y = validate(1)
if y > 0:
    transform(y)
"#;
        let fp_a = normalized_fingerprint(code_a, Language::Python);
        let fp_b = normalized_fingerprint(code_b, Language::Python);

        assert_eq!(
            fp_a, fp_b,
            "Same structure with same call targets but different local vars should produce identical fingerprints.\n  A: {:?}\n  B: {:?}",
            fp_a, fp_b
        );

        // Different call targets — should NOT match (call targets are preserved).
        let code_c = r#"
x = foo(1)
if x > 0:
    bar(x)
"#;
        let code_d = r#"
y = baz(1)
if y > 0:
    qux(y)
"#;
        let fp_c = normalized_fingerprint(code_c, Language::Python);
        let fp_d = normalized_fingerprint(code_d, Language::Python);

        assert_ne!(
            fp_c, fp_d,
            "Different call targets should produce different fingerprints.\n  C: {:?}\n  D: {:?}",
            fp_c, fp_d
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
            patterns.contains(&crate::detectors::ai::ai_boilerplate::BoilerplatePattern::TryExcept),
            "Should detect TryExcept pattern. Got: {:?}",
            patterns
        );
    }
}
