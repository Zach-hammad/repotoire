//! Node2vec + Word2vec embeddings (ported from repotoire-fast).
//!
//! node2vec generates biased random walks on the code graph.
//! word2vec (skip-gram with negative sampling) learns embeddings from those walks.
//! The relational scorer (L3) uses these to compute per-entity distances.
//!
//! References:
//! - Grover & Leskovec, "node2vec: Scalable Feature Learning for Networks" (2016)
//! - Mikolov et al., "Distributed Representations of Words and Phrases" (2013)

use rand::prelude::*;
use rand_chacha::ChaCha8Rng;
use rayon::prelude::*;
use rustc_hash::{FxHashMap, FxHashSet};

// ============================================================================
// WORD2VEC CONFIGURATION & RESULT
// ============================================================================

/// Word2Vec skip-gram configuration.
#[derive(Clone, Debug)]
pub struct Word2VecConfig {
    /// Dimension of embedding vectors (default: 128).
    pub embedding_dim: usize,
    /// Context window size -- how many words on each side (default: 5).
    pub window_size: usize,
    /// Minimum frequency for a word to be included (default: 1 for graphs).
    pub min_count: usize,
    /// Number of negative samples per positive sample (default: 5).
    pub negative_samples: usize,
    /// Initial learning rate (default: 0.025).
    pub learning_rate: f32,
    /// Final learning rate after decay (default: 0.0001).
    pub min_learning_rate: f32,
    /// Number of training epochs (default: 5).
    pub epochs: usize,
    /// Random seed for reproducibility.
    pub seed: Option<u64>,
}

impl Default for Word2VecConfig {
    fn default() -> Self {
        Self {
            embedding_dim: 128,
            window_size: 5,
            min_count: 1,
            negative_samples: 5,
            learning_rate: 0.025,
            min_learning_rate: 0.0001,
            epochs: 5,
            seed: None,
        }
    }
}

/// Word2Vec training result.
#[derive(Debug)]
pub struct Word2VecResult {
    /// Mapping from node ID to embedding vector.
    pub embeddings: FxHashMap<u32, Vec<f32>>,
    /// Vocabulary size (unique nodes).
    pub vocab_size: usize,
    /// Total training samples processed.
    pub samples_processed: u64,
    /// Final loss (average of last epoch).
    pub final_loss: f32,
}

// ============================================================================
// NODE2VEC RANDOM WALKS
// ============================================================================

/// Generate biased random walks for node2vec.
///
/// Performs walks_per_node walks of length walk_length starting from every
/// non-isolated node. Walk bias is controlled by p (return parameter) and
/// q (in-out parameter). Walks are generated in parallel across starting
/// nodes via rayon, with deterministic per-node seeding from ChaCha8Rng.
///
/// # Arguments
/// * `edges` - Directed edges as (source, destination) pairs
/// * `num_nodes` - Total number of nodes (node IDs must be in 0..num_nodes)
/// * `walk_length` - Maximum length of each walk
/// * `walks_per_node` - Number of walks to generate per starting node
/// * `p` - Return parameter (higher = less likely to return to previous node)
/// * `q` - In-out parameter (higher = more local/BFS-like)
/// * `seed` - Optional seed for deterministic walks
pub fn node2vec_random_walks(
    edges: &[(u32, u32)],
    num_nodes: usize,
    walk_length: usize,
    walks_per_node: usize,
    p: f64,
    q: f64,
    seed: Option<u64>,
) -> Vec<Vec<u32>> {
    // Validate parameters
    if num_nodes == 0 || walk_length == 0 || walks_per_node == 0 {
        return vec![];
    }

    if p <= 0.0 || q <= 0.0 {
        return vec![];
    }

    // Build adjacency list
    let mut neighbors: Vec<Vec<u32>> = vec![vec![]; num_nodes];
    for &(src, dst) in edges {
        neighbors[src as usize].push(dst);
    }

    // Build edge set for O(1) edge existence lookup
    let edge_set: FxHashSet<(u32, u32)> = edges.iter().copied().collect();

    // Pre-compute 1/p and 1/q to avoid repeated division
    let inv_p = 1.0 / p;
    let inv_q = 1.0 / q;

    // Master seed for deterministic per-node seeds
    let master_seed = seed.unwrap_or(42);

    // Generate walks in parallel across starting nodes
    let walks: Vec<Vec<u32>> = (0..num_nodes)
        .into_par_iter()
        .flat_map(|start_node| {
            let start = start_node as u32;

            // Skip isolated nodes (no outgoing edges)
            if neighbors[start_node].is_empty() {
                return vec![];
            }

            // Create deterministic RNG seeded by (master_seed, node_id)
            // This ensures reproducibility even with parallel execution
            let node_seed = master_seed
                .wrapping_mul(0x517cc1b727220a95)
                .wrapping_add(start_node as u64);
            let mut rng = ChaCha8Rng::seed_from_u64(node_seed);

            let mut node_walks = Vec::with_capacity(walks_per_node);

            for _ in 0..walks_per_node {
                let walk = generate_biased_walk(
                    start,
                    walk_length,
                    &neighbors,
                    &edge_set,
                    inv_p,
                    inv_q,
                    &mut rng,
                );
                node_walks.push(walk);
            }

            node_walks
        })
        .collect();

    walks
}

