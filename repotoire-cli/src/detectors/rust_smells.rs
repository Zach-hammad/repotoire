//! Rust Code Smell Detectors
//!
//! Detectors for Rust-specific patterns that can lead to panics, poor performance,
//! or suboptimal code quality.
//!
//! # Detectors
//!
//! - `UnwrapWithoutContextDetector` - unwrap()/expect() without context (panic risk)
//! - `UnsafeWithoutSafetyCommentDetector` - unsafe blocks without safety comments
//! - `CloneInHotPathDetector` - .clone() in hot paths (performance)
//! - `MissingMustUseDetector` - Missing #[must_use] on Result-returning functions
//! - `BoxDynTraitDetector` - Box<dyn Trait> where generics would work
//! - `MutexPoisoningRiskDetector` - Mutex poisoning risks

use crate::detectors::base::Detector;
use crate::graph::GraphStore;
use crate::models::{deterministic_finding_id, Finding, Severity};
use anyhow::Result;
use regex::Regex;
use std::path::PathBuf;
use std::sync::OnceLock;
use tracing::info;

// ============================================================================
// Regex patterns (compiled once)
// ============================================================================

static UNWRAP_CALL: OnceLock<Regex> = OnceLock::new();
static EXPECT_CALL: OnceLock<Regex> = OnceLock::new();
static UNSAFE_BLOCK: OnceLock<Regex> = OnceLock::new();
static SAFETY_COMMENT: OnceLock<Regex> = OnceLock::new();
static CLONE_CALL: OnceLock<Regex> = OnceLock::new();
static HOT_PATH_INDICATOR: OnceLock<Regex> = OnceLock::new();
static FN_RETURNS_RESULT: OnceLock<Regex> = OnceLock::new();
static MUST_USE_ATTR: OnceLock<Regex> = OnceLock::new();
static BOX_DYN_TRAIT: OnceLock<Regex> = OnceLock::new();
static MUTEX_LOCK: OnceLock<Regex> = OnceLock::new();
static MUTEX_UNWRAP: OnceLock<Regex> = OnceLock::new();

fn unwrap_call() -> &'static Regex {
    UNWRAP_CALL.get_or_init(|| Regex::new(r"\.unwrap\s*\(\s*\)").unwrap())
}

fn expect_call() -> &'static Regex {
    EXPECT_CALL.get_or_init(|| Regex::new(r#"\.expect\s*\(\s*["']"#).unwrap())
}

fn unsafe_block() -> &'static Regex {
    UNSAFE_BLOCK.get_or_init(|| Regex::new(r"\bunsafe\s*\{").unwrap())
}

fn safety_comment() -> &'static Regex {
    SAFETY_COMMENT
        .get_or_init(|| Regex::new(r"(?i)//\s*SAFETY:|///\s*#\s*Safety|//\s*SAFETY\s*:").unwrap())
}

fn clone_call() -> &'static Regex {
    CLONE_CALL.get_or_init(|| Regex::new(r"\.clone\s*\(\s*\)").unwrap())
}

fn hot_path_indicator() -> &'static Regex {
    HOT_PATH_INDICATOR.get_or_init(|| {
        Regex::new(r"(?i)\b(loop|while|for|iter|map|filter|fold|reduce|collect|into_iter)\b")
            .unwrap()
    })
}

fn fn_returns_result() -> &'static Regex {
    FN_RETURNS_RESULT.get_or_init(|| {
        Regex::new(r"(?m)^[^\n]*\bfn\s+\w+[^{]*->\s*(?:Result|anyhow::Result|io::Result)").unwrap()
    })
}

fn must_use_attr() -> &'static Regex {
    MUST_USE_ATTR.get_or_init(|| Regex::new(r"#\[must_use").unwrap())
}

fn box_dyn_trait() -> &'static Regex {
    BOX_DYN_TRAIT.get_or_init(|| Regex::new(r"Box\s*<\s*dyn\s+\w+").unwrap())
}

fn mutex_lock() -> &'static Regex {
    MUTEX_LOCK.get_or_init(|| Regex::new(r"\.lock\s*\(\s*\)").unwrap())
}

