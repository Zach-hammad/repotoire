# TurboQuant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement TurboQuant (4-bit) vector quantization in Rust and benchmark recall@k on node2vec embeddings.

**Architecture:** Random orthogonal rotation (QR decomposition via nalgebra) + coordinate-wise Lloyd-Max scalar quantization. ADC (asymmetric distance computation) for fast kNN search. Benchmark against brute-force kNN on repotoire's own code graph embeddings.

**Tech Stack:** Rust, nalgebra (QR), rand_chacha (seeded RNG), existing node2vec in `predictive/embeddings.rs`.

**Spec:** `docs/superpowers/specs/2026-03-30-turboquant-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/quantize/mod.rs` | Module root: `pub mod turbo_quant;` |
| `src/quantize/turbo_quant.rs` | TurboQuantCodebook, QuantizedVector, ADC, kNN, pack/unpack, Lloyd-Max |

### Modified Files
| File | Changes |
|------|---------|
| `src/lib.rs` | Add `pub mod quantize;` |
| `Cargo.toml` | Add `nalgebra = "0.33"` |

---

### Task 1: Add nalgebra dependency + module skeleton

**Files:**
- Modify: `Cargo.toml`
- Modify: `src/lib.rs`
- Create: `src/quantize/mod.rs`
- Create: `src/quantize/turbo_quant.rs`

- [ ] **Step 1: Add nalgebra to Cargo.toml**

Add under `[dependencies]` (alphabetical):
```toml
nalgebra = "0.33"
```

- [ ] **Step 2: Add module declaration to `src/lib.rs`**

Add alongside other `pub mod` declarations:
```rust
pub mod quantize;
```

- [ ] **Step 3: Create `src/quantize/mod.rs`**

```rust
//! TurboQuant vector quantization (Zandieh et al. 2025).
pub mod turbo_quant;
```

- [ ] **Step 4: Create `src/quantize/turbo_quant.rs` with empty structs**

```rust
//! TurboQuant: near-optimal vector quantization via random rotation + scalar quantization.
//!
//! Algorithm: rotate by random orthogonal matrix, quantize each coordinate
//! independently using a Lloyd-Max codebook optimized for the Beta distribution
//! of unit-sphere coordinates.
//!
//! Reference: Zandieh et al. 2025, "TurboQuant: Online Vector Quantization
//! with Near-optimal Distortion Rate" (arXiv:2504.19874)

use nalgebra::DMatrix;

/// Configuration for TurboQuant quantizer.
#[derive(Debug, Clone)]
pub struct TurboQuantConfig {
    /// Vector dimension (default: 128).
    pub dim: usize,
    /// Bits per coordinate (default: 4).
    pub bits: usize,
    /// RNG seed for reproducible rotation matrix.
    pub seed: u64,
}

impl Default for TurboQuantConfig {
    fn default() -> Self {
        Self { dim: 128, bits: 4, seed: 42 }
    }
}

/// Precomputed quantization state: rotation matrix + codebook.
pub struct TurboQuantCodebook {
    pub(crate) rotation: DMatrix<f64>,
    pub(crate) rotation_t: DMatrix<f64>,
    centroids: Vec<f64>,
    boundaries: Vec<f64>,
    dim: usize,
    bits: usize,
    num_levels: usize,
}

/// A quantized vector: packed codebook indices + original norm.
#[derive(Debug, Clone)]
pub struct QuantizedVector {
    /// Packed 4-bit indices, 2 per byte, lower nibble first.
    pub indices: Vec<u8>,
    /// Original L2 norm (preserved for reconstruction).
    pub norm: f64,
}

/// Precomputed ADC distance table for a single query.
pub struct DistanceTable {
    /// Flat array: table[j * num_levels + k] = (q_rot[j] - centroid[k])^2
    table: Vec<f64>,
    num_levels: usize,
}
```

- [ ] **Step 5: Verify it compiles**

Run: `cargo check`
Expected: compiles clean (structs exist but no methods yet)

- [ ] **Step 6: Commit**

```bash
git add Cargo.toml src/lib.rs src/quantize/
git commit -m "feat(quantize): add TurboQuant module skeleton with nalgebra"
```

---

### Task 2: Bit packing + Lloyd-Max codebook

**Files:**
- Modify: `src/quantize/turbo_quant.rs`

- [ ] **Step 1: Write pack/unpack tests**

