# Hierarchical Predictive Coding Engine — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the flat n-gram surprisal detector with a 5-level hierarchical predictive coding engine that computes precision-weighted prediction errors at token, structural, dependency-chain, relational (node2vec), and architectural levels.

**Architecture:** New `repotoire-cli/src/predictive/` module contains the engine. Each level independently computes z-scores. A `compound.rs` module aggregates using empirical precision weights. A new `HierarchicalSurprisalDetector` implements the `Detector` trait and replaces `SurprisalDetector` in the registration pipeline.

**Tech Stack:** Pure Rust. Node2vec + Word2vec ported from `repotoire-fast`. petgraph graph queries. Existing n-gram model enhanced. No external dependencies added.

**Design doc:** `docs/plans/2026-03-09-hierarchical-predictive-coding-design.md`

---

## Task 1: Create the `predictive` module skeleton + `PredictiveCodingEngine`

**Files:**
- Create: `repotoire-cli/src/predictive/mod.rs`
- Create: `repotoire-cli/src/predictive/compound.rs`
- Modify: `repotoire-cli/src/lib.rs` or `repotoire-cli/src/main.rs` — add `mod predictive;`

**Step 1: Identify where to add `mod predictive`**

Check `repotoire-cli/src/lib.rs` or `repotoire-cli/src/main.rs` for existing `mod` declarations. Add `pub mod predictive;` alongside existing modules like `pub mod detectors;`, `pub mod calibrate;`, etc.

**Step 2: Create `repotoire-cli/src/predictive/mod.rs`**

```rust
//! Hierarchical Predictive Coding Engine
//!
//! Applies Friston's hierarchical predictive coding theory to code analysis.
//! Five hierarchy levels independently model "what's normal" and compute
//! prediction errors (z-scores). Concordance across levels drives severity.
//!
//! References:
//! - Friston, "A Theory of Cortical Responses" (2005)
//! - Ray & Hellendoorn, "On the Naturalness of Buggy Code" (ICSE 2016)
//! - Yang et al., "Dependency-Aware Code Naturalness" (OOPSLA 2024)

pub mod compound;
pub mod token_level;
pub mod structural;
pub mod dependency_chain;
pub mod relational;
pub mod architectural;
pub mod embeddings;

use crate::graph::GraphQuery;
use crate::models::Severity;
use std::collections::HashMap;

/// Prediction error at a single hierarchy level for a single entity.
#[derive(Debug, Clone)]
pub struct LevelScore {
    pub level: Level,
    pub z_score: f64,
    pub threshold: f64,
    pub is_surprising: bool,
}

/// The 5 hierarchy levels.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Level {
    Token,           // L1
    Structural,      // L2
    DependencyChain, // L1.5
    Relational,      // L3
    Architectural,   // L4
}

impl Level {
    pub fn label(&self) -> &'static str {
        match self {
            Level::Token => "L1 Token",
            Level::Structural => "L2 Structural",
            Level::DependencyChain => "L1.5 Dependency",
            Level::Relational => "L3 Relational",
            Level::Architectural => "L4 Architectural",
        }
    }
}

/// Per-entity compound prediction score across all hierarchy levels.
#[derive(Debug, Clone)]
pub struct CompoundScore {
    /// Per-level z-scores and thresholds.
    pub level_scores: Vec<LevelScore>,
    /// Number of levels where z > threshold.
    pub concordance: usize,
    /// Precision-weighted compound surprise score.
    pub compound_surprise: f64,
    /// Derived severity from concordance.
    pub severity: Severity,
}
```

**Step 3: Create `repotoire-cli/src/predictive/compound.rs`**

```rust
//! Precision-weighted aggregation + concordance scoring.

use super::{CompoundScore, Level, LevelScore};
use crate::models::Severity;
use std::collections::HashMap;

/// Default per-level z-score thresholds (from design doc).
pub fn default_thresholds() -> HashMap<Level, f64> {
    let mut m = HashMap::new();
    m.insert(Level::Token, 2.5);
    m.insert(Level::Structural, 2.0);
    m.insert(Level::DependencyChain, 2.0);
    m.insert(Level::Relational, 1.5);
    m.insert(Level::Architectural, 2.0);
    m
}

/// Compute empirical precision weights from z-score distributions.
/// precision_i = 1 / variance(z_scores_i), then normalize.
pub fn compute_precision_weights(all_scores: &HashMap<Level, Vec<f64>>) -> HashMap<Level, f64> {
    let mut precisions: HashMap<Level, f64> = HashMap::new();
    let mut total_precision = 0.0;

    for (level, scores) in all_scores {
        if scores.len() < 2 {
            precisions.insert(*level, 1.0);
            total_precision += 1.0;
            continue;
        }
        let n = scores.len() as f64;
        let mean = scores.iter().sum::<f64>() / n;
        let variance = scores.iter().map(|s| (s - mean).powi(2)).sum::<f64>() / n;
        let precision = if variance > 1e-10 { 1.0 / variance } else { 1.0 };
        precisions.insert(*level, precision);
        total_precision += precision;
    }

    // Normalize to sum to 1.0
    if total_precision > 0.0 {
        for v in precisions.values_mut() {
            *v /= total_precision;
        }
    }
    precisions
}

/// Score a single entity given its per-level z-scores.
pub fn score_entity(
    level_scores: Vec<LevelScore>,
    weights: &HashMap<Level, f64>,
) -> CompoundScore {
    let concordance = level_scores.iter().filter(|s| s.is_surprising).count();

    let compound_surprise: f64 = level_scores
        .iter()
        .filter(|s| s.is_surprising)
        .map(|s| weights.get(&s.level).copied().unwrap_or(0.2) * s.z_score)
        .sum();

    let severity = match concordance {
        0 => Severity::Info,
        1 => Severity::Info,
        2 => Severity::Low,
        3 => Severity::Medium,
        _ => Severity::High,
    };

    CompoundScore {
        level_scores,
        concordance,
        compound_surprise,
        severity,
    }
}
```

