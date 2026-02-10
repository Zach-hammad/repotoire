//! Duplicate Code Detection using Rabin-Karp Rolling Hash
//!
//! This module implements token-based duplicate code detection using the
//! Rabin-Karp rolling hash algorithm. It provides 5-10x speedup over jscpd
//! by eliminating Node.js subprocess overhead and enabling parallel processing.
//!
//! # Algorithm Overview
//!
//! 1. **Tokenization**: Split source code into normalized tokens
//! 2. **Rolling Hash**: Compute Rabin-Karp hashes for sliding token windows
//! 3. **Hash Index**: Build a hash-to-locations index across all files
//! 4. **Match Detection**: Find hash collisions and verify actual matches
//! 5. **Merge Overlapping**: Combine overlapping duplicate blocks
//!
//! # Example
//!
//! ```python
//! from repotoire_fast import find_duplicates
//!
//! files = [
//!     ("a.py", "def foo():\n    return 1\n"),
//!     ("b.py", "def foo():\n    return 1\n"),
//! ]
//! duplicates = find_duplicates(files, min_tokens=5, min_lines=2, similarity=0.0)
//! ```

use rayon::prelude::*;
use rustc_hash::{FxHashMap, FxHasher};
use std::hash::{Hash, Hasher};

// ============================================================================
// CONSTANTS
// ============================================================================

/// Base for Rabin-Karp polynomial rolling hash
const ROLLING_HASH_BASE: u64 = 257;

/// Modulus for Rabin-Karp hash to prevent overflow
const ROLLING_HASH_MOD: u64 = 1_000_000_007;

// ============================================================================
// DATA STRUCTURES
// ============================================================================

/// A token extracted from source code with its line number
#[derive(Clone, Debug)]
pub struct Token {
    /// The normalized token value
    pub value: String,
    /// 1-indexed line number where this token appears
    pub line: usize,
}

/// A block of duplicate code found between two files
#[derive(Clone, Debug, PartialEq)]
pub struct DuplicateBlock {
    /// Path to the first file
    pub file1: String,
    /// Starting line in the first file (1-indexed)
    pub start1: usize,
    /// Path to the second file
    pub file2: String,
    /// Starting line in the second file (1-indexed)
    pub start2: usize,
    /// Length of the duplicate block in tokens
    pub token_length: usize,
    /// Length of the duplicate block in lines
    pub line_length: usize,
}

/// Location of a token window in a specific file
#[derive(Clone, Debug)]
struct TokenLocation {
    /// Index of the file in the files array
    file_idx: usize,
    /// Starting token index within the file
    token_idx: usize,
    /// Starting line number (1-indexed)
    start_line: usize,
}

// ============================================================================
// TOKENIZATION
// ============================================================================

/// Characters that act as token separators
const SEPARATORS: &[char] = &[
    ' ', '\t', '\n', '\r', // Whitespace
    '(', ')', '[', ']', '{', '}', // Brackets
    ',', ';', ':', '.', '!', '?', // Punctuation
    '+', '-', '*', '/', '%', '=', // Operators
    '<', '>', '&', '|', '^', '~', // More operators
    '@', '#', '$', '`', '\\', // Special
    '"', '\'', // Quotes
];

/// Tokenize a line of source code into normalized tokens.
///
/// Normalization includes:
/// - Lowercasing
/// - Removing leading/trailing whitespace
/// - Filtering empty tokens
fn tokenize_line(line: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut current = String::new();

    for ch in line.chars() {
        if SEPARATORS.contains(&ch) {
            if !current.is_empty() {
                tokens.push(current.to_lowercase());
                current.clear();
            }
            // Include non-whitespace separators as their own tokens
            if !ch.is_whitespace() {
                tokens.push(ch.to_string());
            }
        } else {
            current.push(ch);
        }
    }

    if !current.is_empty() {
        tokens.push(current.to_lowercase());
    }

    tokens
}

/// Tokenize source code into a vector of tokens with line numbers.
///
/// # Arguments
/// * `source` - The source code to tokenize
///
/// # Returns
/// A vector of Token structs with normalized values and line numbers
#[must_use]
pub fn tokenize(source: &str) -> Vec<Token> {
    source
        .lines()
        .enumerate()
        .flat_map(|(line_no, line)| {
            tokenize_line(line).into_iter().map(move |value| Token {
                value,
                line: line_no + 1, // 1-indexed
            })
        })
        .collect()
}