Add at the bottom of `turbo_quant.rs`:

```rust
/// Pack 4-bit indices (0-15) into bytes, two per byte, lower nibble first.
pub fn pack_4bit(indices: &[u8]) -> Vec<u8> {
    assert!(indices.len() % 2 == 0, "indices length must be even");
    indices
        .chunks_exact(2)
        .map(|pair| (pair[0] & 0x0F) | (pair[1] << 4))
        .collect()
}

/// Unpack bytes into 4-bit indices.
pub fn unpack_4bit(packed: &[u8], dim: usize) -> Vec<u8> {
    assert_eq!(packed.len(), dim / 2);
    let mut out = Vec::with_capacity(dim);
    for &byte in packed {
        out.push(byte & 0x0F);
        out.push(byte >> 4);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pack_unpack_roundtrip() {
        let indices: Vec<u8> = (0..128).map(|i| (i % 16) as u8).collect();
        let packed = pack_4bit(&indices);
        assert_eq!(packed.len(), 64);
        let unpacked = unpack_4bit(&packed, 128);
        assert_eq!(unpacked, indices);
    }

    #[test]
    fn test_pack_boundary_values() {
        let indices = vec![0u8, 15, 7, 8];
        let packed = pack_4bit(&indices);
        assert_eq!(packed, vec![0xF0, 0x87]);
        let unpacked = unpack_4bit(&packed, 4);
        assert_eq!(unpacked, indices);
    }
}
```

- [ ] **Step 2: Run tests**

Run: `cargo test quantize::turbo_quant::tests -- --nocapture`
Expected: 2 tests pass

- [ ] **Step 3: Add Lloyd-Max codebook constants**

Add above the tests:

```rust
/// Lloyd-Max optimal centroids for N(0, 1/d) at 4-bit (16 levels).
///
/// These are standard N(0,1) Lloyd-Max centroids scaled by 1/sqrt(d).
/// Source: optimal 4-bit Gaussian quantizer (Lloyd 1982).
fn lloyd_max_codebook_4bit(dim: usize) -> (Vec<f64>, Vec<f64>) {
    // Standard N(0,1) Lloyd-Max centroids for 4-bit (16 levels), sorted ascending
    let std_centroids = [
        -2.7326, -2.0690, -1.6180, -1.2562,
        -0.9424, -0.6568, -0.3882, -0.1284,
         0.1284,  0.3882,  0.6568,  0.9424,
         1.2562,  1.6180,  2.0690,  2.7326,
    ];

    let scale = 1.0 / (dim as f64).sqrt();
    let centroids: Vec<f64> = std_centroids.iter().map(|&c| c * scale).collect();

    // Decision boundaries = midpoints between adjacent centroids
    let boundaries: Vec<f64> = centroids
        .windows(2)
        .map(|w| (w[0] + w[1]) / 2.0)
        .collect();

    (centroids, boundaries)
}

/// Find the nearest centroid index for a scalar value.
pub(crate) fn quantize_scalar(value: f64, boundaries: &[f64]) -> u8 {
    // Binary search: find first boundary > value
    match boundaries.binary_search_by(|b| b.partial_cmp(&value).unwrap()) {
        Ok(i) => i as u8 + 1,  // value == boundary → round up
        Err(i) => i as u8,      // value falls before boundary[i]
    }
}
```

- [ ] **Step 4: Add codebook tests**

```rust
    #[test]
    fn test_codebook_has_16_centroids() {
        let (centroids, boundaries) = lloyd_max_codebook_4bit(128);
        assert_eq!(centroids.len(), 16);
        assert_eq!(boundaries.len(), 15);
    }

    #[test]
    fn test_codebook_symmetric() {
        let (centroids, _) = lloyd_max_codebook_4bit(128);
        for i in 0..8 {
            assert!((centroids[i] + centroids[15 - i]).abs() < 1e-10,
                "centroids should be symmetric: {} vs {}", centroids[i], centroids[15 - i]);
        }
    }

    #[test]
    fn test_codebook_sorted() {
        let (centroids, boundaries) = lloyd_max_codebook_4bit(128);
        for w in centroids.windows(2) {
            assert!(w[0] < w[1], "centroids must be ascending");
        }
        for w in boundaries.windows(2) {
            assert!(w[0] < w[1], "boundaries must be ascending");
        }
    }

    #[test]
    fn test_quantize_scalar_center() {
        let (centroids, boundaries) = lloyd_max_codebook_4bit(128);
        // Value at centroid[8] (first positive) should map to index 8
        let idx = quantize_scalar(centroids[8], &boundaries);
        assert_eq!(idx, 8);
    }

    #[test]
    fn test_quantize_scalar_extreme() {
        let (_, boundaries) = lloyd_max_codebook_4bit(128);
        // Very negative → index 0
        assert_eq!(quantize_scalar(-1.0, &boundaries), 0);
        // Very positive → index 15
        assert_eq!(quantize_scalar(1.0, &boundaries), 15);
    }
```

