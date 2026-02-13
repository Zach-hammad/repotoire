//! Dead code detector - finds unused functions and classes
//!
//! Detects code that is never called or instantiated, indicating:
//! - Leftover code from refactoring
//! - Unused features
//! - Test helpers that were never removed
//!
//! Uses graph analysis to find nodes with zero incoming CALLS relationships.

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use std::collections::HashSet;
use std::path::PathBuf;
use tracing::{debug, info};

/// Paths that indicate dynamically-dispatched code (called via tables, not direct calls)
/// These functions have callers not visible in the static call graph.
static DISPATCH_PATHS: &[&str] = &[
    // FFI/language bindings
    "/ffi/",       // FFI bindings
    "/bindings/",  // Language bindings
    "/extern/",    // External interfaces
    "/jni/",       // Java Native Interface
    "/napi/",      // Node.js Native API
    "/wasm/",      // WebAssembly exports
    "/capi/",      // C API exports
    "/exports/",   // Exported functions
    // Dispatch table patterns (functions called via pointers)
    "/jets/",      // JIT/dispatch tables (common in interpreters)
    "/opcodes/",   // Opcode handlers
    "/handlers/",  // Event/message handlers
    "/callbacks/", // Callback functions
    "/hooks/",     // Hook functions
    "/vtable/",    // Virtual table implementations
    "/impls/",     // Trait/interface implementations
    "/builtins/",  // Built-in function implementations
    "/intrinsics/",// Compiler intrinsics
    "/primitives/",// Primitive operations
    "/ops/",       // Operation implementations
];

/// Entry points that should not be flagged as dead code
static ENTRY_POINTS: &[&str] = &[
    "main",
    "init", // Go init functions run automatically
    "__main__",
    "__init__",
    "setUp",
    "tearDown",
    // Rust common trait methods (called via trait dispatch, not visible in call graph)
    "run",
    "detect",
    "name",
    "description",
    "new",
    "default",
    "from",
    "into",
    "try_from",
    "try_into",
    "clone",
    "fmt",
    "eq",
    "cmp",
    "hash",
    "drop",
    "deref",
    "serialize",
    "deserialize",
    // Builder pattern (called on builder instances, not tracked in graph)
    "build",
    "with_config",
    "with_thresholds",
];

/// Framework-specific files where default exports are auto-loaded
/// (Next.js, React Native Navigation, Fastify, Remix, etc.)
/// Note: patterns without leading / also match at start of relative paths
static FRAMEWORK_AUTO_LOAD_PATTERNS: &[&str] = &[
    // Next.js App Router
    "/page.tsx",
    "/page.ts",
    "/page.jsx",
    "/page.js",
    "/layout.tsx",
    "/layout.ts",
    "/layout.jsx",
    "/layout.js",
    "/loading.tsx",
    "/loading.ts",
    "/error.tsx",
    "/error.ts",
    "/not-found.tsx",
    "/not-found.ts",
    "/template.tsx",
    "/template.ts",
    "/route.tsx",
    "/route.ts",
    // Next.js Pages Router
    "/pages/",
    "pages/",
    // Fastify AutoLoad
    "/routes/",
    "routes/",
    "/plugins/",
    "plugins/",
    // Remix
    "/routes.",
    "routes.",
    // Expo Router
    "/app/",
    "app/",
    // React Navigation screens typically end in Screen
    // Additional framework auto-discovery patterns (Issue #15)
    "/handlers/",
    "handlers/", // Event handlers directory
    "/commands/",
    "commands/", // CLI commands directory
    "/migrations/",
    "migrations/", // Database migrations
    "/seeds/",
    "seeds/", // Database seeds
    "/tasks/",
    "tasks/", // Background tasks (Celery, etc.)
    "/jobs/",
    "jobs/", // Job workers
    "/controllers/",
    "controllers/", // MVC controllers
    "/middleware/",
    "middleware/", // Express/Koa middleware
    "/hooks/",
    "hooks/", // React hooks, Git hooks
    "/subscribers/",
    "subscribers/", // Event subscribers
    "/listeners/",
    "listeners/", // Event listeners
];

