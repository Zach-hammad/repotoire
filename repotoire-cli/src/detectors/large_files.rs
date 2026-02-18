//! Large Files Detector
//!
//! Graph-enhanced detection of overly large files:
//! - Count functions and classes in the file
//! - Analyze coupling (how many other files depend on this)
//! - Suggest split points based on function groupings

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::info;

pub struct LargeFilesDetector {
    repository_path: PathBuf,
    max_findings: usize,
    threshold: usize,
}

impl LargeFilesDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
            threshold: 800,
        }
    }

    /// Apply adaptive thresholds from a style profile.
    pub fn with_style_profile(mut self, profile: &crate::calibrate::StyleProfile) -> Self {
        use crate::calibrate::MetricKind;
        if let Some(dist) = profile.get(MetricKind::FileLength) {
            if dist.confident {
                let adaptive = dist.p90.ceil() as usize;
                if adaptive > self.threshold {
                    tracing::info!(
                        "LargeFiles: adaptive threshold {} (from p90={:.0}, default={})",
                        adaptive, dist.p90, self.threshold
                    );
                    self.threshold = adaptive;
                }
            }
        }
        self
    }

    /// Analyze file structure using graph
    fn analyze_file_structure(
        graph: &dyn crate::graph::GraphQuery,
        file_path: &str,
    ) -> FileAnalysis {
        let functions: Vec<_> = graph
            .get_functions()
            .into_iter()
            .filter(|f| f.file_path == file_path)
            .collect();

        let func_count = functions.len();

        // Count unique files that import from this file
        let mut importers: HashSet<String> = HashSet::new();
        for func in &functions {
            for caller in graph.get_callers(&func.qualified_name) {
                if caller.file_path != file_path {
                    importers.insert(caller.file_path.clone());
                }
            }
        }

        // Find the largest function
        let largest_func = functions
            .iter()
            .map(|f| (f.name.clone(), f.line_end.saturating_sub(f.line_start)))
            .max_by_key(|(_, size)| *size);

        // Group functions by prefix to suggest split points
        let mut prefixes: HashSet<String> = HashSet::new();
        for func in &functions {
            if let Some(prefix) = func.name.split('_').next() {
                if prefix.len() > 2 && func.name.contains('_') {
                    prefixes.insert(prefix.to_string());
                }
            }
        }

        FileAnalysis {
            func_count,
            importer_count: importers.len(),
            largest_func,
            potential_modules: prefixes.into_iter().take(5).collect(),
        }
    }
}

struct FileAnalysis {
    func_count: usize,
    importer_count: usize,
    largest_func: Option<(String, u32)>,
    potential_modules: Vec<String>,
}

impl Detector for LargeFilesDetector {
    fn name(&self) -> &'static str {
        "large-files"
    }
    fn description(&self) -> &'static str {
        "Detects files exceeding size threshold"
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
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

            let path_str = path.to_string_lossy().to_string();

            // Skip vendor/generated
            if path_str.contains("vendor")
                || path_str.contains("node_modules")
                || path_str.contains("generated")
                || path_str.contains(".min.")
            {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(
                ext,
                "py" | "js"
                    | "ts"
                    | "jsx"
                    | "tsx"
                    | "rs"
                    | "go"
                    | "java"
                    | "cs"
                    | "cpp"
                    | "c"
                    | "h"
                    | "rb"
                    | "php"
            ) {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines = content.lines().count();
                if lines > self.threshold {
                    let analysis = Self::analyze_file_structure(graph, &path_str);

                    // Calculate severity based on size and coupling
                    let severity = if lines > 2000 || analysis.importer_count > 10 {
                        Severity::High
                    } else if lines > 1000 || analysis.importer_count > 5 {
                        Severity::Medium
                    } else {
                        Severity::Low
                    };

                    // Build notes
                    let mut notes = Vec::new();
                    notes.push(format!("📏 {} lines", lines));
                    if analysis.func_count > 0 {
                        notes.push(format!("📦 {} functions", analysis.func_count));
                    }
                    if analysis.importer_count > 0 {
                        notes.push(format!(
                            "🔗 {} files depend on this",
                            analysis.importer_count
                        ));
                    }
                    if let Some((name, size)) = &analysis.largest_func {
                        notes.push(format!("📐 Largest function: `{}` ({} lines)", name, size));
                    }

                    let context_notes = format!("\n\n**Analysis:**\n{}", notes.join("\n"));

                    // Build split suggestion
                    let suggestion = if !analysis.potential_modules.is_empty() {
                        format!(
                            "Consider splitting by function prefix into separate modules:\n\n\
                             {}\n\n\
                             ```python\n\
                             # {}_utils.py - extract {}_* functions\n\
                             # {}_core.py - extract core logic\n\
                             ```",
                            analysis
                                .potential_modules
                                .iter()
                                .map(|p| format!("• `{}_*` functions → `{}.py`", p, p))
                                .collect::<Vec<_>>()
                                .join("\n"),
                            analysis
                                .potential_modules
                                .first()
                                .unwrap_or(&"module".to_string()),
                            analysis
                                .potential_modules
                                .first()
                                .unwrap_or(&"module".to_string()),
                            path.file_stem().and_then(|s| s.to_str()).unwrap_or("file")
                        )
                    } else {
                        "Split into smaller, focused modules. Group related functions together."
                            .to_string()
                    };

                    let effort = if lines > 1000 {
                        "2-4 hours"
                    } else {
                        "1-2 hours"
                    };

                    findings.push(Finding {
                        id: String::new(),
                        detector: "LargeFilesDetector".to_string(),
                        severity,
                        title: format!("Large file: {} lines", lines),
                        description: format!(
                            "File exceeds recommended size ({} lines > {} threshold).{}",
                            lines, self.threshold, context_notes
                        ),
                        affected_files: vec![path.to_path_buf()],
                        line_start: Some(1),
                        line_end: Some(lines as u32),
                        suggested_fix: Some(suggestion),
                        estimated_effort: Some(effort.to_string()),
                        category: Some("maintainability".to_string()),
                        cwe_id: None,
                        why_it_matters: Some(if analysis.importer_count > 5 {
                            "This file is a dependency hub - many other files import from it. \
                             Large dependency hubs are hard to refactor and create merge conflicts."
                                .to_string()
                        } else {
                            "Large files are harder to understand, test, and maintain. \
                             They often indicate that the module has too many responsibilities."
                                .to_string()
                        }),
                        ..Default::default()
                    });
                }
            }
        }

        // Sort by line count (largest first)
        findings.sort_by(|a, b| b.line_end.cmp(&a.line_end));

        info!(
            "LargeFilesDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}
