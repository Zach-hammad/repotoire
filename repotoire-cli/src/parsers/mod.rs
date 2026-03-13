//! Source code parsers using tree-sitter
//!
//! This module provides language-specific parsers for extracting code entities
//! (functions, classes, imports) and relationships (calls) from source files.

mod csharp;
mod go;
mod java;
pub mod python;
mod rust;
mod typescript;
// mod kotlin;
mod c;
mod cpp;

/// Streaming AST processing for memory-efficient large repo analysis
pub mod streaming;

/// TRUE streaming with ultra-lightweight info extraction
pub mod lightweight;
pub mod lightweight_parser;

/// Memory-bounded parallel pipeline with adaptive sizing
pub mod bounded_pipeline;

// Re-export lightweight types for convenience
pub use lightweight_parser::parse_file_lightweight;

use anyhow::Result;
use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::LazyLock;
use tree_sitter::Node;

// Performance guardrail: skip very large source files in AST parsing (#48).
const MAX_PARSE_FILE_BYTES: u64 = 2 * 1024 * 1024; // 2MB

/// Cached fingerprint for a single function, computed during the parse phase.
/// Contains all data needed by AIBoilerplate and AIDuplicateBlock detectors,
/// eliminating tree-sitter re-parsing in both.
#[derive(Debug, Clone)]
pub struct CachedFunctionFP {
    pub name: String,
    pub line_start: u32,
    pub line_end: u32,
    /// Structural AST kinds (for boilerplate clustering).
    pub structural_kinds: HashSet<String>,
    /// Normalized bigram fingerprint (for duplicate detection).
    pub normalized_bigrams: HashSet<String>,
    /// All identifier names in the function body.
    pub identifiers: Vec<String>,
    /// Detected boilerplate patterns.
    pub patterns: Vec<crate::detectors::BoilerplatePattern>,
    /// Pre-computed MinHash signature for AIDuplicateBlock (avoids recomputing from bigrams).
    pub minhash_sig: Option<[u64; 100]>,
}

/// Global cache of per-file function fingerprints, populated during the parse
/// phase so AI detectors (AIBoilerplate, AIDuplicateBlock) can skip re-parsing.
/// Key: file path → vec of function fingerprints.
static FP_CACHE: LazyLock<dashmap::DashMap<String, Vec<CachedFunctionFP>>> =
    LazyLock::new(dashmap::DashMap::new);

/// Read all cached function fingerprints for a file.
pub fn get_cached_fps(file_path: &str) -> Option<Vec<CachedFunctionFP>> {
    FP_CACHE
        .get(file_path)
        .map(|entry| entry.value().clone())
}

/// Clear the fingerprint cache (called between analysis runs).
pub fn clear_structural_fingerprint_cache() {
    FP_CACHE.clear();
}

/// Find the smallest scope in `scope_map` that contains the given line.
/// Shared across language parsers to avoid structural duplication.
pub(crate) fn find_containing_scope(
    line: u32,
    scope_map: &HashMap<(u32, u32), String>,
) -> Option<String> {
    scope_map
        .iter()
        .filter(|((start, end), _)| line >= *start && line <= *end)
        .min_by_key(|((start, end), _)| end - start)
        .map(|(_, name)| name.clone())
}

/// Walk up the AST to check if a node is nested inside an ancestor of the given kind.
/// Shared across language parsers to avoid structural duplication.
pub(crate) fn is_inside_ancestor(node: &Node, ancestor_kind: &str) -> bool {
    let mut current = node.parent();
    while let Some(parent) = current {
        if parent.kind() == ancestor_kind {
            return true;
        }
        current = parent.parent();
    }
    false
}

fn is_probably_cpp_header(source: &str) -> bool {
    // Sample only first chunk for speed on large headers.
    let text = &source[..source.len().min(16 * 1024)];

    let cpp_markers = [
        "class ",
        "namespace ",
        "template<",
        "template <",
        "typename ",
        "constexpr",
        "std::",
        "using namespace",
        "#include <iostream>",
        "#include <vector>",
        "#include <string>",
    ];

    cpp_markers.iter().any(|m| text.contains(m))
}

/// Parse a file and extract all code entities and relationships
pub fn parse_file(path: &Path) -> Result<ParseResult> {
    parse_file_inner(path, false)
}

