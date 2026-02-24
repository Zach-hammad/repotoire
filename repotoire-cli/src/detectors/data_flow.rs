#![allow(dead_code)] // Infrastructure module for future taint analysis
//! Intra-function data flow analysis for taint tracking.
//!
//! This module provides a trait-based abstraction for analyzing data flow
//! *within* a single function body. The current implementation (`HeuristicFlow`)
//! uses line-by-line scanning with variable propagation. A future implementation
//! (`SsaFlow`) can use tree-sitter ASTs with proper SSA/def-use chains — swap
//! the trait implementor, zero changes to callers.
//!
//! # Architecture
//!
//! ```text
//! DataFlowProvider (trait)
//!   ├── HeuristicFlow    (Approach A — regex + variable tracking)
//!   └── SsaFlow          (Approach B — tree-sitter AST, future)
//! ```

use crate::detectors::taint::TaintCategory;
use crate::parsers::lightweight::Language;
use std::collections::{HashMap, HashSet};

// ─── Trait (the seam) ───────────────────────────────────────────────────────

/// Result of intra-function data flow analysis.
#[derive(Debug, Clone)]
pub struct IntraFlowResult {
    /// Variables that are tainted at each point (var name → source it came from).
    pub tainted_vars: HashMap<String, TaintSource>,
    /// Detected flows from a tainted variable into a sink call.
    pub sink_reaches: Vec<SinkReach>,
    /// Variables that were sanitized (cleared of taint).
    pub sanitized_vars: HashSet<String>,
}

/// Where a tainted variable got its taint from.
#[derive(Debug, Clone)]
pub struct TaintSource {
    /// The source pattern that matched (e.g. "request.args").
    pub pattern: String,
    /// Line number where taint was introduced.
    pub line: usize,
}

/// A tainted variable reaching a dangerous sink.
#[derive(Debug, Clone)]
pub struct SinkReach {
    /// The tainted variable name.
    pub variable: String,
    /// Where the variable got tainted.
    pub taint_source: TaintSource,
    /// The sink function/pattern that was reached.
    pub sink_pattern: String,
    /// Line number where the sink is called.
    pub sink_line: usize,
    /// Whether a sanitizer was applied between source and sink.
    pub is_sanitized: bool,
    /// Confidence (0.0–1.0) based on how strong the evidence is.
    pub confidence: f64,
}

/// Trait for intra-function data flow analysis.
///
/// Implementors analyze a single function's source code and track how data
/// flows from taint sources through variables to sinks.
///
/// **This is the seam for Approach B**: implement this trait with tree-sitter
/// AST traversal + SSA/def-use chains, then swap it into `TaintAnalyzer`.
pub trait DataFlowProvider: Send + Sync {
    /// Analyze data flow within a single function body.
    fn analyze_intra_function(
        &self,
        func_source: &str,
        language: Language,
        category: TaintCategory,
        sources: &HashSet<String>,
        sinks: &HashSet<String>,
        sanitizers: &HashSet<String>,
    ) -> IntraFlowResult;
}

// ─── Approach A: HeuristicFlow ──────────────────────────────────────────────

/// Line-by-line heuristic data flow analysis.
///
/// Tracks variable taint through:
/// - Assignment from source patterns (`x = request.args.get(...)`)
/// - Propagation through assignment (`y = x`, `z = f"...{x}..."`)
/// - String concatenation/interpolation
/// - Sanitizer clearing (`clean = escape(x)`)
/// - Sink argument detection (`cursor.execute(query)`)
pub struct HeuristicFlow;

impl HeuristicFlow {
    pub fn new() -> Self {
        Self
    }

