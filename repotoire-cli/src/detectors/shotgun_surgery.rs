//! Shotgun Surgery Detector
//!
//! Graph-aware detection of classes/functions where changes propagate widely.
//! Uses call graph to trace actual impact of modifications.
//!
//! Detection criteria:
//! - High fan-in (many callers)
//! - Callers spread across many files/modules
//! - Changes would cascade through call graph

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use tracing::{debug, info};
use uuid::Uuid;

/// Thresholds for shotgun surgery detection
#[derive(Debug, Clone)]
pub struct ShotgunSurgeryThresholds {
    /// Minimum callers to consider
    pub min_callers: usize,
    /// Minimum unique files for medium severity
    pub medium_files: usize,
    /// Minimum unique files for high severity
    pub high_files: usize,
    /// Minimum unique modules for critical
    pub critical_modules: usize,
}

impl Default for ShotgunSurgeryThresholds {
    fn default() -> Self {
        Self {
            min_callers: 5,
            medium_files: 3,
            high_files: 5,
            critical_modules: 4,
        }
    }
}

pub struct ShotgunSurgeryDetector {
    config: DetectorConfig,
    thresholds: ShotgunSurgeryThresholds,
}

impl ShotgunSurgeryDetector {
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds: ShotgunSurgeryThresholds::default(),
        }
    }

    #[allow(dead_code)] // Builder pattern method
    pub fn with_config(config: DetectorConfig) -> Self {
        // Apply coupling multiplier to thresholds (higher multiplier = more lenient)
        let multiplier = config.coupling_multiplier;
        let thresholds = ShotgunSurgeryThresholds {
            min_callers: ((config.get_option_or("min_callers", 5) as f64) * multiplier) as usize,
            medium_files: ((config.get_option_or("medium_files", 3) as f64) * multiplier) as usize,
            high_files: ((config.get_option_or("high_files", 5) as f64) * multiplier) as usize,
            critical_modules: ((config.get_option_or("critical_modules", 4) as f64) * multiplier) as usize,
        };
        Self { config, thresholds }
    }

    /// Analyze impact of changing a class
    fn analyze_class_impact(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        class: &crate::graph::CodeNode,
    ) -> Option<ImpactAnalysis> {
        let functions = graph.get_functions();

        // Find all methods belonging to this class
        let methods: Vec<_> = functions
            .iter()
            .filter(|f| {
                f.file_path == class.file_path
                    && f.line_start >= class.line_start
                    && f.line_end <= class.line_end
            })
            .collect();

        // Collect all external callers of all methods
        let mut all_callers: HashSet<String> = HashSet::new();
        let mut caller_files: HashSet<String> = HashSet::new();
        let mut caller_modules: HashSet<String> = HashSet::new();

        for method in &methods {
            for caller in graph.get_callers(&method.qualified_name) {
                // Skip internal callers (same class)
                if caller.file_path == class.file_path
                    && caller.line_start >= class.line_start
                    && caller.line_end <= class.line_end
                {
                    continue;
                }

                all_callers.insert(caller.qualified_name.clone());
                caller_files.insert(caller.file_path.clone());
                caller_modules.insert(Self::extract_module(&caller.file_path));
            }
        }

        if all_callers.len() < self.thresholds.min_callers {
            return None;
        }

        // Trace cascading impact (callers of callers)
        let cascade_depth = self.trace_cascade_depth(graph, &all_callers, 0);

        Some(ImpactAnalysis {
            direct_callers: all_callers.len(),
            affected_files: caller_files.len(),
            affected_modules: caller_modules.len(),
            cascade_depth,
            sample_files: caller_files.iter().take(5).cloned().collect(),
        })
    }

    /// Trace how far changes cascade through the call graph
    fn trace_cascade_depth(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        callers: &HashSet<String>,
        depth: usize,
    ) -> usize {
        if depth >= 3 || callers.is_empty() {
            return depth;
        }

        let mut next_level: HashSet<String> = HashSet::new();
        for caller_qn in callers {
            for upstream in graph.get_callers(caller_qn) {
                if !callers.contains(&upstream.qualified_name) {
                    next_level.insert(upstream.qualified_name.clone());
                }
            }
        }

        if next_level.is_empty() {
            depth
        } else {
            self.trace_cascade_depth(graph, &next_level, depth + 1)
        }
    }

    fn extract_module(file_path: &str) -> String {
        std::path::Path::new(file_path)
            .parent()
            .and_then(|p| p.to_str())
            .unwrap_or("root")
            .to_string()
    }

    fn calculate_severity(&self, analysis: &ImpactAnalysis) -> Severity {
        if analysis.affected_modules >= self.thresholds.critical_modules {
            Severity::Critical
        } else if analysis.affected_files >= self.thresholds.high_files {
            Severity::High
        } else if analysis.affected_files >= self.thresholds.medium_files {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Detect common runtime/interpreter naming patterns
    /// Pattern: 2-4 alphanumeric prefix + underscore (e.g., u3r_, Py_, lua_, rb_)
    fn has_runtime_prefix(func_name: &str) -> bool {
        if let Some(underscore_pos) = func_name.find('_') {
            if underscore_pos >= 2 && underscore_pos <= 4 {
                let prefix = &func_name[..underscore_pos];
                if prefix.chars().all(|c| c.is_alphanumeric()) {
                    let prefix_lower = prefix.to_lowercase();
                    const COMMON_WORDS: &[&str] = &[
                        "get", "set", "is", "do", "can", "has", "new", "old", "add", "del",
                        "pop", "put", "run", "try", "end", "use", "for", "the", "and", "not",
                        "dead", "live", "test", "mock", "fake", "stub", "temp", "tmp", "foo",
                        "bar", "baz", "qux", "call", "read", "load", "save", "send", "recv",
                    ];
                    if !COMMON_WORDS.contains(&prefix_lower.as_str()) {
                        return true;
                    }
                }
            }
        }
        false
    }
}

struct ImpactAnalysis {
    direct_callers: usize,
    affected_files: usize,
    affected_modules: usize,
    cascade_depth: usize,
    sample_files: Vec<String>,
}

impl Default for ShotgunSurgeryDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for ShotgunSurgeryDetector {
    fn name(&self) -> &'static str {
        "ShotgunSurgeryDetector"
    }

    fn description(&self) -> &'static str {
        "Detects code where changes propagate widely"
    }

    fn category(&self) -> &'static str {
        "coupling"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        for class in graph.get_classes() {
            // Skip interfaces
            if class.qualified_name.contains("::interface::") {
                continue;
            }

            let analysis = match self.analyze_class_impact(graph, &class) {
                Some(a) => a,
                None => continue,
            };

            let severity = self.calculate_severity(&analysis);

            let cascade_note = if analysis.cascade_depth > 1 {
                format!(
                    "\n\n**Cascade Analysis:** Changes propagate {} levels deep through the call graph.",
                    analysis.cascade_depth
                )
            } else {
                String::new()
            };

            let sample_list = analysis.sample_files.join("\n  - ");
            let more_note = if analysis.affected_files > 5 {
                format!("\n  ... and {} more files", analysis.affected_files - 5)
            } else {
                String::new()
            };

            findings.push(Finding {
                id: Uuid::new_v4().to_string(),
                detector: "ShotgunSurgeryDetector".to_string(),
                severity,
                title: format!("Shotgun Surgery Risk: {}", class.name),
                description: format!(
                    "Class '{}' is called by **{} functions** across **{} files** in **{} modules**.\n\n\
                     Any change to this class requires updates throughout the codebase.{}\n\n\
                     **Affected files (sample):**\n  - {}{}",
                    class.name,
                    analysis.direct_callers,
                    analysis.affected_files,
                    analysis.affected_modules,
                    cascade_note,
                    sample_list,
                    more_note
                ),
                affected_files: vec![class.file_path.clone().into()],
                line_start: Some(class.line_start),
                line_end: Some(class.line_end),
                suggested_fix: Some("Options to reduce coupling:\n\
                     1. Create a Facade to limit the API surface\n\
                     2. Use interfaces/protocols to decouple\n\
                     3. Split into smaller, focused classes\n\
                     4. Apply Dependency Injection pattern".to_string()),
                estimated_effort: Some(match severity {
                    Severity::Critical => "Large (1-2 days)",
                    Severity::High => "Large (4-8 hours)",
                    _ => "Medium (2-4 hours)",
                }.to_string()),
                category: Some("coupling".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Shotgun surgery means a single change requires editing many files. \
                     This increases the chance of missing something and introducing bugs."
                        .to_string()
                ),
                ..Default::default()
            });
        }

        // Also check high-impact functions (not just classes)
        // Skip common trait methods that are expected to have many callers
        // Also skip utility function prefixes (these are DESIGNED to be called everywhere)
        const UTILITY_PREFIXES: &[&str] = &[
            // Generic utility prefixes
            "util_", "helper_", "common_", "core_", "base_", "lib_", "shared_",
            // Memory/allocation functions (core runtime, called everywhere)
            "alloc_", "free_", "malloc_", "realloc_", "mem_",
            // Logging/debug (called from everywhere)
            "log_", "debug_", "trace_", "info_", "warn_", "error_", "print_",
            // String/buffer operations
            "str_", "buf_", "fmt_",
            // Common interpreter/runtime prefixes
            "py_", "pyobject_", "_py",  // CPython
            "lua_", "lual_", "luav_",   // Lua
            "rb_", "ruby_",             // Ruby
            "v8_", "js_",               // JavaScript engines
            "g_", "gtk_", "gdk_",       // GLib/GTK
            "uv_", "uv__",              // libuv
        ];
        const UTILITY_SUFFIXES: &[&str] = &["_util", "_utils", "_helper", "_common", "_lib", "_impl"];
        const UTILITY_PATHS: &[&str] = &[
            "/util/", "/utils/", "/common/", "/core/", "/lib/", "/helpers/", "/shared/",
            "/allocator/", "/memory/", "/alloc/", "/runtime/", "/internal/",
        ];
        const SKIP_METHODS: &[&str] = &[
            "new",
            "default",
            "from",
            "into",
            "from_str",
            "to_string",
            "as_str",
            "as_ref",
            "as_mut",
            "clone",
            "fmt",
            "eq",
            "cmp",
            "hash",
            "next",
            "iter",
            "into_iter",
            "len",
            "is_empty",
            "get",
            "set",
            "with_",
            "build",
            "parse",
            "serialize",
            "deserialize",
            "drop",
            "deref",
            "as_i64",
            "as_f64",
            "as_bool",
            "as_array",
            "as_object", // JSON accessors
        ];

        for func in graph.get_functions() {
            // Skip common trait implementations
            let name_lower = func.name.to_lowercase();
            if SKIP_METHODS
                .iter()
                .any(|m| name_lower == *m || name_lower.starts_with(m))
            {
                continue;
            }

            // Skip utility functions by prefix (designed to be called everywhere)
            if UTILITY_PREFIXES.iter().any(|p| name_lower.starts_with(p)) {
                continue;
            }

            // Skip runtime/interpreter functions (short prefix + underscore pattern)
            if Self::has_runtime_prefix(&func.name) {
                continue;
            }

            // Skip utility functions by suffix
            if UTILITY_SUFFIXES.iter().any(|s| name_lower.ends_with(s)) 
                || name_lower.ends_with("_cb") || name_lower.ends_with("_callback")
                || name_lower.ends_with("_handler") || name_lower.ends_with("_hook") {
                continue;
            }

            // Skip functions in utility paths
            let path_lower = func.file_path.to_lowercase();
            if UTILITY_PATHS.iter().any(|p| path_lower.contains(p)) {
                continue;
            }

            let callers = graph.get_callers(&func.qualified_name);
            if callers.len() < self.thresholds.min_callers * 2 {
                continue;
            }

            let _caller_files: HashSet<_> = callers.iter().map(|c| &c.file_path).collect();
            let caller_modules: HashSet<_> = callers
                .iter()
                .map(|c| Self::extract_module(&c.file_path))
                .collect();

            if caller_modules.len() >= self.thresholds.critical_modules {
                findings.push(Finding {
                    id: Uuid::new_v4().to_string(),
                    detector: "ShotgunSurgeryDetector".to_string(),
                    severity: Severity::High,
                    title: format!("High-Impact Function: {}", func.name),
                    description: format!(
                        "Function '{}' is called from {} places across {} modules.\n\n\
                         Changes will have wide-reaching effects.",
                        func.name,
                        callers.len(),
                        caller_modules.len()
                    ),
                    affected_files: vec![func.file_path.clone().into()],
                    line_start: Some(func.line_start),
                    line_end: Some(func.line_end),
                    suggested_fix: Some(
                        "Consider creating wrapper functions or using dependency injection"
                            .to_string(),
                    ),
                    estimated_effort: Some("Medium (2-4 hours)".to_string()),
                    category: Some("coupling".to_string()),
                    cwe_id: None,
                    why_it_matters: Some(
                        "High-impact functions require careful change management".to_string(),
                    ),
                    ..Default::default()
                });
            }
        }

        findings.sort_by(|a, b| b.severity.cmp(&a.severity));
        info!("ShotgunSurgeryDetector found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphStore};

    #[test]
    fn test_detect_shotgun_surgery() {
        let graph = GraphStore::in_memory();

        // Create a class with many external callers
        graph.add_node(
            CodeNode::class("SharedService", "src/shared.py")
                .with_qualified_name("shared::SharedService")
                .with_lines(1, 50),
        );

        graph.add_node(
            CodeNode::function("do_work", "src/shared.py")
                .with_qualified_name("shared::SharedService::do_work")
                .with_lines(10, 20),
        );

        // Add callers from multiple files
        for i in 0..10 {
            let file = format!("src/module_{}.py", i);
            let caller = format!("caller_{}", i);
            graph.add_node(
                CodeNode::function(&caller, &file)
                    .with_qualified_name(&format!("module_{}::{}", i, caller))
                    .with_lines(1, 10),
            );

            graph.add_edge_by_name(
                &format!("module_{}::{}", i, caller),
                "shared::SharedService::do_work",
                CodeEdge::calls(),
            );
        }

        let detector = ShotgunSurgeryDetector::new();
        let findings = detector.detect(&graph).unwrap();

        assert!(!findings.is_empty());
        assert!(findings[0].title.contains("SharedService"));
    }
}