/// Parse a file AND extract symbolic values for constant propagation.
///
/// Only call this from the non-streaming pipeline where results feed into
/// `build_graph()` → `ValueStore`. Streaming mode should use `parse_file()`
/// to avoid wasting cycles on extraction whose results are discarded.
pub fn parse_file_with_values(path: &Path) -> Result<ParseResult> {
    parse_file_inner(path, true)
}

fn parse_file_inner(path: &Path, extract_values: bool) -> Result<ParseResult> {
    // Guardrail for pathological files that can blow up parse time/memory.
    if let Ok(meta) = std::fs::metadata(path) {
        if meta.len() > MAX_PARSE_FILE_BYTES {
            tracing::warn!(
                "Skipping {} ({:.1}MB exceeds {}MB guardrail)",
                path.display(),
                meta.len() as f64 / (1024.0 * 1024.0),
                MAX_PARSE_FILE_BYTES / (1024 * 1024),
            );
            return Ok(ParseResult::default());
        }
    }

    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

    // Read file once via global cache — all subsequent cache.content() calls
    // for this path will be instant DashMap hits.
    let source = match crate::cache::global_cache().content(path) {
        Some(s) => s,
        None => return Ok(ParseResult::default()),
    };

    let (mut parsed, tree) = match ext {
        // Python
        "py" | "pyi" => python::parse_source_with_tree(&source, path).map(|(r, t)| (Ok(r), Some(t)))?,

        // TypeScript/JavaScript
        "ts" | "tsx" | "js" | "jsx" | "mjs" | "cjs" => {
            typescript::parse_source_with_tree(&source, path, ext).map(|(r, t)| (Ok(r), Some(t)))?
        }

        // Rust
        "rs" => rust::parse_source_with_tree(&source, path).map(|(r, t)| (Ok(r), Some(t)))?,

        // Go
        "go" => go::parse_source_with_tree(&source, path).map(|(r, t)| (Ok(r), Some(t)))?,

        // Java
        "java" => java::parse_source_with_tree(&source, path).map(|(r, t)| (Ok(r), Some(t)))?,

        // C#
        "cs" => csharp::parse_source_with_tree(&source, path).map(|(r, t)| (Ok(r), Some(t)))?,

        // Kotlin
        "kt" | "kts" => (Ok(ParseResult::default()), None), // kotlin disabled

        // C
        "c" => c::parse_source_with_tree(&source, path).map(|(r, t)| (Ok(r), Some(t)))?,

        // Header files: heuristic dispatch to C or C++ (#31)
        "h" => {
            if is_probably_cpp_header(&source) {
                cpp::parse_source_with_tree(&source, path).map(|(r, t)| (Ok(r), Some(t)))?
            } else {
                c::parse_source_with_tree(&source, path).map(|(r, t)| (Ok(r), Some(t)))?
            }
        }

        // C++
        "cpp" | "cc" | "cxx" | "c++" | "hpp" | "hh" | "hxx" | "h++" => {
            cpp::parse_source_with_tree(&source, path).map(|(r, t)| (Ok(r), Some(t)))?
        }

        // Unknown extension
        _ => (Ok(ParseResult::default()), None),
    };

    // Enrich with nesting depth for all languages
    if let Ok(ref mut result) = parsed {
        enrich_nesting_depths(result, &source, path);
    }

    // Extract full function fingerprints from the tree for AI detectors.
    // Reuses the tree from the main parse — zero re-parsing overhead.
    if let (Ok(ref result), Some(ref tree)) = (&parsed, &tree) {
        extract_full_fps(tree, &source, ext, path, &result.functions);
    }

    // Extract symbolic values for the value oracle (constant propagation).
    // Only in non-streaming mode — streaming discards raw_values anyway.
    if extract_values {
        if let (Ok(ref mut result), Some(ref tree)) = (&mut parsed, &tree) {
            if let Some(config) = crate::values::configs::config_for_extension(ext) {
                let file_qualified_prefix = path
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("unknown");
                let raw = crate::values::extraction::extract_file_values(
                    tree,
                    &source,
                    &config,
                    &result.functions,
                    file_qualified_prefix,
                );
                result.raw_values = Some(raw);
            }
        }
    }

    // Pre-warm masked content cache using the EXISTING tree — avoids a second
    // tree-sitter parse per file.  For CPython (3,415 files) this eliminates
    // 3,415 redundant parses that `masked_content()` would otherwise trigger.
    if let Some(ref tree) = tree {
        let masked = crate::cache::masking::mask_non_code_with_tree(&source, ext, tree);
        crate::cache::global_cache().store_masked(path, masked);
    } else {
        // No tree (unsupported language) — fall back to lazy parse-on-demand
        let _ = crate::cache::global_cache().masked_content(path);
    }

    parsed
}