fn mutex_unwrap() -> &'static Regex {
    MUTEX_UNWRAP.get_or_init(|| Regex::new(r"\.lock\s*\(\s*\)\s*\.unwrap\s*\(\s*\)").unwrap())
}

// ============================================================================
// Helper functions
// ============================================================================

/// Check if a line is in a test context
fn is_test_context(line: &str, content: &str, line_idx: usize) -> bool {
    // Check if line is in a test module or function
    let lines: Vec<&str> = content.lines().collect();

    // Look backwards for #[test] or #[cfg(test)]
    let start = line_idx.saturating_sub(10);
    for i in start..=line_idx {
        if let Some(prev_line) = lines.get(i) {
            if prev_line.contains("#[test]")
                || prev_line.contains("#[cfg(test)]")
                || prev_line.contains("mod tests")
            {
                return true;
            }
        }
    }

    // Check filename
    line.contains("_test.rs") || line.contains("/tests/")
}

/// Check if unwrap is on a known-safe pattern
fn is_safe_unwrap_context(line: &str, content: &str, line_idx: usize) -> bool {
    let trimmed = line.trim();

    // Skip comments
    if trimmed.starts_with("//") || trimmed.starts_with("/*") {
        return true;
    }

    // Safe patterns where unwrap is acceptable:
    // - OnceLock/OnceCell initialization (will succeed after first call)
    // - Regex::new with static patterns (will panic at startup, not runtime)
    // - const/static initialization
    // - .get_or_init(|| ...).unwrap() patterns
    let safe_patterns = [
        "OnceLock",
        "OnceCell",
        "Lazy",
        "get_or_init",
        "Regex::new",
        "const ",
        "static ",
        "lazy_static!",
        "once_cell",
    ];

    for pattern in &safe_patterns {
        if line.contains(pattern) {
            return true;
        }
    }

    // Check for environment/config unwraps with fallbacks nearby
    if line.contains("env::var") && content.contains("unwrap_or") {
        return true;
    }

    // Skip test contexts
    is_test_context(line, content, line_idx)
}

/// Check if expect() has a meaningful message
fn has_meaningful_expect_message(line: &str) -> bool {
    // Look for .expect("...") and check if message is descriptive
    if let Some(start) = line.find(".expect(") {
        let after = &line[start + 8..];
        if let Some(quote_start) = after.find('"').or_else(|| after.find('\'')) {
            let msg_start = quote_start + 1;
            if let Some(content) = after.get(msg_start..) {
                // Check if message is at least somewhat descriptive
                let words: Vec<&str> = content.split_whitespace().collect();
                // Good messages have more than 2-3 words
                return words.len() >= 3;
            }
        }
    }
    false
}

// ============================================================================
// UnwrapWithoutContextDetector
// ============================================================================

/// Detects unwrap()/expect() calls that may panic without context
///
/// Flags:
/// - `.unwrap()` on Result/Option without nearby error context
/// - `.expect("short message")` with non-descriptive messages
///
/// Skips:
/// - Test code
/// - Known-safe patterns (OnceLock, static Regex, etc.)
pub struct UnwrapWithoutContextDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl UnwrapWithoutContextDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 25,
        }
    }
}

impl Detector for UnwrapWithoutContextDetector {
    fn name(&self) -> &'static str {
        "rust-unwrap-without-context"
    }

    fn description(&self) -> &'static str {
        "Detects unwrap()/expect() calls that may panic without proper context"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "rs" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    // Skip if in safe context
                    if is_safe_unwrap_context(line, &content, i) {
                        continue;
                    }

                    let has_unwrap = unwrap_call().is_match(line);
                    let has_expect = expect_call().is_match(line);

                    // For expect(), check if the message is meaningful
                    if has_expect && has_meaningful_expect_message(line) {
                        continue;
                    }

                    if has_unwrap || has_expect {
                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        let issue_type = if has_unwrap { "unwrap()" } else { "expect()" };
                        let title = format!("Panic risk: {} without context", issue_type);

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "UnwrapWithoutContextDetector",
                                &file_str,
                                line_num,
                                &title,
                            ),
                            detector: "UnwrapWithoutContextDetector".to_string(),
                            severity: Severity::Medium,
                            title,
                            description: format!(
                                "Using `{}` can cause panics. Consider using `?` operator, \
                                `unwrap_or`, `unwrap_or_else`, or proper error handling.",
                                issue_type
                            ),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Replace with proper error handling:\n\
                                ```rust\n\
                                // Instead of:\n\
                                let value = result.unwrap();\n\n\
                                // Use ? operator:\n\
                                let value = result?;\n\n\
                                // Or provide a default:\n\
                                let value = result.unwrap_or_default();\n\
                                let value = result.unwrap_or_else(|e| handle_error(e));\n\n\
                                // Or use expect with context:\n\
                                let value = result.expect(\"failed to X because Y - this indicates Z\");\n\
                                ```".to_string()
                            ),
                            estimated_effort: Some("10 minutes".to_string()),
                            category: Some("reliability".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Panics crash the program without recovery. In production services, \
                                this means dropped requests and potential data loss. Using proper \
                                error handling makes code more robust and debuggable.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "UnwrapWithoutContextDetector found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}

// ============================================================================
// UnsafeWithoutSafetyCommentDetector
// ============================================================================

/// Detects unsafe blocks without a SAFETY comment explaining the invariants
pub struct UnsafeWithoutSafetyCommentDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl UnsafeWithoutSafetyCommentDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }
}

