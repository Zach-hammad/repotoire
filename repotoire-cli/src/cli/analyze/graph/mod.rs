//! Graph building functions for the analyze command
//!
//! This module contains all code graph construction logic:
//! - Building the code graph from parse results
//! - Call edge resolution (edge_builder)
//! - Import edge resolution (edge_builder)
//! - Node construction (node_factory)
//! - Module lookup (module_lookup)
//! - Streaming graph building for huge repos

mod edge_builder;
mod module_lookup;
mod node_factory;

pub(crate) use edge_builder::AMBIGUOUS_METHOD_NAMES;
use edge_builder::{build_call_edges_fast, build_import_edges_fast};
use module_lookup::ModuleLookup;
use node_factory::{build_class_node, build_func_node, emit_decorator_call_edge};

use crate::graph::store_models::{
    ExtraProps, FLAG_ADDRESS_TAKEN, FLAG_HAS_DECORATORS, FLAG_IS_ASYNC, FLAG_IS_EXPORTED,
};
use crate::graph::builder::GraphBuilder;
use crate::graph::interner::{StrKey, global_interner};
use crate::graph::{CodeEdge, CodeNode, NodeKind};
use crate::models::{Class, Function};
use crate::parsers::streaming::{
    FunctionIndex, ModuleIndex, ParsedFileInfo, StreamingGraphBuilder,
};
use crate::parsers::ParseResult;
use anyhow::Result;
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