/// Extract full function fingerprints and store in the global cache.
/// Called during the parse phase using the SAME tree-sitter tree, eliminating the
/// need for AI detectors (AIBoilerplate, AIDuplicateBlock) to re-parse files.
///
/// Uses [`collect_all_features`] from ast_fingerprint to compute structural kinds,
/// normalized bigrams, identifiers, and patterns in a single tree walk.
fn extract_full_fps(
    tree: &tree_sitter::Tree,
    source: &str,
    ext: &str,
    path: &Path,
    functions: &[crate::models::Function],
) {
    use crate::detectors::ast_fingerprint;

    let lang = crate::parsers::lightweight::Language::from_extension(ext);
    let func_kinds = ast_fingerprint::function_node_kinds(lang);
    if func_kinds.is_empty() {
        return;
    }

    let path_str = path.to_string_lossy().to_string();

    // Build a lookup from (name, line_start) → line_end for functions in ParseResult
    let func_set: HashMap<(String, u32), u32> = functions
        .iter()
        .map(|f| ((f.name.clone(), f.line_start), f.line_end))
        .collect();

    let func_kind_set: HashSet<&str> = func_kinds.iter().copied().collect();
    let mut fps = Vec::new();
    collect_full_fps_recursive(
        tree.root_node(),
        source,
        &func_kind_set,
        &func_set,
        &mut fps,
    );

    // Pre-compute MinHash signatures AFTER recursion completes (not inside
    // collect_full_fps_recursive) to avoid adding [u64; 100] to the recursive
    // stack frame, which causes stack overflow on deeply nested ASTs (CPython).
    for fp in &mut fps {
        if !fp.normalized_bigrams.is_empty() {
            fp.minhash_sig = Some(
                crate::detectors::ast_fingerprint::compute_minhash_signature(&fp.normalized_bigrams),
            );
        }
    }

    if !fps.is_empty() {
        FP_CACHE.insert(path_str, fps);
    }
}