/// Generate a single biased random walk starting from a node.
fn generate_biased_walk(
    start: u32,
    walk_length: usize,
    neighbors: &[Vec<u32>],
    edge_set: &FxHashSet<(u32, u32)>,
    inv_p: f64,
    inv_q: f64,
    rng: &mut ChaCha8Rng,
) -> Vec<u32> {
    let mut walk = Vec::with_capacity(walk_length);
    walk.push(start);

    if walk_length == 1 {
        return walk;
    }

    // First step: uniform random choice (no previous node yet)
    let first_neighbors = &neighbors[start as usize];
    if first_neighbors.is_empty() {
        return walk;
    }
    let first_step = first_neighbors[rng.random_range(0..first_neighbors.len())];
    walk.push(first_step);

    // Subsequent steps: biased by p and q
    for _ in 2..walk_length {
        let current = *walk.last().expect("walk has at least one element");
        let previous = walk[walk.len() - 2];

        let current_neighbors = &neighbors[current as usize];
        if current_neighbors.is_empty() {
            break; // Dead end
        }

        // Compute unnormalized transition weights
        let mut weights: Vec<f64> = Vec::with_capacity(current_neighbors.len());
        let mut total_weight = 0.0;

        for &next in current_neighbors {
            let weight = if next == previous {
                // Return to previous node: weight = 1/p
                inv_p
            } else if edge_set.contains(&(previous, next)) {
                // Next is neighbor of previous: weight = 1
                1.0
            } else {
                // Next is not neighbor of previous: weight = 1/q
                inv_q
            };
            weights.push(weight);
            total_weight += weight;
        }

        // Sample next node according to weights
        if total_weight <= 0.0 {
            break;
        }

        let sample = rng.random::<f64>() * total_weight;
        let mut cumulative = 0.0;
        let mut chosen_idx = 0;

        for (i, &w) in weights.iter().enumerate() {
            cumulative += w;
            if sample < cumulative {
                chosen_idx = i;
                break;
            }
        }

        walk.push(current_neighbors[chosen_idx]);
    }

    walk
}

// ============================================================================
// WORD2VEC SKIP-GRAM WITH NEGATIVE SAMPLING
// ============================================================================

/// Internal vocabulary entry.
struct VocabEntry {
    /// Index in embedding matrix.
    index: usize,
    /// Frequency count.
    count: usize,
}

/// Noise distribution for negative sampling.
/// Uses Vose's alias method for O(1) sampling.
struct NoiseDistribution {
    /// Alias table for O(1) sampling.
    alias: Vec<usize>,
    /// Probability table.
    prob: Vec<f32>,
}

impl NoiseDistribution {
    /// Create noise distribution from frequency counts.
    /// Uses unigram distribution raised to 0.75 power (dampens frequent words).
    fn new(counts: &[usize]) -> Self {
        let n = counts.len();
        if n == 0 {
            return Self {
                alias: vec![],
                prob: vec![],
            };
        }

        // Compute unigram^0.75 probabilities
        let total: f64 = counts.iter().map(|&c| (c as f64).powf(0.75)).sum();
        let mut probs: Vec<f64> = counts
            .iter()
            .map(|&c| (c as f64).powf(0.75) / total * n as f64)
            .collect();

        // Build alias table using Vose's algorithm
        let mut small: Vec<usize> = Vec::new();
        let mut large: Vec<usize> = Vec::new();
        let mut alias = vec![0usize; n];
        let mut prob = vec![0.0f32; n];

        for (i, &p) in probs.iter().enumerate() {
            if p < 1.0 {
                small.push(i);
            } else {
                large.push(i);
            }
        }

        while !small.is_empty() && !large.is_empty() {
            let l = small
                .pop()
                .expect("small guaranteed non-empty by while condition");
            let g = large
                .pop()
                .expect("large guaranteed non-empty by while condition");

            prob[l] = probs[l] as f32;
            alias[l] = g;

            probs[g] = probs[g] + probs[l] - 1.0;

            if probs[g] < 1.0 {
                small.push(g);
            } else {
                large.push(g);
            }
        }

        // Handle remaining entries (numerical stability)
        for &g in &large {
            prob[g] = 1.0;
        }
        for &l in &small {
            prob[l] = 1.0;
        }

        Self { alias, prob }
    }

