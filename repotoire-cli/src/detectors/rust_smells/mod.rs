//! Rust Code Smell Detectors
//!
//! Detectors for Rust-specific patterns that can lead to panics, poor performance,
//! or suboptimal code quality.

mod box_dyn;
mod clone_hot_path;
mod must_use;
mod mutex_poisoning;
mod panic_density;
mod unsafe_comment;
mod unwrap;

pub use box_dyn::BoxDynTraitDetector;
pub use clone_hot_path::CloneInHotPathDetector;
pub use must_use::MissingMustUseDetector;
pub use mutex_poisoning::MutexPoisoningRiskDetector;
pub use panic_density::PanicDensityDetector;
pub use unsafe_comment::UnsafeWithoutSafetyCommentDetector;
pub use unwrap::UnwrapWithoutContextDetector;

use regex::Regex;
use std::sync::LazyLock;

// Compiled regex patterns (shared across detectors)

static UNWRAP_CALL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\.unwrap\s*\(\s*\)").expect("valid regex"));
static EXPECT_CALL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"\.expect\s*\(\s*["']"#).expect("valid regex"));
static UNSAFE_BLOCK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\bunsafe\s*\{").expect("valid regex"));
static SAFETY_COMMENT: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)//\s*SAFETY:|///\s*#\s*Safety|//\s*SAFETY\s*:").expect("valid regex")
    });
static CLONE_CALL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\.clone\s*\(\s*\)").expect("valid regex"));
static HOT_PATH_INDICATOR: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)\b(loop|while|for|iter|map|filter|fold|reduce|collect|into_iter)\b")
            .expect("valid regex")
    });
static MUST_USE_ATTR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"#\[must_use").expect("valid regex"));
static BOX_DYN_TRAIT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"Box\s*<\s*dyn\s+\w+").expect("valid regex"));
static MUTEX_UNWRAP: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\.lock\s*\(\s*\)\s*\.unwrap\s*\(\s*\)").expect("valid regex"));

/// Check if a line is in a test context
pub(crate) fn is_test_context(_line: &str, content: &str, line_idx: usize) -> bool {
    let lines: Vec<&str> = content.lines().collect();
    // Scan all preceding lines — #[cfg(test)] may be hundreds of lines above
    for i in (0..=line_idx).rev() {
        if let Some(prev_line) = lines.get(i) {
            let trimmed = prev_line.trim();
            if trimmed.contains("#[test]")
                || trimmed.contains("#[cfg(test)]")
                || trimmed.starts_with("mod tests")
            {
                return true;
            }
        }
    }
    _line.contains("_test.rs") || _line.contains("/tests/")
}

/// Pre-compute test context for all lines in O(n) using brace tracking.
///
/// Returns a Vec<bool> where `true` means the line is inside a test region.
/// Handles `#[cfg(test)]` modules, `mod tests { }` blocks, and `#[test]` functions.
pub(crate) fn precompute_test_context(lines: &[&str]) -> Vec<bool> {
    let mut is_test = vec![false; lines.len()];
    let mut test_brace_depth: Option<i32> = None; // Some(depth) when inside test block
    let mut brace_depth: i32 = 0;

    for (i, line) in lines.iter().enumerate() {
        let trimmed = line.trim();

        // Detect test region start
        if test_brace_depth.is_none() {
            if trimmed.contains("#[cfg(test)]")
                || trimmed.starts_with("mod tests")
                || trimmed.contains("#[test]")
            {
                test_brace_depth = Some(brace_depth);
                is_test[i] = true;
            }
        }

        // Update brace depth
        for ch in line.chars() {
            match ch {
                '{' => brace_depth += 1,
                '}' => brace_depth -= 1,
                _ => {}
            }
        }

        // Mark test lines and check for test region end
        if let Some(start_depth) = test_brace_depth {
            is_test[i] = true;
            // Test region ends when brace depth returns to or below the starting depth
            if brace_depth <= start_depth && i > 0 {
                // For #[test] on individual functions, end when function closes
                // For mod tests { }, end when the module closes
                test_brace_depth = None;
            }
        }
    }

    is_test
}