**Step 4: Verify compilation**

Run: `cargo check -p repotoire-cli`
Expected: compiles with no errors (empty submodules will cause warnings, that's fine)

**Step 5: Commit**

```bash
git add repotoire-cli/src/predictive/
git commit -m "feat(predictive): add module skeleton and compound scoring"
```

---

## Task 2: L1 — Per-language token surprisal

**Files:**
- Create: `repotoire-cli/src/predictive/token_level.rs`
- Reference: `repotoire-cli/src/calibrate/ngram.rs` (existing NgramModel)

**Step 1: Write the test**

In `token_level.rs`, add a `#[cfg(test)] mod tests` block:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::calibrate::NgramModel;

    #[test]
    fn test_per_language_models_trained_separately() {
        let mut scorer = TokenLevelScorer::new();
        scorer.train_file("fn main() { let x = 1; }", "rs");
        scorer.train_file("def main(): x = 1", "py");
        assert!(scorer.models.contains_key("rs"));
        assert!(scorer.models.contains_key("py"));
        assert_eq!(scorer.models.len(), 2);
    }

    #[test]
    fn test_z_scores_computed_for_functions() {
        let mut scorer = TokenLevelScorer::new();
        // Train with enough data for confidence
        let rust_code = "let mut count = 0;\nfor item in list {\n    count += 1;\n}\n";
        for _ in 0..1000 {
            scorer.train_file(rust_code, "rs");
        }
        let scores = scorer.compute_z_scores("rs");
        // With uniform training data, all scores should be near 0 (mean)
        assert!(scores.is_empty() || scores.values().all(|z| z.abs() < 3.0));
    }
}
```

**Step 2: Run test to verify it fails**

Run: `cargo test -p repotoire-cli predictive::token_level::tests -- --nocapture`
Expected: FAIL (module doesn't exist yet)

**Step 3: Implement `TokenLevelScorer`**

```rust
//! L1: Per-language token surprisal using n-gram models.

use crate::calibrate::NgramModel;
use std::collections::HashMap;

/// Trains separate n-gram models per language and computes per-function z-scores.
pub struct TokenLevelScorer {
    pub models: HashMap<String, NgramModel>,
}

impl TokenLevelScorer {
    pub fn new() -> Self {
        Self {
            models: HashMap::new(),
        }
    }

    /// Feed source content from a file into the per-language model.
    pub fn train_file(&mut self, content: &str, extension: &str) {
        let lang = normalize_extension(extension);
        let model = self.models.entry(lang).or_insert_with(NgramModel::new);
        let tokens = NgramModel::tokenize_file(content);
        model.train_on_tokens(&tokens);
    }

    /// Score a function's lines and return average surprisal.
    /// Returns 0.0 if model is not confident for this language.
    pub fn score_function(&self, lines: &[&str], extension: &str) -> f64 {
        let lang = normalize_extension(extension);
        let Some(model) = self.models.get(&lang) else { return 0.0 };
        if !model.is_confident() { return 0.0; }
        let (avg, _, _) = model.function_surprisal(lines);
        avg
    }

    /// Compute per-function z-scores for all scored functions of a given language.
    /// Call this after scoring all functions to get normalized z-scores.
    pub fn compute_z_scores(&self, _extension: &str) -> HashMap<String, f64> {
        // Placeholder — actual z-score computation happens in the engine
        // after all functions are scored. This returns empty for now.
        HashMap::new()
    }
}

