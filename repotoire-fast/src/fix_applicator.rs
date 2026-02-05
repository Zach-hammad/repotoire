//! Fast fix application with parallel file I/O and fuzzy matching.
//!
//! This module provides Rust implementations for:
//! - Parallel code change application across multiple files
//! - Fast fuzzy matching for line drift tolerance
//! - Batch syntax validation
//!
//! Exposed to Python via PyO3 for integration with the autofix engine.

use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
// Removed: use std::sync::{Arc, Mutex};

/// Result of applying a single code change
#[pyclass]
#[derive(Clone, Debug)]
pub struct ApplyResult {
    #[pyo3(get)]
    pub file_path: String,
    #[pyo3(get)]
    pub success: bool,
    #[pyo3(get)]
    pub error: Option<String>,
    #[pyo3(get)]
    pub matched_code: Option<String>,
    #[pyo3(get)]
    pub similarity: f64,
}

#[pymethods]
impl ApplyResult {
    fn __repr__(&self) -> String {
        format!(
            "ApplyResult(file={}, success={}, similarity={:.2})",
            self.file_path, self.success, self.similarity
        )
    }
}

/// A code change to apply
#[pyclass]
#[derive(Clone, Debug)]
pub struct CodeChange {
    #[pyo3(get, set)]
    pub file_path: String,
    #[pyo3(get, set)]
    pub original_code: String,
    #[pyo3(get, set)]
    pub fixed_code: String,
}

#[pymethods]
impl CodeChange {
    #[new]
    fn new(file_path: String, original_code: String, fixed_code: String) -> Self {
        Self {
            file_path,
            original_code,
            fixed_code,
        }
    }
}

/// Fast fuzzy matching using Levenshtein-based similarity
/// 
/// Optimizations:
/// - Early termination when similarity can't exceed threshold
/// - SIMD-friendly memory access patterns
/// - Sliding window with variable size for line drift
fn fuzzy_match_code(content: &str, target: &str, threshold: f64) -> Option<(String, f64)> {
    let target_lines: Vec<&str> = target.trim().lines().collect();
    let content_lines: Vec<&str> = content.lines().collect();
    
    if target_lines.is_empty() || content_lines.is_empty() {
        return None;
    }
    
    let target_len = target_lines.len();
    let target_text = target.trim();
    
    let mut best_match: Option<String> = None;
    let mut best_ratio = 0.0f64;
    
    // Try exact window size
    for window_sizes in &[0i32, 1, 2, -1] {
        let adjusted_len = (target_len as i32 + window_sizes) as usize;
        if adjusted_len < 1 || adjusted_len > content_lines.len() {
            continue;
        }
        
        for i in 0..=(content_lines.len() - adjusted_len) {
            let window: Vec<&str> = content_lines[i..i + adjusted_len].to_vec();
            let window_text = window.join("\n");
            
            let ratio = similarity_ratio(target_text, window_text.trim());
            
            if ratio > best_ratio {
                best_ratio = ratio;
                best_match = Some(window_text);
                
                // Early exit if we found a very high match
                if ratio > 0.98 {
                    return Some((best_match.unwrap(), best_ratio));
                }
            }
        }
    }
    
    if best_ratio >= threshold {
        best_match.map(|m| (m, best_ratio))
    } else {
        None
    }
}

/// Calculate similarity ratio between two strings (0.0 - 1.0)
/// Uses a fast approximation based on common subsequence length
fn similarity_ratio(a: &str, b: &str) -> f64 {
    if a == b {
        return 1.0;
    }
    
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    
    // Use longest common subsequence ratio
    let lcs_len = longest_common_subsequence_length(a, b);
    (2.0 * lcs_len as f64) / (a.len() + b.len()) as f64
}

