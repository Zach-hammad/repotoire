//! TRUE streaming parsers that extract lightweight info directly
//!
//! These functions parse files and extract ONLY what we need into
//! `LightweightFileInfo`, without creating intermediate `ParseResult`.
//!
//! # Memory Model
//!
//! ```text
//! Traditional:
//!   file.py → tree-sitter AST (10MB) → ParseResult (2KB) → graph
//!   file.py → tree-sitter AST (10MB) → ParseResult (2KB) → graph
//!   [All ParseResults kept in memory until graph is built]
//!
//! Streaming:
//!   file.py → tree-sitter AST (10MB) → LightweightFileInfo (400B) → [AST dropped]
//!   [Process next file...]
//!   [Only LightweightFileInfo kept - much smaller]
//! ```

use super::lightweight::*;
use crate::parsers::parse_file;
use anyhow::Result;
use std::path::{Path, PathBuf};

/// Parse a file and return lightweight info directly
///
/// This is a COMPATIBILITY wrapper that uses the existing parse_file()
/// and converts the result. For maximum efficiency, language-specific
/// lightweight parsers should be used (TODO: implement per-language).
///
/// The AST is dropped as soon as this function returns.
pub fn parse_file_lightweight(path: &Path) -> Result<LightweightFileInfo> {
    let language = Language::from_path(path);

    // Parse once. Avoid pre-read line counting to prevent double I/O on hot paths (#54).
    let result = parse_file(path)?;

    // Use actual file line count from cache for accurate LOC (#69)
    let loc = crate::cache::global_cache()
        .get_lines(path)
        .map(|lines| lines.len() as u32)
        .unwrap_or_else(|| {
            // Fallback: max entity line_end (misses trailing module-level code)
            result
                .functions
                .iter()
                .map(|f| f.line_end)
                .chain(result.classes.iter().map(|c| c.line_end))
                .max()
                .unwrap_or(1)
        });

    // Convert to lightweight immediately - ParseResult is dropped after this
    let info = LightweightFileInfo::from_parse_result(&result, path.to_path_buf(), language, loc);

    // result is dropped here - AST memory freed
    Ok(info)
}

/// Parse files one at a time, returning an iterator
///
/// This is the TRUE streaming approach - only one AST in memory at a time.
/// Files are parsed lazily as the iterator is consumed.
pub fn parse_files_streaming<'a>(
    files: &'a [PathBuf],
) -> impl Iterator<Item = Result<LightweightFileInfo>> + 'a {
    files.iter().map(|path| parse_file_lightweight(path))
}

/// Parse files with progress callback, collecting results
///
/// This collects all LightweightFileInfo into a Vec, but crucially:
/// - Only ONE AST is in memory at any time
/// - Each AST is dropped before parsing the next file
/// - Total memory is bounded by number of files × sizeof(LightweightFileInfo)
///
/// For 20k files with ~10 functions each:
/// - Traditional: 20k × ParseResult (~2KB) = ~40MB of parse results
/// - Streaming: 20k × LightweightFileInfo (~400B) = ~8MB
pub fn parse_files_sequential_collect(
    files: &[PathBuf],
    progress: Option<&dyn Fn(usize, usize)>,
) -> (Vec<LightweightFileInfo>, LightweightParseStats) {
    let total = files.len();
    let mut results = Vec::with_capacity(total);
    let mut stats = LightweightParseStats {
        total_files: total,
        ..Default::default()
    };

    for (idx, path) in files.iter().enumerate() {
        if let Some(cb) = progress {
            if idx % 100 == 0 || idx == total - 1 {
                cb(idx, total);
            }
        }

        match parse_file_lightweight(path) {
            Ok(info) => {
                stats.add_file(&info);
                results.push(info);
            }
            Err(e) => {
                stats.parse_errors += 1;
                tracing::warn!("Failed to parse {}: {}", path.display(), e);
            }
        }

        // Critical: AST for this file is now dropped
        // Memory is freed before we parse the next file
    }

    (results, stats)
}