impl Detector for UnsafeWithoutSafetyCommentDetector {
    fn name(&self) -> &'static str {
        "rust-unsafe-without-safety-comment"
    }

    fn description(&self) -> &'static str {
        "Detects unsafe blocks without SAFETY comments"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "rs" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    if unsafe_block().is_match(line) {
                        // Skip if inside string literal (suggested fix examples, test fixtures)
                        let trimmed = line.trim();
                        if trimmed.starts_with('"')
                            || trimmed.starts_with("r#\"")
                            || trimmed.starts_with("r\"")
                            || trimmed.starts_with('\'')
                        {
                            continue;
                        }
                        // Skip if in test context
                        if is_test_context(line, &content, i) {
                            continue;
                        }
                        // Look for SAFETY comment in the 3 lines before
                        let has_safety = (i.saturating_sub(3)..i)
                            .any(|j| lines.get(j).is_some_and(|l| safety_comment().is_match(l)));

                        // Also check if SAFETY is on the same line (inline comment)
                        let has_inline_safety = safety_comment().is_match(line);

                        if !has_safety && !has_inline_safety {
                            let file_str = path.to_string_lossy();
                            let line_num = (i + 1) as u32;

                            findings.push(Finding {
                                id: deterministic_finding_id(
                                    "UnsafeWithoutSafetyCommentDetector",
                                    &file_str,
                                    line_num,
                                    "unsafe without SAFETY comment",
                                ),
                                detector: "UnsafeWithoutSafetyCommentDetector".to_string(),
                                severity: Severity::High,
                                title: "unsafe block without SAFETY comment".to_string(),
                                description: "Unsafe blocks should document why they're safe. \
                                    Add a `// SAFETY:` comment explaining the invariants."
                                    .to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some(line_num),
                                line_end: Some(line_num),
                                suggested_fix: Some(
                                    "Add a SAFETY comment:\n\
                                    ```rust\n\
                                    // SAFETY: [ptr] is valid because:\n\
                                    // 1. It was allocated by [X] and has not been freed\n\
                                    // 2. No other code has mutable access (enforced by [Y])\n\
                                    // 3. The data is properly aligned for [Type]\n\
                                    unsafe {\n\
                                        // ...\n\
                                    }\n\
                                    ```"
                                    .to_string(),
                                ),
                                estimated_effort: Some("15 minutes".to_string()),
                                category: Some("safety".to_string()),
                                cwe_id: Some("CWE-119".to_string()),
                                why_it_matters: Some(
                                    "Unsafe code bypasses Rust's safety guarantees. Without \
                                    documentation, it's impossible to verify correctness during \
                                    code review or maintenance. SAFETY comments are the standard \
                                    way to document why unsafe code is actually safe."
                                        .to_string(),
                                ),
                                ..Default::default()
                            });
                        }
                    }
                }
            }
        }

        info!(
            "UnsafeWithoutSafetyCommentDetector found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}

// ============================================================================
// CloneInHotPathDetector
// ============================================================================

/// Detects .clone() calls in loops and iterators (potential performance issue)
pub struct CloneInHotPathDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl CloneInHotPathDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 25,
        }
    }

    /// Check if we're inside a loop or iterator context
    fn is_hot_path_context(content: &str, line_idx: usize, current_line: &str) -> bool {
        // Check current line for hot path indicators
        if hot_path_indicator().is_match(current_line) {
            return true;
        }

        // Look backwards for loop/iterator context (within ~10 lines)
        let lines: Vec<&str> = content.lines().collect();
        let start = line_idx.saturating_sub(10);

        let mut brace_depth = 0;
        for i in (start..line_idx).rev() {
            if let Some(line) = lines.get(i) {
                // Track braces to find containing scope
                brace_depth += line.matches('}').count();
                brace_depth = brace_depth.saturating_sub(line.matches('{').count());

                // If we're still in the same scope
                if brace_depth == 0 && hot_path_indicator().is_match(line) {
                    return true;
                }
            }
        }

        false
    }
}

