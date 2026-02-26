//! Centralized file provider abstraction for detectors.
//!
//! Instead of each detector independently walking the filesystem and reading files,
//! they receive a `FileProvider` that supplies file lists and cached content.
//! This enables easy mocking in tests and a single point of control for file I/O.

use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Trait for providing source files and their contents to detectors.
///
/// Implementations must be `Send + Sync` so they can be shared across
/// rayon's parallel detector execution.
pub trait FileProvider: Send + Sync {
    /// All source files known to this provider.
    fn files(&self) -> &[PathBuf];

    /// Files whose extension matches `ext` (without the leading dot).
    fn files_with_extension(&self, ext: &str) -> Vec<&Path>;

    /// Files whose extension matches any of `exts` (without leading dots).
    fn files_with_extensions(&self, exts: &[&str]) -> Vec<&Path>;

    /// Read (or return cached) file content.
    fn content(&self, path: &Path) -> Option<Arc<String>>;

    /// Read (or return cached) masked file content (comments/strings replaced).
    fn masked_content(&self, path: &Path) -> Option<Arc<String>>;

    /// The repository root path.
    fn repo_path(&self) -> &Path;
}

/// Real implementation backed by the global [`crate::cache::FileCache`].
pub struct SourceFiles {
    files: Vec<PathBuf>,
    repo_path: PathBuf,
}

impl SourceFiles {
    /// Create a new `SourceFiles` from an already-collected file list.
    pub fn new(files: Vec<PathBuf>, repo_path: PathBuf) -> Self {
        Self { files, repo_path }
    }
}

impl FileProvider for SourceFiles {
    fn files(&self) -> &[PathBuf] {
        &self.files
    }

    fn files_with_extension(&self, ext: &str) -> Vec<&Path> {
        self.files
            .iter()
            .filter(|p| {
                p.extension()
                    .and_then(|e| e.to_str())
                    .map(|e| e == ext)
                    .unwrap_or(false)
            })
            .map(|p| p.as_path())
            .collect()
    }

    fn files_with_extensions(&self, exts: &[&str]) -> Vec<&Path> {
        self.files
            .iter()
            .filter(|p| {
                p.extension()
                    .and_then(|e| e.to_str())
                    .map(|e| exts.contains(&e))
                    .unwrap_or(false)
            })
            .map(|p| p.as_path())
            .collect()
    }

    fn content(&self, path: &Path) -> Option<Arc<String>> {
        crate::cache::global_cache().content(path)
    }

    fn masked_content(&self, path: &Path) -> Option<Arc<String>> {
        crate::cache::global_cache().masked_content(path)
    }

    fn repo_path(&self) -> &Path {
        &self.repo_path
    }
}

// ---------------------------------------------------------------------------
// Test-only mock
// ---------------------------------------------------------------------------

#[cfg(test)]
pub struct MockFileProvider {
    files: Vec<PathBuf>,
    contents: std::collections::HashMap<PathBuf, Arc<String>>,
    repo_path: PathBuf,
}

#[cfg(test)]
impl MockFileProvider {
    /// Build a mock from `(relative_path, content)` pairs.
    ///
    /// Paths are prefixed with `/mock/repo/` so tests never touch real files.
    pub fn new(entries: Vec<(&str, &str)>) -> Self {
        let repo_path = PathBuf::from("/mock/repo");
        let mut files = Vec::with_capacity(entries.len());
        let mut contents = std::collections::HashMap::with_capacity(entries.len());

        for (rel, body) in entries {
            let full = repo_path.join(rel);
            files.push(full.clone());
            contents.insert(full, Arc::new(body.to_string()));
        }

        Self {
            files,
            contents,
            repo_path,
        }
    }
}