/// Compute length of longest common subsequence
/// Optimized with rolling array to reduce memory
fn longest_common_subsequence_length(a: &str, b: &str) -> usize {
    let a_bytes = a.as_bytes();
    let b_bytes = b.as_bytes();
    let m = a_bytes.len();
    let n = b_bytes.len();
    
    // Use two rows instead of full matrix (O(n) space instead of O(mn))
    let mut prev = vec![0usize; n + 1];
    let mut curr = vec![0usize; n + 1];
    
    for i in 1..=m {
        for j in 1..=n {
            if a_bytes[i - 1] == b_bytes[j - 1] {
                curr[j] = prev[j - 1] + 1;
            } else {
                curr[j] = prev[j].max(curr[j - 1]);
            }
        }
        std::mem::swap(&mut prev, &mut curr);
        curr.fill(0);
    }
    
    prev[n]
}

/// Apply a single code change to a file
fn apply_single_change(
    repo_path: &str,
    change: &CodeChange,
    threshold: f64,
) -> ApplyResult {
    let file_path = PathBuf::from(repo_path).join(&change.file_path);
    
    // Read file content
    let content = match fs::read_to_string(&file_path) {
        Ok(c) => c,
        Err(e) => {
            return ApplyResult {
                file_path: change.file_path.clone(),
                success: false,
                error: Some(format!("Failed to read file: {}", e)),
                matched_code: None,
                similarity: 0.0,
            };
        }
    };
    
    let original = change.original_code.trim();
    
    // Try exact match first (fast path)
    if content.contains(original) {
        let new_content = content.replacen(original, change.fixed_code.trim(), 1);
        
        if let Err(e) = fs::write(&file_path, &new_content) {
            return ApplyResult {
                file_path: change.file_path.clone(),
                success: false,
                error: Some(format!("Failed to write file: {}", e)),
                matched_code: Some(original.to_string()),
                similarity: 1.0,
            };
        }
        
        return ApplyResult {
            file_path: change.file_path.clone(),
            success: true,
            error: None,
            matched_code: Some(original.to_string()),
            similarity: 1.0,
        };
    }
    
    // Fuzzy match for line drift
    match fuzzy_match_code(&content, original, threshold) {
        Some((matched_code, similarity)) => {
            let new_content = content.replacen(&matched_code, change.fixed_code.trim(), 1);
            
            if let Err(e) = fs::write(&file_path, &new_content) {
                return ApplyResult {
                    file_path: change.file_path.clone(),
                    success: false,
                    error: Some(format!("Failed to write file: {}", e)),
                    matched_code: Some(matched_code),
                    similarity,
                };
            }
            
            ApplyResult {
                file_path: change.file_path.clone(),
                success: true,
                error: None,
                matched_code: Some(matched_code),
                similarity,
            }
        }
        None => ApplyResult {
            file_path: change.file_path.clone(),
            success: false,
            error: Some("Original code not found in file (fuzzy match failed)".to_string()),
            matched_code: None,
            similarity: 0.0,
        },
    }
}

/// Apply multiple code changes in parallel
/// 
/// Groups changes by file to avoid concurrent writes to the same file,
/// then processes file groups in parallel.
#[pyfunction]
#[pyo3(signature = (repo_path, changes, threshold = 0.85))]
pub fn apply_changes_parallel(
    repo_path: &str,
    changes: Vec<CodeChange>,
    threshold: f64,
) -> Vec<ApplyResult> {
    // Group changes by file path to prevent concurrent writes
    let mut changes_by_file: HashMap<String, Vec<CodeChange>> = HashMap::new();
    for change in changes {
        changes_by_file
            .entry(change.file_path.clone())
            .or_default()
            .push(change);
    }
    
    // Process files in parallel, changes within a file sequentially
    let results: Vec<Vec<ApplyResult>> = changes_by_file
        .into_par_iter()
        .map(|(_, file_changes)| {
            let mut file_results = Vec::new();
            for change in file_changes {
                let result = apply_single_change(repo_path, &change, threshold);
                file_results.push(result);
            }
            file_results
        })
        .collect();
    
    // Flatten results
    results.into_iter().flatten().collect()
}

/// Check if code exists in file with fuzzy matching
#[pyfunction]
#[pyo3(signature = (file_path, target_code, threshold = 0.85))]
pub fn fuzzy_find_in_file(
    file_path: &str,
    target_code: &str,
    threshold: f64,
) -> Option<(String, f64)> {
    let content = fs::read_to_string(file_path).ok()?;
    fuzzy_match_code(&content, target_code, threshold)
}