/// Recursively walk the tree to find function nodes and extract full fingerprints.
fn collect_full_fps_recursive(
    node: tree_sitter::Node,
    source: &str,
    func_kinds: &HashSet<&str>,
    func_set: &HashMap<(String, u32), u32>,
    out: &mut Vec<CachedFunctionFP>,
) {
    use crate::detectors::ast_fingerprint;

    if func_kinds.contains(node.kind()) {
        let line_start = node.start_position().row as u32 + 1; // tree-sitter is 0-based
        let name = node
            .child_by_field_name("name")
            .map(|n| n.utf8_text(source.as_bytes()).unwrap_or_default().to_string())
            .unwrap_or_default();

        if let Some(&line_end) = func_set.get(&(name.clone(), line_start)) {
            let body_node = node.child_by_field_name("body").unwrap_or(node);

            // Single-pass: collect all features at once
            let mut normalized_tokens = Vec::new();
            let mut structural_kinds = HashSet::new();
            let mut identifiers = Vec::new();
            let mut all_kinds = HashSet::new();

            ast_fingerprint::collect_all_features(
                body_node,
                source,
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

            // Detect boilerplate patterns from pre-computed kinds + body text
            let body_text = &source[body_node.start_byte()..body_node.end_byte()];
            let patterns = ast_fingerprint::detect_patterns_from_data(&all_kinds, body_text);

            // Include functions even if structural_kinds is empty — AIDuplicateBlock
            // needs normalized_bigrams which can be non-empty on any function.
            // NOTE: minhash_sig is computed AFTER recursion in extract_full_fps()
            // to avoid adding 800 bytes ([u64; 100]) to this recursive stack frame.
            if !structural_kinds.is_empty() || !normalized_bigrams.is_empty() {
                out.push(CachedFunctionFP {
                    name,
                    line_start,
                    line_end,
                    structural_kinds,
                    normalized_bigrams,
                    identifiers,
                    patterns,
                    minhash_sig: None,
                });
            }
        }
    }

    let count = node.child_count();
    for i in 0..count {
        if let Some(child) = node.child(i) {
            collect_full_fps_recursive(child, source, func_kinds, func_set, out);
        }
    }
}

/// Compute max nesting depth for each function from source code.
/// Uses brace counting for C-family languages, indent counting for Python.
fn enrich_nesting_depths(result: &mut ParseResult, source: &str, path: &Path) {
    let lines: Vec<&str> = source.lines().collect();
    let is_python = path.extension().is_some_and(|e| e == "py" || e == "pyi");

    for func in &mut result.functions {
        if func.max_nesting.is_some() {
            continue;
        }
        let start = func.line_start.saturating_sub(1) as usize;
        let end = (func.line_end as usize).min(lines.len());
        if start >= end {
            continue;
        }

        let max_depth = if is_python {
            compute_nesting_indent(&lines[start..end])
        } else {
            compute_nesting_braces(&lines[start..end])
        };
        func.max_nesting = Some(max_depth);
    }
}

/// Brace-based nesting for C-family languages
fn compute_nesting_braces(lines: &[&str]) -> u32 {
    let mut depth: i32 = 0;
    let mut max_depth: i32 = 0;
    // Start at -1 since the function's own opening brace is depth 0
    let mut found_first = false;

    for line in lines {
        let trimmed = line.trim();
        // Skip comments and strings (rough heuristic)
        if trimmed.starts_with("//") || trimmed.starts_with('*') || trimmed.starts_with("/*") {
            continue;
        }
        for ch in trimmed.chars() {
            match ch {
                '{' => {
                    if found_first {
                        depth += 1;
                        max_depth = max_depth.max(depth);
                    } else {
                        found_first = true;
                    }
                }
                '}' => {
                    if found_first {
                        depth -= 1;
                    }
                }
                _ => {}
            }
        }
    }
    max_depth.max(0) as u32
}

/// Indent-based nesting for Python
fn compute_nesting_indent(lines: &[&str]) -> u32 {
    if lines.is_empty() {
        return 0;
    }

    // Find base indentation from the function def line
    let base_indent = lines[0].len() - lines[0].trim_start().len();
    let mut max_extra = 0u32;

    for line in &lines[1..] {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let indent = line.len() - line.trim_start().len();
        if indent > base_indent {
            // Each 4-space (or 1-tab) indent level is ~1 nesting level
            let extra = ((indent - base_indent) / 4) as u32;
            max_extra = max_extra.max(extra);
        }
    }
    max_extra
}

/// Get the language name for a file extension
pub fn language_for_extension(ext: &str) -> Option<&'static str> {
    match ext {
        "py" | "pyi" => Some("Python"),
        "ts" | "tsx" => Some("TypeScript"),
        "js" | "jsx" | "mjs" | "cjs" => Some("JavaScript"),
        "rs" => Some("Rust"),
        "go" => Some("Go"),
        "java" => Some("Java"),
        "cs" => Some("C#"),
        "kt" | "kts" => Some("Kotlin"),
        "c" | "h" => Some("C"),
        "cpp" | "cc" | "cxx" | "c++" | "hpp" | "hh" | "hxx" | "h++" => Some("C++"),
        _ => None,
    }
}

/// Get all supported file extensions
#[allow(dead_code)] // Public API helper
pub fn supported_extensions() -> &'static [&'static str] {
    &[
        "py", "pyi", // Python
        "ts", "tsx", "js", "jsx", "mjs", "cjs",  // TypeScript/JavaScript
        "rs",   // Rust
        "go",   // Go
        "java", // Java
        "cs",   // C#
        "kt", "kts", // Kotlin
        "c", "h", // C
        "cpp", "cc", "cxx", "c++", "hpp", "hh", "hxx", "h++", // C++
    ]
}

/// Import information including whether it's type-only
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ImportInfo {
    /// The import path/module
    pub path: String,
    /// Whether this is a type-only import (e.g., TypeScript's `import type`)
    pub is_type_only: bool,
}

impl ImportInfo {
    /// Create a runtime import (not type-only)
    pub fn runtime(path: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            is_type_only: false,
        }
    }

    /// Create a type-only import
    pub fn type_only(path: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            is_type_only: true,
        }
    }
}

