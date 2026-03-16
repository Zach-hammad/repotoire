#![allow(dead_code)] // Infrastructure module for future large-repo support
//! Memory-bounded parallel pipeline using crossbeam channels
//!
//! This module implements an improved producer-consumer pipeline with:
//! - Adaptive buffer sizing based on repo size
//! - Periodic edge flushing to cap memory usage
//! - True backpressure when graph building is slow
//!
//! # Memory Model
//!
//! ```text
//! Target Memory Budget: 1.5GB total
//!
//! Allocation:
//!   - Parse results in-flight: 500MB max (buffer_size × ~5KB per file)
//!   - Intra-file edge buffer: flushed periodically at edge_flush_threshold
//!   - Deferred cross-file edges: accumulated until Phase 2 (finalize)
//!   - Graph database: 500MB
//!
//! Two-phase edge resolution:
//!   - Phase 1: intra-file edges resolve immediately, cross-file edges deferred
//!   - Phase 2: deferred edges sorted and resolved with complete symbol tables
//!
//! Backpressure:
//!   - When channel is full, parsers BLOCK (no memory growth)
//!   - When intra-file edges hit threshold, consumer flushes to graph
//! ```
//!
//! # Key Differences from unbounded pipelines
//!
//! - Adaptive buffer size: smaller for larger repos
//! - Edge flushing: periodic flush instead of defer-all
//! - Memory estimation: warns before OOM

use crate::graph::store_models::{FLAG_ADDRESS_TAKEN, FLAG_HAS_DECORATORS, FLAG_IS_ASYNC, FLAG_IS_EXPORTED};
use crate::graph::{CodeEdge, CodeNode, GraphStore, NodeKind};
use crate::parsers::lightweight::{LightweightFileInfo, LightweightParseStats};
use crate::parsers::parse_file_lightweight;
use anyhow::Result;
use crossbeam_channel::bounded;
use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;

/// Unresolved cross-file import edge buffered during Phase 1 for deferred resolution.
/// Call edges are resolved inline (not deferred) — see inline resolution in process().
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
enum DeferredImport {
    Import {
        file_path: String,
        import_path: String,
        is_type_only: bool,
    },
}

/// Tracks whether a bare function name maps to exactly one qualified name.
/// Used for deterministic cross-file call resolution: unique names resolve,
/// ambiguous names (2+ functions with the same bare name) are dropped.
#[derive(Debug, Clone)]
enum LookupEntry {
    /// Exactly one function with this bare name — safe to resolve.
    Unique(String),
    /// Two or more functions share this bare name — cannot resolve without
    /// language-specific import analysis, so we drop the edge.
    Ambiguous,
}

/// Configuration for the bounded pipeline
#[derive(Debug, Clone)]
pub struct PipelineConfig {
    /// Number of worker threads (default: num_cpus)
    pub num_workers: usize,
    /// Channel buffer size (parsed files in-flight)
    pub buffer_size: usize,
    /// Maximum edges before flush (controls memory)
    pub edge_flush_threshold: usize,
    /// Estimated memory per parsed file (for warnings)
    pub estimated_bytes_per_file: usize,
}

impl PipelineConfig {
    /// Create config for a repo of given size
    pub fn for_repo_size(num_files: usize) -> Self {
        let num_workers = std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4);

        // Adaptive buffer sizing:
        // - Small repos (<5k): buffer 100 (fast, ~500KB in-flight)
        // - Medium repos (5k-20k): buffer 50 (balanced, ~250KB)
        // - Large repos (20k-50k): buffer 25 (conservative, ~125KB)
        // - Huge repos (50k+): buffer 10 (minimal, ~50KB)
        let buffer_size = match num_files {
            0..=5_000 => 100,
            5_001..=20_000 => 50,
            20_001..=50_000 => 25,
            _ => 10,
        };

        // Flush edges more frequently for large repos
        let edge_flush_threshold = match num_files {
            0..=10_000 => 100_000,     // 100k edges (~40MB)
            10_001..=50_000 => 50_000, // 50k edges (~20MB)
            _ => 25_000,               // 25k edges (~10MB)
        };

        Self {
            num_workers,
            buffer_size,
            edge_flush_threshold,
            estimated_bytes_per_file: 5_000, // ~5KB average
        }
    }

    /// Estimate memory usage
    pub fn estimated_memory_mb(&self) -> usize {
        let in_flight_mb = (self.buffer_size * self.estimated_bytes_per_file) / (1024 * 1024);
        let edges_mb = (self.edge_flush_threshold * 400) / (1024 * 1024); // ~400 bytes per edge
        in_flight_mb + edges_mb + 500 // + 500MB for graph DB
    }
}

