//! Streaming graph builder using CompactGraphStore
//!
//! Memory-efficient graph building that uses string interning.
//! Target: 75k files in <2GB RAM.

use super::compact_store::CompactGraphStore;
use super::interner::StringInterner;
use crate::parsers::lightweight::LightweightFileInfo;
use anyhow::Result;
use crossbeam::channel::bounded;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::thread;

/// Stats from compact graph building
#[derive(Debug, Clone, Default)]
pub struct CompactBuildStats {
    pub files_processed: usize,
    pub functions_added: usize,
    pub classes_added: usize,
    pub edges_added: usize,
    pub unique_strings: usize,
    pub memory_bytes: usize,
}

impl CompactBuildStats {
    pub fn memory_human(&self) -> String {
        if self.memory_bytes > 1024 * 1024 {
            format!("{:.1}MB", self.memory_bytes as f64 / 1024.0 / 1024.0)
        } else {
            format!("{:.1}KB", self.memory_bytes as f64 / 1024.0)
        }
    }
}

/// Build a CompactGraphStore using parallel parsing pipeline
///
/// This is the most memory-efficient way to build a graph:
/// - Bounded channels limit in-flight parsed files
/// - String interning eliminates duplicate strings
/// - Compact node representation uses ~32 bytes vs ~200 bytes
pub fn build_compact_graph(
    files: Vec<PathBuf>,
    repo_path: &Path,
    num_workers: usize,
    buffer_size: usize,
    progress: Option<&(dyn Fn(usize, usize) + Sync)>,
) -> Result<(CompactGraphStore, CompactBuildStats, crate::parsers::LightweightParseStats)> {
    use crate::parsers::parse_file_lightweight;
    
    let total_files = files.len();
    
    // Estimate capacity
    let est_functions = total_files * 5; // ~5 functions per file avg
    let est_classes = total_files / 3;   // ~1 class per 3 files
    
    let mut store = CompactGraphStore::with_capacity(total_files, est_functions, est_classes);
    let mut stats = CompactBuildStats::default();
    let mut parse_stats = crate::parsers::LightweightParseStats {
        total_files,
        ..Default::default()
    };
    
    // Create bounded channels
    let (file_tx, file_rx) = bounded::<PathBuf>(buffer_size);
    let (result_tx, result_rx) = bounded::<LightweightFileInfo>(buffer_size);
    
    // Producer thread: feed files
    let producer = thread::spawn(move || {
        for file in files {
            if file_tx.send(file).is_err() {
                break;
            }
        }
    });
    
    // Worker threads: parse in parallel
    let parse_errors = std::sync::Arc::new(AtomicUsize::new(0));
    let mut workers = Vec::with_capacity(num_workers);
    
    for _ in 0..num_workers {
        let rx = file_rx.clone();
        let tx = result_tx.clone();
        let errors = std::sync::Arc::clone(&parse_errors);
        
        let handle = thread::spawn(move || {
            for path in rx {
                match parse_file_lightweight(&path) {
                    Ok(info) => {
                        if tx.send(info).is_err() {
                            break;
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
    
    // Drop our copies so receivers detect completion
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
        
        // Get relative path for this file
        let relative = info.relative_path(repo_path);
        
        // Add file node
        store.add_file(&relative, info.loc as u32, Some(info.language.as_str()));
        stats.files_processed += 1;
        
        // Add functions
        for func in &info.functions {
            store.add_function(
                &func.name,
                &func.qualified_name,
                &relative,
                func.line_start,
                func.line_end,
                func.is_async,
                func.complexity as u16,
            );
            store.add_contains(&relative, &func.qualified_name);
            stats.functions_added += 1;
            stats.edges_added += 1;
        }
        
        // Add classes
        for class in &info.classes {
            store.add_class(
                &class.name,
                &class.qualified_name,
                &relative,
                class.line_start,
                class.line_end,
                class.method_count as u16,
            );
            store.add_contains(&relative, &class.qualified_name);
            stats.classes_added += 1;
            stats.edges_added += 1;
        }
        
        // Add call edges
        for call in &info.calls {
            // Try to resolve callee - look up in functions we've seen
            let callee_name = call.callee.rsplit(&[':', '.'][..]).next().unwrap_or(&call.callee);
            
            // For now, just add the edge with the callee name
            // Resolution will improve as more files are processed
            store.add_call(&call.caller, &call.callee);
            stats.edges_added += 1;
        }
        
        // Add import edges
        for import in &info.imports {
            store.add_import(&relative, &import.path, import.is_type_only);
            stats.edges_added += 1;
        }
        
        // info is dropped here - memory freed
    }
    
    // Wait for threads
    let _ = producer.join();
    for w in workers {
        let _ = w.join();
    }
    
    // Finalize stats
    parse_stats.parse_errors = parse_errors.load(Ordering::Relaxed);
    parse_stats.parsed_files = count;
    
    let mem = store.memory_usage();
    stats.unique_strings = store.unique_string_count();
    stats.memory_bytes = mem.total;
    
    // Save graph
    store.save()?;
    
    Ok((store, stats, parse_stats))
}

/// Adaptive configuration based on repo size
pub fn adaptive_config(num_files: usize) -> (usize, usize) {
    let num_workers = num_cpus::get();
    
    // Smaller buffers for larger repos
    let buffer_size = match num_files {
        0..=5_000 => 100,
        5_001..=20_000 => 50,
        20_001..=50_000 => 25,
        _ => 10,
    };
    
    (num_workers, buffer_size)
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
    fn test_compact_build() {
        let dir = TempDir::new().unwrap();
        let path = dir.path();
        
        create_test_file(path, "a.py", "def hello(): pass\ndef world(): pass");
        create_test_file(path, "b.py", "def foo(): hello()");
        
        let files = vec![path.join("a.py"), path.join("b.py")];
        
        let (store, stats, parse_stats) = build_compact_graph(
            files,
            path,
            2,
            10,
            None,
        ).unwrap();
        
        assert_eq!(stats.files_processed, 2);
        assert_eq!(parse_stats.total_functions, 3);
        
        // Check memory efficiency
        println!("Memory: {}", stats.memory_human());
        println!("Unique strings: {}", stats.unique_strings);
    }
}