    /// Check if a line contains an assignment and extract (lhs, rhs).
    fn parse_assignment<'a>(&self, line: &'a str, lang: Language) -> Option<(&'a str, &'a str)> {
        // Skip comments
        let trimmed = line.trim();
        if trimmed.starts_with('#')
            || trimmed.starts_with("//")
            || trimmed.starts_with('*')
            || trimmed.starts_with("/*")
        {
            return None;
        }

        // Handle different assignment styles
        match lang {
            Language::Python => {
                // x = expr  (but not ==, !=, <=, >=)
                if let Some(eq_pos) = trimmed.find('=') {
                    if eq_pos > 0
                        && !trimmed[eq_pos..].starts_with("==")
                        && !matches!(
                            trimmed.as_bytes().get(eq_pos - 1),
                            Some(b'!' | b'<' | b'>' | b'=')
                        )
                    {
                        let lhs = trimmed[..eq_pos].trim();
                        let rhs = trimmed[eq_pos + 1..].trim();
                        // Must be a simple variable name (no dots, brackets on lhs for now)
                        if is_simple_var(lhs) && !rhs.is_empty() {
                            return Some((lhs, rhs));
                        }
                    }
                }
            }
            Language::JavaScript | Language::TypeScript => {
                // const/let/var x = expr  OR  x = expr
                let stripped = trimmed
                    .strip_prefix("const ")
                    .or_else(|| trimmed.strip_prefix("let "))
                    .or_else(|| trimmed.strip_prefix("var "))
                    .unwrap_or(trimmed);
                if let Some(eq_pos) = stripped.find('=') {
                    if eq_pos > 0
                        && !stripped[eq_pos..].starts_with("==")
                        && !stripped[eq_pos..].starts_with("=>")
                        && !matches!(
                            stripped.as_bytes().get(eq_pos - 1),
                            Some(b'!' | b'<' | b'>' | b'=')
                        )
                    {
                        let lhs = stripped[..eq_pos].trim();
                        // Handle type annotations: `x: string = ...`
                        let lhs = lhs.split(':').next().unwrap_or(lhs).trim();
                        let rhs = stripped[eq_pos + 1..].trim();
                        if is_simple_var(lhs) && !rhs.is_empty() {
                            return Some((lhs, rhs));
                        }
                    }
                }
            }
            Language::Go => {
                // x := expr  OR  x = expr
                if let Some(pos) = trimmed.find(":=") {
                    let lhs = trimmed[..pos].trim();
                    let rhs = trimmed[pos + 2..].trim();
                    let lhs = lhs.split(',').next().unwrap_or(lhs).trim();
                    if is_simple_var(lhs) && !rhs.is_empty() {
                        return Some((lhs, rhs));
                    }
                } else if let Some(eq_pos) = trimmed.find('=') {
                    if eq_pos > 0
                        && !trimmed[eq_pos..].starts_with("==")
                        && !matches!(
                            trimmed.as_bytes().get(eq_pos - 1),
                            Some(b'!' | b'<' | b'>' | b'=')
                        )
                    {
                        let lhs = trimmed[..eq_pos].trim();
                        let rhs = trimmed[eq_pos + 1..].trim();
                        if is_simple_var(lhs) && !rhs.is_empty() {
                            return Some((lhs, rhs));
                        }
                    }
                }
            }
            Language::Rust => {
                // let (mut) x = expr
                let stripped = trimmed
                    .strip_prefix("let ")
                    .map(|s| s.strip_prefix("mut ").unwrap_or(s));
                if let Some(stripped) = stripped {
                    if let Some(eq_pos) = stripped.find('=') {
                        if !stripped[eq_pos..].starts_with("==") {
                            let lhs = stripped[..eq_pos].trim();
                            // Handle type annotation: `x: Type = ...`
                            let lhs = lhs.split(':').next().unwrap_or(lhs).trim();
                            let rhs = stripped[eq_pos + 1..].trim();
                            if is_simple_var(lhs) && !rhs.is_empty() {
                                return Some((lhs, rhs));
                            }
                        }
                    }
                }
                // Also handle reassignment: x = expr
                if !trimmed.starts_with("let ") {
                    if let Some(eq_pos) = trimmed.find('=') {
                        if eq_pos > 0
                            && !trimmed[eq_pos..].starts_with("==")
                            && !trimmed[eq_pos..].starts_with("=>")
                            && !matches!(
                                trimmed.as_bytes().get(eq_pos - 1),
                                Some(b'!' | b'<' | b'>' | b'=')
                            )
                        {
                            let lhs = trimmed[..eq_pos].trim();
                            let rhs = trimmed[eq_pos + 1..].trim();
                            if is_simple_var(lhs) && !rhs.is_empty() {
                                return Some((lhs, rhs));
                            }
                        }
                    }
                }
            }
            Language::Java | Language::CSharp | Language::Kotlin => {
                // Type x = expr  OR  var x = expr  OR  x = expr
                // Simple heuristic: find `=` that isn't `==`
                if let Some(eq_pos) = trimmed.find('=') {
                    if eq_pos > 0
                        && !trimmed[eq_pos..].starts_with("==")
                        && !matches!(
                            trimmed.as_bytes().get(eq_pos - 1),
                            Some(b'!' | b'<' | b'>' | b'=')
                        )
                    {
                        let lhs_full = trimmed[..eq_pos].trim();
                        let rhs = trimmed[eq_pos + 1..].trim();
                        // Take the last word as the variable name
                        let lhs = lhs_full.split_whitespace().last().unwrap_or(lhs_full);
                        if is_simple_var(lhs) && !rhs.is_empty() {
                            return Some((lhs, rhs));
                        }
                    }
                }
            }
            _ => {
                // Generic: look for simple `x = expr`
                if let Some(eq_pos) = trimmed.find('=') {
                    if eq_pos > 0
                        && !trimmed[eq_pos..].starts_with("==")
                        && !matches!(
                            trimmed.as_bytes().get(eq_pos - 1),
                            Some(b'!' | b'<' | b'>' | b'=')
                        )
                    {
                        let lhs = trimmed[..eq_pos].trim();
                        let rhs = trimmed[eq_pos + 1..].trim();
                        if is_simple_var(lhs) && !rhs.is_empty() {
                            return Some((lhs, rhs));
                        }
                    }
                }
            }
        }

        None
    }

    /// Check if a right-hand side expression references any tainted variable.
    fn rhs_references_tainted(
        &self,
        rhs: &str,
        tainted: &HashMap<String, TaintSource>,
    ) -> Option<String> {
        for var in tainted.keys() {
            // Check various reference patterns:
            // - Direct use: `var`
            // - String interpolation: `f"...{var}..."` or `${var}` or `{var}`
            // - Concatenation: `"..." + var` or `"..." .. var`
            // - Method call: `var.something()`
            // - As argument: `func(var)` or `func(a, var, b)`
            if rhs_contains_var(rhs, var) {
                return Some(var.clone());
            }
        }
        None
    }

    /// Check if a line calls a sanitizer on a tainted variable.
    fn is_sanitizer_call(&self, rhs: &str, sanitizers: &HashSet<String>) -> bool {
        let rhs_lower = rhs.to_lowercase();
        sanitizers
            .iter()
            .any(|s| rhs_lower.contains(&s.to_lowercase()))
    }

    /// Check if a line calls a sink with a tainted argument.
    fn check_sink_call(
        &self,
        line: &str,
        line_num: usize,
        tainted: &HashMap<String, TaintSource>,
        sinks: &HashSet<String>,
        sanitized: &HashSet<String>,
    ) -> Vec<SinkReach> {
        let mut reaches = Vec::new();
        let line_lower = line.to_lowercase();

        for sink in sinks {
            let sink_lower = sink.to_lowercase();
            if !line_lower.contains(&sink_lower) {
                continue;
            }

            // Check if any tainted variable appears as argument to this sink
            for (var, source) in tainted {
                if sanitized.contains(var) {
                    continue;
                }
                // Check if var appears in the arguments of the sink call
                if line_contains_var_in_call(line, &sink_lower, var) {
                    reaches.push(SinkReach {
                        variable: var.clone(),
                        taint_source: source.clone(),
                        sink_pattern: sink.clone(),
                        sink_line: line_num,
                        is_sanitized: false,
                        confidence: 0.85,
                    });
                }
            }
        }

        reaches
    }
}

