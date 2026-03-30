# TurboQuant: Vector Quantization for Repotoire Embeddings

## Context

Repotoire's predictive coding engine (L3 relational layer) has a node2vec implementation that
generates 128-dimensional embeddings for code graph nodes. These embeddings are currently unused —
L3 switched to a simpler Mahalanobis distance approach. Meanwhile, the broader ecosystem (VectorBase,
bge-large pipeline) stores vectors as raw f32 with no compression.

TurboQuant (Zandieh et al. 2025) is a new vector quantization algorithm that achieves near-optimal
distortion rates using random orthogonal rotations + coordinate-wise scalar quantization. It's
data-oblivious (no training data needed), fast to index (0.001s vs PQ's 240s), and achieves 0.995
cosine similarity at 4-bit (8x compression).

This is a **research/benchmark** implementation to validate TurboQuant on repotoire's graph
embeddings before deciding where to deploy it (VectorBase, bge-large pipeline, or repotoire itself).

## Goals

1. Implement TurboQuant (4-bit, MSE variant) in pure Rust
2. Benchmark recall@k against brute-force kNN on node2vec embeddings
3. Measure compression ratio and search latency
4. Compare against naive scalar quantization baseline

## Non-Goals

- Inner product variant (stage 2 with QJL correction) — deferred
- GPU acceleration — unnecessary at 72K vectors
- Integration with L3 scoring — benchmark first, integrate later
- Integration with VectorBase/bge-large — separate project after validation

---

## Algorithm

### TurboQuant MSE (4-bit, d=128)

Based on Zandieh et al. 2025 ([arXiv:2504.19874](https://arxiv.org/abs/2504.19874)).

**Preprocessing (once):**
1. Generate random d×d matrix G with i.i.d. N(0,1) entries (seeded RNG)
2. Compute QR decomposition: G = QR, keep Q as rotation matrix R (orthogonal)
3. Precompute Lloyd-Max codebook for the Beta distribution f_X(x) = [Γ(d/2)] / [√π·Γ((d-1)/2)] · (1−x²)^((d−3)/2) at b=4 bits → 16 centroids + 15 decision boundaries

**Quantization:**
1. Normalize: x̂ = x / ||x||₂, store norm separately
2. Rotate: y = R · x̂
3. For each coordinate j ∈ [d]: idx_j = argmin_k |y_j − c_k| (nearest centroid)
4. Pack indices: 4 bits × 128 = 512 bits = 64 bytes

**Reconstruction:**
1. Unpack indices, lookup centroids: ỹ_j = c_{idx_j}
2. Rotate back: x̃ = R^T · ỹ
3. Scale: x̃ = x̃ · norm

**Asymmetric Distance Computation (ADC):**
For kNN search, the query stays uncompressed. Pre-rotate the query once, then distance to each
quantized vector is a table lookup:

1. Precompute: q_rot = R · (query / ||query||₂)
2. Build distance table: dist_table[k] = (q_rot_j − c_k)² for each coordinate j and centroid k
   → d × 2^b = 128 × 16 = 2048 entries, fits in L1 cache
3. For each quantized vector: dist = Σ_j dist_table[idx_j] (just index lookups + addition)

### Lloyd-Max Codebook

The Beta distribution at d=128 is well-approximated by N(0, 1/d). For 4-bit (16 levels), the
Lloyd-Max quantizer for a Gaussian can be precomputed to high precision. We hardcode the centroids
and boundaries for (d=128, b=4) as compile-time constants, avoiding runtime optimization.

The codebook values are scaled by 1/√d to match the unit sphere coordinate distribution.

**Fallback:** For non-standard (d, b) pairs, run Lloyd-Max optimization (~300 iterations) at
initialization using the Beta distribution PDF.

### Naive Baseline

For comparison, implement simple uniform scalar quantization:
1. Rotate with same R
2. Quantize each coordinate to nearest value in a uniform grid over [−1/√d, 1/√d]
3. Same ADC search

This isolates the contribution of the Lloyd-Max codebook (optimal for Beta distribution) vs
uniform quantization.

---

## Data Model

### `src/quantize/turbo_quant.rs`

```rust
/// Configuration for TurboQuant quantizer.
pub struct TurboQuantConfig {
    pub dim: usize,       // vector dimension (default: 128)
    pub bits: usize,      // bits per coordinate (default: 4)
    pub seed: u64,        // RNG seed for reproducible rotation matrix
}

/// Precomputed quantization state: rotation matrix + codebook.
pub struct TurboQuantCodebook {
    rotation: DMatrix<f64>,       // d×d orthogonal matrix R
    rotation_t: DMatrix<f64>,     // R^T (precomputed transpose)
    centroids: Vec<f64>,          // 2^b centroid values
    boundaries: Vec<f64>,         // 2^b - 1 decision boundaries
    dim: usize,
    bits: usize,
    num_levels: usize,            // 2^b
}

/// A quantized vector: packed codebook indices + original norm.
pub struct QuantizedVector {
    indices: Vec<u8>,     // packed 4-bit indices (dim/2 bytes for b=4)
    norm: f64,            // original L2 norm
}

/// Precomputed ADC distance table for a query.
pub struct DistanceTable {
    table: Vec<f64>,      // dim × num_levels squared distances
    dim: usize,
    num_levels: usize,
}
```

### Public API

```rust
impl TurboQuantCodebook {
    /// Create a new quantizer. Precomputes rotation matrix and codebook.
    pub fn new(config: TurboQuantConfig) -> Self;

    /// Quantize a raw vector.
    pub fn quantize(&self, x: &[f64]) -> QuantizedVector;

    /// Reconstruct a quantized vector (lossy).
    pub fn reconstruct(&self, qv: &QuantizedVector) -> Vec<f64>;

    /// Precompute ADC distance table for a query vector.
    pub fn build_distance_table(&self, query: &[f64]) -> DistanceTable;

    /// Cosine similarity between raw query and quantized vector via ADC.
    pub fn adc_distance(&self, table: &DistanceTable, qv: &QuantizedVector) -> f64;

    /// Brute-force kNN search over quantized database.
    pub fn knn_search(
        &self,
        query: &[f64],
        database: &[QuantizedVector],
        k: usize,
    ) -> Vec<(usize, f64)>;  // (index, distance)
}
```

---

## Benchmark Harness

### `src/quantize/bench.rs`

**Flow:**
1. Run node2vec on repotoire's own code graph (~5500 functions, 128D)
2. Quantize all embeddings with TurboQuant (4-bit)
3. Also quantize with naive uniform baseline
4. For each node as query:
   - Compute exact brute-force kNN (ground truth)
   - Compute TurboQuant ADC kNN
   - Compute naive baseline ADC kNN
5. Report metrics

**Metrics:**
- **recall@k** for k = 1, 5, 10, 50 (fraction of true top-k found by approximate search)
- **compression ratio** (raw bytes vs quantized bytes per vector)
- **quantization MSE** (mean squared error of reconstructed vs original)
- **cosine similarity** (average cosine between original and reconstructed)
- **search latency** (time per query: brute force vs ADC)

**Success criteria:**
- recall@10 ≥ 0.95
- 8x compression (512 → 64 bytes per 128D vector)
- cosine similarity ≥ 0.99

**Output:** JSON report with all metrics, printed to stdout.

---

## File Structure

```
src/quantize/
  mod.rs              — pub mod turbo_quant; pub mod bench;
  turbo_quant.rs      — TurboQuantCodebook, QuantizedVector, ADC, kNN search
  bench.rs            — Benchmark harness, recall@k computation, JSON output
```

## Dependencies

- `nalgebra` — QR decomposition, matrix multiplication. Add to Cargo.toml.
- `rand` / `rand_chacha` — seeded RNG for reproducible rotation matrix. Already in deps.
- No other new dependencies.

## Files to Modify

| File | Changes |
|------|---------|
| `src/lib.rs` | Add `pub mod quantize;` |
| `Cargo.toml` | Add `nalgebra = "0.33"` |

---

## Verification

### Unit Tests (`src/quantize/turbo_quant.rs`)

- Rotation matrix is orthogonal: R^T · R = I (within floating point tolerance)
- Quantize → reconstruct round-trip preserves direction (cosine > 0.99 for 4-bit)
- Quantize → reconstruct preserves norm (within quantization error)
- ADC distance matches brute-force reconstruction distance
- kNN search returns correct k results
- Packed indices round-trip correctly (pack → unpack → same values)
- Codebook centroids are sorted and within expected range

### Benchmark Test

- Run on repotoire's own codebase
- Assert recall@10 ≥ 0.90 (conservative threshold for CI)
- Assert compression ratio ≥ 7x

---

## References

- Zandieh et al. 2025 — "TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate" ([arXiv](https://arxiv.org/abs/2504.19874)). Core algorithm.
- Dejan AI blog — "TurboQuant: From Paper to Triton Kernel" ([blog](https://dejan.ai/blog/turboquant/)). Practical implementation notes.
- Google Research blog — "TurboQuant: Redefining AI efficiency with extreme compression" ([blog](https://research.google/blog/turboquant-redefining-ai-efficiency-with-extreme-compression/)). Overview.
- Lloyd 1982 — "Least squares quantization in PCM". Lloyd-Max algorithm.
- Jegou et al. 2011 — "Product quantization for nearest neighbor search". ADC technique.
