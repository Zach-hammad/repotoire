//! Wildcard Imports Detector
//!
//! Graph-enhanced detection of wildcard imports.
//! Uses graph to:
//! - Check what's actually imported from the module (via import edges)
//! - Suggest specific imports based on actual usage

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

static WILDCARD_PATTERN: OnceLock<Regex> = OnceLock::new();
static MODULE_NAME: OnceLock<Regex> = OnceLock::new();

fn wildcard_pattern() -> &'static Regex {
    WILDCARD_PATTERN.get_or_init(|| {
        Regex::new(r"(?i)(from\s+\S+\s+import\s+\*|import\s+\*\s+from|import\s+\*\s*;|\.\*;)")
            .expect("valid regex")
    })
}

fn module_name() -> &'static Regex {
    MODULE_NAME.get_or_init(|| {
        Regex::new(r#"from\s+(\S+)\s+import|import\s+\*\s+from\s+['"]([^'"]+)"#)
            .expect("valid regex")
    })
}

pub struct WildcardImportsDetector {
    #[allow(dead_code)] // Part of detector pattern, used for file scanning
    repository_path: PathBuf,
    max_findings: usize,
}

impl WildcardImportsDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
        }
    }

    /// Extract module name from wildcard import line
    fn extract_module_name(line: &str) -> Option<String> {
        module_name()
            .captures(line)
            .and_then(|caps| caps.get(1).or(caps.get(2)).map(|m| m.as_str().to_string()))
    }

    /// Find what symbols from a module are actually used in the file
    fn find_used_symbols(
        content: &str,
        module: &str,
        graph: &dyn crate::graph::GraphQuery,
    ) -> Vec<String> {
        // Get all functions/classes from the module
        let module_symbols: HashSet<String> = graph
            .get_functions()
            .into_iter()
            .filter(|f| f.file_path.contains(module) || f.qualified_name.starts_with(module))
            .map(|f| f.name)
            .collect();

        // Check which are used in the content
        module_symbols
            .into_iter()
            .filter(|sym| content.contains(sym))
            .take(10)
            .collect()
    }
}

impl Detector for WildcardImportsDetector {
    fn name(&self) -> &'static str {
        "wildcard-imports"
    }
    fn description(&self) -> &'static str {
        "Detects wildcard imports"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];

        for path in files.files_with_extensions(&["py", "js", "ts", "java"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            if let Some(content) = files.content(path) {
                let lines: Vec<&str> = content.lines().collect();
                for (i, line) in lines.iter().enumerate() {
                    let prev_line = if i > 0 { Some(lines[i - 1]) } else { None };
                    if crate::detectors::is_line_suppressed(line, prev_line) {
                        continue;
                    }

                    if wildcard_pattern().is_match(line) {
                        // Skip relative wildcard imports in __init__.py -- standard re-export pattern
                        let is_init_py = path.file_name()
                            .and_then(|n| n.to_str())
                            .map(|n| n == "__init__.py")
                            .unwrap_or(false);
                        if is_init_py {
                            // ALL wildcard imports in __init__.py are re-exports
                            // This is the standard Python convention for package namespaces
                            continue;
                        }

                        // Try to extract module name and find used symbols
                        let module_name = Self::extract_module_name(line);
                        let used_symbols = module_name
                            .as_ref()
                            .map(|m| Self::find_used_symbols(&content, m, graph))
                            .unwrap_or_default();

                        let mut notes = Vec::new();
                        if !used_symbols.is_empty() {
                            notes.push(format!(
                                "📊 Actually used: {}",
                                used_symbols
                                    .iter()
                                    .take(5)
                                    .cloned()
                                    .collect::<Vec<_>>()
                                    .join(", ")
                            ));
                            if used_symbols.len() > 5 {
                                notes.push(format!("   ... and {} more", used_symbols.len() - 5));
                            }
                        }

                        let context_notes = if notes.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                        };

                        let suggestion = if !used_symbols.is_empty() {
                            let imports = used_symbols
                                .iter()
                                .take(10)
                                .cloned()
                                .collect::<Vec<_>>()
                                .join(", ");
                            if let Some(ref module) = module_name {
                                format!("Replace with explicit imports:\n```python\nfrom {} import {}\n```", 
                                       module, imports)
                            } else {
                                format!("Import only what's needed: {}", imports)
                            }
                        } else {
                            "Import specific names instead.".to_string()
                        };

                        findings.push(Finding {
                            id: String::new(),
                            detector: "WildcardImportsDetector".to_string(),
                            severity: Severity::Low,
                            title: format!(
                                "Wildcard import{}",
                                module_name
                                    .as_ref()
                                    .map(|m| format!(": {}", m))
                                    .unwrap_or_default()
                            ),
                            description: format!(
                                "Wildcard imports pollute namespace and hide dependencies.{}",
                                context_notes
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some((i + 1) as u32),
                            line_end: Some((i + 1) as u32),
                            suggested_fix: Some(suggestion),
                            estimated_effort: Some("5 minutes".to_string()),
                            category: Some("code-quality".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Makes code harder to understand and refactor. \
                                 Tools can't determine where names come from."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "WildcardImportsDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detects_wildcard_import() {
        let store = GraphStore::in_memory();
        let detector = WildcardImportsDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("app.py", "from os.path import *\n\nresult = join(\"/tmp\", \"file.txt\")\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect wildcard import. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_explicit_import() {
        let store = GraphStore::in_memory();
        let detector = WildcardImportsDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("app.py", "from os.path import join, exists\n\nresult = join(\"/tmp\", \"file.txt\")\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag explicit imports. Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_relative_import_in_init_py() {
        let store = GraphStore::in_memory();
        let detector = WildcardImportsDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("__init__.py", "from .models import *\nfrom .views import *\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag relative wildcard imports in __init__.py (re-export pattern). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_no_finding_for_absolute_import_in_init_py() {
        let store = GraphStore::in_memory();
        let detector = WildcardImportsDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("__init__.py", "from django.db.models.fields import *\nfrom os.path import *\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not flag ANY wildcard imports in __init__.py (all are re-exports). Found: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_still_detects_wildcard_in_regular_file() {
        let store = GraphStore::in_memory();
        let detector = WildcardImportsDetector::new("/mock/repo");
        let mock_files = crate::detectors::file_provider::MockFileProvider::new(vec![
            ("app.py", "from os.path import *\nresult = join('/tmp', 'file')\n"),
        ]);
        let findings = detector.detect(&store, &mock_files).unwrap();
        assert!(!findings.is_empty(), "Should still detect wildcard in regular files");
    }
}