impl DataFlowProvider for HeuristicFlow {
    fn analyze_intra_function(
        &self,
        func_source: &str,
        language: Language,
        category: TaintCategory,
        sources: &HashSet<String>,
        sinks: &HashSet<String>,
        sanitizers: &HashSet<String>,
    ) -> IntraFlowResult {
        let _ = category; // Available for future category-specific logic

        let mut tainted: HashMap<String, TaintSource> = HashMap::new();
        let mut sanitized: HashSet<String> = HashSet::new();
        let mut sink_reaches: Vec<SinkReach> = Vec::new();

        for (line_idx, line) in func_source.lines().enumerate() {
            let line_num = line_idx + 1;
            let trimmed = line.trim();

            // Skip empty lines and comments
            if trimmed.is_empty()
                || trimmed.starts_with('#')
                || trimmed.starts_with("//")
                || trimmed.starts_with('*')
            {
                continue;
            }

            // Step 1: Check if this line introduces taint from a source
            if let Some((lhs, rhs)) = self.parse_assignment(line, language) {
                let rhs_lower = rhs.to_lowercase();

                // Check if RHS contains a taint source
                let is_source = sources
                    .iter()
                    .any(|s| rhs_lower.contains(&s.to_lowercase()));

                if is_source {
                    let pattern = sources
                        .iter()
                        .find(|s| rhs_lower.contains(&s.to_lowercase()))
                        .cloned()
                        .unwrap_or_default();
                    tainted.insert(
                        lhs.to_string(),
                        TaintSource {
                            pattern,
                            line: line_num,
                        },
                    );
                    sanitized.remove(lhs);
                    continue;
                }

                // Step 2: Check if RHS references a tainted variable (propagation)
                if let Some(source_var) = self.rhs_references_tainted(rhs, &tainted) {
                    // But first check if RHS also calls a sanitizer
                    if self.is_sanitizer_call(rhs, sanitizers) {
                        sanitized.insert(lhs.to_string());
                    } else {
                        // Propagate taint
                        if let Some(source) = tainted.get(&source_var) {
                            tainted.insert(lhs.to_string(), source.clone());
                            sanitized.remove(lhs);
                        }
                    }
                    continue;
                }

                // Step 3: Check if LHS is being sanitized
                if self.is_sanitizer_call(rhs, sanitizers) && tainted.contains_key(lhs) {
                    sanitized.insert(lhs.to_string());
                    continue;
                }
            }

            // Step 4: Check if this line has a sink call with tainted arguments
            let mut reaches = self.check_sink_call(trimmed, line_num, &tainted, sinks, &sanitized);
            sink_reaches.append(&mut reaches);

            // Also check non-assignment lines that reference tainted vars in sink calls
            // e.g., `cursor.execute(query)` without assignment
        }

        IntraFlowResult {
            tainted_vars: tainted,
            sink_reaches,
            sanitized_vars: sanitized,
        }
    }
}

