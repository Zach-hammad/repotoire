//! Taint Analysis for Security Vulnerability Detection
//!
//! This module provides graph-based data flow analysis to trace potentially malicious
//! data from untrusted sources (user input) to dangerous sinks (SQL queries, shell commands, etc.).
//!
//! # Architecture
//!
//! ```text
//! ┌─────────────────────────────────────────────────────────────┐
//! │                     TaintAnalyzer                           │
//! │  - Defines sources (user input entry points)                │
//! │  - Defines sinks (dangerous operations)                     │
//! │  - Defines sanitizers (functions that neutralize taint)     │
//! └─────────────────────────────────────────────────────────────┘
//!                              │
//!                              ▼
//! ┌─────────────────────────────────────────────────────────────┐
//! │                    trace_taint()                            │
//! │  - BFS through call graph from source functions             │
//! │  - Track path through function calls                        │
//! │  - Identify when tainted data reaches a sink                │
//! └─────────────────────────────────────────────────────────────┘
//!                              │
//!                              ▼
//! ┌─────────────────────────────────────────────────────────────┐
//! │                     TaintPath                               │
//! │  - Source function (where taint originates)                 │
//! │  - Sink function (dangerous operation)                      │
//! │  - Call chain (functions between source and sink)           │
//! │  - Sanitized flag (whether sanitizer was in path)           │
//! └─────────────────────────────────────────────────────────────┘
//! ```
//!
//! # Usage
//!
//! ```ignore
//! use repotoire_cli::detectors::taint::{TaintAnalyzer, TaintCategory};
//!
//! let analyzer = TaintAnalyzer::new();
//! let paths = analyzer.trace_taint(&graph, TaintCategory::SqlInjection);
//!
//! for path in paths {
//!     if !path.is_sanitized {
//!         // Critical: unsanitized taint flow to SQL sink
//!     }
//! }
//! ```

mod types;
pub use types::*;

pub mod centralized;
pub use centralized::CentralizedTaintResults;

mod heuristic;

#[cfg(test)]
mod tests;

use crate::graph::{GraphStore, GraphQuery, NodeKind};
use crate::models::Finding;
use crate::parsers::lightweight::Language;
use std::collections::{HashMap, HashSet, VecDeque};
use std::path::Path;
use std::sync::Arc;

/// Taint analyzer that uses the code graph for data flow analysis
pub struct TaintAnalyzer {
    /// Source patterns by category - functions that introduce tainted data
    sources: HashMap<TaintCategory, HashSet<String>>,
    /// Sink patterns by category - dangerous functions
    sinks: HashMap<TaintCategory, HashSet<String>>,
    /// Sanitizer patterns by category - functions that neutralize taint
    sanitizers: HashMap<TaintCategory, HashSet<String>>,
    /// Generic sanitizers that apply to all categories
    generic_sanitizers: HashSet<String>,
    /// Maximum depth for BFS traversal
    max_depth: usize,
    /// Optional value store for future enhanced taint analysis
    value_store: Option<Arc<crate::values::store::ValueStore>>,
}

impl Default for TaintAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl TaintAnalyzer {
    /// Create a new TaintAnalyzer with default patterns
    pub fn new() -> Self {
        let mut analyzer = Self {
            sources: HashMap::new(),
            sinks: HashMap::new(),
            sanitizers: HashMap::new(),
            generic_sanitizers: HashSet::new(),
            max_depth: 10,
            value_store: None,
        };
        analyzer.init_default_patterns();
        analyzer
    }

    /// Create with custom max depth
    #[allow(dead_code)] // Public API builder method
    pub fn with_max_depth(mut self, depth: usize) -> Self {
        self.max_depth = depth;
        self
    }

    /// Attach a value store for future enhanced taint analysis
    #[allow(dead_code)] // Public API builder method
    pub fn with_value_store(mut self, store: Arc<crate::values::store::ValueStore>) -> Self {
        self.value_store = Some(store);
        self
    }