    /// Sample a random index using alias method (O(1)).
    #[inline]
    fn sample(&self, rng: &mut ChaCha8Rng) -> usize {
        if self.alias.is_empty() {
            return 0;
        }
        let i = rng.random_range(0..self.alias.len());
        if rng.random::<f32>() < self.prob[i] {
            i
        } else {
            self.alias[i]
        }
    }
}

/// Train Word2Vec skip-gram embeddings from random walks.
///
/// Sequential training that is still fast in Rust due to no interpreter
/// overhead and good cache locality. For each (center, context) pair in the
/// walks, updates embeddings via SGD with negative sampling.
///
/// # Arguments
/// * `walks` - List of random walks, where each walk is a sequence of node IDs
/// * `config` - Training configuration
///
/// # Returns
/// Training result with embeddings and statistics
pub fn train_skipgram(walks: &[Vec<u32>], config: &Word2VecConfig) -> Word2VecResult {
    // Handle empty input
    if walks.is_empty() {
        return Word2VecResult {
            embeddings: FxHashMap::default(),
            vocab_size: 0,
            samples_processed: 0,
            final_loss: 0.0,
        };
    }

    // Step 1: Build vocabulary
    let (vocab, _id_to_node) = build_vocabulary(walks, config.min_count);
    let vocab_size = vocab.len();

    if vocab_size == 0 {
        return Word2VecResult {
            embeddings: FxHashMap::default(),
            vocab_size: 0,
            samples_processed: 0,
            final_loss: 0.0,
        };
    }

    // Step 2: Create noise distribution for negative sampling
    let counts: Vec<usize> = {
        let mut counts = vec![0usize; vocab_size];
        for entry in vocab.values() {
            counts[entry.index] = entry.count;
        }
        counts
    };
    let noise_dist = NoiseDistribution::new(&counts);

    // Step 3: Initialize embedding matrices
    // W: input embeddings (center words) -- this is what we keep
    // W': output embeddings (context words) -- auxiliary
    let mut rng = ChaCha8Rng::seed_from_u64(config.seed.unwrap_or(42));

    // Xavier initialization: uniform(-sqrt(6 / (fan_in + fan_out)), ...)
    let init_range = (6.0_f32 / (2.0 * config.embedding_dim as f32)).sqrt();

    let mut w_input: Vec<f32> = (0..vocab_size * config.embedding_dim)
        .map(|_| rng.random_range(-init_range..init_range))
        .collect();

    let mut w_output: Vec<f32> = vec![0.0; vocab_size * config.embedding_dim];

    // Step 4: Count total samples for learning rate schedule
    let total_samples: u64 = walks
        .iter()
        .map(|walk| walk.iter().filter(|node| vocab.contains_key(node)).count() as u64)
        .sum();

    let total_training_samples = total_samples * config.epochs as u64;
    let mut samples_processed: u64 = 0;

    // Step 5: Training loop
    let mut final_loss = 0.0f32;

    // Gradient accumulators
    let mut grad_input = vec![0.0f32; config.embedding_dim];
    let mut grad_context = vec![0.0f32; config.embedding_dim];

    for epoch in 0..config.epochs {
        let mut epoch_loss_sum = 0.0f64;
        let mut epoch_sample_count = 0u64;

        // Shuffle walks for this epoch (deterministic with seed)
        let epoch_seed = config
            .seed
            .unwrap_or(42)
            .wrapping_mul(0x517cc1b727220a95)
            .wrapping_add(epoch as u64);
        let mut rng = ChaCha8Rng::seed_from_u64(epoch_seed);

        // Create shuffled walk indices
        let mut walk_order: Vec<usize> = (0..walks.len()).collect();
        walk_order.shuffle(&mut rng);

        for walk_idx in walk_order {
            let walk = &walks[walk_idx];

            // Filter walk to only include vocabulary words
            let walk_indices: Vec<usize> = walk
                .iter()
                .filter_map(|node| vocab.get(node).map(|e| e.index))
                .collect();

            if walk_indices.len() < 2 {
                continue;
            }

            // Slide context window
            for (pos, &center_idx) in walk_indices.iter().enumerate() {
                // Dynamic window size (like gensim)
                let actual_window = rng.random_range(1..=config.window_size);

                // Context positions
                let start = pos.saturating_sub(actual_window);
                let end = (pos + actual_window + 1).min(walk_indices.len());

                for ctx_pos in start..end {
                    if ctx_pos == pos {
                        continue;
                    }

                    let context_idx = walk_indices[ctx_pos];

                    // Compute learning rate with linear decay
                    let progress = samples_processed as f32 / total_training_samples.max(1) as f32;
                    let lr = config.learning_rate
                        - (config.learning_rate - config.min_learning_rate) * progress;
                    let lr = lr.max(config.min_learning_rate);

                    // Train on positive sample
                    epoch_loss_sum += train_pair(
                        center_idx,
                        context_idx,
                        true,
                        lr,
                        config.embedding_dim,
                        &mut w_input,
                        &mut w_output,
                        &mut grad_input,
                        &mut grad_context,
                    ) as f64;

                    // Train on negative samples
                    for _ in 0..config.negative_samples {
                        let neg_idx = noise_dist.sample(&mut rng);
                        if neg_idx != context_idx {
                            epoch_loss_sum += train_pair(
                                center_idx,
                                neg_idx,
                                false,
                                lr,
                                config.embedding_dim,
                                &mut w_input,
                                &mut w_output,
                                &mut grad_input,
                                &mut grad_context,
                            ) as f64;
                        }
                    }

                    epoch_sample_count += 1;
                    samples_processed += 1;
                }
            }
        }

        if epoch_sample_count > 0 {
            final_loss = (epoch_loss_sum / epoch_sample_count as f64) as f32;
        }
    }

    // Step 6: Extract embeddings
    let mut embeddings: FxHashMap<u32, Vec<f32>> = FxHashMap::default();
    for (node_id, entry) in &vocab {
        let start = entry.index * config.embedding_dim;
        let end = start + config.embedding_dim;
        embeddings.insert(*node_id, w_input[start..end].to_vec());
    }

    Word2VecResult {
        embeddings,
        vocab_size,
        samples_processed,
        final_loss,
    }
}

