#![allow(dead_code)] // Infrastructure module for future large-repo support
//! Streaming AST processing for memory-efficient large repo analysis
//!
//! This module provides a streaming architecture that parses files one at a time,
//! extracts needed information immediately, and drops the AST before parsing the next file.
//! This prevents OOM on large repositories (75k+ files).
//!
//! # Architecture
//!
//! Instead of collecting all `ParseResult` objects into memory:
//!
//! ```text
//! [Traditional - OOM prone]
//! parse_files() -> Vec<(PathBuf, ParseResult)> -> build_graph()
//!                  ^^^^ 75k ParseResults in memory!
//!
//! [Streaming - Memory efficient]
//! Phase 1: Build lightweight lookup index (function names → qualified names)
//! Phase 2: Stream parse → extract nodes/edges → drop AST → next file
//! ```
//!
//! # Key insight
//!
//! Detectors don't need the full AST - they need:
//! - Function/class names and locations
//! - Call relationships
//! - Import relationships
//! - Code snippets for findings
//!
//! This module provides `ParsedFileInfo` which is much smaller than `ParseResult`.

use crate::parsers::{parse_file, ParseResult};
use anyhow::Result;
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

/// Lightweight extracted info from a parsed file
///
/// This struct contains only what's needed for graph building and detection,
/// without holding the full tree-sitter AST or duplicated PathBuf per entity.
///
/// Memory comparison (estimated per file with 10 functions):
/// - ParseResult with 10 Functions: ~2KB (10 × 200 bytes with PathBuf each)
/// - ParsedFileInfo: ~500 bytes (shared path reference, compact types)
#[derive(Debug, Clone)]
pub struct ParsedFileInfo {
    /// File path
    pub path: PathBuf,

    /// Relative path string for graph building
    pub relative_path: String,

    /// Detected language
    pub language: String,

    /// Lines of code
    pub loc: usize,

    /// Extracted function info (lightweight)
    pub functions: Vec<FunctionInfo>,

    /// Extracted class info (lightweight)
    pub classes: Vec<ClassInfo>,

    /// Import relationships
    pub imports: Vec<ImportEdge>,

    /// Call relationships (caller_qn, callee_name)
    pub calls: Vec<(String, String)>,

    /// Functions whose addresses are taken (for dead code detection)
    pub address_taken: HashSet<String>,
}

/// Lightweight function info - no PathBuf duplication per function
#[derive(Debug, Clone)]
pub struct FunctionInfo {
    pub name: String,
    pub qualified_name: String,
    pub line_start: u32,
    pub line_end: u32,
    pub is_async: bool,
    pub complexity: u32,
    pub parameters: Vec<String>,
    pub return_type: Option<String>,
    /// Maximum nesting depth within this function
    pub max_nesting: Option<u32>,
    /// Whether this function has decorators/annotations
    pub has_annotations: bool,
}

/// Lightweight class info
#[derive(Debug, Clone)]
pub struct ClassInfo {
    pub name: String,
    pub qualified_name: String,
    pub line_start: u32,
    pub line_end: u32,
    pub method_count: usize,
    pub methods: Vec<String>,
    pub bases: Vec<String>,
}

/// Import edge info
#[derive(Debug, Clone)]
pub struct ImportEdge {
    pub path: String,
    pub is_type_only: bool,
}