    /// Initialize default taint source/sink/sanitizer patterns
    fn init_default_patterns(&mut self) {
        self.add_common_sources();
        self.add_sql_patterns();
        self.add_command_patterns();
        self.add_xss_patterns();
        self.add_ssrf_patterns();
        self.add_path_patterns();
        self.add_code_patterns();
        self.add_log_patterns();
        self.add_generic_sanitizers();
    }

    /// Add common taint sources (user input entry points) for all categories
    fn add_common_sources(&mut self) {
        let mut all_sources = HashSet::new();

        // Express.js (Node.js)
        for pattern in &[
            "req.body",
            "req.query",
            "req.params",
            "req.headers",
            "req.cookies",
            "request.body",
            "request.query",
            "request.params",
        ] {
            all_sources.insert(pattern.to_string());
        }

        // Flask (Python)
        for pattern in &[
            "request.form",
            "request.args",
            "request.data",
            "request.json",
            "request.files",
            "request.cookies",
            "request.headers",
        ] {
            all_sources.insert(pattern.to_string());
        }

        // Django (Python)
        for pattern in &[
            "request.GET",
            "request.POST",
            "request.body",
            "request.FILES",
        ] {
            all_sources.insert(pattern.to_string());
        }

        // Gin (Go)
        for pattern in &[
            "c.Param",
            "c.Query",
            "c.PostForm",
            "c.FormValue",
            "c.GetHeader",
            "c.BindJSON",
            "c.ShouldBindJSON",
        ] {
            all_sources.insert(pattern.to_string());
        }

        // FastAPI (Python)
        for pattern in &[
            "request.query_params",
            "request.path_params",
            "request.form",
            "request.body",
        ] {
            all_sources.insert(pattern.to_string());
        }

        // Generic input
        for pattern in &[
            "input(",
            "raw_input(",
            "sys.stdin",
            "os.environ",
            "getenv(",
            "process.env",
        ] {
            all_sources.insert(pattern.to_string());
        }

        // Apply sources to all categories (they're the same entry points)
        for category in &[
            TaintCategory::SqlInjection,
            TaintCategory::CommandInjection,
            TaintCategory::Xss,
            TaintCategory::Ssrf,
            TaintCategory::PathTraversal,
            TaintCategory::CodeInjection,
            TaintCategory::LogInjection,
        ] {
            self.sources.insert(*category, all_sources.clone());
        }
    }

    /// Add SQL injection sinks and sanitizers
    fn add_sql_patterns(&mut self) {
        let mut sinks = HashSet::new();
        for pattern in &[
            // Python
            "cursor.execute",
            "cursor.executemany",
            "cursor.executescript",
            "connection.execute",
            "engine.execute",
            "session.execute",
            "db.execute",
            // SQLAlchemy
            "text(",
            ".raw(",
            "from_statement",
            // Django
            "objects.raw",
            "objects.extra",
            "RawSQL",
            // Node.js
            "pool.query",
            "client.query",
            "connection.query",
            "db.query",
            "knex.raw",
            "sequelize.query",
            // Go
            "db.Query",
            "db.QueryRow",
            "db.Exec",
            "db.Prepare",
            "tx.Query",
            "tx.Exec",
        ] {
            sinks.insert(pattern.to_string());
        }
        self.sinks.insert(TaintCategory::SqlInjection, sinks);

        let mut sanitizers = HashSet::new();
        for pattern in &[
            // Parameterized queries (these patterns in the call chain suggest safe usage)
            "parameterize",
            "prepare",
            "bind",
            // ORMs (proper ORM usage is safe)
            "filter(",
            "where(",
            "findOne",
            "findById",
            "findByPk",
            // Escaping
            "escape(",
            "quote(",
            "mogrify",
        ] {
            sanitizers.insert(pattern.to_string());
        }
        self.sanitizers
            .insert(TaintCategory::SqlInjection, sanitizers);
    }

