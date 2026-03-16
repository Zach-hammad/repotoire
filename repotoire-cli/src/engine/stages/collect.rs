//! Stage 1: File collection and hashing.

use anyhow::Result;
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};

use crate::config::ExcludeConfig;

/// Input for the collect stage.
pub struct CollectInput<'a> {
    pub repo_path: &'a Path,
    pub exclude_patterns: &'a [String],
    pub max_files: usize,
}

/// A source file with its content hash for change detection.
pub struct SourceFile {
    pub path: PathBuf,
    pub content_hash: u64,
}

/// Output from the collect stage — the complete file manifest.
pub struct CollectOutput {
    pub files: Vec<SourceFile>,
}

impl CollectOutput {
    /// Get all file paths from the manifest.
    pub fn all_paths(&self) -> Vec<PathBuf> {
        self.files.iter().map(|f| f.path.clone()).collect()
    }
}

/// Walk the repository, hash files, return the complete file manifest.
pub fn collect_stage(input: &CollectInput) -> Result<CollectOutput> {
    // Build ExcludeConfig from the input patterns
    let exclude = ExcludeConfig {
        paths: input.exclude_patterns.to_vec(),
        skip_defaults: false,
    };

    // Delegate to the existing file collection function
    let mut files = crate::cli::analyze::files::collect_file_list(input.repo_path, &exclude)?;

    // Apply max_files truncation
    if input.max_files > 0 && files.len() > input.max_files {
        files.truncate(input.max_files);
    }

    // Hash each file's content with SipHash for change detection
    let source_files: Vec<SourceFile> = files
        .into_iter()
        .map(|path| {
            let content_hash = hash_file_content(&path);
            SourceFile { path, content_hash }
        })
        .collect();

    Ok(CollectOutput {
        files: source_files,
    })
}

/// Hash file content using SipHash (DefaultHasher) for change detection.
fn hash_file_content(path: &Path) -> u64 {
    match std::fs::read(path) {
        Ok(content) => {
            let mut hasher = DefaultHasher::new();
            content.hash(&mut hasher);
            hasher.finish()
        }
        Err(_) => 0,
    }
}
