//! Streaming graph builder using LightweightFileInfo
//!
//! This module builds the code graph from LightweightFileInfo structs
//! in a streaming fashion. It can process files one at a time, adding
//! nodes immediately and deferring edge resolution to the end.
//!
//! # Memory Model
//!
//! ```text
//! Phase 1: Build lookup indexes (O(functions) not O(source_code))
//!   - FunctionLookup: name → qualified_name
//!   - ModuleLookup: patterns → file paths
//!
//! Phase 2: Stream files through builder
//!   For each LightweightFileInfo:
//!     - Add file node immediately
//!     - Add function/class nodes immediately  
//!     - Collect edges for batch insertion
//!
//! Phase 3: Resolve and insert edges
//!   - Use lookup indexes for O(1) cross-file resolution
//!   - Batch insert all edges
//! ```

use crate::graph::{CodeEdge, CodeNode, GraphStore, NodeKind};
use crate::parsers::lightweight::{Language, LightweightFileInfo, LightweightParseStats};
use anyhow::Result;
use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

/// Lightweight function lookup for edge resolution
#[derive(Debug, Default)]
pub struct FunctionLookup {
    /// function name → qualified name
    pub name_to_qualified: HashMap<String, String>,
}

impl FunctionLookup {
    pub fn new() -> Self {
        Self::default()
    }

    /// Add functions from a LightweightFileInfo
    pub fn add_from_info(&mut self, info: &LightweightFileInfo) {
        for func in &info.functions {
            self.name_to_qualified
                .insert(func.name.clone(), func.qualified_name.clone());
        }
    }

    /// Resolve a callee name to qualified name
    pub fn resolve(&self, name: &str) -> Option<&String> {
        self.name_to_qualified.get(name)
    }
}

/// Module pattern lookup for import resolution
#[derive(Debug, Default)]
pub struct ModuleLookup {
    /// file stem → relative paths
    pub by_stem: HashMap<String, Vec<String>>,
    /// module patterns → relative paths
    pub by_pattern: HashMap<String, Vec<String>>,
}

impl ModuleLookup {
    pub fn new() -> Self {
        Self::default()
    }

    /// Add a file to the lookup
    pub fn add_file(&mut self, relative_path: &str, language: Language) {
        // Extract file stem
        let path = std::path::Path::new(relative_path);
        if let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
            self.by_stem
                .entry(stem.to_string())
                .or_default()
                .push(relative_path.to_string());
        }

        // Add language-specific patterns
        match language {
            Language::Rust => {
                if relative_path.ends_with(".rs") {
                    let rust_path = relative_path.trim_end_matches(".rs").replace('/', "::");
                    self.by_pattern
                        .entry(rust_path)
                        .or_default()
                        .push(relative_path.to_string());
                }
            }
            Language::TypeScript | Language::JavaScript => {
                for ext in &[".ts", ".tsx", ".js", ".jsx", ".mjs"] {
                    if !relative_path.ends_with(ext) {
                        continue;
                    }
                    let base = relative_path.trim_end_matches(ext);
                    self.by_pattern
                        .entry(base.to_string())
                        .or_default()
                        .push(relative_path.to_string());
                    if base.ends_with("/index") {
                        self.by_pattern
                            .entry(base.trim_end_matches("/index").to_string())
                            .or_default()
                            .push(relative_path.to_string());
                    }
                }
            }
            Language::Python => {
                if relative_path.ends_with(".py") {
                    let py_path = relative_path.trim_end_matches(".py").replace('/', ".");
                    self.by_pattern
                        .entry(py_path)
                        .or_default()
                        .push(relative_path.to_string());
                    if relative_path.ends_with("/__init__.py") {
                        let pkg = relative_path
                            .trim_end_matches("/__init__.py")
                            .replace('/', ".");
                        self.by_pattern
                            .entry(pkg)
                            .or_default()
                            .push(relative_path.to_string());
                    }
                }
            }
            _ => {}
        }
    }

    /// Find matches for an import path
    pub fn find_matches(&self, import_path: &str) -> Vec<&String> {
        let clean = import_path
            .trim_start_matches("./")
            .trim_start_matches("../")
            .trim_start_matches("crate::")
            .trim_start_matches("super::");

        // Try pattern match first
        if let Some(matches) = self.by_pattern.get(clean) {
            return matches.iter().collect();
        }

        // Try stem match
        let parts: Vec<&str> = clean.split("::").collect();
        if let Some(first) = parts.first() {
            if let Some(matches) = self.by_stem.get(*first) {
                return matches.iter().collect();
            }
        }

        // Try as python path
        let py_clean = clean.replace('.', "/");
        if let Some(matches) = self.by_pattern.get(&py_clean) {
            return matches.iter().collect();
        }

        Vec::new()
    }
}