fn estimate_graph_capacity(parse_results: &[(PathBuf, Arc<ParseResult>)]) -> (usize, usize) {
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
pub fn build_graph(
    graph: &mut GraphBuilder,
    repo_path: &Path,
    parse_results: &[(PathBuf, Arc<ParseResult>)],
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

    // Parallel collection of nodes, edges, and extra_props per file
    let file_results: Vec<_> = parse_results
        .par_iter()
        .map(|(file_path, result)| {
            let relative_path = file_path.strip_prefix(repo_path).unwrap_or(file_path);
            let relative_str = relative_path.display().to_string();

            let i = global_interner();
            let rel_key = i.intern(&relative_str);
            let lang_key = i.intern(detect_language_from_path_str(&relative_str));

            let mut file_nodes = Vec::with_capacity(1);
            let mut func_nodes = Vec::with_capacity(result.functions.len());
            let mut class_nodes = Vec::with_capacity(result.classes.len());
            let mut edges: Vec<(String, String, CodeEdge)> = Vec::new();
            let mut extra_props_batch: Vec<(StrKey, ExtraProps)> = Vec::new();
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
                field_count: 0,
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
                if func.annotations.iter().any(|a| a == "exported") {
                    flags |= FLAG_IS_EXPORTED;
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
                    field_count: 0,
                    max_nesting: func.max_nesting.unwrap_or(0).min(255) as u8,
                    return_count: 0,
                    commit_count: 0,
                    flags,
                };

                // Collect string properties in extra_props for batch insertion
                let params_str = func.parameters.join(",");
                let has_params = !params_str.is_empty();
                let has_doc = func.doc_comment.is_some();
                let has_decorators = !func.annotations.is_empty();
                if has_params || has_doc || has_decorators {
                    let ep = ExtraProps {
                        params: if has_params {
                            Some(i.intern(&params_str))
                        } else {
                            None
                        },
                        doc_comment: func.doc_comment.as_ref().map(|d| i.intern(d)),
                        decorators: if has_decorators {
                            Some(i.intern(&func.annotations.join(",")))
                        } else {
                            None
                        },
                        ..Default::default()
                    };
                    extra_props_batch.push((func_node.qualified_name, ep));
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
                    has_decorators,
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
                    field_count: class.field_count.min(65535) as u16,
                    max_nesting: 0,
                    return_count: 0,
                    commit_count: 0,
                    flags,
                };

                // Collect string properties in extra_props for batch insertion
                let has_class_doc = class.doc_comment.is_some();
                let has_class_decorators = !class.annotations.is_empty();
                if has_class_doc || has_class_decorators {
                    let ep = ExtraProps {
                        doc_comment: class.doc_comment.as_ref().map(|d| i.intern(d)),
                        decorators: if has_class_decorators {
                            Some(i.intern(&class.annotations.join(",")))
                        } else {
                            None
                        },
                        ..Default::default()
                    };
                    extra_props_batch.push((class_node.qualified_name, ep));
                }

                class_nodes.push(class_node);
                edges.push((
                    relative_str.clone(),
                    class.qualified_name.clone(),
                    CodeEdge::contains(),
                ));
            }

            // Trait implementation edges (type implements trait)
            for (type_name, trait_name) in &result.trait_impls {
                if let Some(type_qn) = result.classes.iter()
                    .find(|c| c.name == *type_name)
                    .map(|c| c.qualified_name.clone())
                {
                    edges.push((type_qn, trait_name.clone(), CodeEdge::inherits()));
                }
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

            (relative_str, file_nodes, func_nodes, class_nodes, edges, extra_props_batch)
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
    // Estimate ~3 edges per function (calls + imports + contains)
    let estimated_edges = total_functions * 3 + parse_results.len();
    let mut all_edges = Vec::with_capacity(estimated_edges);
    let mut all_extra_props: Vec<(StrKey, ExtraProps)> = Vec::new();

    for (_file_path, file_nodes, func_nodes, class_nodes, edges, extra_props) in file_results {
        all_file_nodes.extend(file_nodes);
        all_func_nodes.extend(func_nodes);
        all_class_nodes.extend(class_nodes);
        all_edges.extend(edges);
        all_extra_props.extend(extra_props);
    }

    // Batch insert all nodes
    graph_bar.set_message("Inserting nodes...");
    let mut combined_nodes = Vec::with_capacity(
        all_file_nodes.len() + all_func_nodes.len() + all_class_nodes.len(),
    );
    combined_nodes.extend(all_file_nodes);
    combined_nodes.extend(all_func_nodes);
    combined_nodes.extend(all_class_nodes);
    graph.add_nodes_batch(combined_nodes);

    // Batch insert all extra props
    for (qn_key, ep) in all_extra_props {
        graph.set_extra_props(qn_key, ep);
    }

    // Batch insert all edges
    graph_bar.set_message("Inserting edges...");
    graph.add_edges_batch(all_edges);

    graph_bar.finish_with_message(format!("{}Built code graph", style("✓ ").green()));

    // Save graph stats
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
    graph: &mut GraphBuilder,
    repo_path: &Path,
    parse_results: &[(PathBuf, Arc<ParseResult>)],
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

                let i = global_interner();
                let rel_key = i.intern(&relative_str);
                let lang_key = i.intern(detect_language_from_path_str(&relative_str));

                let mut file_nodes = Vec::with_capacity(1);
                let mut func_nodes = Vec::with_capacity(result.functions.len());
                let mut class_nodes = Vec::with_capacity(result.classes.len());
                let mut edges: Vec<(String, String, CodeEdge)> = Vec::new();
                let mut extra_props_batch: Vec<(StrKey, ExtraProps)> = Vec::new();
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
                    field_count: 0,
                    max_nesting: 0,
                    return_count: 0,
                    commit_count: 0,
                    flags: 0,
                });

                // Function nodes
                for func in &result.functions {
                    let complexity = func.complexity.unwrap_or(1);
                    let address_taken = result.address_taken.contains(&func.name);

                    let func_node = build_func_node(func, &relative_str, complexity, address_taken);

                    // Collect string properties in extra_props for batch insertion
                    let params_str = func.parameters.join(",");
                    let has_params = !params_str.is_empty();
                    let has_doc = func.doc_comment.is_some();
                    let has_decorators = !func.annotations.is_empty();
                    if has_params || has_doc || has_decorators {
                        let ep = ExtraProps {
                            params: if has_params {
                                Some(i.intern(&params_str))
                            } else {
                                None
                            },
                            doc_comment: func.doc_comment.as_ref().map(|d| i.intern(d)),
                            decorators: if has_decorators {
                                Some(i.intern(&func.annotations.join(",")))
                            } else {
                                None
                            },
                            ..Default::default()
                        };
                        extra_props_batch.push((func_node.qualified_name, ep));
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
                        has_decorators,
                        &mut edges,
                    );
                }

                // Class nodes
                for class in &result.classes {
                    let class_node = build_class_node(class, &relative_str);

                    // Collect string properties in extra_props for batch insertion
                    let has_class_doc = class.doc_comment.is_some();
                    let has_class_decorators = !class.annotations.is_empty();
                    if has_class_doc || has_class_decorators {
                        let ep = ExtraProps {
                            doc_comment: class.doc_comment.as_ref().map(|d| i.intern(d)),
                            decorators: if has_class_decorators {
                                Some(i.intern(&class.annotations.join(",")))
                            } else {
                                None
                            },
                            ..Default::default()
                        };
                        extra_props_batch.push((class_node.qualified_name, ep));
                    }

                    class_nodes.push(class_node);
                    edges.push((
                        relative_str.clone(),
                        class.qualified_name.clone(),
                        CodeEdge::contains(),
                    ));
                }

                // Trait implementation edges (type implements trait)
                for (type_name, trait_name) in &result.trait_impls {
                    if let Some(type_qn) = result.classes.iter()
                        .find(|c| c.name == *type_name)
                        .map(|c| c.qualified_name.clone())
                    {
                        edges.push((type_qn, trait_name.clone(), CodeEdge::inherits()));
                    }
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

                (relative_str, file_nodes, func_nodes, class_nodes, edges, extra_props_batch)
            })
            .collect();

        // Sort by file path for deterministic node insertion order (NodeIndex stability)
        let mut chunk_results = chunk_results;
        chunk_results.sort_by(|a, b| a.0.cmp(&b.0));

        // Insert this chunk's data immediately (don't accumulate all chunks)
        for (_file_path, file_nodes, func_nodes, class_nodes, edges, extra_props) in chunk_results {
            let mut combined_nodes: Vec<CodeNode> = file_nodes;
            combined_nodes.extend(func_nodes);
            combined_nodes.extend(class_nodes);
            graph.add_nodes_batch(combined_nodes);
            for (qn_key, ep) in extra_props {
                graph.set_extra_props(qn_key, ep);
            }
            graph.add_edges_batch(edges);
        }

        // Memory is released here when chunk_results goes out of scope
    }

    graph_bar.finish_with_message(format!("{}Built code graph (chunked)", style("✓ ").green()));

    // Save graph stats
    save_graph_stats(graph, repo_path)?;

    // Build ValueStore from parse results
    let value_store = build_value_store(graph, parse_results);

    Ok(value_store)
}

