//! Stage 1: File collection and hashing.

use anyhow::Result;
use std::path::{Path, PathBuf};

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
pub fn collect_stage(_input: &CollectInput) -> Result<CollectOutput> {
    todo!("Implement in Task 3")
}
