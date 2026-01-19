//! String batch operations for parallel text processing.
//!
//! This module provides SIMD-friendly and parallel implementations for common
//! string operations used in code analysis:
//! - `batch_strip_line_numbers`: Remove `:N` line number patterns from strings
//! - `batch_parse_qualified_names`: Parse qualified names like `file.py::Class:40.method:77`
//! - `batch_find_suffix_matches`: Parallel path suffix matching

use rayon::prelude::*;
use regex::Regex;
use lazy_static::lazy_static;

lazy_static! {
    /// Regex for stripping line numbers (`:N` where N is digits)
    static ref LINE_NUMBER_PATTERN: Regex = Regex::new(r":(\d+)").unwrap();
}

/// Parsed qualified name components.
#[derive(Debug, Clone, PartialEq)]
pub struct ParsedQualifiedName {
    /// The file path component (before `::`)
    pub file_path: String,
    /// The entity path (module, class, function components)
    pub entity_path: Vec<String>,
    /// Line numbers associated with each entity component
    pub line_numbers: Vec<Option<u32>>,
    /// The full original string
    pub original: String,
}

/// Strip line numbers from a single string.
/// Removes patterns like `:140` from `ClassName:140.method_name:177`.
#[must_use]
#[inline]
pub fn strip_line_numbers(s: &str) -> String {
    // Fast path: no colon followed by digit means no line numbers
    if !s.bytes().any(|b| b == b':') {
        return s.to_string();
    }

    LINE_NUMBER_PATTERN.replace_all(s, "").into_owned()
}

/// Batch strip line numbers from multiple strings in parallel.
#[must_use]
pub fn batch_strip_line_numbers(strings: &[&str]) -> Vec<String> {
    strings.par_iter().map(|s| strip_line_numbers(s)).collect()
}

/// Parse a qualified name into its components.
///
/// Format: `file.py::ClassName:140.method_name:177`
/// - File path comes before `::`
/// - Entity components are separated by `.`
/// - Line numbers follow entity names with `:`
#[must_use]
pub fn parse_qualified_name(qn: &str) -> ParsedQualifiedName {
    let original = qn.to_string();

    // Split on `::` to separate file path from entity path
    let (file_path, entity_part) = if let Some(idx) = qn.find("::") {
        (qn[..idx].to_string(), &qn[idx + 2..])
    } else {
        // No `::`, assume entire string is entity path
        (String::new(), qn)
    };

    // Split entity part on `.` to get components
    let parts: Vec<&str> = entity_part.split('.').collect();

    let mut entity_path = Vec::with_capacity(parts.len());
    let mut line_numbers = Vec::with_capacity(parts.len());

    for part in parts {
        if part.is_empty() {
            continue;
        }

        // Check if part has `:N` line number suffix
        if let Some(colon_idx) = part.rfind(':') {
            let potential_number = &part[colon_idx + 1..];
            if let Ok(line_num) = potential_number.parse::<u32>() {
                entity_path.push(part[..colon_idx].to_string());
                line_numbers.push(Some(line_num));
            } else {
                entity_path.push(part.to_string());
                line_numbers.push(None);
            }
        } else {
            entity_path.push(part.to_string());
            line_numbers.push(None);
        }
    }

    ParsedQualifiedName {
        file_path,
        entity_path,
        line_numbers,
        original,
    }
}

/// Batch parse qualified names in parallel.
#[must_use]
pub fn batch_parse_qualified_names(names: &[&str]) -> Vec<ParsedQualifiedName> {
    names.par_iter().map(|n| parse_qualified_name(n)).collect()
}

/// Check if a path ends with the given suffix.
/// Handles both path separators (/ and \).
#[must_use]
#[inline]
pub fn path_ends_with_suffix(path: &str, suffix: &str) -> bool {
    if suffix.is_empty() {
        return true;
    }
    if path.len() < suffix.len() {
        return false;
    }

    // Normalize separators for comparison
    let path_normalized: String = path.replace('\\', "/");
    let suffix_normalized: String = suffix.replace('\\', "/");

    // Check if path ends with suffix
    if path_normalized.ends_with(&suffix_normalized) {
        // Make sure it's a proper path boundary
        let remaining = path_normalized.len() - suffix_normalized.len();
        if remaining == 0 {
            return true;
        }
        // Check that the character before suffix is a path separator
        let prev_char = path_normalized.as_bytes()[remaining - 1];
        return prev_char == b'/';
    }

    false
}

/// Find all paths that end with any of the given suffixes.
/// Returns indices of matching paths.
#[must_use]
pub fn batch_find_suffix_matches(
    paths: &[&str],
    suffixes: &[&str],
) -> Vec<Vec<usize>> {
    // For each suffix, find matching path indices
    suffixes
        .par_iter()
        .map(|suffix| {
            paths
                .iter()
                .enumerate()
                .filter(|(_, path)| path_ends_with_suffix(path, suffix))
                .map(|(idx, _)| idx)
                .collect()
        })
        .collect()
}

