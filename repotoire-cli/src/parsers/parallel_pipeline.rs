//! Parallel parsing pipeline using crossbeam channels
//!
//! This module implements a producer-consumer pipeline that SEPARATES:
//! - Parsing (CPU-bound, parallelizable) - runs on N worker threads
//! - Graph building (stateful, sequential) - runs on consumer thread
//!
//! # Architecture
//!
//! ```text
//!                     ┌─────────────┐
//!                     │   Producer  │  Single thread feeds file paths
//!                     └──────┬──────┘
//!                            │ bounded channel (file_tx → file_rx)
//!            ┌───────────────┼───────────────┐
//!            ▼               ▼               ▼
//!     ┌──────────┐    ┌──────────┐    ┌──────────┐
//!     │ Worker 1 │    │ Worker 2 │    │ Worker N │  N threads parse in parallel
//!     └────┬─────┘    └────┬─────┘    └────┬─────┘
//!          │               │               │
//!          └───────────────┼───────────────┘
//!                          │ bounded channel (result_tx → result_rx)
//!                          ▼
//!                   ┌──────────────┐
//!                   │   Consumer   │  Single thread builds graph sequentially
//!                   └──────────────┘
//! ```
//!
//! # Benefits
//!
//! - **Parallel parsing**: Uses all CPU cores for tree-sitter parsing
//! - **Bounded memory**: Channel capacities limit in-flight items
//! - **No contention**: Graph building is sequential, no locks needed
//! - **Backpressure**: Fast parser waits when consumer is slow

use crate::parsers::lightweight::{LightweightFileInfo, LightweightParseStats};
use crate::parsers::parse_file_lightweight;
use crossbeam_channel::{bounded, Receiver};
use std::path::PathBuf;

use std::thread;

/// Result from the parallel pipeline
pub struct ParallelPipelineResult {
    /// Receiver for parsed file info
    receiver: Option<Receiver<LightweightFileInfo>>,
    /// Total files to process
    pub total_files: usize,
    /// Handle to join producer thread
    producer_handle: Option<thread::JoinHandle<()>>,
    /// Handles to join worker threads
    worker_handles: Vec<thread::JoinHandle<WorkerStats>>,
}

impl ParallelPipelineResult {
    /// Take the receiver - can only be called once
    pub fn take_receiver(&mut self) -> Option<Receiver<LightweightFileInfo>> {
        self.receiver.take()
    }

    /// Iterate over received items
    pub fn iter(&mut self) -> impl Iterator<Item = LightweightFileInfo> + '_ {
        self.receiver.take().into_iter().flatten()
    }
}

/// Stats from a single worker
#[derive(Debug, Default)]
pub struct WorkerStats {
    pub parsed: usize,
    pub errors: usize,
}

/// Combined stats from the pipeline
#[derive(Debug, Default)]
pub struct PipelineStats {
    pub total_files: usize,
    pub parsed_files: usize,
    pub parse_errors: usize,
    pub total_functions: usize,
    pub total_classes: usize,
}

impl ParallelPipelineResult {
    /// Wait for all workers to finish and collect stats
    pub fn join(mut self) -> PipelineStats {
        // Wait for producer
        if let Some(h) = self.producer_handle.take() {
            let _ = h.join();
        }

        // Wait for workers and collect stats
        let mut stats = PipelineStats {
            total_files: self.total_files,
            ..Default::default()
        };

        for handle in self.worker_handles.drain(..) {
            if let Ok(worker_stats) = handle.join() {
                stats.parsed_files += worker_stats.parsed;
                stats.parse_errors += worker_stats.errors;
            }
        }

        stats
    }
}

/// Create a parallel parsing pipeline
///
/// Returns a receiver that yields `LightweightFileInfo` as files are parsed.
/// The parsing happens in parallel on `num_workers` threads, but the consumer
/// receives results sequentially.
///
/// # Arguments
///
/// * `files` - Files to parse
/// * `num_workers` - Number of parallel parser threads (typically `std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4)`)
/// * `buffer_size` - Channel buffer size (controls memory, typically 100-500)
///
/// # Example
///
/// ```ignore
/// let pipeline = parse_files_pipeline(files, std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4), 100);
/// for info in pipeline.receiver {
///     builder.process_file(&info)?;
/// }
/// let stats = pipeline.join();
/// ```
pub fn parse_files_pipeline(
    files: Vec<PathBuf>,
    num_workers: usize,
    buffer_size: usize,
) -> ParallelPipelineResult {
    let total_files = files.len();
    let num_workers = num_workers.max(1);

    // Channel for file paths (producer → workers)
    let (file_tx, file_rx) = bounded::<PathBuf>(buffer_size);

    // Channel for parsed results (workers → consumer)
    let (result_tx, result_rx) = bounded::<LightweightFileInfo>(buffer_size);

    // Producer thread: feeds files to workers
    let producer_handle = thread::spawn(move || {
        for file in files {
            // This blocks if channel is full (backpressure)
            if file_tx.send(file).is_err() {
                // Channel closed, workers finished early
                break;
            }
        }
        // Drop sender to signal we're done
        drop(file_tx);
    });

    // Worker threads: parse files in parallel
    let mut worker_handles = Vec::with_capacity(num_workers);

    for _ in 0..num_workers {
        let rx = file_rx.clone();
        let tx = result_tx.clone();

        let handle = thread::spawn(move || {
            let mut stats = WorkerStats::default();

            // Pull files from channel until it's closed
            for path in rx {
                match parse_file_lightweight(&path) {
                    Ok(info) => {
                        stats.parsed += 1;
                        // This blocks if result channel is full (backpressure)
                        if tx.send(info).is_err() {
                            // Consumer closed, stop processing
                            break;
                        }
                    }
                    Err(e) => {
                        stats.errors += 1;
                        tracing::warn!("Failed to parse {}: {}", path.display(), e);
                    }
                }
            }

            stats
        });

        worker_handles.push(handle);
    }

    // Drop our copy of file_rx so workers can detect when producer is done
    drop(file_rx);

    // Drop our copy of result_tx so consumer can detect when workers are done
    drop(result_tx);

    ParallelPipelineResult {
        receiver: Some(result_rx),
        total_files,
        producer_handle: Some(producer_handle),
        worker_handles,
    }
}