/// Build global function name -> qualified name map (parallel)
pub(super) fn build_global_function_map(
    parse_results: &[(PathBuf, Arc<ParseResult>)],
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


pub(super) fn save_graph_stats(graph: &GraphBuilder, repo_path: &Path) -> Result<()> {
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
    graph: &mut GraphBuilder,
    parse_results: &[(PathBuf, Arc<ParseResult>)],
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
    let i = global_interner();
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
                field_count: 0,
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
pub(super) struct StreamingGraphBuilderImpl<'a> {
    graph: &'a mut GraphBuilder,
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
impl<'a> StreamingGraphBuilderImpl<'a> {
    pub(super) fn new(
        graph: &'a mut GraphBuilder,
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

impl<'a> StreamingGraphBuilder for StreamingGraphBuilderImpl<'a> {
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
            field_count: 0,
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
            if func.is_exported {
                flags |= FLAG_IS_EXPORTED;
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
                field_count: 0,
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
                field_count: class.field_count as u16,
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

        // Trait implementation edges (type implements trait)
        for (type_name, trait_name) in &info.trait_impls {
            if let Some(type_qn) = info.classes.iter()
                .find(|c| c.name == *type_name)
                .map(|c| c.qualified_name.clone())
            {
                self.edges.push((type_qn, trait_name.clone(), CodeEdge::inherits()));
            }
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

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use super::node_factory::module_qn_from_path;
    use crate::graph::builder::GraphBuilder;
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
            trait_impls: vec![],
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
        let mut builder = GraphBuilder::new();
        let result = make_parse_result_with_decorators();
        let relative_str = "app/routes.py";

        // File node (the caller for decorated functions)
        let i = global_interner();
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
            field_count: 0,
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
                field_count: 0,
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
        builder.add_nodes_batch(func_nodes);
        builder.add_edges_batch(edges);

        // Freeze to query via GraphQuery
        let graph = builder.freeze();
        use crate::graph::traits::{GraphQuery, GraphQueryExt};
        let gq: &dyn GraphQuery = &graph;

        // Decorated function "index" should have the file node as caller
        let callers = gq.get_callers("app.routes.index:5");
        assert_eq!(callers.len(), 1);
        assert_eq!(i.resolve(callers[0].name), "app/routes.py"); // File node is the caller
        assert_eq!(gq.call_fan_in("app.routes.index:5"), 1);

        // Undecorated function "helper" should have 0 callers
        let callers = gq.get_callers("app.routes.helper:10");
        assert!(callers.is_empty());
        assert_eq!(gq.call_fan_in("app.routes.helper:10"), 0);
    }

    #[test]
    fn test_estimate_graph_capacity_empty() {
        let results: Vec<(PathBuf, Arc<ParseResult>)> = vec![];
        let (nodes, edges) = estimate_graph_capacity(&results);
        assert_eq!(nodes, 0);
        assert_eq!(edges, 0);
    }

    #[test]
    fn test_estimate_graph_capacity_realistic() {
        use crate::parsers::ImportInfo;

        let results = vec![(
            PathBuf::from("app/main.py"),
            Arc::new(ParseResult {
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
                trait_impls: vec![],
                raw_values: None,
            }),
        )];

        let (nodes, edges) = estimate_graph_capacity(&results);
        // 1 file + 2 functions = 3 nodes
        assert_eq!(nodes, 3);
        // 2 contains (functions) + 1 call + 1 import + 3 (safety margin = nodes) = 7
        assert_eq!(edges, 7);
    }
}