/// Result of parsing a source file
#[derive(Debug, Default, Clone, serde::Serialize, serde::Deserialize)]
pub struct ParseResult {
    /// Functions found in the file (including top-level and nested)
    pub functions: Vec<crate::models::Function>,

    /// Classes found in the file
    pub classes: Vec<crate::models::Class>,

    /// Module/package imports
    pub imports: Vec<ImportInfo>,

    /// Function calls as (caller_qualified_name, callee_name) pairs
    pub calls: Vec<(String, String)>,

    /// Function names whose addresses are taken (used as callbacks, in tables, etc.)
    /// These should not be flagged as dead code even if they have zero direct callers.
    pub address_taken: std::collections::HashSet<String>,

    /// Extracted symbolic values (assignments, constants, returns) for the value oracle.
    /// Populated by the value extraction pass during parsing.
    #[serde(skip)]
    pub raw_values: Option<crate::values::store::RawParseValues>,
}

impl ParseResult {
    /// Create a new empty ParseResult
    pub fn new() -> Self {
        Self::default()
    }

    /// Merge another ParseResult into this one
    pub fn merge(&mut self, other: ParseResult) {
        self.functions.extend(other.functions);
        self.classes.extend(other.classes);
        self.imports.extend(other.imports);
        self.calls.extend(other.calls);
        self.address_taken.extend(other.address_taken);

        // Merge raw value extraction results.
        if let Some(other_raw) = other.raw_values {
            if let Some(ref mut self_raw) = self.raw_values {
                self_raw.module_constants.extend(other_raw.module_constants);
                for (k, v) in other_raw.function_assignments {
                    self_raw.function_assignments.entry(k).or_default().extend(v);
                }
                for (k, v) in other_raw.return_expressions {
                    self_raw.return_expressions.insert(k, v);
                }
            } else {
                self.raw_values = Some(other_raw);
            }
        }
    }

    /// Check if the result is empty
    pub fn is_empty(&self) -> bool {
        self.functions.is_empty()
            && self.classes.is_empty()
            && self.imports.is_empty()
            && self.calls.is_empty()
    }

    /// Total number of entities found
    pub fn entity_count(&self) -> usize {
        self.functions.len() + self.classes.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn test_parse_python_file() {
        let path = PathBuf::from("test.py");
        // This would fail if file doesn't exist, but tests the dispatch logic
        let _ = parse_file(&path);
    }

    #[test]
    fn test_unknown_extension_returns_empty() {
        let path = PathBuf::from("test.unknown");
        let result = parse_file(&path).expect("should parse unknown extension");
        assert!(result.is_empty());
    }

    #[test]
    fn test_parse_result_merge() {
        use crate::models::{Class, Function};

        let mut result1 = ParseResult {
            functions: vec![Function {
                name: "func1".to_string(),
                qualified_name: "test::func1:1".to_string(),
                file_path: PathBuf::from("test.py"),
                line_start: 1,
                line_end: 5,
                parameters: vec![],
                return_type: None,
                is_async: false,
                complexity: None,
                max_nesting: None,
                doc_comment: None,
                annotations: vec![],
            }],
            classes: vec![],
            imports: vec![ImportInfo::runtime("os")],
            calls: vec![],
            address_taken: std::collections::HashSet::new(),
            raw_values: None,
        };

        let result2 = ParseResult {
            functions: vec![Function {
                name: "func2".to_string(),
                qualified_name: "test::func2:10".to_string(),
                file_path: PathBuf::from("test.py"),
                line_start: 10,
                line_end: 15,
                parameters: vec![],
                return_type: None,
                is_async: true,
                complexity: None,
                max_nesting: None,
                doc_comment: None,
                annotations: vec![],
            }],
            classes: vec![Class {
                name: "MyClass".to_string(),
                qualified_name: "test::MyClass:20".to_string(),
                file_path: PathBuf::from("test.py"),
                line_start: 20,
                line_end: 30,
                methods: vec![],
                field_count: 0,
                bases: vec![],
                doc_comment: None,
                annotations: vec![],
            }],
            imports: vec![ImportInfo::runtime("sys")],
            calls: vec![("test::func1:1".to_string(), "func2".to_string())],
            address_taken: std::collections::HashSet::new(),
            raw_values: None,
        };

        result1.merge(result2);

        assert_eq!(result1.functions.len(), 2);
        assert_eq!(result1.classes.len(), 1);
        assert_eq!(result1.imports.len(), 2);
        assert_eq!(result1.calls.len(), 1);
        assert_eq!(result1.entity_count(), 3);
    }

    #[test]
    fn test_language_for_extension() {
        assert_eq!(language_for_extension("py"), Some("Python"));
        assert_eq!(language_for_extension("ts"), Some("TypeScript"));
        assert_eq!(language_for_extension("rs"), Some("Rust"));
        assert_eq!(language_for_extension("go"), Some("Go"));
        assert_eq!(language_for_extension("java"), Some("Java"));
        assert_eq!(language_for_extension("cs"), Some("C#"));
        assert_eq!(language_for_extension("kt"), Some("Kotlin"));
        assert_eq!(language_for_extension("c"), Some("C"));
        assert_eq!(language_for_extension("cpp"), Some("C++"));
        assert_eq!(language_for_extension("unknown"), None);
    }

    #[test]
    fn test_header_dispatch_cpp_heuristic() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let hdr = dir.path().join("test.h");
        std::fs::write(
            &hdr,
            r#"
namespace demo {
class Widget { public: int x; };
}
"#,
        )
        .expect("should write test header");

        let result = parse_file(&hdr).expect("should parse C++ header");
        assert_eq!(result.classes.len(), 1);
    }

