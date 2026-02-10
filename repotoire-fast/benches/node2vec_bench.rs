//! Benchmarks for complete Node2Vec pipeline (REPO-250)
//!
//! Run with: cargo bench --bench node2vec_bench
//!
//! These benchmarks measure:
//! - End-to-end Node2Vec performance (walks + training)
//! - Scaling with graph size
//! - Performance with different parameters

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};
use repotoire_fast::graph_algo::node2vec_random_walks;
use repotoire_fast::word2vec::{train_skipgram, Word2VecConfig};

/// Generate a random graph with given parameters.
fn generate_random_graph(num_nodes: usize, avg_degree: usize, seed: u64) -> Vec<(u32, u32)> {
    let mut edges = Vec::new();
    let mut state = seed;

    for src in 0..num_nodes {
        for _ in 0..avg_degree {
            // Simple LCG random number generator
            state = state.wrapping_mul(1103515245).wrapping_add(12345);
            let dst = ((state >> 16) as usize % num_nodes) as u32;
            if dst != src as u32 {
                edges.push((src as u32, dst));
            }
        }
    }

    edges
}

/// Generate a clustered graph (more realistic for codebases).
fn generate_clustered_graph(
    num_clusters: usize,
    nodes_per_cluster: usize,
    intra_connections: usize,
    inter_connections: usize,
    seed: u64,
) -> Vec<(u32, u32)> {
    let mut edges = Vec::new();
    let mut state = seed;
    let num_nodes = num_clusters * nodes_per_cluster;

    for cluster in 0..num_clusters {
        let base = cluster * nodes_per_cluster;

        // Dense intra-cluster connections
        for _ in 0..intra_connections {
            for node in base..(base + nodes_per_cluster) {
                state = state.wrapping_mul(1103515245).wrapping_add(12345);
                let offset = ((state >> 16) as usize % nodes_per_cluster) as u32;
                let dst = base as u32 + offset;
                if dst != node as u32 {
                    edges.push((node as u32, dst));
                }
            }
        }

        // Sparse inter-cluster connections
        for _ in 0..inter_connections {
            state = state.wrapping_mul(1103515245).wrapping_add(12345);
            let src = base + ((state >> 16) as usize % nodes_per_cluster);
            state = state.wrapping_mul(1103515245).wrapping_add(12345);
            let dst_cluster = ((state >> 24) as usize % num_clusters) as u32;
            state = state.wrapping_mul(1103515245).wrapping_add(12345);
            let dst_offset = ((state >> 32) as usize % nodes_per_cluster) as u32;
            let dst = dst_cluster * nodes_per_cluster as u32 + dst_offset;
            if dst != src as u32 && dst < num_nodes as u32 {
                edges.push((src as u32, dst));
            }
        }
    }

    edges
}

/// Benchmark full Node2Vec pipeline with different graph sizes.
fn bench_node2vec_graph_size(c: &mut Criterion) {
    let mut group = c.benchmark_group("node2vec_graph_size");
    group.sample_size(10);

    for num_nodes in [100, 500, 1000] {
        let edges = generate_random_graph(num_nodes, 5, 42);

        group.throughput(Throughput::Elements(num_nodes as u64));
        group.bench_with_input(
            BenchmarkId::new("nodes", num_nodes),
            &(edges, num_nodes),
            |b, (edges, num_nodes)| {
                b.iter(|| {
                    // Generate walks
                    let walks = node2vec_random_walks(
                        edges,
                        *num_nodes,
                        80,  // walk_length
                        10,  // walks_per_node
                        1.0, // p
                        1.0, // q
                        Some(42),
                    )
                    .unwrap();

                    // Train embeddings
                    let config = Word2VecConfig {
                        embedding_dim: 64,
                        window_size: 5,
                        min_count: 1,
                        negative_samples: 5,
                        learning_rate: 0.025,
                        min_learning_rate: 0.0001,
                        epochs: 3,
                        seed: Some(42),
                    };

                    black_box(train_skipgram(&walks, &config))
                });
            },
        );
    }

    group.finish();
}

/// Benchmark walks generation separately.
fn bench_walks_generation(c: &mut Criterion) {
    let mut group = c.benchmark_group("node2vec_walks");

    for num_nodes in [100, 500, 1000] {
        let edges = generate_random_graph(num_nodes, 5, 42);

        group.throughput(Throughput::Elements(num_nodes as u64));
        group.bench_with_input(
            BenchmarkId::new("nodes", num_nodes),
            &(edges, num_nodes),
            |b, (edges, num_nodes)| {
                b.iter(|| {
                    black_box(
                        node2vec_random_walks(edges, *num_nodes, 80, 10, 1.0, 1.0, Some(42)).unwrap(),
                    )
                });
            },
        );
    }

    group.finish();
}

