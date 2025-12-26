//! Benchmarks for Word2Vec skip-gram training (REPO-249)
//!
//! Run with: cargo bench --bench word2vec_bench
//!
//! These benchmarks measure:
//! - Training throughput (walks/second)
//! - Vocabulary building
//! - Scaling with number of walks and walk length

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};
use repotoire_fast::word2vec::{train_skipgram, train_skipgram_parallel, Word2VecConfig};

/// Generate random walks for benchmarking.
///
/// Simulates Node2Vec output with given parameters.
fn generate_random_walks(
    num_walks: usize,
    walk_length: usize,
    vocab_size: usize,
    seed: u64,
) -> Vec<Vec<u32>> {
    let mut walks = Vec::with_capacity(num_walks);
    let mut state = seed;

    for _ in 0..num_walks {
        let mut walk = Vec::with_capacity(walk_length);
        for _ in 0..walk_length {
            // Simple LCG random number generator
            state = state.wrapping_mul(1103515245).wrapping_add(12345);
            let node_id = ((state >> 16) as usize % vocab_size) as u32;
            walk.push(node_id);
        }
        walks.push(walk);
    }

    walks
}

/// Generate walks with cluster structure for more realistic benchmarking.
fn generate_clustered_walks(
    num_walks: usize,
    walk_length: usize,
    num_clusters: usize,
    nodes_per_cluster: usize,
    seed: u64,
) -> Vec<Vec<u32>> {
    let mut walks = Vec::with_capacity(num_walks);
    let mut state = seed;

    for i in 0..num_walks {
        let mut walk = Vec::with_capacity(walk_length);

        // Pick a starting cluster
        let cluster = i % num_clusters;
        let base_node = (cluster * nodes_per_cluster) as u32;

        for _ in 0..walk_length {
            state = state.wrapping_mul(1103515245).wrapping_add(12345);

            // 90% chance to stay in cluster, 10% to jump
            if (state >> 16) % 10 < 9 {
                let offset = ((state >> 24) as usize % nodes_per_cluster) as u32;
                walk.push(base_node + offset);
            } else {
                let other_cluster = ((state >> 32) as usize % num_clusters) as u32;
                let offset = ((state >> 40) as usize % nodes_per_cluster) as u32;
                walk.push(other_cluster * nodes_per_cluster as u32 + offset);
            }
        }
        walks.push(walk);
    }

    walks
}

/// Benchmark training with different numbers of walks.
fn bench_walk_count(c: &mut Criterion) {
    let mut group = c.benchmark_group("word2vec_walk_count");

    let walk_length = 50;
    let vocab_size = 500;

    for num_walks in [100, 500, 1000, 2000] {
        let walks = generate_random_walks(num_walks, walk_length, vocab_size, 42);

        group.throughput(Throughput::Elements(num_walks as u64));
        group.bench_with_input(
            BenchmarkId::new("random_walks", num_walks),
            &walks,
            |b, walks| {
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

                b.iter(|| {
                    black_box(train_skipgram(black_box(walks), black_box(&config)))
                });
            },
        );
    }

    group.finish();
}

/// Benchmark training with different walk lengths.
fn bench_walk_length(c: &mut Criterion) {
    let mut group = c.benchmark_group("word2vec_walk_length");

    let num_walks = 500;
    let vocab_size = 500;

    for walk_length in [20, 40, 80, 120] {
        let walks = generate_random_walks(num_walks, walk_length, vocab_size, 42);

        group.throughput(Throughput::Elements((num_walks * walk_length) as u64));
        group.bench_with_input(
            BenchmarkId::new("walk_length", walk_length),
            &walks,
            |b, walks| {
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

                b.iter(|| {
                    black_box(train_skipgram(black_box(walks), black_box(&config)))
                });
            },
        );
    }

    group.finish();
}

/// Benchmark training with different embedding dimensions.
fn bench_embedding_dim(c: &mut Criterion) {
    let mut group = c.benchmark_group("word2vec_embedding_dim");

    let num_walks = 500;
    let walk_length = 50;
    let vocab_size = 500;
    let walks = generate_random_walks(num_walks, walk_length, vocab_size, 42);

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

                b.iter(|| {
                    black_box(train_skipgram(black_box(walks), black_box(&config)))
                });
            },
        );
    }

    group.finish();
}

