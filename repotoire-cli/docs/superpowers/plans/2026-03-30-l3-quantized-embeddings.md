# L3 Quantized Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace L3 Mahalanobis scoring with node2vec + TurboQuant kNN anomaly detection, computed in a background thread and cached across runs.

**Architecture:** Background thread computes node2vec embeddings (64D, tuned params) after cold analysis, quantizes with TurboQuant (4-bit), writes to session cache. Next run loads cached embeddings, L3 scores each function by its k-th nearest neighbor ADC distance. Falls back to Mahalanobis when embeddings unavailable.

**Tech Stack:** Existing node2vec (`predictive/embeddings.rs`), TurboQuant (`quantize/turbo_quant.rs`), bincode (already in deps), std::thread.

**Spec:** `docs/superpowers/specs/2026-03-30-l3-quantized-embeddings-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/predictive/embedding_scorer.rs` | EmbeddingRelationalScorer, CachedEmbeddings, cache load/save, background compute |

### Modified Files
| File | Changes |
|------|---------|
| `src/predictive/mod.rs` | Add `pub mod embedding_scorer;`, update TrainedModels to use RelationalScorer enum |
| `src/predictive/relational.rs` | Add `RelationalScorer` enum, delegate `.distance()` |
| `src/quantize/turbo_quant.rs` | Add `Serialize`/`Deserialize` derives to `QuantizedVector` |
| `src/engine/mod.rs` | Spawn background embedding thread after cold analysis |

---

### Task 1: Add serde derives to QuantizedVector

**Files:**
- Modify: `src/quantize/turbo_quant.rs`

- [ ] **Step 1: Add serde derives**

Change the `QuantizedVector` struct derives from:
```rust
#[derive(Debug, Clone)]
pub struct QuantizedVector {
```
to:
```rust
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct QuantizedVector {
```

- [ ] **Step 2: Verify compilation**

Run: `cargo check`

- [ ] **Step 3: Commit**

```bash
git add src/quantize/turbo_quant.rs
git commit -m "feat(quantize): add Serialize/Deserialize to QuantizedVector for cache"
```

---

### Task 2: EmbeddingRelationalScorer + cache format

**Files:**
- Create: `src/predictive/embedding_scorer.rs`
- Modify: `src/predictive/mod.rs` (add `pub mod embedding_scorer;`)

- [ ] **Step 1: Create embedding_scorer.rs**