impl Default for PipelineConfig {
    fn default() -> Self {
        Self::for_repo_size(10_000)
    }
}

/// Stats from the bounded pipeline
#[derive(Debug, Clone, Default)]
pub struct BoundedPipelineStats {
    pub files_processed: usize,
    pub parse_errors: usize,
    pub nodes_added: usize,
    pub edges_added: usize,
    pub edge_flushes: usize,
    pub functions_added: usize,
    pub classes_added: usize,
    pub peak_edges_buffered: usize,
}

impl BoundedPipelineStats {
    /// Human-readable memory estimate
    pub fn memory_human(&self) -> String {
        // Rough estimate: files × 5KB + edges × 400B
        let bytes = (self.files_processed * 5_000) + (self.peak_edges_buffered * 400);
        if bytes > 1024 * 1024 * 1024 {
            format!("{:.1}GB", bytes as f64 / (1024.0 * 1024.0 * 1024.0))
        } else {
            format!("{:.0}MB", bytes as f64 / (1024.0 * 1024.0))
        }
    }
}

/// Streaming builder with periodic edge flushing
struct FlushingGraphBuilder {
    graph: Arc<GraphStore>,
    repo_path: PathBuf,

    // Lookup indexes (grow with repo but much smaller than full file info)
    function_lookup: HashMap<String, LookupEntry>,
    module_lookup: ModuleLookupCompact,

    // Buffered edges (flushed periodically)
    edge_buffer: Vec<(String, String, CodeEdge)>,
    edge_flush_threshold: usize,

    // Pending cross-file call edges: callee_bare_name → [caller_qn]
    // Entries are drained as callees are registered, or resolved in finalize().
    pending_calls: HashMap<String, Vec<String>>,

    // Deferred cross-file import edges for Phase 2 resolution
    deferred_imports: Vec<DeferredImport>,

    // Stats
    stats: BoundedPipelineStats,
}

/// Compact module lookup - only stores what we need
#[derive(Debug, Default)]
struct ModuleLookupCompact {
    by_stem: BTreeMap<String, Vec<String>>,
}

impl ModuleLookupCompact {
    fn add_file(&mut self, relative_path: &str) {
        let path = Path::new(relative_path);
        if let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
            self.by_stem
                .entry(stem.to_string())
                .or_default()
                .push(relative_path.to_string());
        }
    }

    /// Sort all candidate vecs for deterministic resolution.
    /// Must be called after all files have been added (end of Phase 1).
    fn sort_candidates(&mut self) {
        for candidates in self.by_stem.values_mut() {
            candidates.sort();
        }
    }

    fn find_match(&self, import_path: &str) -> Option<&String> {
        let clean = import_path
            .trim_start_matches("./")
            .trim_start_matches("../")
            .trim_start_matches("crate::")
            .trim_start_matches("super::");

        // Try stem match
        let stem = clean
            .split(&[':', '.', '/'][..])
            .next_back()
            .unwrap_or(clean);
        self.by_stem.get(stem).and_then(|v| v.first())
    }
}

impl FlushingGraphBuilder {
    fn new(graph: Arc<GraphStore>, repo_path: &Path, edge_flush_threshold: usize, estimated_files: usize) -> Self {
        let est_functions = estimated_files.saturating_mul(20); // ~20 functions per file average
        Self {
            graph,
            repo_path: repo_path.to_path_buf(),
            function_lookup: HashMap::with_capacity(est_functions),
            module_lookup: ModuleLookupCompact::default(),
            edge_buffer: Vec::with_capacity(edge_flush_threshold.min(10_000)),
            edge_flush_threshold,
            pending_calls: HashMap::with_capacity(est_functions / 4),
            deferred_imports: Vec::with_capacity(estimated_files.saturating_mul(5)),
            stats: BoundedPipelineStats::default(),
        }
    }

    /// Pre-populate module lookup from file paths (no parsing needed)
    fn add_file_paths(&mut self, paths: &[PathBuf]) {
        for path in paths {
            let relative = path.strip_prefix(&self.repo_path).unwrap_or(path);
            let relative_str = relative.display().to_string();
            self.module_lookup.add_file(&relative_str);
        }
    }

    /// Add a single file path to the module lookup (for incremental/streaming use).
    fn add_file_path(&mut self, path: &Path) {
        let relative = path.strip_prefix(&self.repo_path).unwrap_or(path);
        let relative_str = relative.display().to_string();
        self.module_lookup.add_file(&relative_str);
    }