impl ParsedFileInfo {
    /// Convert from full ParseResult, extracting only needed data
    pub fn from_parse_result(
        result: ParseResult,
        file_path: &Path,
        relative_path: &str,
        language: &str,
        loc: usize,
    ) -> Self {
        // Extract function info without duplicating file_path
        let functions: Vec<FunctionInfo> = result
            .functions
            .into_iter()
            .map(|f| {
                let has_annotations = !f.annotations.is_empty();
                FunctionInfo {
                    name: f.name,
                    qualified_name: f.qualified_name,
                    line_start: f.line_start,
                    line_end: f.line_end,
                    is_async: f.is_async,
                    complexity: f.complexity.unwrap_or(1),
                    parameters: f.parameters,
                    return_type: f.return_type,
                    max_nesting: f.max_nesting,
                    has_annotations,
                }
            })
            .collect();

        // Extract class info
        let classes: Vec<ClassInfo> = result
            .classes
            .into_iter()
            .map(|c| ClassInfo {
                name: c.name,
                qualified_name: c.qualified_name,
                line_start: c.line_start,
                line_end: c.line_end,
                method_count: c.methods.len(),
                methods: c.methods,
                bases: c.bases,
            })
            .collect();

        // Extract imports
        let imports: Vec<ImportEdge> = result
            .imports
            .into_iter()
            .map(|i| ImportEdge {
                path: i.path,
                is_type_only: i.is_type_only,
            })
            .collect();

        Self {
            path: file_path.to_path_buf(),
            relative_path: relative_path.to_string(),
            language: language.to_string(),
            loc,
            functions,
            classes,
            imports,
            calls: result.calls,
            address_taken: result.address_taken,
        }
    }

    /// Create a ParseResult from this info (for compatibility with existing code)
    pub fn to_parse_result(&self) -> ParseResult {
        use crate::models::{Class, Function};

        ParseResult {
            functions: self
                .functions
                .iter()
                .map(|f| Function {
                    name: f.name.clone(),
                    qualified_name: f.qualified_name.clone(),
                    file_path: self.path.clone(),
                    line_start: f.line_start,
                    line_end: f.line_end,
                    parameters: f.parameters.clone(),
                    return_type: f.return_type.clone(),
                    is_async: f.is_async,
                    complexity: Some(f.complexity),
                    max_nesting: f.max_nesting,
                    doc_comment: None,
                    annotations: vec![],
                })
                .collect(),
            classes: self
                .classes
                .iter()
                .map(|c| Class {
                    name: c.name.clone(),
                    qualified_name: c.qualified_name.clone(),
                    file_path: self.path.clone(),
                    line_start: c.line_start,
                    line_end: c.line_end,
                    methods: c.methods.clone(),
                    bases: c.bases.clone(),
                    doc_comment: None,
                    annotations: vec![],
                })
                .collect(),
            imports: self
                .imports
                .iter()
                .map(|i| crate::parsers::ImportInfo {
                    path: i.path.clone(),
                    is_type_only: i.is_type_only,
                })
                .collect(),
            calls: self.calls.clone(),
            address_taken: self.address_taken.clone(),
        }
    }
}

/// Lightweight index for cross-file reference resolution
///
/// Built in Phase 1, used in Phase 2 for O(1) lookups
#[derive(Debug, Default, Clone)]
pub struct FunctionIndex {
    /// function name → qualified name
    pub name_to_qualified: HashMap<String, String>,

    /// qualified name → file path (as string)
    pub qualified_to_file: HashMap<String, String>,
}

impl FunctionIndex {
    /// Build function index from ParseResult
    pub fn add_from_result(&mut self, result: &ParseResult, file_path: &Path) {
        let file_str = file_path.display().to_string();

        for func in &result.functions {
            self.name_to_qualified
                .insert(func.name.clone(), func.qualified_name.clone());
            self.qualified_to_file
                .insert(func.qualified_name.clone(), file_str.clone());
        }
    }

    /// Add from ParsedFileInfo
    pub fn add_from_info(&mut self, info: &ParsedFileInfo) {
        let file_str = info.path.display().to_string();

        for func in &info.functions {
            self.name_to_qualified
                .insert(func.name.clone(), func.qualified_name.clone());
            self.qualified_to_file
                .insert(func.qualified_name.clone(), file_str.clone());
        }
    }

    /// Merge another index into this one
    pub fn merge(&mut self, other: FunctionIndex) {
        self.name_to_qualified.extend(other.name_to_qualified);
        self.qualified_to_file.extend(other.qualified_to_file);
    }
}

