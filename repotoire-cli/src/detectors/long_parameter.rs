//! Long parameter list detector
//!
//! Graph-enhanced detection of functions with too many parameters.
//!
//! Uses graph analysis to:
//! - Identify constructors/factories (reduce severity - they legitimately need many params)
//! - Detect delegation patterns (function passes most params to callee)
//! - Find builder pattern implementations (acceptable)
//! - Check if DataClumps exist for the parameters
//!
//! FP-reduction strategies:
//! - Constructor/builder/factory: double the threshold (they legitimately take many params)
//! - Hub/Orchestrator role: increase threshold by 50%
//! - Handler functions: increase threshold by 50% (request + response + context)
//! - Test functions: cap severity at Low
//! - Unreachable non-public: reduce severity one level
//! - Trait impl methods: reduce severity (can't control signature)
//! - Delegator/wrapper: reduce severity (just forwarding)
//!
//! Detection indicates:
//! - The function is doing too much (violates SRP)
//! - Related parameters should be grouped into objects
//! - The function has poor API design

use crate::calibrate::MetricKind;
use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::function_context::FunctionRole;
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::{debug, info};

/// Thresholds for long parameter list detection
#[derive(Debug, Clone)]
pub struct LongParameterThresholds {
    /// Parameters above this count are flagged
    pub max_params: usize,
    /// Parameters at this count trigger high severity
    pub high_params: usize,
    /// Parameters at this count trigger critical severity
    pub critical_params: usize,
}

impl Default for LongParameterThresholds {
    fn default() -> Self {
        Self {
            max_params: 5,
            high_params: 7,
            critical_params: 10,
        }
    }
}

/// Parameters to exclude from counting
static SKIP_PARAMS: &[&str] = &["self", "cls"];

/// Name patterns indicating constructors or factory functions where many
/// parameters are legitimate and the threshold should be doubled.
static CONSTRUCTOR_PATTERNS: &[&str] = &[
    "new",
    "create",
    "build",
    "builder",
    "make",
    "init",
    "initialize",
    "from_",
    "with_",
    "__init__",
    "constructor",
    "setup",
    "configure",
    "register",
    "install",
];

/// Builder method prefixes — skip entirely (builder API by design).
static BUILDER_PREFIXES: &[&str] = &["with_", "set_", "add_", "build"];

/// Detects functions with too many parameters
pub struct LongParameterListDetector {
    #[allow(dead_code)] // Stored for future config access
    config: DetectorConfig,
    thresholds: LongParameterThresholds,
    #[allow(dead_code)] // Config field for parameter exclusion
    skip_params: HashSet<String>,
}

impl LongParameterListDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(LongParameterThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: LongParameterThresholds) -> Self {
        let skip_params: HashSet<String> = SKIP_PARAMS.iter().map(|s| s.to_string()).collect();