```rust
//! L3 Relational scoring via quantized node2vec embeddings + kNN anomaly detection.
//!
//! Computes node2vec embeddings in a background thread, quantizes with TurboQuant (4-bit),
//! caches in session directory. On subsequent runs, loads cached embeddings and scores
//! each function by ADC distance to its k-th nearest neighbor.

use crate::quantize::turbo_quant::{TurboQuantCodebook, TurboQuantConfig, QuantizedVector};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;

/// Cached embeddings written to `embeddings.bin` in session directory.
#[derive(Serialize, Deserialize)]
pub struct CachedEmbeddings {
    /// Edge fingerprint for cache invalidation.
    pub edge_fingerprint: u64,
    /// Embedding dimension (64).
    pub dim: usize,
    /// Quantization bits (4).
    pub bits: usize,
    /// RNG seed for deterministic rotation matrix.
    pub seed: u64,
    /// Per-function quantized embeddings keyed by qualified name.
    pub entries: Vec<CachedEntry>,
}

#[derive(Serialize, Deserialize)]
pub struct CachedEntry {
    pub qualified_name: String,
    pub indices: Vec<u8>,
    pub norm: f64,
}

/// L3 scorer that uses quantized node2vec embeddings + ADC kNN.
pub struct EmbeddingRelationalScorer {
    codebook: TurboQuantCodebook,
    /// All quantized embeddings as a flat Vec for kNN scan.
    all_quantized: Vec<QuantizedVector>,
    /// Qualified name → index in all_quantized.
    qn_to_idx: HashMap<String, usize>,
    /// k for kNN anomaly detection.
    k: usize,
}

impl EmbeddingRelationalScorer {
    /// Build scorer from cached embeddings.
    pub fn from_cache(cached: &CachedEmbeddings, k: usize) -> Self {
        let config = TurboQuantConfig {
            dim: cached.dim,
            bits: cached.bits,
            seed: cached.seed,
        };
        let codebook = TurboQuantCodebook::new(config);

        let mut all_quantized = Vec::with_capacity(cached.entries.len());
        let mut qn_to_idx = HashMap::with_capacity(cached.entries.len());

        for (i, entry) in cached.entries.iter().enumerate() {
            all_quantized.push(QuantizedVector {
                indices: entry.indices.clone(),
                norm: entry.norm,
            });
            qn_to_idx.insert(entry.qualified_name.clone(), i);
        }

        Self { codebook, all_quantized, qn_to_idx, k }
    }

    /// kNN anomaly distance for a function. Returns the distance to the k-th
    /// nearest neighbor. Higher = more structurally unusual.
    ///
    /// Returns 0.0 if the function has no embedding.
    pub fn distance(&self, qn: &str) -> f64 {
        let idx = match self.qn_to_idx.get(qn) {
            Some(&i) => i,
            None => return 0.0,
        };

        // Build ADC distance table for this function's embedding
        let qv = &self.all_quantized[idx];
        let reconstructed = self.codebook.reconstruct(qv);
        let table = self.codebook.build_distance_table(&reconstructed);

        // Compute distances to all other embeddings
        let mut distances: Vec<f64> = self.all_quantized
            .iter()
            .enumerate()
            .filter(|(i, _)| *i != idx) // exclude self
            .map(|(_, other)| self.codebook.adc_distance(&table, other))
            .collect();

        // Sort ascending (smallest distance = nearest)
        distances.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        // k-th nearest neighbor distance (0-indexed, so k-1)
        let k_idx = (self.k - 1).min(distances.len().saturating_sub(1));
        distances.get(k_idx).copied().unwrap_or(0.0)
    }
}

// ============================================================================
// CACHE I/O
// ============================================================================

const EMBEDDINGS_FILE: &str = "embeddings.bin";

/// Load cached embeddings from session directory. Returns None if missing or invalid.
pub fn load_embeddings(session_path: &Path, current_fingerprint: u64) -> Option<CachedEmbeddings> {
    let path = session_path.join(EMBEDDINGS_FILE);
    let data = std::fs::read(&path).ok()?;
    let cached: CachedEmbeddings = bincode::deserialize(&data).ok()?;
    if cached.edge_fingerprint != current_fingerprint {
        tracing::debug!("Embedding cache invalidated: fingerprint mismatch");
        return None;
    }
    tracing::debug!("Loaded {} cached embeddings", cached.entries.len());
    Some(cached)
}

/// Save embeddings to session directory (atomic: write .tmp, rename).
pub fn save_embeddings(session_path: &Path, cached: &CachedEmbeddings) -> anyhow::Result<()> {
    std::fs::create_dir_all(session_path)?;
    let path = session_path.join(EMBEDDINGS_FILE);
    let tmp_path = session_path.join(".embeddings.bin.tmp");
    let data = bincode::serialize(cached)?;
    std::fs::write(&tmp_path, &data)?;
    std::fs::rename(&tmp_path, &path)?;
    tracing::debug!("Saved {} embeddings to cache ({} bytes)", cached.entries.len(), data.len());
    Ok(())
}

// ============================================================================
// BACKGROUND COMPUTATION
// ============================================================================

use crate::graph::CodeGraph;
use std::sync::Arc;

/// Compute node2vec embeddings, quantize, and save to cache.
/// Designed to be called from a background thread.
pub fn compute_and_cache_embeddings(
    graph: Arc<CodeGraph>,
    session_path: std::path::PathBuf,
    edge_fingerprint: u64,
) {
    let interner = graph.interner();

    // Extract call edges
    let call_edges = graph.all_call_edges();
    let edges: Vec<(u32, u32)> = call_edges
        .iter()
        .map(|(a, b)| (a.index() as u32, b.index() as u32))
        .collect();
    let num_nodes = graph.node_count();

    if edges.is_empty() || num_nodes < 20 {
        tracing::debug!("Skipping L3 embeddings: too few nodes/edges");
        return;
    }

    // Node2vec with tuned production params
    let walks = crate::predictive::embeddings::node2vec_random_walks(
        &edges, num_nodes,
        10,     // walk_length (tuned down from 20)
        3,      // walks_per_node (tuned down from 10)
        1.0, 1.0, // p, q (balanced)
        Some(42),
    );

    if walks.is_empty() {
        tracing::debug!("No walks generated — graph may be disconnected");
        return;
    }

    // Word2vec with tuned params
    let w2v = crate::predictive::embeddings::train_skipgram(
        &walks,
        &crate::predictive::embeddings::Word2VecConfig {
            embedding_dim: 64,
            epochs: 2,
            seed: Some(42),
            ..Default::default()
        },
    );

    if w2v.embeddings.is_empty() {
        tracing::debug!("No embeddings produced");
        return;
    }

    // Quantize
    let config = TurboQuantConfig { dim: 64, bits: 4, seed: 42 };
    let codebook = TurboQuantCodebook::new(config);

    // Build node_id → qualified_name mapping
    let mut id_to_qn: HashMap<u32, String> = HashMap::new();
    for &idx in graph.functions() {
        if let Some(node) = graph.node(idx) {
            id_to_qn.insert(idx.index() as u32, node.qn(interner).to_string());
        }
    }

    let mut entries = Vec::new();
    for (&node_id, embedding) in &w2v.embeddings {
        if let Some(qn) = id_to_qn.get(&node_id) {
            let f64_vec: Vec<f64> = embedding.iter().map(|&v| v as f64).collect();
            let qv = codebook.quantize(&f64_vec);
            entries.push(CachedEntry {
                qualified_name: qn.clone(),
                indices: qv.indices,
                norm: qv.norm,
            });
        }
    }

    let cached = CachedEmbeddings {
        edge_fingerprint,
        dim: 64,
        bits: 4,
        seed: 42,
        entries,
    };

    if let Err(e) = save_embeddings(&session_path, &cached) {
        tracing::debug!("Failed to save embeddings cache: {e}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cache_roundtrip() {
        let cached = CachedEmbeddings {
            edge_fingerprint: 12345,
            dim: 64,
            bits: 4,
            seed: 42,
            entries: vec![
                CachedEntry {
                    qualified_name: "foo.bar".into(),
                    indices: vec![0u8; 32],
                    norm: 1.5,
                },
            ],
        };

        let dir = tempfile::tempdir().unwrap();
        save_embeddings(dir.path(), &cached).unwrap();
        let loaded = load_embeddings(dir.path(), 12345).unwrap();
        assert_eq!(loaded.entries.len(), 1);
        assert_eq!(loaded.entries[0].qualified_name, "foo.bar");
        assert_eq!(loaded.edge_fingerprint, 12345);
    }

    #[test]
    fn test_cache_invalidation() {
        let cached = CachedEmbeddings {
            edge_fingerprint: 12345,
            dim: 64,
            bits: 4,
            seed: 42,
            entries: vec![],
        };

        let dir = tempfile::tempdir().unwrap();
        save_embeddings(dir.path(), &cached).unwrap();
        // Load with different fingerprint → None
        assert!(load_embeddings(dir.path(), 99999).is_none());
    }

    #[test]
    fn test_scorer_returns_zero_for_missing_qn() {
        let cached = CachedEmbeddings {
            edge_fingerprint: 0,
            dim: 64,
            bits: 4,
            seed: 42,
            entries: vec![],
        };
        let scorer = EmbeddingRelationalScorer::from_cache(&cached, 10);
        assert_eq!(scorer.distance("nonexistent"), 0.0);
    }
}
```