/// Module lookup index for import resolution
#[derive(Debug, Default, Clone)]
pub struct ModuleIndex {
    /// file stem (e.g., "utils") → file paths with that stem
    pub by_stem: HashMap<String, Vec<String>>,

    /// module patterns (e.g., "src/utils") → file paths
    pub by_pattern: HashMap<String, Vec<String>>,
}

impl ModuleIndex {
    /// Add file to index
    pub fn add_file(&mut self, file_path: &Path, relative_path: &str) {
        // Extract file stem
        if let Some(stem) = file_path.file_stem().and_then(|s| s.to_str()) {
            self.by_stem
                .entry(stem.to_string())
                .or_default()
                .push(relative_path.to_string());
        }

        // Add various patterns
        let patterns = generate_module_patterns(relative_path);
        for pattern in patterns {
            self.by_pattern
                .entry(pattern)
                .or_default()
                .push(relative_path.to_string());
        }
    }

    /// Find matching files for an import path
    ///
    /// Returns a slice into the internal storage, avoiding allocation.
    pub fn find_matches(&self, import_path: &str) -> &[String] {
        let clean = clean_import_path(import_path);

        // Try pattern match first
        if let Some(matches) = self.by_pattern.get(&clean) {
            return matches;
        }

        // Try stem match
        let parts: Vec<&str> = clean.split("::").collect();
        if let Some(first) = parts.first() {
            if let Some(matches) = self.by_stem.get(*first) {
                return matches;
            }
        }

        &[]
    }

    /// Merge another index
    pub fn merge(&mut self, other: ModuleIndex) {
        for (k, v) in other.by_stem {
            self.by_stem.entry(k).or_default().extend(v);
        }
        for (k, v) in other.by_pattern {
            self.by_pattern.entry(k).or_default().extend(v);
        }
    }
}

/// Generate module patterns for a file path
fn generate_module_patterns(relative_path: &str) -> Vec<String> {
    let mut patterns = Vec::new();

    // Rust module patterns
    if relative_path.ends_with(".rs") {
        let rust_path = relative_path.trim_end_matches(".rs").replace('/', "::");
        patterns.push(rust_path);
    }

    // TypeScript/JavaScript patterns
    for ext in &[".ts", ".tsx", ".js", ".jsx", ".mjs"] {
        if relative_path.ends_with(ext) {
            let base = relative_path.trim_end_matches(ext);
            patterns.push(base.to_string());
            if base.ends_with("/index") {
                patterns.push(base.trim_end_matches("/index").to_string());
            }
        }
    }

    // Python patterns
    if relative_path.ends_with(".py") {
        let py_path = relative_path.trim_end_matches(".py").replace('/', ".");
        patterns.push(py_path);
        if relative_path.ends_with("/__init__.py") {
            let pkg = relative_path
                .trim_end_matches("/__init__.py")
                .replace('/', ".");
            patterns.push(pkg);
        }
    }

    patterns
}

/// Clean import path for matching
fn clean_import_path(import_path: &str) -> String {
    import_path
        .trim_start_matches("./")
        .trim_start_matches("../")
        .trim_start_matches("crate::")
        .trim_start_matches("super::")
        .to_string()
}

