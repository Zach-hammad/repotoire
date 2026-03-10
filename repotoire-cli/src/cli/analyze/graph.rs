//! Graph building functions for the analyze command
//!
//! This module contains all code graph construction logic:
//! - Building the code graph from parse results
//! - Call edge resolution
//! - Import edge resolution
//! - Streaming graph building for huge repos

use crate::graph::store_models::{
    ExtraProps, FLAG_ADDRESS_TAKEN, FLAG_HAS_DECORATORS, FLAG_IS_ASYNC,
};
use crate::graph::{CodeEdge, CodeNode, GraphStore, NodeKind};
use crate::models::{Class, Function};
use crate::parsers::bounded_pipeline::{run_bounded_pipeline, run_bounded_pipeline_from_channel, PipelineConfig};
use crate::parsers::streaming::{
    FunctionIndex, ModuleIndex, ParsedFileInfo, StreamingGraphBuilder,
};
use crate::parsers::ParseResult;
use anyhow::{Context, Result};
use console::style;
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use rayon::prelude::*;
use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

/// Count lines in a file
#[allow(dead_code)] // Used by streaming parser variants
fn count_lines(path: &Path) -> Result<usize> {
    let content = std::fs::read_to_string(path)?;
    Ok(content.lines().count())
}

/// Detect the language from file extension
#[allow(dead_code)] // Used by streaming parser variants
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

/// Detect language from a relative path string (avoids needing a &Path)
fn detect_language_from_path_str(relative_str: &str) -> &'static str {
    let ext = relative_str.rsplit('.').next().unwrap_or("");
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
}

/// Generate module import pattern keys for a given relative file path.
fn generate_module_patterns(relative_str: &str) -> Vec<String> {
    let mut patterns = Vec::new();

    // Rust module patterns
    if relative_str.ends_with(".rs") {
        let rust_path = relative_str.trim_end_matches(".rs").replace('/', "::");
        patterns.push(rust_path);
    }

    // TypeScript/JavaScript patterns
    for ext in &[".ts", ".tsx", ".js", ".jsx", ".mjs"] {
        if !relative_str.ends_with(ext) {
            continue;
        }
        let base = relative_str.trim_end_matches(ext);
        patterns.push(base.to_string());
        // index.ts -> parent dir name
        if base.ends_with("/index") {
            patterns.push(base.trim_end_matches("/index").to_string());
        }
    }

    // Python patterns
    if relative_str.ends_with(".py") {
        let py_path = relative_str.trim_end_matches(".py").replace('/', ".");
        patterns.push(py_path);
        if relative_str.ends_with("/__init__.py") {
            let pkg = relative_str
                .trim_end_matches("/__init__.py")
                .replace('/', ".");
            patterns.push(pkg);
        }
    }

    patterns
}

/// Build a CodeNode for a single function, attaching optional doc_comment and annotations.
///
/// String properties (params, doc_comment) are written to the ExtraProps side table
/// via `graph.update_node_properties()` after the node is inserted into the graph.
fn build_func_node(
    graph: &GraphStore,
    func: &Function,
    relative_str: &str,
    complexity: u32,
    address_taken: bool,
) -> CodeNode {
    let i = graph.interner();
    let file_key = i.intern(relative_str);
    let lang_key = i.intern(detect_language_from_path_str(relative_str));

    let mut flags: u8 = 0;
    if func.is_async {
        flags |= FLAG_IS_ASYNC;
    }
    if address_taken {
        flags |= FLAG_ADDRESS_TAKEN;
    }
    if !func.annotations.is_empty() {
        flags |= FLAG_HAS_DECORATORS;
    }

    CodeNode {
        kind: NodeKind::Function,
        name: i.intern(&func.name),
        qualified_name: i.intern(&func.qualified_name),
        file_path: file_key,
        language: lang_key,
        line_start: func.line_start,
        line_end: func.line_end,
        complexity: complexity as u16,
        param_count: func.parameters.len().min(255) as u8,
        method_count: 0,
        max_nesting: func.max_nesting.unwrap_or(0).min(255) as u8,
        return_count: 0,
        commit_count: 0,
        flags,
    }
}

/// Build a CodeNode for a single class, attaching optional doc_comment and annotations.
///
/// String properties (doc_comment) are written to the ExtraProps side table
/// via `graph.update_node_properties()` after the node is inserted into the graph.
fn build_class_node(graph: &GraphStore, class: &Class, relative_str: &str) -> CodeNode {
    let i = graph.interner();
    let file_key = i.intern(relative_str);
    let lang_key = i.intern(detect_language_from_path_str(relative_str));

    let mut flags: u8 = 0;
    if !class.annotations.is_empty() {
        flags |= FLAG_HAS_DECORATORS;
    }

    CodeNode {
        kind: NodeKind::Class,
        name: i.intern(&class.name),
        qualified_name: i.intern(&class.qualified_name),
        file_path: file_key,
        language: lang_key,
        line_start: class.line_start,
        line_end: class.line_end,
        complexity: 0,
        param_count: 0,
        method_count: class.methods.len().min(65535) as u16,
        max_nesting: 0,
        return_count: 0,
        commit_count: 0,
        flags,
    }
}

/// Derive a module qualified name from a relative file path.
///
/// e.g. "src/app/routes.py" -> "src.app.routes"
///      "src/lib.rs" -> "src::lib"
#[cfg(test)]
fn module_qn_from_path(relative_str: &str) -> String {
    // Strip common extensions and convert path separators to language-appropriate delimiters
    let base = relative_str
        .trim_end_matches(".py")
        .trim_end_matches(".pyi")
        .trim_end_matches(".ts")
        .trim_end_matches(".tsx")
        .trim_end_matches(".js")
        .trim_end_matches(".jsx")
        .trim_end_matches(".mjs")
        .trim_end_matches(".rs")
        .trim_end_matches(".go")
        .trim_end_matches(".java")
        .trim_end_matches(".cs")
        .trim_end_matches(".c")
        .trim_end_matches(".cpp")
        .trim_end_matches(".cc")
        .trim_end_matches(".hpp");

    // Use dots as delimiter (works for Python-style qualified names)
    base.replace('/', ".")
}

/// Emit a Calls edge from the module (file node) to a decorated function.
///
/// A decorator invocation (`@app.route(...)`) executes at module load time,
/// meaning the module itself is the caller. Using the existing file node as
/// the caller avoids creating synthetic function nodes that would pollute
/// `get_functions()` results and need filtering in every detector.
fn emit_decorator_call_edge(
    func_qn: &str,
    file_qn: &str,
    has_annotations: bool,
    edges: &mut Vec<(String, String, CodeEdge)>,
) {
    if !has_annotations {
        return;
    }
    // Module (file node) calls the decorated function at load time
    edges.push((file_qn.to_string(), func_qn.to_string(), CodeEdge::calls()));
}