/// Build vocabulary from walks.
fn build_vocabulary(
    walks: &[Vec<u32>],
    min_count: usize,
) -> (FxHashMap<u32, VocabEntry>, Vec<u32>) {
    // Count frequencies
    let mut counts: FxHashMap<u32, usize> = FxHashMap::default();
    for walk in walks {
        for &node in walk {
            *counts.entry(node).or_insert(0) += 1;
        }
    }

    // Filter by min_count and assign indices
    let mut vocab: FxHashMap<u32, VocabEntry> = FxHashMap::default();
    let mut id_to_node: Vec<u32> = Vec::new();

    for (node, count) in counts {
        if count >= min_count {
            let index = id_to_node.len();
            vocab.insert(node, VocabEntry { index, count });
            id_to_node.push(node);
        }
    }

    (vocab, id_to_node)
}

/// Train on a single (center, context/negative) pair.
/// Returns the loss for this sample.
#[inline]
fn train_pair(
    center_idx: usize,
    target_idx: usize,
    is_positive: bool,
    lr: f32,
    dim: usize,
    w_input: &mut [f32],
    w_output: &mut [f32],
    grad_input: &mut [f32],
    grad_context: &mut [f32],
) -> f32 {
    let center_start = center_idx * dim;
    let target_start = target_idx * dim;

    // Compute dot product
    let mut dot: f32 = 0.0;
    for i in 0..dim {
        dot += w_input[center_start + i] * w_output[target_start + i];
    }

    // Sigmoid with clamping for numerical stability
    let dot_clamped = dot.clamp(-10.0, 10.0);
    let sigmoid = 1.0 / (1.0 + (-dot_clamped).exp());

    // Label: 1 for positive, 0 for negative
    let label = if is_positive { 1.0 } else { 0.0 };

    // Gradient: (sigmoid - label)
    let grad = sigmoid - label;

    // Loss: -log(sigmoid) for positive, -log(1-sigmoid) for negative
    let loss = if is_positive {
        -sigmoid.max(1e-10).ln()
    } else {
        -(1.0 - sigmoid).max(1e-10).ln()
    };

    // Compute gradients
    for i in 0..dim {
        grad_input[i] = grad * w_output[target_start + i];
        grad_context[i] = grad * w_input[center_start + i];
    }

    // Update embeddings (SGD step)
    for i in 0..dim {
        w_input[center_start + i] -= lr * grad_input[i];
        w_output[target_start + i] -= lr * grad_context[i];
    }

    loss
}