#[cfg(test)]
impl MockFileProvider {
    /// Simple Python string masking: replace content inside triple-quoted strings
    /// and single-line string literals with placeholder text.
    /// This is a rough approximation of what the real masking does via tree-sitter.
    fn mask_python_strings(content: &str) -> String {
        let mut result = String::with_capacity(content.len());
        let chars: Vec<char> = content.chars().collect();
        let len = chars.len();
        let mut i = 0;

        while i < len {
            // Check for triple-quoted strings (""" or ''')
            if i + 2 < len
                && ((chars[i] == '"' && chars[i + 1] == '"' && chars[i + 2] == '"')
                    || (chars[i] == '\'' && chars[i + 1] == '\'' && chars[i + 2] == '\''))
            {
                let quote = chars[i];
                // Keep the opening quotes
                result.push(quote);
                result.push(quote);
                result.push(quote);
                i += 3;
                // Replace content until closing triple-quote, preserving newlines
                while i < len {
                    if i + 2 < len
                        && chars[i] == quote
                        && chars[i + 1] == quote
                        && chars[i + 2] == quote
                    {
                        result.push(quote);
                        result.push(quote);
                        result.push(quote);
                        i += 3;
                        break;
                    }
                    if chars[i] == '\n' {
                        result.push('\n');
                    } else {
                        result.push(' ');
                    }
                    i += 1;
                }
                continue;
            }
            result.push(chars[i]);
            i += 1;
        }

        result
    }
}

#[cfg(test)]
impl FileProvider for MockFileProvider {
    fn files(&self) -> &[PathBuf] {
        &self.files
    }

    fn files_with_extension(&self, ext: &str) -> Vec<&Path> {
        self.files
            .iter()
            .filter(|p| {
                p.extension()
                    .and_then(|e| e.to_str())
                    .map(|e| e == ext)
                    .unwrap_or(false)
            })
            .map(|p| p.as_path())
            .collect()
    }

    fn files_with_extensions(&self, exts: &[&str]) -> Vec<&Path> {
        self.files
            .iter()
            .filter(|p| {
                p.extension()
                    .and_then(|e| e.to_str())
                    .map(|e| exts.contains(&e))
                    .unwrap_or(false)
            })
            .map(|p| p.as_path())
            .collect()
    }

    fn content(&self, path: &Path) -> Option<Arc<String>> {
        self.contents.get(path).cloned()
    }

    fn masked_content(&self, path: &Path) -> Option<Arc<String>> {
        // Basic masking for tests: replace content inside Python triple-quoted
        // strings with placeholder text so detectors that rely on masking
        // (debug_code, hardcoded_ips, secrets) work correctly.
        self.content(path).map(|content| {
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if matches!(ext, "py" | "pyi") {
                Arc::new(Self::mask_python_strings(&content))
            } else {
                content
            }
        })
    }

    fn repo_path(&self) -> &Path {
        &self.repo_path
    }
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mock_file_provider_basics() {
        let provider = MockFileProvider::new(vec![
            ("src/main.rs", "fn main() {}"),
            ("src/lib.rs", "pub mod foo;"),
            ("README.md", "# Hello"),
        ]);

        // files() returns all three
        assert_eq!(provider.files().len(), 3);

        // files_with_extension filters correctly
        let rs_files = provider.files_with_extension("rs");
        assert_eq!(rs_files.len(), 2);
        for p in &rs_files {
            assert_eq!(p.extension().expect("should have extension"), "rs");
        }

        let md_files = provider.files_with_extension("md");
        assert_eq!(md_files.len(), 1);

        // content() returns what we put in
        let main_path = PathBuf::from("/mock/repo/src/main.rs");
        let content = provider.content(&main_path).expect("content should exist");
        assert_eq!(content.as_str(), "fn main() {}");

        // content() returns None for unknown paths
        assert!(provider.content(Path::new("/unknown/path.rs")).is_none());

        // repo_path()
        assert_eq!(provider.repo_path(), Path::new("/mock/repo"));
    }

    #[test]
    fn test_files_with_extensions() {
        let provider = MockFileProvider::new(vec![
            ("app.py", "print('hi')"),
            ("lib.pyi", "def foo() -> int: ..."),
            ("index.ts", "console.log('hi')"),
            ("style.css", "body {}"),
        ]);

        let python_files = provider.files_with_extensions(&["py", "pyi"]);
        assert_eq!(python_files.len(), 2);
        for p in &python_files {
            let ext = p.extension().expect("should have extension").to_str().expect("should have extension");
            assert!(ext == "py" || ext == "pyi");
        }

        let web_files = provider.files_with_extensions(&["ts", "css"]);
        assert_eq!(web_files.len(), 2);

        // No matches
        let empty = provider.files_with_extensions(&["java", "go"]);
        assert!(empty.is_empty());
    }
}