impl Detector for CloneInHotPathDetector {
    fn name(&self) -> &'static str {
        "rust-clone-in-hot-path"
    }

    fn description(&self) -> &'static str {
        "Detects .clone() in loops and iterators"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "rs" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    // Skip comments
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") {
                        continue;
                    }

                    if clone_call().is_match(line) && Self::is_hot_path_context(&content, i, line) {
                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                                id: deterministic_finding_id(
                                    "CloneInHotPathDetector",
                                    &file_str,
                                    line_num,
                                    "clone in hot path",
                                ),
                                detector: "CloneInHotPathDetector".to_string(),
                                severity: Severity::Low,
                                title: ".clone() in loop/iterator (performance)".to_string(),
                                description: "Cloning in a hot path can cause performance issues. \
                                    Consider using references, Cow, or Arc instead.".to_string(),
                                affected_files: vec![path.to_path_buf()],
                                line_start: Some(line_num),
                                line_end: Some(line_num),
                                suggested_fix: Some(
                                    "Consider alternatives to clone:\n\
                                    ```rust\n\
                                    // Use references if you don't need ownership:\n\
                                    for item in &items { /* use &item */ }\n\n\
                                    // Use Cow for clone-on-write:\n\
                                    use std::borrow::Cow;\n\
                                    let data: Cow<str> = Cow::Borrowed(&original);\n\n\
                                    // Use Arc for shared ownership:\n\
                                    let shared = Arc::new(expensive_data);\n\
                                    for _ in 0..n {\n\
                                        spawn(Arc::clone(&shared)); // cheap\n\
                                    }\n\n\
                                    // Move data out of the loop if possible:\n\
                                    let owned = data.clone();\n\
                                    for item in items {\n\
                                        use_data(&owned); // no clone needed\n\
                                    }\n\
                                    ```".to_string()
                                ),
                                estimated_effort: Some("20 minutes".to_string()),
                                category: Some("performance".to_string()),
                                cwe_id: None,
                                why_it_matters: Some(
                                    "Cloning inside loops multiplies allocation overhead. For large \
                                    data or tight loops, this can dominate runtime. Profiling often \
                                    reveals clone() as a hot spot.".to_string()
                                ),
                                ..Default::default()
                            });
                    }
                }
            }
        }

        info!("CloneInHotPathDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// MissingMustUseDetector
// ============================================================================

/// Detects public functions returning Result without #[must_use]
pub struct MissingMustUseDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl MissingMustUseDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 25,
        }
    }
}