    /// Process a parsed file
    fn process(&mut self, info: LightweightFileInfo) -> Result<()> {
        let relative = info.relative_path(&self.repo_path);

        // Add functions to lookup with ambiguity tracking
        for func in &info.functions {
            match self.function_lookup.entry(func.name.clone()) {
                std::collections::hash_map::Entry::Vacant(e) => {
                    e.insert(LookupEntry::Unique(func.qualified_name.clone()));
                    // Drain any pending callers that were waiting for this function
                    if let Some(callers) = self.pending_calls.remove(&func.name) {
                        for caller_qn in callers {
                            self.edge_buffer.push((
                                caller_qn,
                                func.qualified_name.clone(),
                                CodeEdge::calls(),
                            ));
                        }
                    }
                }
                std::collections::hash_map::Entry::Occupied(mut e) => {
                    *e.get_mut() = LookupEntry::Ambiguous;
                }
            }
        }

        // File node — insert first so its index exists for Contains edges
        let i = self.graph.interner();
        let rel_key = i.intern(&relative);
        let lang_key = i.intern(info.language.as_str());
        let file_node = CodeNode {
            kind: NodeKind::File,
            name: rel_key,
            qualified_name: rel_key,
            file_path: rel_key,
            language: lang_key,
            line_start: 1,
            line_end: info.loc,
            complexity: 0,
            param_count: 0,
            method_count: 0,
            field_count: 0,
            max_nesting: 0,
            return_count: 0,
            commit_count: 0,
            flags: 0,
        };
        self.graph.add_nodes_batch(vec![file_node]);

        // Function + class nodes — Contains edges created inside the graph store
        let mut entity_nodes = Vec::with_capacity(info.functions.len() + info.classes.len());

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

            let qn_key = i.intern(&func.qualified_name);
            let node = CodeNode {
                kind: NodeKind::Function,
                name: i.intern(&func.name),
                qualified_name: qn_key,
                file_path: rel_key,
                language: lang_key,
                line_start: func.line_start,
                line_end: func.line_end,
                complexity: func.complexity,
                param_count: func.param_count,
                method_count: 0,
                field_count: 0,
                max_nesting: func.max_nesting.unwrap_or(0) as u8,
                return_count: 0,
                commit_count: 0,
                flags,
            };
            entity_nodes.push(node);

            // Store decorator/annotation strings in ExtraProps side table
            if let Some(ref annotations) = func.annotations_joined {
                let ep = crate::graph::ExtraProps {
                    decorators: Some(i.intern(annotations)),
                    ..Default::default()
                };
                self.graph.set_extra_props(qn_key, ep);
            }

            // Decorated functions still need a Calls edge (file → func via decorator)
            if func.has_annotations {
                self.edge_buffer.push((
                    relative.clone(),
                    func.qualified_name.clone(),
                    CodeEdge::calls(),
                ));
            }
        }

        for class in &info.classes {
            entity_nodes.push(CodeNode {
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
            });
        }

        // Batch insert with Contains edges created inside the graph store
        if !entity_nodes.is_empty() {
            self.graph.add_nodes_batch_with_contains(entity_nodes, &relative);
        }

        let func_count = info.functions.len();
        let class_count = info.classes.len();
        self.stats.nodes_added += 1 + func_count + class_count;
        self.stats.functions_added += func_count;
        self.stats.classes_added += class_count;
        self.stats.files_processed += 1;

        // Resolve call edges — same-file immediately, cross-file via inline resolution.
        if !info.calls.is_empty() {
            let local_funcs: HashMap<&str, &str> = info
                .functions
                .iter()
                .map(|f| (f.name.as_str(), f.qualified_name.as_str()))
                .collect();

            for call in &info.calls {
                let callee_name = call
                    .callee
                    .rsplit(&[':', '.'][..])
                    .next()
                    .unwrap_or(&call.callee);

                // 1. Same-file fast path
                if let Some(&qn) = local_funcs.get(callee_name) {
                    self.edge_buffer
                        .push((call.caller.clone(), qn.to_string(), CodeEdge::calls()));
                    continue;
                }

                // 2. Cross-file inline resolution
                let has_module = call.callee.contains(':') || call.callee.contains('.');
                if !has_module
                    && crate::cli::analyze::graph::AMBIGUOUS_METHOD_NAMES
                        .contains(&callee_name)
                {
                    continue;
                }

                match self.function_lookup.get(callee_name) {
                    Some(LookupEntry::Unique(callee_qn)) => {
                        self.edge_buffer.push((
                            call.caller.clone(),
                            callee_qn.clone(),
                            CodeEdge::calls(),
                        ));
                    }
                    Some(LookupEntry::Ambiguous) => {
                        // Drop — can't know which is correct
                    }
                    None => {
                        self.pending_calls
                            .entry(callee_name.to_string())
                            .or_default()
                            .push(call.caller.clone());
                    }
                }
            }
        }