- [ ] **Step 5: Run all tests**

Run: `cargo test quantize::turbo_quant -- --nocapture`
Expected: 7 tests pass

- [ ] **Step 6: Commit**

```bash
git add src/quantize/turbo_quant.rs
git commit -m "feat(quantize): add bit packing and Lloyd-Max codebook for 4-bit"
```

- [ ] **Step 7: Add naive uniform codebook for baseline comparison**

```rust
/// Naive uniform scalar quantizer for baseline comparison.
/// Uniform grid over [-3/sqrt(d), 3/sqrt(d)] with 2^b levels.
pub(crate) fn uniform_codebook_4bit(dim: usize) -> (Vec<f64>, Vec<f64>) {
    let range = 3.0 / (dim as f64).sqrt();
    let num_levels = 16usize;
    let step = 2.0 * range / num_levels as f64;
    let centroids: Vec<f64> = (0..num_levels)
        .map(|i| -range + step * (i as f64 + 0.5))
        .collect();
    let boundaries: Vec<f64> = centroids
        .windows(2)
        .map(|w| (w[0] + w[1]) / 2.0)
        .collect();
    (centroids, boundaries)
}
```

The `TurboQuantCodebook::new()` in Task 3 will accept a flag or the benchmark will construct a second codebook with uniform centroids for comparison.

---

### Task 3: Rotation matrix + quantize/reconstruct

**Files:**
- Modify: `src/quantize/turbo_quant.rs`

- [ ] **Step 1: Implement `TurboQuantCodebook::new()`**

```rust
use rand::SeedableRng;
use rand_chacha::ChaCha8Rng;
use rand::distributions::{Distribution, Standard};

impl TurboQuantCodebook {
    /// Create a new quantizer. Precomputes rotation matrix via QR and codebook.
    pub fn new(config: TurboQuantConfig) -> Self {
        let d = config.dim;
        let b = config.bits;
        let num_levels = 1 << b;

        // Generate random d×d Gaussian matrix, seeded for reproducibility
        let mut rng = ChaCha8Rng::seed_from_u64(config.seed);
        let data: Vec<f64> = (0..d * d).map(|_| {
            let u1: f64 = rng.random();
            let u2: f64 = rng.random();
            // Box-Muller transform for N(0,1)
            (-2.0 * (1.0 - u1).ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
        }).collect();
        let g = DMatrix::from_vec(d, d, data);

        // QR decomposition → Q is our rotation matrix
        let qr = g.qr();
        let rotation = qr.q();
        let rotation_t = rotation.transpose();

        // Precompute codebook
        let (centroids, boundaries) = lloyd_max_codebook_4bit(d);

        Self {
            rotation,
            rotation_t,
            centroids,
            boundaries,
            dim: d,
            bits: b,
            num_levels,
        }
    }
}
```

- [ ] **Step 2: Implement quantize()**

```rust
    /// Quantize a raw vector. Normalizes, rotates, scalar-quantizes, packs.
    pub fn quantize(&self, x: &[f64]) -> QuantizedVector {
        assert_eq!(x.len(), self.dim);

        // L2 normalize
        let norm: f64 = x.iter().map(|v| v * v).sum::<f64>().sqrt();
        let inv_norm = if norm > 0.0 { 1.0 / norm } else { 1.0 };

        // Rotate: y = R * x_hat
        let x_vec = nalgebra::DVector::from_iterator(self.dim, x.iter().map(|v| v * inv_norm));
        let y = &self.rotation * &x_vec;

        // Scalar quantize each coordinate
        let indices: Vec<u8> = (0..self.dim)
            .map(|j| quantize_scalar(y[j], &self.boundaries))
            .collect();

        QuantizedVector {
            indices: pack_4bit(&indices),
            norm,
        }
    }
```