/// Estimate node and edge counts from parse results for pre-allocation.
///
/// Nodes: 1 file node + N function nodes + M class nodes per file.
/// Edges: at least 1 Contains edge per function/class, plus call and import edges.
/// The edge multiplier (3x nodes) is a heuristic that covers Contains, Calls,
/// and Imports edges in typical codebases.
fn estimate_graph_capacity(parse_results: &[(PathBuf, ParseResult)]) -> (usize, usize) {
    let mut estimated_nodes: usize = 0;
    let mut estimated_edges: usize = 0;
    for (_, pr) in parse_results {
        // 1 file node + functions + classes
        let file_nodes = 1 + pr.functions.len() + pr.classes.len();
        estimated_nodes += file_nodes;
        // Contains edges (1 per function/class) + call edges + import edges
        estimated_edges += pr.functions.len() + pr.classes.len() + pr.calls.len() + pr.imports.len();
    }
    // Add a safety margin for cross-file call resolution edges
    estimated_edges = estimated_edges.saturating_add(estimated_nodes);
    (estimated_nodes, estimated_edges)
}

/// Build the code graph from parse results.
///
/// Returns a [`ValueStore`] containing all resolved symbolic values extracted
/// during parsing, with cross-function propagation already applied.
pub(super) fn build_graph(
    graph: &Arc<GraphStore>,
    repo_path: &Path,
    parse_results: &[(PathBuf, ParseResult)],
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
) -> Result<crate::values::store::ValueStore> {
    let total_functions: usize = parse_results.iter().map(|(_, r)| r.functions.len()).sum();
    let total_classes: usize = parse_results.iter().map(|(_, r)| r.classes.len()).sum();

    // Pre-allocate graph capacity to eliminate reallocations during bulk insert
    let (estimated_nodes, estimated_edges) = estimate_graph_capacity(parse_results);
    graph.reserve_capacity(estimated_nodes, estimated_edges);

    let graph_bar = multi.add(ProgressBar::new(parse_results.len() as u64));
    graph_bar.set_style(bar_style.clone());
    graph_bar.set_message("Building code graph (parallel)...");

    // Build lookup structures in parallel (needed for O(1) edge resolution)
    let global_func_map = build_global_function_map(parse_results);
    let module_lookup = ModuleLookup::build(parse_results, repo_path);
    let counter = AtomicUsize::new(0);

    // Parallel collection of nodes and edges per file
    let file_results: Vec<_> = parse_results
        .par_iter()
        .map(|(file_path, result)| {
            let relative_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);
            let relative_str = relative_path.display().to_string();

            let i = graph.interner();
            let rel_key = i.intern(&relative_str);
            let lang_key = i.intern(detect_language_from_path_str(&relative_str));

            let mut file_nodes = Vec::with_capacity(1);
            let mut func_nodes = Vec::with_capacity(result.functions.len());
            let mut class_nodes = Vec::with_capacity(result.classes.len());
            let mut edges: Vec<(String, String, CodeEdge)> = Vec::new();
            // File node — compute LOC from max line_end of functions/classes
            let file_loc = result.functions.iter().map(|f| f.line_end)
                .chain(result.classes.iter().map(|c| c.line_end))
                .max()
                .unwrap_or(0);
            file_nodes.push(CodeNode {
                kind: NodeKind::File,
                name: rel_key,
                qualified_name: rel_key,
                file_path: rel_key,
                language: lang_key,
                line_start: 1,
                line_end: file_loc,
                complexity: 0,
                param_count: 0,
                method_count: 0,
                max_nesting: 0,
                return_count: 0,
                commit_count: 0,
                flags: 0,
            });

            // Function nodes
            for func in &result.functions {
                let complexity = func.complexity.unwrap_or(1);
                let address_taken = result.address_taken.contains(&func.name);

                let mut flags: u8 = 0;
                if func.is_async {
                    flags |= FLAG_IS_ASYNC;
                }
                if address_taken {
                    flags |= FLAG_ADDRESS_TAKEN;
                }
                if !func.annotations.is_empty() {
                    flags |= FLAG_HAS_DECORATORS;
                }

                let func_node = CodeNode {
                    kind: NodeKind::Function,
                    name: i.intern(&func.name),
                    qualified_name: i.intern(&func.qualified_name),
                    file_path: rel_key,
                    language: lang_key,
                    line_start: func.line_start,
                    line_end: func.line_end,
                    complexity: complexity as u16,
                    param_count: func.parameters.len().min(255) as u8,
                    method_count: 0,
                    max_nesting: func.max_nesting.unwrap_or(0).min(255) as u8,
                    return_count: 0,
                    commit_count: 0,
                    flags,
                };

                // Store string properties in extra_props side table
                let params_str = func.parameters.join(",");
                let has_params = !params_str.is_empty();
                let has_doc = func.doc_comment.is_some();
                if has_params || has_doc {
                    let ep = ExtraProps {
                        params: if has_params {
                            Some(i.intern(&params_str))
                        } else {
                            None
                        },
                        doc_comment: func.doc_comment.as_ref().map(|d| i.intern(d)),
                        ..Default::default()
                    };
                    graph.set_extra_props(func_node.qualified_name, ep);
                }

                func_nodes.push(func_node);
                edges.push((
                    relative_str.clone(),
                    func.qualified_name.clone(),
                    CodeEdge::contains(),
                ));

                // Module calls decorated functions at load time
                emit_decorator_call_edge(
                    &func.qualified_name,
                    &relative_str,
                    !func.annotations.is_empty(),
                    &mut edges,
                );
            }

            // Class nodes
            for class in &result.classes {
                let mut flags: u8 = 0;
                if !class.annotations.is_empty() {
                    flags |= FLAG_HAS_DECORATORS;
                }

                let class_node = CodeNode {
                    kind: NodeKind::Class,
                    name: i.intern(&class.name),
                    qualified_name: i.intern(&class.qualified_name),
                    file_path: rel_key,
                    language: lang_key,
                    line_start: class.line_start,
                    line_end: class.line_end,
                    complexity: 0,
                    param_count: 0,
                    method_count: class.methods.len().min(65535) as u16,
                    max_nesting: 0,
                    return_count: 0,
                    commit_count: 0,
                    flags,
                };

                // Store string properties in extra_props side table
                if class.doc_comment.is_some() {
                    let ep = ExtraProps {
                        doc_comment: class.doc_comment.as_ref().map(|d| i.intern(d)),
                        ..Default::default()
                    };
                    graph.set_extra_props(class_node.qualified_name, ep);
                }

                class_nodes.push(class_node);
                edges.push((
                    relative_str.clone(),
                    class.qualified_name.clone(),
                    CodeEdge::contains(),
                ));
            }

            // Call edges
            build_call_edges_fast(
                &mut edges,
                result,
                parse_results,
                repo_path,
                &global_func_map,
                &module_lookup,
            );

            // Import edges
            build_import_edges_fast(&mut edges, result, &relative_str, &module_lookup);

            let count = counter.fetch_add(1, Ordering::Relaxed);
            if count.is_multiple_of(100) {
                graph_bar.set_position(count as u64);
            }

            (relative_str, file_nodes, func_nodes, class_nodes, edges)
        })
        .collect();

    // Sort by file path for deterministic node insertion order (NodeIndex stability)
    let mut file_results = file_results;
    file_results.sort_by(|a, b| a.0.cmp(&b.0));

    // Merge results from all threads
    graph_bar.set_message("Merging graph data...");
    let mut all_file_nodes = Vec::with_capacity(parse_results.len());
    let mut all_func_nodes = Vec::with_capacity(total_functions);
    let mut all_class_nodes = Vec::with_capacity(total_classes);
    let mut all_edges = Vec::new();

    for (_file_path, file_nodes, func_nodes, class_nodes, edges) in file_results {
        all_file_nodes.extend(file_nodes);
        all_func_nodes.extend(func_nodes);
        all_class_nodes.extend(class_nodes);
        all_edges.extend(edges);
    }

    // Batch insert all nodes
    graph_bar.set_message("Inserting nodes...");
    graph.add_nodes_batch(all_file_nodes);
    graph.add_nodes_batch(all_func_nodes);
    graph.add_nodes_batch(all_class_nodes);

    // Batch insert all edges
    graph_bar.set_message("Inserting edges...");
    graph.add_edges_batch(all_edges);

    graph_bar.finish_with_message(format!("{}Built code graph", style("✓ ").green()));

    // Persist graph and stats
    graph
        .save()
        .with_context(|| "Failed to save graph database")?;
    save_graph_stats(graph, repo_path)?;

    // Build ValueStore from parse results
    let value_store = build_value_store(graph, parse_results);

    Ok(value_store)
}

