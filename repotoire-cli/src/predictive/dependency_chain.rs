//! L1.5: Dependency-chain surprisal.
//!
//! Instead of scoring source lines in isolation (L1), this module concatenates
//! code along **dependency graph paths** (call edges) and scores the combined
//! sequence through the n-gram model. A function that looks perfectly natural
//! on its own but uses unusual patterns relative to its callers/callees will
//! receive a high chain surprisal score.
//!
//! Reference: Yang et al., "Dependency-Aware Code Naturalness" (OOPSLA 2024, DAN)

use crate::calibrate::NgramModel;
use std::collections::{HashMap, HashSet};

/// Extract dependency chains up to `max_depth` from call edges.
///
/// Each chain is a sequence of qualified names connected by call edges,
/// discovered via acyclic DFS from every call-edge source. Chains shorter
/// than 2 nodes are discarded (a single node has no dependency context).
///
/// Cycles are broken by refusing to revisit a node already on the current
/// path — this guarantees termination without needing a separate visited set.
pub fn extract_dependency_chains(calls: &[(String, String)], max_depth: usize) -> Vec<Vec<String>> {
    extract_dependency_chains_bounded(calls, max_depth, usize::MAX)
}

/// Extract dependency chains with an upper bound on total chains produced.
///
/// Identical to `extract_dependency_chains` but stops early once `max_chains`
/// chains have been collected. This prevents combinatorial explosion on large
/// call graphs (e.g. CPython's 72k functions).
pub fn extract_dependency_chains_bounded(
    calls: &[(String, String)],
    max_depth: usize,
    max_chains: usize,
) -> Vec<Vec<String>> {
    // Build adjacency list from call edges.
    let mut adj: HashMap<&str, Vec<&str>> = HashMap::new();
    for (caller, callee) in calls {
        adj.entry(caller.as_str())
            .or_default()
            .push(callee.as_str());
    }

    // Deduplicate start nodes — starting DFS from the same node multiple times
    // (once per outgoing edge) is redundant.
    let unique_starts: Vec<&str> = {
        let mut seen = HashSet::new();
        calls
            .iter()
            .filter_map(|(start, _)| {
                if seen.insert(start.as_str()) {
                    Some(start.as_str())
                } else {
                    None
                }
            })
            .collect()
    };

    let mut chains = Vec::new();

    for start in unique_starts {
        if chains.len() >= max_chains {
            break;
        }

        let mut initial_visited = HashSet::new();
        initial_visited.insert(start.to_string());
        let mut stack: Vec<(Vec<String>, HashSet<String>)> =
            vec![(vec![start.to_string()], initial_visited)];

        while let Some((chain, visited)) = stack.pop() {
            if chains.len() >= max_chains {
                break;
            }

            // If we've reached the maximum depth, emit the chain as-is.
            if chain.len() >= max_depth {
                chains.push(chain);
                continue;
            }

            let last = chain.last().expect("chain is non-empty by construction");
            let neighbors = adj.get(last.as_str());
            let mut extended = false;

            if let Some(nbrs) = neighbors {
                for nbr in nbrs {
                    // Cycle avoidance: O(1) HashSet lookup instead of O(n) Vec scan.
                    let nbr_string = nbr.to_string();
                    if !visited.contains(&nbr_string) {
                        let mut new_chain = chain.clone();
                        new_chain.push(nbr_string.clone());
                        let mut new_visited = visited.clone();
                        new_visited.insert(nbr_string);
                        stack.push((new_chain, new_visited));
                        extended = true;
                    }
                }
            }

            // Emit leaf chains (no further extensions) that have at least 2 nodes.
            if !extended && chain.len() >= 2 {
                chains.push(chain);
            }
        }
    }

    chains
}

/// Compute surprisal of a chain's concatenated token sequences.
///
/// The lines from each function in the chain are tokenized, joined with
/// `<EOL>` markers, and scored as a single contiguous sequence through the
/// n-gram model. Returns `0.0` if the model lacks confidence or the input
/// is empty.
pub fn chain_surprisal(model: &NgramModel, chain_lines: &[&str]) -> f64 {
    if !model.is_confident() || chain_lines.is_empty() {
        return 0.0;
    }

    let mut tokens = Vec::new();
    for line in chain_lines {
        let line_tokens = NgramModel::tokenize_line(line);
        if !line_tokens.is_empty() {
            tokens.extend(line_tokens);
            tokens.push("<EOL>".to_string());
        }
    }

    model.surprisal(&tokens)
}

/// Tracks the maximum chain surprisal per function.
///
/// Each function may appear in multiple dependency chains. The scorer keeps
/// the **maximum** surprisal seen across all chains containing a given
/// function, which captures the worst-case dependency-context anomaly.
pub struct DependencyChainScorer {
    pub scores: HashMap<String, f64>,
}

impl DependencyChainScorer {
    pub fn new() -> Self {
        Self {
            scores: HashMap::new(),
        }
    }

