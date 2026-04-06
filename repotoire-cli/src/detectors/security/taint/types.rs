//! Taint analysis types and utility functions.

/// Word-boundary match for function/variable names (#28).
/// Patterns containing '.' (like 'req.body') use contains() since dots are natural boundaries.
/// For other patterns, matches at word boundaries where boundaries are transitions between
/// alphanumeric chars (not underscores -- `validate_input` should match `validate`).
/// This prevents 'id' from matching inside 'valid' or 'provider'.
pub(super) fn word_boundary_match(text: &str, pattern: &str) -> bool {
    // Dotted patterns (e.g., req.body, request.form) -- use contains
    if pattern.contains('.') {
        return text.contains(pattern);
    }

    let bytes = text.as_bytes();
    let mut search_from = 0;
    while let Some(pos) = text[search_from..].find(pattern) {
        let abs_pos = search_from + pos;
        // Before: must be start of string, underscore, dot, or non-alphanumeric
        let before_ok = abs_pos == 0 || {
            let prev = bytes[abs_pos - 1];
            !prev.is_ascii_alphanumeric()
        };
        let after_pos = abs_pos + pattern.len();
        let after_ok = after_pos >= text.len() || {
            let next = bytes[after_pos];
            !next.is_ascii_alphanumeric()
        };

        if before_ok && after_ok {
            return true;
        }
        search_from = abs_pos + 1;
    }
    false
}

/// Categories of taint analysis
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
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
    #[allow(dead_code)] // Public API
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

    /// File extensions where this taint category's sinks can actually exist.
    /// Files with other extensions are skipped entirely — any pattern matches
    /// in unsupported languages (e.g. `cursor.execute` in a `.rs` raw string)
    /// are guaranteed false positives.
    pub fn relevant_extensions(&self) -> &'static [&'static str] {
        match self {
            // All current taint sinks are Python/JS/TS/Go/Java patterns.
            // Rust, C, C++, C# have different DB APIs (sqlx, diesel, ADO.NET)
            // that aren't modeled by the taint analyzer.
            TaintCategory::SqlInjection
            | TaintCategory::CommandInjection
            | TaintCategory::PathTraversal
            | TaintCategory::CodeInjection
            | TaintCategory::LogInjection => &["py", "js", "ts", "jsx", "tsx", "go", "java", "rb"],
            TaintCategory::Xss => &["js", "ts", "jsx", "tsx", "py", "rb", "java"],
            TaintCategory::Ssrf => &["py", "js", "ts", "jsx", "tsx", "go", "java", "rb"],
        }
    }

    /// Literal strings that MUST appear in file content for this taint category
    /// to be relevant. Files without any of these patterns are skipped entirely.
    pub fn quick_reject_patterns(&self) -> &'static [&'static str] {
        match self {
            TaintCategory::SqlInjection => &[
                "execute", "cursor", "query", "SELECT", "INSERT", "UPDATE", "DELETE", "sql", "SQL",
                "db.",
            ],
            TaintCategory::CommandInjection => &[
                "exec",
                "spawn",
                "system",
                "popen",
                "subprocess",
                "shell",
                "Process",
            ],
            TaintCategory::Xss => &[
                "innerHTML",
                "document.write",
                "dangerouslySetInnerHTML",
                "render",
                "template",
                "html",
            ],
            TaintCategory::Ssrf => &[
                "fetch",
                "request",
                "http",
                "urllib",
                "requests.get",
                "curl",
                "urlopen",
            ],
            TaintCategory::PathTraversal => &[
                "open(",
                "readFile",
                "readdir",
                "path.join",
                "os.path",
                "file_get_contents",
            ],
            TaintCategory::CodeInjection => &[
                "eval(",
                "exec(",
                "compile(",
                "Function(",
                "setInterval(",
                "setTimeout(",
            ],
            TaintCategory::LogInjection => &[
                "log(",
                "logger",
                "logging",
                "console.log",
                "print(",
                "warn(",
                "error(",
            ],
        }
    }

    /// Check if file content might contain relevant sinks for this category.
    pub fn file_might_be_relevant(&self, content: &str) -> bool {
        self.quick_reject_patterns()
            .iter()
            .any(|p| content.contains(p))
    }
}

/// A path from a taint source to a sink through the call graph
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pre_filter_skips_irrelevant_files() {
        let content = "def hello():\n    print('world')\n";
        assert!(!TaintCategory::SqlInjection.file_might_be_relevant(content));

        let content_sql = "cursor.execute(query)";
        assert!(TaintCategory::SqlInjection.file_might_be_relevant(content_sql));
    }

    #[test]
    fn test_pre_filter_all_categories_have_patterns() {
        let categories = [
            TaintCategory::SqlInjection,
            TaintCategory::CommandInjection,
            TaintCategory::Xss,
            TaintCategory::Ssrf,
            TaintCategory::PathTraversal,
            TaintCategory::CodeInjection,
            TaintCategory::LogInjection,
        ];
        for cat in &categories {
            assert!(
                !cat.quick_reject_patterns().is_empty(),
                "{:?} should have quick-reject patterns",
                cat
            );
        }
    }

    #[test]
    fn test_pre_filter_command_injection() {
        let irrelevant = "def add(a, b):\n    return a + b\n";
        assert!(!TaintCategory::CommandInjection.file_might_be_relevant(irrelevant));

        let relevant = "subprocess.run(cmd)";
        assert!(TaintCategory::CommandInjection.file_might_be_relevant(relevant));
    }

    #[test]
    fn test_pre_filter_xss() {
        let irrelevant = "fn compute(x: i32) -> i32 { x * 2 }";
        assert!(!TaintCategory::Xss.file_might_be_relevant(irrelevant));

        let relevant = "element.innerHTML = userInput;";
        assert!(TaintCategory::Xss.file_might_be_relevant(relevant));
    }
}