/// Build the code graph in chunks to limit peak memory for huge repos.
///
/// Returns a [`ValueStore`] containing all resolved symbolic values extracted
/// during parsing, with cross-function propagation already applied.
pub(super) fn build_graph_chunked(
    graph: &Arc<GraphStore>,
    repo_path: &Path,
    parse_results: &[(PathBuf, ParseResult)],
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
    chunk_size: usize,
) -> Result<crate::values::store::ValueStore> {
    // Pre-allocate graph capacity upfront even though we insert in chunks.
    // The graph itself still accumulates all nodes/edges, so reserving the
    // full estimated capacity avoids reallocations across chunk boundaries.
    let (estimated_nodes, estimated_edges) = estimate_graph_capacity(parse_results);
    graph.reserve_capacity(estimated_nodes, estimated_edges);

    let graph_bar = multi.add(ProgressBar::new(parse_results.len() as u64));
    graph_bar.set_style(bar_style.clone());
    graph_bar.set_message("Building code graph (chunked)...");

    // Build global lookup structures (unavoidable - needed for cross-file references)
    // But we can at least build them more memory-efficiently
    graph_bar.set_message("Building lookup tables...");
    let global_func_map = build_global_function_map(parse_results);
    let module_lookup = ModuleLookup::build(parse_results, repo_path);

    let counter = AtomicUsize::new(0);
    let total_chunks = parse_results.len().div_ceil(chunk_size);

    // Process in chunks to limit peak memory from intermediate results
    for (chunk_idx, chunk) in parse_results.chunks(chunk_size).enumerate() {
        graph_bar.set_message(format!(
            "Building graph (chunk {}/{})",
            chunk_idx + 1,
            total_chunks
        ));

        // Process this chunk in parallel
        let chunk_results: Vec<_> = chunk
            .par_iter()
            .map(|(file_path, result)| {
                let relative_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);
                let relative_str = relative_path.display().to_string();

                let i = graph.interner();
                let rel_key = i.intern(&relative_str);
                let lang_key = i.intern(detect_language_from_path_str(&relative_str));

                let mut file_nodes = Vec::with_capacity(1);
                let mut func_nodes = Vec::with_capacity(result.functions.len());
                let mut class_nodes = Vec::with_capacity(result.classes.len());
                let mut edges: Vec<(String, String, CodeEdge)> = Vec::new();
                // File node — compute LOC from max line_end of functions/classes
                let file_loc = result.functions.iter().map(|f| f.line_end)
                    .chain(result.classes.iter().map(|c| c.line_end))
                    .max()
                    .unwrap_or(0);
                file_nodes.push(CodeNode {
                    kind: NodeKind::File,
                    name: rel_key,
                    qualified_name: rel_key,
                    file_path: rel_key,
                    language: lang_key,
                    line_start: 1,
                    line_end: file_loc,
                    complexity: 0,
                    param_count: 0,
                    method_count: 0,
                    max_nesting: 0,
                    return_count: 0,
                    commit_count: 0,
                    flags: 0,
                });

                // Function nodes
                for func in &result.functions {
                    let complexity = func.complexity.unwrap_or(1);
                    let address_taken = result.address_taken.contains(&func.name);

                    let func_node = build_func_node(graph, func, &relative_str, complexity, address_taken);

                    // Store string properties in extra_props side table
                    let params_str = func.parameters.join(",");
                    let has_params = !params_str.is_empty();
                    let has_doc = func.doc_comment.is_some();
                    if has_params || has_doc {
                        let ep = ExtraProps {
                            params: if has_params {
                                Some(i.intern(&params_str))
                            } else {
                                None
                            },
                            doc_comment: func.doc_comment.as_ref().map(|d| i.intern(d)),
                            ..Default::default()
                        };
                        graph.set_extra_props(func_node.qualified_name, ep);
                    }

                    func_nodes.push(func_node);
                    edges.push((
                        relative_str.clone(),
                        func.qualified_name.clone(),
                        CodeEdge::contains(),
                    ));

                    // Module calls decorated functions at load time
                    emit_decorator_call_edge(
                        &func.qualified_name,
                        &relative_str,
                        !func.annotations.is_empty(),
                        &mut edges,
                    );
                }

                // Class nodes
                for class in &result.classes {
                    let class_node = build_class_node(graph, class, &relative_str);

                    // Store string properties in extra_props side table
                    if class.doc_comment.is_some() {
                        let ep = ExtraProps {
                            doc_comment: class.doc_comment.as_ref().map(|d| i.intern(d)),
                            ..Default::default()
                        };
                        graph.set_extra_props(class_node.qualified_name, ep);
                    }

                    class_nodes.push(class_node);
                    edges.push((
                        relative_str.clone(),
                        class.qualified_name.clone(),
                        CodeEdge::contains(),
                    ));
                }

                // Call edges (using global lookup)
                build_call_edges_fast(
                    &mut edges,
                    result,
                    parse_results,
                    repo_path,
                    &global_func_map,
                    &module_lookup,
                );

                // Import edges (using global lookup)
                build_import_edges_fast(&mut edges, result, &relative_str, &module_lookup);

                let count = counter.fetch_add(1, Ordering::Relaxed);
                if count.is_multiple_of(100) {
                    graph_bar.set_position(count as u64);
                }

                (relative_str, file_nodes, func_nodes, class_nodes, edges)
            })
            .collect();

        // Sort by file path for deterministic node insertion order (NodeIndex stability)
        let mut chunk_results = chunk_results;
        chunk_results.sort_by(|a, b| a.0.cmp(&b.0));

        // Insert this chunk's data immediately (don't accumulate all chunks)
        for (_file_path, file_nodes, func_nodes, class_nodes, edges) in chunk_results {
            graph.add_nodes_batch(file_nodes);
            graph.add_nodes_batch(func_nodes);
            graph.add_nodes_batch(class_nodes);
            graph.add_edges_batch(edges);
        }

        // Memory is released here when chunk_results goes out of scope
    }

    graph_bar.finish_with_message(format!("{}Built code graph (chunked)", style("✓ ").green()));

    // Persist graph and stats
    graph
        .save()
        .with_context(|| "Failed to save graph database")?;
    save_graph_stats(graph, repo_path)?;

    // Build ValueStore from parse results
    let value_store = build_value_store(graph, parse_results);

    Ok(value_store)
}

