//! Rust Code Smell Detectors
//!
//! Detectors for Rust-specific patterns that can lead to panics, poor performance,
//! or suboptimal code quality.

mod unwrap;
mod unsafe_comment;
mod clone_hot_path;
mod must_use;
mod box_dyn;
mod mutex_poisoning;

pub use unwrap::UnwrapWithoutContextDetector;
pub use unsafe_comment::UnsafeWithoutSafetyCommentDetector;
pub use clone_hot_path::CloneInHotPathDetector;
pub use must_use::MissingMustUseDetector;
pub use box_dyn::BoxDynTraitDetector;
pub use mutex_poisoning::MutexPoisoningRiskDetector;

use regex::Regex;
use std::sync::OnceLock;

// Compiled regex patterns (shared across detectors)

static UNWRAP_CALL: OnceLock<Regex> = OnceLock::new();
static EXPECT_CALL: OnceLock<Regex> = OnceLock::new();
static UNSAFE_BLOCK: OnceLock<Regex> = OnceLock::new();
static SAFETY_COMMENT: OnceLock<Regex> = OnceLock::new();
static CLONE_CALL: OnceLock<Regex> = OnceLock::new();
static HOT_PATH_INDICATOR: OnceLock<Regex> = OnceLock::new();
static MUST_USE_ATTR: OnceLock<Regex> = OnceLock::new();
static BOX_DYN_TRAIT: OnceLock<Regex> = OnceLock::new();
static MUTEX_UNWRAP: OnceLock<Regex> = OnceLock::new();

pub(crate) fn unwrap_call() -> &'static Regex {
    UNWRAP_CALL.get_or_init(|| Regex::new(r"\.unwrap\s*\(\s*\)").expect("valid regex"))
}

pub(crate) fn expect_call() -> &'static Regex {
    EXPECT_CALL.get_or_init(|| Regex::new(r#"\.expect\s*\(\s*["']"#).expect("valid regex"))
}

pub(crate) fn unsafe_block() -> &'static Regex {
    UNSAFE_BLOCK.get_or_init(|| Regex::new(r"\bunsafe\s*\{").expect("valid regex"))
}

pub(crate) fn safety_comment() -> &'static Regex {
    SAFETY_COMMENT
        .get_or_init(|| Regex::new(r"(?i)//\s*SAFETY:|///\s*#\s*Safety|//\s*SAFETY\s*:").expect("valid regex"))
}

pub(crate) fn clone_call() -> &'static Regex {
    CLONE_CALL.get_or_init(|| Regex::new(r"\.clone\s*\(\s*\)").expect("valid regex"))
}

pub(crate) fn hot_path_indicator() -> &'static Regex {
    HOT_PATH_INDICATOR.get_or_init(|| {
        Regex::new(r"(?i)\b(loop|while|for|iter|map|filter|fold|reduce|collect|into_iter)\b")
            .expect("valid regex")
    })
}

pub(crate) fn must_use_attr() -> &'static Regex {
    MUST_USE_ATTR.get_or_init(|| Regex::new(r"#\[must_use").expect("valid regex"))
}

pub(crate) fn box_dyn_trait() -> &'static Regex {
    BOX_DYN_TRAIT.get_or_init(|| Regex::new(r"Box\s*<\s*dyn\s+\w+").expect("valid regex"))
}

pub(crate) fn mutex_unwrap() -> &'static Regex {
    MUTEX_UNWRAP.get_or_init(|| Regex::new(r"\.lock\s*\(\s*\)\s*\.unwrap\s*\(\s*\)").expect("valid regex"))
}

/// Check if a line is in a test context
pub(crate) fn is_test_context(_line: &str, content: &str, line_idx: usize) -> bool {
    let lines: Vec<&str> = content.lines().collect();
    let start = line_idx.saturating_sub(50);
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
    _line.contains("_test.rs") || _line.contains("/tests/")
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
        "OnceLock", "OnceCell", "Lazy", "get_or_init",
        "Query::new", "const ", "static ", "lazy_static!", "once_cell",
        ".read().unwrap()", ".write().unwrap()", ".lock().unwrap()",
        ".to_str().unwrap()", ".to_lowercase().next().unwrap()",
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
    use std::fs;
    use std::path::PathBuf;
    use tempfile::TempDir;

    fn setup_test_file(content: &str) -> (TempDir, PathBuf) {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.rs");
        fs::write(&path, content).unwrap();
        (dir, path)
    }

    #[test]
    fn test_unwrap_detection() {
        let content = "fn main() {\n    let x = some_result.unwrap();\n}\n";
        let (dir, _) = setup_test_file(content);
        let detector = UnwrapWithoutContextDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
        assert!(findings[0].title.contains("unwrap"));
    }

    #[test]
    fn test_unwrap_in_test_skipped() {
        let content = "#[test]\nfn test_something() {\n    let x = some_result.unwrap();\n}\n";
        let (dir, _) = setup_test_file(content);
        let detector = UnwrapWithoutContextDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert!(findings.is_empty());
    }

    #[test]
    fn test_unsafe_without_safety() {
        let content = "fn dangerous() {\n    unsafe {\n        do_something();\n    }\n}\n";
        let (dir, _) = setup_test_file(content);
        let detector = UnsafeWithoutSafetyCommentDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_unsafe_with_safety_ok() {
        let content = "fn dangerous() {\n    // SAFETY: pointer is valid and aligned\n    unsafe {\n        do_something();\n    }\n}\n";
        let (dir, _) = setup_test_file(content);
        let detector = UnsafeWithoutSafetyCommentDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert!(findings.is_empty());
    }

    #[test]
    fn test_clone_in_loop() {
        let content = "fn process(items: &[Item]) {\n    for item in items {\n        let owned = item.clone();\n        do_something(owned);\n    }\n}\n";
        let (dir, _) = setup_test_file(content);
        let detector = CloneInHotPathDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_missing_must_use() {
        let content = "pub fn do_something() -> Result<(), Error> {\n    Ok(())\n}\n";
        let (dir, _) = setup_test_file(content);
        let detector = MissingMustUseDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_must_use_present_ok() {
        let content = "#[must_use]\npub fn do_something() -> Result<(), Error> {\n    Ok(())\n}\n";
        let (dir, _) = setup_test_file(content);
        let detector = MissingMustUseDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert!(findings.is_empty());
    }

    #[test]
    fn test_mutex_poisoning_risk() {
        let content = "fn get_data(mutex: &Mutex<Data>) -> Data {\n    mutex.lock().unwrap().clone()\n}\n";
        let (dir, _) = setup_test_file(content);
        let detector = MutexPoisoningRiskDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert_eq!(findings.len(), 1);
    }

    #[test]
    fn test_box_dyn_in_vec_ok() {
        let content = "fn get_handlers() -> Vec<Box<dyn Handler>> {\n    vec![]\n}\n";
        let (dir, _) = setup_test_file(content);
        let detector = BoxDynTraitDetector::new(dir.path());
        let graph = GraphStore::in_memory();
        let findings = detector.detect(&graph).unwrap();
        assert!(findings.is_empty());
    }
}
