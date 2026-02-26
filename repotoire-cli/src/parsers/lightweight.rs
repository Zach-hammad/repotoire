//! TRUE streaming AST processing with minimal memory footprint
//!
//! This module provides `LightweightFileInfo` - an ultra-compact representation
//! of parsed file data that holds ONLY what detectors and graph building need.
//!
//! # Key Differences from ParseResult
//!
//! ```text
//! ParseResult (10+ functions):
//! - Vec<Function> with PathBuf EACH (~200 bytes × N)
//! - Full parameter lists with strings
//! - AST metadata
//! Total: ~2-5KB per file with 10 functions
//!
//! LightweightFileInfo (10+ functions):  
//! - Single PathBuf for the file
//! - Compact function info (no PathBuf duplication)
//! - Only essential fields
//! Total: ~300-500 bytes per file with 10 functions
//! ```
//!
//! # Memory Strategy
//!
//! The key insight is that tree-sitter ASTs are HUGE (10-50MB for large files),
//! but we only need a tiny fraction of that data. This module:
//!
//! 1. Parses file with tree-sitter
//! 2. Extracts essential info immediately
//! 3. DROPS the AST before returning
//! 4. Returns compact struct suitable for collection
//!
//! This means even with 20k files, memory stays bounded.

use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// Ultra-compact function info - no PathBuf, minimal strings
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LightweightFunctionInfo {
    /// Function name (not qualified - saves memory)
    pub name: String,
    /// Qualified name for graph edges
    pub qualified_name: String,
    /// Start line
    pub line_start: u32,
    /// End line
    pub line_end: u32,
    /// Number of parameters (not the full list - saves memory)
    pub param_count: u8,
    /// Is async function
    pub is_async: bool,
    /// Cyclomatic complexity
    pub complexity: u16,
    /// Whether this function has decorators/annotations (1 byte flag)
    #[serde(default)]
    pub has_annotations: bool,
}

impl LightweightFunctionInfo {
    /// Lines of code for this function
    pub fn loc(&self) -> u32 {
        if self.line_end >= self.line_start {
            self.line_end - self.line_start + 1
        } else {
            1
        }
    }
}

/// Ultra-compact class info
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LightweightClassInfo {
    /// Class name
    pub name: String,
    /// Qualified name for graph edges
    pub qualified_name: String,
    /// Start line
    pub line_start: u32,
    /// End line
    pub line_end: u32,
    /// Number of methods (not the full list)
    pub method_count: u16,
    /// Number of base classes
    pub base_count: u8,
}

/// Compact import info
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LightweightImport {
    /// Import path
    pub path: String,
    /// Is type-only import (TypeScript)
    pub is_type_only: bool,
}

/// Compact call edge
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LightweightCall {
    /// Caller qualified name
    pub caller: String,
    /// Callee name (may not be qualified)
    pub callee: String,
}

/// Language enum for efficient storage
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[repr(u8)]
pub enum Language {
    Python = 0,
    TypeScript = 1,
    JavaScript = 2,
    Rust = 3,
    Go = 4,
    Java = 5,
    CSharp = 6,
    Kotlin = 7,
    C = 8,
    Cpp = 9,
    Ruby = 10,
    Php = 11,
    Swift = 12,
    Unknown = 255,
}

impl Language {
    pub fn from_extension(ext: &str) -> Self {
        match ext {
            "py" | "pyi" => Language::Python,
            "ts" | "tsx" => Language::TypeScript,
            "js" | "jsx" | "mjs" | "cjs" => Language::JavaScript,
            "rs" => Language::Rust,
            "go" => Language::Go,
            "java" => Language::Java,
            "cs" => Language::CSharp,
            "kt" | "kts" => Language::Kotlin,
            "c" | "h" => Language::C,
            "cpp" | "cc" | "cxx" | "hpp" | "hh" => Language::Cpp,
            "rb" => Language::Ruby,
            "php" => Language::Php,
            "swift" => Language::Swift,
            _ => Language::Unknown,
        }
    }