impl Detector for MissingMustUseDetector {
    fn name(&self) -> &'static str {
        "rust-missing-must-use"
    }

    fn description(&self) -> &'static str {
        "Detects Result-returning functions without #[must_use]"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        // Pattern for pub fn that returns Result
        let pub_fn_result = Regex::new(
            r"^\s*pub\s+(?:async\s+)?fn\s+(\w+)[^{]*->\s*(?:Result|anyhow::Result|io::Result)",
        )
        .unwrap();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "rs" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                let lines: Vec<&str> = content.lines().collect();

                for (i, line) in lines.iter().enumerate() {
                    if let Some(caps) = pub_fn_result.captures(line) {
                        let fn_name = caps.get(1).map_or("", |m| m.as_str());

                        // Skip if #[must_use] is on the previous line(s)
                        let has_must_use = (i.saturating_sub(3)..i)
                            .any(|j| lines.get(j).is_some_and(|l| must_use_attr().is_match(l)));

                        // Skip common patterns that don't need #[must_use]
                        // - main() functions
                        // - test functions
                        // - impl blocks for traits (inherits from trait definition)
                        if fn_name == "main" || fn_name.starts_with("test_") {
                            continue;
                        }

                        // Check if we're in an impl block for a trait (look for "impl X for Y")
                        let mut is_trait_impl = false;
                        for j in (0..i).rev() {
                            if let Some(prev_line) = lines.get(j) {
                                if prev_line.contains("impl ") && prev_line.contains(" for ") {
                                    is_trait_impl = true;
                                    break;
                                }
                                if prev_line.trim().starts_with("impl ")
                                    && !prev_line.contains(" for ")
                                {
                                    break;
                                }
                            }
                        }

                        if is_trait_impl || has_must_use {
                            continue;
                        }

                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "MissingMustUseDetector",
                                &file_str,
                                line_num,
                                &format!("missing must_use: {}", fn_name),
                            ),
                            detector: "MissingMustUseDetector".to_string(),
                            severity: Severity::Low,
                            title: format!("Missing #[must_use] on Result-returning fn `{}`", fn_name),
                            description: "Public functions returning Result should have #[must_use] \
                                to warn callers who ignore the Result.".to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(format!(
                                "Add #[must_use]:\n\
                                ```rust\n\
                                #[must_use = \"this Result should be handled\"]\n\
                                pub fn {}(...) -> Result<...> {{\n\
                                    // ...\n\
                                }}\n\
                                ```", fn_name
                            )),
                            estimated_effort: Some("2 minutes".to_string()),
                            category: Some("correctness".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Without #[must_use], callers can silently ignore Results, missing \
                                errors. This is a common source of bugs. Clippy's `must_use_candidate` \
                                lint catches this, but it's good to be explicit.".to_string()
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!("MissingMustUseDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// BoxDynTraitDetector
// ============================================================================

/// Detects Box<dyn Trait> usage that could be replaced with generics
pub struct BoxDynTraitDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl BoxDynTraitDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }

    /// Check if the context suggests dynamic dispatch is actually needed
    fn needs_dynamic_dispatch(content: &str, line_idx: usize) -> bool {
        let lines: Vec<&str> = content.lines().collect();

        // Look for patterns that require dynamic dispatch:
        // - Storing in collections (Vec<Box<dyn Trait>>)
        // - Return type polymorphism (-> Box<dyn Trait>)
        // - Object safety requirements

        if let Some(line) = lines.get(line_idx) {
            // Vec<Box<dyn _>> is a valid use case
            if line.contains("Vec<Box<dyn") || line.contains("Vec<Box<dyn") {
                return true;
            }

            // -> Box<dyn _> return type is often necessary
            if line.contains("-> Box<dyn") {
                return true;
            }

            // HashMap/BTreeMap values
            if line.contains("HashMap") || line.contains("BTreeMap") {
                return true;
            }

            // Struct fields with multiple implementors
            if line.trim().ends_with(',') || line.contains("pub ") && line.contains(":") {
                return true;
            }
        }

        false
    }
}

impl Detector for BoxDynTraitDetector {
    fn name(&self) -> &'static str {
        "rust-box-dyn-trait"
    }

    fn description(&self) -> &'static str {
        "Detects Box<dyn Trait> that could be replaced with generics"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "rs" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    // Skip comments
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") {
                        continue;
                    }

                    if box_dyn_trait().is_match(line) {
                        // Skip if dynamic dispatch is actually needed
                        if Self::needs_dynamic_dispatch(&content, i) {
                            continue;
                        }

                        // Skip if it's in a function parameter (might be intentional API)
                        if line.contains("fn ") && line.contains("Box<dyn") && line.contains("(") {
                            // Check if it's a parameter, not return type
                            if let Some(paren_pos) = line.find('(') {
                                if let Some(box_pos) = line.find("Box<dyn") {
                                    if box_pos > paren_pos {
                                        // Box<dyn is in parameters, might be intentional
                                        continue;
                                    }
                                }
                            }
                        }

                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "BoxDynTraitDetector",
                                &file_str,
                                line_num,
                                "box dyn trait",
                            ),
                            detector: "BoxDynTraitDetector".to_string(),
                            severity: Severity::Low,
                            title: "Box<dyn Trait> may be replaceable with generics".to_string(),
                            description: "Dynamic dispatch via Box<dyn Trait> has overhead. \
                                If the concrete type is known at compile time, consider generics."
                                .to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Consider using generics instead:\n\
                                ```rust\n\
                                // Instead of:\n\
                                fn process(handler: Box<dyn Handler>) { ... }\n\n\
                                // Use generics (monomorphized, no vtable):\n\
                                fn process<H: Handler>(handler: H) { ... }\n\n\
                                // Or impl Trait for simpler syntax:\n\
                                fn process(handler: impl Handler) { ... }\n\n\
                                // Box<dyn> IS needed when:\n\
                                // - Storing heterogeneous types in collections\n\
                                // - Returning different types from a function\n\
                                // - Type erasure for plugin systems\n\
                                ```"
                                .to_string(),
                            ),
                            estimated_effort: Some("15 minutes".to_string()),
                            category: Some("performance".to_string()),
                            cwe_id: None,
                            why_it_matters: Some(
                                "Box<dyn Trait> involves heap allocation and vtable indirection. \
                                Generics are monomorphized at compile time, enabling inlining and \
                                avoiding runtime overhead. However, generics increase binary size, \
                                so Box<dyn> can be the right choice for plugin architectures."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!("BoxDynTraitDetector found {} findings", findings.len());
        Ok(findings)
    }
}