- [ ] **Step 3: Implement reconstruct()**

```rust
    /// Reconstruct a quantized vector (lossy).
    pub fn reconstruct(&self, qv: &QuantizedVector) -> Vec<f64> {
        let indices = unpack_4bit(&qv.indices, self.dim);

        // Lookup centroids
        let y_hat: Vec<f64> = indices.iter().map(|&idx| self.centroids[idx as usize]).collect();
        let y_vec = nalgebra::DVector::from_vec(y_hat);

        // Rotate back: x_hat = R^T * y_hat
        let x_hat = &self.rotation_t * &y_vec;

        // Scale by original norm
        x_hat.iter().map(|v| v * qv.norm).collect()
    }
```

- [ ] **Step 4: Write round-trip tests**

```rust
    #[test]
    fn test_rotation_orthogonal() {
        let cb = TurboQuantCodebook::new(TurboQuantConfig::default());
        let product = &cb.rotation_t * &cb.rotation;
        let identity = DMatrix::identity(128, 128);
        let diff = (&product - &identity).norm();
        assert!(diff < 1e-10, "R^T * R should be identity, diff = {diff}");
    }

    #[test]
    fn test_quantize_reconstruct_cosine() {
        let cb = TurboQuantCodebook::new(TurboQuantConfig::default());

        // Random vector
        let mut rng = ChaCha8Rng::seed_from_u64(123);
        let x: Vec<f64> = (0..128).map(|_| {
            let u1: f64 = rng.random();
            let u2: f64 = rng.random();
            (-2.0 * (1.0 - u1).ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
        }).collect();

        let qv = cb.quantize(&x);
        let x_hat = cb.reconstruct(&qv);

        // Cosine similarity
        let dot: f64 = x.iter().zip(&x_hat).map(|(a, b)| a * b).sum();
        let norm_x = x.iter().map(|v| v * v).sum::<f64>().sqrt();
        let norm_xh = x_hat.iter().map(|v| v * v).sum::<f64>().sqrt();
        let cos_sim = dot / (norm_x * norm_xh);

        assert!(cos_sim > 0.99, "4-bit cosine should be > 0.99, got {cos_sim}");
    }

    #[test]
    fn test_quantize_preserves_norm() {
        let cb = TurboQuantCodebook::new(TurboQuantConfig::default());
        let x: Vec<f64> = (0..128).map(|i| (i as f64) * 0.1).collect();
        let qv = cb.quantize(&x);
        let x_hat = cb.reconstruct(&qv);

        let norm_x = x.iter().map(|v| v * v).sum::<f64>().sqrt();
        let norm_xh = x_hat.iter().map(|v| v * v).sum::<f64>().sqrt();

        let rel_err = (norm_x - norm_xh).abs() / norm_x;
        assert!(rel_err < 0.1, "norm should be approximately preserved, rel_err = {rel_err}");
    }
```

- [ ] **Step 5: Run tests**

Run: `cargo test quantize::turbo_quant -- --nocapture`
Expected: 10 tests pass

- [ ] **Step 6: Commit**

```bash
git add src/quantize/turbo_quant.rs
git commit -m "feat(quantize): implement TurboQuant quantize/reconstruct with rotation matrix"
```

---

### Task 4: ADC distance + kNN search

**Files:**
- Modify: `src/quantize/turbo_quant.rs`

- [ ] **Step 1: Implement build_distance_table()**

```rust
    /// Precompute ADC distance table for a query. Normalizes + rotates the query once.
    pub fn build_distance_table(&self, query: &[f64]) -> DistanceTable {
        assert_eq!(query.len(), self.dim);

        // Normalize query
        let norm: f64 = query.iter().map(|v| v * v).sum::<f64>().sqrt();
        let inv_norm = if norm > 0.0 { 1.0 / norm } else { 1.0 };

        // Rotate: q_rot = R * q_hat
        let q_vec = nalgebra::DVector::from_iterator(self.dim, query.iter().map(|v| v * inv_norm));
        let q_rot = &self.rotation * &q_vec;

        // Build table: table[j * num_levels + k] = (q_rot[j] - centroid[k])^2
        let mut table = Vec::with_capacity(self.dim * self.num_levels);
        for j in 0..self.dim {
            for k in 0..self.num_levels {
                let diff = q_rot[j] - self.centroids[k];
                table.push(diff * diff);
            }
        }

        DistanceTable {
            table,
            num_levels: self.num_levels,
        }
    }
```