- [ ] **Step 2: Add module declaration**

In `src/predictive/mod.rs`, add after the other module declarations:
```rust
pub mod embedding_scorer;
```

- [ ] **Step 3: Verify compilation**

Run: `cargo check`

- [ ] **Step 4: Run tests**

Run: `cargo test predictive::embedding_scorer -- --nocapture`
Expected: 3 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/predictive/embedding_scorer.rs src/predictive/mod.rs
git commit -m "feat(predictive): add EmbeddingRelationalScorer with cache and background compute"
```

---

### Task 3: RelationalScorer enum

**Files:**
- Modify: `src/predictive/relational.rs`

- [ ] **Step 1: Add RelationalScorer enum**

Add at the bottom of `relational.rs` (before tests):

```rust
use super::embedding_scorer::EmbeddingRelationalScorer;
// Note: FunctionContextMap is already imported at the top of this file

/// L3 relational scorer — either quantized embeddings (preferred) or Mahalanobis fallback.
pub enum RelationalScorer {
    Embedding(EmbeddingRelationalScorer),
    Mahalanobis(GraphRelationalScorer),
}

impl RelationalScorer {
    /// Compute anomaly distance for a function.
    /// Embedding variant ignores `contexts` (uses its own embeddings).
    pub fn distance(&self, qn: &str, contexts: &FunctionContextMap) -> f64 {
        match self {
            RelationalScorer::Embedding(scorer) => scorer.distance(qn),
            RelationalScorer::Mahalanobis(scorer) => scorer.distance(qn, contexts),
        }
    }
}
```

- [ ] **Step 2: Verify compilation**

Run: `cargo check`

- [ ] **Step 3: Commit**

```bash
git add src/predictive/relational.rs
git commit -m "feat(predictive): add RelationalScorer enum with Embedding/Mahalanobis variants"
```

---

### Task 4: Wire RelationalScorer into PredictiveCodingEngine

**Files:**
- Modify: `src/predictive/mod.rs`

- [ ] **Step 1: Update TrainedModels**

In `src/predictive/mod.rs`, change the `relational_scorer` field type in `TrainedModels` (line ~87):

From:
```rust
    relational_scorer: relational::GraphRelationalScorer,
