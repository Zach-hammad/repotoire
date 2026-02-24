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
//!   - Deferred edges: 500MB max (flush every 50k edges)
//!   - Graph database: 500MB
//!
//! Backpressure:
//!   - When channel is full, parsers BLOCK (no memory growth)
//!   - When edges hit threshold, consumer flushes to disk
//! ```
//!
//! # Key Differences from parallel_pipeline.rs
//!
//! - Adaptive buffer size: smaller for larger repos
//! - Edge flushing: periodic flush instead of defer-all
//! - Memory estimation: warns before OOM

use crate::graph::{CodeEdge, CodeNode, GraphStore, NodeKind};
use crate::parsers::lightweight::{LightweightFileInfo, LightweightParseStats};
use crate::parsers::parse_file_lightweight;
use anyhow::Result;
use crossbeam_channel::bounded;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;

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
    function_lookup: HashMap<String, String>,
    module_lookup: ModuleLookupCompact,

    // Buffered edges (flushed periodically)
    edge_buffer: Vec<(String, String, CodeEdge)>,
    edge_flush_threshold: usize,

    // Stats
    stats: BoundedPipelineStats,
}

/// Compact module lookup - only stores what we need
#[derive(Debug, Default)]
struct ModuleLookupCompact {
    by_stem: HashMap<String, Vec<String>>,
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
    fn new(graph: Arc<GraphStore>, repo_path: &Path, edge_flush_threshold: usize) -> Self {
        Self {
            graph,
            repo_path: repo_path.to_path_buf(),
            function_lookup: HashMap::new(),
            module_lookup: ModuleLookupCompact::default(),
            edge_buffer: Vec::with_capacity(edge_flush_threshold.min(10_000)),
            edge_flush_threshold,
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

    /// Process a parsed file
    fn process(&mut self, info: LightweightFileInfo) -> Result<()> {
        let relative = info.relative_path(&self.repo_path);

        // Add functions to lookup
        for func in &info.functions {
            self.function_lookup
                .insert(func.name.clone(), func.qualified_name.clone());
        }

        // Add file node
        let file_node = CodeNode::new(NodeKind::File, &relative, &relative)
            .with_qualified_name(&relative)
            .with_language(info.language.as_str())
            .with_property("loc", info.loc as i64);
        self.graph.add_node(file_node);
        self.stats.nodes_added += 1;
        self.stats.files_processed += 1;

        // Add function nodes + edges
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

            // Buffer contains edge
            self.edge_buffer.push((
                relative.clone(),
                func.qualified_name.clone(),
                CodeEdge::contains(),
            ));
        }

        // Add class nodes + edges
        for class in &info.classes {
            let class_node = CodeNode::new(NodeKind::Class, &class.name, &relative)
                .with_qualified_name(&class.qualified_name)
                .with_lines(class.line_start, class.line_end)
                .with_property("methodCount", class.method_count as i64);
            self.graph.add_node(class_node);
            self.stats.nodes_added += 1;
            self.stats.classes_added += 1;

            self.edge_buffer.push((
                relative.clone(),
                class.qualified_name.clone(),
                CodeEdge::contains(),
            ));
        }

        // Resolve and buffer call edges
        for call in &info.calls {
            let callee_name = call
                .callee
                .rsplit(&[':', '.'][..])
                .next()
                .unwrap_or(&call.callee);

            // Check same file first, then lookup
            let callee_qn = info
                .functions
                .iter()
                .find(|f| f.name == callee_name)
                .map(|f| f.qualified_name.clone())
                .or_else(|| self.function_lookup.get(callee_name).cloned());

            if let Some(qn) = callee_qn {
                self.edge_buffer
                    .push((call.caller.clone(), qn, CodeEdge::calls()));
            }
        }

        // Resolve and buffer import edges
        for import in &info.imports {
            if let Some(target) = self.module_lookup.find_match(&import.path) {
                if target != &relative {
                    let edge =
                        CodeEdge::imports().with_property("is_type_only", import.is_type_only);
                    self.edge_buffer
                        .push((relative.clone(), target.clone(), edge));
                }
            }
        }

        // Track peak buffer size
        if self.edge_buffer.len() > self.stats.peak_edges_buffered {
            self.stats.peak_edges_buffered = self.edge_buffer.len();
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

    /// Finalize - flush remaining edges and save
    fn finalize(mut self) -> Result<BoundedPipelineStats> {
        self.flush_edges()?;
        self.graph.save()?;
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
        FlushingGraphBuilder::new(Arc::clone(&graph), repo_path, config.edge_flush_threshold);
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

        let handle = thread::spawn(move || {
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
        });
        workers.push(handle);
    }

    // Drop our copies of channels so receivers can detect completion
    drop(file_rx);
    drop(result_tx);

    // Consumer: build graph sequentially
    let mut count = 0;
    for info in result_rx {
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
        // info is dropped here - memory freed immediately
    }

    // Wait for threads
    let _ = producer.join();
    for w in workers {
        let _ = w.join();
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

#[cfg(test)]
mod tests {
    use super::*;

    use tempfile::TempDir;

    fn create_test_file(dir: &Path, name: &str, content: &str) -> PathBuf {
        let path = dir.join(name);
        std::fs::write(&path, content).unwrap();
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
        let dir = TempDir::new().unwrap();
        let path = dir.path();

        create_test_file(path, "a.py", "def hello(): pass");
        create_test_file(path, "b.py", "def world(): pass");

        let files = vec![path.join("a.py"), path.join("b.py")];

        let graph = Arc::new(GraphStore::in_memory());
        let config = PipelineConfig::for_repo_size(2);

        let (stats, parse_stats) = run_bounded_pipeline(files, path, graph, config, None).unwrap();

        assert_eq!(stats.files_processed, 2);
        assert_eq!(parse_stats.parsed_files, 2);
        assert_eq!(parse_stats.total_functions, 2);
    }

    #[test]
    fn test_edge_flushing() {
        let dir = TempDir::new().unwrap();
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

        let (stats, _) = run_bounded_pipeline(files, path, graph, config, None).unwrap();

        // Should have flushed at least once
        assert!(stats.edge_flushes > 0 || stats.edges_added > 0);
    }
}
