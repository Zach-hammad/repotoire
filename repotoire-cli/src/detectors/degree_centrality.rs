//! Degree centrality detector
//!
//! Uses in-degree and out-degree to detect coupling issues.
//! Now enhanced with function context for smarter role-based detection.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::{FunctionContextMap, FunctionRole};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::sync::Arc;
use tracing::debug;
use uuid::Uuid;

/// Detects coupling issues using degree centrality.
///
/// Degree centrality measures direct connections:
/// - In-degree: How many functions call this function
/// - Out-degree: How many functions this function calls
///
/// Now uses FunctionContext to make smarter decisions:
/// - Utility functions: High fan-in expected, only flag if also high fan-out
/// - Orchestrators: High fan-out expected, only flag if also high fan-in
/// - Hubs: Both high fan-in and fan-out - genuine coupling problems
pub struct DegreeCentralityDetector {
    #[allow(dead_code)] // Stored for future config access
    config: DetectorConfig,
    /// Complexity threshold for severity escalation
    high_complexity_threshold: u32,
    /// Minimum total degree for coupling hotspot
    min_total_degree: usize,
    /// Minimum fan-in to be considered elevated
    min_elevated_fanin: usize,
    /// Minimum fan-out to be considered elevated
    min_elevated_fanout: usize,
}

impl DegreeCentralityDetector {
    /// Create a new detector with default config
    pub fn new() -> Self {
        Self {
            config: DetectorConfig::new(),
            high_complexity_threshold: 15,
            min_total_degree: 50,      // Raised from 30 (too noisy for C code)
            min_elevated_fanin: 15,    // Raised from 8
            min_elevated_fanout: 15,   // Raised from 8
        }
    }

    /// Create with custom config
    #[allow(dead_code)] // Builder pattern method
    pub fn with_config(config: DetectorConfig) -> Self {
        // Apply coupling multiplier to thresholds (higher multiplier = more lenient)
        let multiplier = config.coupling_multiplier;
        Self {
            high_complexity_threshold: ((config.get_option_or("high_complexity_threshold", 15) as f64) * multiplier) as u32,
            min_total_degree: ((config.get_option_or("min_total_degree", 30) as f64) * multiplier) as usize,
            min_elevated_fanin: ((config.get_option_or("min_elevated_fanin", 8) as f64) * multiplier) as usize,
            min_elevated_fanout: ((config.get_option_or("min_elevated_fanout", 8) as f64) * multiplier) as usize,
            config,
        }
    }

    /// Calculate severity based on metrics and function role
    fn calculate_severity(&self, fan_in: usize, fan_out: usize, role: FunctionRole) -> Severity {
        let total = fan_in + fan_out;

        // Base severity from raw metrics (raised thresholds for C code)
        let base_severity = if total >= 100 && fan_in >= 25 && fan_out >= 25 {
            Severity::High
        } else if total >= 70 {
            Severity::Medium
        } else {
            Severity::Low
        };

        // Adjust based on function role
        match role {
            FunctionRole::Utility => {
                // Utilities are expected to have high fan-in
                // Only flag if fan-out is also problematic
                if fan_out < self.min_elevated_fanout * 2 {
                    Severity::Low // Expected behavior
                } else {
                    base_severity.min(Severity::Medium)
                }
            }
            FunctionRole::Orchestrator => {
                // Orchestrators are expected to have high fan-out
                // Only flag if fan-in is also problematic
                if fan_in < self.min_elevated_fanin * 2 {
                    Severity::Low // Expected behavior
                } else {
                    base_severity.min(Severity::Medium)
                }
            }
            FunctionRole::Leaf => {
                // Leaf functions shouldn't have high coupling
                base_severity.min(Severity::Medium)
            }
            FunctionRole::Test => Severity::Low,
            FunctionRole::Hub => {
                // Hubs are genuine coupling concerns
                base_severity
            }
            FunctionRole::EntryPoint | FunctionRole::Unknown => base_severity,
        }
    }

    /// Utility function patterns (designed to be highly connected)
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