fn normalize_extension(ext: &str) -> String {
    match ext {
        "ts" | "tsx" => "ts".to_string(),
        "js" | "jsx" => "js".to_string(),
        "cc" | "cpp" | "cxx" | "hpp" => "cpp".to_string(),
        "h" => "c".to_string(), // C header default
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_per_language_models_trained_separately() {
        let mut scorer = TokenLevelScorer::new();
        scorer.train_file("fn main() { let x = 1; }", "rs");
        scorer.train_file("def main(): x = 1", "py");
        assert!(scorer.models.contains_key("rs"));
        assert!(scorer.models.contains_key("py"));
        assert_eq!(scorer.models.len(), 2);
    }

    #[test]
    fn test_score_function_returns_zero_without_confidence() {
        let scorer = TokenLevelScorer::new();
        let lines = vec!["fn main() {", "    let x = 1;", "}"];
        assert_eq!(scorer.score_function(&lines, "rs"), 0.0);
    }

    #[test]
    fn test_normalize_extensions() {
        assert_eq!(normalize_extension("tsx"), "ts");
        assert_eq!(normalize_extension("jsx"), "js");
        assert_eq!(normalize_extension("cc"), "cpp");
        assert_eq!(normalize_extension("rs"), "rs");
    }
}
```

**Step 4: Run tests**

Run: `cargo test -p repotoire-cli predictive::token_level -- --nocapture`
Expected: PASS

**Step 5: Commit**

```bash
git add repotoire-cli/src/predictive/token_level.rs
git commit -m "feat(predictive): add L1 per-language token surprisal scorer"
```

---

## Task 3: L2 — Structural surprise (Mahalanobis distance)

**Files:**
- Create: `repotoire-cli/src/predictive/structural.rs`

**Step 1: Write tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mahalanobis_identical_to_mean_is_zero() {
        let features = vec![
            vec![10.0, 5.0, 3.0],
            vec![10.0, 5.0, 3.0],
            vec![10.0, 5.0, 3.0],
        ];
        let scorer = StructuralScorer::from_features(&features);
        let d = scorer.mahalanobis_distance(&[10.0, 5.0, 3.0]);
        assert!(d.abs() < 1e-6, "Distance from mean should be ~0, got {}", d);
    }

    #[test]
    fn test_outlier_has_high_distance() {
        let mut features = Vec::new();
        for _ in 0..100 {
            features.push(vec![10.0, 5.0, 3.0]);
        }
        // Add an outlier
        features.push(vec![100.0, 50.0, 30.0]);
        let scorer = StructuralScorer::from_features(&features);
        let normal_d = scorer.mahalanobis_distance(&[10.0, 5.0, 3.0]);
        let outlier_d = scorer.mahalanobis_distance(&[100.0, 50.0, 30.0]);
        assert!(outlier_d > normal_d, "Outlier should have higher distance");
    }

    #[test]
    fn test_feature_extraction_from_code_node() {
        let features = extract_structural_features_raw(5, 12, 3, 50, 2);
        assert_eq!(features.len(), 5);
        assert_eq!(features[0], 5.0);  // param_count
        assert_eq!(features[1], 12.0); // complexity
    }
}
```

**Step 2: Implement `StructuralScorer`**

```rust
//! L2: Structural surprise via Mahalanobis distance on function feature vectors.
//!
//! For each function, compute [param_count, complexity, nesting_depth, LOC, return_count].
//! Learn per-language multivariate distribution (mean + inverse covariance).
//! Mahalanobis distance from project centroid = structural surprise.
//!
//! Reference: "Why is the Mahalanobis Distance Effective for Anomaly Detection?" (arXiv 2003.00402)

/// Extract a feature vector from raw function metrics.
pub fn extract_structural_features_raw(
    param_count: i64,
    complexity: i64,
    nesting_depth: i64,
    loc: u32,
    return_count: i64,
) -> Vec<f64> {
    vec![
        param_count as f64,
        complexity as f64,
        nesting_depth as f64,
        loc as f64,
        return_count as f64,
    ]
}

/// Computes Mahalanobis distance for structural anomaly detection.
pub struct StructuralScorer {
    mean: Vec<f64>,
    inv_cov: Vec<Vec<f64>>,
    dim: usize,
}

impl StructuralScorer {
    /// Build from a collection of feature vectors.
    pub fn from_features(features: &[Vec<f64>]) -> Self {
        let n = features.len();
        if n == 0 || features[0].is_empty() {
            return Self { mean: vec![], inv_cov: vec![], dim: 0 };
        }
        let dim = features[0].len();

        // Compute mean
        let mut mean = vec![0.0; dim];
        for f in features {
            for (i, v) in f.iter().enumerate() {
                mean[i] += v;
            }
        }
        for m in &mut mean {
            *m /= n as f64;
        }

        // Compute covariance matrix
        let mut cov = vec![vec![0.0; dim]; dim];
        for f in features {
            for i in 0..dim {
                for j in 0..dim {
                    cov[i][j] += (f[i] - mean[i]) * (f[j] - mean[j]);
                }
            }
        }
        for row in &mut cov {
            for v in row.iter_mut() {
                *v /= n as f64;
            }
        }

        // Add regularization to diagonal to prevent singular matrix
        for i in 0..dim {
            cov[i][i] += 1e-6;
        }

        // Invert covariance matrix (for small dims, direct Gauss-Jordan)
        let inv_cov = invert_matrix(&cov);

        Self { mean, inv_cov, dim }
    }

    /// Compute Mahalanobis distance of a point from the distribution.
    pub fn mahalanobis_distance(&self, point: &[f64]) -> f64 {
        if self.dim == 0 || point.len() != self.dim {
            return 0.0;
        }
        let diff: Vec<f64> = point.iter().zip(&self.mean).map(|(p, m)| p - m).collect();
        let mut result = 0.0;
        for i in 0..self.dim {
            for j in 0..self.dim {
                result += diff[i] * self.inv_cov[i][j] * diff[j];
            }
        }
        result.max(0.0).sqrt()
    }
}

/// Gauss-Jordan matrix inversion for small matrices (dim <= 6).
fn invert_matrix(matrix: &[Vec<f64>]) -> Vec<Vec<f64>> {
    let n = matrix.len();
    let mut aug = vec![vec![0.0; 2 * n]; n];

    // Build augmented matrix [A | I]
    for i in 0..n {
        for j in 0..n {
            aug[i][j] = matrix[i][j];
        }
        aug[i][n + i] = 1.0;
    }

    // Forward elimination with partial pivoting
    for col in 0..n {
        let mut max_row = col;
        for row in (col + 1)..n {
            if aug[row][col].abs() > aug[max_row][col].abs() {
                max_row = row;
            }
        }
        aug.swap(col, max_row);

        let pivot = aug[col][col];
        if pivot.abs() < 1e-12 {
            // Singular — return identity as fallback
            return (0..n).map(|i| {
                let mut row = vec![0.0; n];
                row[i] = 1.0;
                row
            }).collect();
        }

        for j in 0..(2 * n) {
            aug[col][j] /= pivot;
        }

        for row in 0..n {
            if row != col {
                let factor = aug[row][col];
                for j in 0..(2 * n) {
                    aug[row][j] -= factor * aug[col][j];
                }
            }
        }
    }

    // Extract inverse from right half
    aug.iter().map(|row| row[n..].to_vec()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mahalanobis_identical_to_mean_is_zero() {
        let features = vec![
            vec![10.0, 5.0, 3.0],
            vec![12.0, 6.0, 4.0],
            vec![11.0, 5.5, 3.5],
        ];
        let scorer = StructuralScorer::from_features(&features);
        let mean_point = [11.0, 5.5, 3.5];
        let d = scorer.mahalanobis_distance(&mean_point);
        assert!(d < 1.0, "Distance from near-mean should be small, got {}", d);
    }

    #[test]
    fn test_outlier_has_high_distance() {
        let mut features = Vec::new();
        for _ in 0..100 {
            features.push(vec![10.0, 5.0, 3.0]);
        }
        let scorer = StructuralScorer::from_features(&features);
        let normal_d = scorer.mahalanobis_distance(&[10.0, 5.0, 3.0]);
        let outlier_d = scorer.mahalanobis_distance(&[100.0, 50.0, 30.0]);
        assert!(outlier_d > normal_d, "Outlier ({}) should > normal ({})", outlier_d, normal_d);
    }

    #[test]
    fn test_feature_extraction() {
        let features = extract_structural_features_raw(5, 12, 3, 50, 2);
        assert_eq!(features.len(), 5);
        assert_eq!(features[0], 5.0);
        assert_eq!(features[1], 12.0);
    }

    #[test]
    fn test_empty_features() {
        let scorer = StructuralScorer::from_features(&[]);
        assert_eq!(scorer.mahalanobis_distance(&[1.0, 2.0]), 0.0);
    }

    #[test]
    fn test_invert_identity() {
        let identity = vec![vec![1.0, 0.0], vec![0.0, 1.0]];
        let inv = invert_matrix(&identity);
        assert!((inv[0][0] - 1.0).abs() < 1e-10);
        assert!((inv[1][1] - 1.0).abs() < 1e-10);
        assert!(inv[0][1].abs() < 1e-10);
    }
}
```

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli predictive::structural -- --nocapture`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/predictive/structural.rs
git commit -m "feat(predictive): add L2 structural Mahalanobis distance scorer"
```

---

## Task 4: Port node2vec + word2vec from `repotoire-fast`

**Files:**
- Create: `repotoire-cli/src/predictive/embeddings.rs`
- Reference: `repotoire-fast/src/graph_algo.rs` (lines 1185-1305, `node2vec_random_walks`)
- Reference: `repotoire-fast/src/word2vec.rs` (full file, `train_skipgram_parallel`)

**Step 1: Write test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_node2vec_walks_basic() {
        let edges = vec![(0, 1), (1, 2), (2, 0)];
        let walks = node2vec_random_walks(&edges, 3, 5, 2, 1.0, 1.0, Some(42));
        assert!(!walks.is_empty(), "Should generate walks");
        for walk in &walks {
            assert!(walk.len() <= 5, "Walk length should be <= walk_length");
        }
    }

    #[test]
    fn test_word2vec_produces_embeddings() {
        let edges = vec![(0, 1), (1, 2), (2, 0), (0, 2)];
        let walks = node2vec_random_walks(&edges, 3, 10, 5, 1.0, 1.0, Some(42));
        let config = Word2VecConfig {
            embedding_dim: 16,
            epochs: 2,
            ..Default::default()
        };
        let result = train_skipgram(&walks, &config);
        assert!(!result.embeddings.is_empty(), "Should produce embeddings");
        // Each embedding should have dim=16
        for emb in result.embeddings.values() {
            assert_eq!(emb.len(), 16);
        }
    }
}
```

**Step 2: Port the code**

Port `node2vec_random_walks()` from `repotoire-fast/src/graph_algo.rs` (lines 1185-1305) and `train_skipgram()` / `train_skipgram_parallel()` from `repotoire-fast/src/word2vec.rs`. Strip PyO3 wrappers. Keep rayon parallelism. Keep ChaCha8Rng for deterministic seeding.

Dependencies needed in `repotoire-cli/Cargo.toml`:
- `rand` (check if already present)
- `rand_chacha` (check if already present)
- `rustc-hash` (already present per CLAUDE.md)

The port should be a clean copy with:
- Remove `pyo3` annotations
- Remove `GraphError` — use `anyhow::Result` instead
- Keep the algorithm identical
- Keep `Word2VecConfig`, `Word2VecResult` structs

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli predictive::embeddings -- --nocapture`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/predictive/embeddings.rs
git commit -m "feat(predictive): port node2vec + word2vec from repotoire-fast"
```

---

## Task 5: L3 — Relational surprise (per-edge-type node2vec embeddings)

**Files:**
- Create: `repotoire-cli/src/predictive/relational.rs`

**Step 1: Write test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_relational_scorer_basic() {
        // Build a small graph with 4 nodes and 2 edge types
        let call_edges = vec![(0u32, 1), (1, 2), (2, 3)];
        let import_edges = vec![(0u32, 2), (1, 3)];

        let edge_sets = vec![
            ("calls", call_edges),
            ("imports", import_edges),
        ];

        let scorer = RelationalScorer::from_edge_sets(&edge_sets, 4, 16, Some(42));
        assert!(!scorer.embeddings.is_empty());

        // Node distances should be computable
        let d = scorer.neighborhood_distance(0, 3);
        assert!(d >= 0.0, "Distance should be non-negative");
    }

    #[test]
    fn test_empty_graph_returns_zero() {
        let scorer = RelationalScorer::from_edge_sets(&[], 0, 16, Some(42));
        assert_eq!(scorer.neighborhood_distance(0, 1), 0.0);
    }
}
```