    /// Add command injection sinks and sanitizers
    fn add_command_patterns(&mut self) {
        let mut sinks = HashSet::new();
        for pattern in &[
            // Python
            "os.system",
            "os.popen",
            "subprocess.call",
            "subprocess.run",
            "subprocess.Popen",
            "commands.getoutput",
            // Node.js
            "child_process.exec",
            "child_process.execSync",
            "child_process.spawn",
            "child_process.spawnSync",
            "execSync",
            "exec(",
            "spawn(",
            // Go
            "exec.Command",
            "exec.CommandContext",
            // PHP
            "shell_exec",
            "system(",
            "passthru",
            "proc_open",
        ] {
            sinks.insert(pattern.to_string());
        }
        self.sinks.insert(TaintCategory::CommandInjection, sinks);

        let mut sanitizers = HashSet::new();
        for pattern in &[
            "shlex.quote",
            "shlex.split",
            "pipes.quote",
            "shell=False", // subprocess flag
            "escapeshellarg",
            "escapeshellcmd",
        ] {
            sanitizers.insert(pattern.to_string());
        }
        self.sanitizers
            .insert(TaintCategory::CommandInjection, sanitizers);
    }

    /// Add XSS sinks and sanitizers
    fn add_xss_patterns(&mut self) {
        let mut sinks = HashSet::new();
        for pattern in &[
            // JavaScript
            "innerHTML",
            "outerHTML",
            "document.write",
            "document.writeln",
            // React
            "dangerouslySetInnerHTML",
            // Vue
            "v-html",
            // Angular
            "ng-bind-html",
            "[innerHTML]",
            // Python templates
            "render_template_string",
            "Markup(",
            "|safe",
        ] {
            sinks.insert(pattern.to_string());
        }
        self.sinks.insert(TaintCategory::Xss, sinks);

        let mut sanitizers = HashSet::new();
        for pattern in &[
            "escapeHtml",
            "escape(",
            "encode(",
            "htmlspecialchars",
            "sanitize",
            "DOMPurify",
            "xss(",
            "textContent", // safe alternative to innerHTML
            "innerText",
            "createTextNode",
        ] {
            sanitizers.insert(pattern.to_string());
        }
        self.sanitizers.insert(TaintCategory::Xss, sanitizers);
    }

    /// Add SSRF sinks and sanitizers
    fn add_ssrf_patterns(&mut self) {
        let mut sinks = HashSet::new();
        for pattern in &[
            // Python
            "requests.get",
            "requests.post",
            "requests.put",
            "requests.delete",
            "requests.head",
            "urllib.urlopen",
            "urllib.request.urlopen",
            "httpx.get",
            "httpx.post",
            "aiohttp.get",
            // Node.js
            "fetch(",
            "axios.get",
            "axios.post",
            "http.get",
            "https.get",
            "got(",
            "request(",
            // Go
            "http.Get",
            "http.Post",
            "http.NewRequest",
            "client.Get",
            "client.Do",
        ] {
            sinks.insert(pattern.to_string());
        }
        self.sinks.insert(TaintCategory::Ssrf, sinks);

        let mut sanitizers = HashSet::new();
        for pattern in &[
            "validate_url",
            "is_safe_url",
            "url_validator",
            "allowlist",
            "whitelist",
            "check_host",
        ] {
            sanitizers.insert(pattern.to_string());
        }
        self.sanitizers.insert(TaintCategory::Ssrf, sanitizers);
    }

    /// Add path traversal sinks and sanitizers
    fn add_path_patterns(&mut self) {
        let mut sinks = HashSet::new();
        for pattern in &[
            // Python
            "open(",
            "os.path.join",
            "pathlib.Path",
            "shutil.copy",
            "shutil.move",
            "send_file",
            "send_from_directory",
            // Node.js
            "fs.readFile",
            "fs.writeFile",
            "fs.readFileSync",
            "fs.writeFileSync",
            "path.join",
            "path.resolve",
            // Go
            "os.Open",
            "os.Create",
            "ioutil.ReadFile",
            "ioutil.WriteFile",
            "filepath.Join",
        ] {
            sinks.insert(pattern.to_string());
        }
        self.sinks.insert(TaintCategory::PathTraversal, sinks);

        let mut sanitizers = HashSet::new();
        for pattern in &[
            "basename",
            "os.path.basename",
            "path.basename",
            "filepath.Base",
            "realpath",
            "abspath",
            "secure_filename",
            "sanitize_path",
        ] {
            sanitizers.insert(pattern.to_string());
        }
        self.sanitizers
            .insert(TaintCategory::PathTraversal, sanitizers);
    }

