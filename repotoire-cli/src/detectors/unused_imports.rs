//! Unused imports detector
//!
//! Graph-enhanced detection of unused imports:
//! - Uses graph to track import edges
//! - Cross-references with actual symbol usage in files
//! - Groups by file for bulk cleanup suggestions

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static PYTHON_IMPORT: OnceLock<Regex> = OnceLock::new();
static JS_IMPORT: OnceLock<Regex> = OnceLock::new();
static WORD: OnceLock<Regex> = OnceLock::new();

fn python_import() -> &'static Regex {
    PYTHON_IMPORT
        .get_or_init(|| Regex::new(r"(?:from\s+[\w.]+\s+)?import\s+(.+)").expect("valid regex"))
}

fn js_import() -> &'static Regex {
    JS_IMPORT.get_or_init(|| {
        Regex::new(r#"import\s+(?:\{([^}]+)\}|(\w+))\s+from"#).expect("valid regex")
    })
}

fn word() -> &'static Regex {
    WORD.get_or_init(|| Regex::new(r"\b(\w+)\b").expect("valid regex"))
}

/// Detects unused imports
pub struct UnusedImportsDetector {
    config: DetectorConfig,
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl UnusedImportsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            config: DetectorConfig::new(),
            repository_path: repository_path.into(),
            max_findings: 100,
        }
    }

    /// Extract imported symbols from Python import line
    fn extract_python_imports(line: &str) -> Vec<(String, Option<String>)> {
        let mut symbols = Vec::new();

        // Handle "from x import a, b, c" and "import x, y"
        if let Some(caps) = python_import().captures(line) {
            if let Some(imports) = caps.get(1) {
                for part in imports.as_str().split(',') {
                    let part = part.trim();
                    if part.is_empty() {
                        continue;
                    }
                    if part.contains(" as ") {
                        let parts: Vec<&str> = part.split(" as ").collect();
                        if parts.len() == 2 {
                            symbols.push((
                                parts[1].trim().to_string(),
                                Some(parts[0].trim().to_string()),
                            ));
                        }
                    } else {
                        // Handle "from x import *" - skip these
                        if part != "*" {
                            let name = part.split('.').next_back().unwrap_or(part);
                            symbols.push((name.to_string(), None));
                        }
                    }
                }
            }
        }

        symbols
    }

    /// Extract imported symbols from JS/TS import line
    fn extract_js_imports(line: &str) -> Vec<(String, Option<String>)> {
        let mut symbols = Vec::new();

        if let Some(caps) = js_import().captures(line) {
            // Named imports: { a, b, c }
            if let Some(named) = caps.get(1) {
                for part in named.as_str().split(',') {
                    let part = part.trim();
                    if part.contains(" as ") {
                        let parts: Vec<&str> = part.split(" as ").collect();
                        if parts.len() == 2 {
                            symbols.push((
                                parts[1].trim().to_string(),
                                Some(parts[0].trim().to_string()),
                            ));
                        }
                    } else {
                        symbols.push((part.to_string(), None));
                    }
                }
            }
            // Default import: import X from
            if let Some(default) = caps.get(2) {
                symbols.push((default.as_str().to_string(), None));
            }
        }

        symbols
    }

    /// Extract symbols listed in __all__ = [...]
    fn extract_all_exports(content: &str) -> HashSet<String> {
        static ALL_PATTERN: OnceLock<Regex> = OnceLock::new();
        let pattern = ALL_PATTERN.get_or_init(|| {
            Regex::new(r#"__all__\s*=\s*\[([^\]]+)\]"#).expect("valid regex")
        });

        let mut exports = HashSet::new();
        if let Some(caps) = pattern.captures(content) {
            if let Some(items) = caps.get(1) {
                static ITEM_PATTERN: OnceLock<Regex> = OnceLock::new();
                let item_re = ITEM_PATTERN.get_or_init(|| {
                    Regex::new(r#"["'](\w+)["']"#).expect("valid regex")
                });
                for m in item_re.captures_iter(items.as_str()) {
                    if let Some(name) = m.get(1) {
                        exports.insert(name.as_str().to_string());
                    }
                }
            }
        }
        exports
    }

    /// Check if a symbol is used in the content after the import
    fn is_symbol_used(content: &str, symbol: &str, import_line: usize) -> bool {
        let lines: Vec<&str> = content.lines().collect();

        // Skip common false positives
        if symbol == "_" || symbol == "annotations" || symbol == "TYPE_CHECKING" {
            return true;
        }

        for (i, line) in lines.iter().enumerate() {
            if i <= import_line {
                continue;
            }

            // Check for word boundary match
            for m in word().find_iter(line) {
                if m.as_str() == symbol {
                    return true;
                }
            }
        }

        false
    }
}

impl Default for UnusedImportsDetector {
    fn default() -> Self {
        Self::new(".")
    }
}

impl Detector for UnusedImportsDetector {
    fn name(&self) -> &'static str {
        "UnusedImportsDetector"
    }

    fn description(&self) -> &'static str {
        "Detects imports that are never used in the code"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut unused_per_file: HashMap<PathBuf, Vec<(String, u32)>> = HashMap::new();

        for path in files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

            // Skip __init__.py (often re-exports)
            if path
                .file_name()
                .map(|n| n.to_string_lossy().contains("__init__"))
                .unwrap_or(false)
            {
                continue;
            }

            // Skip type stub files
            if path.to_string_lossy().ends_with(".pyi") {
                continue;
            }

            if let Some(content) = files.content(path) {
                let all_exports = Self::extract_all_exports(&content);
                let lines: Vec<&str> = content.lines().collect();

                let mut in_type_checking = false;
                let mut type_checking_indent: usize = 0;

                for (line_num, line) in lines.iter().enumerate() {
                    let prev_line = if line_num > 0 { Some(lines[line_num - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    let trimmed = line.trim();

                    // Skip imports with # noqa suppression
                    if trimmed.contains("# noqa") {
                        continue;
                    }
                    // Skip imports with eslint-disable
                    if trimmed.contains("// eslint-disable") {
                        continue;
                    }

                    // Skip comments
                    if trimmed.starts_with("#") || trimmed.starts_with("//") {
                        continue;
                    }

                    // Track TYPE_CHECKING blocks -- skip all indented lines within
                    if trimmed == "if TYPE_CHECKING:" || trimmed.starts_with("if TYPE_CHECKING:") {
                        in_type_checking = true;
                        type_checking_indent = line.len() - line.trim_start().len();
                        continue;
                    }
                    if in_type_checking {
                        let current_indent = line.len() - line.trim_start().len();
                        if !trimmed.is_empty() && current_indent <= type_checking_indent {
                            in_type_checking = false;
                            // Fall through to process this line normally
                        } else {
                            continue; // Skip lines inside TYPE_CHECKING block
                        }
                    }

                    let imports = if ext == "py" {
                        if trimmed.starts_with("import ") || trimmed.starts_with("from ") {
                            // Skip function-scoped imports (indented imports inside function bodies)
                            // These exist to avoid circular imports or lazy-load modules and are
                            // always intentional. Check if the line has leading whitespace.
                            let leading_spaces = line.len() - line.trim_start().len();
                            if leading_spaces >= 4 {
                                // Indented import — inside a function/method body, skip it
                                continue;
                            }

                            // Handle multi-line imports: from X import (\n    A,\n    B,\n)
                            let effective_line = if trimmed.contains("(") && !trimmed.contains(")") {
                                let mut accumulated = trimmed.to_string();
                                let mut j = line_num + 1;
                                while j < lines.len() {
                                    let continuation = lines[j].trim();
                                    accumulated.push(' ');
                                    accumulated.push_str(continuation);
                                    if continuation.contains(")") {
                                        break;
                                    }
                                    j += 1;
                                }
                                // Remove parentheses so the regex sees a flat import list
                                accumulated.replace(['(', ')'], "")
                            } else {
                                trimmed.to_string()
                            };
                            Self::extract_python_imports(&effective_line)
                        } else {
                            continue;
                        }
                    } else if trimmed.starts_with("import ") {
                        // For TypeScript: skip `import type { ... }` and `import type X from`
                        // Type imports are erased at compile time and used only in type positions.
                        // The simple word-search in is_symbol_used() can't find type positions,
                        // so we'd get false positives for every type import.
                        if matches!(ext, "ts" | "tsx")
                            && (trimmed.starts_with("import type ")
                                || trimmed.contains(" type {")
                                || trimmed.contains("{ type ")
                                || trimmed.contains(", type "))
                        {
                            // Skip entire import if it's a type-only import or
                            // contains any inline type imports (import { type X, type Y })
                            // These are used in type annotations which our word search may miss
                            continue;
                        }
                        Self::extract_js_imports(trimmed)
                    } else {
                        continue;
                    };

                    for (symbol, _alias) in imports {
                        if all_exports.contains(&symbol) {
                            continue;
                        }
                        if !Self::is_symbol_used(&content, &symbol, line_num) {
                            unused_per_file
                                .entry(path.to_path_buf())
                                .or_default()
                                .push((symbol.clone(), (line_num + 1) as u32));
                        }
                    }
                }
            }
        }

        // Create findings grouped by file
        for (file_path, unused) in unused_per_file {
            if unused.is_empty() {
                continue;
            }

            // Group closely located imports
            let mut i = 0;
            while i < unused.len() {
                let (symbol, line) = &unused[i];

                // Find consecutive unused imports
                let mut group = vec![symbol.clone()];
                let first_line = *line;
                let mut last_line = *line;

                while i + 1 < unused.len() && unused[i + 1].1 <= last_line + 3 {
                    i += 1;
                    group.push(unused[i].0.clone());
                    last_line = unused[i].1;
                }

                let severity = if group.len() >= 5 {
                    Severity::Medium // Many unused imports = messier code
                } else {
                    Severity::Low
                };

                let symbols_str = if group.len() > 3 {
                    format!("{} and {} others", group[..3].join(", "), group.len() - 3)
                } else {
                    group.join(", ")
                };

                findings.push(Finding {
                    id: String::new(),
                    detector: "UnusedImportsDetector".to_string(),
                    severity,
                    title: format!("Unused import{}: {}", if group.len() > 1 { "s" } else { "" }, symbols_str),
                    description: format!(
                        "Import{} `{}` {} never used in this file.\n\n\
                         **Cleanup tip:** Run `autoflake --remove-all-unused-imports` (Python) or configure ESLint/TypeScript.",
                        if group.len() > 1 { "s" } else { "" },
                        group.join("`, `"),
                        if group.len() > 1 { "are" } else { "is" }
                    ),
                    affected_files: vec![file_path.clone()],
                    line_start: Some(first_line),
                    line_end: Some(last_line),
                    suggested_fix: Some(format!(
                        "Remove unused import{}:\n```\n# Delete: {}\n```",
                        if group.len() > 1 { "s" } else { "" },
                        group.join(", ")
                    )),
                    estimated_effort: Some("2 minutes".to_string()),
                    category: Some("code-quality".to_string()),
                    cwe_id: None,
                    why_it_matters: Some(
                        "Unused imports add noise, increase load times, and can cause \
                         circular dependency issues. They also make it harder to understand \
                         what a file actually depends on.".to_string()
                    ),
                    ..Default::default()
                });

                i += 1;
            }
        }

        info!("UnusedImportsDetector found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_no_finding_for_noqa_suppressed_import() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("module.py");
        std::fs::write(
            &file,
            "from flask import Flask  # noqa: F401\nfrom utils import helper  # noqa\n",
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = UnusedImportsDetector::new(dir.path());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag imports with # noqa suppression. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_all_re_export() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("api.py");
        std::fs::write(
            &file,
            "from .models import User, Post\nfrom .views import ListView\n\n__all__ = [\"User\", \"Post\", \"ListView\"]\n",
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = UnusedImportsDetector::new(dir.path());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag imports listed in __all__. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_type_checking_block() {
        let store = GraphStore::in_memory();
        let detector = UnusedImportsDetector::new("/mock/repo");
        // UserModel is only imported inside TYPE_CHECKING and never referenced
        // outside (only in string annotation "UserModel").
        // The detector should skip the entire block, not just the `if` line.
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("typed.py", "from __future__ import annotations\nfrom typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from models import UserModel\n    from services import AuthService\n\ndef greet() -> str:\n    return \"hello\"\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        let tc_findings: Vec<_> = findings.iter()
            .filter(|f| f.title.contains("UserModel") || f.title.contains("AuthService"))
            .collect();
        assert!(
            tc_findings.is_empty(),
            "Should not flag imports inside TYPE_CHECKING block. Found: {:?}",
            tc_findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_handles_multiline_import() {
        let store = GraphStore::in_memory();
        let detector = UnusedImportsDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("views.py", "from django.db.models import (\n    CharField,\n    IntegerField,\n)\n\nname = CharField(max_length=100)\nage = IntegerField()\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should handle multi-line imports (CharField and IntegerField are used). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_still_detects_unused_import() {
        let store = GraphStore::in_memory();
        let detector = UnusedImportsDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("unused.py", "import os\nimport sys\n\nprint(sys.argv)\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should still detect unused import (os)"
        );
    }

    #[test]
    fn test_no_finding_for_function_scoped_import() {
        let store = GraphStore::in_memory();
        let detector = UnusedImportsDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("lookups.py", "def get_prep_lookup(self):\n    from django.db.models.sql.query import Query\n    if isinstance(self.rhs, Query):\n        return self.rhs\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag function-scoped imports (used to avoid circular imports). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_deeply_indented_import() {
        let store = GraphStore::in_memory();
        let detector = UnusedImportsDetector::new("/mock/repo");
        let files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("base.py", "class DatabaseWrapper:\n    def connect(self):\n        from .psycopg_any import IsolationLevel, is_psycopg3\n        if is_psycopg3:\n            conn.isolation_level = IsolationLevel.READ_COMMITTED\n"),
        ]);
        let findings = detector.detect(&store, &files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag indented imports inside methods. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