```
To:
```rust
    relational_scorer: relational::RelationalScorer,
```

- [ ] **Step 2: Update train_models to accept optional embeddings**

Add a parameter to `train_models` and `train_and_score`:

In `train_and_score` (line ~107), add `embeddings` parameter:
```rust
    pub fn train_and_score(
        &mut self,
        graph: &dyn crate::graph::GraphQuery,
        files: &dyn crate::detectors::file_provider::FileProvider,
        contexts: &FunctionContextMap,
        cached_embeddings: Option<&embedding_scorer::CachedEmbeddings>,
    ) {
```

In `train_models`, update the L3 section (line ~233):

From:
```rust
        // === L3: Relational graph features (Mahalanobis distance) ===
        let relational_scorer = relational::GraphRelationalScorer::from_contexts(contexts);
```
To:
```rust
        // === L3: Relational graph features ===
        let relational_scorer = if let Some(cached) = cached_embeddings {
            tracing::debug!("[predictive] L3 using quantized node2vec embeddings ({} vectors)", cached.entries.len());
            relational::RelationalScorer::Embedding(
                embedding_scorer::EmbeddingRelationalScorer::from_cache(cached, 10),
            )
        } else {
            tracing::debug!("[predictive] L3 falling back to Mahalanobis (no cached embeddings)");
            relational::RelationalScorer::Mahalanobis(
                relational::GraphRelationalScorer::from_contexts(contexts),
            )
        };
```

- [ ] **Step 3: Update all callers of train_and_score**

Search for `train_and_score(` in the codebase and add the new `None` parameter where embeddings aren't available yet. The main caller is in `src/detectors/engine.rs` (the `precompute_gd_startup` function or similar). Pass `None` for now — Task 5 will wire in the real embeddings.

Run: `grep -rn "train_and_score" src/`

Update each callsite to pass `None` as the last argument.

- [ ] **Step 4: Verify compilation + tests**

Run: `cargo check && cargo test predictive -- --nocapture`

- [ ] **Step 5: Commit**

```bash
git add src/predictive/mod.rs src/detectors/
git commit -m "feat(predictive): wire RelationalScorer enum into PredictiveCodingEngine"
```

---

### Task 5: Load cached embeddings in precompute

**Files:**
- Modify: `src/detectors/engine.rs` (or wherever `train_and_score` is called)
- Modify: `src/engine/mod.rs` (pass session_path to precompute)

- [ ] **Step 1: Find where train_and_score is called**

Run: `grep -rn "train_and_score" src/`

Read the calling code to understand how to get `session_path` and `edge_fingerprint` to the call site.

- [ ] **Step 2: Load embeddings before train_and_score**

At the call site, add:
```rust
let cached_embeddings = if let Some(session_path) = session_path {
    crate::predictive::embedding_scorer::load_embeddings(
        session_path,
        edge_fingerprint,
    )
} else {
    None
};
engine.train_and_score(graph, files, contexts, cached_embeddings.as_ref());
```

This requires `session_path` and `edge_fingerprint` to be available at the precompute call site. They may need to be threaded through from `AnalysisEngine` or `DetectInput`.

- [ ] **Step 3: Verify compilation + tests**

Run: `cargo check && cargo test`

- [ ] **Step 4: Commit**

```bash
git add src/
git commit -m "feat(predictive): load cached embeddings for L3 scoring during precompute"
```

---

### Task 6: Spawn background embedding thread

**Files:**
- Modify: `src/engine/mod.rs`

- [ ] **Step 1: Add background thread spawn after cold analysis**

**Session path threading:** `AnalysisEngine` does NOT have a `session_path()` method. The
session path is passed as a parameter to `save()` and `load()` from the CLI layer. To make
it available for the background thread, add a `session_path: Option<PathBuf>` field to
`AnalysisEngine` and set it from the CLI when known.

In `src/engine/mod.rs`, add to the `AnalysisEngine` struct:
```rust
    /// Session directory path (set by CLI for cache operations).
    pub(crate) session_path: Option<PathBuf>,
```

Initialize to `None` in constructors. The CLI sets it after `load()` or before `save()`:
```rust
    engine.session_path = Some(session_dir.to_path_buf());
```

Find where the CLI calls `engine.save(session_path)` (likely in `src/cli/analyze/mod.rs`)
and also set `engine.session_path` there.

Then in `analyze_cold()`, right before the `Ok(AnalysisResult { ... })` return (~line 476):

```rust
        // Spawn background thread to compute L3 embeddings if not cached
        if let Some(ref session_path) = self.session_path {
            let graph_arc = Arc::clone(&frozen.graph);
            let sp = session_path.clone();
            let fingerprint = frozen.edge_fingerprint;

            // Only spawn if embeddings are missing or stale
            let embeddings_exist = crate::predictive::embedding_scorer::load_embeddings(
                &sp, fingerprint
            ).is_some();

            if !embeddings_exist {
                std::thread::spawn(move || {
                    tracing::debug!("Background: computing L3 node2vec embeddings...");
                    let t0 = std::time::Instant::now();
                    crate::predictive::embedding_scorer::compute_and_cache_embeddings(
                        graph_arc, sp, fingerprint,
                    );
                    tracing::debug!(
                        "Background: L3 embeddings complete in {:.1}s",
                        t0.elapsed().as_secs_f64()
                    );
                });
            }
        }
```

Only spawn on cold path. On incremental path, if `edge_fingerprint` changed (topology change),
also spawn the background thread using the same pattern.

- [ ] **Step 2: Also spawn on incremental path when topology changes**

On the incremental path, if `edge_fingerprint` changed (topology change), spawn the background thread there too.

- [ ] **Step 3: Verify compilation**

Run: `cargo check`

- [ ] **Step 4: Commit**

```bash
git add src/engine/mod.rs
git commit -m "feat(engine): spawn background thread for L3 embedding computation"
```

---

### Task 7: Integration test + manual verification

- [ ] **Step 1: Run full test suite**

Run: `cargo test`
Expected: all tests pass

- [ ] **Step 2: Run clippy**

Run: `RUSTFLAGS="-Dwarnings" cargo clippy --all-features`

- [ ] **Step 3: Manual test — cold run**

```bash
# Clear cache first
rm -rf ~/.cache/repotoire/*/embeddings.bin
# Cold run — L3 should fall back to Mahalanobis
./target/debug/repotoire analyze . --format text 2>&1 | grep -i "L3\|relational\|embedding"
# Check background thread completed
sleep 5
ls ~/.cache/repotoire/*/embeddings.bin
```

- [ ] **Step 4: Manual test — second run with embeddings**

```bash
# Second run — L3 should use quantized embeddings
./target/debug/repotoire analyze . --format text 2>&1 | grep -i "L3\|relational\|embedding"
```

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve integration issues from L3 embedding wiring"
```
