//! Deep Nesting Detector
//!
//! Graph-enhanced detection of excessive nesting depth.
//! Uses graph to:
//! - Find the containing function and its role
//! - Identify callees that could be extracted
//! - Reduce severity for entry points/handlers

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::{Path, PathBuf};
use tracing::info;

pub struct DeepNestingDetector {
    repository_path: PathBuf,
    max_findings: usize,
    threshold: usize,
    default_threshold: usize,
    resolver: crate::calibrate::ThresholdResolver,
}

impl DeepNestingDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
            threshold: 4,
            default_threshold: 4,
            resolver: Default::default(),
        }
    }

    /// Create with adaptive threshold resolver
    pub fn with_resolver(
        repository_path: impl Into<PathBuf>,
        resolver: &crate::calibrate::ThresholdResolver,
    ) -> Self {
        use crate::calibrate::MetricKind;
        let default_threshold = 4usize;
        let threshold = resolver.warn_usize(MetricKind::NestingDepth, default_threshold);
        if threshold != default_threshold {
            tracing::info!(
                "DeepNesting: adaptive threshold {} (default={})",
                threshold,
                default_threshold
            );
        }
        Self {
            repository_path: repository_path.into(),
            max_findings: 100,
            threshold,
            default_threshold,
            resolver: resolver.clone(),
        }
    }

    /// Find the function containing this line
    fn find_containing_function(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
        line: u32,
    ) -> Option<crate::graph::CodeNode> {
        graph
            .get_functions()
            .into_iter()
            .find(|f| f.file_path == file_path && f.line_start <= line && f.line_end >= line)
    }

    /// Check if function is an entry point (handlers need more nesting)
    fn is_entry_point(name: &str, file_path: &str) -> bool {
        let entry_patterns = [
            "handle",
            "route",
            "endpoint",
            "view",
            "controller",
            "main",
            "run",
        ];
        let entry_paths = [
            "/handlers/",
            "/routes/",
            "/views/",
            "/controllers/",
            "/api/",
        ];

        entry_patterns
            .iter()
            .any(|p| name.to_lowercase().contains(p))
            || entry_paths.iter().any(|p| file_path.contains(p))
    }

    /// Find callees at deep nesting that could be extracted
    fn find_extraction_candidates(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        func_qn: &str,
    ) -> Vec<String> {
        let callees = graph.get_callees(func_qn);

        // Find callees that are called only from this function (private helpers)
        // These are good extraction candidates
        callees
            .into_iter()
            .filter(|c| {
                let callers = graph.get_callers(&c.qualified_name);
                callers.len() == 1 // Only called from this function
            })
            .map(|c| c.name)
            .take(3)
            .collect()
    }
}

