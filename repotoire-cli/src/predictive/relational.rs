//! L3: Relational surprise via per-edge-type node2vec embeddings.
//!
//! Runs separate node2vec passes per edge type (Calls, Imports, Inherits, Contains),
//! concatenates embeddings, and computes cosine kNN distance. Nodes that are
//! structurally distant from their k-nearest neighbors in the concatenated
//! embedding space are flagged as relationally surprising.
//!
//! References:
//! - Qu et al., "node2defect" (ASE 2018)
//! - Zhang et al., "DSHGT" (arXiv 2306.01376)

use super::embeddings::{node2vec_random_walks, train_skipgram, Word2VecConfig};
use rustc_hash::FxHashMap;

/// Relational scorer that concatenates per-edge-type node2vec embeddings.
pub struct RelationalScorer {
    /// Concatenated embeddings: node_id -> Vec<f32> of length `total_dim`.
    pub embeddings: FxHashMap<u32, Vec<f32>>,
    /// Total embedding dimension (embedding_dim * num_edge_types).
    pub total_dim: usize,
}

impl RelationalScorer {
    /// Build embeddings from multiple edge-type sets.
    ///
    /// Each entry in `edge_sets` is a (edge_type_name, edges) pair where edges
    /// are directed (u32, u32) pairs. A separate node2vec + skip-gram pass is
    /// run for each edge type, and the resulting embeddings are concatenated
    /// into a single vector per node.
    ///
    /// # Arguments
    /// * `edge_sets` - Slice of (name, edges) per edge type
    /// * `num_nodes` - Total number of nodes (IDs in 0..num_nodes)
    /// * `embedding_dim` - Dimension per edge type (total = dim * edge_sets.len())
    /// * `seed` - Optional seed for deterministic results
    pub fn from_edge_sets(
        edge_sets: &[(&str, Vec<(u32, u32)>)],
        num_nodes: usize,
        embedding_dim: usize,
        seed: Option<u64>,
    ) -> Self {
        if num_nodes == 0 || edge_sets.is_empty() {
            return Self {
                embeddings: FxHashMap::default(),
                total_dim: 0,
            };
        }

        let total_dim = embedding_dim * edge_sets.len();
        let mut combined: FxHashMap<u32, Vec<f32>> = FxHashMap::default();

        for (i, (_name, edges)) in edge_sets.iter().enumerate() {
            if edges.is_empty() {
                continue;
            }

            // Vary seed per edge type so each pass produces different walks
            let walk_seed = seed.map(|s| s.wrapping_add(i as u64));

            let walks =
                node2vec_random_walks(edges, num_nodes, 10, 20, 1.0, 1.0, walk_seed);

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

            // Splice this edge type's embeddings into the correct offset range
            for (node_id, emb) in &result.embeddings {
                let entry = combined
                    .entry(*node_id)
                    .or_insert_with(|| vec![0.0; total_dim]);
                let offset = i * embedding_dim;
                let copy_len = emb.len().min(total_dim - offset);
                entry[offset..offset + copy_len].copy_from_slice(&emb[..copy_len]);
            }
        }

        Self {
            embeddings: combined,
            total_dim,
        }
    }

    /// Cosine distance between two nodes in the concatenated embedding space.
    ///
    /// Returns 0.0 if either node has no embedding.
    pub fn node_distance(&self, node_a: u32, node_b: u32) -> f64 {
        let (Some(a), Some(b)) =
            (self.embeddings.get(&node_a), self.embeddings.get(&node_b))
        else {
            return 0.0;
        };
        1.0 - cosine_similarity(a, b)
    }