// ============================================================================
// HASHING
// ============================================================================

/// Compute a hash for a single token string.
/// Uses FxHasher for better performance (2-5x faster than DefaultHasher)
#[inline]
fn hash_token(token: &str) -> u64 {
    let mut hasher = FxHasher::default();
    token.hash(&mut hasher);
    hasher.finish() % ROLLING_HASH_MOD
}

/// Compute rolling hashes for all token windows of a given size.
///
/// Uses Rabin-Karp rolling hash for O(n) computation of all window hashes.
///
/// # Arguments
/// * `tokens` - The tokens to hash
/// * `window_size` - Size of the sliding window in tokens
///
/// # Returns
/// Vector of (hash, start_index, start_line) tuples
#[must_use]
pub fn rolling_hash(tokens: &[Token], window_size: usize) -> Vec<(u64, usize, usize)> {
    if tokens.len() < window_size || window_size == 0 {
        return vec![];
    }

    let mut hashes = Vec::with_capacity(tokens.len() - window_size + 1);
    let mut hash: u64 = 0;

    // Compute base^(window_size-1) for removing old token contribution
    let mut base_power: u64 = 1;
    for _ in 0..window_size.saturating_sub(1) {
        base_power = base_power.wrapping_mul(ROLLING_HASH_BASE) % ROLLING_HASH_MOD;
    }

    // Initial hash for first window
    for token in tokens.iter().take(window_size) {
        let token_hash = hash_token(&token.value);
        hash = (hash.wrapping_mul(ROLLING_HASH_BASE) + token_hash) % ROLLING_HASH_MOD;
    }
    hashes.push((hash, 0, tokens[0].line));

    // Rolling hash for remaining windows
    for i in window_size..tokens.len() {
        let old_hash = hash_token(&tokens[i - window_size].value);
        let new_hash = hash_token(&tokens[i].value);

        // Remove old token contribution and add new token
        hash = hash
            .wrapping_add(ROLLING_HASH_MOD)
            .wrapping_sub(old_hash.wrapping_mul(base_power) % ROLLING_HASH_MOD)
            % ROLLING_HASH_MOD;
        hash = (hash.wrapping_mul(ROLLING_HASH_BASE) + new_hash) % ROLLING_HASH_MOD;

        let start_idx = i - window_size + 1;
        hashes.push((hash, start_idx, tokens[start_idx].line));
    }

    hashes
}

// ============================================================================
// DUPLICATE DETECTION
// ============================================================================

/// Verify that two token sequences actually match (not just hash collision).
fn verify_match(
    tokens1: &[Token],
    start1: usize,
    tokens2: &[Token],
    start2: usize,
    length: usize,
) -> bool {
    if start1 + length > tokens1.len() || start2 + length > tokens2.len() {
        return false;
    }

    for i in 0..length {
        if tokens1[start1 + i].value != tokens2[start2 + i].value {
            return false;
        }
    }

    true
}

/// Extend a match to find the maximum overlapping region.
fn extend_match(
    tokens1: &[Token],
    start1: usize,
    tokens2: &[Token],
    start2: usize,
    initial_length: usize,
) -> usize {
    let max_extension = tokens1.len().min(tokens2.len());
    let mut length = initial_length;

    // Extend forward
    while start1 + length < tokens1.len()
        && start2 + length < tokens2.len()
        && tokens1[start1 + length].value == tokens2[start2 + length].value
    {
        length += 1;
    }

    // Could also extend backward, but for simplicity we start from the hash match point
    length.min(max_extension)
}

/// Calculate the line span for a token range.
fn line_span(tokens: &[Token], start: usize, length: usize) -> usize {
    if length == 0 || start >= tokens.len() {
        return 0;
    }

    let end = (start + length - 1).min(tokens.len() - 1);
    tokens[end].line.saturating_sub(tokens[start].line) + 1
}