    /// Add code injection sinks and sanitizers
    fn add_code_patterns(&mut self) {
        let mut sinks = HashSet::new();
        for pattern in &[
            // Python
            "eval(",
            "exec(",
            "compile(",
            "__import__",
            // JavaScript
            "eval(",
            "Function(",
            "setTimeout(",  // with string arg
            "setInterval(", // with string arg
            // Go
            // (Go doesn't have direct eval, but template injection is similar)
            "template.HTML",
        ] {
            sinks.insert(pattern.to_string());
        }
        self.sinks.insert(TaintCategory::CodeInjection, sinks);

        let mut sanitizers = HashSet::new();
        for pattern in &["ast.literal_eval", "json.loads", "JSON.parse"] {
            sanitizers.insert(pattern.to_string());
        }
        self.sanitizers
            .insert(TaintCategory::CodeInjection, sanitizers);
    }

    /// Add log injection sinks and sanitizers
    fn add_log_patterns(&mut self) {
        let mut sinks = HashSet::new();
        for pattern in &[
            // Python
            "logging.info",
            "logging.debug",
            "logging.warning",
            "logging.error",
            "logging.critical",
            "logger.info",
            "logger.debug",
            "logger.warning",
            "logger.error",
            "log.info",
            "log.debug",
            "log.warn",
            "log.error",
            // JavaScript
            "console.log",
            "console.error",
            "console.warn",
            "console.info",
            // Go
            "log.Print",
            "log.Printf",
            "log.Println",
            "log.Fatal",
        ] {
            sinks.insert(pattern.to_string());
        }
        self.sinks.insert(TaintCategory::LogInjection, sinks);

        let mut sanitizers = HashSet::new();
        for pattern in &["strip(", "replace(", "sanitize_log"] {
            sanitizers.insert(pattern.to_string());
        }
        self.sanitizers
            .insert(TaintCategory::LogInjection, sanitizers);
    }

    /// Add generic sanitizers that apply to all categories
    fn add_generic_sanitizers(&mut self) {
        for pattern in &["validate", "sanitize", "clean", "safe_", "_safe"] {
            self.generic_sanitizers.insert(pattern.to_string());
        }
    }

    /// Check if a function name matches any source pattern for the category.
    /// Uses word-boundary matching to avoid false positives like 'id' matching 'valid' (#28).
    #[allow(dead_code)] // Public API for taint analysis
    pub fn is_source(&self, func_name: &str, category: TaintCategory) -> bool {
        if let Some(sources) = self.sources.get(&category) {
            let name_lower = func_name.to_lowercase();
            sources
                .iter()
                .any(|s| word_boundary_match(&name_lower, &s.to_lowercase()))
        } else {
            false
        }
    }

    /// Check if a function name matches any sink pattern for the category.
    /// Uses word-boundary matching to avoid false positives (#28).
    pub fn is_sink(&self, func_name: &str, category: TaintCategory) -> bool {
        if let Some(sinks) = self.sinks.get(&category) {
            let name_lower = func_name.to_lowercase();
            sinks
                .iter()
                .any(|s| word_boundary_match(&name_lower, &s.to_lowercase()))
        } else {
            false
        }
    }

    /// Check if a function name matches any sanitizer pattern for the category.
    /// Uses word-boundary matching to avoid false positives (#28).
    pub fn is_sanitizer(&self, func_name: &str, category: TaintCategory) -> bool {
        let name_lower = func_name.to_lowercase();

        // Check category-specific sanitizers
        if let Some(sanitizers) = self.sanitizers.get(&category) {
            if sanitizers
                .iter()
                .any(|s| word_boundary_match(&name_lower, &s.to_lowercase()))
            {
                return true;
            }
        }

        // Check generic sanitizers
        self.generic_sanitizers
            .iter()
            .any(|s| word_boundary_match(&name_lower, &s.to_lowercase()))
    }

