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
