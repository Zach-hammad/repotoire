//! TurboQuant benchmark harness.
//!
//! Builds a code graph from repotoire's own source, generates node2vec embeddings,
//! quantizes with TurboQuant (4-bit), and measures compression ratio, cosine
//! similarity, MSE, and recall@k against a naive uniform baseline.
//!
//! Run with: `cargo test turbo_quant_benchmark -- --ignored --nocapture`

use std::collections::BTreeMap;
use std::path::Path;
use std::time::Instant;

use rand::seq::SliceRandom;
use rustc_hash::FxHashMap;

use crate::engine::stages::collect::{collect_stage, CollectInput};
use crate::engine::stages::graph::{freeze_graph, graph_stage, GraphInput};
use crate::engine::stages::parse::{parse_stage, ParseInput};
use crate::predictive::embeddings::{node2vec_random_walks, train_skipgram, Word2VecConfig};
use crate::quantize::turbo_quant::{
    quantize_scalar, uniform_codebook_4bit, QuantizedVector, TurboQuantCodebook, TurboQuantConfig,
};

/// Cosine similarity between two f64 slices.
fn cosine_sim(a: &[f64], b: &[f64]) -> f64 {
    let dot: f64 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let na: f64 = a.iter().map(|x| x * x).sum::<f64>().sqrt();
    let nb: f64 = b.iter().map(|x| x * x).sum::<f64>().sqrt();
    if na == 0.0 || nb == 0.0 {
        return 0.0;
    }
    dot / (na * nb)
}

/// Mean squared error between two f64 slices.
fn mse(a: &[f64], b: &[f64]) -> f64 {
    let n = a.len() as f64;
    a.iter().zip(b).map(|(x, y)| (x - y).powi(2)).sum::<f64>() / n
}

/// Brute-force exact kNN by cosine similarity (descending). Returns sorted indices.
fn exact_knn(query: &[f64], database: &[Vec<f64>], k: usize) -> Vec<usize> {
    let mut scored: Vec<(usize, f64)> = database
        .iter()
        .enumerate()
        .map(|(i, v)| (i, cosine_sim(query, v)))
        .collect();
    scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    scored.truncate(k);
    scored.into_iter().map(|(i, _)| i).collect()
}

/// Recall@k: fraction of exact top-k that appear in approximate top-k.
fn recall_at_k(exact: &[usize], approx: &[usize]) -> f64 {
    let hit_count = exact.iter().filter(|i| approx.contains(i)).count();
    hit_count as f64 / exact.len() as f64
}

/// Quantize using the naive uniform baseline (no rotation, uniform codebook).
fn quantize_naive(x: &[f64], dim: usize) -> (Vec<u8>, f64) {
    let norm: f64 = x.iter().map(|v| v * v).sum::<f64>().sqrt();
    let inv = if norm > 0.0 { 1.0 / norm } else { 1.0 };
    let (centroids, boundaries) = uniform_codebook_4bit(dim);
    let indices: Vec<u8> = x.iter().map(|v| quantize_scalar(v * inv, &boundaries)).collect();
    let recon: Vec<f64> = indices
        .iter()
        .map(|&idx| centroids[idx as usize] * norm)
        .collect();
    let cos = cosine_sim(x, &recon);
    (indices, cos)
}