/// Callback/handler function name patterns that are called dynamically
static CALLBACK_PATTERNS: &[&str] = &[
    "on",     // onClick, onSubmit, onLoad, etc.
    "handle", // handleClick, handleSubmit, etc.
    "cb",     // Common callback abbreviation
    "callback", "listener", "handler",
];

/// Special methods that are called implicitly
static MAGIC_METHODS: &[&str] = &[
    "__str__",
    "__repr__",
    "__enter__",
    "__exit__",
    "__call__",
    "__len__",
    "__iter__",
    "__next__",
    "__getitem__",
    "__setitem__",
    "__delitem__",
    "__eq__",
    "__ne__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__hash__",
    "__bool__",
    "__add__",
    "__sub__",
    "__mul__",
    "__truediv__",
    "__floordiv__",
    "__mod__",
    "__pow__",
    "__post_init__",
    "__init_subclass__",
    "__set_name__",
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
    entry_points: HashSet<String>,
    magic_methods: HashSet<String>,
}

impl DeadCodeDetector {
    /// Create a new detector with default thresholds
    pub fn new() -> Self {
        Self::with_thresholds(DeadCodeThresholds::default())
    }

    /// Create with custom thresholds
    pub fn with_thresholds(thresholds: DeadCodeThresholds) -> Self {
        let entry_points: HashSet<String> = ENTRY_POINTS.iter().map(|s| s.to_string()).collect();
        let magic_methods: HashSet<String> = MAGIC_METHODS.iter().map(|s| s.to_string()).collect();

        Self {
            config: DetectorConfig::new(),
            thresholds,
            entry_points,
            magic_methods,
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

    /// Check if a function name is an entry point
    fn is_entry_point(&self, name: &str) -> bool {
        self.entry_points.contains(name) || name.starts_with("test_")
    }

    /// Check if function has decorators (Issue #15)
    /// Decorators like @app.route, @Controller, @Route register functions at runtime
    fn has_decorator(&self, func: &crate::graph::CodeNode) -> bool {
        func.get_bool("has_decorators").unwrap_or(false)
    }

    /// Check if function name matches callback/handler patterns (Issue #15)
    /// These are typically called dynamically via .on(), .addEventListener(), etc.
    fn is_callback_pattern(&self, name: &str) -> bool {
        let name_lower = name.to_lowercase();

        // Check for on* patterns (onClick, onSubmit, onLoad)
        if name_lower.starts_with("on") && name.len() > 2 {
            // Ensure the character after "on" is uppercase (camelCase convention)
            if let Some(c) = name.chars().nth(2) {
                if c.is_uppercase() {
                    return true;
                }
            }
        }

        // Check for handle* patterns (handleClick, handleSubmit)
        if name_lower.starts_with("handle") && name.len() > 6 {
            if let Some(c) = name.chars().nth(6) {
                if c.is_uppercase() {
                    return true;
                }
            }
        }

        // Check for other callback patterns
        for pattern in CALLBACK_PATTERNS {
            if name_lower == *pattern || name_lower.ends_with(&format!("_{}", pattern)) {
                return true;
            }
        }

        false
    }

    /// Check if file is a CLI entry point defined in package.json bin field (Issue #15)
    fn is_cli_entry_point(&self, file_path: &str) -> bool {
        // Find package.json in parent directories
        let path = std::path::Path::new(file_path);
        let mut current = path.parent();

        while let Some(dir) = current {
            let package_json = dir.join("package.json");
            if package_json.exists() {
                if let Ok(content) = std::fs::read_to_string(&package_json) {
                    if let Ok(json) = serde_json::from_str::<serde_json::Value>(&content) {
                        // Check "bin" field
                        if let Some(bin) = json.get("bin") {
                            let file_name = path.file_name().and_then(|f| f.to_str()).unwrap_or("");
                            let relative_path = path
                                .strip_prefix(dir)
                                .ok()
                                .and_then(|p| p.to_str())
                                .unwrap_or("");

                            // bin can be a string or object
                            match bin {
                                serde_json::Value::String(s) => {
                                    if s.contains(file_name) || s.ends_with(relative_path) {
                                        return true;
                                    }
                                }
                                serde_json::Value::Object(map) => {
                                    for (_, v) in map {
                                        if let serde_json::Value::String(s) = v {
                                            if s.contains(file_name) || s.ends_with(relative_path) {
                                                return true;
                                            }
                                        }
                                    }
                                }
                                _ => {}
                            }
                        }

                        // Also check "main" field for library entry points
                        if let Some(serde_json::Value::String(main)) = json.get("main") {
                            let file_name = path.file_name().and_then(|f| f.to_str()).unwrap_or("");
                            if main.contains(file_name) {
                                return true;
                            }
                        }
                    }
                }
                break; // Found package.json, stop searching
            }
            current = dir.parent();
        }

        false
    }

    /// Check if file is in a framework auto-load location
    /// These files have their exports auto-registered by the framework
    fn is_framework_auto_load(&self, file_path: &str) -> bool {
        FRAMEWORK_AUTO_LOAD_PATTERNS
            .iter()
            .any(|pattern| file_path.contains(pattern))
    }

    /// Check if this is likely a framework-registered component/route
    fn is_framework_export(&self, name: &str, file_path: &str) -> bool {
        // Any export from framework auto-load files is considered used
        // (Fastify plugins, Next.js pages, CLI commands, etc. are auto-discovered)
        if self.is_framework_auto_load(file_path) {
            return true;
        }

        // React Native / Expo screens
        if name.ends_with("Screen") || name.ends_with("Page") {
            return true;
        }

        // Next.js/Remix conventions
        if matches!(
            name,
            "loader"
                | "action"
                | "meta"
                | "links"
                | "headers"
                | "generateStaticParams"
                | "generateMetadata"
                | "revalidate"
                | "GET"
                | "POST"
                | "PUT"
                | "DELETE"
                | "PATCH"
                | "HEAD"
                | "OPTIONS"
        ) {
            return true;
        }

        // Fastify route handlers - any function in /routes/ is likely auto-loaded
        if file_path.contains("/routes/") || file_path.starts_with("routes/") {
            // Fastify AutoLoad registers all exports from route files
            return true;
        }

        false
    }

    /// Check if a function is exported by looking at the source file
    /// This is a fallback when the graph doesn't have is_exported set
    fn is_exported_in_source(&self, file_path: &str, line_start: u32) -> bool {
        use tracing::debug;

        // Only check JS/TS files
        let is_js_ts = file_path.ends_with(".js")
            || file_path.ends_with(".ts")
            || file_path.ends_with(".jsx")
            || file_path.ends_with(".tsx")
            || file_path.ends_with(".mjs");

        if !is_js_ts {
            return false;
        }

        // Read the relevant lines from the source
        match std::fs::read_to_string(file_path) {
            Ok(content) => {
                let lines: Vec<&str> = content.lines().collect();
                let line_idx = (line_start as usize).saturating_sub(1);

                // Check the function's line and the line before for export keyword
                for offset in 0..=1 {
                    if line_idx >= offset {
                        if let Some(line) = lines.get(line_idx - offset) {
                            let trimmed = line.trim();
                            debug!(
                                "Checking line {} for export: '{}'",
                                line_idx - offset + 1,
                                trimmed
                            );
                            // Check for various export patterns
                            if trimmed.starts_with("export ")
                                || trimmed.starts_with("export{")
                                || trimmed.contains("module.exports")
                                || trimmed.contains("exports.")
                            {
                                debug!("Found export pattern in {}", file_path);
                                return true;
                            }
                        }
                    }
                }
                false
            }
            Err(e) => {
                debug!("Could not read file {}: {}", file_path, e);
                false
            }
        }
    }

    /// Check if a function name is a magic method
    fn is_magic_method(&self, name: &str) -> bool {
        self.magic_methods.contains(name)
    }

    /// Check if function should be filtered out
    fn should_filter(&self, name: &str, is_method: bool, has_decorators: bool) -> bool {
        // Magic methods
        if self.is_magic_method(name) {
            return true;
        }

        // Entry points
        if self.is_entry_point(name) {
            return true;
        }

        // Public methods (may be called externally)
        if is_method && !name.starts_with('_') {
            return true;
        }

        // Decorated functions
        if has_decorators {
            return true;
        }

        // Common patterns that are often called dynamically
        let filter_patterns = [
            "handle",
            "on_",
            "callback",
            "load_data",
            "loader",
            "_loader",
            "load_",
            "create_",
            "build_",
            "make_",
            "_parse_",
            "_process_",
            "load_config",
            "generate_",
            "validate_",
            "setup_",
            "initialize_",
            "to_dict",
            "to_json",
            "from_dict",
            "from_json",
            "serialize",
            "deserialize",
            "_side_effect",
            "_effect",
            "_extract_",
            "_find_",
            "_calculate_",
            "_get_",
            "_set_",
            "_check_",
            // Rust trait implementation patterns
            "with_", // builder pattern
            "into_",
            "as_",
            "is_",
            "has_",
            "can_",
            "should_",
            "try_",
            "parse_",
            "render_",
            "run_",
            "execute_",
            "process_",
            "extract_",
        ];

        let name_lower = name.to_lowercase();
        for pattern in filter_patterns {
            if name_lower.contains(pattern) {
                return true;
            }
        }

        false
    }

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

    /// Find dead functions using GraphStore API
    fn find_dead_functions(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Get all functions
        let functions = graph.get_functions();

        // Sort by complexity (descending) for prioritization
        let mut functions: Vec<_> = functions.into_iter().collect();
        functions.sort_by(|a, b| {
            b.complexity()
                .unwrap_or(0)
                .cmp(&a.complexity().unwrap_or(0))
        });

        for func in functions {
            let name = &func.name;
            let file_path = &func.file_path;

            // Skip test functions and entry points
            if name.starts_with("test_") || self.is_entry_point(name) {
                continue;
            }

            // Skip functions whose address is taken (callbacks, dispatch tables, FFI)
            // This is the primary mechanism for detecting dynamically-called functions
            if func.get_bool("address_taken").unwrap_or(false) {
                debug!("Skipping address_taken function: {}", name);
                continue;
            }

            // Skip functions in dispatch/FFI paths (called via tables, not direct calls)
            let path_lower = file_path.to_lowercase();
            if DISPATCH_PATHS.iter().any(|p| path_lower.contains(p)) {
                debug!("Skipping dispatch path function: {} in {}", name, file_path);
                continue;
            }

            // Skip framework auto-loaded exports (Next.js pages, Fastify routes, etc.)
            if self.is_framework_export(name, file_path) {
                continue;
            }

            // Skip CLI entry points defined in package.json bin field (Issue #15)
            if self.is_cli_entry_point(file_path) {
                debug!("Skipping CLI entry point: {} in {}", name, file_path);
                continue;
            }

            // Skip callback/handler patterns (Issue #15)
            // These are called dynamically via .on(), .addEventListener(), etc.
            if self.is_callback_pattern(name) {
                debug!("Skipping callback pattern: {}", name);
                continue;
            }

            // Check if function has any callers
            let callers = graph.get_callers(&func.qualified_name);
            if !callers.is_empty() {
                continue; // Function is called, not dead
            }

            // Check if method is called via self.method() in same file (Rust parser limitation)
            if let Some(content) =
                crate::cache::global_cache().get_content(std::path::Path::new(file_path))
            {
                let self_call = format!("self.{}(", name);
                let self_call_alt = format!("self.{},", name); // Passed as closure
                if content.contains(&self_call) || content.contains(&self_call_alt) {
                    continue; // Called via self
                }
            }

            // Check additional properties
            let is_method = func.get_bool("is_method").unwrap_or(false);
            let has_decorators = self.has_decorator(&func);
            let is_exported = func.get_bool("is_exported").unwrap_or(false);

            // Skip decorated functions - they're registered at runtime (Issue #15)
            // Decorators like @Route, @Controller, @app.route register functions dynamically
            if has_decorators {
                debug!("Skipping decorated function: {}", name);
                continue;
            }

            // Skip exported functions - they're likely used by external modules
            // Export means the author intended external use, so not "dead" even if uncalled internally
            if is_exported {
                continue;
            }

            // Check source for JS/TS export keyword (graph may not have is_exported set)
            // Use qualified_name to get full path since file_path may be relative
            let full_path = func.qualified_name.split("::").next().unwrap_or(file_path);
            if self.is_exported_in_source(full_path, func.line_start) {
                continue;
            }

            // Apply filters
            if self.should_filter(name, is_method, has_decorators) {
                continue;
            }

            let complexity = func.complexity().unwrap_or(1) as usize;
            let line_start = Some(func.line_start);

            findings.push(self.create_function_finding(
                func.qualified_name.clone(),
                name.clone(),
                func.file_path.clone(),
                line_start,
                complexity,
            ));

            if findings.len() >= self.thresholds.max_results {
                break;
            }
        }

        Ok(findings)
    }

    /// Find dead classes using GraphStore API
    fn find_dead_classes(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Get all classes
        let classes = graph.get_classes();

        // Sort by complexity (descending)
        let mut classes: Vec<_> = classes.into_iter().collect();
        classes.sort_by(|a, b| {
            b.complexity()
                .unwrap_or(0)
                .cmp(&a.complexity().unwrap_or(0))
        });

        for class in classes {
            let name = &class.name;
            let file_path = &class.file_path;

            // Skip common patterns
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

            // Skip React/RN components (Screen, Page, Layout, etc.)
            if name.ends_with("Screen")
                || name.ends_with("Page")
                || name.ends_with("Layout")
                || name.ends_with("Component")
                || name.ends_with("Provider")
                || name.ends_with("Context")
            {
                continue;
            }

            // Skip exports from framework auto-load files
            let is_exported = class.get_bool("is_exported").unwrap_or(false);
            if is_exported && self.is_framework_auto_load(file_path) {
                continue;
            }

            // Check if class has any callers (instantiation)
            let callers = graph.get_callers(&class.qualified_name);
            if !callers.is_empty() {
                continue;
            }

            // Check if class has any child classes
            let children = graph.get_child_classes(&class.qualified_name);
            if !children.is_empty() {
                continue;
            }

            // Check if class's file is imported by other files
            // This catches Python "from module import Class" patterns
            let class_file = class.file_path.to_lowercase();
            let imports = graph.get_imports();
            let file_is_imported = imports.iter().any(|(_, target)| {
                let target_lower = target.to_lowercase();
                // Match if the import target contains the class's file path
                class_file.ends_with(&target_lower)
                    || target_lower
                        .ends_with(&class_file.replace("/tmp/", "").replace("/home/", ""))
                    // Also check just the filename
                    || class_file.split('/').next_back() == target_lower.split('/').next_back()
            });
            if file_is_imported {
                continue;
            }

            // Skip public classes (uppercase, no underscore prefix) in non-test files
            // These are likely exported and used elsewhere
            let is_public =
                !name.starts_with('_') && name.chars().next().is_some_and(|c| c.is_uppercase());
            let is_test_file = class_file.contains("/test") || class_file.contains("_test.");
            if is_public && !is_test_file {
                continue;
            }

            // Skip decorated classes
            let has_decorators = class.get_bool("has_decorators").unwrap_or(false);
            if has_decorators {
                continue;
            }

            let complexity = class.complexity().unwrap_or(1) as usize;
            let method_count = class.get_i64("methodCount").unwrap_or(0) as usize;

            findings.push(self.create_class_finding(
                class.qualified_name.clone(),
                name.clone(),
                class.file_path.clone(),
                method_count,
                complexity,
            ));

            if findings.len() >= 50 {
                break;
            }
        }

        Ok(findings)
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

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        debug!("Starting dead code detection");

        let mut findings = Vec::new();

        // Find dead functions
        let function_findings = self.find_dead_functions(graph)?;
        findings.extend(function_findings);

        // Find dead classes
        let class_findings = self.find_dead_classes(graph)?;
        findings.extend(class_findings);

        // Sort by severity
        findings.sort_by(|a, b| b.severity.cmp(&a.severity));

        info!("DeadCodeDetector found {} dead code issues", findings.len());

        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_entry_points() {
        let detector = DeadCodeDetector::new();

        assert!(detector.is_entry_point("main"));
        assert!(detector.is_entry_point("__init__"));
        assert!(detector.is_entry_point("test_something"));
        assert!(!detector.is_entry_point("my_function"));
    }

    #[test]
    fn test_magic_methods() {
        let detector = DeadCodeDetector::new();

        assert!(detector.is_magic_method("__str__"));
        assert!(detector.is_magic_method("__repr__"));
        assert!(!detector.is_magic_method("my_method"));
    }

    #[test]
    fn test_should_filter() {
        let detector = DeadCodeDetector::new();

        // Magic methods
        assert!(detector.should_filter("__str__", false, false));

        // Entry points
        assert!(detector.should_filter("main", false, false));
        assert!(detector.should_filter("test_foo", false, false));

        // Public methods
        assert!(detector.should_filter("public_method", true, false));
        assert!(!detector.should_filter("_private_method", true, false));

        // Decorated
        assert!(detector.should_filter("any_func", false, true));

        // Patterns
        assert!(detector.should_filter("load_config", false, false));
        assert!(detector.should_filter("to_dict", false, false));
    }

    #[test]
    fn test_severity() {
        let detector = DeadCodeDetector::new();

        assert_eq!(detector.calculate_function_severity(5), Severity::Low);
        assert_eq!(detector.calculate_function_severity(10), Severity::Medium);
        assert_eq!(detector.calculate_function_severity(25), Severity::High);

        assert_eq!(detector.calculate_class_severity(3, 10), Severity::Low);
        assert_eq!(detector.calculate_class_severity(5, 10), Severity::Medium);
        assert_eq!(detector.calculate_class_severity(10, 10), Severity::High);
    }

    #[test]
    fn test_callback_patterns() {
        let detector = DeadCodeDetector::new();

        // on* handlers (camelCase)
        assert!(detector.is_callback_pattern("onClick"));
        assert!(detector.is_callback_pattern("onSubmit"));
        assert!(detector.is_callback_pattern("onLoad"));
        assert!(detector.is_callback_pattern("onMouseOver"));

        // handle* functions (camelCase)
        assert!(detector.is_callback_pattern("handleClick"));
        assert!(detector.is_callback_pattern("handleSubmit"));
        assert!(detector.is_callback_pattern("handleChange"));

        // Should NOT match non-callback patterns
        assert!(!detector.is_callback_pattern("online")); // "on" but not camelCase callback
        assert!(!detector.is_callback_pattern("only"));
        assert!(!detector.is_callback_pattern("handler_setup")); // not camelCase handle*
        assert!(!detector.is_callback_pattern("regular_function"));

        // Should match explicit callback names
        assert!(detector.is_callback_pattern("my_callback"));
        assert!(detector.is_callback_pattern("event_handler"));
        assert!(detector.is_callback_pattern("click_listener"));
    }

    #[test]
    fn test_framework_auto_load_patterns() {
        let detector = DeadCodeDetector::new();

        // Fastify autoload
        assert!(detector.is_framework_auto_load("src/plugins/auth.ts"));
        assert!(detector.is_framework_auto_load("plugins/db.js"));
        assert!(detector.is_framework_auto_load("/app/routes/api/users.ts"));

        // Event handlers directory
        assert!(detector.is_framework_auto_load("src/handlers/user-created.ts"));
        assert!(detector.is_framework_auto_load("handlers/order.js"));

        // CLI commands
        assert!(detector.is_framework_auto_load("src/commands/deploy.ts"));
        assert!(detector.is_framework_auto_load("commands/init.js"));

        // Migrations/seeds
        assert!(detector.is_framework_auto_load("db/migrations/001_create_users.ts"));
        assert!(detector.is_framework_auto_load("seeds/users.js"));

        // Should NOT match regular files
        assert!(!detector.is_framework_auto_load("src/utils/helpers.ts"));
        assert!(!detector.is_framework_auto_load("lib/core.js"));
    }
}