- [ ] **Step 2: Implement adc_distance()**

```rust
    /// Approximate squared L2 distance between normalized query and quantized vector.
    /// For cosine similarity: cos_sim ≈ 1 - adc_distance() / 2
    pub fn adc_distance(&self, table: &DistanceTable, qv: &QuantizedVector) -> f64 {
        let indices = unpack_4bit(&qv.indices, self.dim);
        let mut dist = 0.0;
        for j in 0..self.dim {
            dist += table.table[j * table.num_levels + indices[j] as usize];
        }
        dist
    }
```

- [ ] **Step 3: Implement knn_search()**

```rust
    /// Brute-force kNN search over quantized database using ADC.
    /// Returns (index, approximate_cosine_similarity) sorted descending (most similar first).
    pub fn knn_search(
        &self,
        query: &[f64],
        database: &[QuantizedVector],
        k: usize,
    ) -> Vec<(usize, f64)> {
        let table = self.build_distance_table(query);
        let mut results: Vec<(usize, f64)> = database
            .iter()
            .enumerate()
            .map(|(i, qv)| {
                let dist_sq = self.adc_distance(&table, qv);
                let cos_sim = 1.0 - dist_sq / 2.0;
                (i, cos_sim)
            })
            .collect();
        // Sort descending by cosine similarity
        results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        results.truncate(k);
        results
    }
```

- [ ] **Step 4: Write ADC tests**

```rust
    #[test]
    fn test_adc_matches_reconstruct() {
        let cb = TurboQuantCodebook::new(TurboQuantConfig::default());
        let mut rng = ChaCha8Rng::seed_from_u64(456);

        let query: Vec<f64> = (0..128).map(|_| {
            let u1: f64 = rng.random();
            let u2: f64 = rng.random();
            (-2.0 * (1.0 - u1).ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
        }).collect();
        let x: Vec<f64> = (0..128).map(|_| {
            let u1: f64 = rng.random();
            let u2: f64 = rng.random();
            (-2.0 * (1.0 - u1).ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
        }).collect();

        let qv = cb.quantize(&x);

        // ADC distance
        let table = cb.build_distance_table(&query);
        let adc_dist = cb.adc_distance(&table, &qv);

        // Reconstruct-based distance
        let x_hat = cb.reconstruct(&qv);
        let q_norm: f64 = query.iter().map(|v| v * v).sum::<f64>().sqrt();
        let xh_norm: f64 = x_hat.iter().map(|v| v * v).sum::<f64>().sqrt();
        let q_hat: Vec<f64> = query.iter().map(|v| v / q_norm).collect();
        let xh_hat: Vec<f64> = x_hat.iter().map(|v| v / xh_norm).collect();
        let direct_dist: f64 = q_hat.iter().zip(&xh_hat).map(|(a, b)| (a - b).powi(2)).sum();

        assert!((adc_dist - direct_dist).abs() < 1e-6,
            "ADC should match direct distance: adc={adc_dist}, direct={direct_dist}");
    }

    #[test]
    fn test_knn_returns_k_results() {
        let cb = TurboQuantCodebook::new(TurboQuantConfig::default());
        let mut rng = ChaCha8Rng::seed_from_u64(789);

        let gen_vec = |rng: &mut ChaCha8Rng| -> Vec<f64> {
            (0..128).map(|_| {
                let u1: f64 = rng.random();
                let u2: f64 = rng.random();
                (-2.0 * (1.0 - u1).ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
            }).collect()
        };

        let database: Vec<QuantizedVector> = (0..100).map(|_| cb.quantize(&gen_vec(&mut rng))).collect();
        let query = gen_vec(&mut rng);

        let results = cb.knn_search(&query, &database, 10);
        assert_eq!(results.len(), 10);

        // Verify descending order
        for w in results.windows(2) {
            assert!(w[0].1 >= w[1].1, "results should be sorted descending by similarity");
        }
    }
```

- [ ] **Step 5: Run tests**

Run: `cargo test quantize::turbo_quant -- --nocapture`
Expected: 12 tests pass

- [ ] **Step 6: Commit**

```bash
git add src/quantize/turbo_quant.rs
git commit -m "feat(quantize): implement ADC distance and kNN search"
```

---

### Task 5: Benchmark harness