    /// Trace taint paths through the call graph for a specific category
    ///
    /// This uses BFS to find all paths from source functions to sink functions,
    /// tracking whether sanitizers are encountered along the way.
    pub fn trace_taint(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        category: TaintCategory,
    ) -> Vec<TaintPath> {
        self.trace_taint_with_functions(graph, category, None)
    }

    /// Trace taint paths, optionally reusing a pre-fetched function list.
    pub fn trace_taint_with_functions(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        category: TaintCategory,
        functions: Option<&[crate::graph::CodeNode]>,
    ) -> Vec<TaintPath> {
        let i = graph.interner();
        let owned_functions;
        let functions = match functions {
            Some(f) => f,
            None => {
                owned_functions = graph.get_functions_shared();
                &owned_functions
            }
        };

        // Find sink functions FIRST — if none exist, skip entirely
        let sink_funcs: Vec<_> = functions
            .iter()
            .filter(|f| {
                self.is_sink(f.node_name(i), category) || self.is_sink(f.qn(i), category)
            })
            .collect();

        if sink_funcs.is_empty() {
            return Vec::new();
        }

        // Build sink set ONCE (outside source loop)
        let sink_qns: HashSet<&str> = sink_funcs
            .iter()
            .map(|f| f.qn(i))
            .collect();

        // Find source functions
        let source_funcs: Vec<_> = functions
            .iter()
            .filter(|f| self.is_potential_source_function(graph, f, category))
            .collect();

        if source_funcs.is_empty() {
            return Vec::new();
        }
        let mut paths = Vec::new();

        // BFS from each source to find paths to sinks
        for source in &source_funcs {
            let source_paths = self.bfs_to_sinks(
                graph,
                source.qn(i),
                &sink_qns,
                category,
            );

            for (sink_qn, call_chain, sanitizer) in source_paths {
                if let Some(sink) = sink_funcs.iter().find(|f| f.qn(i) == sink_qn) {
                    paths.push(TaintPath {
                        source_function: source.node_name(i).to_string(),
                        source_file: source.path(i).to_string(),
                        source_line: source.line_start,
                        sink_function: sink.node_name(i).to_string(),
                        sink_file: sink.path(i).to_string(),
                        sink_line: sink.line_start,
                        category,
                        call_chain,
                        is_sanitized: sanitizer.is_some(),
                        sanitizer,
                        confidence: 0.7,
                    });
                }
            }
        }

        paths
    }