impl Detector for DeepNestingDetector {
    fn name(&self) -> &'static str {
        "deep-nesting"
    }
    fn description(&self) -> &'static str {
        "Detects excessive nesting depth"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, _files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = vec![];
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
            if !matches!(
                ext,
                "py" | "js" | "ts" | "jsx" | "tsx" | "rs" | "go" | "java" | "cs" | "cpp" | "c"
            ) {
                continue;
            }

            // Skip detector files (they have inherently complex parsing logic)
            let path_str_check = path.to_string_lossy();
            if path_str_check.contains("/detectors/") {
                continue;
            }

            // Skip parsers (parsing code naturally has deep nesting)
            if path_str_check.contains("/parsers/") {
                continue;
            }

            // Skip non-production paths
            if crate::detectors::content_classifier::is_non_production_path(&path_str_check) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().content(path) {
                let path_str = path.to_string_lossy().to_string();
                let mut max_depth = 0;
                let mut current_depth = 0;
                let mut max_line = 0;

                for (i, line) in content.lines().enumerate() {
                    for ch in line.chars() {
                        if ch == '{' {
                            current_depth += 1;
                            if current_depth > max_depth {
                                max_depth = current_depth;
                                max_line = i + 1;
                            }
                        } else if ch == '}' && current_depth > 0 {
                            current_depth -= 1;
                        }
                    }
                }

                if max_depth > self.threshold {
                    // === Graph-enhanced analysis ===
                    let containing_func =
                        self.find_containing_function(graph, &path_str, max_line as u32);

                    let (func_name, is_entry, complexity, extraction_candidates) =
                        if let Some(func) = &containing_func {
                            let is_entry = Self::is_entry_point(&func.name, &func.file_path);
                            let complexity = func.complexity().unwrap_or(1);
                            let candidates =
                                self.find_extraction_candidates(graph, &func.qualified_name);
                            (Some(func.name.clone()), is_entry, complexity, candidates)
                        } else {
                            (None, false, 1, vec![])
                        };

                    // Adjust severity based on context
                    let mut severity = if max_depth > 8 {
                        Severity::High
                    } else {
                        Severity::Medium
                    };

                    // Entry points/handlers get slightly reduced severity
                    if is_entry {
                        severity = match severity {
                            Severity::High => Severity::Medium,
                            _ => Severity::Low,
                        };
                    }

                    // Build analysis notes
                    let mut notes = Vec::new();

                    if let Some(ref name) = func_name {
                        notes.push(format!("📍 In function: `{}`", name));
                    }
                    if is_entry {
                        notes.push("🚪 Entry point/handler (reduced severity)".to_string());
                    }
                    if complexity > 10 {
                        notes.push(format!(
                            "⚠️ High complexity: {} (nesting compounds this)",
                            complexity
                        ));
                    }
                    if !extraction_candidates.is_empty() {
                        notes.push(format!(
                            "💡 Existing helpers that could reduce nesting: {}",
                            extraction_candidates.join(", ")
                        ));
                    }

                    let context_notes = if notes.is_empty() {
                        String::new()
                    } else {
                        format!("\n\n**Analysis:**\n{}", notes.join("\n"))
                    };

                    // Build smart suggestion
                    let suggestion = if let Some(first_candidate) = extraction_candidates.first() {
                        format!(
                            "This function already has helpers like `{}`. Consider:\n\
                             1. Extract more nested blocks into similar helpers\n\
                             2. Use guard clauses (early returns) to reduce nesting\n\
                             3. Replace nested ifs with switch/match",
                            first_candidate
                        )
                    } else if max_depth > 6 {
                        "Severely nested code. Apply multiple techniques:\n\
                         1. Guard clauses: `if (!condition) return;`\n\
                         2. Extract Method: pull nested blocks into functions\n\
                         3. Replace conditionals with polymorphism\n\
                         4. Use functional patterns (map/filter instead of nested loops)"
                            .to_string()
                    } else {
                        "Extract nested logic into functions or use early returns.".to_string()
                    };

                    // Build threshold explainability metadata
                    let explanation = self.resolver.explain(
                        crate::calibrate::MetricKind::NestingDepth,
                        max_depth as f64,
                        self.default_threshold as f64,
                    );
                    let threshold_metadata: std::collections::HashMap<String, String> =
                        explanation.to_metadata().into_iter().collect();

                    findings.push(Finding {
                        id: String::new(),
                        detector: "DeepNestingDetector".to_string(),
                        severity,
                        title: format!(
                            "Excessive nesting: {} levels{}",
                            max_depth,
                            func_name.map(|n| format!(" in {}", n)).unwrap_or_default()
                        ),
                        description: format!(
                            "{} levels of nesting (threshold: {}).{}\n\n📊 {}",
                            max_depth,
                            self.threshold,
                            context_notes,
                            explanation.to_note()
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(max_line as u32),
                        line_end: Some(max_line as u32),
                        suggested_fix: Some(suggestion),
                        estimated_effort: Some(if max_depth > 6 {
                            "1 hour".to_string()
                        } else {
                            "30 minutes".to_string()
                        }),
                        category: Some("complexity".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(
                            "Deep nesting makes code hard to read and maintain. \
                             Each level increases cognitive load exponentially."
                                .to_string(),
                        ),
                        threshold_metadata,
                        ..Default::default()
                    });
                }
            }
        }

        info!(
            "DeepNestingDetector found {} findings (graph-aware)",
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
    fn test_detects_deep_nesting() {
        let dir = tempfile::tempdir().unwrap();
        // The detector counts { and } characters, threshold is 4, so >4 means 5+ levels.
        let file = dir.path().join("nested.py");
        std::fs::write(
            &file,
            r#"def process(data):
    if True {
        if True {
            if True {
                if True {
                    if True {
                        print("deeply nested")
                    }
                }
            }
        }
    }
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = DeepNestingDetector::new(dir.path());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).unwrap();
        assert!(
            !findings.is_empty(),
            "Should detect deep nesting with 5 levels of braces"
        );
        assert!(
            findings[0].title.contains("nesting"),
            "Title should mention nesting, got: {}",
            findings[0].title
        );
    }

    #[test]
    fn test_no_finding_for_shallow_nesting() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("shallow.py");
        // Only 2 levels of braces - well below threshold of 4
        std::fs::write(
            &file,
            r#"def process(data):
    result = {"key": "value"}
    if True {
        print("ok")
    }
"#,
        )
        .unwrap();

        let store = GraphStore::in_memory();
        let detector = DeepNestingDetector::new(dir.path());
        let empty_files = crate::detectors::file_provider::MockFileProvider::new(vec![]);
        let findings = detector.detect(&store, &empty_files).unwrap();
        assert!(
            findings.is_empty(),
            "Should not detect deep nesting for shallow code, but got: {:?}",
            findings.iter().map(|f| &f.title).collect::<Vec<_>>()
        );
    }
}
