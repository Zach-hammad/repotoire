# L3 Relational Scoring: Quantized Node2Vec Embeddings

## Context

Repotoire's predictive coding engine has 5 hierarchy levels. L3 (Relational) currently uses
6-dimensional Mahalanobis distance on raw graph metrics (in_degree, out_degree, betweenness,
caller_modules, callee_modules, call_depth). This works but only captures direct metrics —
it misses transitivity, community structure, role equivalence, and other higher-order patterns
that node2vec embeddings capture.

The original L3 used node2vec → word2vec → kNN but was abandoned because training took too
long (~138s for a 6500-node graph). With TurboQuant (4-bit quantization, now implemented in
`src/quantize/turbo_quant.rs`) and aggressive parameter tuning, we can make this practical:

- **64D embeddings** (vs 128D research): ~2s training with tuned params
- **4-bit quantization**: 32 bytes per function (vs 512 bytes raw f64)
- **ADC kNN**: fast distance computation via precomputed lookup tables
- **Background computation**: doesn't block the main analysis

## Goals

1. Replace L3 Mahalanobis with node2vec embedding-based kNN anomaly detection
2. Background thread computes embeddings on cold start (~2-4s)
3. Cache quantized embeddings in session state, gated by edge_fingerprint
4. Graceful degradation: L3 returns z_score=0 when embeddings unavailable (first run)
5. No user-facing API changes — L3 just produces richer z-scores

## Non-Goals

- Changing the predictive coding concordance/severity logic
- Modifying other hierarchy levels (L1, L2, L1.5, L4)
- Real-time embedding updates during watch mode
- GPU acceleration

---

## Architecture

### Lifecycle

```
Cold run (no cache):
  precompute → L3 scorer checks session cache → miss → returns z_score=0 for all
  detect + score → 4 active levels (L1, L2, L1.5, L4), L3 silent
  after analysis → spawn background thread:
    extract call edges from Arc<CodeGraph>
    → node2vec (tuned params) → word2vec (64D)
    → TurboQuant quantize all embeddings
    → write embeddings.bin to session cache
  return results to user immediately

Second run (cache hit, topology unchanged):
  precompute → L3 scorer loads embeddings.bin → hit
  → for each function: ADC kNN distance → z-score
  detect + score → all 5 levels active
  (background thread not spawned — embeddings valid)

Incremental run (file changed, topology unchanged):
  edge_fingerprint matches → embeddings still valid → 5 levels

Topology change (function/edge added/removed):
  edge_fingerprint changed → invalidate embeddings.bin
  → same as cold run (4 levels + background recompute)
```

### Background Thread

The background thread receives:
- `Arc<CodeGraph>` (frozen, immutable — safe to share across threads)
- Session cache directory path
- `edge_fingerprint: u64` (written alongside embeddings for validation)
- `TurboQuantConfig` (dim=64, bits=4, seed=42)

It does NOT need:
- AnalysisContext, detectors, findings, or any mutable state
- The thread is fire-and-forget — the main thread doesn't wait for it

Implementation: `std::thread::spawn` with a closure that captures the Arc. The thread
writes `embeddings.bin` atomically (write to `.tmp`, rename) to avoid partial reads.

### Node2Vec Parameters (Tuned for Speed)

| Param | Research (128s) | Production (~2s) | Rationale |
|-------|----------------|------------------|-----------|
| walks_per_node | 10 | 3 | 3 walks sufficient for anomaly detection |
| walk_length | 20 | 10 | Shorter walks still capture local structure |
| embedding_dim | 128 | 64 | Diminishing returns above 64D for outlier detection |
| epochs | 5 | 2 | 2 epochs converge for coarse structure |
| p (return param) | 1.0 | 1.0 | Balanced BFS/DFS |
| q (in-out param) | 1.0 | 1.0 | Balanced BFS/DFS |

Expected training time: ~2s for 6500 nodes (33x faster than research settings).

---

## L3 Scorer Design

### New: `EmbeddingRelationalScorer`

Replaces `GraphRelationalScorer` in `src/predictive/relational.rs`.

```rust
pub enum RelationalScorer {
    /// Quantized node2vec kNN scorer (when embeddings available).
    Embedding(EmbeddingRelationalScorer),
    /// Fallback: 6D Mahalanobis (when embeddings unavailable).
    Mahalanobis(GraphRelationalScorer),
}
```

The `PredictiveCodingEngine` uses `RelationalScorer` — it calls `.distance(qn, contexts)`
on whichever variant is active. The concordance logic doesn't change.

**API note:** `EmbeddingRelationalScorer::distance()` ignores the `contexts` parameter (it uses
its own quantized embeddings). The `contexts` param is kept in the signature for API compatibility
with `GraphRelationalScorer` which needs it. This is an intentional unused parameter.

### `EmbeddingRelationalScorer`

```rust
pub struct EmbeddingRelationalScorer {
    codebook: TurboQuantCodebook,
    quantized_embeddings: HashMap<String, QuantizedVector>,  // qn → quantized embedding
    k: usize,  // kNN parameter (default: 10)
    all_quantized: Vec<QuantizedVector>,  // flat list for kNN search
    qn_to_idx: HashMap<String, usize>,   // qn → index in all_quantized
}
```

**Anomaly score computation:**

For a function with qualified name `qn`:
1. Look up its quantized embedding
2. Compute ADC kNN distance to k-th nearest neighbor across all functions
3. Return the distance as the raw anomaly score (higher = more unusual)

The k-th nearest neighbor distance is a standard anomaly detection metric — functions that
are far from their nearest neighbors in embedding space have unusual structural positions.