/// Build global function name -> qualified name map (parallel)
pub(super) fn build_global_function_map(
    parse_results: &[(PathBuf, ParseResult)],
) -> HashMap<String, String> {
    // Parallel collection then merge - avoids lock contention
    let maps: Vec<HashMap<String, String>> = parse_results
        .par_iter()
        .map(|(_, result)| {
            let mut local_map = HashMap::with_capacity(result.functions.len());
            for func in &result.functions {
                local_map.insert(func.name.clone(), func.qualified_name.clone());
            }
            local_map
        })
        .collect();

    // Merge all maps - estimate total size for efficiency
    let total_size: usize = maps.iter().map(|m| m.len()).sum();
    let mut final_map = HashMap::with_capacity(total_size);
    for map in maps {
        final_map.extend(map);
    }
    final_map
}

/// Pre-computed lookup structures for efficient edge resolution
pub(super) struct ModuleLookup {
    /// file_stem (e.g. "utils") -> Vec<(file_path_str, file_index)>
    by_stem: BTreeMap<String, Vec<(String, usize)>>,
    /// Various module path patterns -> Vec<(file_path_str, file_index)>
    by_pattern: BTreeMap<String, Vec<(String, usize)>>,
}

impl ModuleLookup {
    pub(super) fn build(parse_results: &[(PathBuf, ParseResult)], repo_path: &Path) -> Self {
        // Build index entries in parallel
        let entries: Vec<(usize, String, String, Vec<String>)> = parse_results
            .par_iter()
            .enumerate()
            .map(|(idx, (file_path, _))| {
                let relative = file_path.strip_prefix(repo_path).unwrap_or(file_path);
                let relative_str = relative.display().to_string();
                let file_stem = relative
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
                    .to_string();
                let patterns = generate_module_patterns(&relative_str);
                (idx, relative_str, file_stem, patterns)
            })
            .collect();

        // Build lookup maps
        let mut by_stem: BTreeMap<String, Vec<(String, usize)>> = BTreeMap::new();
        let mut by_pattern: BTreeMap<String, Vec<(String, usize)>> = BTreeMap::new();

        for (idx, relative_str, file_stem, patterns) in entries {
            by_stem
                .entry(file_stem)
                .or_default()
                .push((relative_str.clone(), idx));

            for pattern in patterns {
                by_pattern
                    .entry(pattern)
                    .or_default()
                    .push((relative_str.clone(), idx));
            }
        }

        // Sort candidate vecs for deterministic resolution order
        for candidates in by_stem.values_mut() {
            candidates.sort_by(|a, b| a.0.cmp(&b.0));
        }
        for candidates in by_pattern.values_mut() {
            candidates.sort_by(|a, b| a.0.cmp(&b.0));
        }

        ModuleLookup {
            by_stem,
            by_pattern,
        }
    }

    #[allow(dead_code)] // Planned for import resolution improvements
    pub(super) fn find_matches(
        &self,
        import_path: &str,
        _parse_results: &[(PathBuf, ParseResult)],
        _repo_path: &Path,
    ) -> Vec<String> {
        let clean_import = import_path
            .trim_start_matches("./")
            .trim_start_matches("../")
            .trim_start_matches("crate::")
            .trim_start_matches("super::");

        let module_parts: Vec<&str> = clean_import.split("::").collect();
        let first_module = module_parts.first().copied().unwrap_or("");
        let _python_path = clean_import.replace('.', "/");

        let mut matches = Vec::new();

        // Try direct pattern lookup first (O(1) instead of O(n))
        Self::collect_paths(&mut matches, self.by_pattern.get(clean_import));

        // Try file stem lookup
        if matches.is_empty() {
            Self::collect_paths(&mut matches, self.by_stem.get(first_module));
        }

        // If still no matches, fall back to pattern matching (but on fewer candidates)
        if matches.is_empty() {
            Self::collect_paths(&mut matches, self.by_stem.get(clean_import));
        }

        // Final fallback: check all patterns for partial matches
        if matches.is_empty() {
            self.collect_partial_matches(&mut matches, clean_import);
        }

        matches
    }

    /// Push all paths from an optional candidate list into matches
    fn collect_paths(matches: &mut Vec<String>, candidates: Option<&Vec<(String, usize)>>) {
        let Some(candidates) = candidates else { return };
        for (path, _) in candidates {
            matches.push(path.clone());
        }
    }

    /// Collect paths from patterns that partially match the import
    fn collect_partial_matches(&self, matches: &mut Vec<String>, clean_import: &str) {
        let new_paths = self.by_pattern.iter()
            .filter(|(pattern, _)| pattern.contains(clean_import) || clean_import.contains(pattern.as_str()))
            .flat_map(|(_, candidates)| candidates.iter().map(|(path, _)| path.clone()));
        for path in new_paths {
            if !matches.contains(&path) {
                matches.push(path);
            }
        }
    }
}