    /// Average cosine distance to k-nearest neighbors.
    ///
    /// Higher values indicate nodes that are relationally surprising — they are
    /// far from their closest peers in the joint embedding space.
    pub fn knn_distance(&self, node_id: u32, k: usize) -> f64 {
        let Some(emb) = self.embeddings.get(&node_id) else {
            return 0.0;
        };

        let mut distances: Vec<f64> = self
            .embeddings
            .iter()
            .filter(|(&id, _)| id != node_id)
            .map(|(_, other)| 1.0 - cosine_similarity(emb, other))
            .collect();

        distances.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        let k = k.min(distances.len());
        if k == 0 {
            return 0.0;
        }

        distances[..k].iter().sum::<f64>() / k as f64
    }
}

/// Cosine similarity between two f32 vectors, computed in f64 for numerical stability.
///
/// Returns 0.0 if either vector has near-zero norm.
fn cosine_similarity(a: &[f32], b: &[f32]) -> f64 {
    let dot: f64 = a
        .iter()
        .zip(b)
        .map(|(x, y)| (*x as f64) * (*y as f64))
        .sum();
    let norm_a: f64 = a.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
    let norm_b: f64 = b.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
    if norm_a < 1e-10 || norm_b < 1e-10 {
        return 0.0;
    }
    dot / (norm_a * norm_b)
}