        Self {
            config: DetectorConfig::new(),
            thresholds,
            skip_params,
        }
    }

    /// Create with custom config
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = LongParameterThresholds {
            max_params: config.get_option_or(
                "max_params",
                config.adaptive.warn_usize(MetricKind::ParameterCount, 5),
            ),
            high_params: config.get_option_or(
                "high_params",
                config.adaptive.high_usize(MetricKind::ParameterCount, 7),
            ),
            critical_params: config.get_option_or("critical_params", 10),
        };

        let skip_params: HashSet<String> = SKIP_PARAMS.iter().map(|s| s.to_string()).collect();

        Self {
            config,
            thresholds,
            skip_params,
        }
    }

    /// Extract meaningful parameter names (excluding self/cls)
    #[allow(dead_code)] // Helper for graph-based parameter analysis
    fn get_meaningful_params(&self, params: &[serde_json::Value]) -> Vec<String> {
        params
            .iter()
            .filter_map(|p| {
                let name = if p.is_string() {
                    p.as_str().map(|s| s.to_string())
                } else if let Some(obj) = p.as_object() {
                    obj.get("name")
                        .and_then(|n| n.as_str())
                        .map(|s| s.to_string())
                } else {
                    None
                };

                name.filter(|n| !self.skip_params.contains(n))
            })
            .collect()
    }

    // ── Effective threshold computation ──────────────────────────────

    /// Compute the effective parameter threshold for a function,
    /// incorporating constructor, hub, orchestrator, and handler
    /// adjustments.
    fn effective_threshold(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
        qn: &str,
        name_lower: &str,
        base: usize,
    ) -> (usize, Vec<&'static str>) {
        let mut threshold = base;
        let mut reasons: Vec<&'static str> = Vec::new();

        // 1. Constructor/factory/builder: double the threshold
        if Self::is_constructor_like(name_lower) {
            threshold *= 2;
            reasons.push("Constructor/factory pattern (threshold doubled)");
        }

        // 2. Hub or Orchestrator role: increase by 50%
        if let Some(role) = ctx.function_role(qn) {
            if matches!(role, FunctionRole::Hub | FunctionRole::Orchestrator) {
                threshold = threshold * 3 / 2;
                reasons.push("Hub/Orchestrator function (threshold +50%)");
            }
        }

        // 3. Handler functions: increase by 50% (request + response + context)
        if ctx.is_handler(qn) {
            threshold = threshold * 3 / 2;
            reasons.push("Handler function (threshold +50%)");
        }

        (threshold, reasons)
    }

    /// Check if a function name matches constructor/factory patterns.
    fn is_constructor_like(name_lower: &str) -> bool {
        CONSTRUCTOR_PATTERNS
            .iter()
            .any(|p| name_lower.starts_with(p) || name_lower == *p)
    }

    /// Check if a function name matches builder method patterns.
    fn is_builder_method(name_lower: &str) -> bool {
        BUILDER_PREFIXES.iter().any(|p| name_lower.starts_with(p))
    }

    /// Check if a function is a trait impl method (Rust `impl Trait for Type`).
    ///
    /// Trait impl methods can't control their parameter count — the trait
    /// defines the signature. Qualified names contain `impl<TraitName for Type>`.
    fn is_trait_impl(qn: &str) -> bool {
        // Rust parser encodes: `path::impl<Trait for Type>::method:line`
        qn.contains("impl<") && qn.contains(" for ")
    }

    /// Check if a function delegates most of its parameters to a single callee.
    fn is_delegator(
        graph: &dyn crate::graph::GraphQuery,
        qn: &str,
        param_count: usize,
    ) -> bool {
        let callees = graph.get_callees(qn);
        callees.iter().any(|callee| {
            let callee_params = callee.param_count_opt().unwrap_or(0) as usize;
            callee_params >= param_count.saturating_sub(2)
        })
    }

    // ── Severity helpers ─────────────────────────────────────────────

    /// Calculate base severity from parameter count relative to effective
    /// thresholds.
    fn calculate_severity(&self, param_count: usize, effective_threshold: usize) -> Severity {
        // Scale high/critical thresholds proportionally to the effective
        // threshold so that when the threshold is raised (e.g. doubled for
        // constructors), the severity bands shift accordingly.
        let scale = effective_threshold as f64 / self.thresholds.max_params.max(1) as f64;
        let high = (self.thresholds.high_params as f64 * scale).ceil() as usize;
        let critical = (self.thresholds.critical_params as f64 * scale).ceil() as usize;

        if param_count >= critical {
            Severity::Critical
        } else if param_count >= high {
            Severity::High
        } else if param_count > effective_threshold {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Apply post-hoc severity reductions for context-specific patterns.
    ///
    /// Returns the adjusted severity and any notes about reductions applied.
    fn apply_severity_reductions(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
        qn: &str,
        base_severity: Severity,
        is_delegator: bool,
        is_trait_impl: bool,
    ) -> (Severity, Vec<String>) {
        let mut severity = base_severity;
        let mut notes = Vec::new();

        // Delegator/wrapper pattern: reduce one level
        if is_delegator {
            severity = Self::reduce_severity(severity);
            notes.push("Delegates to callee (wrapper pattern, reduced severity)".to_string());
        }

        // Trait impl methods can't control their parameter count
        if is_trait_impl {
            severity = Self::reduce_severity(severity);
            notes.push("Trait impl method (signature fixed by trait, reduced severity)".to_string());
        }

        // Test functions: cap severity at Low
        if ctx.is_test_function(qn) {
            severity = Severity::Low;
            notes.push("Test function (severity capped at Low)".to_string());
        }

        // Unreachable + non-public: reduce one level
        if !ctx.is_reachable(qn) && !ctx.is_public_api(qn) {
            severity = Self::reduce_severity(severity);
            notes.push("Unreachable non-public function (reduced severity)".to_string());
        }

        (severity, notes)
    }

    /// Reduce severity by one level (Critical -> High -> Medium -> Low).
    fn reduce_severity(s: Severity) -> Severity {
        match s {
            Severity::Critical => Severity::High,
            Severity::High => Severity::Medium,
            Severity::Medium | Severity::Low | Severity::Info => Severity::Low,
        }
    }

    // ── Suggestion / description helpers ─────────────────────────────

    /// Generate a suggested config class name
    #[allow(dead_code)] // Helper for graph-based parameter analysis
    fn suggest_config_name(&self, func_name: &str, params: &[String]) -> String {
        // Try to derive from function name
        if let Some(base) = func_name.strip_prefix("create_") {
            return format!("{}Config", to_pascal_case(base));
        }
        if let Some(base) = func_name.strip_prefix("init_") {
            return format!("{}Options", to_pascal_case(base));
        }
        if let Some(base) = func_name.strip_prefix("initialize_") {
            return format!("{}Options", to_pascal_case(base));
        }
        if let Some(base) = func_name.strip_prefix("process_") {
            return format!("{}Params", to_pascal_case(base));
        }
        if let Some(base) = func_name.strip_prefix("configure_") {
            return format!("{}Config", to_pascal_case(base));
        }

        // Look for common parameter patterns
        let param_set: HashSet<&str> = params.iter().map(|s| s.as_str()).collect();

        if param_set.contains("host") && param_set.contains("port") {
            return "ConnectionConfig".to_string();
        }
        if param_set.contains("url") && param_set.contains("timeout") {
            return "ConnectionConfig".to_string();
        }
        if param_set.contains("username") && param_set.contains("password") {
            return "Credentials".to_string();
        }
        if param_set.contains("width") && param_set.contains("height") {
            return "Dimensions".to_string();
        }
        if param_set.contains("x") && param_set.contains("y") {
            return "Position".to_string();
        }
        if param_set.contains("start") && param_set.contains("end") {
            return "Range".to_string();
        }

        // Default: use function name
        format!("{}Config", to_pascal_case(func_name))
    }

    /// Estimate effort based on parameter count
    fn estimate_effort(&self, param_count: usize) -> String {
        if param_count >= 12 {
            "Large (1-2 days)".to_string()
        } else if param_count >= 8 {
            "Medium (4-8 hours)".to_string()
        } else if param_count >= 6 {
            "Small (2-4 hours)".to_string()
        } else {
            "Small (1 hour)".to_string()
        }
    }

    /// Build contextual suggestion based on function patterns.
    fn build_suggestion(is_constructor: bool, is_delegator: bool, is_trait_impl: bool) -> String {
        if is_trait_impl {
            "This function's signature is fixed by a trait. Consider:\n\
             1. Grouping related trait parameters into a struct in the trait definition\n\
             2. Using an associated type or configuration struct if the trait is yours"
                .to_string()
        } else if is_constructor {
            "For constructors with many parameters, consider:\n\
             1. Builder pattern: `MyClass::builder().field1(x).field2(y).build()`\n\
             2. Configuration struct: `MyClass::new(Config { ... })`"
                .to_string()
        } else if is_delegator {
            "This function appears to be a wrapper. Consider:\n\
             1. If wrapping is necessary, this is acceptable\n\
             2. If not, remove the wrapper and call the target directly"
                .to_string()
        } else {
            "Group related parameters into a configuration object or class".to_string()
        }
    }
}

impl Default for LongParameterListDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for LongParameterListDetector {
    fn name(&self) -> &'static str {
        "LongParameterListDetector"
    }

    fn description(&self) -> &'static str {
        "Detects functions with too many parameters"
    }

    fn category(&self) -> &'static str {
        "code_smell"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(&self, ctx: &crate::detectors::analysis_context::AnalysisContext) -> Result<Vec<Finding>> {
        let graph = ctx.graph;
        let i = graph.interner();
        let mut findings = Vec::new();

        // Use adaptive threshold from context resolver, falling back to config.
        let adaptive_base = ctx.threshold(MetricKind::ParameterCount, self.thresholds.max_params as f64) as usize;

        for func in graph.get_functions_shared().iter() {
            let param_count = func.param_count_opt().unwrap_or(0) as usize;

            // Quick pre-filter: skip functions clearly under the base threshold.
            // Even with no adjustments, they can't be flagged.
            if param_count <= adaptive_base {
                continue;
            }

            let name_lower = func.node_name(i).to_lowercase();
            let qn = func.qn(i);

            // === Skip builder methods entirely ===
            if Self::is_builder_method(&name_lower) {
                debug!("Skipping builder method: {}", qn);
                continue;
            }

            // === Compute effective threshold with all allowances ===
            let (effective_threshold, threshold_reasons) =
                self.effective_threshold(ctx, qn, &name_lower, adaptive_base);

            // After computing the full effective threshold, check again
            if param_count <= effective_threshold {
                debug!(
                    "Under effective threshold ({} <= {}): {}",
                    param_count, effective_threshold, qn
                );
                continue;
            }

            // === Classify function patterns ===
            let is_constructor = Self::is_constructor_like(&name_lower);
            let is_delegator_fn = Self::is_delegator(graph, qn, param_count);
            let is_trait_impl_fn = Self::is_trait_impl(qn);

            // === Calculate severity with scaled bands ===
            let base_severity = self.calculate_severity(param_count, effective_threshold);

            // === Apply post-hoc severity reductions ===
            let (severity, reduction_notes) =
                self.apply_severity_reductions(ctx, qn, base_severity, is_delegator_fn, is_trait_impl_fn);

            // Collect all analysis notes
            let mut notes: Vec<String> = threshold_reasons
                .into_iter()
                .map(|s| s.to_string())
                .collect();
            notes.extend(reduction_notes);

            let pattern_notes = if notes.is_empty() {
                String::new()
            } else {
                format!("\n\n**Graph Analysis:**\n{}", notes.join("\n"))
            };

            let suggestion = Self::build_suggestion(is_constructor, is_delegator_fn, is_trait_impl_fn);

            let explanation = self.config.adaptive.explain(
                MetricKind::ParameterCount,
                param_count as f64,
                5.0,
            );
            let threshold_metadata = explanation.to_metadata().into_iter().collect();

            findings.push(Finding {
                id: String::new(),
                detector: "LongParameterListDetector".to_string(),
                severity,
                title: format!("Long parameter list: {}", func.node_name(i)),
                description: format!(
                    "Function '{}' has {} parameters (effective threshold: {}).{}\n\n{}",
                    func.node_name(i),
                    param_count,
                    effective_threshold,
                    pattern_notes,
                    explanation.to_note()
                ),
                affected_files: vec![func.path(i).to_string().into()],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some(suggestion),
                estimated_effort: Some(self.estimate_effort(param_count)),
                category: Some("quality".to_string()),
                cwe_id: None,
                why_it_matters: Some(
                    "Long parameter lists make functions hard to call and understand. \
                     Callers must remember parameter order and meaning."
                        .to_string(),
                ),
                threshold_metadata,
                ..Default::default()
            });
        }

        info!(
            "LongParameterListDetector found {} findings (graph-aware)",
            findings.len()
        );
        Ok(findings)
    }
}