/// Common method names that exist on standard library types and traits.
/// These are extracted as bare names by method_call_expression parsing and
/// must NOT be resolved via the global function map, as that would conflate
/// unrelated methods (e.g., `str::find` vs a local `fn find`) into a single
/// graph node, producing massive false-positive fan-in counts.
pub(crate) const AMBIGUOUS_METHOD_NAMES: &[&str] = &[
    // Iterator trait methods
    "find",
    "map",
    "filter",
    "fold",
    "reduce",
    "collect",
    "any",
    "all",
    "count",
    "sum",
    "min",
    "max",
    "zip",
    "chain",
    "skip",
    "take",
    "flat_map",
    "for_each",
    "enumerate",
    "peekable",
    "position",
    // Option/Result methods
    "unwrap",
    "expect",
    "ok",
    "err",
    "map_err",
    "unwrap_or",
    "unwrap_or_else",
    "unwrap_or_default",
    "and_then",
    "or_else",
    "is_some",
    "is_none",
    "is_ok",
    "is_err",
    // String/str methods
    "contains",
    "starts_with",
    "ends_with",
    "replace",
    "trim",
    "split",
    "join",
    "to_lowercase",
    "to_uppercase",
    "chars",
    "bytes",
    "lines",
    // Vec/slice methods
    "push",
    "pop",
    "insert",
    "remove",
    "sort",
    "sort_by",
    "retain",
    "extend",
    "truncate",
    "clear",
    "is_empty",
    "len",
    "first",
    "last",
    "iter",
    "into_iter",
    "iter_mut",
    // HashMap/BTreeMap
    "entry",
    "or_insert",
    "or_default",
    "or_insert_with",
    "keys",
    "values",
    // Conversion traits
    "into",
    "from",
    "as_ref",
    "as_mut",
    "to_owned",
    "to_string",
    "clone",
    // Display/Debug/comparison traits
    "fmt",
    "eq",
    "cmp",
    "partial_cmp",
    "hash",
    // I/O
    "read",
    "write",
    "flush",
    "close",
    "seek",
    // Sync primitives
    "lock",
    "unlock",
    "send",
    "recv",
    // Common trait methods
    "next",
    "poll",
    "drop",
    "deref",
    "index",
    "borrow",
    // Python/JS common builtins (also bare names from method calls)
    "append",
    "update",
    "items",
    "keys",
    "values",
    "strip",
    "encode",
    "decode",
    "match",
    "test",
    "exec",
    "apply",
    "bind",
    "call",
    "then",
    "catch",
    "finally",
    "resolve",
    "reject",
    "slice",
    "splice",
    "shift",
    "unshift",
    "concat",
    "includes",
    "indexOf",
    "forEach",
    "some",
    "every",
    "flat",
    "fill",
    "at",
    "with",
];

/// Build call edges using pre-computed lookup (O(1) module resolution)
pub(super) fn build_call_edges_fast(
    edges: &mut Vec<(String, String, CodeEdge)>,
    result: &ParseResult,
    parse_results: &[(PathBuf, ParseResult)],
    _repo_path: &Path,
    global_func_map: &HashMap<String, String>,
    module_lookup: &ModuleLookup,
) {
    for (caller, callee) in &result.calls {
        let parts: Vec<&str> = callee.rsplitn(2, "::").collect();
        let callee_name = parts[0];
        let callee_module = if parts.len() > 1 {
            Some(parts[1])
        } else {
            None
        };
        let callee_name = callee_name.rsplit('.').next().unwrap_or(callee_name);

        // Try to find callee in this file first (fast path)
        if let Some(callee_func) = result.functions.iter().find(|f| f.name == callee_name) {
            edges.push((
                caller.clone(),
                callee_func.qualified_name.clone(),
                CodeEdge::calls(),
            ));
            continue;
        }

        // Skip cross-file resolution for bare method names that are ambiguous.
        // These come from method_call_expression nodes where the parser extracts
        // just the method name without receiver type. Resolving "find" globally
        // would conflate str::find, Iterator::find, and user-defined find() into
        // one node, creating massive false-positive fan-in/fan-out counts.
        if callee_module.is_none()
            && AMBIGUOUS_METHOD_NAMES.contains(&callee_name)
        {
            continue;
        }

        // Use module lookup for O(1) cross-file resolution
        let found = resolve_callee_cross_file(
            callee_name,
            callee_module,
            module_lookup,
            parse_results,
            global_func_map,
        );
        let callee_qn = match found {
            Some(qn) => qn,
            None => continue,
        };
        edges.push((caller.clone(), callee_qn, CodeEdge::calls()));
    }
}

/// Resolve a callee function name across files using module lookup.
fn resolve_callee_cross_file(
    callee_name: &str,
    callee_module: Option<&str>,
    module_lookup: &ModuleLookup,
    parse_results: &[(PathBuf, crate::parsers::ParseResult)],
    global_func_map: &std::collections::HashMap<String, String>,
) -> Option<String> {
    if let Some(module) = callee_module {
        let candidates = module_lookup.by_stem.get(module)?;
        for (_file_path, idx) in candidates {
            let (_, other_result) = parse_results.get(*idx)?;
            if let Some(func) = other_result
                .functions
                .iter()
                .find(|f| f.name == callee_name)
            {
                return Some(func.qualified_name.clone());
            }
        }
    }
    global_func_map.get(callee_name).cloned()
}

/// Find the first candidate path that isn't the source file itself
fn first_other_file(candidates: Option<&Vec<(String, usize)>>, exclude: &str) -> Option<String> {
    candidates?.iter().find(|(p, _)| p != exclude).map(|(p, _)| p.clone())
}

/// Build import edges using pre-computed lookup (O(1) instead of O(n))
pub(super) fn build_import_edges_fast(
    edges: &mut Vec<(String, String, CodeEdge)>,
    result: &ParseResult,
    relative_str: &str,
    module_lookup: &ModuleLookup,
) {
    for import_info in &result.imports {
        let clean_import = import_info
            .path
            .trim_start_matches("./")
            .trim_start_matches("../")
            .trim_start_matches("crate::")
            .trim_start_matches("super::");

        let module_parts: Vec<&str> = clean_import.split("::").collect();
        let first_module = module_parts.first().copied().unwrap_or("");
        let python_path = clean_import.replace('.', "/");

        // Try fast lookup paths in order of specificity
        let matched_file = first_other_file(module_lookup.by_pattern.get(clean_import), relative_str)
            .or_else(|| first_other_file(module_lookup.by_pattern.get(&python_path), relative_str))
            .or_else(|| first_other_file(module_lookup.by_stem.get(first_module), relative_str))
            .or_else(|| first_other_file(module_lookup.by_stem.get(clean_import), relative_str));

        if let Some(target_file) = matched_file {
            let mut import_edge = CodeEdge::imports();
            if import_info.is_type_only {
                import_edge = import_edge.with_type_only();
            }
            edges.push((relative_str.to_string(), target_file, import_edge));
        }
    }
}