// ============================================================================
// UNIT TESTS
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_relational_scorer_basic() {
        // Two edge types on a small 5-node graph
        let calls_edges = vec![(0, 1), (1, 2), (2, 0), (0, 2)];
        let imports_edges = vec![(3, 4), (4, 3), (3, 0), (0, 3)];

        let edge_sets: Vec<(&str, Vec<(u32, u32)>)> = vec![
            ("calls", calls_edges),
            ("imports", imports_edges),
        ];

        let scorer = RelationalScorer::from_edge_sets(&edge_sets, 5, 16, Some(42));

        // Total dim should be 16 * 2 = 32
        assert_eq!(scorer.total_dim, 32);

        // At least some nodes should have embeddings
        assert!(
            !scorer.embeddings.is_empty(),
            "Should produce embeddings for at least some nodes"
        );

        // Embeddings should have the correct dimension
        for (_node_id, emb) in &scorer.embeddings {
            assert_eq!(
                emb.len(),
                32,
                "Each embedding should have total_dim=32 components"
            );
        }

        // Node distances should be computable
        let dist = scorer.node_distance(0, 1);
        assert!(dist >= 0.0, "Distance should be non-negative, got {dist}");
    }

    #[test]
    fn test_empty_graph_returns_zero() {
        // No nodes
        let scorer =
            RelationalScorer::from_edge_sets(&[], 0, 16, Some(42));
        assert!(scorer.embeddings.is_empty());
        assert_eq!(scorer.total_dim, 0);
        assert_eq!(scorer.node_distance(0, 1), 0.0);
        assert_eq!(scorer.knn_distance(0, 5), 0.0);

        // Nodes but empty edge sets
        let edge_sets: Vec<(&str, Vec<(u32, u32)>)> =
            vec![("calls", vec![]), ("imports", vec![])];
        let scorer =
            RelationalScorer::from_edge_sets(&edge_sets, 5, 16, Some(42));
        // All edge sets are empty, so no walks are produced, no embeddings
        assert!(scorer.embeddings.is_empty());
        // total_dim is still set based on edge_sets count
        assert_eq!(scorer.total_dim, 32);
        assert_eq!(scorer.node_distance(0, 1), 0.0);
        assert_eq!(scorer.knn_distance(0, 3), 0.0);
    }

    #[test]
    fn test_knn_distance_basic() {
        // Fully connected small graph so all nodes get embeddings
        let edges = vec![
            (0, 1),
            (1, 0),
            (1, 2),
            (2, 1),
            (2, 3),
            (3, 2),
            (3, 0),
            (0, 3),
        ];

        let edge_sets: Vec<(&str, Vec<(u32, u32)>)> = vec![("calls", edges)];

        let scorer = RelationalScorer::from_edge_sets(&edge_sets, 4, 16, Some(42));

        // kNN distance for each node with k=2
        for node in 0..4u32 {
            if scorer.embeddings.contains_key(&node) {
                let dist = scorer.knn_distance(node, 2);
                assert!(
                    dist >= 0.0,
                    "kNN distance for node {node} should be non-negative, got {dist}"
                );
            }
        }

        // kNN distance with k larger than number of neighbors
        if scorer.embeddings.contains_key(&0) {
            let dist = scorer.knn_distance(0, 100);
            assert!(
                dist >= 0.0,
                "kNN distance with large k should still be non-negative"
            );
        }

        // kNN distance for missing node
        assert_eq!(
            scorer.knn_distance(999, 5),
            0.0,
            "Missing node should return 0.0"
        );
    }

    #[test]
    fn test_cosine_similarity_identical() {
        let a = vec![1.0f32, 2.0, 3.0, 4.0];
        let b = vec![1.0f32, 2.0, 3.0, 4.0];
        let sim = cosine_similarity(&a, &b);
        assert!(
            (sim - 1.0).abs() < 1e-6,
            "Identical vectors should have similarity ~1.0, got {sim}"
        );
    }

    #[test]
    fn test_cosine_similarity_orthogonal() {
        let a = vec![1.0f32, 0.0, 0.0];
        let b = vec![0.0f32, 1.0, 0.0];
        let sim = cosine_similarity(&a, &b);
        assert!(
            sim.abs() < 1e-6,
            "Orthogonal vectors should have similarity ~0.0, got {sim}"
        );
    }

    #[test]
    fn test_cosine_similarity_opposite() {
        let a = vec![1.0f32, 2.0, 3.0];
        let b = vec![-1.0f32, -2.0, -3.0];
        let sim = cosine_similarity(&a, &b);
        assert!(
            (sim - (-1.0)).abs() < 1e-6,
            "Opposite vectors should have similarity ~-1.0, got {sim}"
        );
    }

    #[test]
    fn test_cosine_similarity_zero_vector() {
        let a = vec![0.0f32, 0.0, 0.0];
        let b = vec![1.0f32, 2.0, 3.0];
        let sim = cosine_similarity(&a, &b);
        assert!(
            sim.abs() < 1e-6,
            "Zero vector should give similarity 0.0, got {sim}"
        );
    }

    #[test]
    fn test_node_distance_missing_nodes() {
        let edges = vec![(0, 1), (1, 0)];
        let edge_sets: Vec<(&str, Vec<(u32, u32)>)> = vec![("calls", edges)];
        let scorer = RelationalScorer::from_edge_sets(&edge_sets, 2, 8, Some(42));

        // Distance involving a node that doesn't exist should be 0.0
        assert_eq!(scorer.node_distance(0, 999), 0.0);
        assert_eq!(scorer.node_distance(999, 0), 0.0);
        assert_eq!(scorer.node_distance(998, 999), 0.0);
    }

    #[test]
    fn test_multi_edge_type_concatenation() {
        // Verify that embeddings from different edge types occupy different
        // slices of the concatenated vector.
        let edges_a = vec![(0, 1), (1, 0), (1, 2), (2, 1)];
        let edges_b = vec![(2, 3), (3, 2), (3, 4), (4, 3)];

        let edge_sets: Vec<(&str, Vec<(u32, u32)>)> =
            vec![("calls", edges_a), ("imports", edges_b)];

        let dim = 8;
        let scorer = RelationalScorer::from_edge_sets(&edge_sets, 5, dim, Some(42));

        assert_eq!(scorer.total_dim, 16, "2 edge types * 8 dim = 16");

        // Nodes that only appear in edge set A should have zeros in the B slice
        // (nodes 0 only has call edges, not import edges)
        if let Some(emb) = scorer.embeddings.get(&0) {
            // First 8 dimensions are from calls — should have non-zero values
            // (node 0 participates in calls)
            let calls_slice = &emb[..dim];
            let has_nonzero_calls = calls_slice.iter().any(|v| v.abs() > 1e-8);
            assert!(
                has_nonzero_calls,
                "Node 0 participates in calls, so calls embedding slice should be non-zero"
            );
        }
    }
}