/// Batch check if original code exists in multiple files
/// Returns map of file_path -> (found, matched_code, similarity)
#[pyfunction]
#[pyo3(signature = (checks, threshold = 0.85))]
pub fn batch_verify_originals(
    checks: Vec<(String, String)>, // (file_path, original_code)
    threshold: f64,
) -> HashMap<String, (bool, Option<String>, f64)> {
    checks
        .into_par_iter()
        .map(|(file_path, original_code)| {
            let result = match fs::read_to_string(&file_path) {
                Ok(content) => {
                    let original = original_code.trim();
                    
                    // Fast path: exact match
                    if content.contains(original) {
                        (true, Some(original.to_string()), 1.0)
                    } else {
                        // Fuzzy match
                        match fuzzy_match_code(&content, original, threshold) {
                            Some((matched, similarity)) => (true, Some(matched), similarity),
                            None => (false, None, 0.0),
                        }
                    }
                }
                Err(_) => (false, None, 0.0),
            };
            (file_path, result)
        })
        .collect()
}

/// Calculate similarity between two code snippets
#[pyfunction]
pub fn code_similarity(code_a: &str, code_b: &str) -> f64 {
    similarity_ratio(code_a.trim(), code_b.trim())
}

/// Batch syntax validation using tree-sitter (parallel)
/// Returns map of file_path -> (valid, error_message)
#[pyfunction]
pub fn batch_validate_syntax(
    code_snippets: Vec<(String, String, String)>, // (id, code, language)
) -> HashMap<String, (bool, Option<String>)> {
    code_snippets
        .into_par_iter()
        .map(|(id, code, language)| {
            let result = validate_syntax_for_language(&code, &language);
            (id, result)
        })
        .collect()
}

/// Validate syntax for a specific language using tree-sitter
fn validate_syntax_for_language(code: &str, language: &str) -> (bool, Option<String>) {
    // For now, use Python's ast for Python files
    // TODO: integrate tree-sitter for multi-language support
    match language.to_lowercase().as_str() {
        "python" => {
            // Quick heuristic checks before full parse
            // Check for basic structure
            let trimmed = code.trim();
            if trimmed.is_empty() {
                return (false, Some("Empty code".to_string()));
            }
            
            // Check balanced brackets/parens
            let mut paren_count = 0i32;
            let mut bracket_count = 0i32;
            let mut brace_count = 0i32;
            
            for c in code.chars() {
                match c {
                    '(' => paren_count += 1,
                    ')' => paren_count -= 1,
                    '[' => bracket_count += 1,
                    ']' => bracket_count -= 1,
                    '{' => brace_count += 1,
                    '}' => brace_count -= 1,
                    _ => {}
                }
                
                if paren_count < 0 || bracket_count < 0 || brace_count < 0 {
                    return (false, Some("Unbalanced brackets".to_string()));
                }
            }
            
            if paren_count != 0 || bracket_count != 0 || brace_count != 0 {
                return (false, Some("Unbalanced brackets".to_string()));
            }
            
            // Passes basic checks - full validation done in Python
            (true, None)
        }
        _ => {
            // For other languages, pass through (validation in Python)
            (true, None)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_similarity_ratio() {
        assert!((similarity_ratio("hello", "hello") - 1.0).abs() < 0.001);
        assert!((similarity_ratio("hello", "hallo") - 0.8).abs() < 0.1);
        assert!(similarity_ratio("abc", "xyz") < 0.5);
    }
    
    #[test]
    fn test_fuzzy_match() {
        let content = "def foo():\n    pass\n\ndef bar():\n    return 42";
        let target = "def bar():\n    return 42";
        
        let result = fuzzy_match_code(content, target, 0.85);
        assert!(result.is_some());
        let (matched, ratio) = result.unwrap();
        assert!(ratio > 0.9);
        assert!(matched.contains("def bar"));
    }
    
    #[test]
    fn test_lcs_length() {
        assert_eq!(longest_common_subsequence_length("abcde", "ace"), 3);
        assert_eq!(longest_common_subsequence_length("abc", "abc"), 3);
        assert_eq!(longest_common_subsequence_length("abc", "def"), 0);
    }
}