**Why kNN distance, not centroid distance:**

Centroid distance (like current Mahalanobis) assumes a unimodal distribution. Code graphs
are multimodal — leaf functions cluster together, hub functions cluster together, bridge
functions are sparse. kNN distance handles multimodal distributions naturally: a leaf
function is "normal" if it's near other leaves, even though it's far from the centroid.

---

## Cache Format

### `embeddings.bin`

Stored in the session cache directory (`~/.cache/repotoire/<repo>/session/embeddings.bin`).

Format (bincode):
```rust
#[derive(Serialize, Deserialize)]
struct CachedEmbeddings {
    edge_fingerprint: u64,          // for invalidation
    dim: usize,                      // 64
    bits: usize,                     // 4
    seed: u64,                       // rotation matrix seed
    codebook_centroids: Vec<f64>,    // 16 centroids (precomputed, not the rotation matrix)
    entries: Vec<CachedEntry>,
}

#[derive(Serialize, Deserialize)]
struct CachedEntry {
    qualified_name: String,
    indices: Vec<u8>,               // packed 4-bit (32 bytes for 64D)
    norm: f64,
}
```

The rotation matrix is NOT cached — it's regenerated deterministically from `seed` via
`TurboQuantCodebook::new()`. This keeps the cache file small (~200KB for 5000 functions).

**Size estimate:** 5000 entries × (avg 40 bytes qn + 32 bytes indices + 8 bytes norm) = ~400KB.

**Invalidation:** On load, compare `edge_fingerprint` with current graph's fingerprint.
Mismatch → discard and recompute in background.

---

## Data Flow

```
CodeGraph (frozen, Arc)
  │
  ├── [background thread]
  │     │
  │     ├── all_call_edges() → (u32, u32) edges
  │     ├── node2vec_random_walks(edges, 3 walks, length 10)
  │     ├── train_skipgram(walks, dim=64, epochs=2)
  │     ├── TurboQuantCodebook::new(dim=64, bits=4)
  │     ├── quantize all embeddings
  │     └── write embeddings.bin (atomic)
  │
  └── [main thread, next run]
        │
        ├── load embeddings.bin
        ├── TurboQuantCodebook::new(dim=64, bits=4, same seed)
        ├── EmbeddingRelationalScorer { codebook, embeddings }
        └── per function: knn_search(qn_embedding, all_embeddings, k=10)
            → k-th neighbor ADC distance → z-score
            (ADC-only, no reranking — reranking adds reconstruction
             overhead that exceeds the scoring budget)
```

---

## Files to Create

| File | Purpose |
|------|---------|
| `src/predictive/embedding_scorer.rs` | EmbeddingRelationalScorer, CachedEmbeddings, cache load/save |

## Files to Modify

| File | Changes |
|------|---------|
| `src/predictive/relational.rs` | Add `RelationalScorer` enum wrapping Embedding or Mahalanobis |
| `src/predictive/mod.rs` | Add `pub mod embedding_scorer;`, update `PredictiveCodingEngine` to use `RelationalScorer` |
| `src/engine/mod.rs` | Spawn background thread after analysis when embeddings missing |
| `src/engine/state.rs` | Add `embeddings_available: bool` to session meta (informational) |
| `src/quantize/turbo_quant.rs` | Add `Serialize`/`Deserialize` derives on `QuantizedVector` |

---

## Configuration

```toml
[predictive.l3]
enabled = true                  # enable L3 embedding scorer
dim = 64                        # embedding dimension
walks_per_node = 3              # node2vec walks per node
walk_length = 10                # node2vec walk length
epochs = 2                      # word2vec training epochs
knn_k = 10                      # k for kNN anomaly detection
```

Falls back to Mahalanobis if `enabled = false`.

---

## Verification

### Unit Tests

- `EmbeddingRelationalScorer` returns higher distance for structurally unusual nodes
- Cache round-trip: save → load → same embeddings
- `RelationalScorer::Mahalanobis` fallback works when embeddings unavailable
- Background thread writes valid `embeddings.bin`
- edge_fingerprint mismatch triggers invalidation

### Integration Tests

- Cold run: L3 z-scores are all 0.0 (no embeddings yet)
- Second run (after background completes): L3 z-scores are non-zero
- Topology change: embeddings invalidated, L3 falls back to 0.0

### Manual Test

```bash
# First run — L3 silent, background computes
repotoire analyze . --format text
# Wait 3s for background thread
# Second run — L3 active
repotoire analyze . --format text --explain-score
# Should show L3 Relational scores in the output
```

---

## Performance Budget

| Phase | Time Budget | Notes |
|-------|-------------|-------|
| Background embedding computation | ~2-4s | Fire-and-forget, doesn't block |
| Cache load (embeddings.bin) | <50ms | Bincode deserialize ~400KB |
| L3 scoring per function | ~0.3ms | ADC table build (once) + scan 5000 entries |
| L3 total (5000 functions) | ~1.5-2s | 5000 queries × 5000 entries × 64ns ADC. Acceptable — runs in parallel with other precompute work via rayon. |
| Cache write (atomic) | <100ms | Bincode serialize + rename |

---

## References

- Grover & Leskovec 2016 — "node2vec: Scalable Feature Learning for Networks". Walk algorithm.
- Zandieh et al. 2025 — "TurboQuant". Quantization algorithm (implemented in `src/quantize/`).
- Ramaswamy et al. 2000 — "Efficient Algorithms for Mining Outliers from Large Data Sets". kNN anomaly detection.
- Friston 2010 — "The free-energy principle". Predictive coding framework used by repotoire.