/// Parse files using parallel pipeline with progress callback
///
/// This is a convenience function that creates the pipeline and processes
/// results, calling the progress callback periodically.
pub fn parse_files_parallel_pipeline(
    files: Vec<PathBuf>,
    num_workers: usize,
    buffer_size: usize,
    progress: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> (Vec<LightweightFileInfo>, LightweightParseStats) {
    let total = files.len();
    let mut pipeline = parse_files_pipeline(files, num_workers, buffer_size);

    let mut results = Vec::with_capacity(total);
    let mut stats = LightweightParseStats {
        total_files: total,
        ..Default::default()
    };

    // Take receiver to iterate
    let receiver = pipeline.take_receiver().expect("receiver already taken");

    let mut count = 0;
    for info in receiver {
        count += 1;

        if let Some(cb) = progress {
            if count % 100 == 0 || count == total {
                cb(count, total);
            }
        }

        stats.add_file(&info);
        results.push(info);
    }

    // Wait for workers and get error count
    let pipeline_stats = pipeline.join();
    stats.parse_errors = pipeline_stats.parse_errors;
    stats.parsed_files = pipeline_stats.parsed_files;

    (results, stats)
}

/// Parse files and stream results through a callback
///
/// This is the most memory-efficient version - results are processed
/// immediately without collection.
///
/// # Arguments
///
/// * `files` - Files to parse
/// * `num_workers` - Number of parallel parser threads
/// * `buffer_size` - Channel buffer size
/// * `on_file` - Callback for each parsed file
/// * `progress` - Optional progress callback
pub fn stream_parse_parallel<F>(
    files: Vec<PathBuf>,
    num_workers: usize,
    buffer_size: usize,
    mut on_file: F,
    progress: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> LightweightParseStats
where
    F: FnMut(LightweightFileInfo),
{
    let total = files.len();
    let mut pipeline = parse_files_pipeline(files, num_workers, buffer_size);

    let mut stats = LightweightParseStats {
        total_files: total,
        ..Default::default()
    };

    // Take receiver to iterate
    let receiver = pipeline.take_receiver().expect("receiver already taken");

    let mut count = 0;
    for info in receiver {
        count += 1;

        if let Some(cb) = progress {
            if count % 100 == 0 || count == total {
                cb(count, total);
            }
        }

        stats.add_file(&info);
        on_file(info);
    }

    // Wait for workers and get error count
    let pipeline_stats = pipeline.join();
    stats.parse_errors = pipeline_stats.parse_errors;
    stats.parsed_files = pipeline_stats.parsed_files;

    stats
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    #[test]
    fn test_pipeline_single_file() {
        let mut file = NamedTempFile::with_suffix(".py").unwrap();
        writeln!(file, "def hello():\n    pass").unwrap();

        let files = vec![file.path().to_path_buf()];
        let mut pipeline = parse_files_pipeline(files, 1, 10);

        let receiver = pipeline.take_receiver().unwrap();
        let results: Vec<_> = receiver.iter().collect();
        assert_eq!(results.len(), 1);
        assert!(!results[0].functions.is_empty());

        let _ = pipeline.join();
    }

    #[test]
    fn test_pipeline_multiple_workers() {
        let mut files = Vec::new();
        let mut temp_files = Vec::new();

        for i in 0..10 {
            let mut file = NamedTempFile::with_suffix(".py").unwrap();
            writeln!(file, "def func{}():\n    pass", i).unwrap();
            files.push(file.path().to_path_buf());
            temp_files.push(file);
        }

        let (results, stats) = parse_files_parallel_pipeline(files, 4, 5, None);

        assert_eq!(results.len(), 10);
        assert_eq!(stats.parsed_files, 10);
        assert_eq!(stats.total_functions, 10);
    }

    #[test]
    fn test_stream_parse() {
        let mut file = NamedTempFile::with_suffix(".py").unwrap();
        writeln!(file, "def test(): pass").unwrap();

        let files = vec![file.path().to_path_buf()];
        let mut count = 0;

        let stats = stream_parse_parallel(files, 1, 10, |_info| count += 1, None);

        assert_eq!(count, 1);
        assert_eq!(stats.parsed_files, 1);
    }
}
