//! Dead code detector - finds unused functions and classes
//!
//! Detects code that is never called or instantiated, indicating:
//! - Leftover code from refactoring
//! - Unused features
//! - Test helpers that were never removed
//!
//! Uses graph analysis to find nodes with zero incoming CALLS relationships.
//! Exemptions are driven by graph flags (is_exported, has_decorators,
//! address_taken) and role-based gating (FunctionRole, HMM FunctionContext),
//! replacing the previous 200+ hardcoded pattern lists.

use crate::detectors::analysis_context::AnalysisContext;
use crate::detectors::base::{Detector, DetectorConfig};
use crate::detectors::context_hmm;
use crate::detectors::function_context::FunctionRole;
use crate::graph::GraphQueryExt;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;
use tracing::{debug, info};

/// Minimal entry point names that should never be flagged.
/// Most entry points are now handled by FunctionRole::EntryPoint.
const ENTRY_POINTS: &[&str] = &[
    "main", "__main__", "__init__", "setUp", "tearDown",
    "init", // Go init functions run automatically
];

/// Thresholds for dead code detection
#[derive(Debug, Clone)]
pub struct DeadCodeThresholds {
    /// Base confidence for graph-only detection
    pub base_confidence: f64,
    /// Maximum functions to return
    pub max_results: usize,
}

impl Default for DeadCodeThresholds {
    fn default() -> Self {
        Self {
            base_confidence: 0.70,
            max_results: 100,
        }
    }
}

/// Detects dead code (unused functions and classes)
pub struct DeadCodeDetector {
    config: DetectorConfig,
    thresholds: DeadCodeThresholds,
}

