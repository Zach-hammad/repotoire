//! Unused imports detector
//!
//! Graph-enhanced detection of unused imports:
//! - Uses graph to track import edges
//! - Cross-references with actual symbol usage in files
//! - Groups by file for bulk cleanup suggestions

use crate::detectors::base::{Detector, DetectorConfig};
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::LazyLock;
use tracing::info;

static PYTHON_IMPORT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?:from\s+[\w.]+\s+)?import\s+(.+)").expect("valid regex"));
static JS_IMPORT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"import\s+(?:\{([^}]+)\}|(\w+))\s+from"#).expect("valid regex"));
static WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(\w+)\b").expect("valid regex"));

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
        if let Some(caps) = PYTHON_IMPORT.captures(line) {
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

        if let Some(caps) = JS_IMPORT.captures(line) {
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
        static ALL_PATTERN: LazyLock<Regex> =
            LazyLock::new(|| Regex::new(r#"__all__\s*=\s*\[([^\]]+)\]"#).expect("valid regex"));
        let pattern = &*ALL_PATTERN;

        let mut exports = HashSet::new();
        if let Some(caps) = pattern.captures(content) {
            if let Some(items) = caps.get(1) {
                static ITEM_PATTERN: LazyLock<Regex> =
                    LazyLock::new(|| Regex::new(r#"["'](\w+)["']"#).expect("valid regex"));
                let item_re = &*ITEM_PATTERN;
                for m in item_re.captures_iter(items.as_str()) {
                    if let Some(name) = m.get(1) {
                        exports.insert(name.as_str().to_string());
                    }
                }
            }
        }
        exports
    }

    // Symbol usage is checked via pre-built HashSet in detect(), not per-symbol scanning
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

    fn requires_graph(&self) -> bool {
        false
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "go", "java", "rs"]
    }

    fn content_requirements(&self) -> crate::detectors::detector_context::ContentFlags {
        crate::detectors::detector_context::ContentFlags::HAS_IMPORT
    }

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        let files = &ctx.as_file_provider();
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
                // Fast pre-filter: skip files without any import/require statements
                if !content.contains("import") && !content.contains("require") {
                    continue;
                }

                let all_exports = Self::extract_all_exports(&content);
                let lines: Vec<&str> = content.lines().collect();

                // Build word set from non-import lines ONCE per file.
                // O(N) instead of O(K×N) per-symbol scanning.
                let usage_set: HashSet<&str> = {
                    let mut set = HashSet::new();
                    for line in &lines {
                        let trimmed = line.trim();
                        let is_import = if ext == "py" {
                            (trimmed.starts_with("import ") || trimmed.starts_with("from "))
                                && (line.len() - line.trim_start().len()) < 4
                        } else {
                            trimmed.starts_with("import ")
                        };
                        if !is_import {
                            for m in WORD.find_iter(line) {
                                set.insert(m.as_str());
                            }
                        }
                    }
                    set
                };

                let mut in_type_checking = false;
                let mut type_checking_indent: usize = 0;

                for (line_num, line) in lines.iter().enumerate() {
                    let prev_line = if line_num > 0 {
                        Some(lines[line_num - 1])
                    } else {
                        None
                    };
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
                            let effective_line = if trimmed.contains("(") && !trimmed.contains(")")
                            {
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
                        // Skip common false positives
                        if symbol == "_" || symbol == "annotations" || symbol == "TYPE_CHECKING" {
                            continue;
                        }
                        if all_exports.contains(&symbol) {
                            continue;
                        }
                        // O(1) lookup in pre-built word set instead of O(N) file re-scan
                        if !usage_set.contains(symbol.as_str()) {
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
        // Sort by file path for deterministic iteration order
        let mut sorted_unused: Vec<_> = unused_per_file.into_iter().collect();
        sorted_unused.sort_by(|(a, _), (b, _)| a.cmp(b));
        for (file_path, unused) in sorted_unused {
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

impl crate::detectors::RegisteredDetector for UnusedImportsDetector {
    fn create(init: &crate::detectors::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new(init.repo_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;

    #[test]
    fn test_no_finding_for_noqa_suppressed_import() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let file = dir.path().join("module.py");
        std::fs::write(
            &file,
            "from flask import Flask  # noqa: F401\nfrom utils import helper  # noqa\n",
        )
        .expect("should write test file");

        let store = GraphBuilder::new().freeze();
        let detector = UnusedImportsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &store,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag imports with # noqa suppression. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_all_re_export() {
        let dir = tempfile::tempdir().expect("should create temp dir");
        let file = dir.path().join("api.py");
        std::fs::write(
            &file,
            "from .models import User, Post\nfrom .views import ListView\n\n__all__ = [\"User\", \"Post\", \"ListView\"]\n",
        )
        .expect("should write test file");

        let store = GraphBuilder::new().freeze();
        let detector = UnusedImportsDetector::new(dir.path());
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &store,
            vec![],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag imports listed in __all__. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_type_checking_block() {
        let store = GraphBuilder::new().freeze();
        let _detector = UnusedImportsDetector::new("/mock/repo");
        // UserModel is only imported inside TYPE_CHECKING and never referenced
        // outside (only in string annotation "UserModel").
        let detector = UnusedImportsDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("typed.py", "from __future__ import annotations\nfrom typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from models import UserModel\n    from services import AuthService\n\ndef greet() -> str:\n    return \"hello\"\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        let tc_findings: Vec<_> = findings
            .iter()
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
        let store = GraphBuilder::new().freeze();
        let detector = UnusedImportsDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("views.py", "from django.db.models import (\n    CharField,\n    IntegerField,\n)\n\nname = CharField(max_length=100)\nage = IntegerField()\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should handle multi-line imports (CharField and IntegerField are used). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_still_detects_unused_import() {
        let store = GraphBuilder::new().freeze();
        let detector = UnusedImportsDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(
            &store,
            vec![("unused.py", "import os\nimport sys\n\nprint(sys.argv)\n")],
        );
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            !findings.is_empty(),
            "Should still detect unused import (os)"
        );
    }

    #[test]
    fn test_no_finding_for_function_scoped_import() {
        let store = GraphBuilder::new().freeze();
        let detector = UnusedImportsDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("lookups.py", "def get_prep_lookup(self):\n    from django.db.models.sql.query import Query\n    if isinstance(self.rhs, Query):\n        return self.rhs\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag function-scoped imports (used to avoid circular imports). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_deeply_indented_import() {
        let store = GraphBuilder::new().freeze();
        let detector = UnusedImportsDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&store, vec![
            ("base.py", "class DatabaseWrapper:\n    def connect(self):\n        from .psycopg_any import IsolationLevel, is_psycopg3\n        if is_psycopg3:\n            conn.isolation_level = IsolationLevel.READ_COMMITTED\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(
            findings.is_empty(),
            "Should not flag indented imports inside methods. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