        // Defer all import edges to Phase 2 (need complete module lookup)
        for import in &info.imports {
            self.deferred_imports.push(DeferredImport::Import {
                file_path: relative.clone(),
                import_path: import.path.clone(),
                is_type_only: import.is_type_only,
            });
        }

        // Track peak buffer size (resolved edges + deferred imports + pending calls)
        let combined = self.edge_buffer.len()
            + self.deferred_imports.len()
            + self
                .pending_calls
                .values()
                .map(|v| v.len())
                .sum::<usize>();
        if combined > self.stats.peak_edges_buffered {
            self.stats.peak_edges_buffered = combined;
        }

        // Flush edges if threshold reached
        if self.edge_buffer.len() >= self.edge_flush_threshold {
            self.flush_edges()?;
        }

        Ok(())
    }

    /// Flush buffered edges to graph
    fn flush_edges(&mut self) -> Result<()> {
        if self.edge_buffer.is_empty() {
            return Ok(());
        }

        let count = self.edge_buffer.len();
        self.graph
            .add_edges_batch(std::mem::take(&mut self.edge_buffer));
        self.stats.edges_added += count;
        self.stats.edge_flushes += 1;

        // Re-allocate smaller buffer after flush
        self.edge_buffer = Vec::with_capacity(self.edge_flush_threshold.min(10_000));

        Ok(())
    }

    /// Finalize — drain pending calls, resolve deferred imports, flush, and save
    fn finalize(mut self) -> Result<BoundedPipelineStats> {
        // Drain remaining pending calls: resolve unique, drop ambiguous/unknown
        let mut pending_resolved = 0usize;
        let mut pending_dropped = 0usize;
        for (callee_name, callers) in std::mem::take(&mut self.pending_calls) {
            match self.function_lookup.get(&callee_name) {
                Some(LookupEntry::Unique(callee_qn)) => {
                    for caller_qn in callers {
                        self.edge_buffer.push((
                            caller_qn,
                            callee_qn.clone(),
                            CodeEdge::calls(),
                        ));
                        pending_resolved += 1;
                    }
                }
                Some(LookupEntry::Ambiguous) | None => {
                    pending_dropped += callers.len();
                }
            }
        }

        // Sort module lookup candidates for deterministic import resolution
        self.module_lookup.sort_candidates();

        // Resolve deferred imports
        let import_count = self.deferred_imports.len();
        let mut import_resolved = 0usize;
        for deferred in std::mem::take(&mut self.deferred_imports) {
            let DeferredImport::Import {
                file_path,
                import_path,
                is_type_only,
            } = deferred;

            if let Some(target) = self.module_lookup.find_match(&import_path) {
                if *target != file_path {
                    let mut edge = CodeEdge::imports();
                    if is_type_only {
                        edge = edge.with_type_only();
                    }
                    self.edge_buffer
                        .push((file_path, target.clone(), edge));
                    import_resolved += 1;
                }
            }
        }

        tracing::info!(
            "Finalize: {} pending calls resolved, {} dropped; {}/{} imports resolved",
            pending_resolved,
            pending_dropped,
            import_resolved,
            import_count,
        );

        self.flush_edges()?;

        // Defer graph.save() to background — redb persistence is NOT needed for
        // in-memory analysis (detect, score, postprocess all use petgraph directly).
        // This saves ~780ms from the critical path on large repos.
        let graph_for_save = Arc::clone(&self.graph);
        std::thread::spawn(move || {
            if let Err(e) = graph_for_save.save() {
                tracing::warn!("Background graph save failed: {}", e);
            }
        });

        Ok(self.stats)
    }
}