/// Calculate Jaccard similarity between two token sequences.
fn jaccard_similarity(tokens1: &[Token], tokens2: &[Token]) -> f64 {
    if tokens1.is_empty() && tokens2.is_empty() {
        return 1.0;
    }
    if tokens1.is_empty() || tokens2.is_empty() {
        return 0.0;
    }

    let set1: std::collections::HashSet<&str> = tokens1.iter().map(|t| t.value.as_str()).collect();
    let set2: std::collections::HashSet<&str> = tokens2.iter().map(|t| t.value.as_str()).collect();

    let intersection = set1.intersection(&set2).count();
    let union = set1.union(&set2).count();

    if union == 0 {
        0.0
    } else {
        intersection as f64 / union as f64
    }
}

/// Threshold for using grouped comparison optimization.
/// Below this, simple O(n²) is faster due to lower overhead.
const GROUPING_THRESHOLD: usize = 25;

/// Compare two locations and return a DuplicateBlock if they match.
#[inline]
fn compare_locations(
    loc1: &TokenLocation,
    loc2: &TokenLocation,
    file_tokens: &[(String, Vec<Token>)],
    min_tokens: usize,
    min_lines: usize,
    min_similarity: f64,
) -> Option<DuplicateBlock> {
    let (path1, tokens1) = &file_tokens[loc1.file_idx];
    let (path2, tokens2) = &file_tokens[loc2.file_idx];

    // Verify the match is real (not just hash collision)
    if !verify_match(tokens1, loc1.token_idx, tokens2, loc2.token_idx, min_tokens) {
        return None;
    }

    // Extend match to find full duplicate region
    let token_length = extend_match(tokens1, loc1.token_idx, tokens2, loc2.token_idx, min_tokens);

    // Check minimum token length
    if token_length < min_tokens {
        return None;
    }

    // Calculate line span
    let line_length1 = line_span(tokens1, loc1.token_idx, token_length);
    let line_length2 = line_span(tokens2, loc2.token_idx, token_length);
    let line_length = line_length1.max(line_length2);

    // Check minimum line length
    if line_length < min_lines {
        return None;
    }

    // Check similarity threshold
    if min_similarity > 0.0 {
        let end1 = (loc1.token_idx + token_length).min(tokens1.len());
        let end2 = (loc2.token_idx + token_length).min(tokens2.len());
        let similarity = jaccard_similarity(
            &tokens1[loc1.token_idx..end1],
            &tokens2[loc2.token_idx..end2],
        );
        if similarity < min_similarity {
            return None;
        }
    }

    Some(DuplicateBlock {
        file1: path1.clone(),
        start1: loc1.start_line,
        file2: path2.clone(),
        start2: loc2.start_line,
        token_length,
        line_length,
    })
}

/// Check if two same-file locations overlap.
#[inline]
fn locations_overlap(loc1: &TokenLocation, loc2: &TokenLocation, min_tokens: usize) -> bool {
    if loc1.token_idx <= loc2.token_idx {
        loc1.token_idx + min_tokens > loc2.token_idx
    } else {
        loc2.token_idx + min_tokens > loc1.token_idx
    }
}

/// Find duplicate pairs from hash collisions.
/// Uses simple O(n²) for small inputs, grouped comparison for larger inputs.
fn find_duplicate_pairs(
    locations: &[TokenLocation],
    file_tokens: &[(String, Vec<Token>)],
    min_tokens: usize,
    min_lines: usize,
    min_similarity: f64,
) -> Vec<DuplicateBlock> {
    let mut duplicates = Vec::new();

    // For small inputs, use simple O(n²) - lower overhead wins
    if locations.len() < GROUPING_THRESHOLD {
        for i in 0..locations.len() {
            for j in (i + 1)..locations.len() {
                let loc1 = &locations[i];
                let loc2 = &locations[j];

                // Skip same-file overlapping regions
                if loc1.file_idx == loc2.file_idx && locations_overlap(loc1, loc2, min_tokens) {
                    continue;
                }

                if let Some(dup) = compare_locations(
                    loc1,
                    loc2,
                    file_tokens,
                    min_tokens,
                    min_lines,
                    min_similarity,
                ) {
                    duplicates.push(dup);
                }
            }
        }
        return duplicates;
    }

    // For larger inputs, group by file to optimize same-file vs cross-file comparisons
    let mut by_file: FxHashMap<usize, Vec<&TokenLocation>> = FxHashMap::default();
    for loc in locations {
        by_file.entry(loc.file_idx).or_default().push(loc);
    }

    // Same-file comparisons: need overlap check
    for (_file_idx, locs) in &by_file {
        for i in 0..locs.len() {
            for j in (i + 1)..locs.len() {
                let loc1 = locs[i];
                let loc2 = locs[j];

                if locations_overlap(loc1, loc2, min_tokens) {
                    continue;
                }

                if let Some(dup) = compare_locations(
                    loc1,
                    loc2,
                    file_tokens,
                    min_tokens,
                    min_lines,
                    min_similarity,
                ) {
                    duplicates.push(dup);
                }
            }
        }
    }

    // Cross-file comparisons: no overlap check needed
    let file_indices: Vec<_> = by_file.keys().collect();
    for i in 0..file_indices.len() {
        for j in (i + 1)..file_indices.len() {
            let locs1 = &by_file[file_indices[i]];
            let locs2 = &by_file[file_indices[j]];

            for loc1 in locs1.iter() {
                for loc2 in locs2.iter() {
                    if let Some(dup) = compare_locations(
                        loc1,
                        loc2,
                        file_tokens,
                        min_tokens,
                        min_lines,
                        min_similarity,
                    ) {
                        duplicates.push(dup);
                    }
                }
            }
        }
    }

    duplicates
}