    /// Record a chain's surprisal for every function in the chain.
    ///
    /// Only updates a function's score if the new surprisal exceeds the
    /// previously recorded maximum.
    pub fn record_chain(&mut self, chain_qns: &[String], surprisal: f64) {
        for qn in chain_qns {
            let entry = self.scores.entry(qn.clone()).or_insert(0.0);
            if surprisal > *entry {
                *entry = surprisal;
            }
        }
    }

    /// Retrieve the maximum chain surprisal for a function.
    ///
    /// Returns `0.0` for functions that were never part of any recorded chain.
    pub fn score(&self, function_qn: &str) -> f64 {
        self.scores.get(function_qn).copied().unwrap_or(0.0)
    }
}

impl Default for DependencyChainScorer {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: build a confident `NgramModel` by training on repetitive
    /// Rust-like token patterns until the 5 000-token threshold is crossed.
    fn trained_model() -> NgramModel {
        let mut model = NgramModel::new();
        // Each iteration contributes 7 tokens; 800 * 7 = 5 600 > 5 000.
        for _ in 0..800 {
            model.train_on_tokens(&[
                "let".to_string(),
                "mut".to_string(),
                "<ID>".to_string(),
                "=".to_string(),
                "<NUM>".to_string(),
                ";".to_string(),
                "<EOL>".to_string(),
            ]);
        }
        assert!(
            model.is_confident(),
            "Model should be confident after training"
        );
        model
    }

    #[test]
    fn test_extract_dependency_chains() {
        // A -> B -> C linear call chain.
        let calls = vec![
            ("A".to_string(), "B".to_string()),
            ("B".to_string(), "C".to_string()),
        ];

        let chains = extract_dependency_chains(&calls, 4);

        // The full chain [A, B, C] should be present.
        let has_abc = chains.iter().any(|c| c == &["A", "B", "C"]);
        assert!(
            has_abc,
            "Expected chain [A, B, C] in extracted chains: {:?}",
            chains
        );

        // Shorter sub-chain [B, C] should also be extracted (starting from B).
        let has_bc = chains.iter().any(|c| c == &["B", "C"]);
        assert!(
            has_bc,
            "Expected chain [B, C] in extracted chains: {:?}",
            chains
        );
    }

    #[test]
    fn test_extract_chains_handles_cycles() {
        // A -> B -> A: cycle must not cause infinite loop.
        let calls = vec![
            ("A".to_string(), "B".to_string()),
            ("B".to_string(), "A".to_string()),
        ];

        let chains = extract_dependency_chains(&calls, 10);

        // Should terminate and produce finite chains.
        assert!(
            !chains.is_empty(),
            "Should extract at least one chain from a cycle"
        );

        // No chain should contain a repeated node (cycle avoidance).
        for chain in &chains {
            let mut seen = std::collections::HashSet::new();
            for node in chain {
                assert!(
                    seen.insert(node),
                    "Chain contains duplicate node {:?}: {:?}",
                    node,
                    chain
                );
            }
        }
    }

    #[test]
    fn test_chain_surprisal_computation() {
        let model = trained_model();

        let lines = &["let mut count = 0;", "let mut total = 42;"];
        let score = chain_surprisal(&model, lines);
        assert!(
            score >= 0.0,
            "Chain surprisal should be non-negative, got {}",
            score
        );
    }

    #[test]
    fn test_chain_surprisal_zero_without_confidence() {
        let model = NgramModel::new(); // empty, not confident
        assert!(!model.is_confident());

        let lines = &["let x = 1;", "let y = 2;"];
        let score = chain_surprisal(&model, lines);
        assert_eq!(
            score, 0.0,
            "Unconfident model should return 0.0, got {}",
            score
        );
    }

    #[test]
    fn test_dependency_chain_scorer_max() {
        let mut scorer = DependencyChainScorer::new();

        let chain_a = vec!["foo".to_string(), "bar".to_string()];
        let chain_b = vec!["foo".to_string(), "baz".to_string()];

        // Record two chains for "foo" with different surprisal values.
        scorer.record_chain(&chain_a, 3.5);
        scorer.record_chain(&chain_b, 7.2);

        // "foo" should keep the maximum.
        assert!(
            (scorer.score("foo") - 7.2).abs() < f64::EPSILON,
            "Expected max surprisal 7.2 for foo, got {}",
            scorer.score("foo")
        );

        // "bar" only appeared in the first chain.
        assert!(
            (scorer.score("bar") - 3.5).abs() < f64::EPSILON,
            "Expected surprisal 3.5 for bar, got {}",
            scorer.score("bar")
        );

        // "baz" only appeared in the second chain.
        assert!(
            (scorer.score("baz") - 7.2).abs() < f64::EPSILON,
            "Expected surprisal 7.2 for baz, got {}",
            scorer.score("baz")
        );
    }

    #[test]
    fn test_score_missing_function() {
        let scorer = DependencyChainScorer::new();
        assert_eq!(
            scorer.score("nonexistent::function"),
            0.0,
            "Unknown function should return 0.0"
        );
    }
}