/// Parse files in parallel batches, maintaining streaming memory properties
///
/// This processes files in parallel within batches, but:
/// - Each batch is processed independently
/// - ASTs within a batch are dropped before starting the next batch
/// - Total peak memory = batch_size × max_ast_size + collected_info
///
/// Recommended batch_size: 500-2000 depending on file sizes
pub fn parse_files_parallel_streaming(
    files: &[PathBuf],
    batch_size: usize,
    progress: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> (Vec<LightweightFileInfo>, LightweightParseStats) {
    use rayon::prelude::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    let total = files.len();
    let mut all_results = Vec::with_capacity(total);
    let mut stats = LightweightParseStats {
        total_files: total,
        ..Default::default()
    };

    let counter = AtomicUsize::new(0);
    let errors = AtomicUsize::new(0);

    // Process in batches to limit peak memory
    for chunk in files.chunks(batch_size) {
        // Parse batch in parallel
        let batch_results: Vec<Option<LightweightFileInfo>> = chunk
            .par_iter()
            .map(|path| {
                let count = counter.fetch_add(1, Ordering::Relaxed);
                if let Some(cb) = progress {
                    if count.is_multiple_of(200) {
                        cb(count, total);
                    }
                }

                match parse_file_lightweight(path) {
                    Ok(info) => Some(info),
                    Err(e) => {
                        errors.fetch_add(1, Ordering::Relaxed);
                        tracing::warn!("Failed to parse {}: {}", path.display(), e);
                        None
                    }
                }
                // AST dropped here when parse_file_lightweight returns
            })
            .collect();

        // Collect results from this batch
        for info in batch_results.into_iter().flatten() {
            stats.add_file(&info);
            all_results.push(info);
        }

        // All ASTs from this batch are now dropped
        // Memory is freed before starting next batch
    }

    stats.parse_errors = errors.load(Ordering::Relaxed);

    (all_results, stats)
}

/// Callback-based streaming parser
///
/// This is the most memory-efficient approach - results are processed
/// immediately and not collected into a Vec.
///
/// Use this when building the graph incrementally.
pub fn stream_parse_with_callback<F>(
    files: &[PathBuf],
    mut on_file: F,
    progress: Option<&dyn Fn(usize, usize)>,
) -> LightweightParseStats
where
    F: FnMut(LightweightFileInfo) -> Result<()>,
{
    let total = files.len();
    let mut stats = LightweightParseStats {
        total_files: total,
        ..Default::default()
    };

    for (idx, path) in files.iter().enumerate() {
        if let Some(cb) = progress {
            if idx % 100 == 0 || idx == total - 1 {
                cb(idx, total);
            }
        }

        match parse_file_lightweight(path) {
            Ok(info) => {
                stats.add_file(&info);
                if let Err(e) = on_file(info) {
                    tracing::warn!("Callback error for {}: {}", path.display(), e);
                }
            }
            Err(e) => {
                stats.parse_errors += 1;
                tracing::warn!("Failed to parse {}: {}", path.display(), e);
            }
        }
        // AST dropped here
    }

    stats
}

/// Helper to count lines in a file
fn count_lines(path: &Path) -> Result<u32> {
    let content = std::fs::read_to_string(path)?;
    Ok(content.lines().count() as u32)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    #[test]
    fn test_parse_file_lightweight() {
        // Create a temporary Python file
        let mut file = NamedTempFile::with_suffix(".py").unwrap();
        writeln!(
            file,
            "def hello(name):\n    print(f'Hello {{name}}')\n\ndef world():\n    pass"
        )
        .unwrap();

        let result = parse_file_lightweight(file.path());
        assert!(result.is_ok());

        let info = result.unwrap();
        assert_eq!(info.language, Language::Python);
        assert!(!info.functions.is_empty());
    }

    #[test]
    fn test_streaming_iterator() {
        let mut file = NamedTempFile::with_suffix(".py").unwrap();
        writeln!(file, "x = 1").unwrap();

        let files = vec![file.path().to_path_buf()];
        let mut results: Vec<_> = parse_files_streaming(&files).collect();

        assert_eq!(results.len(), 1);
        assert!(results.pop().unwrap().is_ok());
    }

    #[test]
    fn test_callback_streaming() {
        let mut file = NamedTempFile::with_suffix(".py").unwrap();
        writeln!(file, "def test(): pass").unwrap();

        let files = vec![file.path().to_path_buf()];
        let mut count = 0;

        let stats = stream_parse_with_callback(
            &files,
            |_info| {
                count += 1;
                Ok(())
            },
            None,
        );

        assert_eq!(count, 1);
        assert_eq!(stats.parsed_files, 1);
    }
}
