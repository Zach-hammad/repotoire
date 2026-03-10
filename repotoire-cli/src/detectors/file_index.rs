//! Pre-indexed file content with lazy per-file computations.
//!
//! Built once before detector execution. Detectors query the index
//! instead of iterating raw files. Expensive per-file operations
//! (lowercase, tokenization) are computed lazily via OnceLock and
//! shared across all detectors.

use crate::detectors::detector_context::ContentFlags;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

/// A single file in the index with lazy pre-computed fields.
pub struct FileEntry {
    pub path: PathBuf,
    pub content: Arc<str>,
    pub flags: ContentFlags,
    lowercased: OnceLock<Arc<str>>,
    word_set: OnceLock<Arc<HashSet<String>>>,
}

impl FileEntry {
    pub fn new(path: PathBuf, content: Arc<str>, flags: ContentFlags) -> Self {
        Self {
            path,
            content,
            flags,
            lowercased: OnceLock::new(),
            word_set: OnceLock::new(),
        }
    }

    /// Get lowercased content (computed once, then cached).
    pub fn content_lower(&self) -> &Arc<str> {
        self.lowercased
            .get_or_init(|| Arc::from(self.content.to_ascii_lowercase()))
    }

    /// Get the set of word tokens in this file (computed once, then cached).
    /// Words are sequences of [a-zA-Z_][a-zA-Z0-9_]* -- valid identifiers.
    pub fn word_set(&self) -> &Arc<HashSet<String>> {
        self.word_set.get_or_init(|| {
            let mut words = HashSet::new();
            let bytes = self.content.as_bytes();
            let mut i = 0;
            while i < bytes.len() {
                if bytes[i].is_ascii_alphabetic() || bytes[i] == b'_' {
                    let start = i;
                    i += 1;
                    while i < bytes.len()
                        && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_')
                    {
                        i += 1;
                    }
                    let word = &self.content[start..i];
                    if word.len() >= 2 {
                        words.insert(word.to_string());
                    }
                } else {
                    i += 1;
                }
            }
            Arc::new(words)
        })
    }
}

/// Pre-indexed collection of source files.
///
/// Built once before detector execution. Provides O(1) filtering
/// by file extension and content flags.
pub struct FileIndex {
    entries: Vec<FileEntry>,
    /// Extension -> Vec<index into entries>
    by_extension: rustc_hash::FxHashMap<String, Vec<usize>>,
}

impl FileIndex {
    /// Build a FileIndex from pre-loaded file data.
    pub fn new(file_data: Vec<(PathBuf, Arc<str>, ContentFlags)>) -> Self {
        let mut by_extension: rustc_hash::FxHashMap<String, Vec<usize>> =
            rustc_hash::FxHashMap::default();

        let mut entries = Vec::with_capacity(file_data.len());
        for (i, (path, content, flags)) in file_data.into_iter().enumerate() {
            if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
                by_extension.entry(ext.to_string()).or_default().push(i);
            }
            entries.push(FileEntry::new(path, content, flags));
        }

        Self {
            entries,
            by_extension,
        }
    }

    /// Get all file entries.
    pub fn all(&self) -> &[FileEntry] {
        &self.entries
    }

    /// Get file entries matching ANY of the given extensions AND having
    /// at least one of the required content flags.
    ///
    /// If `required_flags` is empty, returns all files with matching extensions.
    pub fn matching(&self, extensions: &[&str], required_flags: ContentFlags) -> Vec<&FileEntry> {
        let mut result = Vec::new();
        for ext in extensions {
            if let Some(indices) = self.by_extension.get(*ext) {
                for &idx in indices {
                    let entry = &self.entries[idx];
                    if required_flags.is_empty() || entry.flags.has(required_flags) {
                        result.push(entry);
                    }
                }
            }
        }
        result
    }

    /// Get file entries matching ANY of the given extensions (no flag filter).
    pub fn by_extensions(&self, extensions: &[&str]) -> Vec<&FileEntry> {
        let mut result = Vec::new();
        for ext in extensions {
            if let Some(indices) = self.by_extension.get(*ext) {
                for &idx in indices {
                    result.push(&self.entries[idx]);
                }
            }
        }
        result
    }

    /// Get a file entry by path.
    pub fn get(&self, path: &Path) -> Option<&FileEntry> {
        self.entries.iter().find(|e| e.path == path)
    }

    /// Number of files in the index.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Whether the index is empty.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_data() -> Vec<(PathBuf, Arc<str>, ContentFlags)> {
        vec![
            (
                PathBuf::from("/repo/app.py"),
                Arc::from("import os\ndef main(): pass"),
                ContentFlags::HAS_IMPORT,
            ),
            (
                PathBuf::from("/repo/sql.py"),
                Arc::from("SELECT * FROM users"),
                ContentFlags::HAS_SQL,
            ),
            (
                PathBuf::from("/repo/safe.py"),
                Arc::from("x = 1 + 2"),
                ContentFlags::empty(),
            ),
            (
                PathBuf::from("/repo/index.ts"),
                Arc::from("import React from 'react'"),
                {
                    let mut f = ContentFlags::empty();
                    f.set(ContentFlags::HAS_IMPORT);
                    f.set(ContentFlags::HAS_REACT);
                    f
                },
            ),
        ]
    }

    #[test]
    fn test_file_index_matching_with_flags() {
        let index = FileIndex::new(test_data());
        let sql_files = index.matching(&["py"], ContentFlags::HAS_SQL);
        assert_eq!(sql_files.len(), 1);
        assert!(sql_files[0].path.ends_with("sql.py"));
    }

    #[test]
    fn test_file_index_by_extensions() {
        let index = FileIndex::new(test_data());
        let py_files = index.by_extensions(&["py"]);
        assert_eq!(py_files.len(), 3);
        let ts_files = index.by_extensions(&["ts"]);
        assert_eq!(ts_files.len(), 1);
    }

    #[test]
    fn test_file_entry_content_lower() {
        let entry = FileEntry::new(
            PathBuf::from("test.py"),
            Arc::from("Hello WORLD"),
            ContentFlags::empty(),
        );
        assert_eq!(entry.content_lower().as_ref(), "hello world");
        // Second call returns cached value (same Arc)
        let ptr1 = Arc::as_ptr(entry.content_lower());
        let ptr2 = Arc::as_ptr(entry.content_lower());
        assert_eq!(ptr1, ptr2);
    }

    #[test]
    fn test_file_entry_word_set() {
        let entry = FileEntry::new(
            PathBuf::from("test.py"),
            Arc::from("def hello_world():\n    x = 42"),
            ContentFlags::empty(),
        );
        let words = entry.word_set();
        assert!(words.contains("def"));
        assert!(words.contains("hello_world"));
        // Single-char 'x' excluded (len < 2)
        assert!(!words.contains("x"));
        // Number '42' excluded (not alphabetic start)
        assert!(!words.contains("42"));
    }

    #[test]
    fn test_file_index_empty_flags_returns_all_matching_ext() {
        let index = FileIndex::new(test_data());
        let all_py = index.matching(&["py"], ContentFlags::empty());
        assert_eq!(all_py.len(), 3);
    }
}