#[test]
#[ignore]
fn turbo_quant_benchmark() {
    let t_total = Instant::now();

    // ── 1. Build code graph from repotoire's own source ──────────────────
    let repo = Path::new(env!("CARGO_MANIFEST_DIR"));

    let collect_out = collect_stage(&CollectInput {
        repo_path: repo,
        exclude_patterns: &[],
        max_files: 10_000,
    })
    .expect("collect_stage failed");

    let parse_out = parse_stage(&ParseInput {
        files: collect_out.all_paths(),
        workers: 8,
        progress: None,
    })
    .expect("parse_stage failed");

    let graph_out = graph_stage(&GraphInput {
        parse_results: &parse_out.results,
        repo_path: repo,
    })
    .expect("graph_stage failed");

    let frozen = freeze_graph(graph_out.mutable_graph, graph_out.value_store, None);
    let code_graph = &frozen.graph;

    let edges: Vec<(u32, u32)> = code_graph
        .all_call_edges()
        .iter()
        .map(|(a, b)| (a.index() as u32, b.index() as u32))
        .collect();
    let num_nodes = code_graph.node_count();

    println!(
        "Graph: {} nodes, {} edges, {} files parsed",
        num_nodes,
        edges.len(),
        parse_out.stats.files_parsed
    );

    // ── 2. Generate 128-D embeddings via node2vec ────────────────────────
    let t_embed = Instant::now();
    let walks = node2vec_random_walks(&edges, num_nodes, 20, 10, 1.0, 1.0, Some(42));
    let w2v = train_skipgram(
        &walks,
        &Word2VecConfig {
            embedding_dim: 128,
            seed: Some(42),
            ..Default::default()
        },
    );
    let embed_ms = t_embed.elapsed().as_millis();
    println!(
        "Embeddings: {} vectors in {}ms",
        w2v.embeddings.len(),
        embed_ms
    );

    // Collect embeddings as f64 vecs, keyed by node id.
    let embeddings_f64: FxHashMap<u32, Vec<f64>> = w2v
        .embeddings
        .iter()
        .map(|(&id, v)| (id, v.iter().map(|&x| x as f64).collect()))
        .collect();

    assert!(
        embeddings_f64.len() >= 50,
        "Need at least 50 embeddings, got {}",
        embeddings_f64.len()
    );

    // ── 3. Quantize all embeddings with TurboQuant (4-bit) ───────────────
    let t_quant = Instant::now();
    let cb = TurboQuantCodebook::new(TurboQuantConfig::default());

    let mut ids: Vec<u32> = embeddings_f64.keys().copied().collect();
    ids.sort();

    let quantized: Vec<QuantizedVector> = ids.iter().map(|id| cb.quantize(&embeddings_f64[id])).collect();
    let quant_ms = t_quant.elapsed().as_millis();

    // ── 4. Measure compression ratio ─────────────────────────────────────
    let raw_bytes = ids.len() * 128 * 8; // 128 f64s per vector
    let packed_bytes: usize = quantized.iter().map(|qv| qv.indices.len() + 8).sum(); // packed + f64 norm
    let compression_ratio = raw_bytes as f64 / packed_bytes as f64;

    // ── 5. Measure cosine similarity and MSE ─────────────────────────────
    let mut cosines = Vec::with_capacity(ids.len());
    let mut mses = Vec::with_capacity(ids.len());

    for (i, id) in ids.iter().enumerate() {
        let orig = &embeddings_f64[id];
        let recon = cb.reconstruct(&quantized[i]);
        cosines.push(cosine_sim(orig, &recon));
        mses.push(mse(orig, &recon));
    }

    let avg_cosine = cosines.iter().sum::<f64>() / cosines.len() as f64;
    let min_cosine = cosines.iter().cloned().fold(f64::INFINITY, f64::min);
    let avg_mse = mses.iter().sum::<f64>() / mses.len() as f64;

    // ── 6. Naive uniform baseline ────────────────────────────────────────
    let mut naive_cosines = Vec::with_capacity(ids.len());
    for id in &ids {
        let orig = &embeddings_f64[id];
        let (_, cos) = quantize_naive(orig, 128);
        naive_cosines.push(cos);
    }
    let naive_avg_cosine = naive_cosines.iter().sum::<f64>() / naive_cosines.len() as f64;

    // ── 7. Recall@k ─────────────────────────────────────────────────────
    let db_vecs: Vec<Vec<f64>> = ids.iter().map(|id| embeddings_f64[id].clone()).collect();
    let db_f64_refs: &[Vec<f64>] = &db_vecs;

    // Sample query vectors for recall measurement.
    let mut rng = rand::rng();
    let mut query_indices: Vec<usize> = (0..ids.len()).collect();
    query_indices.shuffle(&mut rng);
    let num_queries = 50.min(ids.len());
    query_indices.truncate(num_queries);

    let ks = [1, 5, 10, 50];
    let mut recall_sums: BTreeMap<usize, f64> = ks.iter().map(|&k| (k, 0.0)).collect();

    for &qi in &query_indices {
        let query = &db_vecs[qi];
        let query_f64: &[f64] = query.as_slice();

        for &k in &ks {
            let effective_k = k.min(ids.len());
            let exact_topk = exact_knn(query_f64, db_f64_refs, effective_k);
            let approx_results = cb.knn_search(query_f64, &quantized, effective_k);
            let approx_topk: Vec<usize> = approx_results.iter().map(|(i, _)| *i).collect();
            *recall_sums.get_mut(&k).unwrap() += recall_at_k(&exact_topk, &approx_topk);
        }
    }

    let recall: BTreeMap<String, f64> = recall_sums
        .iter()
        .map(|(&k, &sum)| (format!("recall@{}", k), sum / num_queries as f64))
        .collect();

    // ── 8. JSON report ───────────────────────────────────────────────────
    let total_ms = t_total.elapsed().as_millis();

    let report = serde_json::json!({
        "benchmark": "turbo_quant_on_repotoire",
        "num_vectors": ids.len(),
        "dim": 128,
        "bits": 4,
        "compression_ratio": format!("{:.1}x", compression_ratio),
        "cosine_similarity": {
            "mean": format!("{:.6}", avg_cosine),
            "min": format!("{:.6}", min_cosine),
        },
        "mse": format!("{:.8}", avg_mse),
        "naive_baseline": {
            "cosine_mean": format!("{:.6}", naive_avg_cosine),
        },
        "recall": recall,
        "timing_ms": {
            "embeddings": embed_ms,
            "quantization": quant_ms,
            "total": total_ms,
        },
        "graph": {
            "nodes": num_nodes,
            "edges": edges.len(),
            "files_parsed": parse_out.stats.files_parsed,
        }
    });

    println!("\n{}", serde_json::to_string_pretty(&report).unwrap());

    // ── 9. Assertions ────────────────────────────────────────────────────
    assert!(
        avg_cosine >= 0.99,
        "Average cosine similarity {avg_cosine:.6} < 0.99"
    );
    assert!(
        compression_ratio >= 7.0,
        "Compression ratio {compression_ratio:.1}x < 7x"
    );
    assert!(
        avg_cosine > naive_avg_cosine,
        "Lloyd-Max ({avg_cosine:.6}) should beat naive ({naive_avg_cosine:.6})"
    );
}