impl DeadCodeDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(DeadCodeThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: DeadCodeThresholds) -> Self {
        Self {
            config: DetectorConfig::new(),
            thresholds,
        }
    }

    /// Create with custom config
    #[allow(dead_code)] // Builder pattern method
    pub fn with_config(config: DetectorConfig) -> Self {
        let thresholds = DeadCodeThresholds {
            base_confidence: config.get_option_or("base_confidence", 0.70),
            max_results: config.get_option_or("max_results", 100),
        };

        Self::with_thresholds(thresholds)
    }

    // ── Path-based checks (minimal fallbacks) ─────────────────────────

    /// Check if file path is in a test directory.
    /// Fallback for when FunctionContextMap doesn't have test role.
    fn is_test_path(file_path: &str) -> bool {
        let path_lower = file_path.to_lowercase();
        // Rust test files
        path_lower.ends_with("/tests.rs")
            || path_lower.ends_with("/test.rs")
            || path_lower.ends_with("\\tests.rs")
            || path_lower.ends_with("\\test.rs")
            // Test directories
            || path_lower.contains("/tests/")
            || path_lower.contains("/test/")
            || path_lower.contains("\\tests\\")
            || path_lower.contains("\\test\\")
            || path_lower.starts_with("tests/")
            || path_lower.starts_with("test/")
            || path_lower.starts_with("tests\\")
            || path_lower.starts_with("test\\")
            // Python/JS test conventions
            || path_lower.contains("/__tests__/")
            || path_lower.contains("/spec/")
    }

    /// Check if file path is in a benchmark directory.
    fn is_benchmark_path(file_path: &str) -> bool {
        let path_lower = file_path.to_lowercase();
        path_lower.contains("/benches/")
            || path_lower.contains("/benchmark/")
            || path_lower.contains("/benchmarks/")
            || path_lower.contains("\\benches\\")
            || path_lower.contains("\\benchmark\\")
            || path_lower.contains("\\benchmarks\\")
            || path_lower.starts_with("benches/")
            || path_lower.starts_with("benchmark/")
            || path_lower.starts_with("benchmarks/")
            || path_lower.starts_with("benches\\")
            || path_lower.starts_with("benchmark\\")
            || path_lower.starts_with("benchmarks\\")
    }

    /// Check if a function is a trait implementation method.
    ///
    /// Trait impl methods have QN format: `path::impl<TraitName for TypeName>::method:line`
    /// These are called via dynamic dispatch (`&dyn Trait`) which is invisible
    /// to the static call graph. They should never be flagged as dead code
    /// because trait impls are contractual obligations — they exist because
    /// something requires the trait.
    fn is_trait_impl_method(func_qn: &str) -> bool {
        func_qn.contains("::impl<") && func_qn.contains(" for ")
    }

    /// Check if a function name is in the minimal entry points list.
    fn is_entry_point(name: &str) -> bool {
        ENTRY_POINTS.contains(&name) || name.starts_with("test_")
    }

    /// Check if a function is a public API entry in a library crate.
    ///
    /// Exempts `pub` functions in `lib.rs` or `mod.rs`, which
    /// indicates a top-level public API surface.
    fn is_pub_api_surface(file_path: &str, is_exported: bool) -> bool {
        if !is_exported {
            return false;
        }

        let path_lower = file_path.to_lowercase();
        path_lower.ends_with("/lib.rs")
            || path_lower.ends_with("/mod.rs")
            || path_lower.ends_with("\\lib.rs")
            || path_lower.ends_with("\\mod.rs")
    }

    /// Check if a function is called via `self.method()` or `Self::method()` in the same file.
    ///
    /// This is a workaround for Rust parser limitations where self-calls
    /// aren't tracked in the call graph.
    /// Check if a function is defined inside a trait block (trait method definition).
    /// These are called via dynamic dispatch and never have direct callers in the call graph.
    fn is_inside_trait_definition(
        ctx: &AnalysisContext<'_>,
        file_path: &str,
        line_start: u32,
        line_end: u32,
    ) -> bool {
        let classes = ctx.graph.get_classes_in_file(file_path);
        for class in &classes {
            let i = ctx.graph.interner();
            let class_name = class.node_name(i);
            // Trait definitions have class nodes that encompass the trait body.
            // Check if this function's line range is inside a class's line range.
            // This catches trait method definitions and default implementations.
            if class.line_start <= line_start && class.line_end >= line_end {
                // Skip impl blocks — those are handled by is_trait_impl_method QN check.
                // Trait definitions use names like "trait::TraitName" or just "TraitName".
                // Impl blocks use QN patterns like "impl<Trait for Type>".
                let class_qn = class.qn(i);
                if class_qn.contains("::impl<") {
                    continue; // impl block, not a trait definition
                }
                debug!(
                    "Skipping function inside class/trait '{}': line {}-{} within {}-{}",
                    class_name, line_start, line_end, class.line_start, class.line_end
                );
                return true;
            }
        }
        false
    }

    /// Check if a function is referenced anywhere in its source file outside its own definition.
    /// This catches calls inside macros (format!, println!, etc.), function pointers,
    /// attribute references, and other patterns that tree-sitter can't extract as call edges.
    fn is_referenced_in_file(
        ctx: &AnalysisContext<'_>,
        name: &str,
        file_path: &str,
        func_line_start: u32,
        func_line_end: u32,
    ) -> bool {
        // Skip very short names to avoid false matches (e.g., "id", "as", "to")
        if name.len() < 3 {
            return false;
        }

        let path = std::path::Path::new(file_path);

        // Build the search pattern: function name followed by call/reference patterns
        let call_pattern = format!("{}(", name);
        let pointer_pattern_comma = format!("{},", name); // .map(func_name, ...)
        let pointer_pattern_paren = format!("{})", name); // .map(func_name)
        let attr_pattern_quoted = format!("\"{}\"", name); // #[serde(deserialize_with = "name")]
        let attr_pattern_bare = format!("= {}", name); // #[arg(value_parser = name)]

        let check_content = |content: &str| -> bool {
            for (line_idx, line) in content.lines().enumerate() {
                let line_num = line_idx as u32 + 1;
                // Skip the function's own definition lines
                if line_num >= func_line_start && line_num <= func_line_end {
                    continue;
                }
                // Skip pure comment lines (// and /// but NOT # which is a Rust attribute)
                let trimmed = line.trim();
                if trimmed.starts_with("//") {
                    continue;
                }
                if line.contains(&call_pattern)
                    || line.contains(&pointer_pattern_comma)
                    || line.contains(&pointer_pattern_paren)
                    || line.contains(&attr_pattern_quoted)
                    || line.contains(&attr_pattern_bare)
                {
                    return true;
                }
            }
            false
        };

        // Try AnalysisContext FileIndex first
        if let Some(entry) = ctx.files.get(path) {
            return check_content(&entry.content);
        }
        // Fall back to global_cache
        if let Some(content) = crate::cache::global_cache().content(path) {
            return check_content(&content);
        }

        false
    }

    fn is_called_via_self(ctx: &AnalysisContext<'_>, name: &str, file_path: &str) -> bool {
        let path = std::path::Path::new(file_path);

        let self_call = format!("self.{}(", name);
        let self_call_alt = format!("self.{},", name); // Passed as closure
        let self_static = format!("Self::{}(", name); // Associated function via Self

        // Try AnalysisContext FileIndex first
        if let Some(entry) = ctx.files.get(path) {
            if entry.content.contains(&self_call)
                || entry.content.contains(&self_call_alt)
                || entry.content.contains(&self_static)
            {
                return true;
            }
        } else {
            // Fall back to global_cache
            if let Some(content) = crate::cache::global_cache().content(path) {
                if content.contains(&self_call)
                    || content.contains(&self_call_alt)
                    || content.contains(&self_static)
                {
                    return true;
                }
            }
        }

        false
    }

    // ── Severity calculations ─────────────────────────────────────────

    /// Calculate severity for dead function
    fn calculate_function_severity(&self, complexity: usize) -> Severity {
        if complexity >= 20 {
            Severity::High
        } else if complexity >= 10 {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    /// Calculate severity for dead class
    fn calculate_class_severity(&self, method_count: usize, complexity: usize) -> Severity {
        if method_count >= 10 || complexity >= 50 {
            Severity::High
        } else if method_count >= 5 || complexity >= 20 {
            Severity::Medium
        } else {
            Severity::Low
        }
    }

    // ── Finding creation ──────────────────────────────────────────────

    /// Create a finding for an unused function
    fn create_function_finding(
        &self,
        _qualified_name: String,
        name: String,
        file_path: String,
        line_start: Option<u32>,
        complexity: usize,
    ) -> Finding {
        let severity = self.calculate_function_severity(complexity);
        let confidence = self.thresholds.base_confidence;

        Finding {
            id: deterministic_finding_id(
                "DeadCodeDetector",
                &file_path,
                0,
                &format!("Unused function: {}", name),
            ),
            detector: "DeadCodeDetector".to_string(),
            severity,
            title: format!("Unused function: {}", name),
            description: format!(
                "Function '{}' is never called in the codebase. \
                 It has complexity {}.\n\n\
                 **Confidence:** {:.0}% (graph analysis only)\n\
                 **Recommendation:** Review before removing",
                name,
                complexity,
                confidence * 100.0
            ),
            affected_files: vec![PathBuf::from(&file_path)],
            line_start,
            line_end: None,
            suggested_fix: Some(format!(
                "**REVIEW REQUIRED** (confidence: {:.0}%)\n\
                 1. Remove the function from {}\n\
                 2. Check for dynamic calls (getattr, eval) that might use it\n\
                 3. Verify it's not an API endpoint or callback",
                confidence * 100.0,
                file_path.split('/').next_back().unwrap_or(&file_path)
            )),
            estimated_effort: Some("Small (30-60 minutes)".to_string()),
            category: Some("dead_code".to_string()),
            cwe_id: Some("CWE-561".to_string()), // Dead Code
            why_it_matters: Some(
                "Dead code increases maintenance burden, confuses developers, \
                 and can hide bugs. Removing unused code improves readability \
                 and reduces the codebase size."
                    .to_string(),
            ),
            confidence: Some(confidence),
            ..Default::default()
        }
    }

    /// Create a finding for an unused class
    fn create_class_finding(
        &self,
        _qualified_name: String,
        name: String,
        file_path: String,
        method_count: usize,
        complexity: usize,
    ) -> Finding {
        let severity = self.calculate_class_severity(method_count, complexity);
        let confidence = self.thresholds.base_confidence;

        let effort = if method_count >= 10 {
            "Medium (2-4 hours)"
        } else if method_count >= 5 {
            "Small (1-2 hours)"
        } else {
            "Small (30 minutes)"
        };

        Finding {
            id: deterministic_finding_id(
                "DeadCodeDetector",
                &file_path,
                0,
                &format!("Unused class: {}", name),
            ),
            detector: "DeadCodeDetector".to_string(),
            severity,
            title: format!("Unused class: {}", name),
            description: format!(
                "Class '{}' is never instantiated or inherited from. \
                 It has {} methods and complexity {}.\n\n\
                 **Confidence:** {:.0}% (graph analysis only)\n\
                 **Recommendation:** Review before removing",
                name,
                method_count,
                complexity,
                confidence * 100.0
            ),
            affected_files: vec![PathBuf::from(&file_path)],
            line_start: None,
            line_end: None,
            suggested_fix: Some(format!(
                "**REVIEW REQUIRED** (confidence: {:.0}%)\n\
                 1. Remove the class and its {} methods\n\
                 2. Check for dynamic instantiation (factory patterns, reflection)\n\
                 3. Verify it's not used in configuration or plugins",
                confidence * 100.0,
                method_count
            )),
            estimated_effort: Some(effort.to_string()),
            category: Some("dead_code".to_string()),
            cwe_id: Some("CWE-561".to_string()),
            why_it_matters: Some(
                "Unused classes bloat the codebase and increase cognitive load. \
                 They may also cause confusion about the system's actual behavior."
                    .to_string(),
            ),
            confidence: Some(confidence),
            ..Default::default()
        }
    }

    // ── Core detection logic ──────────────────────────────────────────

    /// Find dead functions using graph flags and role-based gating.
    fn find_dead_functions(&self, ctx: &AnalysisContext<'_>) -> Vec<Finding> {
        let graph = ctx.graph;
        let i = graph.interner();
        let mut findings = Vec::new();

        // Get all functions, sorted by complexity (descending) for prioritization
        let mut functions: Vec<_> = graph.get_functions().into_iter().collect();
        functions.sort_by(|a, b| {
            b.complexity_opt()
                .unwrap_or(0)
                .cmp(&a.complexity_opt().unwrap_or(0))
                .then_with(|| a.qualified_name.cmp(&b.qualified_name))
        });

        for func in functions {
            let name = func.node_name(i);
            let file_path = func.path(i);
            let func_qn = func.qn(i);

            // Core check: has callers -> not dead
            let fan_in = graph.call_fan_in(func_qn);
            if fan_in > 0 {
                continue;
            }

            // === Graph flag exemptions ===
            if func.is_exported() {
                debug!("Skipping exported function: {}", name);
                continue; // Public API
            }
            if func.has_decorators() {
                debug!("Skipping decorated function: {}", name);
                continue; // Runtime-registered
            }
            if func.address_taken() {
                debug!("Skipping address_taken function: {}", name);
                continue; // Used as callback
            }

            // === Role-based exemptions (from FunctionContextMap) ===
            if ctx.is_test_function(func_qn) {
                debug!("Skipping test function (role): {}", name);
                continue;
            }
            if let Some(role) = ctx.function_role(func_qn) {
                match role {
                    FunctionRole::EntryPoint => {
                        debug!("Skipping entry point (role): {}", name);
                        continue;
                    }
                    FunctionRole::Hub => {
                        debug!("Skipping hub (role): {}", name);
                        continue; // Central infrastructure
                    }
                    _ => {}
                }
            }

            // === Python dunder methods ===
            if name.starts_with("__") && name.ends_with("__") {
                debug!("Skipping dunder method: {}", name);
                continue;
            }

            // === HMM context: skip handler and test functions ===
            if let Some((hmm_ctx, conf)) = ctx.hmm_role(func_qn) {
                if matches!(hmm_ctx, context_hmm::FunctionContext::Handler) && conf > 0.6 {
                    debug!("Skipping HMM handler (conf={:.2}): {}", conf, name);
                    continue;
                }
                if matches!(hmm_ctx, context_hmm::FunctionContext::Test) && conf > 0.6 {
                    debug!("Skipping HMM test (conf={:.2}): {}", conf, name);
                    continue;
                }
            }

            // === Minimal remaining checks ===

            // Minimal entry points (main, __init__, test_ prefix, etc.)
            if Self::is_entry_point(name) {
                continue;
            }

            // Test paths (fallback for when FunctionContextMap doesn't have test role)
            if Self::is_test_path(file_path) {
                debug!("Skipping test path function: {} in {}", name, file_path);
                continue;
            }

            // Benchmark paths
            if Self::is_benchmark_path(file_path) {
                debug!("Skipping benchmark function: {} in {}", name, file_path);
                continue;
            }

            // Public API surface in library crates (lib.rs, mod.rs)
            if Self::is_pub_api_surface(file_path, func.is_exported()) {
                debug!("Skipping pub API surface: {} in {}", name, file_path);
                continue;
            }

            // Trait impl methods (called via dynamic dispatch, invisible in call graph)
            if Self::is_trait_impl_method(func_qn) {
                debug!("Skipping trait impl method: {}", func_qn);
                continue;
            }

            // Self-call check (Rust parser limitation)
            if Self::is_called_via_self(ctx, name, file_path) {
                debug!("Skipping self-call: {}", name);
                continue;
            }

            // === Qualified name test module check ===
            if func_qn.contains("::tests::") || func_qn.contains("::test::") {
                debug!("Skipping test module function: {}", func_qn);
                continue;
            }

            // === Trait/class definition spatial check ===
            // Functions defined inside trait blocks are called via dynamic dispatch.
            if Self::is_inside_trait_definition(ctx, file_path, func.line_start, func.line_end) {
                debug!("Skipping function inside trait definition: {}", name);
                continue;
            }

            // === Same-file text scan (catches calls inside macros, function pointers, attributes) ===
            // Tree-sitter can't extract calls from Rust macro bodies (format!, println!, etc.)
            // so we fall back to a text scan of the source file as a last resort.
            if Self::is_referenced_in_file(ctx, name, file_path, func.line_start, func.line_end) {
                debug!(
                    "Skipping function referenced in file: {} in {}",
                    name, file_path
                );
                continue;
            }

            let complexity = func.complexity_opt().unwrap_or(1) as usize;
            let line_start = Some(func.line_start);

            findings.push(self.create_function_finding(
                func_qn.to_string(),
                name.to_string(),
                file_path.to_string(),
                line_start,
                complexity,
            ));

            if findings.len() >= self.thresholds.max_results {
                break;
            }
        }

        findings
    }

    /// Find dead classes using graph flags and role-based gating.
    fn find_dead_classes(&self, ctx: &AnalysisContext<'_>) -> Vec<Finding> {
        let graph = ctx.graph;
        let i = graph.interner();
        let mut findings = Vec::new();

        let mut classes: Vec<_> = graph.get_classes().into_iter().collect();
        classes.sort_by(|a, b| {
            b.complexity_opt()
                .unwrap_or(0)
                .cmp(&a.complexity_opt().unwrap_or(0))
                .then_with(|| a.qualified_name.cmp(&b.qualified_name))
        });

        let import_edges = graph.all_import_edges();

        // Precompute lowercased qualified names of all import targets ONCE, outside
        // the class loop. The prior implementation called `graph.node_idx(dst_idx)`
        // and `qn().to_lowercase()` for every (class, edge) pair — O(classes × edges)
        // with a per-iteration allocation. Also precompute a basename set for an
        // O(1) fast path on the basename-equality branch of the match.
        let importee_qnames_lower: Vec<String> = import_edges
            .iter()
            .filter_map(|&(_, dst_idx)| graph.node_idx(dst_idx).map(|n| n.qn(i).to_lowercase()))
            .collect();
        let importee_basenames: std::collections::HashSet<&str> = importee_qnames_lower
            .iter()
            .map(|q| q.rsplit('/').next().unwrap_or(q.as_str()))
            .collect();

        for class in classes {
            let name = class.node_name(i);
            let file_path = class.path(i);
            let class_qn = class.qn(i);

            // Skip common patterns (Error/Exception/Mixin/Test/ABC)
            if name.ends_with("Error")
                || name.ends_with("Exception")
                || name.ends_with("Mixin")
                || name.contains("Mixin")
                || name.starts_with("Test")
                || name.ends_with("Test")
                || name == "ABC"
                || name == "Enum"
                || name == "Exception"
                || name == "BaseException"
            {
                continue;
            }

            // === Graph flag exemptions ===
            if class.is_exported() {
                debug!("Skipping exported class: {}", name);
                continue;
            }
            if class.has_decorators() {
                debug!("Skipping decorated class: {}", name);
                continue;
            }

            // Check if class has any callers (instantiation)
            if graph.call_fan_in(class_qn) > 0 {
                continue;
            }

            // Check if class has any child classes
            let children = graph.get_child_classes(class_qn);
            if !children.is_empty() {
                continue;
            }

            // === Test/benchmark path exemptions ===
            if Self::is_test_path(file_path) {
                debug!("Skipping test path class: {} in {}", name, file_path);
                continue;
            }
            if Self::is_benchmark_path(file_path) {
                debug!("Skipping benchmark class: {} in {}", name, file_path);
                continue;
            }

            // Qualified name test module check
            if class_qn.contains("::tests::") || class_qn.contains("::test::") {
                continue;
            }

            // === HMM context for class methods ===
            // If the class qualified name is classified as Handler, skip
            if let Some((hmm_ctx, conf)) = ctx.hmm_role(class_qn) {
                if matches!(hmm_ctx, context_hmm::FunctionContext::Handler) && conf > 0.6 {
                    debug!("Skipping HMM handler class (conf={:.2}): {}", conf, name);
                    continue;
                }
            }

            // Check if class's file is imported by other files.
            // Hoist the per-class allocations out of the inner loop, and try the
            // O(1) basename set before falling back to a linear suffix scan.
            let class_file = file_path.to_lowercase();
            let class_file_cleaned =
                class_file.replace("/tmp/", "").replace("/home/", "");
            let class_basename = class_file.rsplit('/').next().unwrap_or(&class_file);
            let file_is_imported = importee_basenames.contains(class_basename)
                || importee_qnames_lower.iter().any(|target_lower| {
                    class_file.ends_with(target_lower.as_str())
                        || target_lower.ends_with(class_file_cleaned.as_str())
                });
            if file_is_imported {
                continue;
            }

            // Skip public classes (uppercase, no underscore prefix) in non-test files
            let is_public =
                !name.starts_with('_') && name.chars().next().is_some_and(|c| c.is_uppercase());
            let is_test_file = class_file.contains("/test") || class_file.contains("_test.");
            if is_public && !is_test_file {
                continue;
            }

            // Public API surface
            if Self::is_pub_api_surface(file_path, class.is_exported()) {
                continue;
            }

            let complexity = class.complexity_opt().unwrap_or(1) as usize;
            let method_count = class.get_i64("methodCount").unwrap_or(0) as usize;

            findings.push(self.create_class_finding(
                class_qn.to_string(),
                name.to_string(),
                file_path.to_string(),
                method_count,
                complexity,
            ));

            if findings.len() >= 50 {
                break;
            }
        }

        findings
    }
}

impl Default for DeadCodeDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl Detector for DeadCodeDetector {
    fn name(&self) -> &'static str {
        "DeadCodeDetector"
    }

    fn description(&self) -> &'static str {
        "Detects unused functions and classes"
    }

    fn category(&self) -> &'static str {
        "dead_code"
    }

    fn config(&self) -> Option<&DetectorConfig> {
        Some(&self.config)
    }

    fn detect(
        &self,
        ctx: &crate::detectors::analysis_context::AnalysisContext,
    ) -> Result<Vec<Finding>> {
        debug!("Starting dead code detection (graph flags + role-based)");
        let mut findings = Vec::new();

        // Find dead functions
        let function_findings = self.find_dead_functions(ctx);
        findings.extend(function_findings);

        // Find dead classes
        let class_findings = self.find_dead_classes(ctx);
        findings.extend(class_findings);

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        info!("DeadCodeDetector found {} dead code issues", findings.len());

        Ok(findings)
    }
}

impl crate::detectors::RegisteredDetector for DeadCodeDetector {
    fn create(_init: &crate::detectors::DetectorInit) -> std::sync::Arc<dyn Detector> {
        std::sync::Arc::new(Self::new())
    }
}

#[cfg(test)]
mod tests;
