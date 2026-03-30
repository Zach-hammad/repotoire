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
1. Generate random d×d matrix G with i.i.d. N(0,1) entries (seeded RNG via `rand_chacha`)
2. Compute QR decomposition: G = QR, keep Q as rotation matrix R (orthogonal)
3. Precompute Lloyd-Max codebook for the Beta distribution
   f_X(x) = [Γ(d/2)] / [√π·Γ((d-1)/2)] · (1−x²)^((d−3)/2)
   at b=4 bits → 16 centroids + 15 decision boundaries

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

ADC computes **approximate squared Euclidean distance** between the rotated, normalized query and
the quantized database vector. Since both are L2-normalized before rotation, this is equivalent to
approximate cosine distance: `cosine_sim ≈ 1 - dist_sq / 2`.

For kNN search, the query stays uncompressed. Pre-rotate the query once, then distance to each
quantized vector is a table lookup:

1. Normalize query: q̂ = query / ||query||₂
2. Rotate: q_rot = R · q̂
3. Build distance table: `dist_table[j][k] = (q_rot[j] − c_k)²` for each coordinate j ∈ [d]
   and centroid k ∈ [2^b] → d × 2^b = 128 × 16 = 2048 entries, fits in L1 cache.
   Stored as flat array: `table[j * num_levels + k]`
4. For each quantized vector: `dist_sq = Σ_j table[j * num_levels + idx_j]`
   (just index lookups + addition — no floating point multiply)

### Lloyd-Max Codebook

The Beta distribution at d=128 is well-approximated by N(0, 1/d) ≈ N(0, 0.0078). For 4-bit
(16 levels), we hardcode the Lloyd-Max codebook for N(0, 1/128) as compile-time constants.

The standard Lloyd-Max centroids for N(0,1) at 4-bit are well-known. Scale each centroid by
`1/√d = 1/√128 ≈ 0.0884` to get the codebook for N(0, 1/d).

**Hardcoded values for (d=128, b=4):**
The 16 centroids for N(0,1) 4-bit Lloyd-Max are approximately:
±{0.1284, 0.3882, 0.6568, 0.9424, 1.2562, 1.6180, 2.0690, 2.7326}
Scaled by 1/√128: ±{0.01135, 0.03432, 0.05807, 0.08331, 0.11104, 0.14302, 0.18290, 0.24155}

**Fallback for non-standard (d, b) pairs:**
Run Lloyd-Max optimization (~300 iterations) at initialization:
1. Initialize centroids uniformly over [−3σ, 3σ] where σ = 1/√d
2. Repeat until convergence (relative change < 1e-10):
   a. Update boundaries: b_k = (c_k + c_{k+1}) / 2
   b. Update centroids: c_k = E[X | b_{k-1} < X < b_k] computed via numerical integration
      (Simpson's rule over the Gaussian PDF with σ = 1/√d, 1000 quadrature points)
3. Store final centroids and boundaries

### Bit Packing Convention

4-bit indices are packed two per byte, lower nibble first:

```
byte[i] = (idx[2*i] & 0x0F) | (idx[2*i + 1] << 4)
```

Unpacking:
```
idx[2*i]     = byte[i] & 0x0F
idx[2*i + 1] = byte[i] >> 4
```

For d=128: `indices.len() = 64` bytes.

### Naive Baseline

For comparison, implement simple uniform scalar quantization:
1. Rotate with same R
2. Quantize each coordinate to nearest value in a uniform grid over [−3/√d, 3/√d]
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
    centroids: Vec<f64>,          // 2^b centroid values (sorted)
    boundaries: Vec<f64>,         // 2^b - 1 decision boundaries
    dim: usize,
    bits: usize,
    num_levels: usize,            // 2^b
}

/// A quantized vector: packed codebook indices + original norm.
pub struct QuantizedVector {
    indices: Vec<u8>,     // packed 4-bit indices, 2 per byte, lower nibble first
    norm: f64,            // original L2 norm (preserved for reconstruction)
}

/// Precomputed ADC distance table for a query.
pub struct DistanceTable {
    table: Vec<f64>,      // flat array: table[j * num_levels + k] = (q_rot[j] - c_k)²
    dim: usize,
    num_levels: usize,
}
```

**Note on f32 vs f64:** The existing node2vec implementation (`predictive/embeddings.rs`)
produces `Vec<f32>` embeddings. The quantizer operates on `f64` for numerical precision during
rotation. The benchmark casts `f32 → f64` at the boundary. The `QuantizedVector` itself is
format-agnostic — it stores only packed indices + norm.

### Public API

```rust
impl TurboQuantCodebook {
    /// Create a new quantizer. Precomputes rotation matrix and codebook.
    pub fn new(config: TurboQuantConfig) -> Self;

    /// Quantize a raw vector. Normalizes internally, stores norm.
    pub fn quantize(&self, x: &[f64]) -> QuantizedVector;

    /// Reconstruct a quantized vector (lossy). Returns unnormalized vector.
    pub fn reconstruct(&self, qv: &QuantizedVector) -> Vec<f64>;

    /// Precompute ADC distance table for a query vector.
    /// Query is L2-normalized internally before rotation.
    pub fn build_distance_table(&self, query: &[f64]) -> DistanceTable;

    /// Approximate squared L2 distance between normalized query and quantized vector.
    /// For cosine similarity: cos_sim ≈ 1 - adc_distance() / 2
    pub fn adc_distance(&self, table: &DistanceTable, qv: &QuantizedVector) -> f64;

    /// Brute-force kNN search over quantized database using ADC.
    /// Returns (index, approximate_cosine_similarity) pairs, sorted descending.
    pub fn knn_search(
        &self,
        query: &[f64],
        database: &[QuantizedVector],
        k: usize,
    ) -> Vec<(usize, f64)>;
}