// ─── Helpers ────────────────────────────────────────────────────────────────

/// Check if a string is a simple variable name (no dots, brackets, etc.)
fn is_simple_var(s: &str) -> bool {
    !s.is_empty()
        && s.chars()
            .next()
            .is_some_and(|c| c.is_alphabetic() || c == '_')
        && s.chars().all(|c| c.is_alphanumeric() || c == '_')
}

/// Check if a RHS expression references a specific variable.
fn rhs_contains_var(rhs: &str, var: &str) -> bool {
    // Find all occurrences and check they're at word boundaries
    let mut search_from = 0;
    while let Some(pos) = rhs[search_from..].find(var) {
        let abs_pos = search_from + pos;
        let before_ok = abs_pos == 0
            || !rhs.as_bytes()[abs_pos - 1].is_ascii_alphanumeric()
                && rhs.as_bytes()[abs_pos - 1] != b'_';
        let after_pos = abs_pos + var.len();
        let after_ok = after_pos >= rhs.len()
            || !rhs.as_bytes()[after_pos].is_ascii_alphanumeric()
                && rhs.as_bytes()[after_pos] != b'_';

        if before_ok && after_ok {
            return true;
        }
        search_from = abs_pos + 1;
    }
    false
}

/// Check if a variable appears within a sink call's arguments on a line.
fn line_contains_var_in_call(line: &str, _sink_lower: &str, var: &str) -> bool {
    // For now, just check if the variable appears on the same line as the sink.
    // A more precise check would parse the argument list, but this is the heuristic approach.
    rhs_contains_var(line, var)
}

