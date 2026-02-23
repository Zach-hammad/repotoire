//! Feature Envy Detector
//!
//! Graph-aware detection of methods that use other classes more than their own.
//! Uses FunctionContext to distinguish legitimate patterns from actual smells.
//!
//! Example:
//! ```text
//! class Order {
//!     fn calculate_discount(&self, customer: &Customer) {
//!         // Uses customer data 10 times, own data 1 time
//!         if customer.loyalty_level() > 5 &&
//!            customer.total_purchases() > 1000 &&
//!            customer.is_premium() { ... }
//!     }
//! }
//! ```
//! This method should probably be on Customer, not Order.
//!
//! Graph-aware enhancements:
//! - Skip Utility functions (expected to be used across modules)
//! - Skip Orchestrator functions (their job is to coordinate)
//! - Skip Facade patterns (high out-degree, few callers from each module)
//! - Reduce severity for Hub functions (they bridge modules intentionally)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::{FunctionContext, FunctionContextMap, FunctionRole};
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::{debug, info};

/// Thresholds for feature envy detection
#[derive(Debug, Clone)]
pub struct FeatureEnvyThresholds {
    /// Minimum ratio of external to internal uses
    pub threshold_ratio: f64,
    /// Minimum external uses to trigger detection
    pub min_external_uses: usize,
    /// Ratio for critical severity
    pub critical_ratio: f64,
    /// Minimum uses for critical severity
    pub critical_min_uses: usize,
    /// Ratio for high severity
    pub high_ratio: f64,
    /// Minimum uses for high severity
    pub high_min_uses: usize,
    /// Ratio for medium severity
    pub medium_ratio: f64,
    /// Minimum uses for medium severity
    pub medium_min_uses: usize,
}

impl Default for FeatureEnvyThresholds {
    fn default() -> Self {
        Self {
            threshold_ratio: 4.0,  // Increased from 3.0
            min_external_uses: 25, // Increased from 15
            critical_ratio: 10.0,
            critical_min_uses: 50, // Increased from 30
            high_ratio: 6.0,       // Increased from 5.0
            high_min_uses: 35,     // Increased from 20
            medium_ratio: 4.0,     // Increased from 3.0
            medium_min_uses: 20,   // Increased from 10
        }
    }
}

/// Detects methods with feature envy
pub struct FeatureEnvyDetector {
    #[allow(dead_code)] // Stored for future config access
    config: DetectorConfig,
    thresholds: FeatureEnvyThresholds,
    /// Function context for graph-aware analysis
    function_contexts: Option<FunctionContextMap>,
}