    /// Legacy name-based skip check (fallback when no context available)
    fn should_skip_by_name(&self, name: &str) -> bool {
        let name_lower = name.to_lowercase();
        
        // Skip utility function prefixes (designed to be called everywhere)
        if Self::UTILITY_PREFIXES.iter().any(|p| name_lower.starts_with(p)) {
            return true;
        }

        // Skip utility function suffixes
        if name_lower.ends_with("_util") || name_lower.ends_with("_utils") 
            || name_lower.ends_with("_helper") || name_lower.ends_with("_common")
            || name_lower.ends_with("_cb") || name_lower.ends_with("_callback")
            || name_lower.ends_with("_handler") || name_lower.ends_with("_hook") {
            return true;
        }

        // Detect runtime/interpreter naming convention: short_prefix + underscore
        // Examples: u3r_word, Py_Initialize, lua_pushnil, rb_str_new
        if Self::has_runtime_prefix(name) {
            return true;
        }

        const SKIP_NAMES: &[&str] = &[
            "new",
            "default",
            "from",
            "into",
            "create",
            "build",
            "make",
            "with",
            "clone",
            "drop",
            "fmt",
            "eq",
            "hash",
            "cmp",
            "partial_cmp",
            "get",
            "set",
            "instance",
            "global",
            "shared",
            "current",
            "run",
            "main",
            "init",
            "setup",
            "start",
            "execute",
            "dispatch",
            "handle",
            "read",
            "write",
            "parse",
            "format",
            "render",
            "display",
            "detect",
            "analyze",
            "iter",
            "next",
            "map",
            "filter",
            "fold",
            "is_",
            "has_",
            "check_",
            "validate_",
            "should_",
            "can_",
            "find_",
            "calculate_",
            "compute_",
            "scan_",
            "extract_",
            "normalize_",
        ];

        let name_lower = name.to_lowercase();
        SKIP_NAMES.iter().any(|&skip| {
            name_lower == skip
                || name_lower.starts_with(&format!("{}_", skip))
                || name_lower.starts_with(skip)
        })
    }

    /// Detect common runtime/interpreter naming patterns
    /// Pattern: 2-4 alphanumeric prefix + underscore (e.g., u3r_, Py_, lua_, rb_)
    fn has_runtime_prefix(func_name: &str) -> bool {
        if let Some(underscore_pos) = func_name.find('_') {
            if (2..=4).contains(&underscore_pos) {
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

    /// Check if path is a natural hub file or utility location
    fn is_hub_file(&self, path: &str) -> bool {
        const SKIP_PATHS: &[&str] = &[
            // Natural hub files (entry points, module roots)
            "/mod.rs",
            "/lib.rs",
            "/main.rs",
            "/index.",
            "/cli/",
            "/handlers/",
            "/server.",
            "/router.",
            // Utility directories (expected to be highly connected)
            "/util/",
            "/utils/",
            "/common/",
            "/core/",
            "/lib/",
            "/helpers/",
            "/shared/",
            "/internal/",
            // Runtime/memory (naturally called from everywhere)
            "/allocator/",
            "/memory/",
            "/alloc/",
            "/runtime/",
        ];
        let path_lower = path.to_lowercase();
        SKIP_PATHS.iter().any(|&pat| path_lower.contains(pat))
    }

    /// Create a coupling finding
    fn create_finding(
        &self,
        name: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        fan_in: usize,
        fan_out: usize,
        role: FunctionRole,
    ) -> Finding {
        let severity = self.calculate_severity(fan_in, fan_out, role);
        let total = fan_in + fan_out;

        let role_note = match role {
            FunctionRole::Utility => " (utility - high fan-in expected)",
            FunctionRole::Orchestrator => " (orchestrator - high fan-out expected)",
            FunctionRole::Hub => " (architectural hub)",
            _ => "",
        };

        let title = format!("High Coupling: {}{}", name, role_note);

        let description = format!(
            "Function '{}' has {} connections ({} callers, {} callees). \
            High coupling increases change risk.\n\n\
            **Analysis:**\n\
            - In-degree (callers): {}\n\
            - Out-degree (callees): {}\n\
            - Total coupling: {}",
            name, total, fan_in, fan_out, fan_in, fan_out, total
        );

        let suggested_fix = match role {
            FunctionRole::Utility => "This utility is more coupled than expected. Consider:\n\
                - Breaking into smaller, focused helpers\n\
                - Reducing its dependencies on other modules"
                .to_string(),
            FunctionRole::Hub => "This is a coupling hotspot. Consider:\n\
                - Introducing abstraction layers\n\
                - Applying facade pattern\n\
                - Splitting by responsibility"
                .to_string(),
            _ => {
                "Consider breaking into smaller functions or using dependency injection".to_string()
            }
        };

        Finding {
            id: Uuid::new_v4().to_string(),
            detector: "DegreeCentralityDetector".to_string(),
            severity,
            title,
            description,
            affected_files: vec![file_path.into()],
            line_start: Some(line_start),
            line_end: Some(line_end),
            suggested_fix: Some(suggested_fix),
            estimated_effort: Some("Medium (2-4 hours)".to_string()),
            category: Some("coupling".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Highly coupled code is harder to change and test. Changes cascade unpredictably."
                    .to_string(),
            ),
            ..Default::default()
        }
    }
}

impl Default for DegreeCentralityDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for DegreeCentralityDetector {
    fn name(&self) -> &'static str {
        "DegreeCentralityDetector"
    }

    fn description(&self) -> &'static str {
        "Detects coupling issues using degree centrality"
    }

    fn category(&self) -> &'static str {
        "coupling"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    /// Legacy detection without context
    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        for func in graph.get_functions() {
            // Skip by name or hub file
            if self.should_skip_by_name(&func.name) || self.is_hub_file(&func.file_path) {
                continue;
            }

            let fan_in = graph.call_fan_in(&func.qualified_name);
            let fan_out = graph.call_fan_out(&func.qualified_name);
            let total_degree = fan_in + fan_out;

            // Skip expected patterns
            if fan_in > 20 && fan_out < 5 {
                continue; // Utility pattern
            }
            if fan_out > 20 && fan_in < 5 {
                continue; // Orchestrator pattern
            }

            // Only flag when BOTH are elevated
            if total_degree >= self.min_total_degree
                && fan_in >= self.min_elevated_fanin
                && fan_out >= self.min_elevated_fanout
            {
                findings.push(self.create_finding(
                    &func.name,
                    &func.file_path,
                    func.line_start,
                    func.line_end,
                    fan_in,
                    fan_out,
                    FunctionRole::Unknown,
                ));
            }
        }

        Ok(findings)
    }