/// Convert snake_case to PascalCase
#[allow(dead_code)] // Used by suggest_config_name
fn to_pascal_case(s: &str) -> String {
    s.split('_')
        .map(|word| {
            let mut chars = word.chars();
            match chars.next() {
                None => String::new(),
                Some(first) => first.to_uppercase().chain(chars).collect(),
            }
        })
        .collect()
}

impl super::RegisteredDetector for LongParameterListDetector {
    fn create(init: &super::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::with_config(init.config_for("LongParameterListDetector")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::analysis_context::AnalysisContext;
    use crate::detectors::base::Detector;

    #[test]
    fn test_default_thresholds() {
        let detector = LongParameterListDetector::new();
        assert_eq!(detector.thresholds.max_params, 5);
        assert_eq!(detector.thresholds.high_params, 7);
        assert_eq!(detector.thresholds.critical_params, 10);
    }

    #[test]
    fn test_severity_calculation_default_threshold() {
        let detector = LongParameterListDetector::new();

        // With effective_threshold == max_params (5), scale factor is 1.0
        // so severity bands are unchanged: <=5 Low, 6 Medium, 7+ High, 10+ Critical
        assert_eq!(detector.calculate_severity(5, 5), Severity::Low);
        assert_eq!(detector.calculate_severity(6, 5), Severity::Medium);
        assert_eq!(detector.calculate_severity(7, 5), Severity::High);
        assert_eq!(detector.calculate_severity(10, 5), Severity::Critical);
    }

    #[test]
    fn test_severity_calculation_doubled_threshold() {
        let detector = LongParameterListDetector::new();

        // With effective_threshold == 10 (constructor doubled), scale factor = 2.0
        // High band = ceil(7*2.0) = 14, Critical band = ceil(10*2.0) = 20
        assert_eq!(detector.calculate_severity(10, 10), Severity::Low);
        assert_eq!(detector.calculate_severity(11, 10), Severity::Medium);
        assert_eq!(detector.calculate_severity(14, 10), Severity::High);
        assert_eq!(detector.calculate_severity(20, 10), Severity::Critical);
    }

    #[test]
    fn test_meaningful_params() {
        let detector = LongParameterListDetector::new();

        let params = vec![
            serde_json::json!("self"),
            serde_json::json!("x"),
            serde_json::json!("y"),
            serde_json::json!({"name": "cls"}),
            serde_json::json!({"name": "config"}),
        ];

        let meaningful = detector.get_meaningful_params(&params);
        assert_eq!(meaningful, vec!["x", "y", "config"]);
    }

    #[test]
    fn test_to_pascal_case() {
        assert_eq!(to_pascal_case("hello_world"), "HelloWorld");
        assert_eq!(to_pascal_case("create_user"), "CreateUser");
        assert_eq!(to_pascal_case("x"), "X");
    }

    #[test]
    fn test_suggest_config_name() {
        let detector = LongParameterListDetector::new();

        assert_eq!(
            detector.suggest_config_name("create_user", &[]),
            "UserConfig"
        );
        assert_eq!(
            detector.suggest_config_name("connect", &["host".to_string(), "port".to_string()]),
            "ConnectionConfig"
        );
        assert_eq!(
            detector
                .suggest_config_name("login", &["username".to_string(), "password".to_string()]),
            "Credentials"
        );
    }

    #[test]
    fn test_constructor_pattern_detection() {
        assert!(LongParameterListDetector::is_constructor_like("new"));
        assert!(LongParameterListDetector::is_constructor_like("new_with_options"));
        assert!(LongParameterListDetector::is_constructor_like("create_user"));
        assert!(LongParameterListDetector::is_constructor_like("from_parts"));
        assert!(LongParameterListDetector::is_constructor_like("with_config"));
        assert!(LongParameterListDetector::is_constructor_like("__init__"));
        assert!(LongParameterListDetector::is_constructor_like("setup_database"));
        assert!(LongParameterListDetector::is_constructor_like("configure_server"));
        assert!(LongParameterListDetector::is_constructor_like("initialize_state"));
        assert!(!LongParameterListDetector::is_constructor_like("process_data"));
        assert!(!LongParameterListDetector::is_constructor_like("detect"));
    }

    #[test]
    fn test_builder_method_detection() {
        assert!(LongParameterListDetector::is_builder_method("with_timeout"));
        assert!(LongParameterListDetector::is_builder_method("set_name"));
        assert!(LongParameterListDetector::is_builder_method("add_header"));
        assert!(LongParameterListDetector::is_builder_method("build"));
        assert!(!LongParameterListDetector::is_builder_method("detect"));
        assert!(!LongParameterListDetector::is_builder_method("process"));
    }

    #[test]
    fn test_trait_impl_detection() {
        assert!(LongParameterListDetector::is_trait_impl(
            "src/detectors/god_class.rs::impl<Detector for GodClassDetector>::detect:42"
        ));
        assert!(LongParameterListDetector::is_trait_impl(
            "foo.rs::impl<Display for MyType>::fmt:10"
        ));
        // Inherent impl (no trait) should NOT match
        assert!(!LongParameterListDetector::is_trait_impl(
            "foo.rs::impl<MyType>::new:5"
        ));
        // Regular function
        assert!(!LongParameterListDetector::is_trait_impl(
            "foo.rs::MyModule::process:20"
        ));
    }

    #[test]
    fn test_reduce_severity() {
        assert_eq!(
            LongParameterListDetector::reduce_severity(Severity::Critical),
            Severity::High
        );
        assert_eq!(
            LongParameterListDetector::reduce_severity(Severity::High),
            Severity::Medium
        );
        assert_eq!(
            LongParameterListDetector::reduce_severity(Severity::Medium),
            Severity::Low
        );
        assert_eq!(
            LongParameterListDetector::reduce_severity(Severity::Low),
            Severity::Low
        );
    }

    #[test]
    fn test_detect_no_findings_on_empty_graph() {
        let graph = GraphStore::in_memory();
        let ctx = AnalysisContext::test(&graph);
        let detector = LongParameterListDetector::new();
        let findings = detector.detect(&ctx).unwrap();
        assert!(findings.is_empty());
    }

    #[test]
    fn test_build_suggestion_trait_impl() {
        let suggestion = LongParameterListDetector::build_suggestion(false, false, true);
        assert!(suggestion.contains("trait"));
    }

    #[test]
    fn test_build_suggestion_constructor() {
        let suggestion = LongParameterListDetector::build_suggestion(true, false, false);
        assert!(suggestion.contains("Builder pattern"));
    }

    #[test]
    fn test_build_suggestion_delegator() {
        let suggestion = LongParameterListDetector::build_suggestion(false, true, false);
        assert!(suggestion.contains("wrapper"));
    }

    #[test]
    fn test_build_suggestion_generic() {
        let suggestion = LongParameterListDetector::build_suggestion(false, false, false);
        assert!(suggestion.contains("configuration object"));
    }
}
