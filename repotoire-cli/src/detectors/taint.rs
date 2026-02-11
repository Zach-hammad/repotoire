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

use crate::graph::{GraphStore, NodeKind};
use std::collections::{HashMap, HashSet, VecDeque};

/// Categories of taint analysis
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TaintCategory {
    /// SQL injection (CWE-89)
    SqlInjection,
    /// Command injection (CWE-78)
    CommandInjection,
    /// Cross-site scripting (CWE-79)
    Xss,
    /// Server-side request forgery (CWE-918)
    Ssrf,
    /// Path traversal (CWE-22)
    PathTraversal,
    /// Code injection via eval/exec (CWE-94)
    CodeInjection,
    /// Log injection (CWE-117)
    LogInjection,
}

impl TaintCategory {
    /// Get the CWE ID for this category
    pub fn cwe_id(&self) -> &'static str {
        match self {
            TaintCategory::SqlInjection => "CWE-89",
            TaintCategory::CommandInjection => "CWE-78",
            TaintCategory::Xss => "CWE-79",
            TaintCategory::Ssrf => "CWE-918",
            TaintCategory::PathTraversal => "CWE-22",
            TaintCategory::CodeInjection => "CWE-94",
            TaintCategory::LogInjection => "CWE-117",
        }
    }

    /// Get a human-readable name
    pub fn name(&self) -> &'static str {
        match self {
            TaintCategory::SqlInjection => "SQL Injection",
            TaintCategory::CommandInjection => "Command Injection",
            TaintCategory::Xss => "Cross-Site Scripting (XSS)",
            TaintCategory::Ssrf => "Server-Side Request Forgery (SSRF)",
            TaintCategory::PathTraversal => "Path Traversal",
            TaintCategory::CodeInjection => "Code Injection",
            TaintCategory::LogInjection => "Log Injection",
        }
    }
}

/// A path from a taint source to a sink through the call graph
#[derive(Debug, Clone)]
pub struct TaintPath {
    /// The source function where taint originates (e.g., route handler)
    pub source_function: String,
    /// The file containing the source
    pub source_file: String,
    /// Line number of the source
    pub source_line: u32,
    /// The sink function where tainted data is used dangerously
    pub sink_function: String,
    /// The file containing the sink
    pub sink_file: String,
    /// Line number of the sink
    pub sink_line: u32,
    /// The category of vulnerability
    pub category: TaintCategory,
    /// Functions in the call chain from source to sink
    pub call_chain: Vec<String>,
    /// Whether a sanitizer function was found in the path
    pub is_sanitized: bool,
    /// The sanitizer function that was found (if any)
    pub sanitizer: Option<String>,
    /// Confidence level (0.0 - 1.0)
    pub confidence: f64,
}

impl TaintPath {
    /// Check if this path represents a likely vulnerability
    pub fn is_vulnerable(&self) -> bool {
        !self.is_sanitized && self.confidence >= 0.5
    }