    /// Check if a function is a potential taint source (e.g., route handler).
    ///
    /// Uses a two-tier approach to avoid false positives in large non-web codebases:
    /// - **Strong signals** (route decorators, explicit source patterns) → always match
    /// - **Weak signals** (handler-like names: `get_*`, `handle_*`, `view`) → require
    ///   corroborating evidence from the file path (must look web-related)
    fn is_potential_source_function(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        func: &crate::graph::CodeNode,
        _category: TaintCategory,
    ) -> bool {
        let i = graph.interner();
        // Strong signal: route decorator — always a source
        // Check has_decorators flag first (cheap), then resolve from ExtraProps
        let has_route_decorator = func.has_decorators() && {
            if let Some(dec_key) = graph.extra_props(func.qualified_name).and_then(|ep| ep.decorators) {
                let d = i.resolve(dec_key);
                d.contains("@app.route")
                    || d.contains("@router")
                    || d.contains("@get")
                    || d.contains("@post")
                    || d.contains("@api")
            } else {
                false
            }
        };

        if has_route_decorator {
            return true;
        }

        // Strong signal: function name/qn contains actual source patterns (request.body, etc.)
        let name_lower = func.node_name(i).to_lowercase();
        let qn_lower = func.qn(i).to_lowercase();

        let references_source = if let Some(sources) = self.sources.get(&_category) {
            sources.iter().any(|s| {
                qn_lower.contains(&s.to_lowercase()) || name_lower.contains(&s.to_lowercase())
            })
        } else {
            false
        };

        if references_source {
            return true;
        }

        // Weak signal: handler-like name patterns — require corroborating file path evidence.
        // Without this, patterns like `get_*` match thousands of functions in non-web
        // codebases (e.g., CPython's get_item, get_type, get_value, ...).
        let has_handler_name = name_lower.contains("handler")
            || name_lower.contains("controller")
            || name_lower.contains("view")
            || name_lower.contains("endpoint")
            || name_lower.starts_with("get_")
            || name_lower.starts_with("post_")
            || name_lower.starts_with("put_")
            || name_lower.starts_with("delete_")
            || name_lower.starts_with("patch_")
            || name_lower.starts_with("handle_");

        if !has_handler_name {
            return false;
        }

        // Corroborating evidence: file path looks web-related
        let path_lower = func.path(i).to_lowercase();
        path_lower.contains("route")
            || path_lower.contains("view")
            || path_lower.contains("handler")
            || path_lower.contains("controller")
            || path_lower.contains("endpoint")
            || path_lower.contains("/api/")
            || path_lower.contains("/api.")
            || path_lower.contains("/web/")
            || path_lower.contains("/server/")
            || path_lower.contains("/app/")
            || path_lower.contains("app.")
    }

    /// BFS from a source function to find paths to any sink
    fn bfs_to_sinks(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        source_qn: &str,
        sink_qns: &HashSet<&str>,
        category: TaintCategory,
    ) -> Vec<(String, Vec<String>, Option<String>)> {
        let i = graph.interner();
        let mut results = Vec::new();
        let mut visited = HashSet::new();
        let mut queue = VecDeque::new();

        // (current_qn, path, sanitizer_found)
        queue.push_back((source_qn.to_string(), Vec::new(), None::<String>));
        visited.insert(source_qn.to_string());

        while let Some((current_qn, path, sanitizer)) = queue.pop_front() {
            if path.len() > self.max_depth {
                continue;
            }

            // Check if we've reached a sink
            if sink_qns.contains(current_qn.as_str()) {
                results.push((current_qn.clone(), path.clone(), sanitizer.clone()));
                continue;
            }

            // Get callees (functions called by current)
            let callees = graph.get_callees(&current_qn);

            for callee in callees {
                let callee_qn = callee.qn(i).to_string();
                if visited.contains(&callee_qn) {
                    continue;
                }

                visited.insert(callee_qn.clone());

                let callee_name = callee.node_name(i);
                let mut new_path = path.clone();
                new_path.push(callee_name.to_string());

                // Check if this callee is a sanitizer
                let new_sanitizer = if sanitizer.is_some() {
                    sanitizer.clone()
                } else if self.is_sanitizer(callee_name, category)
                    || self.is_sanitizer(&callee_qn, category)
                {
                    Some(callee_name.to_string())
                } else {
                    None
                };

                queue.push_back((callee_qn, new_path, new_sanitizer));
            }
        }

        results
    }

    /// Run intra-function data flow analysis on a function's source code.
    ///
    /// This uses `HeuristicFlow` for line-by-line taint tracking within
    /// a single function body. Returns taint paths found.
    pub fn analyze_intra_function(
        &self,
        func_source: &str,
        func_name: &str,
        func_file: &str,
        func_line: usize,
        language: crate::parsers::lightweight::Language,
        category: TaintCategory,
    ) -> Vec<TaintPath> {
        let sources = self.sources.get(&category).cloned().unwrap_or_default();
        let sinks = self.sinks.get(&category).cloned().unwrap_or_default();
        let mut sanitizers = self.sanitizers.get(&category).cloned().unwrap_or_default();
        sanitizers.extend(self.generic_sanitizers.iter().cloned());

        let result = heuristic::HeuristicFlow::new().analyze_intra_function(
            func_source,
            language,
            category,
            &sources,
            &sinks,
            &sanitizers,
        );

        result
            .sink_reaches
            .into_iter()
            .map(|reach| TaintPath {
                source_function: func_name.to_string(),
                source_file: func_file.to_string(),
                source_line: (func_line + reach.taint_source.line) as u32,
                sink_function: reach.sink_pattern.clone(),
                sink_file: func_file.to_string(),
                sink_line: (func_line + reach.sink_line) as u32,
                category,
                call_chain: vec![format!("{} → {}", reach.variable, reach.sink_pattern)],
                is_sanitized: reach.is_sanitized,
                sanitizer: None,
                confidence: reach.confidence,
            })
            .collect()
    }