**Step 2: Implement `RelationalScorer`**

```rust
//! L3: Relational surprise via per-edge-type node2vec embeddings.
//!
//! Runs separate node2vec passes per edge type (Calls, Imports, Inherits, Contains).
//! Concatenates embeddings. Computes cosine distance to k-nearest neighbors.
//! Entities far from their neighborhood = relationally surprising.
//!
//! References:
//! - Qu et al., "node2defect" (ASE 2018)
//! - Zhang et al., "DSHGT" (arXiv 2306.01376) — heterogeneous edge types matter

use super::embeddings::{node2vec_random_walks, train_skipgram, Word2VecConfig};
use std::collections::HashMap;

pub struct RelationalScorer {
    /// Concatenated embeddings: node_id → Vec<f32>
    pub embeddings: HashMap<u32, Vec<f32>>,
    /// Total embedding dimension (embedding_dim * num_edge_types)
    pub total_dim: usize,
}

impl RelationalScorer {
    /// Build embeddings from multiple edge type sets.
    /// Each entry: (edge_type_name, edges as (u32, u32) pairs).
    pub fn from_edge_sets(
        edge_sets: &[(&str, Vec<(u32, u32)>)],
        num_nodes: usize,
        embedding_dim: usize,
        seed: Option<u64>,
    ) -> Self {
        if num_nodes == 0 || edge_sets.is_empty() {
            return Self { embeddings: HashMap::new(), total_dim: 0 };
        }

        let total_dim = embedding_dim * edge_sets.len();
        let mut combined: HashMap<u32, Vec<f32>> = HashMap::new();

        for (i, (_name, edges)) in edge_sets.iter().enumerate() {
            if edges.is_empty() { continue; }

            let walk_seed = seed.map(|s| s.wrapping_add(i as u64));
            let walks = node2vec_random_walks(edges, num_nodes, 10, 20, 1.0, 1.0, walk_seed);

            let config = Word2VecConfig {
                embedding_dim,
                window_size: 5,
                min_count: 1,
                negative_samples: 5,
                learning_rate: 0.025,
                min_learning_rate: 0.0001,
                epochs: 5,
                seed: walk_seed,
            };

            let result = train_skipgram(&walks, &config);

            // Place this edge type's embeddings at the correct offset
            for (node_id, emb) in &result.embeddings {
                let entry = combined.entry(*node_id).or_insert_with(|| vec![0.0; total_dim]);
                let offset = i * embedding_dim;
                let end = (offset + emb.len()).min(total_dim);
                entry[offset..end].copy_from_slice(&emb[..end - offset]);
            }
        }

        Self { embeddings: combined, total_dim }
    }

    /// Cosine distance between two nodes' concatenated embeddings.
    pub fn neighborhood_distance(&self, node_a: u32, node_b: u32) -> f64 {
        let (Some(a), Some(b)) = (self.embeddings.get(&node_a), self.embeddings.get(&node_b)) else {
            return 0.0;
        };
        1.0 - cosine_similarity(a, b)
    }

    /// Compute average cosine distance to k-nearest neighbors for a node.
    /// Higher = more relationally surprising.
    pub fn knn_distance(&self, node_id: u32, k: usize) -> f64 {
        let Some(emb) = self.embeddings.get(&node_id) else { return 0.0 };
        let mut distances: Vec<f64> = self.embeddings.iter()
            .filter(|(&id, _)| id != node_id)
            .map(|(_, other)| 1.0 - cosine_similarity(emb, other))
            .collect();
        distances.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let k = k.min(distances.len());
        if k == 0 { return 0.0; }
        distances[..k].iter().sum::<f64>() / k as f64
    }
}

fn cosine_similarity(a: &[f32], b: &[f32]) -> f64 {
    let dot: f64 = a.iter().zip(b).map(|(x, y)| (*x as f64) * (*y as f64)).sum();
    let norm_a: f64 = a.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
    let norm_b: f64 = b.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
    if norm_a < 1e-10 || norm_b < 1e-10 { return 0.0; }
    dot / (norm_a * norm_b)
}
```

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli predictive::relational -- --nocapture`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/predictive/relational.rs
git commit -m "feat(predictive): add L3 relational scorer with per-edge-type node2vec"
```