    #[test]
    fn test_header_dispatch_c_fallback() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let hdr = dir.path().join("test.h");
        std::fs::write(
            &hdr,
            r#"
#ifndef TEST_H
#define TEST_H
int add(int a, int b);
#endif
"#,
        )
        .expect("should write test header");

        let result = parse_file(&hdr).expect("should parse C header");
        assert!(result.functions.is_empty());
        assert!(result.classes.is_empty());
    }

    #[test]
    fn test_parse_file_skips_very_large_files() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let big = dir.path().join("big.py");
        // slightly over 2MB
        let payload = "x = 1\n".repeat((2 * 1024 * 1024 / 6) + 1024);
        std::fs::write(&big, payload).expect("should write large file");

        let result = parse_file(&big).expect("should handle large file");
        assert!(result.is_empty());
    }

    #[test]
    fn test_supported_extensions() {
        let exts = supported_extensions();
        assert!(exts.contains(&"py"));
        assert!(exts.contains(&"ts"));
        assert!(exts.contains(&"rs"));
        assert!(exts.contains(&"go"));
        assert!(exts.contains(&"java"));
        assert!(exts.contains(&"cs"));
        assert!(exts.contains(&"kt"));
        assert!(exts.contains(&"c"));
        assert!(exts.contains(&"cpp"));
    }

    #[test]
    fn test_parse_python_extracts_values() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("test.py");
        std::fs::write(
            &file,
            "TIMEOUT = 3600\n\ndef foo():\n    x = \"hello\"\n    return x\n",
        )
        .unwrap();
        let result = parse_file_with_values(&file).unwrap();
        let raw = result
            .raw_values
            .as_ref()
            .expect("raw_values should be populated");
        assert!(
            !raw.module_constants.is_empty(),
            "should extract module constant TIMEOUT"
        );
    }

    #[test]
    fn test_parse_typescript_extracts_values() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("test.ts");
        std::fs::write(
            &file,
            "const MAX = 100;\nfunction foo() { return MAX; }\n",
        )
        .unwrap();
        let result = parse_file_with_values(&file).unwrap();
        let raw = result
            .raw_values
            .as_ref()
            .expect("raw_values should be populated for TS");
        assert!(
            !raw.module_constants.is_empty(),
            "should extract TS constant"
        );
    }

    #[test]
    fn test_parse_unsupported_extension_no_values() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("test.xyz");
        std::fs::write(&file, "x = 1").unwrap();
        let result = parse_file(&file).unwrap();
        assert!(
            result.raw_values.is_none(),
            "unsupported extension should have no values"
        );
    }

    #[test]
    fn test_parse_file_skips_value_extraction() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("test.py");
        std::fs::write(&file, "TIMEOUT = 3600\n").unwrap();
        let result = parse_file(&file).unwrap();
        assert!(
            result.raw_values.is_none(),
            "parse_file() should skip value extraction for streaming perf"
        );
    }
}