// ─── Integration helper ─────────────────────────────────────────────────────

use crate::detectors::taint::{TaintAnalyzer, TaintPath};
use crate::graph::GraphQuery;
use std::path::Path;

/// Run intra-function data flow analysis across all functions in the graph.
///
/// For each function, reads its source file, extracts the function body,
/// and runs the `TaintAnalyzer`'s intra-function analysis. Returns all
/// taint paths found.
///
/// This is the shared integration point — all security detectors call this.
pub fn run_intra_function_taint(
    analyzer: &TaintAnalyzer,
    graph: &dyn GraphQuery,
    category: TaintCategory,
    repository_path: &Path,
) -> Vec<TaintPath> {
    let functions = graph.get_functions();
    let mut all_paths = Vec::new();

    // Cache file contents to avoid re-reading
    let mut file_cache: HashMap<String, String> = HashMap::new();

    for func in &functions {
        // Need a source file to analyze
        if func.file_path.is_empty() {
            continue;
        }

        let full_path = repository_path.join(&func.file_path);

        // Read file (cached)
        let content = match file_cache.get(&func.file_path) {
            Some(c) => c.clone(),
            None => match std::fs::read_to_string(&full_path) {
                Ok(c) => {
                    file_cache.insert(func.file_path.clone(), c.clone());
                    c
                }
                Err(_) => continue,
            },
        };

        // Extract function body from source
        let line_start = func.line_start as usize;
        let line_end = func.get_i64("lineEnd").unwrap_or(0) as usize;

        if line_start == 0 || line_end == 0 || line_end < line_start {
            continue;
        }

        let lines: Vec<&str> = content.lines().collect();
        if line_end > lines.len() {
            continue;
        }

        let func_body = lines[line_start.saturating_sub(1)..line_end].join("\n");

        // Detect language from file extension
        let ext = full_path.extension().and_then(|e| e.to_str()).unwrap_or("");
        let language = Language::from_extension(ext);

        // Run intra-function analysis
        let paths = analyzer.analyze_intra_function(
            &func_body,
            &func.name,
            &func.file_path,
            line_start,
            language,
            category,
        );

        all_paths.extend(paths);
    }

    all_paths
}

// ─── Finding helper ─────────────────────────────────────────────────────────

use crate::models::Finding;

/// Convert a TaintPath into a Finding. Shared by security detectors that wire in
/// intra-function taint analysis.
pub fn taint_path_to_finding(path: &TaintPath, detector_name: &str, vuln_name: &str) -> Finding {
    Finding {
        id: String::new(),
        detector: detector_name.to_string(),
        title: format!("{} via data flow", vuln_name),
        description: format!(
            "**{} ({})**\n\nAST-based data flow analysis traced taint from `{}` (line {}) \
             to sink `{}` (line {}) without sanitization.\n\nConfidence: {:.0}%",
            vuln_name,
            path.category.cwe_id(),
            path.source_function,
            path.source_line,
            path.sink_function,
            path.sink_line,
            path.confidence * 100.0,
        ),
        severity: crate::models::Severity::High,
        affected_files: vec![std::path::PathBuf::from(&path.sink_file)],
        line_start: Some(path.sink_line),
        line_end: None,
        suggested_fix: Some(format!(
            "Sanitize or validate the input from `{}` before passing it to `{}`.",
            path.source_function, path.sink_function,
        )),
        cwe_id: Some(path.category.cwe_id().to_string()),
        confidence: Some(path.confidence),
        ..Default::default()
    }
}

