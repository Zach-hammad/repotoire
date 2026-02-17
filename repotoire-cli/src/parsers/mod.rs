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
            return Ok(ParseResult::default());
        }
    }

    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

    match ext {
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
    }
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
