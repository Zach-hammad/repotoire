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
use uuid::Uuid;

static PYTHON_IMPORT: OnceLock<Regex> = OnceLock::new();
static JS_IMPORT: OnceLock<Regex> = OnceLock::new();
static WORD: OnceLock<Regex> = OnceLock::new();

fn python_import() -> &'static Regex {
    PYTHON_IMPORT.get_or_init(|| Regex::new(r"(?:from\s+[\w.]+\s+)?import\s+(.+)").unwrap())
}

fn js_import() -> &'static Regex {
    JS_IMPORT.get_or_init(|| Regex::new(r#"import\s+(?:\{([^}]+)\}|(\w+))\s+from"#).unwrap())
}

fn word() -> &'static Regex {
    WORD.get_or_init(|| Regex::new(r"\b(\w+)\b").unwrap())
}

/// Detects unused imports
pub struct UnusedImportsDetector {
    config: DetectorConfig,
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

    fn detect(&self, _graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let mut unused_per_file: HashMap<PathBuf, Vec<(String, u32)>> = HashMap::new();

        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }
            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py" | "js" | "ts" | "jsx" | "tsx") {
                continue;
            }

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

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (line_num, line) in lines.iter().enumerate() {
                    let trimmed = line.trim();

                    // Skip comments
                    if trimmed.starts_with("#") || trimmed.starts_with("//") {
                        continue;
                    }

                    // Skip TYPE_CHECKING blocks (type-only imports)
                    if trimmed.contains("TYPE_CHECKING") {
                        continue;
                    }

                    let imports = if ext == "py" {
                        if trimmed.starts_with("import ") || trimmed.starts_with("from ") {
                            Self::extract_python_imports(trimmed)
                        } else {
                            continue;
                        }
                    } else if trimmed.starts_with("import ") {
                        Self::extract_js_imports(trimmed)
                    } else {
                        continue;
                    };

                    for (symbol, _alias) in imports {
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
                    id: Uuid::new_v4().to_string(),
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