    /// Analyze a specific function for taint issues using both graph and local analysis
    ///
    /// This combines graph-based call chain analysis with local pattern matching
    /// for more comprehensive coverage.
    #[allow(dead_code)] // Public API method for targeted analysis
    pub fn analyze_function(
        &self,
        graph: &dyn crate::graph::GraphQuery,
        func_qn: &str,
        category: TaintCategory,
    ) -> Vec<TaintPath> {
        let i = graph.interner();
        let mut paths = Vec::new();

        // Get the function
        let func = match graph.get_node(func_qn) {
            Some(f) => f,
            None => return paths,
        };

        // Check direct callees for sinks
        let callees = graph.get_callees(func_qn);
        for callee in &callees {
            let callee_name = callee.node_name(i);
            let callee_qn = callee.qn(i);
            if self.is_sink(callee_name, category)
                || self.is_sink(callee_qn, category)
            {
                // Direct call to sink from this function
                let is_sanitized = callees.iter().any(|c| {
                    self.is_sanitizer(c.node_name(i), category)
                        || self.is_sanitizer(c.qn(i), category)
                });

                let sanitizer = if is_sanitized {
                    callees
                        .iter()
                        .find(|c| {
                            self.is_sanitizer(c.node_name(i), category)
                                || self.is_sanitizer(c.qn(i), category)
                        })
                        .map(|c| c.node_name(i).to_string())
                } else {
                    None
                };

                paths.push(TaintPath {
                    source_function: func.node_name(i).to_string(),
                    source_file: func.path(i).to_string(),
                    source_line: func.line_start,
                    sink_function: callee_name.to_string(),
                    sink_file: callee.path(i).to_string(),
                    sink_line: callee.line_start,
                    category,
                    call_chain: vec![],
                    is_sanitized,
                    sanitizer,
                    confidence: 0.8, // Higher confidence for direct calls
                });
            }
        }

        // Also trace through the call graph for indirect paths
        let indirect_paths = self.bfs_to_sinks(
            graph,
            func_qn,
            &callees
                .iter()
                .filter(|c| self.is_sink(c.node_name(i), category))
                .map(|c| c.qn(i))
                .collect(),
            category,
        );

        for (sink_qn, chain, sanitizer) in indirect_paths {
            if let Some(sink) = graph.get_node(&sink_qn) {
                paths.push(TaintPath {
                    source_function: func.node_name(i).to_string(),
                    source_file: func.path(i).to_string(),
                    source_line: func.line_start,
                    sink_function: sink.node_name(i).to_string(),
                    sink_file: sink.path(i).to_string(),
                    sink_line: sink.line_start,
                    category,
                    call_chain: chain,
                    is_sanitized: sanitizer.is_some(),
                    sanitizer,
                    confidence: 0.6, // Lower confidence for indirect paths
                });
            }
        }

        paths
    }

    /// Get all sink patterns for a category (useful for regex-based detection)
    #[allow(dead_code)] // Public API accessor
    pub fn get_sinks(&self, category: TaintCategory) -> Option<&HashSet<String>> {
        self.sinks.get(&category)
    }

    /// Get all source patterns for a category
    #[allow(dead_code)] // Public API accessor
    pub fn get_sources(&self, category: TaintCategory) -> Option<&HashSet<String>> {
        self.sources.get(&category)
    }