/// Save graph statistics to JSON
pub(super) fn save_graph_stats(graph: &GraphStore, repo_path: &Path) -> Result<()> {
    let graph_stats = serde_json::json!({
        "total_files": graph.get_files().len(),
        "total_functions": graph.get_functions().len(),
        "total_classes": graph.get_classes().len(),
        "total_nodes": graph.node_count(),
        "total_edges": graph.edge_count(),
        "calls": graph.get_calls().len(),
        "imports": graph.get_imports().len(),
    });
    let stats_path = crate::cache::graph_stats_path(repo_path);
    std::fs::write(&stats_path, serde_json::to_string_pretty(&graph_stats)?)?;
    Ok(())
}

/// Build a [`ValueStore`] from parse results and the completed graph.
///
/// This function:
/// 1. Ingests all `RawParseValues` from parse results into a `ValueStore`
/// 2. Inserts synthetic `Variable` nodes for module-level constants into the graph
/// 3. Computes topological ordering of functions from the call graph
/// 4. Runs cross-function value propagation using the topo order
fn build_value_store(
    graph: &Arc<GraphStore>,
    parse_results: &[(PathBuf, ParseResult)],
) -> crate::values::store::ValueStore {
    use crate::values::store::ValueStore;
    use std::collections::{HashMap as StdHashMap, HashSet as StdHashSet};

    // 1. Ingest all raw parse values into the store
    let mut value_store = ValueStore::new();
    for (_file_path, result) in parse_results {
        if let Some(raw) = result.raw_values.clone() {
            value_store.ingest(raw);
        }
    }

    // 2. Insert Variable nodes for module-level constants into the graph
    let i = graph.interner();
    let empty = i.empty_key();
    let var_nodes: Vec<CodeNode> = value_store
        .constants
        .keys()
        .map(|qn| {
            let qn_key = i.intern(qn);
            CodeNode {
                kind: NodeKind::Variable,
                name: qn_key,
                qualified_name: qn_key,
                file_path: empty,
                language: empty,
                line_start: 0,
                line_end: 0,
                complexity: 0,
                param_count: 0,
                method_count: 0,
                max_nesting: 0,
                return_count: 0,
                commit_count: 0,
                flags: 0,
            }
        })
        .collect();
    if !var_nodes.is_empty() {
        graph.add_nodes_batch(var_nodes);
    }

    // 3. Compute topological order of call graph for cross-function propagation.
    //    We use the function QNs in arbitrary order since the internal petgraph
    //    is not directly exposed. The propagation module's cycle detection and
    //    depth limiting handle cycles safely, so arbitrary order is correct
    //    (just potentially less efficient than true topo order).
    let topo_order: Vec<String> = graph
        .get_functions()
        .iter()
        .map(|n| i.resolve(n.qualified_name).to_string())
        .collect();

    // 4. Build call map for propagation (caller -> set of callees)
    let call_map: StdHashMap<String, StdHashSet<String>> = {
        let calls = graph.get_calls();
        let mut map = StdHashMap::new();
        for (caller, callee) in calls {
            map.entry(i.resolve(caller).to_string())
                .or_insert_with(StdHashSet::new)
                .insert(i.resolve(callee).to_string());
        }
        map
    };

    // 5. Run cross-function value propagation
    crate::values::propagation::resolve_cross_function(&mut value_store, &topo_order, &call_map);

    value_store
}

/// Graph builder that processes files in streaming fashion
///
/// This implementation receives parsed files one at a time and immediately
/// adds nodes to the graph. Edges are collected for batch insertion at the end.
/// This prevents OOM on large repositories (75k+ files).
#[allow(dead_code)] // Infrastructure for streaming graph building
pub(super) struct StreamingGraphBuilderImpl {
    graph: Arc<GraphStore>,
    repo_path: PathBuf,
    function_index: FunctionIndex,
    module_index: ModuleIndex,

    // Collected edges for batch insertion
    edges: Vec<(String, String, CodeEdge)>,

    // Stats
    total_functions: usize,
    total_classes: usize,
}

#[allow(dead_code)]
impl StreamingGraphBuilderImpl {
    pub(super) fn new(
        graph: Arc<GraphStore>,
        repo_path: PathBuf,
        function_index: FunctionIndex,
        module_index: ModuleIndex,
    ) -> Self {
        Self {
            graph,
            repo_path,
            function_index,
            module_index,
            edges: Vec::new(),
            total_functions: 0,
            total_classes: 0,
        }
    }
}

impl StreamingGraphBuilder for StreamingGraphBuilderImpl {
    fn on_file(&mut self, info: ParsedFileInfo) -> Result<()> {
        let i = self.graph.interner();
        let rel_key = i.intern(&info.relative_path);
        let lang_key = i.intern(&info.language);

        // Add file node immediately
        let file_node = CodeNode {
            kind: NodeKind::File,
            name: rel_key,
            qualified_name: rel_key,
            file_path: rel_key,
            language: lang_key,
            line_start: 1,
            line_end: info.loc as u32,
            complexity: 0,
            param_count: 0,
            method_count: 0,
            max_nesting: 0,
            return_count: 0,
            commit_count: 0,
            flags: 0,
        };
        self.graph.add_node(file_node);

        // Add function nodes immediately
        for func in &info.functions {
            let address_taken = info.address_taken.contains(&func.name);

            let mut flags: u8 = 0;
            if func.is_async {
                flags |= FLAG_IS_ASYNC;
            }
            if address_taken {
                flags |= FLAG_ADDRESS_TAKEN;
            }
            if func.has_annotations {
                flags |= FLAG_HAS_DECORATORS;
            }

            let func_node = CodeNode {
                kind: NodeKind::Function,
                name: i.intern(&func.name),
                qualified_name: i.intern(&func.qualified_name),
                file_path: rel_key,
                language: lang_key,
                line_start: func.line_start,
                line_end: func.line_end,
                complexity: func.complexity as u16,
                param_count: 0,
                method_count: 0,
                max_nesting: 0,
                return_count: 0,
                commit_count: 0,
                flags,
            };
            self.graph.add_node(func_node);

            // Collect contains edge
            self.edges.push((
                info.relative_path.clone(),
                func.qualified_name.clone(),
                CodeEdge::contains(),
            ));

            // Module calls decorated functions at load time
            if func.has_annotations {
                self.edges.push((
                    info.relative_path.clone(),
                    func.qualified_name.clone(),
                    CodeEdge::calls(),
                ));
            }

            self.total_functions += 1;
        }

        // Add class nodes immediately
        for class in &info.classes {
            let class_node = CodeNode {
                kind: NodeKind::Class,
                name: i.intern(&class.name),
                qualified_name: i.intern(&class.qualified_name),
                file_path: rel_key,
                language: lang_key,
                line_start: class.line_start,
                line_end: class.line_end,
                complexity: 0,
                param_count: 0,
                method_count: class.method_count as u16,
                max_nesting: 0,
                return_count: 0,
                commit_count: 0,
                flags: 0,
            };
            self.graph.add_node(class_node);

            // Collect contains edge
            self.edges.push((
                info.relative_path.clone(),
                class.qualified_name.clone(),
                CodeEdge::contains(),
            ));

            self.total_classes += 1;
        }

        // Collect call edges (resolve using index)
        for (caller, callee) in &info.calls {
            let parts: Vec<&str> = callee.rsplitn(2, "::").collect();
            let callee_name = parts[0];
            let callee_name = callee_name.rsplit('.').next().unwrap_or(callee_name);

            // Try to find callee - first check this file's functions
            let callee_qn =
                if let Some(func) = info.functions.iter().find(|f| f.name == callee_name) {
                    func.qualified_name.clone()
                } else if let Some(qn) = self.function_index.name_to_qualified.get(callee_name) {
                    qn.clone()
                } else {
                    continue; // Can't resolve, skip this edge
                };

            self.edges
                .push((caller.clone(), callee_qn, CodeEdge::calls()));
        }

        // Collect import edges (resolve using module index)
        for import in &info.imports {
            let matches = self.module_index.find_matches(&import.path);
            let Some(target) = matches.first() else { continue };
            if target == &info.relative_path {
                continue;
            }
            let mut import_edge = CodeEdge::imports();
            if import.is_type_only {
                import_edge = import_edge.with_type_only();
            }
            self.edges
                .push((info.relative_path.clone(), target.clone(), import_edge));
        }

        Ok(())
    }