    /// Get the full path as a string for display
    pub fn path_string(&self) -> String {
        if self.call_chain.is_empty() {
            format!("{} → {}", self.source_function, self.sink_function)
        } else {
            format!(
                "{} → {} → {}",
                self.source_function,
                self.call_chain.join(" → "),
                self.sink_function
            )
        }
    }
}

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
        };
        analyzer.init_default_patterns();
        analyzer
    }

    /// Create with custom max depth
    pub fn with_max_depth(mut self, depth: usize) -> Self {
        self.max_depth = depth;
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
            "setTimeout(", // with string arg
            "setInterval(", // with string arg
            // Go
            // (Go doesn't have direct eval, but template injection is similar)
            "template.HTML",
        ] {
            sinks.insert(pattern.to_string());
        }
        self.sinks.insert(TaintCategory::CodeInjection, sinks);

        let mut sanitizers = HashSet::new();
        for pattern in &[
            "ast.literal_eval",
            "json.loads",
            "JSON.parse",
        ] {
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
        for pattern in &[
            "strip(",
            "replace(",
            "sanitize_log",
        ] {
            sanitizers.insert(pattern.to_string());
        }
        self.sanitizers
            .insert(TaintCategory::LogInjection, sanitizers);
    }

    /// Add generic sanitizers that apply to all categories
    fn add_generic_sanitizers(&mut self) {
        for pattern in &[
            "validate",
            "sanitize",
            "clean",
            "safe_",
            "_safe",
        ] {
            self.generic_sanitizers.insert(pattern.to_string());
        }
    }

    /// Check if a function name matches any source pattern for the category
    pub fn is_source(&self, func_name: &str, category: TaintCategory) -> bool {
        if let Some(sources) = self.sources.get(&category) {
            let name_lower = func_name.to_lowercase();
            sources.iter().any(|s| name_lower.contains(&s.to_lowercase()))
        } else {
            false
        }
    }

    /// Check if a function name matches any sink pattern for the category
    pub fn is_sink(&self, func_name: &str, category: TaintCategory) -> bool {
        if let Some(sinks) = self.sinks.get(&category) {
            let name_lower = func_name.to_lowercase();
            sinks.iter().any(|s| name_lower.contains(&s.to_lowercase()))
        } else {
            false
        }
    }

    /// Check if a function name matches any sanitizer pattern for the category
    pub fn is_sanitizer(&self, func_name: &str, category: TaintCategory) -> bool {
        let name_lower = func_name.to_lowercase();

        // Check category-specific sanitizers
        if let Some(sanitizers) = self.sanitizers.get(&category) {
            if sanitizers
                .iter()
                .any(|s| name_lower.contains(&s.to_lowercase()))
            {
                return true;
            }
        }

        // Check generic sanitizers
        self.generic_sanitizers
            .iter()
            .any(|s| name_lower.contains(&s.to_lowercase()))
    }

    /// Trace taint paths through the call graph for a specific category
    ///
    /// This uses BFS to find all paths from source functions to sink functions,
    /// tracking whether sanitizers are encountered along the way.
    pub fn trace_taint(&self, graph: &GraphStore, category: TaintCategory) -> Vec<TaintPath> {
        let mut paths = Vec::new();
        let functions = graph.get_functions();

        // Find all potential source functions (route handlers, input processors)
        let source_funcs: Vec<_> = functions
            .iter()
            .filter(|f| self.is_potential_source_function(f, category))
            .collect();

        // Find all sink functions
        let sink_funcs: Vec<_> = functions
            .iter()
            .filter(|f| self.is_sink(&f.name, category) || self.is_sink(&f.qualified_name, category))
            .collect();

        // For each source, BFS to find paths to sinks
        for source in &source_funcs {
            let source_paths = self.bfs_to_sinks(
                graph,
                &source.qualified_name,
                &sink_funcs.iter().map(|f| f.qualified_name.as_str()).collect::<HashSet<_>>(),
                category,
            );

            for (sink_qn, call_chain, sanitizer) in source_paths {
                if let Some(sink) = sink_funcs.iter().find(|f| f.qualified_name == sink_qn) {
                    paths.push(TaintPath {
                        source_function: source.name.clone(),
                        source_file: source.file_path.clone(),
                        source_line: source.line_start,
                        sink_function: sink.name.clone(),
                        sink_file: sink.file_path.clone(),
                        sink_line: sink.line_start,
                        category,
                        call_chain,
                        is_sanitized: sanitizer.is_some(),
                        sanitizer,
                        confidence: 0.7, // Base confidence for graph-traced paths
                    });
                }
            }
        }

        paths
    }

    /// Check if a function is a potential taint source (e.g., route handler)
    fn is_potential_source_function(
        &self,
        func: &crate::graph::CodeNode,
        _category: TaintCategory,
    ) -> bool {
        let name_lower = func.name.to_lowercase();
        let qn_lower = func.qualified_name.to_lowercase();

        // Route handlers are typically taint sources
        let is_route_handler = name_lower.contains("handler")
            || name_lower.contains("controller")
            || name_lower.contains("view")
            || name_lower.contains("endpoint")
            || name_lower.starts_with("get_")
            || name_lower.starts_with("post_")
            || name_lower.starts_with("put_")
            || name_lower.starts_with("delete_")
            || name_lower.starts_with("patch_")
            || name_lower.starts_with("handle_");

        // Check if function references any source patterns
        let references_source = if let Some(sources) = self.sources.get(&_category) {
            sources.iter().any(|s| {
                qn_lower.contains(&s.to_lowercase()) || name_lower.contains(&s.to_lowercase())
            })
        } else {
            false
        };

        // Check properties for route decorator indicators
        let has_route_decorator = func
            .get_str("decorators")
            .map(|d| {
                d.contains("@app.route")
                    || d.contains("@router")
                    || d.contains("@get")
                    || d.contains("@post")
                    || d.contains("@api")
            })
            .unwrap_or(false);

        is_route_handler || references_source || has_route_decorator
    }

    /// BFS from a source function to find paths to any sink
    fn bfs_to_sinks(
        &self,
        graph: &GraphStore,
        source_qn: &str,
        sink_qns: &HashSet<&str>,
        category: TaintCategory,
    ) -> Vec<(String, Vec<String>, Option<String>)> {
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
                if visited.contains(&callee.qualified_name) {
                    continue;
                }

                visited.insert(callee.qualified_name.clone());

                let mut new_path = path.clone();
                new_path.push(callee.name.clone());

                // Check if this callee is a sanitizer
                let new_sanitizer = if sanitizer.is_some() {
                    sanitizer.clone()
                } else if self.is_sanitizer(&callee.name, category)
                    || self.is_sanitizer(&callee.qualified_name, category)
                {
                    Some(callee.name.clone())
                } else {
                    None
                };

                queue.push_back((callee.qualified_name.clone(), new_path, new_sanitizer));
            }
        }

        results
    }

    /// Analyze a specific function for taint issues using both graph and local analysis
    ///
    /// This combines graph-based call chain analysis with local pattern matching
    /// for more comprehensive coverage.
    pub fn analyze_function(
        &self,
        graph: &GraphStore,
        func_qn: &str,
        category: TaintCategory,
    ) -> Vec<TaintPath> {
        let mut paths = Vec::new();

        // Get the function
        let func = match graph.get_node(func_qn) {
            Some(f) => f,
            None => return paths,
        };

        // Check direct callees for sinks
        let callees = graph.get_callees(func_qn);
        for callee in &callees {
            if self.is_sink(&callee.name, category) || self.is_sink(&callee.qualified_name, category)
            {
                // Direct call to sink from this function
                let is_sanitized = callees.iter().any(|c| {
                    self.is_sanitizer(&c.name, category)
                        || self.is_sanitizer(&c.qualified_name, category)
                });

                let sanitizer = if is_sanitized {
                    callees
                        .iter()
                        .find(|c| {
                            self.is_sanitizer(&c.name, category)
                                || self.is_sanitizer(&c.qualified_name, category)
                        })
                        .map(|c| c.name.clone())
                } else {
                    None
                };

                paths.push(TaintPath {
                    source_function: func.name.clone(),
                    source_file: func.file_path.clone(),
                    source_line: func.line_start,
                    sink_function: callee.name.clone(),
                    sink_file: callee.file_path.clone(),
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
                .filter(|c| self.is_sink(&c.name, category))
                .map(|c| c.qualified_name.as_str())
                .collect(),
            category,
        );

        for (sink_qn, chain, sanitizer) in indirect_paths {
            if let Some(sink) = graph.get_node(&sink_qn) {
                paths.push(TaintPath {
                    source_function: func.name.clone(),
                    source_file: func.file_path.clone(),
                    source_line: func.line_start,
                    sink_function: sink.name.clone(),
                    sink_file: sink.file_path.clone(),
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
    pub fn get_sinks(&self, category: TaintCategory) -> Option<&HashSet<String>> {
        self.sinks.get(&category)
    }

    /// Get all source patterns for a category
    pub fn get_sources(&self, category: TaintCategory) -> Option<&HashSet<String>> {
        self.sources.get(&category)
    }

    /// Get all sanitizer patterns for a category
    pub fn get_sanitizers(&self, category: TaintCategory) -> Option<&HashSet<String>> {
        self.sanitizers.get(&category)
    }

    /// Add a custom source pattern
    pub fn add_source(&mut self, category: TaintCategory, pattern: String) {
        self.sources.entry(category).or_default().insert(pattern);
    }

    /// Add a custom sink pattern
    pub fn add_sink(&mut self, category: TaintCategory, pattern: String) {
        self.sinks.entry(category).or_default().insert(pattern);
    }

    /// Add a custom sanitizer pattern
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
    pub fn has_vulnerabilities(&self) -> bool {
        self.vulnerable_count > 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_taint_category_cwe() {
        assert_eq!(TaintCategory::SqlInjection.cwe_id(), "CWE-89");
        assert_eq!(TaintCategory::CommandInjection.cwe_id(), "CWE-78");
        assert_eq!(TaintCategory::Xss.cwe_id(), "CWE-79");
    }

    #[test]
    fn test_is_source() {
        let analyzer = TaintAnalyzer::new();

        assert!(analyzer.is_source("req.body", TaintCategory::SqlInjection));
        assert!(analyzer.is_source("request.form", TaintCategory::SqlInjection));
        assert!(analyzer.is_source("c.Param", TaintCategory::SqlInjection));
        assert!(!analyzer.is_source("random_function", TaintCategory::SqlInjection));
    }

    #[test]
    fn test_is_sink() {
        let analyzer = TaintAnalyzer::new();

        assert!(analyzer.is_sink("cursor.execute", TaintCategory::SqlInjection));
        assert!(analyzer.is_sink("db.query", TaintCategory::SqlInjection));
        assert!(analyzer.is_sink("os.system", TaintCategory::CommandInjection));
        assert!(analyzer.is_sink("innerHTML", TaintCategory::Xss));
        assert!(!analyzer.is_sink("print", TaintCategory::SqlInjection));
    }

    #[test]
    fn test_is_sanitizer() {
        let analyzer = TaintAnalyzer::new();

        assert!(analyzer.is_sanitizer("escapeHtml", TaintCategory::Xss));
        assert!(analyzer.is_sanitizer("shlex.quote", TaintCategory::CommandInjection));
        assert!(analyzer.is_sanitizer("validate_input", TaintCategory::SqlInjection)); // generic
        assert!(analyzer.is_sanitizer("sanitize_data", TaintCategory::Xss)); // generic
    }

    #[test]
    fn test_taint_path_is_vulnerable() {
        let vulnerable_path = TaintPath {
            source_function: "handler".to_string(),
            source_file: "app.py".to_string(),
            source_line: 10,
            sink_function: "execute".to_string(),
            sink_file: "db.py".to_string(),
            sink_line: 20,
            category: TaintCategory::SqlInjection,
            call_chain: vec![],
            is_sanitized: false,
            sanitizer: None,
            confidence: 0.8,
        };

        let safe_path = TaintPath {
            is_sanitized: true,
            sanitizer: Some("escape".to_string()),
            ..vulnerable_path.clone()
        };

        assert!(vulnerable_path.is_vulnerable());
        assert!(!safe_path.is_vulnerable());
    }

    #[test]
    fn test_taint_path_string() {
        let path = TaintPath {
            source_function: "handler".to_string(),
            source_file: "app.py".to_string(),
            source_line: 10,
            sink_function: "execute".to_string(),
            sink_file: "db.py".to_string(),
            sink_line: 20,
            category: TaintCategory::SqlInjection,
            call_chain: vec!["process".to_string(), "query".to_string()],
            is_sanitized: false,
            sanitizer: None,
            confidence: 0.8,
        };

        assert_eq!(path.path_string(), "handler → process → query → execute");
    }

    #[test]
    fn test_analysis_result() {
        let paths = vec![
            TaintPath {
                source_function: "a".to_string(),
                source_file: "a.py".to_string(),
                source_line: 1,
                sink_function: "b".to_string(),
                sink_file: "b.py".to_string(),
                sink_line: 2,
                category: TaintCategory::SqlInjection,
                call_chain: vec![],
                is_sanitized: false,
                sanitizer: None,
                confidence: 0.8,
            },
            TaintPath {
                source_function: "c".to_string(),
                source_file: "c.py".to_string(),
                source_line: 3,
                sink_function: "d".to_string(),
                sink_file: "d.py".to_string(),
                sink_line: 4,
                category: TaintCategory::SqlInjection,
                call_chain: vec![],
                is_sanitized: true,
                sanitizer: Some("escape".to_string()),
                confidence: 0.8,
            },
        ];

        let result = TaintAnalysisResult::from_paths(paths);

        assert_eq!(result.vulnerable_count, 1);
        assert_eq!(result.sanitized_count, 1);
        assert!(result.has_vulnerabilities());
        assert_eq!(result.vulnerable_paths().len(), 1);
    }
}