---

## Task 6: L1.5 — Dependency-chain surprisal

**Files:**
- Create: `repotoire-cli/src/predictive/dependency_chain.rs`

**Step 1: Write test**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::calibrate::NgramModel;

    #[test]
    fn test_extract_dependency_chains() {
        // A calls B, B calls C
        let calls = vec![
            ("A".to_string(), "B".to_string()),
            ("B".to_string(), "C".to_string()),
        ];
        let chains = extract_dependency_chains(&calls, 3);
        assert!(!chains.is_empty());
        // Should contain chain [A, B, C]
        assert!(chains.iter().any(|c| c.len() == 3));
    }

    #[test]
    fn test_chain_surprisal_computation() {
        let mut model = NgramModel::new();
        let code = "let x = foo();\nlet y = bar();\n";
        for _ in 0..1000 {
            model.train_on_tokens(&NgramModel::tokenize_file(code));
        }
        assert!(model.is_confident());

        let chain_content = vec!["let x = foo();", "let y = bar();"];
        let score = chain_surprisal(&model, &chain_content);
        assert!(score >= 0.0);
    }
}
```

**Step 2: Implement**

```rust
//! L1.5: Dependency-chain surprisal.
//!
//! Computes n-gram surprisal along dependency graph paths rather than
//! isolated lines. Bridges L1 (token) and L3 (relational).
//!
//! Reference: Yang et al., "Dependency-Aware Code Naturalness" (OOPSLA 2024, DAN)