/// Build function and module indexes in parallel (Phase 1)
///
/// This is a lightweight first pass that extracts only:
/// - Function name → qualified name mappings
/// - File patterns for module resolution
///
/// Memory: O(number of functions) not O(source code size)
pub fn build_indexes_parallel(
    files: &[PathBuf],
    repo_path: &Path,
    progress_callback: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> Result<(FunctionIndex, ModuleIndex)> {
    let counter = AtomicUsize::new(0);
    let total = files.len();

    // Parallel index building
    let indexes: Vec<(FunctionIndex, ModuleIndex)> = files
        .par_iter()
        .filter_map(|file_path| {
            let count = counter.fetch_add(1, Ordering::Relaxed);
            if let Some(cb) = progress_callback {
                if count.is_multiple_of(500) {
                    cb(count, total);
                }
            }

            // Parse file
            let result = parse_file(file_path).ok()?;

            // Get relative path
            let relative = file_path.strip_prefix(repo_path).unwrap_or(file_path);
            let relative_str = relative.display().to_string();

            // Build local indexes
            let mut func_idx = FunctionIndex::default();
            let mut mod_idx = ModuleIndex::default();

            func_idx.add_from_result(&result, file_path);
            mod_idx.add_file(file_path, &relative_str);

            Some((func_idx, mod_idx))
        })
        .collect();

    // Merge all indexes
    let mut function_index = FunctionIndex::default();
    let mut module_index = ModuleIndex::default();

    for (func_idx, mod_idx) in indexes {
        function_index.merge(func_idx);
        module_index.merge(mod_idx);
    }

    Ok((function_index, module_index))
}

/// Streaming graph builder callback trait
///
/// Implement this trait to receive parsed file info and build the graph
/// incrementally without holding all ParseResults in memory.
pub trait StreamingGraphBuilder: Send {
    /// Called for each parsed file
    fn on_file(&mut self, info: ParsedFileInfo) -> Result<()>;

    /// Called after all files are processed
    fn finalize(&mut self) -> Result<()>;
}

/// Statistics from streaming parse
#[derive(Debug, Clone, Default)]
pub struct StreamingStats {
    pub total_files: usize,
    pub parsed_files: usize,
    pub total_functions: usize,
    pub total_classes: usize,
    pub parse_errors: usize,
}

/// Process files in streaming fashion with a builder callback
///
/// This is the core streaming function. For each file:
/// 1. Parse the file
/// 2. Extract lightweight info
/// 3. Call builder.on_file()
/// 4. Drop ParseResult (memory freed)
/// 5. Move to next file
///
/// At no point are all ParseResults held simultaneously.
pub fn stream_parse_files<B: StreamingGraphBuilder>(
    files: &[PathBuf],
    repo_path: &Path,
    builder: &mut B,
    progress_callback: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> Result<StreamingStats> {
    let mut stats = StreamingStats {
        total_files: files.len(),
        ..Default::default()
    };

    for (idx, file_path) in files.iter().enumerate() {
        if let Some(cb) = progress_callback {
            if idx % 100 == 0 {
                cb(idx, files.len());
            }
        }

        // Parse file
        let result = match parse_file(file_path) {
            Ok(r) => r,
            Err(e) => {
                stats.parse_errors += 1;
                tracing::warn!("Failed to parse {}: {}", file_path.display(), e);
                continue;
            }
        };

        // Track counts
        stats.total_functions += result.functions.len();
        stats.total_classes += result.classes.len();
        stats.parsed_files += 1;

        // Get relative path
        let relative = file_path.strip_prefix(repo_path).unwrap_or(file_path);
        let relative_str = relative.display().to_string();

        // Detect language and count lines
        let language = detect_language(file_path);
        let loc = count_lines(file_path).unwrap_or(0);

        // Convert to lightweight info
        let info =
            ParsedFileInfo::from_parse_result(result, file_path, &relative_str, &language, loc);

        // Callback to builder - ParseResult is dropped after this
        builder.on_file(info)?;
    }

    builder.finalize()?;

    Ok(stats)
}

/// Stream parse files in parallel batches
///
/// Processes files in parallel batches of `batch_size`, calling the builder
/// for each file. This balances memory efficiency with parallelism.
pub fn stream_parse_files_parallel<B: StreamingGraphBuilder>(
    files: &[PathBuf],
    repo_path: &Path,
    builder: &mut B,
    batch_size: usize,
    progress_callback: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> Result<StreamingStats> {
    let mut stats = StreamingStats {
        total_files: files.len(),
        ..Default::default()
    };

    let counter = AtomicUsize::new(0);
    let total = files.len();

    // Process in batches to limit peak memory
    for chunk in files.chunks(batch_size) {
        // Parse batch in parallel
        let batch_results: Vec<Option<ParsedFileInfo>> = chunk
            .par_iter()
            .map(|file_path| {
                let count = counter.fetch_add(1, Ordering::Relaxed);
                if let Some(cb) = progress_callback {
                    if count.is_multiple_of(200) {
                        cb(count, total);
                    }
                }

                // Parse file
                let result = match parse_file(file_path) {
                    Ok(r) => r,
                    Err(e) => {
                        tracing::warn!("Failed to parse {}: {}", file_path.display(), e);
                        return None;
                    }
                };

                // Get relative path
                let relative = file_path.strip_prefix(repo_path).unwrap_or(file_path);
                let relative_str = relative.display().to_string();

                // Detect language and count lines
                let language = detect_language(file_path);
                let loc = count_lines(file_path).unwrap_or(0);

                // Convert to lightweight info
                let info = ParsedFileInfo::from_parse_result(
                    result,
                    file_path,
                    &relative_str,
                    &language,
                    loc,
                );

                Some(info)
            })
            .collect();

        // Process results sequentially through builder
        for info_opt in batch_results {
            if let Some(info) = info_opt {
                stats.total_functions += info.functions.len();
                stats.total_classes += info.classes.len();
                stats.parsed_files += 1;
                builder.on_file(info)?;
            } else {
                stats.parse_errors += 1;
            }
        }

        // Memory for this batch's ParseResults is freed here
    }

    builder.finalize()?;

    Ok(stats)
}

// Helper functions

fn detect_language(path: &Path) -> String {
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
    match ext {
        "py" | "pyi" => "Python",
        "ts" | "tsx" => "TypeScript",
        "js" | "jsx" | "mjs" => "JavaScript",
        "rs" => "Rust",
        "go" => "Go",
        "java" => "Java",
        "c" | "h" => "C",
        "cpp" | "hpp" | "cc" => "C++",
        "cs" => "C#",
        "kt" | "kts" => "Kotlin",
        "rb" => "Ruby",
        "php" => "PHP",
        "swift" => "Swift",
        _ => "Unknown",
    }
    .to_string()
}

fn count_lines(path: &Path) -> Result<usize> {
    let content = std::fs::read_to_string(path)?;
    Ok(content.lines().count())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_module_patterns() {
        let patterns = generate_module_patterns("src/utils/helpers.py");
        assert!(patterns.contains(&"src.utils.helpers".to_string()));

        let patterns = generate_module_patterns("src/utils/mod.rs");
        assert!(patterns.contains(&"src::utils::mod".to_string()));

        let patterns = generate_module_patterns("src/components/index.ts");
        assert!(patterns.contains(&"src/components/index".to_string()));
        assert!(patterns.contains(&"src/components".to_string()));
    }

    #[test]
    fn test_clean_import_path() {
        assert_eq!(clean_import_path("./utils"), "utils");
        assert_eq!(clean_import_path("../helpers"), "helpers");
        assert_eq!(clean_import_path("crate::utils"), "utils");
        assert_eq!(clean_import_path("super::parent"), "parent");
    }

    #[test]
    fn test_function_index() {
        let mut index = FunctionIndex::default();

        index
            .name_to_qualified
            .insert("helper".to_string(), "src/utils.py::helper:10".to_string());

        assert!(index.name_to_qualified.contains_key("helper"));
        assert_eq!(
            index.name_to_qualified.get("helper").expect("should find helper in index"),
            "src/utils.py::helper:10"
        );
    }

    #[test]
    fn test_module_index() {
        let mut index = ModuleIndex::default();

        let path = Path::new("src/utils/helpers.py");
        index.add_file(path, "src/utils/helpers.py");

        assert!(index.by_stem.contains_key("helpers"));
        assert!(!index.find_matches("helpers").is_empty());
    }
}