/// Benchmark with clustered graphs (more realistic).
fn bench_clustered_graphs(c: &mut Criterion) {
    let mut group = c.benchmark_group("node2vec_clustered");
    group.sample_size(10);

    for (num_clusters, nodes_per_cluster) in [(5, 100), (10, 50), (20, 25)] {
        let num_nodes = num_clusters * nodes_per_cluster;
        let edges = generate_clustered_graph(num_clusters, nodes_per_cluster, 3, 2, 42);

        group.bench_with_input(
            BenchmarkId::new("clusters", format!("{}x{}", num_clusters, nodes_per_cluster)),
            &(edges, num_nodes),
            |b, (edges, num_nodes)| {
                b.iter(|| {
                    let walks = node2vec_random_walks(edges, *num_nodes, 80, 10, 1.0, 1.0, Some(42))
                        .unwrap();

                    let config = Word2VecConfig {
                        embedding_dim: 64,
                        window_size: 5,
                        min_count: 1,
                        negative_samples: 5,
                        learning_rate: 0.025,
                        min_learning_rate: 0.0001,
                        epochs: 3,
                        seed: Some(42),
                    };

                    black_box(train_skipgram(&walks, &config))
                });
            },
        );
    }

    group.finish();
}

/// Benchmark different embedding dimensions.
fn bench_embedding_dimensions(c: &mut Criterion) {
    let mut group = c.benchmark_group("node2vec_embedding_dim");
    group.sample_size(10);

    let edges = generate_random_graph(500, 5, 42);
    let num_nodes = 500;

    // Pre-generate walks
    let walks = node2vec_random_walks(&edges, num_nodes, 80, 10, 1.0, 1.0, Some(42)).unwrap();

    for embedding_dim in [32, 64, 128, 256] {
        group.bench_with_input(
            BenchmarkId::new("dim", embedding_dim),
            &walks,
            |b, walks| {
                let config = Word2VecConfig {
                    embedding_dim,
                    window_size: 5,
                    min_count: 1,
                    negative_samples: 5,
                    learning_rate: 0.025,
                    min_learning_rate: 0.0001,
                    epochs: 3,
                    seed: Some(42),
                };

                b.iter(|| black_box(train_skipgram(walks, &config)));
            },
        );
    }

    group.finish();
}

/// Benchmark p/q parameter effects on walk generation.
fn bench_p_q_parameters(c: &mut Criterion) {
    let mut group = c.benchmark_group("node2vec_p_q");

    let edges = generate_random_graph(500, 5, 42);
    let num_nodes = 500;

    for (p, q, name) in [(1.0, 1.0, "default"), (0.5, 2.0, "bfs"), (2.0, 0.5, "dfs")] {
        group.bench_with_input(BenchmarkId::new("strategy", name), &edges, |b, edges| {
            b.iter(|| {
                black_box(node2vec_random_walks(edges, num_nodes, 80, 10, p, q, Some(42)).unwrap())
            });
        });
    }

    group.finish();
}

/// Realistic Node2Vec workload benchmark.
fn bench_realistic_workload(c: &mut Criterion) {
    let mut group = c.benchmark_group("node2vec_realistic");
    group.sample_size(10);

    // Simulate a medium-sized codebase:
    // - 20 modules with 25 functions each = 500 functions
    // - Dense intra-module connections, sparse inter-module
    let edges = generate_clustered_graph(20, 25, 5, 3, 42);
    let num_nodes = 500;

    group.bench_function("medium_codebase", |b| {
        b.iter(|| {
            let walks =
                node2vec_random_walks(&edges, num_nodes, 80, 10, 1.0, 1.0, Some(42)).unwrap();

            let config = Word2VecConfig {
                embedding_dim: 128,
                window_size: 10,
                min_count: 1,
                negative_samples: 5,
                learning_rate: 0.025,
                min_learning_rate: 0.0001,
                epochs: 5,
                seed: Some(42),
            };

            black_box(train_skipgram(&walks, &config))
        });
    });

    group.finish();
}

criterion_group!(
    benches,
    bench_node2vec_graph_size,
    bench_walks_generation,
    bench_clustered_graphs,
    bench_embedding_dimensions,
    bench_p_q_parameters,
    bench_realistic_workload,
);

criterion_main!(benches);