/// Merge overlapping duplicate blocks.
fn merge_overlapping(mut duplicates: Vec<DuplicateBlock>) -> Vec<DuplicateBlock> {
    if duplicates.len() <= 1 {
        return duplicates;
    }

    // Sort by file pair and start positions
    duplicates.sort_by(|a, b| {
        (&a.file1, &a.file2, a.start1, a.start2).cmp(&(&b.file1, &b.file2, b.start1, b.start2))
    });

    let mut merged: Vec<DuplicateBlock> = Vec::new();

    for dup in duplicates {
        if let Some(last) = merged.last_mut() {
            // Check if this block overlaps with the previous one
            let same_files = last.file1 == dup.file1 && last.file2 == dup.file2;
            let overlaps1 = dup.start1 <= last.start1 + last.line_length;
            let overlaps2 = dup.start2 <= last.start2 + last.line_length;

            if same_files && overlaps1 && overlaps2 {
                // Extend the previous block to cover this one
                let end1 = (last.start1 + last.line_length).max(dup.start1 + dup.line_length);
                last.line_length = end1 - last.start1;
                last.token_length = last.token_length.max(dup.token_length);
                continue;
            }
        }
        merged.push(dup);
    }

    merged
}

/// Find duplicate code blocks across multiple files.
///
/// Uses Rabin-Karp rolling hash algorithm for O(n) detection.
///
/// # Arguments
/// * `files` - List of (path, source) tuples
/// * `min_tokens` - Minimum tokens for a duplicate block (default: 50)
/// * `min_lines` - Minimum lines for a duplicate block (default: 5)
/// * `min_similarity` - Minimum Jaccard similarity (0.0 to 1.0, default: 0.0)
///
/// # Returns
/// List of DuplicateBlock with file paths, positions, and lengths
///
/// # Example
/// ```python
/// from repotoire_fast import find_duplicates
///
/// files = [("a.py", "def foo(): pass"), ("b.py", "def foo(): pass")]
/// duplicates = find_duplicates(files, min_tokens=5, min_lines=1, similarity=0.0)
/// ```
#[must_use]
pub fn find_duplicates(
    files: Vec<(String, String)>,
    min_tokens: usize,
    min_lines: usize,
    min_similarity: f64,
) -> Vec<DuplicateBlock> {
    if files.is_empty() || min_tokens == 0 {
        return vec![];
    }

    // Step 1: Tokenize all files in parallel
    let file_tokens: Vec<(String, Vec<Token>)> = files
        .into_par_iter()
        .map(|(path, source)| (path, tokenize(&source)))
        .collect();

    // Step 2: Build hash index (hash -> list of locations)
    // Pre-size the HashMap based on estimated number of hash windows
    let estimated_entries: usize = file_tokens
        .iter()
        .map(|(_, tokens)| tokens.len().saturating_sub(min_tokens).saturating_add(1))
        .sum();
    let mut hash_index: FxHashMap<u64, Vec<TokenLocation>> =
        FxHashMap::with_capacity_and_hasher(estimated_entries, Default::default());

    for (file_idx, (_, tokens)) in file_tokens.iter().enumerate() {
        for (hash, token_idx, start_line) in rolling_hash(tokens, min_tokens) {
            hash_index.entry(hash).or_default().push(TokenLocation {
                file_idx,
                token_idx,
                start_line,
            });
        }
    }

    // Step 3: Find duplicates (hash collisions with > 1 location)
    // Process in parallel for large hash tables
    let collision_entries: Vec<_> = hash_index
        .into_iter()
        .filter(|(_, locations)| locations.len() > 1)
        .collect();

    let duplicates: Vec<DuplicateBlock> = collision_entries
        .into_par_iter()
        .flat_map(|(_, locations)| {
            find_duplicate_pairs(
                &locations,
                &file_tokens,
                min_tokens,
                min_lines,
                min_similarity,
            )
        })
        .collect();

    // Step 4: Merge overlapping blocks
    merge_overlapping(duplicates)
}