    /// Get all sanitizer patterns for a category
    #[allow(dead_code)] // Public API accessor
    pub fn get_sanitizers(&self, category: TaintCategory) -> Option<&HashSet<String>> {
        self.sanitizers.get(&category)
    }

    /// Add a custom source pattern
    #[allow(dead_code)] // Public API for custom taint rules
    pub fn add_source(&mut self, category: TaintCategory, pattern: String) {
        self.sources.entry(category).or_default().insert(pattern);
    }

    /// Add a custom sink pattern
    #[allow(dead_code)] // Public API for custom taint rules
    pub fn add_sink(&mut self, category: TaintCategory, pattern: String) {
        self.sinks.entry(category).or_default().insert(pattern);
    }

    /// Add a custom sanitizer pattern
    #[allow(dead_code)] // Public API for custom taint rules
    pub fn add_sanitizer(&mut self, category: TaintCategory, pattern: String) {
        self.sanitizers.entry(category).or_default().insert(pattern);
    }
}

/// Result of taint analysis for a file or function
#[derive(Debug, Clone)]
pub struct TaintAnalysisResult {
    /// All taint paths found
    pub paths: Vec<TaintPath>,
    /// Number of vulnerable paths (unsanitized)
    pub vulnerable_count: usize,
    /// Number of sanitized paths
    pub sanitized_count: usize,
}

impl TaintAnalysisResult {
    /// Create from a list of paths
    pub fn from_paths(paths: Vec<TaintPath>) -> Self {
        let vulnerable_count = paths.iter().filter(|p| p.is_vulnerable()).count();
        let sanitized_count = paths.iter().filter(|p| p.is_sanitized).count();

        Self {
            paths,
            vulnerable_count,
            sanitized_count,
        }
    }

    /// Get only vulnerable paths
    pub fn vulnerable_paths(&self) -> Vec<&TaintPath> {
        self.paths.iter().filter(|p| p.is_vulnerable()).collect()
    }

    /// Check if there are any vulnerabilities
    #[allow(dead_code)] // Public API method, used in tests
    pub fn has_vulnerabilities(&self) -> bool {
        self.vulnerable_count > 0
    }
}

// ---- Integration helpers (used by security detectors) ----------------------

/// Run intra-function data flow analysis across all functions in the graph.
///
/// For each function, reads its source file, extracts the function body,
/// and runs the `TaintAnalyzer`'s intra-function analysis. Returns all
/// taint paths found.
///
/// This is the shared integration point -- all security detectors call this.
pub fn run_intra_function_taint(
    analyzer: &TaintAnalyzer,
    graph: &dyn GraphQuery,
    category: TaintCategory,
    repository_path: &Path,
) -> Vec<TaintPath> {
    let i = graph.interner();
    let functions = graph.get_functions_shared();
    let mut all_paths = Vec::new();

    // Cache file contents to avoid re-reading
    use crate::graph::interner::StrKey;
    let mut file_cache: HashMap<StrKey, String> = HashMap::new();

    for func in functions.iter() {
        let func_path = func.path(i);
        // Need a source file to analyze
        if func_path.is_empty() {
            continue;
        }

        let full_path = repository_path.join(func_path);

        // Read file (cached)
        let content = match file_cache.get(&func.file_path) {
            Some(c) => c.clone(),
            None => match std::fs::read_to_string(&full_path) {
                Ok(c) => {
                    file_cache.insert(func.file_path, c.clone());
                    c
                }
                Err(_) => continue,
            },
        };

        // Pre-filter: skip files that don't contain any relevant sink patterns
        if !category.file_might_be_relevant(&content) {
            continue;
        }

        // Extract function body from source
        let line_start = func.line_start as usize;
        let line_end = func.line_end as usize;

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
        let func_name = func.node_name(i);
        let paths = analyzer.analyze_intra_function(
            &func_body,
            func_name,
            func_path,
            line_start,
            language,
            category,
        );

        all_paths.extend(paths);
    }

    all_paths
}

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