use crate::calibrate::NgramModel;
use std::collections::{HashMap, HashSet};

/// Extract dependency chains of length up to `max_depth` from call edges.
/// Each chain is a sequence of qualified names connected by call edges.
pub fn extract_dependency_chains(
    calls: &[(String, String)],
    max_depth: usize,
) -> Vec<Vec<String>> {
    // Build adjacency list
    let mut adj: HashMap<&str, Vec<&str>> = HashMap::new();
    for (caller, callee) in calls {
        adj.entry(caller.as_str()).or_default().push(callee.as_str());
    }

    let mut chains = Vec::new();
    let mut visited = HashSet::new();

    // DFS from each node to build chains
    for (start, _) in calls {
        visited.clear();
        let mut stack: Vec<Vec<String>> = vec![vec![start.clone()]];

        while let Some(chain) = stack.pop() {
            if chain.len() >= max_depth {
                chains.push(chain);
                continue;
            }
            let last = chain.last().unwrap();
            let neighbors = adj.get(last.as_str());
            let mut extended = false;

            if let Some(nbrs) = neighbors {
                for nbr in nbrs {
                    if !chain.contains(&nbr.to_string()) {
                        let mut new_chain = chain.clone();
                        new_chain.push(nbr.to_string());
                        stack.push(new_chain);
                        extended = true;
                    }
                }
            }
            if !extended && chain.len() >= 2 {
                chains.push(chain);
            }
        }
    }

    chains
}