**Files:**
- Modify: `src/quantize/mod.rs` (add `pub mod bench;` behind `#[cfg(test)]`)
- Create: `src/quantize/bench.rs`

- [ ] **Step 1: Add bench module declaration**

In `src/quantize/mod.rs`:
```rust
pub mod turbo_quant;

#[cfg(test)]
mod bench;
```

- [ ] **Step 2: Create `src/quantize/bench.rs`**

```rust
//! TurboQuant benchmark: recall@k on real node2vec embeddings.
//!
//! Run: cargo test bench_turboquant -- --ignored --nocapture

use crate::engine::stages::{collect, parse, graph};
use crate::predictive::embeddings::{node2vec_random_walks, train_skipgram, Word2VecConfig};
use crate::quantize::turbo_quant::{
    TurboQuantCodebook, TurboQuantConfig, quantize_scalar, uniform_codebook_4bit,
};
use std::path::Path;
use std::time::Instant;

/// Brute-force exact kNN on raw vectors (ground truth).
fn exact_knn(query: &[f64], database: &[Vec<f64>], k: usize) -> Vec<usize> {
    let q_norm: f64 = query.iter().map(|v| v * v).sum::<f64>().sqrt();
    let mut sims: Vec<(usize, f64)> = database
        .iter()
        .enumerate()
        .map(|(i, x)| {
            let x_norm: f64 = x.iter().map(|v| v * v).sum::<f64>().sqrt();
            let dot: f64 = query.iter().zip(x).map(|(a, b)| a * b).sum();
            let cos = if q_norm > 0.0 && x_norm > 0.0 { dot / (q_norm * x_norm) } else { 0.0 };
            (i, cos)
        })
        .collect();
    sims.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    sims.truncate(k);
    sims.into_iter().map(|(i, _)| i).collect()
}

/// Compute recall@k: fraction of true top-k found in approximate top-k.
fn recall_at_k(exact: &[usize], approx: &[(usize, f64)]) -> f64 {
    let approx_set: std::collections::HashSet<usize> = approx.iter().map(|(i, _)| *i).collect();
    let hits = exact.iter().filter(|i| approx_set.contains(i)).count();
    hits as f64 / exact.len() as f64
}

#[test]
#[ignore] // Run: cargo test bench_turboquant -- --ignored --nocapture
fn bench_turboquant() {
    println!("=== TurboQuant Benchmark ===\n");

    // Step 1: Build code graph from repotoire's own source
    let repo_path = Path::new(env!("CARGO_MANIFEST_DIR"));
    println!("Building code graph from {repo_path:?}...");
    let t0 = Instant::now();

    let collect_out = collect::collect_stage(&collect::CollectInput {
        repo_path,
        exclude_patterns: &[],
        max_files: 10_000,
    }).expect("collect failed");

    let parse_out = parse::parse_stage(&parse::ParseInput {
        files: collect_out.all_paths(),
        workers: 8,
        progress: None,
    }).expect("parse failed");

    let graph_out = graph::graph_stage(&graph::GraphInput {
        parse_results: &parse_out.results,
        repo_path,
    }).expect("graph failed");

    let frozen = graph::freeze_graph(graph_out.mutable_graph, graph_out.value_store, None);
    let code_graph = frozen.graph;

    println!("Graph built in {:.1}s", t0.elapsed().as_secs_f64());

    // Step 2: Extract edges and run node2vec
    let call_edges = code_graph.all_call_edges();
    let edges: Vec<(u32, u32)> = call_edges
        .iter()
        .map(|(a, b)| (a.index() as u32, b.index() as u32))
        .collect();
    let num_nodes = code_graph.node_count();
    println!("Nodes: {num_nodes}, Call edges: {}", edges.len());

    let walks = node2vec_random_walks(&edges, num_nodes, 20, 10, 1.0, 1.0, Some(42));
    println!("Walks generated: {}", walks.len());

    let w2v = train_skipgram(&walks, &Word2VecConfig {
        embedding_dim: 128,
        seed: Some(42),
        ..Default::default()
    });
    println!("Embeddings: {} vectors of {}D\n", w2v.embeddings.len(), 128);

    if w2v.embeddings.len() < 100 {
        println!("Too few embeddings for meaningful benchmark, skipping.");
        return;
    }

    // Step 3: Convert to Vec<Vec<f64>> for benchmark
    let mut node_ids: Vec<u32> = w2v.embeddings.keys().copied().collect();
    node_ids.sort();
    let raw_vecs: Vec<Vec<f64>> = node_ids
        .iter()
        .map(|id| w2v.embeddings[id].iter().map(|&v| v as f64).collect())
        .collect();

    // Step 4: Quantize all vectors
    let cb = TurboQuantCodebook::new(TurboQuantConfig::default());
    let t1 = Instant::now();
    let quantized: Vec<_> = raw_vecs.iter().map(|v| cb.quantize(v)).collect();
    let quant_time = t1.elapsed();
    println!("Quantization: {:.1}ms for {} vectors", quant_time.as_secs_f64() * 1000.0, quantized.len());

    // Step 5: Measure compression
    let raw_bytes = raw_vecs.len() * 128 * 8; // f64
    let quant_bytes = quantized.len() * (64 + 8); // 64 bytes indices + 8 bytes norm
    let ratio = raw_bytes as f64 / quant_bytes as f64;
    println!("Compression: {raw_bytes} → {quant_bytes} bytes ({ratio:.1}x)\n");

    // Step 6: Measure cosine similarity + MSE of reconstructed vectors
    let mut total_cos = 0.0;
    let mut total_mse = 0.0;
    for (raw, qv) in raw_vecs.iter().zip(&quantized) {
        let recon = cb.reconstruct(qv);
        let dot: f64 = raw.iter().zip(&recon).map(|(a, b)| a * b).sum();
        let n1: f64 = raw.iter().map(|v| v * v).sum::<f64>().sqrt();
        let n2: f64 = recon.iter().map(|v| v * v).sum::<f64>().sqrt();
        if n1 > 0.0 && n2 > 0.0 {
            total_cos += dot / (n1 * n2);
        }
        let mse: f64 = raw.iter().zip(&recon).map(|(a, b)| (a - b).powi(2)).sum::<f64>() / raw.len() as f64;
        total_mse += mse;
    }
    let avg_cos = total_cos / raw_vecs.len() as f64;
    let avg_mse = total_mse / raw_vecs.len() as f64;
    println!("Avg cosine similarity: {avg_cos:.4}");
    println!("Avg MSE: {avg_mse:.6}");

    // Step 6b: Naive baseline — uniform codebook with same rotation
    // Construct a codebook using uniform centroids instead of Lloyd-Max
    // (Reuse the same TurboQuantCodebook but swap centroids — or build a second one)
    // For simplicity, quantize with a uniform grid and measure cosine for comparison
    let (uniform_centroids, uniform_boundaries) = uniform_codebook_4bit(128);
    let mut naive_cos = 0.0;
    let naive_quantized: Vec<_> = raw_vecs.iter().map(|x| {
        // Reuse cb's rotation but with uniform boundaries
        let norm: f64 = x.iter().map(|v| v * v).sum::<f64>().sqrt();
        let inv_norm = if norm > 0.0 { 1.0 / norm } else { 1.0 };
        let x_vec = nalgebra::DVector::from_iterator(128, x.iter().map(|v| v * inv_norm));
        let y = &cb.rotation * &x_vec; // same rotation
        let indices: Vec<u8> = (0..128)
            .map(|j| quantize_scalar(y[j], &uniform_boundaries))
            .collect();
        // Reconstruct with uniform centroids
        let y_hat: Vec<f64> = indices.iter().map(|&idx| uniform_centroids[idx as usize]).collect();
        let y_vec = nalgebra::DVector::from_vec(y_hat);
        let x_hat = &cb.rotation_t * &y_vec;
        let recon: Vec<f64> = x_hat.iter().map(|v| v * norm).collect();
        let dot: f64 = x.iter().zip(&recon).map(|(a, b)| a * b).sum();
        let n1: f64 = x.iter().map(|v| v * v).sum::<f64>().sqrt();
        let n2: f64 = recon.iter().map(|v| v * v).sum::<f64>().sqrt();
        if n1 > 0.0 && n2 > 0.0 { naive_cos += dot / (n1 * n2); }
        cb.quantize(x) // quantize normally for kNN comparison below
    }).collect();
    // Note: naive_quantized uses Lloyd-Max for kNN; the naive_cos above measures uniform quality
    let naive_avg_cos = naive_cos / raw_vecs.len() as f64;
    println!("Naive uniform cosine: {naive_avg_cos:.4} (vs Lloyd-Max: {avg_cos:.4})\n");

    // Step 7: Recall@k benchmark
    let sample_size = raw_vecs.len().min(500);
    let mut rng = rand::rng();
    let sample_indices: Vec<usize> = {
        use rand::seq::SliceRandom;
        let mut all: Vec<usize> = (0..raw_vecs.len()).collect();
        all.shuffle(&mut rng);
        all.truncate(sample_size);
        all
    };

    let mut recall_results = Vec::new();
    for &k in &[1, 5, 10, 50] {
        if k > raw_vecs.len() { continue; }

        let mut total_recall = 0.0;
        let mut total_adc_us = 0.0;
        let mut total_exact_us = 0.0;

        for &qi in &sample_indices {
            let query = &raw_vecs[qi];

            let t = Instant::now();
            let exact = exact_knn(query, &raw_vecs, k);
            total_exact_us += t.elapsed().as_secs_f64() * 1e6;

            let t = Instant::now();
            let approx = cb.knn_search(query, &quantized, k);
            total_adc_us += t.elapsed().as_secs_f64() * 1e6;

            total_recall += recall_at_k(&exact, &approx);
        }

        let avg_recall = total_recall / sample_size as f64;
        let avg_exact_us = total_exact_us / sample_size as f64;
        let avg_adc_us = total_adc_us / sample_size as f64;
        let speedup = avg_exact_us / avg_adc_us;

        println!("recall@{k:<3} = {avg_recall:.3}  exact={avg_exact_us:.0}μs  adc={avg_adc_us:.0}μs  speedup={speedup:.1}x");
        recall_results.push((k, avg_recall, avg_exact_us, avg_adc_us, speedup));
    }

    // Step 8: JSON output
    println!("\n--- JSON Report ---");
    println!("{{");
    println!("  \"vectors\": {},", raw_vecs.len());
    println!("  \"dim\": 128,");
    println!("  \"bits\": 4,");
    println!("  \"compression_ratio\": {ratio:.1},");
    println!("  \"avg_cosine_similarity\": {avg_cos:.4},");
    println!("  \"avg_mse\": {avg_mse:.6},");
    println!("  \"naive_cosine_similarity\": {naive_avg_cos:.4},");
    println!("  \"recall\": [");
    for (i, (k, recall, exact_us, adc_us, speedup)) in recall_results.iter().enumerate() {
        let comma = if i < recall_results.len() - 1 { "," } else { "" };
        println!("    {{\"k\": {k}, \"recall\": {recall:.3}, \"exact_us\": {exact_us:.0}, \"adc_us\": {adc_us:.0}, \"speedup\": {speedup:.1}}}{comma}");
    }
    println!("  ]");
    println!("}}");

    // Assertions for CI
    println!();
    assert!(avg_cos >= 0.99, "cosine similarity should be >= 0.99, got {avg_cos}");
    assert!(ratio >= 7.0, "compression ratio should be >= 7x, got {ratio:.1}x");
    assert!(avg_cos > naive_avg_cos, "Lloyd-Max should beat naive uniform: {avg_cos} vs {naive_avg_cos}");

    println!("Benchmark passed.");
}
```

- [ ] **Step 3: Verify it compiles**

Run: `cargo check --tests`
Expected: compiles (test is `#[ignore]` so won't run in normal suite)

- [ ] **Step 4: Run the benchmark**

Run: `cargo test bench_turboquant -- --ignored --nocapture`
Expected: prints recall@k table, cosine similarity, compression ratio. Should take 30-60 seconds.

- [ ] **Step 5: Commit**

```bash
git add src/quantize/mod.rs src/quantize/bench.rs
git commit -m "feat(quantize): add TurboQuant benchmark harness with recall@k"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full test suite**

Run: `cargo test`
Expected: all existing + new tests pass (bench is ignored)

- [ ] **Step 2: Run clippy**

Run: `RUSTFLAGS="-Dwarnings" cargo clippy --all-features`
Expected: no warnings on our new files

- [ ] **Step 3: Run fmt**

Run: `cargo fmt -- --check src/quantize/turbo_quant.rs src/quantize/bench.rs src/quantize/mod.rs`
Expected: no formatting issues

- [ ] **Step 4: Run benchmark one more time**

Run: `cargo test bench_turboquant -- --ignored --nocapture`
Expected: prints results, passes assertions

- [ ] **Step 5: Commit any fixes**
