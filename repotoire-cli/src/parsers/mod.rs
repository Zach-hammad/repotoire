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

/// Parallel pipeline using crossbeam channels
pub mod parallel_pipeline;

/// Memory-bounded parallel pipeline with adaptive sizing
pub mod bounded_pipeline;

// Re-export lightweight types for convenience
pub use lightweight::{Language, LightweightParseStats};
pub use lightweight_parser::parse_file_lightweight;

use anyhow::Result;
use std::path::Path;

// Performance guardrail: skip very large source files in AST parsing (#48).
const MAX_PARSE_FILE_BYTES: u64 = 2 * 1024 * 1024; // 2MB

fn is_probably_cpp_header(path: &Path) -> bool {
    let content = match std::fs::read(path) {
        Ok(bytes) => bytes,
        Err(_) => return false,
    };

    // Sample only first chunk for speed on large headers.
    let sample = &content[..content.len().min(16 * 1024)];
    let text = String::from_utf8_lossy(sample);

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

    let mut parsed = match ext {
        // Python
        "py" | "pyi" => python::parse(path),

        // TypeScript/JavaScript
        "ts" | "tsx" | "js" | "jsx" | "mjs" | "cjs" => typescript::parse(path),

        // Rust
        "rs" => rust::parse(path),

        // Go
        "go" => go::parse(path),

        // Java
        "java" => java::parse(path),

        // C#
        "cs" => csharp::parse(path),

        // Kotlin
        "kt" | "kts" => Ok(ParseResult::default()), // kotlin disabled

        // C
        "c" => c::parse(path),

        // Header files: heuristic dispatch to C or C++ (#31)
        "h" => {
            if is_probably_cpp_header(path) {
                cpp::parse(path)
            } else {
                c::parse(path)
            }
        }

        // C++
        "cpp" | "cc" | "cxx" | "c++" | "hpp" | "hh" | "hxx" | "h++" => cpp::parse(path),

        // Unknown extension
        _ => Ok(ParseResult::default()),
    };

    // Enrich with nesting depth for all languages
    if let Ok(ref mut result) = parsed {
        enrich_nesting_depths(result, path);
    }

    parsed
}

/// Compute max nesting depth for each function from source code.
/// Uses brace counting for C-family languages, indent counting for Python.
fn enrich_nesting_depths(result: &mut ParseResult, path: &Path) {
    let Ok(source) = std::fs::read_to_string(path) else {
        return;
    };
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
#[allow(dead_code)] // Public API helper
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
}

impl ParseResult {
    /// Create a new empty ParseResult
    pub fn new() -> Self {
        Self::default()
    }

    /// Merge another ParseResult into this one
    #[allow(dead_code)] // Public API method
    pub fn merge(&mut self, other: ParseResult) {
        self.functions.extend(other.functions);
        self.classes.extend(other.classes);
        self.imports.extend(other.imports);
        self.calls.extend(other.calls);
        self.address_taken.extend(other.address_taken);
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
        let result = parse_file(&path).unwrap();
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
            }],
            classes: vec![],
            imports: vec![ImportInfo::runtime("os")],
            calls: vec![],
            address_taken: std::collections::HashSet::new(),
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
            }],
            classes: vec![Class {
                name: "MyClass".to_string(),
                qualified_name: "test::MyClass:20".to_string(),
                file_path: PathBuf::from("test.py"),
                line_start: 20,
                line_end: 30,
                methods: vec![],
                bases: vec![],
            }],
            imports: vec![ImportInfo::runtime("sys")],
            calls: vec![("test::func1:1".to_string(), "func2".to_string())],
            address_taken: std::collections::HashSet::new(),
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
        let dir = tempfile::tempdir().unwrap();
        let hdr = dir.path().join("test.h");
        std::fs::write(
            &hdr,
            r#"
namespace demo {
class Widget { public: int x; };
}
"#,
        )
        .unwrap();

        let result = parse_file(&hdr).unwrap();
        assert_eq!(result.classes.len(), 1);
    }

    #[test]
    fn test_header_dispatch_c_fallback() {
        let dir = tempfile::tempdir().unwrap();
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
        .unwrap();

        let result = parse_file(&hdr).unwrap();
        assert!(result.functions.is_empty());
        assert!(result.classes.is_empty());
    }

    #[test]
    fn test_parse_file_skips_very_large_files() {
        let dir = tempfile::tempdir().unwrap();
        let big = dir.path().join("big.py");
        // slightly over 2MB
        let payload = "x = 1\n".repeat((2 * 1024 * 1024 / 6) + 1024);
        std::fs::write(&big, payload).unwrap();

        let result = parse_file(&big).unwrap();
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
}