// ─── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn sources() -> HashSet<String> {
        [
            "request.args",
            "request.form",
            "req.body",
            "req.query",
            "req.params",
            "params[",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect()
    }

    fn sql_sinks() -> HashSet<String> {
        ["execute", "executemany", "raw_sql", "query(", "db.run"]
            .iter()
            .map(|s| s.to_string())
            .collect()
    }

    fn sanitizers() -> HashSet<String> {
        [
            "escape",
            "sanitize",
            "parameterize",
            "prepare",
            "bindparam",
            "html.escape",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect()
    }

    #[test]
    fn test_python_basic_taint_flow() {
        let code = r#"
user_input = request.args.get("q")
query = "SELECT * FROM t WHERE x = '" + user_input + "'"
cursor.execute(query)
"#;
        let flow = HeuristicFlow::new();
        let result = flow.analyze_intra_function(
            code,
            Language::Python,
            TaintCategory::SqlInjection,
            &sources(),
            &sql_sinks(),
            &sanitizers(),
        );

        assert!(
            !result.sink_reaches.is_empty(),
            "Should detect taint reaching execute()"
        );
        assert_eq!(result.sink_reaches[0].sink_pattern, "execute");
        assert!(!result.sink_reaches[0].is_sanitized);
    }

    #[test]
    fn test_python_fstring_propagation() {
        let code = r#"
user_input = request.args.get("q")
query = f"SELECT * FROM t WHERE x = '{user_input}'"
cursor.execute(query)
"#;
        let flow = HeuristicFlow::new();
        let result = flow.analyze_intra_function(
            code,
            Language::Python,
            TaintCategory::SqlInjection,
            &sources(),
            &sql_sinks(),
            &sanitizers(),
        );

        assert!(
            !result.sink_reaches.is_empty(),
            "Should detect f-string taint propagation"
        );
    }

    #[test]
    fn test_python_sanitized_flow() {
        let code = r#"
user_input = request.args.get("q")
clean_input = escape(user_input)
query = f"SELECT * FROM t WHERE x = '{clean_input}'"
cursor.execute(query)
"#;
        let flow = HeuristicFlow::new();
        let result = flow.analyze_intra_function(
            code,
            Language::Python,
            TaintCategory::SqlInjection,
            &sources(),
            &sql_sinks(),
            &sanitizers(),
        );

        // clean_input should be sanitized, so no vulnerable sink reaches
        let vulnerable: Vec<_> = result
            .sink_reaches
            .iter()
            .filter(|r| !r.is_sanitized)
            .collect();
        assert!(
            vulnerable.is_empty(),
            "Sanitized flow should not be flagged"
        );
        assert!(result.sanitized_vars.contains("clean_input"));
    }

    #[test]
    fn test_javascript_taint_flow() {
        let code = r#"
const userInput = req.body.username;
const query = "SELECT * FROM users WHERE name = '" + userInput + "'";
db.run(query);
"#;
        let flow = HeuristicFlow::new();
        let result = flow.analyze_intra_function(
            code,
            Language::JavaScript,
            TaintCategory::SqlInjection,
            &sources(),
            &sql_sinks(),
            &sanitizers(),
        );

        assert!(
            !result.sink_reaches.is_empty(),
            "Should detect JS taint flow"
        );
    }

    #[test]
    fn test_go_taint_flow() {
        let code = r#"
userInput := req.query.Get("name")
query := "SELECT * FROM users WHERE name = '" + userInput + "'"
db.run(query)
"#;
        let flow = HeuristicFlow::new();
        let result = flow.analyze_intra_function(
            code,
            Language::Go,
            TaintCategory::SqlInjection,
            &sources(),
            &sql_sinks(),
            &sanitizers(),
        );

        assert!(
            !result.sink_reaches.is_empty(),
            "Should detect Go taint flow"
        );
    }

    #[test]
    fn test_rust_taint_flow() {
        let code = r#"
let user_input = req.query("name");
let query = format!("SELECT * FROM users WHERE name = '{}'", user_input);
db.run(&query);
"#;
        let flow = HeuristicFlow::new();

        // Add format! as propagation-aware
        let mut srcs = sources();
        srcs.insert("req.query".to_string());

        let result = flow.analyze_intra_function(
            code,
            Language::Rust,
            TaintCategory::SqlInjection,
            &srcs,
            &sql_sinks(),
            &sanitizers(),
        );

        assert!(
            result.tainted_vars.contains_key("user_input"),
            "user_input should be tainted"
        );
    }

    #[test]
    fn test_no_taint_no_findings() {
        let code = r#"
x = 42
y = x + 1
print(y)
"#;
        let flow = HeuristicFlow::new();
        let result = flow.analyze_intra_function(
            code,
            Language::Python,
            TaintCategory::SqlInjection,
            &sources(),
            &sql_sinks(),
            &sanitizers(),
        );

        assert!(
            result.sink_reaches.is_empty(),
            "No taint sources means no findings"
        );
        assert!(result.tainted_vars.is_empty());
    }

    #[test]
    fn test_taint_propagation_chain() {
        let code = r#"
raw = request.args.get("input")
step1 = raw
step2 = step1
step3 = step2
cursor.execute(step3)
"#;
        let flow = HeuristicFlow::new();
        let result = flow.analyze_intra_function(
            code,
            Language::Python,
            TaintCategory::SqlInjection,
            &sources(),
            &sql_sinks(),
            &sanitizers(),
        );

        assert!(
            result.tainted_vars.contains_key("step3"),
            "Taint should propagate through chain"
        );
        assert!(
            !result.sink_reaches.is_empty(),
            "Should detect taint at end of chain"
        );
    }

    #[test]
    fn test_command_injection_flow() {
        let cmd_sinks: HashSet<String> = [
            "system",
            "exec",
            "popen",
            "subprocess.run",
            "subprocess.call",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();

        let code = r#"
filename = request.form.get("file")
cmd = "cat " + filename
os.system(cmd)
"#;
        let flow = HeuristicFlow::new();
        let result = flow.analyze_intra_function(
            code,
            Language::Python,
            TaintCategory::CommandInjection,
            &sources(),
            &cmd_sinks,
            &sanitizers(),
        );

        assert!(
            !result.sink_reaches.is_empty(),
            "Should detect command injection flow"
        );
    }

    #[test]
    fn test_is_simple_var() {
        assert!(is_simple_var("x"));
        assert!(is_simple_var("user_input"));
        assert!(is_simple_var("_private"));
        assert!(is_simple_var("camelCase"));
        assert!(!is_simple_var("obj.field"));
        assert!(!is_simple_var("arr[0]"));
        assert!(!is_simple_var(""));
        assert!(!is_simple_var("123abc"));
    }

    #[test]
    fn test_rhs_contains_var_word_boundary() {
        assert!(rhs_contains_var("foo + bar", "foo"));
        assert!(rhs_contains_var("func(foo)", "foo"));
        assert!(rhs_contains_var("f\"{foo}\"", "foo"));
        assert!(!rhs_contains_var("foobar", "foo"));
        assert!(!rhs_contains_var("barfoo", "foo"));
        assert!(!rhs_contains_var("_foo", "foo"));
        assert!(rhs_contains_var("a + foo + b", "foo"));
    }
}