// ============================================================================
// MutexPoisoningRiskDetector
// ============================================================================

/// Detects Mutex usage patterns that risk poisoning or deadlocks
pub struct MutexPoisoningRiskDetector {
    repository_path: PathBuf,
    max_findings: usize,
}

impl MutexPoisoningRiskDetector {
    pub fn new(repository_path: impl Into<PathBuf>) -> Self {
        Self {
            repository_path: repository_path.into(),
            max_findings: 50,
        }
    }
}

impl Detector for MutexPoisoningRiskDetector {
    fn name(&self) -> &'static str {
        "rust-mutex-poisoning-risk"
    }

    fn description(&self) -> &'static str {
        "Detects Mutex poisoning risks from panic-prone lock handling"
    }

    fn detect(&self, _graph: &dyn crate::graph::GraphQuery) -> Result<Vec<Finding>> {
        let mut findings = vec![];
        let walker = ignore::WalkBuilder::new(&self.repository_path)
            .hidden(false)
            .git_ignore(true)
            .build();

        for entry in walker.filter_map(|e| e.ok()) {
            if findings.len() >= self.max_findings {
                break;
            }

            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if ext != "rs" {
                continue;
            }

            if let Some(content) = crate::cache::global_cache().get_content(path) {
                for (i, line) in content.lines().enumerate() {
                    // Skip comments
                    let trimmed = line.trim();
                    if trimmed.starts_with("//") {
                        continue;
                    }

                    // Detect .lock().unwrap() pattern
                    if mutex_unwrap().is_match(line) {
                        let file_str = path.to_string_lossy();
                        let line_num = (i + 1) as u32;

                        findings.push(Finding {
                            id: deterministic_finding_id(
                                "MutexPoisoningRiskDetector",
                                &file_str,
                                line_num,
                                "mutex lock unwrap",
                            ),
                            detector: "MutexPoisoningRiskDetector".to_string(),
                            severity: Severity::Medium,
                            title: "Mutex poisoning risk: .lock().unwrap()".to_string(),
                            description: "Using .lock().unwrap() will panic if the mutex is \
                                poisoned (a thread panicked while holding it). Consider handling \
                                the PoisonError or using parking_lot::Mutex."
                                .to_string(),
                            affected_files: vec![path.to_path_buf()],
                            line_start: Some(line_num),
                            line_end: Some(line_num),
                            suggested_fix: Some(
                                "Handle mutex poisoning gracefully:\n\
                                ```rust\n\
                                // Option 1: Clear the poison and continue\n\
                                let guard = mutex.lock().unwrap_or_else(|e| {\n\
                                    eprintln!(\"Mutex was poisoned, recovering...\");\n\
                                    e.into_inner()\n\
                                });\n\n\
                                // Option 2: Use parking_lot::Mutex (no poisoning)\n\
                                use parking_lot::Mutex;\n\
                                let guard = mutex.lock(); // Never panics on poison\n\n\
                                // Option 3: Propagate with context\n\
                                let guard = mutex.lock().map_err(|e| {\n\
                                    anyhow!(\"mutex poisoned: {}\", e)\n\
                                })?;\n\n\
                                // Option 4: If panic is acceptable, document why\n\
                                // SAFETY: This mutex protects X. If it's poisoned,\n\
                                // the program state is unrecoverable.\n\
                                let guard = mutex.lock().expect(\"critical mutex poisoned\");\n\
                                ```"
                                .to_string(),
                            ),
                            estimated_effort: Some("15 minutes".to_string()),
                            category: Some("reliability".to_string()),
                            cwe_id: Some("CWE-667".to_string()),
                            why_it_matters: Some(
                                "When a thread panics while holding a std::sync::Mutex, the mutex \
                                becomes 'poisoned'. Subsequent .lock().unwrap() calls will panic, \
                                potentially cascading failures across threads. This is especially \
                                problematic in long-running services."
                                    .to_string(),
                            ),
                            ..Default::default()
                        });
                    }
                }
            }
        }

        info!(
            "MutexPoisoningRiskDetector found {} findings",
            findings.len()
        );
        Ok(findings)
    }
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    fn setup_test_file(content: &str) -> (TempDir, PathBuf) {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.rs");
        fs::write(&path, content).unwrap();
        (dir, path)
    }

    #[test]
    fn test_unwrap_detection() {
        let content = r#"
fn main() {
    let x = some_result.unwrap();
}
"#;
        let (dir, _) = setup_test_file(content);
        let detector = UnwrapWithoutContextDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
        assert!(findings[0].title.contains("unwrap"));
    }

    #[test]
    fn test_unwrap_in_test_skipped() {
        let content = r#"
#[test]
fn test_something() {
    let x = some_result.unwrap();
}
"#;
        let (dir, _) = setup_test_file(content);
        let detector = UnwrapWithoutContextDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert!(findings.is_empty());
    }

    #[test]
    fn test_unsafe_without_safety() {
        let content = r#"
fn dangerous() {
    unsafe {
        do_something();
    }
}
"#;
        let (dir, _) = setup_test_file(content);
        let detector = UnsafeWithoutSafetyCommentDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_unsafe_with_safety_ok() {
        let content = r#"
fn dangerous() {
    // SAFETY: pointer is valid and aligned
    unsafe {
        do_something();
    }
}
"#;
        let (dir, _) = setup_test_file(content);
        let detector = UnsafeWithoutSafetyCommentDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert!(findings.is_empty());
    }

    #[test]
    fn test_clone_in_loop() {
        let content = r#"
fn process(items: &[Item]) {
    for item in items {
        let owned = item.clone();
        do_something(owned);
    }
}
"#;
        let (dir, _) = setup_test_file(content);
        let detector = CloneInHotPathDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_missing_must_use() {
        let content = r#"
pub fn do_something() -> Result<(), Error> {
    Ok(())
}
"#;
        let (dir, _) = setup_test_file(content);
        let detector = MissingMustUseDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_must_use_present_ok() {
        let content = r#"
#[must_use]
pub fn do_something() -> Result<(), Error> {
    Ok(())
}
"#;
        let (dir, _) = setup_test_file(content);
        let detector = MissingMustUseDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert!(findings.is_empty());
    }

    #[test]
    fn test_mutex_poisoning_risk() {
        let content = r#"
fn get_data(mutex: &Mutex<Data>) -> Data {
    mutex.lock().unwrap().clone()
}
"#;
        let (dir, _) = setup_test_file(content);
        let detector = MutexPoisoningRiskDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_box_dyn_in_vec_ok() {
        let content = r#"
fn get_handlers() -> Vec<Box<dyn Handler>> {
    vec![]
}
"#;
        let (dir, _) = setup_test_file(content);
        let detector = BoxDynTraitDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        // Vec<Box<dyn>> is a valid use case, should be skipped
        assert!(findings.is_empty());
    }
}