/// Benchmark training with different negative sample counts.
fn bench_negative_samples(c: &mut Criterion) {
    let mut group = c.benchmark_group("word2vec_negative_samples");

    let num_walks = 500;
    let walk_length = 50;
    let vocab_size = 500;
    let walks = generate_random_walks(num_walks, walk_length, vocab_size, 42);

    for negative_samples in [1, 5, 10, 20] {
        group.bench_with_input(
            BenchmarkId::new("neg_samples", negative_samples),
            &walks,
            |b, walks| {
                let config = Word2VecConfig {
                    embedding_dim: 64,
                    window_size: 5,
                    min_count: 1,
                    negative_samples,
                    learning_rate: 0.025,
                    min_learning_rate: 0.0001,
                    epochs: 3,
                    seed: Some(42),
                };

                b.iter(|| {
                    black_box(train_skipgram(black_box(walks), black_box(&config)))
                });
            },
        );
    }

    group.finish();
}

/// Benchmark with clustered walks (more realistic for graph embedding).
fn bench_clustered_walks(c: &mut Criterion) {
    let mut group = c.benchmark_group("word2vec_clustered");

    for (num_clusters, nodes_per_cluster) in [(10, 50), (20, 50), (50, 20)] {
        let num_walks = 1000;
        let walk_length = 50;
        let walks = generate_clustered_walks(
            num_walks,
            walk_length,
            num_clusters,
            nodes_per_cluster,
            42,
        );

        group.bench_with_input(
            BenchmarkId::new("clusters", format!("{}x{}", num_clusters, nodes_per_cluster)),
            &walks,
            |b, walks| {
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

                b.iter(|| {
                    black_box(train_skipgram(black_box(walks), black_box(&config)))
                });
            },
        );
    }

    group.finish();
}

/// Benchmark a realistic Node2Vec workload.
fn bench_realistic_node2vec(c: &mut Criterion) {
    let mut group = c.benchmark_group("word2vec_realistic");
    group.sample_size(10); // Fewer samples for longer benchmarks

    // Realistic Node2Vec parameters:
    // - 500 nodes (medium codebase)
    // - 10 walks per node = 5000 walks
    // - walk length = 80
    // - embedding dim = 128

    let num_walks = 5000;
    let walk_length = 80;
    let vocab_size = 500;
    let walks = generate_random_walks(num_walks, walk_length, vocab_size, 42);

    group.bench_function("realistic_workload", |b| {
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

        b.iter(|| {
            black_box(train_skipgram(black_box(&walks), black_box(&config)))
        });
    });

    group.finish();
}

/// Benchmark sequential vs parallel training.
fn bench_sequential_vs_parallel(c: &mut Criterion) {
    let mut group = c.benchmark_group("word2vec_parallel_vs_sequential");
    group.sample_size(10); // Fewer samples for longer benchmarks

    // Test with realistic Node2Vec workload
    let num_walks = 5000;
    let walk_length = 80;
    let vocab_size = 500;
    let walks = generate_random_walks(num_walks, walk_length, vocab_size, 42);

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

    group.throughput(Throughput::Elements(num_walks as u64));

    // Sequential training
    group.bench_with_input(BenchmarkId::new("method", "sequential"), &walks, |b, walks| {
        b.iter(|| black_box(train_skipgram(black_box(walks), black_box(&config))));
    });

    // Parallel training (Hogwild!)
    group.bench_with_input(BenchmarkId::new("method", "parallel"), &walks, |b, walks| {
        b.iter(|| black_box(train_skipgram_parallel(black_box(walks), black_box(&config))));
    });

    group.finish();
}

/// Benchmark parallel scaling with different workload sizes.
fn bench_parallel_scaling(c: &mut Criterion) {
    let mut group = c.benchmark_group("word2vec_parallel_scaling");
    group.sample_size(10);

    for num_walks in [1000, 5000, 10000] {
        let walk_length = 80;
        let vocab_size = 500;
        let walks = generate_random_walks(num_walks, walk_length, vocab_size, 42);

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

        group.throughput(Throughput::Elements(num_walks as u64));
        group.bench_with_input(
            BenchmarkId::new("parallel_walks", num_walks),
            &walks,
            |b, walks| {
                b.iter(|| black_box(train_skipgram_parallel(black_box(walks), black_box(&config))));
            },
        );
    }

    group.finish();
}

criterion_group!(
    benches,
    bench_walk_count,
    bench_walk_length,
    bench_embedding_dim,
    bench_negative_samples,
    bench_clustered_walks,
    bench_realistic_node2vec,
    bench_sequential_vs_parallel,
    bench_parallel_scaling,
);

criterion_main!(benches);