// ============================================================================
// UNIT TESTS
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    // ====================================================================
    // NODE2VEC TESTS
    // ====================================================================

    #[test]
    fn test_node2vec_walks_basic() {
        // Triangle graph: 0 -> 1, 1 -> 2, 2 -> 0
        let edges = vec![(0, 1), (1, 2), (2, 0)];
        let walks = node2vec_random_walks(&edges, 3, 5, 2, 1.0, 1.0, Some(42));

        // 3 nodes, each with 2 walks
        assert_eq!(walks.len(), 6, "Should produce 6 walks (3 nodes * 2 walks)");

        // Each walk should be at most walk_length
        for walk in &walks {
            assert!(!walk.is_empty(), "Walk should not be empty");
            assert!(walk.len() <= 5, "Walk length {} exceeds max 5", walk.len());
        }
    }

    #[test]
    fn test_node2vec_deterministic_with_seed() {
        let edges = vec![(0, 1), (1, 2), (2, 0), (0, 2), (1, 0), (2, 1)];

        let walks1 = node2vec_random_walks(&edges, 3, 10, 3, 1.0, 1.0, Some(12345));
        let walks2 = node2vec_random_walks(&edges, 3, 10, 3, 1.0, 1.0, Some(12345));

        assert_eq!(
            walks1.len(),
            walks2.len(),
            "Same seed should produce same number of walks"
        );
        for (w1, w2) in walks1.iter().zip(walks2.iter()) {
            assert_eq!(w1, w2, "Same seed should produce identical walks");
        }
    }

    #[test]
    fn test_node2vec_empty_graph() {
        // 0 nodes
        let walks = node2vec_random_walks(&[], 0, 10, 3, 1.0, 1.0, Some(42));
        assert!(walks.is_empty(), "Empty graph should return no walks");

        // Nodes but no edges (all isolated)
        let walks = node2vec_random_walks(&[], 5, 10, 3, 1.0, 1.0, Some(42));
        assert!(
            walks.is_empty(),
            "Graph with only isolated nodes should return no walks"
        );
    }

    #[test]
    fn test_node2vec_walk_length_one() {
        let edges = vec![(0, 1), (1, 0)];
        let walks = node2vec_random_walks(&edges, 2, 1, 2, 1.0, 1.0, Some(42));

        for walk in &walks {
            assert_eq!(
                walk.len(),
                1,
                "Walk with length 1 should have exactly 1 node"
            );
        }
    }

    #[test]
    fn test_node2vec_p_q_bias() {
        // Star graph: 0 is hub, 1-4 are leaves
        // 0 -> 1, 0 -> 2, 0 -> 3, 0 -> 4
        // 1 -> 0, 2 -> 0, 3 -> 0, 4 -> 0
        let edges = vec![
            (0, 1),
            (0, 2),
            (0, 3),
            (0, 4),
            (1, 0),
            (2, 0),
            (3, 0),
            (4, 0),
        ];

        // High p = less backtracking (explore outward)
        let walks_high_p = node2vec_random_walks(&edges, 5, 20, 10, 4.0, 1.0, Some(42));
        // Low p = more backtracking
        let walks_low_p = node2vec_random_walks(&edges, 5, 20, 10, 0.25, 1.0, Some(42));

        // Both should produce valid walks
        assert!(!walks_high_p.is_empty());
        assert!(!walks_low_p.is_empty());
    }

    // ====================================================================
    // WORD2VEC TESTS
    // ====================================================================

    #[test]
    fn test_word2vec_produces_embeddings() {
        // Generate walks from a small triangle graph
        let edges = vec![(0, 1), (1, 2), (2, 0), (0, 2), (1, 0), (2, 1)];
        let walks = node2vec_random_walks(&edges, 3, 10, 5, 1.0, 1.0, Some(42));

        let config = Word2VecConfig {
            embedding_dim: 32,
            window_size: 3,
            epochs: 3,
            seed: Some(42),
            ..Default::default()
        };

        let result = train_skipgram(&walks, &config);

        assert!(result.vocab_size > 0, "Should have non-empty vocabulary");
        assert!(!result.embeddings.is_empty(), "Should produce embeddings");
        assert!(
            result.samples_processed > 0,
            "Should process training samples"
        );

        // All three nodes should have embeddings
        for node in 0..3u32 {
            assert!(
                result.embeddings.contains_key(&node),
                "Missing embedding for node {}",
                node
            );
        }
    }

    #[test]
    fn test_word2vec_embedding_dimension() {
        let walks = vec![
            vec![0, 1, 2, 3, 4],
            vec![4, 3, 2, 1, 0],
            vec![2, 1, 3, 4, 0],
        ];

        for dim in [8, 16, 64, 128] {
            let config = Word2VecConfig {
                embedding_dim: dim,
                epochs: 1,
                seed: Some(42),
                ..Default::default()
            };

            let result = train_skipgram(&walks, &config);

            for (node_id, emb) in &result.embeddings {
                assert_eq!(
                    emb.len(),
                    dim,
                    "Node {} has embedding dim {} but expected {}",
                    node_id,
                    emb.len(),
                    dim
                );
            }
        }
    }

    #[test]
    fn test_word2vec_empty_walks() {
        let walks: Vec<Vec<u32>> = vec![];
        let config = Word2VecConfig::default();
        let result = train_skipgram(&walks, &config);

        assert!(result.embeddings.is_empty());
        assert_eq!(result.vocab_size, 0);
        assert_eq!(result.samples_processed, 0);
    }

    #[test]
    fn test_word2vec_determinism() {
        let walks = vec![
            vec![0, 1, 2, 3, 4],
            vec![4, 3, 2, 1, 0],
            vec![2, 1, 3, 4, 2, 1, 0],
        ];

        let config = Word2VecConfig {
            embedding_dim: 16,
            epochs: 2,
            seed: Some(12345),
            ..Default::default()
        };

        let result1 = train_skipgram(&walks, &config);
        let result2 = train_skipgram(&walks, &config);

        // Same seed should produce same embeddings
        for node in 0..5u32 {
            let emb1 = &result1.embeddings[&node];
            let emb2 = &result2.embeddings[&node];
            for (a, b) in emb1.iter().zip(emb2.iter()) {
                assert!(
                    (a - b).abs() < 1e-6,
                    "Embeddings differ for node {}: {} vs {}",
                    node,
                    a,
                    b
                );
            }
        }
    }

    #[test]
    fn test_word2vec_cluster_similarity() {
        // Two clusters: {0,1,2} and {3,4,5} with rare cross-cluster walks
        let walks = vec![
            // Cluster 1
            vec![0, 1, 2, 1, 0, 1, 2],
            vec![1, 0, 1, 2, 1, 0],
            vec![2, 1, 0, 1, 2],
            // Cluster 2
            vec![3, 4, 5, 4, 3, 4, 5],
            vec![4, 3, 4, 5, 4, 3],
            vec![5, 4, 3, 4, 5],
            // Rare cross-cluster
            vec![2, 3],
        ];

        let config = Word2VecConfig {
            embedding_dim: 32,
            window_size: 3,
            epochs: 10,
            learning_rate: 0.05,
            seed: Some(42),
            ..Default::default()
        };

        let result = train_skipgram(&walks, &config);

        fn cosine_sim(a: &[f32], b: &[f32]) -> f32 {
            let dot: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
            let norm_a: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
            let norm_b: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
            if norm_a > 0.0 && norm_b > 0.0 {
                dot / (norm_a * norm_b)
            } else {
                0.0
            }
        }

        let sim_01 = cosine_sim(&result.embeddings[&0], &result.embeddings[&1]);
        let sim_34 = cosine_sim(&result.embeddings[&3], &result.embeddings[&4]);
        let sim_03 = cosine_sim(&result.embeddings[&0], &result.embeddings[&3]);

        assert!(
            sim_01 > sim_03,
            "Within-cluster similarity ({}) should be higher than cross-cluster ({})",
            sim_01,
            sim_03
        );
        assert!(
            sim_34 > sim_03,
            "Within-cluster similarity ({}) should be higher than cross-cluster ({})",
            sim_34,
            sim_03
        );
    }
}