/// Find the first path that ends with the given suffix.
/// Returns the index of the first match, or None.
#[must_use]
pub fn find_first_suffix_match(paths: &[&str], suffix: &str) -> Option<usize> {
    paths.iter().position(|path| path_ends_with_suffix(path, suffix))
}

/// Batch find first suffix matches in parallel.
/// Returns index of first matching path for each suffix (or usize::MAX if no match).
#[must_use]
pub fn batch_find_first_suffix_matches(
    paths: &[&str],
    suffixes: &[&str],
) -> Vec<Option<usize>> {
    suffixes
        .par_iter()
        .map(|suffix| find_first_suffix_match(paths, suffix))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_strip_line_numbers_basic() {
        assert_eq!(
            strip_line_numbers("ClassName:140.method_name:177"),
            "ClassName.method_name"
        );
    }

    #[test]
    fn test_strip_line_numbers_no_numbers() {
        assert_eq!(
            strip_line_numbers("ClassName.method_name"),
            "ClassName.method_name"
        );
    }

    #[test]
    fn test_strip_line_numbers_single() {
        assert_eq!(strip_line_numbers("func:42"), "func");
    }

    #[test]
    fn test_batch_strip_line_numbers() {
        let inputs = vec!["a:1", "b:2.c:3", "plain"];
        let results = batch_strip_line_numbers(&inputs);
        assert_eq!(results, vec!["a", "b.c", "plain"]);
    }

    #[test]
    fn test_parse_qualified_name_full() {
        let parsed = parse_qualified_name("src/utils.py::Calculator:40.add:55");
        assert_eq!(parsed.file_path, "src/utils.py");
        assert_eq!(parsed.entity_path, vec!["Calculator", "add"]);
        assert_eq!(parsed.line_numbers, vec![Some(40), Some(55)]);
    }

    #[test]
    fn test_parse_qualified_name_no_file() {
        let parsed = parse_qualified_name("Calculator:40.add:55");
        assert_eq!(parsed.file_path, "");
        assert_eq!(parsed.entity_path, vec!["Calculator", "add"]);
    }

    #[test]
    fn test_parse_qualified_name_no_line_numbers() {
        let parsed = parse_qualified_name("src/app.py::MyClass.my_method");
        assert_eq!(parsed.file_path, "src/app.py");
        assert_eq!(parsed.entity_path, vec!["MyClass", "my_method"]);
        assert_eq!(parsed.line_numbers, vec![None, None]);
    }

    #[test]
    fn test_batch_parse_qualified_names() {
        let names = vec!["a.py::A:1", "b.py::B:2.c:3"];
        let results = batch_parse_qualified_names(&names);
        assert_eq!(results.len(), 2);
        assert_eq!(results[0].file_path, "a.py");
        assert_eq!(results[1].entity_path, vec!["B", "c"]);
    }

    #[test]
    fn test_path_ends_with_suffix() {
        assert!(path_ends_with_suffix("src/utils/helper.py", "helper.py"));
        assert!(path_ends_with_suffix("src/utils/helper.py", "utils/helper.py"));
        assert!(path_ends_with_suffix("helper.py", "helper.py"));
        assert!(!path_ends_with_suffix("src/utils/helper.py", "lper.py"));
        assert!(!path_ends_with_suffix("short.py", "very/long/path.py"));
    }

    #[test]
    fn test_path_ends_with_suffix_windows() {
        assert!(path_ends_with_suffix("src\\utils\\helper.py", "helper.py"));
        assert!(path_ends_with_suffix("src\\utils\\helper.py", "utils/helper.py"));
    }

    #[test]
    fn test_batch_find_suffix_matches() {
        let paths = vec!["src/a.py", "src/utils/b.py", "tests/test_a.py"];
        let suffixes = vec!["a.py", "b.py", "c.py"];

        let results = batch_find_suffix_matches(&paths, &suffixes);

        assert_eq!(results.len(), 3);
        assert_eq!(results[0], vec![0]); // a.py matches src/a.py
        assert_eq!(results[1], vec![1]); // b.py matches src/utils/b.py
        assert!(results[2].is_empty()); // c.py has no matches
    }

    #[test]
    fn test_batch_find_first_suffix_matches() {
        let paths = vec!["src/a.py", "tests/a.py", "lib/b.py"];
        let suffixes = vec!["a.py", "b.py", "c.py"];

        let results = batch_find_first_suffix_matches(&paths, &suffixes);

        assert_eq!(results[0], Some(0)); // first a.py match
        assert_eq!(results[1], Some(2)); // b.py match
        assert_eq!(results[2], None); // no c.py
    }
}