/// Check if unwrap is on a known-safe pattern
pub(crate) fn is_safe_unwrap_context(line: &str, content: &str, line_idx: usize) -> bool {
    let trimmed = line.trim();

    if trimmed.starts_with("//") || trimmed.starts_with("/*") {
        return true;
    }

    if trimmed.ends_with("\\n\\") || trimmed.starts_with('"') || trimmed.starts_with("r#\"") {
        return true;
    }

    let safe_patterns = [
        "OnceLock",
        "OnceCell",
        "Lazy",
        "get_or_init",
        "Query::new",
        "const ",
        "static ",
        "lazy_static!",
        "once_cell",
        ".read().unwrap()",
        ".write().unwrap()",
        ".lock().unwrap()",
        ".to_str().unwrap()",
        ".to_lowercase().next().unwrap()",
    ];

    // Check for Regex::new without triggering self-detection
    const REGEX_CTOR: &str = "Regex\x3a\x3anew";
    if line.contains(REGEX_CTOR) {
        return true;
    }

    for pattern in &safe_patterns {
        if line.contains(pattern) {
            return true;
        }
    }

    if line.contains("env::var") && content.contains("unwrap_or") {
        return true;
    }

    // Multi-line: check if preceding lines contain regex construction
    let lines: Vec<&str> = content.lines().collect();
    for j in line_idx.saturating_sub(3)..line_idx {
        if let Some(prev) = lines.get(j) {
            if prev.contains(REGEX_CTOR) {
                return true;
            }
        }
    }

    is_test_context(line, content, line_idx)
}

/// Check if expect() has a meaningful message
pub(crate) fn has_meaningful_expect_message(line: &str) -> bool {
    if let Some(start) = line.find(".expect(") {
        let after = &line[start + 8..];
        if let Some(quote_start) = after.find('"').or_else(|| after.find('\'')) {
            let msg_start = quote_start + 1;
            if let Some(content) = after.get(msg_start..) {
                let words: Vec<&str> = content.split_whitespace().collect();
                return !words.is_empty();
            }
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::base::Detector;
    use crate::graph::GraphStore;

    #[test]
    fn test_unwrap_detection() {
        let graph = GraphStore::in_memory();
        let detector = UnwrapWithoutContextDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.rs", "fn main() {\n    let x = some_result.unwrap();\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert_eq!(findings.len(), 1);
        assert!(findings[0].title.contains("unwrap"));
    }

    #[test]
    fn test_unwrap_in_test_skipped() {
        let graph = GraphStore::in_memory();
        let detector = UnwrapWithoutContextDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.rs", "#[test]\nfn test_something() {\n    let x = some_result.unwrap();\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty());
    }

    #[test]
    fn test_unsafe_without_safety() {
        let graph = GraphStore::in_memory();
        let detector = UnsafeWithoutSafetyCommentDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.rs", "fn dangerous() {\n    unsafe {\n        do_something();\n    }\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_unsafe_with_safety_ok() {
        let graph = GraphStore::in_memory();
        let detector = UnsafeWithoutSafetyCommentDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.rs", "fn dangerous() {\n    // SAFETY: pointer is valid and aligned\n    unsafe {\n        do_something();\n    }\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty());
    }

    #[test]
    fn test_clone_in_loop() {
        let graph = GraphStore::in_memory();
        let detector = CloneInHotPathDetector::new("/mock/repo");
        // Two clones in a loop — exceeds MIN_CLONES_TO_FLAG for orphan hits
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.rs", "fn process(items: &[Item]) {\n    for item in items {\n        let owned = item.clone();\n        let name = item.name.clone();\n        do_something(owned, name);\n    }\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_missing_must_use() {
        let graph = GraphStore::in_memory();
        let detector = MissingMustUseDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.rs", "pub fn do_something() -> Result<(), Error> {\n    Ok(())\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_must_use_present_ok() {
        let graph = GraphStore::in_memory();
        let detector = MissingMustUseDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.rs", "#[must_use]\npub fn do_something() -> Result<(), Error> {\n    Ok(())\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty());
    }

    #[test]
    fn test_mutex_poisoning_risk() {
        let graph = GraphStore::in_memory();
        let detector = MutexPoisoningRiskDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.rs", "fn get_data(mutex: &Mutex<Data>) -> Data {\n    mutex.lock().unwrap().clone()\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_box_dyn_in_vec_ok() {
        let graph = GraphStore::in_memory();
        let detector = BoxDynTraitDetector::new("/mock/repo");
        let ctx = crate::detectors::analysis_context::AnalysisContext::test_with_mock_files(&graph, vec![
            ("test.rs", "fn get_handlers() -> Vec<Box<dyn Handler>> {\n    vec![]\n}\n"),
        ]);
        let findings = detector.detect(&ctx).expect("detection should succeed");
        assert!(findings.is_empty());
    }
}