/// Pack/unpack helpers
pub fn pack_4bit(indices: &[u8]) -> Vec<u8>;
pub fn unpack_4bit(packed: &[u8], dim: usize) -> Vec<u8>;
```

---

## Benchmark Harness

### `src/quantize/bench.rs`

**Invocation:** `#[test] #[ignore]` test, run via:
```bash
cargo test bench_turboquant -- --ignored --nocapture
```

The `#[ignore]` tag keeps it out of normal `cargo test` runs since it needs to build a real
code graph (~15-20s startup).

**Building the code graph:**

The benchmark needs real node2vec embeddings. To obtain them:
1. Call stage functions directly (NOT `AnalysisEngine` — it runs all 8 stages):
   ```rust
   use crate::engine::stages::{collect, parse, graph};
   let collect_out = collect::collect_stage(&CollectInput { repo_path, ... })?;
   let parse_out = parse::parse_stage(&ParseInput { files: &collect_out.files, ... })?;
   let graph_out = graph::graph_stage(&GraphInput { parse_results: &parse_out.results, ... })?;
   let frozen = graph::freeze_graph(graph_out.mutable_graph, graph_out.value_store, None);
   ```
2. Extract call edges from frozen `CodeGraph` via `graph.all_call_edges()` → `&[(NodeIndex, NodeIndex)]`.
   Convert to `(u32, u32)`: `edges.iter().map(|(a, b)| (a.index() as u32, b.index() as u32))`
3. Run `node2vec_random_walks()` + `train_skipgram()` from `predictive::embeddings`
4. Collect embeddings as `FxHashMap<u32, Vec<f32>>`, cast to `Vec<f64>` for quantization

**Note:** This uses only collect/parse/graph stages — skips git_enrich, calibrate, detect, etc.
Expected startup: ~15-20 seconds to parse repotoire's ~93K lines of Rust.

**Flow:**
1. Build code graph and generate node2vec embeddings (~5500 functions, 128D)
2. Cast `f32 → f64`
3. Quantize all embeddings with TurboQuant (4-bit)
4. Also quantize with naive uniform baseline
5. For a sample of 500 random query nodes:
   - Compute exact brute-force kNN (ground truth, on raw f64 vectors)
   - Compute TurboQuant ADC kNN
   - Compute naive baseline ADC kNN
6. Report metrics as JSON to stdout

**Metrics:**
- **recall@k** for k = 1, 5, 10, 50 (fraction of true top-k found by approximate search)
- **compression ratio** (raw bytes vs quantized bytes per vector)
- **quantization MSE** (mean squared error of reconstructed vs original)
- **cosine similarity** (average cosine between original and reconstructed)
- **search latency** (time per query: brute force vs ADC, in microseconds)
- **TurboQuant vs naive recall** (side-by-side comparison)

**Success criteria:**
- recall@10 ≥ 0.95 (aspiration), ≥ 0.90 (CI hard gate)
- 8x compression (512 → 64 bytes per 128D vector)
- cosine similarity ≥ 0.99
- TurboQuant recall > naive baseline recall (proves Lloyd-Max codebook matters)

**Output:** JSON report with all metrics, printed to stdout.

---

## File Structure

```
src/quantize/
  mod.rs              — pub mod turbo_quant; pub mod bench;
  turbo_quant.rs      — TurboQuantCodebook, QuantizedVector, ADC, kNN search, pack/unpack
  bench.rs            — Benchmark harness (#[test] #[ignore]), recall@k, JSON output
```

## Dependencies

- `nalgebra = "0.33"` — QR decomposition (`DMatrix::qr()` → `.q()`), matrix multiplication. Add to Cargo.toml.
- `rand` / `rand_chacha` — seeded RNG for reproducible rotation matrix. Already in Cargo.toml.
- No other new dependencies.

## Files to Modify

| File | Changes |
|------|---------|
| `src/lib.rs` | Add `pub mod quantize;` |
| `Cargo.toml` | Add `nalgebra = "0.33"` |

---

## Verification

### Unit Tests (`src/quantize/turbo_quant.rs`)

- Rotation matrix is orthogonal: R^T · R ≈ I (within 1e-10 tolerance)
- Quantize → reconstruct round-trip preserves direction (cosine > 0.99 for 4-bit)
- Quantize → reconstruct preserves norm (within quantization error)
- ADC distance matches brute-force reconstruction distance (within 1e-6)
- kNN search returns correct k results
- Packed indices round-trip: `unpack_4bit(pack_4bit(indices)) == indices`
- Codebook centroids are sorted and within expected range [−3/√d, 3/√d]
- Lloyd-Max fallback produces same codebook as hardcoded values (within tolerance)

### Benchmark Test (`#[test] #[ignore]`)

- Run on repotoire's own codebase
- Assert recall@10 ≥ 0.90 (conservative threshold for CI)
- Assert compression ratio ≥ 7x
- Assert TurboQuant recall@10 > naive baseline recall@10

---

## References

- Zandieh et al. 2025 — "TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate" ([arXiv](https://arxiv.org/abs/2504.19874)). Core algorithm.
- Dejan AI blog — "TurboQuant: From Paper to Triton Kernel" ([blog](https://dejan.ai/blog/turboquant/)). Practical implementation notes, gotchas.
- Google Research blog — "TurboQuant: Redefining AI efficiency with extreme compression" ([blog](https://research.google/blog/turboquant-redefining-ai-efficiency-with-extreme-compression/)). Overview.
- Lloyd 1982 — "Least squares quantization in PCM". Lloyd-Max algorithm.
- Jegou et al. 2011 — "Product quantization for nearest neighbor search". ADC technique.