/// Streaming graph builder that processes LightweightFileInfo one at a time
pub struct StreamingGraphBuilder {
    graph: Arc<GraphStore>,
    repo_path: std::path::PathBuf,

    // Lookup indexes (built in first pass or incrementally)
    function_lookup: FunctionLookup,
    module_lookup: ModuleLookup,

    // Deferred edges for batch insertion
    deferred_edges: Vec<(String, String, CodeEdge)>,

    // Stats
    pub stats: StreamingGraphStats,
}

/// Statistics from streaming graph build
#[derive(Debug, Clone, Default)]
pub struct StreamingGraphStats {
    pub files_processed: usize,
    pub nodes_added: usize,
    pub edges_added: usize,
    pub functions_added: usize,
    pub classes_added: usize,
    pub calls_resolved: usize,
    pub calls_unresolved: usize,
    pub imports_resolved: usize,
}

impl StreamingGraphBuilder {
    /// Create a new streaming builder
    pub fn new(graph: Arc<GraphStore>, repo_path: &Path) -> Self {
        Self {
            graph,
            repo_path: repo_path.to_path_buf(),
            function_lookup: FunctionLookup::new(),
            module_lookup: ModuleLookup::new(),
            deferred_edges: Vec::new(),
            stats: StreamingGraphStats::default(),
        }
    }

    /// Pre-build lookup indexes from file list (Phase 1)
    ///
    /// This is optional but recommended for large repos.
    /// It builds the function and module lookups needed for edge resolution.
    pub fn build_indexes(&mut self, files: &[LightweightFileInfo]) {
        for info in files {
            let relative = info.relative_path(&self.repo_path);

            // Add to function lookup
            self.function_lookup.add_from_info(info);

            // Add to module lookup
            self.module_lookup.add_file(&relative, info.language);
        }
    }

    /// Process a single file (Phase 2)
    ///
    /// Adds nodes immediately, collects edges for later.
    pub fn process_file(&mut self, info: &LightweightFileInfo) -> Result<()> {
        let relative = info.relative_path(&self.repo_path);

        // Add to lookups if not already present
        self.function_lookup.add_from_info(info);
        self.module_lookup.add_file(&relative, info.language);

        // Add file node
        let file_node = CodeNode::new(NodeKind::File, &relative, &relative)
            .with_qualified_name(&relative)
            .with_language(info.language.as_str())
            .with_property("loc", info.loc as i64);
        self.graph.add_node(file_node);
        self.stats.nodes_added += 1;
        self.stats.files_processed += 1;

        // Add function nodes
        for func in &info.functions {
            let loc = func.loc();
            let address_taken = info.address_taken.contains(&func.name);

            let func_node = CodeNode::new(NodeKind::Function, &func.name, &relative)
                .with_qualified_name(&func.qualified_name)
                .with_lines(func.line_start, func.line_end)
                .with_property("is_async", func.is_async)
                .with_property("complexity", func.complexity as i64)
                .with_property("loc", loc as i64)
                .with_property("address_taken", address_taken);
            self.graph.add_node(func_node);
            self.stats.nodes_added += 1;
            self.stats.functions_added += 1;

            // Defer contains edge
            self.deferred_edges.push((
                relative.clone(),
                func.qualified_name.clone(),
                CodeEdge::contains(),
            ));
        }

        // Add class nodes
        for class in &info.classes {
            let class_node = CodeNode::new(NodeKind::Class, &class.name, &relative)
                .with_qualified_name(&class.qualified_name)
                .with_lines(class.line_start, class.line_end)
                .with_property("methodCount", class.method_count as i64);
            self.graph.add_node(class_node);
            self.stats.nodes_added += 1;
            self.stats.classes_added += 1;

            // Defer contains edge
            self.deferred_edges.push((
                relative.clone(),
                class.qualified_name.clone(),
                CodeEdge::contains(),
            ));
        }

        // Collect call edges (resolve during finalization)
        for call in &info.calls {
            // Try to resolve callee
            let callee_name = call.callee.rsplit("::").next().unwrap_or(&call.callee);
            let callee_name = callee_name.rsplit('.').next().unwrap_or(callee_name);

            // Check if callee is in same file
            let callee_qn = info
                .functions
                .iter()
                .find(|f| f.name == callee_name)
                .map(|f| f.qualified_name.clone())
                .or_else(|| self.function_lookup.resolve(callee_name).cloned());

            if let Some(qn) = callee_qn {
                self.deferred_edges
                    .push((call.caller.clone(), qn, CodeEdge::calls()));
                self.stats.calls_resolved += 1;
            } else {
                self.stats.calls_unresolved += 1;
            }
        }

        // Collect import edges
        for import in &info.imports {
            let matches = self.module_lookup.find_matches(&import.path);
            if let Some(target) = matches.first() {
                if *target != &relative {
                    let import_edge =
                        CodeEdge::imports().with_property("is_type_only", import.is_type_only);
                    self.deferred_edges
                        .push((relative.clone(), (*target).clone(), import_edge));
                    self.stats.imports_resolved += 1;
                }
            }
        }

        Ok(())
    }