    fn finalize(&mut self) -> Result<()> {
        // Batch insert all collected edges
        self.graph.add_edges_batch(std::mem::take(&mut self.edges));

        // Persist graph
        self.graph.save()?;

        Ok(())
    }
}

/// Parse files and build graph using BOUNDED PARALLEL PIPELINE
///
/// This function uses crossbeam channels with ADAPTIVE sizing:
/// - Small repos (<5k files): buffer=100 for speed
/// - Large repos (50k+ files): buffer=10 for memory
/// - Periodic edge flushing prevents unbounded edge accumulation
///
/// Benefits:
/// - Parallel parsing uses all CPU cores
/// - Bounded memory via adaptive channel capacities
/// - Periodic edge flushing caps memory growth
/// - True backpressure - parsers block when consumer is slow
///
/// Memory target: <1.5GB for 50k files, <2GB for 100k files
pub(super) fn parse_and_build_streaming(
    files: &[PathBuf],
    repo_path: &Path,
    graph: Arc<GraphStore>,
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
) -> Result<(usize, usize)> {
    let parse_bar = multi.add(ProgressBar::new(files.len() as u64));
    parse_bar.set_style(bar_style.clone());

    // Adaptive config based on repo size
    let config = PipelineConfig::for_repo_size(files.len());

    parse_bar.set_message(format!(
        "Bounded pipeline ({} workers, buf={}, flush@{})...",
        config.num_workers, config.buffer_size, config.edge_flush_threshold
    ));

    // Use the new bounded pipeline with adaptive sizing
    let (graph_stats, parse_stats) = run_bounded_pipeline(
        files.to_vec(),
        repo_path,
        graph,
        config,
        Some(&|count, _total| {
            if count % 100 == 0 {
                parse_bar.set_position(count as u64);
            }
        }),
    )?;

    let flush_info = if graph_stats.edge_flushes > 0 {
        format!(", {} flushes", graph_stats.edge_flushes)
    } else {
        String::new()
    };

    parse_bar.finish_with_message(format!(
        "{}Bounded pipeline: {} files ({} fns, {} cls{})",
        style("✓ ").green(),
        style(parse_stats.parsed_files).cyan(),
        style(graph_stats.functions_added).cyan(),
        style(graph_stats.classes_added).cyan(),
        style(flush_info).dim(),
    ));

    Ok((graph_stats.functions_added, graph_stats.classes_added))
}

