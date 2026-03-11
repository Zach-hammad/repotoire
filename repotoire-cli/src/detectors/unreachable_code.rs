//! Unreachable Code Detector
//!
//! Graph-aware detection of unreachable code:
//! 1. Code after return/throw/exit (source pattern)
//! 2. Functions with zero callers in the call graph (dead functions)

use crate::detectors::base::{Detector, DetectorConfig};
use crate::graph::interner::StrKey;
use crate::graph::GraphStore;
use crate::models::{Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::LazyLock;
use tracing::{debug, info};

static RETURN_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(
            r"^\s*(return\b|throw\b|raise\b|exit\(|sys\.exit|process\.exit|break;|continue;)",
        )
        .expect("valid regex")
    });

/// Entry point patterns - these functions are called externally
const ENTRY_POINT_PATTERNS: &[&str] = &[
    "main",
    "test_",
    "setup",
    "teardown",
    "run",
    "start",
    "init",
    "handle",
    "on_",
    "get_",
    "post_",
    "put_",
    "delete_",
    "patch_",
    "__init__",
    "__new__",
    "__call__",
    "__enter__",
    "__exit__",
    "configure",
    "register",
    "setup_",
    "create_app",
    // Rust trait methods (called via trait dispatch, not visible in call graph)
    "detect",
    "name",
    "description",
    "category",
    "config",
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
    "build",
    "parse",
    "validate",
    // Builder pattern methods (called on builder instances, not tracked in graph)
    "with_",
    "set_",
    "add_",
    "find",
    "calculate",
    "analyze",
    // Callback/handler patterns (called via function pointers)
    "_cb", // callback suffix
    "_callback",
    "_handler",
    "_hook",
    "_fn",
    // Common interpreter/runtime prefixes (called via dispatch tables)
    // CPython: Py_, PyObject_, PyList_, etc.
    "py_",
    "pyobject_",
    "pylist_",
    "pydict_",
    "pytuple_",
    "pyset_",
    "_py", // internal CPython
    // Lua: lua_, luaL_, luaV_, etc.
    "lua_",
    "lual_",
    "luav_",
    "luac_",
    "luad_",
    "luag_",
    "luah_",
    // Ruby: rb_, RUBY_
    "rb_",
    "ruby_",
    // V8/JavaScript engines
    "v8_",
    "js_",
    // GLib/GTK
    "g_",
    "gtk_",
    "gdk_",
    // libuv
    "uv_",
    "uv__",
    // React/UI framework patterns (exported for external use)
    "use",       // React hooks: useEffect, useState, useCallback, useMemo
    "render",    // React render functions
    "component", // React components
    "create",    // Factory functions: createElement, createContext
    "provide",   // Provider components
    "consume",   // Consumer components
    "forward",   // forwardRef
    "memo",      // React.memo
    "lazy",      // React.lazy
    "suspense",  // Suspense-related
    // Compiler visitor patterns (called via dispatch)
    "visit",     // Visitor pattern: visitNode, visitExpression
    "enter",     // AST traversal: enterBlock
    "exit",      // AST traversal: exitBlock
    "transform", // AST transforms
    "emit",      // Code emission
    "infer",     // Type inference
    "check",     // Type checking
    "validate",  // Validation passes
    "lower",     // IR lowering
    "optimize",  // Optimization passes
    "analyze",   // Analysis passes
];

/// Paths that indicate entry points or dispatch-table code
const ENTRY_POINT_PATHS: &[&str] = &[
    // Direct entry points
    "/cli/",
    "/cmd/",
    "/main",
    "/handlers/",
    "/routes/",
    "/views/",
    "/api/",
    "/endpoints/",
    "/__main__",
    "/tests/",
    "_test.",
    // Dispatch table patterns (functions called via pointers, not direct calls)
    "/jets/",       // JIT/dispatch tables (interpreters)
    "/opcodes/",    // Opcode handlers
    "/callbacks/",  // Callback functions
    "/hooks/",      // Hook functions
    "/vtable/",     // Virtual table implementations
    "/impls/",      // Trait/interface implementations
    "/builtins/",   // Built-in functions
    "/intrinsics/", // Compiler intrinsics
    "/primitives/", // Primitive operations
    "/ops/",        // Operation implementations
    "/ffi/",        // FFI bindings
    "/bindings/",   // Language bindings
    "/wasm/",       // WebAssembly exports
    // Vendored/third-party code (shouldn't flag external code)
    "/ext/",          // External dependencies
    "/vendor/",       // Vendored code
    "/third_party/",  // Third-party libraries
    "/thirdparty/",   // Third-party libraries (alt)
    "/external/",     // External dependencies
    "/deps/",         // Dependencies
    "/node_modules/", // npm packages
    // Framework source code (exports are API surface, not dead code)
    "/packages/react",     // React monorepo packages
    "/packages/shared",    // React shared utilities
    "/packages/scheduler", // React scheduler
    "/packages/use-",      // React hooks packages
    "/reconciler/",        // React reconciler internals
    "/scheduler/",         // Scheduler internals
    "/forks/",             // React platform forks
    "/fiber/",             // React Fiber internals
];