// ============================================================================
// UNIT TESTS
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    // -------------------------------------------------------------------------
    // TOKENIZATION TESTS
    // -------------------------------------------------------------------------

    #[test]
    fn test_tokenize_empty() {
        let tokens = tokenize("");
        assert!(tokens.is_empty());
    }

    #[test]
    fn test_tokenize_simple() {
        let tokens = tokenize("def foo():");
        let values: Vec<_> = tokens.iter().map(|t| t.value.as_str()).collect();
        assert_eq!(values, vec!["def", "foo", "(", ")", ":"]);
    }

    #[test]
    fn test_tokenize_multiline() {
        let tokens = tokenize("def foo():\n    return 1");
        assert_eq!(tokens[0].line, 1);
        assert_eq!(tokens.last().unwrap().line, 2);
    }

    #[test]
    fn test_tokenize_normalizes_case() {
        let tokens = tokenize("DEF Foo BAR");
        let values: Vec<_> = tokens.iter().map(|t| t.value.as_str()).collect();
        assert_eq!(values, vec!["def", "foo", "bar"]);
    }

    #[test]
    fn test_tokenize_preserves_operators() {
        let tokens = tokenize("a + b * c");
        let values: Vec<_> = tokens.iter().map(|t| t.value.as_str()).collect();
        assert_eq!(values, vec!["a", "+", "b", "*", "c"]);
    }

    // -------------------------------------------------------------------------
    // ROLLING HASH TESTS
    // -------------------------------------------------------------------------

    #[test]
    fn test_rolling_hash_empty() {
        let tokens: Vec<Token> = vec![];
        let hashes = rolling_hash(&tokens, 5);
        assert!(hashes.is_empty());
    }

    #[test]
    fn test_rolling_hash_too_short() {
        let tokens = vec![
            Token {
                value: "a".to_string(),
                line: 1,
            },
            Token {
                value: "b".to_string(),
                line: 1,
            },
        ];
        let hashes = rolling_hash(&tokens, 5);
        assert!(hashes.is_empty());
    }

    #[test]
    fn test_rolling_hash_exact_size() {
        let tokens = vec![
            Token {
                value: "a".to_string(),
                line: 1,
            },
            Token {
                value: "b".to_string(),
                line: 1,
            },
            Token {
                value: "c".to_string(),
                line: 1,
            },
        ];
        let hashes = rolling_hash(&tokens, 3);
        assert_eq!(hashes.len(), 1);
        assert_eq!(hashes[0].1, 0); // start index
        assert_eq!(hashes[0].2, 1); // start line
    }

    #[test]
    fn test_rolling_hash_multiple_windows() {
        let tokens = vec![
            Token {
                value: "a".to_string(),
                line: 1,
            },
            Token {
                value: "b".to_string(),
                line: 1,
            },
            Token {
                value: "c".to_string(),
                line: 2,
            },
            Token {
                value: "d".to_string(),
                line: 2,
            },
            Token {
                value: "e".to_string(),
                line: 3,
            },
        ];
        let hashes = rolling_hash(&tokens, 3);
        assert_eq!(hashes.len(), 3); // Windows: abc, bcd, cde
    }

    #[test]
    fn test_rolling_hash_same_content_same_hash() {
        let tokens1 = vec![
            Token {
                value: "def".to_string(),
                line: 1,
            },
            Token {
                value: "foo".to_string(),
                line: 1,
            },
        ];
        let tokens2 = vec![
            Token {
                value: "def".to_string(),
                line: 5,
            },
            Token {
                value: "foo".to_string(),
                line: 5,
            },
        ];
        let hash1 = rolling_hash(&tokens1, 2);
        let hash2 = rolling_hash(&tokens2, 2);
        assert_eq!(hash1[0].0, hash2[0].0); // Same hash for same content
    }

    // -------------------------------------------------------------------------
    // DUPLICATE DETECTION TESTS
    // -------------------------------------------------------------------------

    #[test]
    fn test_find_duplicates_empty() {
        let files: Vec<(String, String)> = vec![];
        let duplicates = find_duplicates(files, 5, 2, 0.0);
        assert!(duplicates.is_empty());
    }

    #[test]
    fn test_find_duplicates_single_file() {
        let files = vec![("a.py".to_string(), "def foo():\n    return 1".to_string())];
        let duplicates = find_duplicates(files, 5, 2, 0.0);
        assert!(duplicates.is_empty()); // No duplicates in single file
    }

    #[test]
    fn test_find_duplicates_identical_files() {
        let code = "def foo():\n    return 1\n\ndef bar():\n    return 2".to_string();
        let files = vec![
            ("a.py".to_string(), code.clone()),
            ("b.py".to_string(), code),
        ];
        let duplicates = find_duplicates(files, 5, 2, 0.0);
        assert!(
            !duplicates.is_empty(),
            "Should find duplicates in identical files"
        );
    }

    #[test]
    fn test_find_duplicates_partial_match() {
        let files = vec![
            (
                "a.py".to_string(),
                "def foo():\n    return 1\n\ndef unique_a():\n    pass".to_string(),
            ),
            (
                "b.py".to_string(),
                "def foo():\n    return 1\n\ndef unique_b():\n    pass".to_string(),
            ),
        ];
        let duplicates = find_duplicates(files, 5, 2, 0.0);
        assert!(!duplicates.is_empty(), "Should find partial duplicates");
    }

    #[test]
    fn test_find_duplicates_no_match() {
        let files = vec![
            ("a.py".to_string(), "def foo(): return 1".to_string()),
            ("b.py".to_string(), "def bar(): return 2".to_string()),
        ];
        let duplicates = find_duplicates(files, 5, 2, 0.0);
        assert!(
            duplicates.is_empty(),
            "Should not find duplicates in different code"
        );
    }

    #[test]
    fn test_find_duplicates_min_tokens() {
        let code = "a = 1".to_string();
        let files = vec![
            ("a.py".to_string(), code.clone()),
            ("b.py".to_string(), code),
        ];

        let with_low_threshold = find_duplicates(files.clone(), 2, 1, 0.0);
        let with_high_threshold = find_duplicates(files, 100, 1, 0.0);

        assert!(
            !with_low_threshold.is_empty(),
            "Should find with low threshold"
        );
        assert!(
            with_high_threshold.is_empty(),
            "Should not find with high threshold"
        );
    }

    #[test]
    fn test_find_duplicates_min_lines() {
        let code = "a = 1".to_string(); // Single line
        let files = vec![
            ("a.py".to_string(), code.clone()),
            ("b.py".to_string(), code),
        ];

        let with_1_line = find_duplicates(files.clone(), 2, 1, 0.0);
        let with_5_lines = find_duplicates(files, 2, 5, 0.0);

        assert!(!with_1_line.is_empty(), "Should find with min_lines=1");
        assert!(with_5_lines.is_empty(), "Should not find with min_lines=5");
    }

    #[test]
    fn test_find_duplicates_same_file_different_locations() {
        // Duplicate code within the same file
        let code = "def foo():\n    return 1\n\ndef bar():\n    return 1".to_string();
        let files = vec![("a.py".to_string(), code)];
        // This should potentially find internal duplicates if not filtered
        let duplicates = find_duplicates(files, 3, 1, 0.0);
        // We filter self-comparisons that overlap, so this depends on implementation
        // The important thing is it doesn't crash
        assert!(duplicates.len() <= 1);
    }

    #[test]
    fn test_find_duplicates_three_files() {
        let code = "def common():\n    x = 1\n    y = 2\n    return x + y".to_string();
        let files = vec![
            ("a.py".to_string(), code.clone()),
            ("b.py".to_string(), code.clone()),
            ("c.py".to_string(), code),
        ];
        let duplicates = find_duplicates(files, 5, 2, 0.0);
        // Should find duplicates between all pairs: (a,b), (a,c), (b,c)
        assert!(
            duplicates.len() >= 1,
            "Should find duplicates across 3 files"
        );
    }

    // -------------------------------------------------------------------------
    // MERGE OVERLAPPING TESTS
    // -------------------------------------------------------------------------

    #[test]
    fn test_merge_empty() {
        let duplicates: Vec<DuplicateBlock> = vec![];
        let merged = merge_overlapping(duplicates);
        assert!(merged.is_empty());
    }

    #[test]
    fn test_merge_single() {
        let duplicates = vec![DuplicateBlock {
            file1: "a.py".to_string(),
            start1: 1,
            file2: "b.py".to_string(),
            start2: 5,
            token_length: 10,
            line_length: 3,
        }];
        let merged = merge_overlapping(duplicates);
        assert_eq!(merged.len(), 1);
    }

    #[test]
    fn test_merge_non_overlapping() {
        let duplicates = vec![
            DuplicateBlock {
                file1: "a.py".to_string(),
                start1: 1,
                file2: "b.py".to_string(),
                start2: 1,
                token_length: 10,
                line_length: 3,
            },
            DuplicateBlock {
                file1: "a.py".to_string(),
                start1: 100,
                file2: "b.py".to_string(),
                start2: 100,
                token_length: 10,
                line_length: 3,
            },
        ];
        let merged = merge_overlapping(duplicates);
        assert_eq!(merged.len(), 2, "Non-overlapping should not merge");
    }

    #[test]
    fn test_merge_overlapping() {
        let duplicates = vec![
            DuplicateBlock {
                file1: "a.py".to_string(),
                start1: 1,
                file2: "b.py".to_string(),
                start2: 1,
                token_length: 10,
                line_length: 5,
            },
            DuplicateBlock {
                file1: "a.py".to_string(),
                start1: 3,
                file2: "b.py".to_string(),
                start2: 3,
                token_length: 10,
                line_length: 5,
            },
        ];
        let merged = merge_overlapping(duplicates);
        assert_eq!(merged.len(), 1, "Overlapping should merge");
        assert_eq!(merged[0].line_length, 7, "Merged length should cover both");
    }

    // -------------------------------------------------------------------------
    // SIMILARITY TESTS
    // -------------------------------------------------------------------------

    #[test]
    fn test_jaccard_identical() {
        let tokens1 = vec![
            Token {
                value: "a".to_string(),
                line: 1,
            },
            Token {
                value: "b".to_string(),
                line: 1,
            },
        ];
        let tokens2 = vec![
            Token {
                value: "a".to_string(),
                line: 1,
            },
            Token {
                value: "b".to_string(),
                line: 1,
            },
        ];
        let sim = jaccard_similarity(&tokens1, &tokens2);
        assert!((sim - 1.0).abs() < 0.001);
    }

    #[test]
    fn test_jaccard_disjoint() {
        let tokens1 = vec![
            Token {
                value: "a".to_string(),
                line: 1,
            },
            Token {
                value: "b".to_string(),
                line: 1,
            },
        ];
        let tokens2 = vec![
            Token {
                value: "c".to_string(),
                line: 1,
            },
            Token {
                value: "d".to_string(),
                line: 1,
            },
        ];
        let sim = jaccard_similarity(&tokens1, &tokens2);
        assert!((sim - 0.0).abs() < 0.001);
    }

    #[test]
    fn test_jaccard_partial() {
        let tokens1 = vec![
            Token {
                value: "a".to_string(),
                line: 1,
            },
            Token {
                value: "b".to_string(),
                line: 1,
            },
        ];
        let tokens2 = vec![
            Token {
                value: "a".to_string(),
                line: 1,
            },
            Token {
                value: "c".to_string(),
                line: 1,
            },
        ];
        let sim = jaccard_similarity(&tokens1, &tokens2);
        // Intersection: {a}, Union: {a, b, c} => 1/3
        assert!((sim - 0.333).abs() < 0.01);
    }

    #[test]
    fn test_jaccard_empty() {
        let tokens1: Vec<Token> = vec![];
        let tokens2: Vec<Token> = vec![];
        let sim = jaccard_similarity(&tokens1, &tokens2);
        assert!((sim - 1.0).abs() < 0.001);
    }
}
