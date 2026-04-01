//! L3 Relational scoring via quantized node2vec embeddings + kNN anomaly detection.
//!
//! Computes node2vec embeddings in a background thread, quantizes with TurboQuant (4-bit),
//! caches in session directory. On subsequent runs, loads cached embeddings and scores
//! each function by ADC distance to its k-th nearest neighbor.

use crate::quantize::turbo_quant::{QuantizedVector, TurboQuantCodebook, TurboQuantConfig};
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

        Self {
            codebook,
            all_quantized,
            qn_to_idx,
            k,
        }
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
        let mut distances: Vec<f64> = self
            .all_quantized
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
    let cached: CachedEmbeddings = bitcode::deserialize(&data).ok()?;
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
    let data = bitcode::serialize(cached)?;
    std::fs::write(&tmp_path, &data)?;
    std::fs::rename(&tmp_path, &path)?;
    tracing::debug!(
        "Saved {} embeddings to cache ({} bytes)",
        cached.entries.len(),
        data.len()
    );
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
        &edges,
        num_nodes,
        10, // walk_length (tuned down from 20)
        3,  // walks_per_node (tuned down from 10)
        1.0,
        1.0, // p, q (balanced)
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
    let config = TurboQuantConfig {
        dim: 64,
        bits: 4,
        seed: 42,
    };
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
            entries: vec![CachedEntry {
                qualified_name: "foo.bar".into(),
                indices: vec![0u8; 32],
                norm: 1.5,
            }],
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