impl FeatureEnvyDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(FeatureEnvyThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: FeatureEnvyThresholds) -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds,
            function_contexts: None,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        // Apply coupling multiplier to thresholds (higher multiplier = more lenient)
        let multiplier = config.coupling_multiplier;
        let thresholds = FeatureEnvyThresholds {
            threshold_ratio: config.get_option_or("threshold_ratio", 3.0) * multiplier,
            min_external_uses: ((config.get_option_or("min_external_uses", 15) as f64) * multiplier)
                as usize,
            critical_ratio: config.get_option_or("critical_ratio", 10.0) * multiplier,
            critical_min_uses: ((config.get_option_or("critical_min_uses", 30) as f64) * multiplier)
                as usize,
            high_ratio: config.get_option_or("high_ratio", 5.0) * multiplier,
            high_min_uses: ((config.get_option_or("high_min_uses", 20) as f64) * multiplier)
                as usize,
            medium_ratio: config.get_option_or("medium_ratio", 3.0) * multiplier,
            medium_min_uses: ((config.get_option_or("medium_min_uses", 10) as f64) * multiplier)
                as usize,
        };

        Self {
            config,
            thresholds,
            function_contexts: None,
        }
    }

    /// Set function contexts for graph-aware analysis
    pub fn with_function_contexts(mut self, contexts: FunctionContextMap) -> Self {
        self.function_contexts = Some(contexts);
        self
    }

    /// Class name patterns that indicate orchestrator classes.
    /// Methods inside these classes are expected to call many external services.
    const ORCHESTRATOR_CLASS_PATTERNS: &'static [&'static str] = &[
        "controller",
        "router",
        "handler",
        "dispatcher",
        "orchestrator",
        "coordinator",
        "mediator",
        "presenter",
        "endpoint",
        "resolver",
        "middleware",
        "viewset",
    ];

    /// File path patterns indicating orchestrator directories
    const ORCHESTRATOR_PATH_PATTERNS: &'static [&'static str] = &[
        "/controllers/",
        "/controller/",
        "/routers/",
        "/router/",
        "/handlers/",
        "/handler/",
        "/dispatchers/",
        "/endpoints/",
        "/resolvers/",
        "/middleware/",
        "/routes/",
        "/viewsets/",
        "/views/",
    ];

    /// Check if a function belongs to an orchestrator class based on its qualified name or file path.
    /// Orchestrator classes (controllers, routers, handlers) are expected to call many external services.
    fn is_in_orchestrator_class(&self, qualified_name: &str, file_path: &str) -> bool {
        // Check qualified name for orchestrator class patterns
        // Qualified names look like "module::ClassName::method" or "file::ClassName.method"
        let qn_lower = qualified_name.to_lowercase();
        for pattern in Self::ORCHESTRATOR_CLASS_PATTERNS {
            if qn_lower.contains(pattern) {
                debug!(
                    "Skipping method in orchestrator class (name pattern '{}'): {}",
                    pattern, qualified_name
                );
                return true;
            }
        }

        // Check file path for orchestrator directory patterns
        let path_lower = file_path.to_lowercase();
        for pattern in Self::ORCHESTRATOR_PATH_PATTERNS {
            if path_lower.contains(pattern) {
                debug!(
                    "Skipping method in orchestrator path ('{}'): {}",
                    pattern, qualified_name
                );
                return true;
            }
        }

        false
    }

    /// Check if function should be skipped based on role
    fn should_skip_by_role(&self, qualified_name: &str) -> bool {
        if let Some(ref contexts) = self.function_contexts {
            if let Some(ctx) = contexts.get(qualified_name) {
                match ctx.role {
                    // Utilities are EXPECTED to be called from many modules
                    FunctionRole::Utility => {
                        debug!("Skipping utility function: {}", ctx.name);
                        return true;
                    }
                    // Orchestrators coordinate many functions by design
                    FunctionRole::Orchestrator => {
                        debug!("Skipping orchestrator function: {}", ctx.name);
                        return true;
                    }
                    // Test functions can call whatever they need
                    FunctionRole::Test => {
                        return true;
                    }
                    _ => {}
                }
            }
        }
        false
    }

    /// Get severity multiplier from function context
    fn get_severity_multiplier(&self, qualified_name: &str) -> f64 {
        if let Some(ref contexts) = self.function_contexts {
            if let Some(ctx) = contexts.get(qualified_name) {
                return ctx.severity_multiplier();
            }
        }
        1.0
    }

    /// Check if this is a facade pattern (high out-degree, delegates to many modules)
    fn is_facade_pattern(&self, qualified_name: &str) -> bool {
        if let Some(ref contexts) = self.function_contexts {
            if let Some(ctx) = contexts.get(qualified_name) {
                // Facade: high out-degree, moderate caller modules, low complexity
                // It delegates work rather than implementing logic
                let is_delegator = ctx.out_degree >= 5 && ctx.callee_modules >= 3;
                let is_low_complexity = ctx.complexity.unwrap_or(1) <= 5;
                if is_delegator && is_low_complexity {
                    debug!(
                        "Detected facade pattern: {} (out={}, callee_mods={})",
                        ctx.name, ctx.out_degree, ctx.callee_modules
                    );
                    return true;
                }
            }
        }
        false
    }

    /// Calculate severity based on ratio and uses
    fn calculate_severity(&self, ratio: f64, external_uses: usize) -> Severity {
        if ratio >= self.thresholds.critical_ratio
            && external_uses >= self.thresholds.critical_min_uses
        {
            Severity::Critical
        } else if ratio >= self.thresholds.high_ratio
            && external_uses >= self.thresholds.high_min_uses
        {
            Severity::High
        } else if ratio >= self.thresholds.medium_ratio
            && external_uses >= self.thresholds.medium_min_uses
        {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Estimate effort based on severity
    fn estimate_effort(&self, severity: Severity) -> String {
        match severity {
            Severity::Critical => "Large (2-4 hours)".to_string(),
            Severity::High => "Medium (1-2 hours)".to_string(),
            Severity::Medium => "Small (30-60 minutes)".to_string(),
            Severity::Low | Severity::Info => "Small (15-30 minutes)".to_string(),
        }
    }

    /// Create a finding for feature envy
    fn create_finding(
        &self,
        _method_name: String,
        method_simple: String,
        owner_class: String,
        file_path: String,
        line_start: Option<u32>,
        line_end: Option<u32>,
        internal_uses: usize,
        external_uses: usize,
    ) -> Finding {
        let ratio = if internal_uses > 0 {
            external_uses as f64 / internal_uses as f64
        } else {
            f64::INFINITY
        };

        let severity = self.calculate_severity(ratio, external_uses);

        let suggestion = if internal_uses == 0 {
            format!(
                "Method '{}' uses external classes {} times but never uses its own class. \
                 Consider moving this method to the class it uses most, \
                 or making it a standalone utility function.",
                method_simple, external_uses
            )
        } else {
            format!(
                "Method '{}' uses external classes {} times vs its own class {} times (ratio: {:.1}x). \
                 Consider moving to the most-used external class or refactoring \
                 to reduce external dependencies.",
                method_simple, external_uses, internal_uses, ratio
            )
        };

        let ratio_display = if ratio.is_infinite() {
            "∞".to_string()
        } else {
            format!("{:.1}", ratio)
        };

        Finding {
            id: String::new(),
            detector: "FeatureEnvyDetector".to_string(),
            severity,
            title: format!("Feature Envy: {}", method_simple),
            description: format!(
                "Method '{}' in class '{}' shows feature envy by using external classes \
                 {} times compared to {} internal uses (ratio: {}x).\n\n\
                 This suggests the method may belong in a different class.",
                method_simple,
                owner_class.split('.').next_back().unwrap_or(&owner_class),
                external_uses,
                internal_uses,
                ratio_display
            ),
            affected_files: vec![PathBuf::from(&file_path)],
            line_start,
            line_end,
            suggested_fix: Some(suggestion),
            estimated_effort: Some(self.estimate_effort(severity)),
            category: Some("code_smell".to_string()),
            cwe_id: None,
            why_it_matters: Some(
                "Feature envy indicates a method is in the wrong place. \
                 Moving it to the class it actually operates on improves cohesion, \
                 reduces coupling, and makes the code easier to understand and maintain."
                    .to_string(),
            ),
            ..Default::default()
        }
    }
}

impl Default for FeatureEnvyDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for FeatureEnvyDetector {
    fn name(&self) -> &'static str {
        "FeatureEnvyDetector"
    }

    fn description(&self) -> &'static str {
        "Detects methods that use other classes more than their own"
    }

    fn category(&self) -> &'static str {
        "code_smell"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }
    fn detect(&self, graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Skip orchestrator/dispatch function names - these are EXPECTED to call many external functions
        const ORCHESTRATOR_NAMES: &[&str] = &[
            "run",
            "main",
            "execute",
            "dispatch",
            "process",
            "handle",
            "build",
            "create",
            "new",
            "init",
            "setup",
            "configure",
            "detect",
            "analyze",
            "parse",
            "render",
            "format",
            "report",
            "convert",
            "transform",
            "serialize",
            "deserialize",
            "encode",
            "decode",
            "route",
            "validate",
        ];

        // Skip utility function prefixes (designed to work across modules)
        const UTILITY_PREFIXES: &[&str] = &[
            // Generic utility prefixes
            "util_",
            "helper_",
            "common_",
            "core_",
            "base_",
            "lib_",
            "shared_",
            // Memory/string operations
            "alloc_",
            "free_",
            "mem_",
            "str_",
            "buf_",
            "fmt_",
            // Common interpreter/runtime prefixes
            "py_",
            "pyobject_",
            "_py", // CPython
            "lua_",
            "lual_",
            "luav_", // Lua
            "rb_",
            "ruby_", // Ruby
            "v8_",
            "js_", // JavaScript engines
            "g_",
            "gtk_",
            "gdk_", // GLib/GTK
            "uv_",
            "uv__", // libuv
        ];

        // Skip utility function suffixes
        const UTILITY_SUFFIXES: &[&str] =
            &["_util", "_utils", "_helper", "_common", "_lib", "_impl"];

        // Skip files that are naturally orchestration points or utilities
        const SKIP_PATHS: &[&str] = &[
            "/cli/",
            "/handlers/",
            "/main.rs",
            "/mod.rs",
            "/lib.rs",
            "/index.",
            "/util/",
            "/utils/",
            "/common/",
            "/core/",
            "/lib/",
            "/helpers/",
            "/shared/",
            "/internal/",
            "/runtime/",
            "/allocator/",
            "/memory/",
        ];

        for func in graph.get_functions() {
            // Skip test functions (they naturally access many things for fixtures)
            if func.name.starts_with("test_") || func.file_path.contains("/tests/") {
                continue;
            }

            // === Graph-aware role-based filtering ===
            if self.should_skip_by_role(&func.qualified_name) {
                continue;
            }

            // Skip facade patterns (intentional delegation)
            if self.is_facade_pattern(&func.qualified_name) {
                continue;
            }

            // Skip methods in orchestrator classes (controllers, routers, handlers, dispatchers)
            // These classes delegate to services by design — flagging them is a false positive
            if self.is_in_orchestrator_class(&func.qualified_name, &func.file_path) {
                continue;
            }

            // Skip orchestrator functions by name (fallback for no context)
            let name_lower = func.name.to_lowercase();
            if ORCHESTRATOR_NAMES
                .iter()
                .any(|&pat| name_lower == pat || name_lower.starts_with(&format!("{}_", pat)))
            {
                continue;
            }

            // Skip utility functions by prefix
            if UTILITY_PREFIXES.iter().any(|&p| name_lower.starts_with(p)) {
                continue;
            }

            // Skip utility functions by suffix
            if UTILITY_SUFFIXES.iter().any(|&s| name_lower.ends_with(s)) {
                continue;
            }

            // Skip utility/orchestrator files
            let path_lower = func.file_path.to_lowercase();
            if SKIP_PATHS.iter().any(|&pat| path_lower.contains(pat)) {
                continue;
            }

            let callees = graph.get_callees(&func.qualified_name);
            if callees.is_empty() {
                continue;
            }

            // Count calls to own file vs other files
            // Also track which modules are being called
            let own_file = &func.file_path;
            let _own_module = own_file.rsplit('/').nth(1).unwrap_or("");
            let mut internal_calls = 0;
            let mut external_calls = 0;
            let mut external_modules: HashSet<String> = HashSet::new();

            for callee in &callees {
                if callee.file_path == *own_file {
                    internal_calls += 1;
                } else {
                    external_calls += 1;
                    // Track the external module
                    if let Some(module) = callee.file_path.rsplit('/').nth(1) {
                        external_modules.insert(module.to_string());
                    }
                }
            }

            // === Enhanced feature envy detection ===
            // Original: external > internal * 3 && external >= 15 && internal > 0
            // New: Also check module concentration - if calling many modules, it's likely orchestration

            let is_concentrated = external_modules.len() <= 2; // Calls mostly 1-2 modules
            let high_external = external_calls > internal_calls * 3 && external_calls >= 15;

            if high_external && internal_calls > 0 && is_concentrated {
                let ratio = external_calls as f64 / (internal_calls + 1) as f64;
                let mut severity = if ratio > 8.0 && external_calls >= 25 {
                    Severity::High
                } else if ratio > 5.0 && external_calls >= 15 {
                    Severity::Medium
                } else {
                    Severity::Low
                };

                // Apply role-based severity multiplier
                let multiplier = self.get_severity_multiplier(&func.qualified_name);
                if multiplier < 1.0 {
                    severity = match severity {
                        Severity::Critical => Severity::High,
                        Severity::High => Severity::Medium,
                        Severity::Medium => Severity::Low,
                        _ => Severity::Low,
                    };
                }

                // Build suggestion with target module
                let target_module = external_modules.iter().next().cloned().unwrap_or_default();
                let suggestion = if is_concentrated && !target_module.is_empty() {
                    format!("Consider moving '{}' to the '{}' module where most of its dependencies live", 
                            func.name, target_module)
                } else {
                    "Consider moving this function to the class it uses most".to_string()
                };

                findings.push(Finding {
                    id: String::new(),
                    detector: "FeatureEnvyDetector".to_string(),
                    severity,
                    title: format!("Feature Envy: {}", func.name),
                    description: format!(
                        "Function '{}' calls {} external functions (in {} modules) but only {} internal.\n\n\
                         Primary external dependency: {}",
                        func.name, external_calls, external_modules.len(), internal_calls,
                        if is_concentrated { target_module.as_str() } else { "scattered across modules" }
                    ),
                    affected_files: vec![func.file_path.clone().into()],
                    line_start: Some(func.line_start),
                    line_end: Some(func.line_end),
                    suggested_fix: Some(suggestion),
                    estimated_effort: Some("Medium (1-2 hours)".to_string()),
                    category: Some("coupling".to_string()),
                    cwe_id: None,
                    why_it_matters: Some(
                        "Feature envy indicates misplaced functionality. Moving the function \
                         to its natural home reduces coupling and improves cohesion.".to_string()
                    ),
                    ..Default::default()
                });
            }
        }

        info!(
            "FeatureEnvyDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_thresholds() {
        let detector = FeatureEnvyDetector::new();
        assert!((detector.thresholds.threshold_ratio - 4.0).abs() < f64::EPSILON);
        assert_eq!(detector.thresholds.min_external_uses, 25);
    }

    #[test]
    fn test_severity_calculation() {
        let detector = FeatureEnvyDetector::new();

        // Low (below thresholds)
        assert_eq!(detector.calculate_severity(2.0, 10), Severity::Low);

        // Medium (ratio >= 4.0 && uses >= 20)
        assert_eq!(detector.calculate_severity(4.0, 20), Severity::Medium);

        // High (ratio >= 6.0 && uses >= 35)
        assert_eq!(detector.calculate_severity(6.0, 35), Severity::High);

        // Critical (ratio >= 10.0 && uses >= 50)
        assert_eq!(detector.calculate_severity(10.0, 50), Severity::Critical);
    }

    #[test]
    fn test_orchestrator_class_detection_by_name() {
        let detector = FeatureEnvyDetector::new();

        // Methods in controller classes should be detected as orchestrators
        assert!(detector.is_in_orchestrator_class("app::UserController::get_stats", "src/api.py"));
        assert!(detector.is_in_orchestrator_class("app::RequestHandler::process", "src/server.py"));
        assert!(detector.is_in_orchestrator_class("events::EventDispatcher::emit", "src/events.py"));
        assert!(detector.is_in_orchestrator_class("api::ApiRouter::get_user", "src/routes.py"));
        assert!(detector.is_in_orchestrator_class("gql::QueryResolver::resolve", "src/graphql.py"));

        // Regular classes should NOT be detected
        assert!(!detector.is_in_orchestrator_class("app::OrderService::calculate", "src/services/orders.py"));
        assert!(!detector.is_in_orchestrator_class("models::User::validate", "src/models.py"));
    }

    #[test]
    fn test_orchestrator_class_detection_by_path() {
        let detector = FeatureEnvyDetector::new();

        // Methods in orchestrator directories should be detected
        assert!(detector.is_in_orchestrator_class("app::Users::index", "src/controllers/users.py"));
        assert!(detector.is_in_orchestrator_class("app::Auth::login", "src/handlers/auth.ts"));
        assert!(detector.is_in_orchestrator_class("api::Items::list", "src/endpoints/items.py"));
        assert!(detector.is_in_orchestrator_class("app::Logging::call", "src/middleware/logging.py"));

        // Non-orchestrator paths should NOT be detected
        assert!(!detector.is_in_orchestrator_class("app::Order::save", "src/models/order.py"));
        assert!(!detector.is_in_orchestrator_class("app::Auth::hash", "src/services/auth.py"));
    }
}