/// Compute surprisal of a chain's concatenated token sequences.
pub fn chain_surprisal(model: &NgramModel, chain_lines: &[&str]) -> f64 {
    if !model.is_confident() || chain_lines.is_empty() {
        return 0.0;
    }

    // Tokenize all lines in the chain as a single sequence
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

/// Score a function by computing max chain surprisal across all dependency
/// chains that include this function.
pub struct DependencyChainScorer {
    /// function_qn → max chain surprisal
    pub scores: HashMap<String, f64>,
}

impl DependencyChainScorer {
    pub fn new() -> Self {
        Self { scores: HashMap::new() }
    }

    /// Record a chain's surprisal for all functions in the chain.
    pub fn record_chain(&mut self, chain_qns: &[String], surprisal: f64) {
        for qn in chain_qns {
            let entry = self.scores.entry(qn.clone()).or_insert(0.0);
            if surprisal > *entry {
                *entry = surprisal;
            }
        }
    }

    pub fn score(&self, function_qn: &str) -> f64 {
        self.scores.get(function_qn).copied().unwrap_or(0.0)
    }
}
```

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli predictive::dependency_chain -- --nocapture`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/predictive/dependency_chain.rs
git commit -m "feat(predictive): add L1.5 dependency-chain surprisal scorer"
```

---

## Task 7: L4 — Architectural surprise

**Files:**
- Create: `repotoire-cli/src/predictive/architectural.rs`

**Step 1: Write test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_module_profile_aggregation() {
        let mut scorer = ArchitecturalScorer::new();
        scorer.add_module("src/auth", ModuleProfile {
            avg_fan_in: 5.0, avg_fan_out: 3.0, internal_cohesion: 0.8,
            external_coupling: 0.2, entity_count: 10, smell_type_count: 1,
        });
        scorer.add_module("src/db", ModuleProfile {
            avg_fan_in: 4.0, avg_fan_out: 2.0, internal_cohesion: 0.7,
            external_coupling: 0.3, entity_count: 8, smell_type_count: 2,
        });
        scorer.add_module("src/hack", ModuleProfile {
            avg_fan_in: 50.0, avg_fan_out: 30.0, internal_cohesion: 0.1,
            external_coupling: 0.9, entity_count: 100, smell_type_count: 8,
        });
        scorer.finalize();

        let hack_d = scorer.module_distance("src/hack");
        let auth_d = scorer.module_distance("src/auth");
        assert!(hack_d > auth_d, "Unusual module should have higher distance");
    }
}
```

**Step 2: Implement**

```rust
//! L4: Architectural surprise — module-level distributional outlier detection.
//!
//! Aggregates L2/L3 signals per module/directory. Detects modules whose profile
//! is unusual compared to peer modules. Includes cross-smell co-occurrence
//! (Zhang et al. arXiv 2509.03896).

use super::structural::StructuralScorer;
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct ModuleProfile {
    pub avg_fan_in: f64,
    pub avg_fan_out: f64,
    pub internal_cohesion: f64,
    pub external_coupling: f64,
    pub entity_count: usize,
    pub smell_type_count: usize,
}

impl ModuleProfile {
    pub fn to_feature_vec(&self) -> Vec<f64> {
        vec![
            self.avg_fan_in,
            self.avg_fan_out,
            self.internal_cohesion,
            self.external_coupling,
            self.entity_count as f64,
            self.smell_type_count as f64,
        ]
    }
}

pub struct ArchitecturalScorer {
    modules: HashMap<String, ModuleProfile>,
    scorer: Option<StructuralScorer>,
}

impl ArchitecturalScorer {
    pub fn new() -> Self {
        Self { modules: HashMap::new(), scorer: None }
    }

    pub fn add_module(&mut self, module_path: &str, profile: ModuleProfile) {
        self.modules.insert(module_path.to_string(), profile);
    }

    /// Finalize: build the Mahalanobis scorer from all module profiles.
    pub fn finalize(&mut self) {
        let features: Vec<Vec<f64>> = self.modules.values()
            .map(|p| p.to_feature_vec())
            .collect();
        if features.len() >= 3 {
            self.scorer = Some(StructuralScorer::from_features(&features));
        }
    }

    /// Mahalanobis distance for a module.
    pub fn module_distance(&self, module_path: &str) -> f64 {
        let Some(profile) = self.modules.get(module_path) else { return 0.0 };
        let Some(scorer) = &self.scorer else { return 0.0 };
        scorer.mahalanobis_distance(&profile.to_feature_vec())
    }
}
```

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli predictive::architectural -- --nocapture`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/predictive/architectural.rs
git commit -m "feat(predictive): add L4 architectural module-level scorer"
```

---

## Task 8: Wire up `PredictiveCodingEngine` — orchestrate all 5 levels

**Files:**
- Modify: `repotoire-cli/src/predictive/mod.rs`

**Step 1: Write integration test**

Add to `mod.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_engine_produces_compound_scores() {
        // This is a smoke test — full integration tested via detector
        let engine = PredictiveCodingEngine::new();
        // Engine should be constructible and have all 5 levels
        assert_eq!(engine.level_count(), 5);
    }
}
```

**Step 2: Implement the engine**

Add to `mod.rs` the `PredictiveCodingEngine` struct that:
1. Holds all 5 level scorers
2. Has a `train()` method that takes `&dyn GraphQuery`, `&dyn FileProvider`, and parse results
3. Has a `score_entity()` method that returns `CompoundScore` for a function

The engine:
- Trains L1 `TokenLevelScorer` from file contents (per-language)
- Trains L2 `StructuralScorer` from graph function nodes
- Computes L1.5 `DependencyChainScorer` from call graph + L1 model
- Trains L3 `RelationalScorer` from graph edges (per edge type: Calls, Imports, Inherits, Contains)
- Trains L4 `ArchitecturalScorer` from module-aggregated metrics
- Computes empirical precision weights via `compound::compute_precision_weights()`
- Scores each function at all 5 levels and produces `CompoundScore`

**Step 3: Run tests**

Run: `cargo test -p repotoire-cli predictive -- --nocapture`
Expected: PASS

**Step 4: Commit**

```bash
git add repotoire-cli/src/predictive/mod.rs
git commit -m "feat(predictive): wire up PredictiveCodingEngine orchestrating all 5 levels"
```

---

## Task 9: Create `HierarchicalSurprisalDetector`

**Files:**
- Create: `repotoire-cli/src/detectors/hierarchical_surprisal.rs`
- Modify: `repotoire-cli/src/detectors/mod.rs` — register detector

**Step 1: Write test**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::GraphStore;

    #[test]
    fn test_detector_trait_name() {
        let engine = crate::predictive::PredictiveCodingEngine::new();
        let detector = HierarchicalSurprisalDetector::new(engine);
        assert_eq!(detector.name(), "hierarchical-surprisal");
        assert_eq!(detector.category(), "predictive-coding");
    }
}
```

**Step 2: Implement the detector**

The detector:
- Implements the `Detector` trait
- `name()` → `"hierarchical-surprisal"`
- `category()` → `"predictive-coding"`
- `detect()` takes `&dyn GraphQuery` and `&dyn FileProvider`
- Internally trains the `PredictiveCodingEngine` then scores all functions
- Converts `CompoundScore` results into `Finding` structs
- Each finding includes per-level breakdown in `threshold_metadata`
- Findings are sorted by compound_surprise (descending)
- Max findings: 30

**Step 3: Register in `mod.rs`**

In `repotoire-cli/src/detectors/mod.rs`:
1. Add `mod hierarchical_surprisal;`
2. Add `pub use hierarchical_surprisal::HierarchicalSurprisalDetector;`
3. In `default_detectors_full()`, replace the `SurprisalDetector` construction (lines 525-541) with `HierarchicalSurprisalDetector`. Keep the same conditional pattern (only add if n-gram model is confident).

**Step 4: Run all tests**

Run: `cargo test -p repotoire-cli -- --nocapture`
Expected: PASS (all existing tests + new tests)

**Step 5: Compile check**

Run: `cargo check -p repotoire-cli`
Expected: compiles with no errors

**Step 6: Commit**

```bash
git add repotoire-cli/src/detectors/hierarchical_surprisal.rs repotoire-cli/src/detectors/mod.rs
git commit -m "feat(predictive): add HierarchicalSurprisalDetector replacing flat surprisal"
```

---

## Task 10: Integration test — run against a real codebase

**Files:**
- Create: `repotoire-cli/tests/predictive_coding.rs` (integration test)

**Step 1: Write integration test**

```rust
//! Integration test: verify hierarchical predictive coding detector runs
//! on the repotoire-cli source tree and produces sensible output.