pub struct UnreachableCodeDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl UnreachableCodeDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Check if function is likely an entry point (called externally)
    fn is_entry_point(&self, func_name: &str, file_path: &str) -> bool {
        let name_lower = func_name.to_lowercase();

        // Check name patterns
        if ENTRY_POINT_PATTERNS
            .iter()
            .any(|p| name_lower.starts_with(p) || name_lower == *p || name_lower.ends_with(p))
        {
            return true;
        }

        // Check path patterns
        if ENTRY_POINT_PATHS.iter().any(|p| file_path.contains(p)) {
            return true;
        }

        // Exported functions (capitalized in Go, pub in Rust implied by graph)
        if func_name
            .chars()
            .next()
            .map(|c| c.is_uppercase())
            .unwrap_or(false)
        {
            return true;
        }

        // Detect runtime/interpreter naming convention: short_prefix + underscore + name
        // Examples: u3r_word, Py_Initialize, lua_pushnil, rb_str_new
        // Pattern: 2-4 alphanumeric chars followed by underscore
        if Self::has_runtime_prefix(func_name) {
            return true;
        }

        false
    }

    /// Detect common runtime/interpreter naming patterns
    /// Pattern: 2-4 alphanumeric prefix + underscore (e.g., u3r_, Py_, lua_, rb_)
    fn has_runtime_prefix(func_name: &str) -> bool {
        // Find first underscore
        if let Some(underscore_pos) = func_name.find('_') {
            // Prefix must be 2-4 characters
            if (2..=4).contains(&underscore_pos) {
                let prefix = &func_name[..underscore_pos];
                // Prefix must be alphanumeric (allow mixed case for Py_, Rb_, etc.)
                if prefix.chars().all(|c| c.is_alphanumeric()) {
                    // Additional check: avoid false positives from common words
                    let prefix_lower = prefix.to_lowercase();
                    const COMMON_WORDS: &[&str] = &[
                        "get", "set", "is", "do", "can", "has", "new", "old", "add", "del", "pop",
                        "put", "run", "try", "end", "use", "for", "the", "and", "not", "dead",
                        "live", "test", "mock", "fake", "stub", "temp", "tmp", "foo", "bar", "baz",
                        "qux", "call", "read", "load", "save", "send", "recv",
                    ];
                    if !COMMON_WORDS.contains(&prefix_lower.as_str()) {
                        return true;
                    }
                }
            }
        }
        false
    }

    /// Check if function is exported (has export keyword or is in module.exports).
    /// Takes pre-fetched file content to avoid repeated cache lookups.
    fn is_exported_function_with_content(
        file_path: &str,
        func_name: &str,
        line_start: u32,
        content: &str,
    ) -> bool {
        let func_pattern = func_name.split("::").last().unwrap_or(func_name);

        // For Python, we can do a quick __all__ check without collecting lines
        if file_path.ends_with(".py") {
            for line in content.lines() {
                if line.contains("__all__") && line.contains(func_pattern) {
                    return true;
                }
            }
            // Python has no export/module.exports, so return early
            return false;
        }

        // Go: Capitalized = exported
        if file_path.ends_with(".go") {
            if let Some(c) = func_pattern.chars().next() {
                if c.is_uppercase() {
                    return true;
                }
            }
            return false;
        }

        let lines: Vec<&str> = content.lines().collect();

        // Check the function declaration line and a few lines before
        let start = (line_start as usize).saturating_sub(3);
        let end = (line_start as usize + 2).min(lines.len());

        for i in start..end {
            if i < lines.len() {
                let line = lines[i];

                // JS/TS export patterns - must be on the actual function line
                if line.contains("export ")
                    && (line.contains("function")
                        || line.contains("const")
                        || line.contains("=>"))
                {
                    return true;
                }
                if line.contains("export default") {
                    return true;
                }
            }
        }

        // Check for export statements anywhere in file (re-exports)
        for line in lines.iter() {
            // module.exports = { funcName } or module.exports.funcName
            if line.contains("module.exports") && line.contains(func_pattern) {
                return true;
            }
            // exports.funcName =
            if line.contains("exports.") && line.contains(func_pattern) {
                return true;
            }
            // export { funcName } or export { funcName as alias }
            if (line.contains("export {") || line.contains("export{"))
                && line.contains(func_pattern)
            {
                return true;
            }
            // export default funcName
            if line.contains("export default") && line.contains(func_pattern) {
                return true;
            }
        }

        // Rust: Check for pub fn at the declaration
        if file_path.ends_with(".rs") {
            let start_idx = (line_start as usize).saturating_sub(1);
            if start_idx < lines.len() {
                let line = lines[start_idx];
                if line.contains("pub fn") || line.contains("pub async fn") {
                    return true;
                }
            }
        }

        false
    }

    /// Find functions with zero callers using the call graph
    fn find_dead_functions(&self, graph: &dyn crate::graph::GraphQuery) -> Vec<Finding> {
        let i = graph.interner();
        let mut findings = Vec::new();
        let functions = graph.get_functions_shared();

        // Pre-build caller/callee maps from get_calls_shared() using StrKey for O(1) lookups.
        let calls = graph.get_calls_shared();
        let mut callee_to_callers: HashMap<StrKey, Vec<StrKey>> = HashMap::new();
        let mut caller_to_callees: HashMap<StrKey, Vec<StrKey>> = HashMap::new();
        let mut called_functions: HashSet<StrKey> = HashSet::with_capacity(calls.len());
        for (caller, callee) in calls.iter() {
            called_functions.insert(*callee);
            callee_to_callers
                .entry(*callee)
                .or_default()
                .push(*caller);
            caller_to_callees
                .entry(*caller)
                .or_default()
                .push(*callee);
        }

        // First pass: find directly dead functions
        let mut directly_dead: HashSet<StrKey> = HashSet::new();
        // Per-file exclusion cache: avoids re-checking bundled/minified/fixture per file
        let mut file_excluded: rustc_hash::FxHashMap<StrKey, bool> = rustc_hash::FxHashMap::default();
        // Per-file content cache: avoids repeated DashMap lookups for exported/self.method checks
        let mut file_content: rustc_hash::FxHashMap<StrKey, Option<std::sync::Arc<String>>> = rustc_hash::FxHashMap::default();

        for func in functions.iter() {
            // Skip if it's called somewhere
            if called_functions.contains(&func.qualified_name) {
                continue;
            }

            // Skip entry points
            if self.is_entry_point(func.node_name(i), func.path(i)) {
                continue;
            }

            // Skip functions whose address is taken (callbacks, dispatch tables, etc.)
            // These are invoked indirectly via function pointers, not direct calls
            if func.address_taken() {
                continue;
            }

            let fp = func.path(i);
            // Per-file exclusion: test, scripts, non-production, framework, bundled (cached)
            let excluded = *file_excluded
                .entry(func.file_path)
                .or_insert_with(|| {
                    // Test files
                    if fp.contains("/test") || fp.contains("_test.")
                        || fp.contains("/tests/") || fp.contains("conftest")
                        || fp.contains("type_check")
                    {
                        return true;
                    }
                    // Scripts/build tools
                    if fp.contains("/scripts/") || fp.contains("/tools/") || fp.contains("/build/") {
                        return true;
                    }
                    // Non-production paths
                    if crate::detectors::content_classifier::is_non_production_path(fp) {
                        return true;
                    }
                    // Framework internal paths
                    if fp.contains("packages/react") || fp.contains("/react-dom/")
                        || fp.contains("/react-server/") || fp.contains("/reconciler/")
                        || fp.contains("/scheduler/") || fp.contains("/shared/")
                        || fp.contains("/forks/")
                    {
                        return true;
                    }
                    // Bundled/generated/fixture code
                    if crate::detectors::content_classifier::is_likely_bundled_path(fp) {
                        return true;
                    }
                    if let Some(content) =
                        crate::cache::global_cache().content(std::path::Path::new(fp))
                    {
                        if crate::detectors::content_classifier::is_bundled_code(&content)
                            || crate::detectors::content_classifier::is_minified_code(&content)
                            || crate::detectors::content_classifier::is_fixture_code(fp, &content)
                        {
                            return true;
                        }
                    }
                    false
                });
            if excluded {
                continue;
            }

            let func_name = func.node_name(i);
            // Skip CLI-related functions (often entry points)
            if fp.contains("/cli")
                || func_name.contains("locate")
                || func_name.contains("app")
                || func_name.contains("create")
            {
                continue;
            }

            // Skip private/internal functions (underscore prefix)
            if func_name.starts_with('_') && !func_name.starts_with("__") {
                continue;
            }

            // Skip constructors (always called when class is instantiated)
            if func_name == "constructor" || func_name == "__init__" || func_name == "new" {
                continue;
            }

            // Skip dev-only functions (conditional compilation)
            let name_lower = func_name.to_lowercase();
            if name_lower.ends_with("dev")
                || name_lower.contains("indev")
                || name_lower.starts_with("warn")
                || name_lower.starts_with("debug")
            {
                continue;
            }

            // Expensive checks AFTER cheap graph-based filters:
            // Get/cache file content once per file (avoids repeated DashMap lookups)
            let content = file_content
                .entry(func.file_path)
                .or_insert_with(|| {
                    crate::cache::global_cache().content(std::path::Path::new(fp))
                });

            if let Some(ref content) = content {
                // Skip exported functions
                if Self::is_exported_function_with_content(
                    fp, func.qn(i), func.line_start, content,
                ) {
                    continue;
                }

                // Check if method is called via self.method() in same file
                let self_call = format!("self.{}(", func_name);
                if content.contains(&self_call) {
                    continue;
                }
            }

            directly_dead.insert(func.qualified_name);
        }

        // Second pass: find transitively dead functions
        // (functions only called by dead functions)
        let transitively_dead =
            self.find_transitively_dead(i, &functions, &callee_to_callers, &directly_dead);

        // Create findings for directly dead functions
        for func in functions.iter() {
            if !directly_dead.contains(&func.qualified_name) {
                continue;
            }

            // Check how many functions this dead function calls (impact) — O(1) lookup
            let dead_callees: Vec<StrKey> = caller_to_callees
                .get(&func.qualified_name)
                .map(|cs| {
                    cs.iter()
                        .copied()
                        .filter(|c| transitively_dead.contains(c))
                        .collect()
                })
                .unwrap_or_default();

            let cascade_note = if !dead_callees.is_empty() {
                format!(
                    "\n\n⚠️ **Cascade**: Removing this also removes {} transitively dead function(s):\n{}",
                    dead_callees.len(),
                    dead_callees.iter().take(3).map(|c| format!("  - {}", i.resolve(*c))).collect::<Vec<_>>().join("\n")
                )
            } else {
                String::new()
            };

            let func_name = func.node_name(i);
            let func_path = func.path(i);
            debug!("Dead function found: {} in {}", func_name, func_path);

            findings.push(Finding {
                id: String::new(),
                detector: "UnreachableCodeDetector".to_string(),
                severity: if !dead_callees.is_empty() {
                    Severity::High
                } else {
                    Severity::Medium
                },
                title: format!("Dead function: {}", func_name),
                description: format!(
                    "Function '{}' has **zero callers** in the codebase.\n\n\
                     This function is never called and may be dead code that can be removed.{}",
                    func_name, cascade_note
                ),
                affected_files: vec![PathBuf::from(func_path)],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some(
                    "Options:\n\
                     1. Remove the dead function\n\
                     2. If it's an entry point, add it to exports or ensure it's registered\n\
                     3. If it's a callback, ensure it's passed to the caller"
                        .to_string(),
                ),
                estimated_effort: Some(if !dead_callees.is_empty() {
                    "15 minutes".to_string()
                } else {
                    "10 minutes".to_string()
                }),
                category: Some("dead-code".to_string()),
                cwe_id: Some("CWE-561".to_string()),
                why_it_matters: Some(
                    "Dead functions add maintenance burden without providing value. \
                     They can confuse developers and increase cognitive load."
                        .to_string(),
                ),
                ..Default::default()
            });
        }

        // Create separate findings for transitively dead (lower severity - removing root will fix)
        for func in functions.iter() {
            if !transitively_dead.contains(&func.qualified_name) {
                continue;
            }
            if directly_dead.contains(&func.qualified_name) {
                continue; // Already reported
            }

            // Find which dead function(s) call this one — O(1) lookup
            let dead_callers: Vec<StrKey> = callee_to_callers
                .get(&func.qualified_name)
                .map(|callers| {
                    callers
                        .iter()
                        .copied()
                        .filter(|c| {
                            directly_dead.contains(c)
                                || transitively_dead.contains(c)
                        })
                        .collect()
                })
                .unwrap_or_default();

            let func_name = func.node_name(i);
            findings.push(Finding {
                id: String::new(),
                detector: "UnreachableCodeDetector".to_string(),
                severity: Severity::Low, // Lower - fixing root dead function will resolve this
                title: format!("Transitively dead: {}", func_name),
                description: format!(
                    "Function '{}' is only called by dead function(s):\n{}\n\n\
                     Removing the dead callers will make this removable too.",
                    func_name,
                    dead_callers
                        .iter()
                        .take(3)
                        .map(|c| format!("  - {}", i.resolve(*c)))
                        .collect::<Vec<_>>()
                        .join("\n")
                ),
                affected_files: vec![PathBuf::from(func.path(i))],
                line_start: Some(func.line_start),
                line_end: Some(func.line_end),
                suggested_fix: Some(
                    "This function will become removable after its dead callers are removed."
                        .to_string(),
                ),
                estimated_effort: Some("5 minutes".to_string()),
                category: Some("dead-code".to_string()),
                cwe_id: Some("CWE-561".to_string()),
                why_it_matters: Some(
                    "Transitively dead code is only reachable through other dead code.".to_string(),
                ),
                ..Default::default()
            });
        }

        findings
    }

    /// Find functions that are transitively dead (only called by dead functions)
    fn find_transitively_dead(
        &self,
        i: &crate::graph::interner::StringInterner,
        functions: &[crate::graph::store_models::CodeNode],
        callee_to_callers: &HashMap<StrKey, Vec<StrKey>>,
        directly_dead: &HashSet<StrKey>,
    ) -> HashSet<StrKey> {
        let mut transitively_dead: HashSet<StrKey> = HashSet::new();
        let mut changed = true;
        let mut iterations = 0;

        // Iterate until no new dead functions found
        while changed && iterations < 10 {
            changed = false;
            iterations += 1;

            for func in functions {
                // Skip if already marked dead
                if directly_dead.contains(&func.qualified_name)
                    || transitively_dead.contains(&func.qualified_name)
                {
                    continue;
                }

                // Skip entry points
                if self.is_entry_point(func.node_name(i), func.path(i)) {
                    continue;
                }

                // Get all callers from pre-built map (O(1) lookup)
                let callers = match callee_to_callers.get(&func.qualified_name) {
                    Some(c) => c,
                    None => continue, // No callers — would have been caught as directly dead
                };

                // Check if ALL callers are dead
                let all_callers_dead = callers.iter().all(|c| {
                    directly_dead.contains(c)
                        || transitively_dead.contains(c)
                });

                if all_callers_dead {
                    transitively_dead.insert(func.qualified_name);
                    changed = true;
                }
            }
        }

        debug!(
            "Found {} transitively dead functions",
            transitively_dead.len()
        );
        transitively_dead
    }

    /// Find code after return/throw statements using source scanning
    fn find_code_after_return(&self, files: &dyn crate::detectors::file_provider::FileProvider) -> Vec<Finding> {
        let mut findings = Vec::new();

        for path in files.files_with_extensions(&["py", "js", "ts", "jsx", "tsx", "java", "go", "rb", "php"]) {
            if findings.len() >= self.max_findings {
                break;
            }

            let rel_path = path.strip_prefix(&self.repository_path).unwrap_or(path);

            if let Some(content) = files.content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for i in 0..lines.len().saturating_sub(1) {
                    let line = lines[i];
                    let next = lines[i + 1].trim();

                    // Skip if next line is empty, closing brace, or comment
                    if next.is_empty()
                        || next == "}"
                        || next == "]"
                        || next == ")"  // Closing paren (multi-line calls)
                        || next.starts_with("//")
                        || next.starts_with("#")
                        || next.starts_with("else")
                        || next.starts_with("elif")
                        || next.starts_with("except")
                        || next.starts_with("catch")
                        || next.starts_with("finally")
                        || next.starts_with("case")
                        || next.starts_with("default")
                        || next.starts_with(")")  // Multi-line function call closing
                        || next.starts_with("ctx")  // Common continuation pattern
                        || next.starts_with("param")
                    // Common continuation pattern
                    {
                        continue;
                    }

                    // Skip if current line is inside a multi-line statement
                    if line.trim().ends_with(",") || line.trim().ends_with("(") {
                        continue;
                    }

                    if RETURN_PATTERN.is_match(line)
                        && !line.contains("if")
                        && !line.contains("?")
                    {
                        let curr_indent = line.len() - line.trim_start().len();
                        let next_indent = lines[i + 1].len() - next.len();

                        if next_indent >= curr_indent && !next.starts_with("}") {
                            findings.push(Finding {
                                id: String::new(),
                                detector: "UnreachableCodeDetector".to_string(),
                                severity: Severity::Medium,
                                title: "Unreachable code after return".to_string(),
                                description: format!(
                                    "Code after return/throw/exit will never execute:\n```\n{}\n{}\n```",
                                    line.trim(), next
                                ),
                                affected_files: vec![rel_path.to_path_buf()],
                                line_start: Some((i + 2) as u32),
                                line_end: Some((i + 2) as u32),
                                suggested_fix: Some(
                                    "Remove unreachable code or fix control flow logic.".to_string()
                                ),
                                estimated_effort: Some("10 minutes".to_string()),
                                category: Some("dead-code".to_string()),
                                cwe_id: Some("CWE-561".to_string()),
                                why_it_matters: Some(
                                    "Unreachable code indicates logic errors and adds confusion."
                                        .to_string()
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        findings
    }
}

impl Detector for UnreachableCodeDetector {
    fn name(&self) -> &'static str {
        "UnreachableCodeDetector"
    }

    fn description(&self) -> &'static str {
        "Detects unreachable code and dead functions"
    }

    fn category(&self) -> &'static str {
        "dead-code"
    }

    fn file_extensions(&self) -> &'static [&'static str] {
        &["py", "js", "ts", "jsx", "tsx", "java", "go", "rs", "c", "cpp", "cs"]
    }

    fn detect(&self, graph: &dyn crate::graph::GraphQuery, files: &dyn crate::detectors::file_provider::FileProvider) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();

        // Graph-based: find functions with zero callers
        findings.extend(self.find_dead_functions(graph));

        // Source-based: find code after return/throw
        findings.extend(self.find_code_after_return(files));

        info!("UnreachableCodeDetector found {} findings", findings.len());
        Ok(findings)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::{CodeEdge, CodeNode, GraphStore};

    #[test]
    fn test_is_entry_point() {
        let detector = UnreachableCodeDetector::new(".");

        assert!(detector.is_entry_point("main", "src/main.py"));
        assert!(detector.is_entry_point("test_something", "tests/test_foo.py"));
        assert!(detector.is_entry_point("handle_request", "handlers/api.py"));
        assert!(detector.is_entry_point("GetUser", "api/user.go")); // Capitalized = exported
        assert!(!detector.is_entry_point("helper_func", "src/utils.py"));
    }

    #[test]
    fn test_find_dead_functions() {
        let graph = GraphStore::in_memory();

        // Add a dead function (no callers)
        graph.add_node(
            CodeNode::function("dead_func", "src/utils.py")
                .with_qualified_name("utils::dead_func")
                .with_lines(10, 20),
        );

        // Add a live function with a caller
        graph.add_node(
            CodeNode::function("live_func", "src/utils.py")
                .with_qualified_name("utils::live_func")
                .with_lines(30, 40),
        );
        graph.add_node(
            CodeNode::function("caller", "src/main.py")
                .with_qualified_name("main::caller")
                .with_lines(1, 10),
        );
        graph.add_edge_by_name("main::caller", "utils::live_func", CodeEdge::calls());

        let detector = UnreachableCodeDetector::new(".");
        let findings = detector.find_dead_functions(&graph);

        assert_eq!(findings.len(), 1);
        assert!(findings[0].title.contains("dead_func"));
    }
}