    pub fn from_path(path: &Path) -> Self {
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

        if ext != "h" {
            return Self::from_extension(ext);
        }

        // Heuristic C/C++ header detection for .h files (#31)
        let content = match std::fs::read(path) {
            Ok(bytes) => bytes,
            Err(_) => return Language::C,
        };
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
        ];

        if cpp_markers.iter().any(|m| text.contains(m)) {
            Language::Cpp
        } else {
            Language::C
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Language::Python => "Python",
            Language::TypeScript => "TypeScript",
            Language::JavaScript => "JavaScript",
            Language::Rust => "Rust",
            Language::Go => "Go",
            Language::Java => "Java",
            Language::CSharp => "C#",
            Language::Kotlin => "Kotlin",
            Language::C => "C",
            Language::Cpp => "C++",
            Language::Ruby => "Ruby",
            Language::Php => "PHP",
            Language::Swift => "Swift",
            Language::Unknown => "Unknown",
        }
    }
}

/// Ultra-lightweight file info - the ONLY struct we collect during streaming
///
/// Memory target: <500 bytes for a typical file with 10 functions
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LightweightFileInfo {
    /// File path (only place PathBuf appears)
    pub path: PathBuf,
    /// Language (1 byte instead of String)
    pub language: Language,
    /// Lines of code
    pub loc: u32,
    /// Functions in this file
    pub functions: Vec<LightweightFunctionInfo>,
    /// Classes in this file
    pub classes: Vec<LightweightClassInfo>,
    /// Imports from this file
    pub imports: Vec<LightweightImport>,
    /// Call edges originating from this file
    pub calls: Vec<LightweightCall>,
    /// Functions whose address is taken (callbacks, etc.)
    pub address_taken: HashSet<String>,
}

impl LightweightFileInfo {
    /// Create empty info for a path
    #[allow(dead_code)] // Public API
    pub fn empty(path: PathBuf, language: Language) -> Self {
        Self {
            path,
            language,
            loc: 0,
            functions: Vec::new(),
            classes: Vec::new(),
            imports: Vec::new(),
            calls: Vec::new(),
            address_taken: HashSet::new(),
        }
    }

    /// Get relative path string for graph building
    pub fn relative_path(&self, repo_root: &Path) -> String {
        self.path
            .strip_prefix(repo_root)
            .unwrap_or(&self.path)
            .display()
            .to_string()
    }

    /// Check if file is empty (no functions or classes)
    #[allow(dead_code)] // Public API
    pub fn is_empty(&self) -> bool {
        self.functions.is_empty() && self.classes.is_empty()
    }

    /// Total entities count
    #[allow(dead_code)] // Public API
    pub fn entity_count(&self) -> usize {
        self.functions.len() + self.classes.len()
    }

    /// Estimated memory usage in bytes
    pub fn estimated_memory(&self) -> usize {
        let base = std::mem::size_of::<Self>();
        let path_size = self.path.as_os_str().len();
        let funcs_size = self.functions.len() * std::mem::size_of::<LightweightFunctionInfo>();
        let classes_size = self.classes.len() * std::mem::size_of::<LightweightClassInfo>();
        let imports_size: usize = self.imports.iter().map(|i| i.path.len() + 2).sum();
        let calls_size: usize = self
            .calls
            .iter()
            .map(|c| c.caller.len() + c.callee.len())
            .sum();

        base + path_size + funcs_size + classes_size + imports_size + calls_size
    }
}