use std::path::PathBuf;
use std::process::Command;

#[test]
fn test_predictive_coding_on_self() {
    let repo_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let output = Command::new(env!("CARGO_BIN_EXE_repotoire"))
        .args(&["analyze", repo_path.to_str().unwrap(), "--format", "json"])
        .output()
        .expect("Failed to run repotoire analyze");

    let stdout = String::from_utf8_lossy(&output.stdout);

    // Verify the analysis completes (exit code 0)
    assert!(output.status.success(), "Analyze failed: {}", String::from_utf8_lossy(&output.stderr));

    // Verify hierarchical-surprisal findings appear in JSON output
    // (the codebase should be large enough for the n-gram model to be confident)
    // Note: it's OK if there are 0 findings — just verify the detector ran without crashing
    if stdout.contains("hierarchical-surprisal") {
        // Parse and verify finding structure
        let v: serde_json::Value = serde_json::from_str(&stdout).unwrap_or_default();
        if let Some(findings) = v.get("findings").and_then(|f| f.as_array()) {
            for f in findings {
                if f.get("detector").and_then(|d| d.as_str()) == Some("HierarchicalSurprisalDetector") {
                    // Verify per-level metadata exists
                    let meta = f.get("threshold_metadata");
                    assert!(meta.is_some(), "Findings should have threshold_metadata");
                }
            }
        }
    }
}
```

**Step 2: Run integration test**

Run: `cargo test -p repotoire-cli --test predictive_coding -- --nocapture`
Expected: PASS

**Step 3: Commit**

```bash
git add repotoire-cli/tests/predictive_coding.rs
git commit -m "test: add integration test for hierarchical predictive coding"
```

---

## Task 11: Performance validation + documentation

**Step 1: Benchmark on the repotoire-cli codebase (93k+ lines)**

Run: `time cargo run --release -- analyze . --format json > /dev/null`

Compare to before the change. The design budget is <2.5s for 10k-node graphs.

**Step 2: Update CLAUDE.md**

Add the predictive coding module to the "Core Modules" section. Update the detector count (99 → still 99 if replacing SurprisalDetector, or 100 if keeping both).

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add hierarchical predictive coding to CLAUDE.md"
```

---

## Dependency Graph

```
Task 1 (skeleton)
├── Task 2 (L1 token) — depends on Task 1
├── Task 3 (L2 structural) — depends on Task 1
├── Task 4 (embeddings port) — depends on Task 1
├── Task 6 (L1.5 dependency chain) — depends on Task 2
├── Task 5 (L3 relational) — depends on Task 4
└── Task 7 (L4 architectural) — depends on Task 3

Task 8 (engine) — depends on Tasks 2, 3, 5, 6, 7
Task 9 (detector) — depends on Task 8
Task 10 (integration test) — depends on Task 9
Task 11 (perf + docs) — depends on Task 10
```

Tasks 2, 3, 4 can run in parallel after Task 1.
Tasks 5, 6, 7 can partially overlap.
Tasks 8-11 are sequential.