    /// Whether this detector uses function context
    fn uses_context(&self) -> bool {
        true
    }

    /// Enhanced detection with function context
    fn detect_with_context(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        contexts: &Arc<FunctionContextMap>,
    ) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        let funcs = graph.get_functions();

        debug!(
            "DegreeCentralityDetector: analyzing {} functions with context",
            funcs.len()
        );

        for func in funcs {
            // Skip common utility/trait method names
            if self.should_skip_by_name(&func.name) {
                continue;
            }

            let ctx = contexts.get(&func.qualified_name);

            // Skip test functions
            if let Some(c) = ctx {
                if c.is_test || c.role == FunctionRole::Test {
                    continue;
                }
            }

            // Skip hub files (even with context)
            if self.is_hub_file(&func.file_path) {
                continue;
            }

            let (fan_in, fan_out, role) = if let Some(c) = ctx {
                (c.in_degree, c.out_degree, c.role)
            } else {
                let fan_in = graph.call_fan_in(&func.qualified_name);
                let fan_out = graph.call_fan_out(&func.qualified_name);
                (fan_in, fan_out, FunctionRole::Unknown)
            };

            let total_degree = fan_in + fan_out;

            // Role-aware filtering
            match role {
                FunctionRole::Utility => {
                    // Utilities can have high fan-in, only flag if extreme
                    if fan_out < self.min_elevated_fanout || total_degree < 50 {
                        continue;
                    }
                }
                FunctionRole::Orchestrator => {
                    // Orchestrators can have high fan-out, only flag if extreme
                    if fan_in < self.min_elevated_fanin || total_degree < 50 {
                        continue;
                    }
                }
                FunctionRole::Leaf => {
                    // Leaf functions shouldn't have high coupling - flag if elevated
                    if total_degree < self.min_total_degree {
                        continue;
                    }
                }
                FunctionRole::Hub => {
                    // Hubs are expected to have high coupling, but still flag extreme cases
                    if total_degree < self.min_total_degree * 2 {
                        continue;
                    }
                }
                _ => {
                    // Default: require both elevated
                    if total_degree < self.min_total_degree
                        || fan_in < self.min_elevated_fanin
                        || fan_out < self.min_elevated_fanout
                    {
                        continue;
                    }
                }
            }

            findings.push(self.create_finding(
                &func.name,
                &func.file_path,
                func.line_start,
                func.line_end,
                fan_in,
                fan_out,
                role,
            ));
        }

        debug!(
            "DegreeCentralityDetector: found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_new_detector() {
        let detector = DegreeCentralityDetector::new();
        assert_eq!(detector.high_complexity_threshold, 15);
        assert_eq!(detector.min_elevated_fanin, 15);  // Raised from 8
    }

    #[test]
    fn test_severity_with_role() {
        let detector = DegreeCentralityDetector::new();

        // Utility with high fan-in but low fan-out = Low severity (expected behavior)
        let sev = detector.calculate_severity(50, 5, FunctionRole::Utility);
        assert_eq!(sev, Severity::Low);

        // Hub with medium both = Low (total=80, needs 100+ for High)
        let sev = detector.calculate_severity(40, 40, FunctionRole::Hub);
        assert_eq!(sev, Severity::Medium);

        // Hub with very high both = High (total=100, fan_in=50, fan_out=50)
        let sev = detector.calculate_severity(50, 50, FunctionRole::Hub);
        assert_eq!(sev, Severity::High);
    }

    #[test]
    fn test_skip_by_name() {
        let detector = DegreeCentralityDetector::new();

        assert!(detector.should_skip_by_name("is_valid"));
        assert!(detector.should_skip_by_name("new"));
        assert!(detector.should_skip_by_name("get"));
        assert!(!detector.should_skip_by_name("process_orders"));
    }
}