/// Convert from full ParseResult to LightweightFileInfo
///
/// This is a COMPATIBILITY function for gradual migration.
/// Prefer using `parse_file_lightweight()` directly for true streaming.
impl LightweightFileInfo {
    pub fn from_parse_result(
        result: &crate::parsers::ParseResult,
        path: PathBuf,
        language: Language,
        loc: u32,
    ) -> Self {
        let functions = result
            .functions
            .iter()
            .map(|f| LightweightFunctionInfo {
                name: f.name.clone(),
                qualified_name: f.qualified_name.clone(),
                line_start: f.line_start,
                line_end: f.line_end,
                param_count: f.parameters.len().min(255) as u8,
                is_async: f.is_async,
                complexity: f.complexity.unwrap_or(1).min(65535) as u16,
                has_annotations: !f.annotations.is_empty(),
            })
            .collect();

        let classes = result
            .classes
            .iter()
            .map(|c| LightweightClassInfo {
                name: c.name.clone(),
                qualified_name: c.qualified_name.clone(),
                line_start: c.line_start,
                line_end: c.line_end,
                method_count: c.methods.len().min(65535) as u16,
                base_count: c.bases.len().min(255) as u8,
            })
            .collect();

        let imports = result
            .imports
            .iter()
            .map(|i| LightweightImport {
                path: i.path.clone(),
                is_type_only: i.is_type_only,
            })
            .collect();

        let calls = result
            .calls
            .iter()
            .map(|(caller, callee)| LightweightCall {
                caller: caller.clone(),
                callee: callee.clone(),
            })
            .collect();

        Self {
            path,
            language,
            loc,
            functions,
            classes,
            imports,
            calls,
            address_taken: result.address_taken.clone(),
        }
    }
}

/// Statistics from lightweight parsing
#[derive(Debug, Clone, Default)]
pub struct LightweightParseStats {
    #[allow(dead_code)] // Included in stats
    pub total_files: usize,
    pub parsed_files: usize,
    #[allow(dead_code)] // Included in stats
    pub skipped_files: usize,
    pub total_functions: usize,
    pub total_classes: usize,
    pub total_imports: usize,
    pub total_calls: usize,
    pub parse_errors: usize,
    pub estimated_memory_bytes: usize,
}

impl LightweightParseStats {
    /// Add stats from a single file
    pub fn add_file(&mut self, info: &LightweightFileInfo) {
        self.parsed_files += 1;
        self.total_functions += info.functions.len();
        self.total_classes += info.classes.len();
        self.total_imports += info.imports.len();
        self.total_calls += info.calls.len();
        self.estimated_memory_bytes += info.estimated_memory();
    }

    /// Human-readable memory estimate
    #[allow(dead_code)] // Public API
    pub fn memory_human(&self) -> String {
        let bytes = self.estimated_memory_bytes;
        if bytes < 1024 {
            format!("{} B", bytes)
        } else if bytes < 1024 * 1024 {
            format!("{:.1} KB", bytes as f64 / 1024.0)
        } else {
            format!("{:.1} MB", bytes as f64 / (1024.0 * 1024.0))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile;

    #[test]
    fn test_language_from_extension() {
        assert_eq!(Language::from_extension("py"), Language::Python);
        assert_eq!(Language::from_extension("ts"), Language::TypeScript);
        assert_eq!(Language::from_extension("rs"), Language::Rust);
        assert_eq!(Language::from_extension("go"), Language::Go);
        assert_eq!(Language::from_extension("xyz"), Language::Unknown);
    }

    #[test]
    fn test_lightweight_function_loc() {
        let func = LightweightFunctionInfo {
            name: "test".to_string(),
            qualified_name: "mod::test:1".to_string(),
            line_start: 10,
            line_end: 20,
            param_count: 2,
            is_async: false,
            complexity: 5,
            has_annotations: false,
        };
        assert_eq!(func.loc(), 11);
    }

    #[test]
    fn test_language_from_path_header_heuristic() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let h = dir.path().join("x.h");
        std::fs::write(&h, "namespace n { class A {}; }").expect("should write test header");
        assert_eq!(Language::from_path(&h), Language::Cpp);

        std::fs::write(&h, "#ifndef X_H\nint sum(int a, int b);\n#endif").expect("should write C header");
        assert_eq!(Language::from_path(&h), Language::C);
    }

    fn test_estimated_memory() {
        let info = LightweightFileInfo::empty(PathBuf::from("test.py"), Language::Python);
        let mem = info.estimated_memory();
        assert!(mem > 0);
        // Empty file should be very small
        assert!(mem < 500);
    }
}
