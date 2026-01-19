//! Fast parallel data clump detection (REPO-404)
//!
//! Replaces Python's O(C(n,k)^2) combination generation with parallel Rust implementation.
//! Uses bitsets for efficient subset operations and rayon for parallelization.

use rayon::prelude::*;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::HashSet;

/// Result of data clump detection
#[derive(Debug, Clone)]
pub struct DataClump {
    /// Set of parameter names that form the clump
    pub params: FxHashSet<String>,
    /// Set of function names that share this clump
    pub functions: FxHashSet<String>,
}

/// Find data clumps in function parameters using parallel processing.
///
/// A data clump is a set of parameters that appear together in multiple functions,
/// suggesting they should be grouped into a class/struct.
///
/// # Arguments
/// * `functions_params` - Vec of (function_name, parameter_names)
/// * `min_params` - Minimum number of parameters to consider a clump (typically 3)
/// * `min_occurrences` - Minimum number of functions sharing the clump (typically 2)
///
/// # Returns
/// Vec of (param_set, function_set) tuples representing detected clumps
pub fn find_clumps_fast(
    functions_params: Vec<(String, Vec<String>)>,
    min_params: usize,
    min_occurrences: usize,
) -> Vec<(FxHashSet<String>, FxHashSet<String>)> {
    if functions_params.is_empty() || min_params == 0 {
        return Vec::new();
    }

    // Phase 1: Generate all combinations in parallel
    // Each function generates its own param combinations independently
    let all_combinations: Vec<(FxHashSet<String>, String)> = functions_params
        .par_iter()
        .flat_map(|(func_name, params)| {
            let mut combos = Vec::new();
            let param_list: Vec<&String> = params.iter().collect();
            let n = param_list.len();

            // Generate combinations of size min_params to n
            for size in min_params..=n {
                for combo in combinations(&param_list, size) {
                    let param_set: FxHashSet<String> = combo.into_iter().cloned().collect();
                    combos.push((param_set, func_name.clone()));
                }
            }
            combos
        })
        .collect();

    // Phase 2: Group by param set (sequential - needs mutable map)
    let mut param_to_functions: FxHashMap<Vec<String>, FxHashSet<String>> = FxHashMap::default();
    for (param_set, func_name) in all_combinations {
        // Use sorted vec as key for deterministic hashing
        let mut key: Vec<String> = param_set.into_iter().collect();
        key.sort();
        param_to_functions
            .entry(key)
            .or_default()
            .insert(func_name);
    }

    // Phase 3: Filter by minimum occurrences
    let clumps: Vec<(FxHashSet<String>, FxHashSet<String>)> = param_to_functions
        .into_iter()
        .filter(|(_, funcs)| funcs.len() >= min_occurrences)
        .map(|(params, funcs)| {
            let param_set: FxHashSet<String> = params.into_iter().collect();
            (param_set, funcs)
        })
        .collect();

    // Phase 4: Remove subsets (parallel subset checking)
    remove_subsets_parallel(clumps)
}

/// Generate all combinations of size k from items
fn combinations<T: Clone>(items: &[T], k: usize) -> Vec<Vec<T>> {
    if k == 0 {
        return vec![vec![]];
    }
    if items.len() < k {
        return vec![];
    }

    let mut result = Vec::new();
    let first = items[0].clone();
    let rest = &items[1..];

    // Combinations including first element
    for mut combo in combinations(rest, k - 1) {
        combo.insert(0, first.clone());
        result.push(combo);
    }

    // Combinations excluding first element
    result.extend(combinations(rest, k));
    result
}

/// Remove subsets in parallel using chunked comparison
fn remove_subsets_parallel(
    clumps: Vec<(FxHashSet<String>, FxHashSet<String>)>,
) -> Vec<(FxHashSet<String>, FxHashSet<String>)> {
    if clumps.len() <= 1 {
        return clumps;
    }

    // Sort by param set size (largest first)
    let mut sorted: Vec<_> = clumps;
    sorted.sort_by(|a, b| b.0.len().cmp(&a.0.len()));

    // For each clump, check if it's a subset of any larger clump
    let keep_indices: FxHashSet<usize> = (0..sorted.len())
        .into_par_iter()
        .filter(|&i| {
            let (param_set, functions) = &sorted[i];

            // Check against all larger clumps (those before this one in sorted order)
            for j in 0..i {
                let (larger_params, larger_funcs) = &sorted[j];

                // If this param set is a strict subset and functions are a subset
                if param_set.len() < larger_params.len()
                    && is_subset(param_set, larger_params)
                    && is_subset(functions, larger_funcs)
                {
                    return false; // This is a redundant subset
                }
            }
            true
        })
        .collect();

    // Collect non-subset clumps
    sorted
        .into_iter()
        .enumerate()
        .filter(|(i, _)| keep_indices.contains(i))
        .map(|(_, clump)| clump)
        .collect()
}

/// Check if a is a subset of b
#[inline]
fn is_subset<T: std::hash::Hash + Eq>(a: &FxHashSet<T>, b: &FxHashSet<T>) -> bool {
    a.iter().all(|item| b.contains(item))
}

/// Convert Python-friendly input to internal format and back
pub fn find_clumps_py(
    functions_params: Vec<(String, Vec<String>)>,
    min_params: usize,
    min_occurrences: usize,
) -> Vec<(HashSet<String>, HashSet<String>)> {
    find_clumps_fast(functions_params, min_params, min_occurrences)
        .into_iter()
        .map(|(params, funcs)| {
            (
                params.into_iter().collect(),
                funcs.into_iter().collect(),
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_find_clumps_basic() {
        let functions = vec![
            ("func1".to_string(), vec!["a".to_string(), "b".to_string(), "c".to_string()]),
            ("func2".to_string(), vec!["a".to_string(), "b".to_string(), "c".to_string()]),
            ("func3".to_string(), vec!["x".to_string(), "y".to_string()]),
        ];

        let clumps = find_clumps_fast(functions, 2, 2);
        assert!(!clumps.is_empty());

        // Should find {a, b, c} shared by func1 and func2
        let found = clumps.iter().any(|(params, funcs)| {
            params.contains("a") && params.contains("b") && params.contains("c")
                && funcs.contains("func1") && funcs.contains("func2")
        });
        assert!(found);
    }

    #[test]
    fn test_find_clumps_empty() {
        let functions: Vec<(String, Vec<String>)> = vec![];
        let clumps = find_clumps_fast(functions, 2, 2);
        assert!(clumps.is_empty());
    }

    #[test]
    fn test_subset_removal() {
        // If {a, b, c} is found in func1, func2
        // Then {a, b} should be removed as a subset
        let functions = vec![
            ("func1".to_string(), vec!["a".to_string(), "b".to_string(), "c".to_string(), "d".to_string()]),
            ("func2".to_string(), vec!["a".to_string(), "b".to_string(), "c".to_string(), "d".to_string()]),
        ];

        let clumps = find_clumps_fast(functions, 2, 2);

        // Should only have the largest clump, not subsets
        assert_eq!(clumps.len(), 1);
        assert_eq!(clumps[0].0.len(), 4); // {a, b, c, d}
    }

    #[test]
    fn test_combinations() {
        let items = vec!["a", "b", "c"];
        let combos = combinations(&items, 2);
        assert_eq!(combos.len(), 3); // (a,b), (a,c), (b,c)
    }
}