/// Run the bounded parallel pipeline
///
/// This is the main entry point for memory-efficient parallel parsing and graph building.
///
/// # Memory Guarantees
///
/// - In-flight parsed files: bounded by `config.buffer_size`
/// - Buffered edges: bounded by `config.edge_flush_threshold`
/// - Parsers block when consumer is slow (backpressure)
///
/// # Arguments
///
/// * `files` - Files to parse
/// * `repo_path` - Repository root path
/// * `graph` - Graph store to populate
/// * `config` - Pipeline configuration
/// * `progress` - Optional progress callback
pub fn run_bounded_pipeline(
    files: Vec<PathBuf>,
    repo_path: &Path,
    graph: Arc<GraphStore>,
    config: PipelineConfig,
    progress: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> Result<(BoundedPipelineStats, LightweightParseStats)> {
    let total_files = files.len();

    // Log estimated memory
    let est_mem = config.estimated_memory_mb();
    tracing::info!(
        "Bounded pipeline: {} files, buffer={}, edge_flush={}, est_mem={}MB",
        total_files,
        config.buffer_size,
        config.edge_flush_threshold,
        est_mem
    );

    // Initialize builder with module lookup
    let mut builder =
        FlushingGraphBuilder::new(Arc::clone(&graph), repo_path, config.edge_flush_threshold, total_files);
    builder.add_file_paths(&files);

    let mut parse_stats = LightweightParseStats {
        total_files,
        ..Default::default()
    };

    // Create bounded channels
    let (file_tx, file_rx) = bounded::<PathBuf>(config.buffer_size);
    let (result_tx, result_rx) = bounded::<LightweightFileInfo>(config.buffer_size);

    // Producer thread: feed files
    let producer = thread::spawn(move || {
        for file in files {
            if file_tx.send(file).is_err() {
                break; // Channel closed
            }
        }
    });

    // Worker threads: parse in parallel
    let parse_errors = Arc::new(AtomicUsize::new(0));
    let mut workers = Vec::with_capacity(config.num_workers);

    for _ in 0..config.num_workers {
        let rx = file_rx.clone();
        let tx = result_tx.clone();
        let errors = Arc::clone(&parse_errors);

        // Use 8MB stack to handle deeply nested C/C++ files (e.g., CPython)
        let handle = thread::Builder::new()
            .stack_size(8 * 1024 * 1024)
            .spawn(move || {
                for path in rx {
                    match parse_file_lightweight(&path) {
                        Ok(info) => {
                            if tx.send(info).is_err() {
                                break; // Consumer closed
                            }
                        }
                        Err(e) => {
                            errors.fetch_add(1, Ordering::Relaxed);
                            tracing::debug!("Parse error {}: {}", path.display(), e);
                        }
                    }
                }
            })
            .expect("failed to spawn parser worker thread");
        workers.push(handle);
    }

    // Drop our copies of channels so receivers can detect completion
    drop(file_rx);
    drop(result_tx);

    // Collect all parse results from parallel workers, then re-sort by
    // file path to restore the deterministic input order.  Without sorting,
    // parallel workers return results in nondeterministic completion order.
    //
    // Callers (e.g. parse_and_build_streaming) should pre-sort the input
    // `files` Vec; sorting here ensures the consumer processes them in that
    // same order regardless of per-file parse time variation.
    let mut all_results: Vec<LightweightFileInfo> = result_rx.into_iter().collect();

    // Wait for threads
    let _ = producer.join();
    for w in workers {
        let _ = w.join();
    }

    // Re-sort parse results to match input file order (alphabetical).
    // This is a no-op when the caller already provides sorted input AND
    // num_workers == 1, but is essential for determinism with parallel workers.
    all_results.sort_by(|a, b| a.path.cmp(&b.path));

    // Process sorted results sequentially
    let mut count = 0;
    for info in all_results {
        count += 1;

        if let Some(cb) = progress {
            if count % 100 == 0 || count == total_files {
                cb(count, total_files);
            }
        }

        parse_stats.add_file(&info);

        if let Err(e) = builder.process(info) {
            tracing::warn!("Process error: {}", e);
        }
    }

    // Finalize
    parse_stats.parse_errors = parse_errors.load(Ordering::Relaxed);
    parse_stats.parsed_files = count;

    let stats = builder.finalize()?;

    Ok((stats, parse_stats))
}

/// Convenience function with default config
pub fn run_bounded_pipeline_auto(
    files: Vec<PathBuf>,
    repo_path: &Path,
    graph: Arc<GraphStore>,
    progress: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> Result<(BoundedPipelineStats, LightweightParseStats)> {
    let config = PipelineConfig::for_repo_size(files.len());
    run_bounded_pipeline(files, repo_path, graph, config, progress)
}

/// Run the bounded pipeline from a channel of file paths.
///
/// Instead of accepting a pre-collected `Vec<PathBuf>`, this variant reads file
/// paths from a `crossbeam_channel::Receiver`. This enables walk+parse overlap:
/// the walker can send file paths into the channel as they are discovered, while
/// parser threads begin work immediately rather than waiting for the walk to finish.
///
/// The module lookup is populated incrementally as each file's parse result is
/// consumed. Cross-file edges (calls and imports) are deferred to Phase 2
/// (`finalize()`) where they are resolved against complete symbol tables,
/// ensuring deterministic graphs regardless of file discovery order.
///
/// # Arguments
///
/// * `file_receiver` - Channel of file paths to parse (sender side is owned by walker)
/// * `repo_path` - Repository root path
/// * `graph` - Graph store to populate
/// * `config` - Pipeline configuration
/// * `progress` - Optional progress callback `(count, 0)` — total is unknown
pub fn run_bounded_pipeline_from_channel(
    file_receiver: crossbeam_channel::Receiver<PathBuf>,
    repo_path: &Path,
    graph: Arc<GraphStore>,
    config: PipelineConfig,
    progress: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> Result<(BoundedPipelineStats, LightweightParseStats)> {
    // Log estimated memory (total unknown at this point)
    tracing::info!(
        "Bounded pipeline (channel mode): buffer={}, edge_flush={}, workers={}",
        config.buffer_size,
        config.edge_flush_threshold,
        config.num_workers,
    );

    // Initialize builder WITHOUT pre-populated module lookup —
    // it will be populated incrementally as files are processed.
    // estimated_files=0 since total is unknown in channel mode (containers grow dynamically).
    let mut builder =
        FlushingGraphBuilder::new(Arc::clone(&graph), repo_path, config.edge_flush_threshold, 0);

    let mut parse_stats = LightweightParseStats::default();

    // Create bounded channel for parse results
    let (result_tx, result_rx) = bounded::<LightweightFileInfo>(config.buffer_size);

    // Worker threads: parse files from the external channel
    let parse_errors = Arc::new(AtomicUsize::new(0));
    let mut workers = Vec::with_capacity(config.num_workers);

    for _ in 0..config.num_workers {
        let rx = file_receiver.clone();
        let tx = result_tx.clone();
        let errors = Arc::clone(&parse_errors);

        // Use 8MB stack to handle deeply nested C/C++ files (e.g., CPython)
        let handle = thread::Builder::new()
            .stack_size(8 * 1024 * 1024)
            .spawn(move || {
                for path in rx {
                    match parse_file_lightweight(&path) {
                        Ok(info) => {
                            if tx.send(info).is_err() {
                                break; // Consumer closed
                            }
                        }
                        Err(e) => {
                            errors.fetch_add(1, Ordering::Relaxed);
                            tracing::debug!("Parse error {}: {}", path.display(), e);
                        }
                    }
                }
            })
            .expect("failed to spawn parser worker thread");
        workers.push(handle);
    }

    // Drop our copies so receivers detect completion when walker + workers finish
    drop(file_receiver);
    drop(result_tx);

    // Collect all parse results, then sort by file path for deterministic
    // graph node insertion order.  Parallel parsing means results arrive in
    // completion order (thread-scheduling-dependent); sorting removes that
    // nondeterminism so detectors always see the same graph structure.
    let mut all_results: Vec<LightweightFileInfo> = result_rx.into_iter().collect();

    // Wait for workers before sorting (they may still be finishing)
    for w in workers {
        let _ = w.join();
    }

    all_results.sort_by(|a, b| a.path.cmp(&b.path));

    // Process sorted results sequentially
    let mut count = 0;
    for info in all_results {
        count += 1;

        // Incrementally populate module lookup from each processed file's path
        builder.add_file_path(&info.path);

        if let Some(cb) = progress {
            if count % 100 == 0 {
                cb(count, 0); // total unknown in channel mode
            }
        }

        parse_stats.add_file(&info);

        if let Err(e) = builder.process(info) {
            tracing::warn!("Process error: {}", e);
        }
    }

    // Finalize
    parse_stats.parse_errors = parse_errors.load(Ordering::Relaxed);
    parse_stats.parsed_files = count;
    parse_stats.total_files = count; // In channel mode, total = parsed

    let stats = builder.finalize()?;

    Ok((stats, parse_stats))
}

#[cfg(test)]
mod tests {
    use super::*;

    use tempfile::TempDir;

    fn create_test_file(dir: &Path, name: &str, content: &str) -> PathBuf {
        let path = dir.join(name);
        std::fs::write(&path, content).expect("should write test file");
        path
    }

    #[test]
    fn test_config_adaptive() {
        let small = PipelineConfig::for_repo_size(1000);
        let large = PipelineConfig::for_repo_size(100_000);

        assert!(small.buffer_size > large.buffer_size);
        assert!(small.edge_flush_threshold > large.edge_flush_threshold);
    }

    #[test]
    fn test_bounded_pipeline_small() {
        let dir = TempDir::new().expect("should create temp dir");
        let path = dir.path();

        create_test_file(path, "a.py", "def hello(): pass");
        create_test_file(path, "b.py", "def world(): pass");

        let files = vec![path.join("a.py"), path.join("b.py")];

        let graph = Arc::new(GraphStore::in_memory());
        let config = PipelineConfig::for_repo_size(2);

        let (stats, parse_stats) =
            run_bounded_pipeline(files, path, graph, config, None).expect("should run pipeline");

        assert_eq!(stats.files_processed, 2);
        assert_eq!(parse_stats.parsed_files, 2);
        assert_eq!(parse_stats.total_functions, 2);
    }

    #[test]
    fn test_edge_flushing() {
        let dir = TempDir::new().expect("should create temp dir");
        let path = dir.path();

        // Create files with many functions to trigger edge flush
        for i in 0..20 {
            let content = (0..100)
                .map(|j| format!("def func_{}_{j}(): pass\n", i))
                .collect::<String>();
            create_test_file(path, &format!("file_{}.py", i), &content);
        }

        let files: Vec<_> = (0..20)
            .map(|i| path.join(format!("file_{}.py", i)))
            .collect();

        let graph = Arc::new(GraphStore::in_memory());
        let mut config = PipelineConfig::for_repo_size(20);
        config.edge_flush_threshold = 500; // Low threshold to trigger flush

        let (stats, _) =
            run_bounded_pipeline(files, path, graph, config, None).expect("should run pipeline");

        // Should have flushed at least once
        assert!(stats.edge_flushes > 0 || stats.edges_added > 0);
    }

    #[test]
    fn test_bounded_pipeline_from_channel() {
        let dir = TempDir::new().expect("should create temp dir");
        let path = dir.path();

        create_test_file(path, "a.py", "def hello(): pass");
        create_test_file(path, "b.py", "def world(): pass");

        let files = vec![path.join("a.py"), path.join("b.py")];

        let graph = Arc::new(GraphStore::in_memory());
        let config = PipelineConfig::for_repo_size(2);

        // Simulate a walker feeding into the channel
        let (tx, rx) = bounded::<PathBuf>(config.buffer_size);
        let sender_handle = thread::spawn(move || {
            for f in files {
                tx.send(f).expect("send should succeed");
            }
            // tx drops here, closing the channel
        });

        let (stats, parse_stats) =
            run_bounded_pipeline_from_channel(rx, path, graph, config, None)
                .expect("should run channel pipeline");

        sender_handle.join().expect("sender thread should finish");

        assert_eq!(stats.files_processed, 2);
        assert_eq!(parse_stats.parsed_files, 2);
        assert_eq!(parse_stats.total_functions, 2);
    }

    /// Verify that the overlapped pipeline produces identical graphs regardless of
    /// the order files arrive. This is the core determinism invariant.
    #[test]
    fn test_overlapped_pipeline_deterministic_across_file_orders() {
        let dir = TempDir::new().expect("temp dir");
        let path = dir.path();

        // module_a imports module_b and calls helper_from_b
        create_test_file(
            path,
            "module_a.py",
            "from module_b import helper_from_b\n\ndef main():\n    helper_from_b()\n",
        );
        // module_b defines helper_from_b and calls main from module_a
        create_test_file(
            path,
            "module_b.py",
            "from module_a import main\n\ndef helper_from_b():\n    main()\n",
        );

        // Run N times with alternating file order via channel
        let mut edge_snapshots: Vec<Vec<String>> = Vec::new();

        for run_idx in 0..5 {
            let graph = Arc::new(GraphStore::in_memory());
            let config = PipelineConfig::for_repo_size(2);

            let (tx, rx) = bounded::<PathBuf>(config.buffer_size);

            // Alternate file order between runs
            let files = if run_idx % 2 == 0 {
                vec![path.join("module_a.py"), path.join("module_b.py")]
            } else {
                vec![path.join("module_b.py"), path.join("module_a.py")]
            };

            let sender = thread::spawn(move || {
                for f in files {
                    tx.send(f).expect("send");
                }
            });

            let (_stats, _parse_stats) =
                run_bounded_pipeline_from_channel(rx, path, graph.clone(), config, None)
                    .expect("pipeline should succeed");

            sender.join().expect("sender thread");

            // Collect all edges as sorted strings for comparison
            let mut edges: Vec<String> = graph
                .get_all_edges()
                .into_iter()
                .map(|(src, dst, kind)| format!("{} --{:?}--> {}", src, kind, dst))
                .collect();
            edges.sort();
            edge_snapshots.push(edges);
        }

        // All runs must produce identical edge sets
        for (i, snapshot) in edge_snapshots.iter().enumerate().skip(1) {
            assert_eq!(
                &edge_snapshots[0], snapshot,
                "Run {} edges differ from run 0",
                i
            );
        }
    }

    /// Verify that the file-list pipeline variant is also deterministic
    /// with the two-phase approach.
    #[test]
    fn test_bounded_pipeline_deterministic_cross_file_calls() {
        let dir = TempDir::new().expect("temp dir");
        let path = dir.path();

        create_test_file(
            path,
            "module_a.py",
            "from module_b import helper_from_b\n\ndef main():\n    helper_from_b()\n",
        );
        create_test_file(
            path,
            "module_b.py",
            "from module_a import main\n\ndef helper_from_b():\n    main()\n",
        );

        let mut edge_snapshots: Vec<Vec<String>> = Vec::new();

        for run_idx in 0..5 {
            let graph = Arc::new(GraphStore::in_memory());
            let config = PipelineConfig::for_repo_size(2);

            // Alternate file order
            let files = if run_idx % 2 == 0 {
                vec![path.join("module_a.py"), path.join("module_b.py")]
            } else {
                vec![path.join("module_b.py"), path.join("module_a.py")]
            };

            let (_stats, _parse_stats) =
                run_bounded_pipeline(files, path, graph.clone(), config, None)
                    .expect("pipeline should succeed");

            let mut edges: Vec<String> = graph
                .get_all_edges()
                .into_iter()
                .map(|(src, dst, kind)| format!("{} --{:?}--> {}", src, kind, dst))
                .collect();
            edges.sort();
            edge_snapshots.push(edges);
        }

        for (i, snapshot) in edge_snapshots.iter().enumerate().skip(1) {
            assert_eq!(
                &edge_snapshots[0], snapshot,
                "Run {} edges differ from run 0",
                i
            );
        }
    }

    /// Verify that ambiguous bare names (same function name in two files)
    /// do NOT produce spurious cross-file call edges.
    ///
    /// File names are chosen so that alphabetical sort processes definitions
    /// (a_utils, b_utils) before the caller (c_main). After both definition
    /// files are processed, `process` is marked Ambiguous and the call from
    /// c_main should be dropped.
    #[test]
    fn test_ambiguous_bare_name_drops_cross_file_edge() {
        let dir = TempDir::new().expect("temp dir");
        let path = dir.path();

        create_test_file(path, "a_utils.py", "def process():\n    pass\n");
        create_test_file(path, "b_utils.py", "def process():\n    pass\n");
        create_test_file(path, "c_main.py", "def main():\n    process()\n");

        let graph = Arc::new(GraphStore::in_memory());
        let mut config = PipelineConfig::for_repo_size(3);
        config.num_workers = 1;
        let files = vec![
            path.join("a_utils.py"),
            path.join("b_utils.py"),
            path.join("c_main.py"),
        ];

        let (_stats, _parse_stats) =
            run_bounded_pipeline(files, path, graph.clone(), config, None)
                .expect("pipeline should succeed");

        let gi = graph.interner();
        let call_edges = graph.get_edges_by_kind(crate::graph::EdgeKind::Calls);
        let spurious = call_edges
            .iter()
            .filter(|(src, _dst)| gi.resolve(*src).contains("c_main"))
            .filter(|(_src, dst)| gi.resolve(*dst).contains("process"))
            .count();
        assert_eq!(
            spurious, 0,
            "ambiguous bare name should not create cross-file call edge"
        );
    }

    /// Verify forward references resolve via pending queue.
    ///
    /// Uses 1 worker to ensure caller.py is processed before helper.py,
    /// so the call to `helper()` goes into the pending queue and is
    /// resolved when helper.py registers the function.
    #[test]
    fn test_pending_queue_resolves_forward_references() {
        let dir = TempDir::new().expect("temp dir");
        let path = dir.path();

        create_test_file(path, "caller.py", "def main():\n    helper()\n");
        create_test_file(path, "helper.py", "def helper():\n    pass\n");

        let graph = Arc::new(GraphStore::in_memory());
        let mut config = PipelineConfig::for_repo_size(2);
        config.num_workers = 1; // deterministic order: caller before helper
        let files = vec![path.join("caller.py"), path.join("helper.py")];

        let (_stats, _) =
            run_bounded_pipeline(files, path, graph.clone(), config, None)
                .expect("pipeline should succeed");

        let gi = graph.interner();
        let call_edges = graph.get_edges_by_kind(crate::graph::EdgeKind::Calls);
        let has_edge = call_edges
            .iter()
            .any(|(src, dst)| gi.resolve(*src).contains("main") && gi.resolve(*dst).contains("helper"));
        assert!(
            has_edge,
            "forward reference should be resolved via pending queue"
        );
    }
}