/// Stream-parse with walk+parse overlap.
///
/// Unlike `parse_and_build_streaming` which accepts a pre-collected `&[PathBuf]`,
/// this variant takes a `Receiver<PathBuf>` that the file walker feeds into
/// concurrently. Parser threads start working as soon as the first files are
/// discovered, rather than waiting for the entire walk to complete.
///
/// Returns `(total_functions, total_classes)` matching the streaming contract.
pub(super) fn parse_and_build_streaming_overlapped(
    file_receiver: crossbeam_channel::Receiver<PathBuf>,
    repo_path: &Path,
    graph: Arc<GraphStore>,
    multi: &MultiProgress,
    bar_style: &ProgressStyle,
    config: PipelineConfig,
) -> Result<(usize, usize)> {
    let parse_bar = multi.add(ProgressBar::new_spinner());
    parse_bar.set_style(bar_style.clone());
    parse_bar.set_message(format!(
        "Overlapped walk+parse ({} workers, buf={}, flush@{})...",
        config.num_workers, config.buffer_size, config.edge_flush_threshold
    ));

    let (graph_stats, parse_stats) = run_bounded_pipeline_from_channel(
        file_receiver,
        repo_path,
        graph,
        config,
        Some(&|count, _total| {
            if count % 100 == 0 {
                parse_bar.set_position(count as u64);
            }
        }),
    )?;

    let flush_info = if graph_stats.edge_flushes > 0 {
        format!(", {} flushes", graph_stats.edge_flushes)
    } else {
        String::new()
    };

    parse_bar.finish_with_message(format!(
        "{}Overlapped pipeline: {} files ({} fns, {} cls{})",
        style("✓ ").green(),
        style(parse_stats.parsed_files).cyan(),
        style(graph_stats.functions_added).cyan(),
        style(graph_stats.classes_added).cyan(),
        style(flush_info).dim(),
    ));

    Ok((graph_stats.functions_added, graph_stats.classes_added))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;
    use crate::models::Function;
    use crate::parsers::ParseResult;
    use std::collections::HashSet as StdHashSet;

    /// Helper: create a minimal ParseResult with decorated functions.
    fn make_parse_result_with_decorators() -> ParseResult {
        ParseResult {
            functions: vec![
                Function {
                    name: "index".to_string(),
                    qualified_name: "app.routes.index:5".to_string(),
                    file_path: PathBuf::from("app/routes.py"),
                    line_start: 5,
                    line_end: 8,
                    parameters: vec![],
                    return_type: None,
                    is_async: false,
                    complexity: Some(1),
                    max_nesting: None,
                    doc_comment: None,
                    annotations: vec!["app.route".to_string()],
                },
                Function {
                    name: "helper".to_string(),
                    qualified_name: "app.routes.helper:10".to_string(),
                    file_path: PathBuf::from("app/routes.py"),
                    line_start: 10,
                    line_end: 12,
                    parameters: vec![],
                    return_type: None,
                    is_async: false,
                    complexity: Some(1),
                    max_nesting: None,
                    doc_comment: None,
                    annotations: vec![], // no decorator
                },
            ],
            classes: vec![],
            imports: vec![],
            calls: vec![],
            address_taken: StdHashSet::new(),
            raw_values: None,
        }
    }

    #[test]
    fn test_emit_decorator_call_edge_creates_edge_from_file() {
        let mut edges: Vec<(String, String, CodeEdge)> = Vec::new();

        // Decorated function — should create edge from file to function
        emit_decorator_call_edge(
            "app.routes.index:5",
            "app/routes.py",
            true,
            &mut edges,
        );

        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].0, "app/routes.py"); // File node is the caller
        assert_eq!(edges[0].1, "app.routes.index:5");

        // Second decorated function in the same module
        emit_decorator_call_edge(
            "app.routes.about:20",
            "app/routes.py",
            true,
            &mut edges,
        );

        // Two edges, both from the same file node
        assert_eq!(edges.len(), 2);
        assert_eq!(edges[1].0, "app/routes.py");
        assert_eq!(edges[1].1, "app.routes.about:20");
    }

    #[test]
    fn test_emit_decorator_call_edge_skips_undecorated() {
        let mut edges: Vec<(String, String, CodeEdge)> = Vec::new();

        emit_decorator_call_edge(
            "app.routes.helper:10",
            "app/routes.py",
            false,
            &mut edges,
        );

        assert!(edges.is_empty());
    }

    #[test]
    fn test_module_qn_from_path() {
        assert_eq!(module_qn_from_path("app/routes.py"), "app.routes");
        assert_eq!(module_qn_from_path("src/lib.rs"), "src.lib");
        assert_eq!(
            module_qn_from_path("src/handlers/auth.ts"),
            "src.handlers.auth"
        );
        assert_eq!(module_qn_from_path("main.go"), "main");
    }

    #[test]
    fn test_decorated_function_has_callers_in_graph() {
        let graph = Arc::new(GraphStore::in_memory());
        let result = make_parse_result_with_decorators();
        let relative_str = "app/routes.py";

        // File node (the caller for decorated functions)
        let i = graph.interner();
        let rel_key = i.intern(relative_str);
        let empty = i.empty_key();
        let file_loc = result.functions.iter().map(|f| f.line_end)
            .chain(result.classes.iter().map(|c| c.line_end))
            .max()
            .unwrap_or(0);
        let file_node = CodeNode {
            kind: NodeKind::File,
            name: rel_key,
            qualified_name: rel_key,
            file_path: rel_key,
            language: empty,
            line_start: 1,
            line_end: file_loc,
            complexity: 0,
            param_count: 0,
            method_count: 0,
            max_nesting: 0,
            return_count: 0,
            commit_count: 0,
            flags: 0,
        };

        // Simulate build_graph inline path: create nodes and edges
        let mut func_nodes = vec![file_node];
        let mut edges: Vec<(String, String, CodeEdge)> = Vec::new();

        for func in &result.functions {
            let complexity = func.complexity.unwrap_or(1);
            let func_node = CodeNode {
                kind: NodeKind::Function,
                name: i.intern(&func.name),
                qualified_name: i.intern(&func.qualified_name),
                file_path: rel_key,
                language: empty,
                line_start: func.line_start,
                line_end: func.line_end,
                complexity: complexity as u16,
                param_count: 0,
                method_count: 0,
                max_nesting: 0,
                return_count: 0,
                commit_count: 0,
                flags: 0,
            };
            func_nodes.push(func_node);

            emit_decorator_call_edge(
                &func.qualified_name,
                relative_str,
                !func.annotations.is_empty(),
                &mut edges,
            );
        }

        // Insert into graph
        graph.add_nodes_batch(func_nodes);
        graph.add_edges_batch(edges);

        // Decorated function "index" should have the file node as caller
        let callers = graph.get_callers("app.routes.index:5");
        assert_eq!(callers.len(), 1);
        assert_eq!(i.resolve(callers[0].name), "app/routes.py"); // File node is the caller
        assert_eq!(graph.call_fan_in("app.routes.index:5"), 1);

        // Undecorated function "helper" should have 0 callers
        let callers = graph.get_callers("app.routes.helper:10");
        assert!(callers.is_empty());
        assert_eq!(graph.call_fan_in("app.routes.helper:10"), 0);
    }

    #[test]
    fn test_estimate_graph_capacity_empty() {
        let results: Vec<(PathBuf, ParseResult)> = vec![];
        let (nodes, edges) = estimate_graph_capacity(&results);
        assert_eq!(nodes, 0);
        assert_eq!(edges, 0);
    }

    #[test]
    fn test_estimate_graph_capacity_realistic() {
        use crate::parsers::ImportInfo;

        let results = vec![(
            PathBuf::from("app/main.py"),
            ParseResult {
                functions: vec![
                    Function {
                        name: "foo".to_string(),
                        qualified_name: "app.main.foo:1".to_string(),
                        file_path: PathBuf::from("app/main.py"),
                        line_start: 1,
                        line_end: 5,
                        parameters: vec![],
                        return_type: None,
                        is_async: false,
                        complexity: Some(1),
                        max_nesting: None,
                        doc_comment: None,
                        annotations: vec![],
                    },
                    Function {
                        name: "bar".to_string(),
                        qualified_name: "app.main.bar:6".to_string(),
                        file_path: PathBuf::from("app/main.py"),
                        line_start: 6,
                        line_end: 10,
                        parameters: vec![],
                        return_type: None,
                        is_async: false,
                        complexity: Some(1),
                        max_nesting: None,
                        doc_comment: None,
                        annotations: vec![],
                    },
                ],
                classes: vec![],
                imports: vec![ImportInfo::runtime("os")],
                calls: vec![("app.main.foo:1".to_string(), "bar".to_string())],
                address_taken: StdHashSet::new(),
                raw_values: None,
            },
        )];

        let (nodes, edges) = estimate_graph_capacity(&results);
        // 1 file + 2 functions = 3 nodes
        assert_eq!(nodes, 3);
        // 2 contains (functions) + 1 call + 1 import + 3 (safety margin = nodes) = 7
        assert_eq!(edges, 7);
    }
}