    /// Finalize the graph (Phase 3)
    ///
    /// Inserts all deferred edges in batch and saves the graph.
    pub fn finalize(&mut self) -> Result<()> {
        // Batch insert all edges
        self.stats.edges_added = self.deferred_edges.len();
        self.graph
            .add_edges_batch(std::mem::take(&mut self.deferred_edges));

        // Save graph
        self.graph.save()?;

        Ok(())
    }
}

/// Build graph from LightweightFileInfo in streaming fashion
///
/// This is the main entry point for streaming graph building.
/// It processes files in batches for parallelism while maintaining
/// streaming memory properties.
pub fn build_graph_streaming(
    graph: Arc<GraphStore>,
    repo_path: &Path,
    files: Vec<LightweightFileInfo>,
    progress: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> Result<StreamingGraphStats> {
    let total = files.len();
    let mut builder = StreamingGraphBuilder::new(graph, repo_path);

    // Phase 1: Build indexes (fast pass)
    if let Some(cb) = progress {
        cb(0, total);
    }
    builder.build_indexes(&files);

    // Phase 2: Process files
    for (idx, info) in files.iter().enumerate() {
        if let Some(cb) = progress {
            if idx % 100 == 0 || idx == total - 1 {
                cb(idx, total);
            }
        }
        builder.process_file(info)?;
    }

    // Phase 3: Finalize
    builder.finalize()?;

    Ok(builder.stats)
}

/// TRUE streaming: Parse and build graph without collecting all files
///
/// This function processes files one at a time:
/// 1. Parse file → LightweightFileInfo
/// 2. Add to graph
/// 3. Drop info, move to next file
///
/// Only the lookup indexes grow with file count, not the full file info.
pub fn parse_and_build_streaming_true(
    files: &[std::path::PathBuf],
    repo_path: &Path,
    graph: Arc<GraphStore>,
    progress: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> Result<(StreamingGraphStats, LightweightParseStats)> {
    use crate::parsers::parse_file_lightweight;

    let total = files.len();
    let mut builder = StreamingGraphBuilder::new(Arc::clone(&graph), repo_path);
    let mut parse_stats = LightweightParseStats {
        total_files: total,
        ..Default::default()
    };

    // Two-pass approach for best memory efficiency:
    //
    // Pass 1: Quick scan for function names (builds lookup)
    // Pass 2: Full parse and graph build
    //
    // This ensures cross-file resolution works correctly.

    // Pass 1: Build module lookup (just file paths, no parsing)
    for path in files {
        let relative = path.strip_prefix(repo_path).unwrap_or(path);
        let relative_str = relative.display().to_string();
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
        let lang = crate::parsers::Language::from_extension(ext);
        builder.module_lookup.add_file(&relative_str, lang);
    }

    // Pass 2: Parse each file, add to graph, collect function names
    for (idx, path) in files.iter().enumerate() {
        if let Some(cb) = progress {
            if idx % 100 == 0 || idx == total - 1 {
                cb(idx, total);
            }
        }

        match parse_file_lightweight(path) {
            Ok(info) => {
                parse_stats.add_file(&info);

                // Add function names to lookup for later files
                builder.function_lookup.add_from_info(&info);

                // Process file (adds nodes, collects edges)
                if let Err(e) = builder.process_file(&info) {
                    tracing::warn!("Failed to process {}: {}", path.display(), e);
                }

                // info is dropped here - only lookup keys remain
            }
            Err(e) => {
                parse_stats.parse_errors += 1;
                tracing::warn!("Failed to parse {}: {}", path.display(), e);
            }
        }
        // AST is dropped here
    }

    // Finalize graph
    builder.finalize()?;

    Ok((builder.stats, parse_stats))
}

/// Parse and build graph using PARALLEL PIPELINE
///
/// This is the highest-performance approach for large repos:
/// - Producer thread feeds file paths
/// - N worker threads parse in parallel (CPU-bound)
/// - Consumer receives results and builds graph sequentially (stateful)
///
/// Benefits:
/// - Uses all CPU cores for parsing
/// - Bounded memory via channel capacities
/// - No lock contention in graph building
/// - Automatic backpressure
///
/// Memory target: <1.5GB for 20k files (vs 3GB+ with traditional approach)
pub fn parse_and_build_pipeline(
    files: Vec<std::path::PathBuf>,
    repo_path: &Path,
    graph: Arc<GraphStore>,
    num_workers: usize,
    buffer_size: usize,
    progress: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> Result<(StreamingGraphStats, LightweightParseStats)> {
    use crate::parsers::parallel_pipeline::parse_files_pipeline;

    let total = files.len();
    let mut builder = StreamingGraphBuilder::new(Arc::clone(&graph), repo_path);
    let mut parse_stats = LightweightParseStats {
        total_files: total,
        ..Default::default()
    };

    // Pass 1: Build module lookup from file paths (no parsing needed)
    for path in &files {
        let relative = path.strip_prefix(repo_path).unwrap_or(path);
        let relative_str = relative.display().to_string();
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
        let lang = crate::parsers::Language::from_extension(ext);
        builder.module_lookup.add_file(&relative_str, lang);
    }

    // Pass 2: Start parallel pipeline
    let mut pipeline = parse_files_pipeline(files, num_workers, buffer_size);

    // Take the receiver from the pipeline
    let receiver = pipeline.take_receiver().expect("receiver already taken");

    // Process results as they come in (sequential graph building)
    let mut count = 0;
    for info in receiver {
        count += 1;

        if let Some(cb) = progress {
            if count % 100 == 0 || count == total {
                cb(count, total);
            }
        }

        parse_stats.add_file(&info);

        // Add to function lookup for later files' edge resolution
        builder.function_lookup.add_from_info(&info);

        // Process file (adds nodes, collects edges)
        if let Err(e) = builder.process_file(&info) {
            tracing::warn!("Failed to process file: {}", e);
        }

        // info is dropped here - only lookup keys remain
    }

    // Wait for workers and collect final stats
    let pipeline_stats = pipeline.join();
    parse_stats.parse_errors = pipeline_stats.parse_errors;
    parse_stats.parsed_files = pipeline_stats.parsed_files;

    // Finalize graph (batch insert edges)
    builder.finalize()?;

    Ok((builder.stats, parse_stats))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parsers::lightweight::*;

    #[test]
    fn test_function_lookup() {
        let mut lookup = FunctionLookup::new();

        let info = LightweightFileInfo {
            path: std::path::PathBuf::from("test.py"),
            language: Language::Python,
            loc: 10,
            functions: vec![LightweightFunctionInfo {
                name: "helper".to_string(),
                qualified_name: "test.py::helper:1".to_string(),
                line_start: 1,
                line_end: 5,
                param_count: 2,
                is_async: false,
                complexity: 3,
            }],
            classes: vec![],
            imports: vec![],
            calls: vec![],
            address_taken: std::collections::HashSet::new(),
        };

        lookup.add_from_info(&info);

        assert_eq!(
            lookup.resolve("helper"),
            Some(&"test.py::helper:1".to_string())
        );
    }

    #[test]
    fn test_module_lookup() {
        let mut lookup = ModuleLookup::new();

        lookup.add_file("src/utils/helpers.py", Language::Python);

        let matches = lookup.find_matches("helpers");
        assert!(!matches.is_empty());
        assert!(matches[0].contains("helpers"));
    }
}
