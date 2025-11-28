//! Benchmarks for graph algorithms (REPO-167)
//!
//! Run with: cargo bench --bench graph_bench
//!
//! These benchmarks measure:
//! - PageRank convergence and throughput
//! - Leiden community detection
//! - Strongly Connected Components (SCC)
//! - Betweenness and Harmonic centrality

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};
use repotoire_fast::graph_algo;

/// Generate a random graph with specified density.
fn generate_random_graph(num_nodes: usize, edge_probability: f64) -> Vec<(u32, u32)> {
    let mut edges = Vec::new();
    let seed = 42u64;
    let mut state = seed;

    for i in 0..num_nodes {
        for j in 0..num_nodes {
            if i != j {
                // Simple LCG random number generator
                state = state.wrapping_mul(1103515245).wrapping_add(12345);
                let rand = (state >> 16) as f64 / 65536.0;

                if rand < edge_probability {
                    edges.push((i as u32, j as u32));
                }
            }
        }
    }

    edges
}

/// Generate a scale-free graph (power-law degree distribution).
/// More realistic for code dependency graphs.
fn generate_scale_free_graph(num_nodes: usize, edges_per_node: usize) -> Vec<(u32, u32)> {
    let mut edges = Vec::new();
    let mut degrees = vec![0usize; num_nodes];
    let mut seed = 42u64;

    // Each new node connects to existing nodes with probability proportional to degree
    for i in 1..num_nodes {
        let mut connections = 0;
        let total_degree: usize = degrees.iter().sum::<usize>().max(1);

        for j in 0..i {
            if connections >= edges_per_node {
                break;
            }

            // Preferential attachment
            seed = seed.wrapping_mul(1103515245).wrapping_add(12345);
            let rand = (seed >> 16) as f64 / 65536.0;
            let attach_prob = (degrees[j] + 1) as f64 / total_degree as f64;

            if rand < attach_prob {
                edges.push((j as u32, i as u32));
                edges.push((i as u32, j as u32)); // Bidirectional
                degrees[j] += 1;
                degrees[i] += 1;
                connections += 1;
            }
        }

        // Ensure at least one connection
        if connections == 0 && i > 0 {
            let j = (seed as usize) % i;
            edges.push((j as u32, i as u32));
            edges.push((i as u32, j as u32));
            degrees[j] += 1;
            degrees[i] += 1;
        }
    }

    edges
}

/// Generate a graph with multiple connected components.
fn generate_clustered_graph(
    num_clusters: usize,
    nodes_per_cluster: usize,
    inter_cluster_edges: usize,
) -> Vec<(u32, u32)> {
    let mut edges = Vec::new();
    let num_nodes = num_clusters * nodes_per_cluster;
    let mut seed = 42u64;

    // Create dense intra-cluster edges
    for cluster in 0..num_clusters {
        let base = cluster * nodes_per_cluster;
        for i in 0..nodes_per_cluster {
            for j in (i + 1)..nodes_per_cluster {
                seed = seed.wrapping_mul(1103515245).wrapping_add(12345);
                if (seed >> 16) % 3 == 0 {
                    edges.push(((base + i) as u32, (base + j) as u32));
                    edges.push(((base + j) as u32, (base + i) as u32));
                }
            }
        }
    }

    // Add sparse inter-cluster edges
    for _ in 0..inter_cluster_edges {
        seed = seed.wrapping_mul(1103515245).wrapping_add(12345);
        let cluster1 = (seed as usize) % num_clusters;
        seed = seed.wrapping_mul(1103515245).wrapping_add(12345);
        let cluster2 = (seed as usize) % num_clusters;

        if cluster1 != cluster2 {
            let node1 = cluster1 * nodes_per_cluster + (seed as usize) % nodes_per_cluster;
            seed = seed.wrapping_mul(1103515245).wrapping_add(12345);
            let node2 = cluster2 * nodes_per_cluster + (seed as usize) % nodes_per_cluster;

            if node1 < num_nodes && node2 < num_nodes {
                edges.push((node1 as u32, node2 as u32));
                edges.push((node2 as u32, node1 as u32));
            }
        }
    }

    edges
}

/// Benchmark PageRank with varying graph sizes.
fn bench_pagerank(c: &mut Criterion) {
    let mut group = c.benchmark_group("pagerank");

    for num_nodes in [100, 500, 1000, 5000].iter() {
        let edges = generate_scale_free_graph(*num_nodes, 3);

        group.throughput(Throughput::Elements(*num_nodes as u64));
        group.bench_with_input(
            BenchmarkId::new("nodes", num_nodes),
            &(edges, *num_nodes),
            |b, (edges, num_nodes)| {
                b.iter(|| {
                    graph_algo::pagerank(
                        black_box(edges),
                        black_box(*num_nodes),
                        0.85,  // damping
                        20,    // max_iterations
                        1e-4,  // tolerance
                    )
                })
            }
        );
    }

    group.finish();
}

/// Benchmark Leiden community detection.
fn bench_leiden(c: &mut Criterion) {
    let mut group = c.benchmark_group("leiden");
    group.sample_size(10);

    for num_nodes in [100, 500, 1000, 5000].iter() {
        let edges = generate_clustered_graph(*num_nodes / 10, 10, *num_nodes / 20);

        group.throughput(Throughput::Elements(*num_nodes as u64));
        group.bench_with_input(
            BenchmarkId::new("nodes", num_nodes),
            &(edges, *num_nodes),
            |b, (edges, num_nodes)| {
                b.iter(|| {
                    graph_algo::leiden(
                        black_box(edges),
                        black_box(*num_nodes),
                        1.0,  // resolution
                        10,   // max_iterations
                    )
                })
            }
        );
    }

    group.finish();
}

/// Benchmark parallel Leiden vs sequential.
fn bench_leiden_parallel(c: &mut Criterion) {
    let mut group = c.benchmark_group("leiden_parallel_vs_sequential");
    group.sample_size(10);

    let num_nodes = 2000;
    let edges = generate_clustered_graph(20, 100, 100);

    group.bench_function("sequential", |b| {
        b.iter(|| {
            graph_algo::leiden_parallel(
                black_box(&edges),
                black_box(num_nodes),
                1.0,
                10,
                false, // sequential
            )
        })
    });

    group.bench_function("parallel", |b| {
        b.iter(|| {
            graph_algo::leiden_parallel(
                black_box(&edges),
                black_box(num_nodes),
                1.0,
                10,
                true, // parallel
            )
        })
    });

    group.finish();
}

/// Benchmark SCC (Strongly Connected Components).
fn bench_scc(c: &mut Criterion) {
    let mut group = c.benchmark_group("scc");

    for num_nodes in [100, 500, 1000, 5000].iter() {
        let edges = generate_scale_free_graph(*num_nodes, 2);

        group.throughput(Throughput::Elements(*num_nodes as u64));
        group.bench_with_input(
            BenchmarkId::new("nodes", num_nodes),
            &(edges, *num_nodes),
            |b, (edges, num_nodes)| {
                b.iter(|| {
                    graph_algo::find_sccs(
                        black_box(edges),
                        black_box(*num_nodes),
                    )
                })
            }
        );
    }

    group.finish();
}

/// Benchmark Betweenness Centrality.
fn bench_betweenness(c: &mut Criterion) {
    let mut group = c.benchmark_group("betweenness_centrality");
    group.sample_size(10);

    // Betweenness is O(V*E), so use smaller graphs
    for num_nodes in [50, 100, 200, 500].iter() {
        let edges = generate_scale_free_graph(*num_nodes, 3);

        group.throughput(Throughput::Elements(*num_nodes as u64));
        group.bench_with_input(
            BenchmarkId::new("nodes", num_nodes),
            &(edges, *num_nodes),
            |b, (edges, num_nodes)| {
                b.iter(|| {
                    graph_algo::betweenness_centrality(
                        black_box(edges),
                        black_box(*num_nodes),
                    )
                })
            }
        );
    }

    group.finish();
}

/// Benchmark Harmonic Centrality.
fn bench_harmonic(c: &mut Criterion) {
    let mut group = c.benchmark_group("harmonic_centrality");
    group.sample_size(10);

    for num_nodes in [100, 500, 1000, 2000].iter() {
        let edges = generate_scale_free_graph(*num_nodes, 3);

        group.throughput(Throughput::Elements(*num_nodes as u64));
        group.bench_with_input(
            BenchmarkId::new("nodes", num_nodes),
            &(edges, *num_nodes),
            |b, (edges, num_nodes)| {
                b.iter(|| {
                    graph_algo::harmonic_centrality(
                        black_box(edges),
                        black_box(*num_nodes),
                        true, // normalized
                    )
                })
            }
        );
    }

    group.finish();
}

/// Benchmark with graph density variations.
fn bench_density_impact(c: &mut Criterion) {
    let mut group = c.benchmark_group("density_impact");
    group.sample_size(10);

    let num_nodes = 500;

    for density in ["sparse", "medium", "dense"].iter() {
        let (edges_per_node, label) = match *density {
            "sparse" => (2, "sparse_2"),
            "medium" => (5, "medium_5"),
            "dense" => (10, "dense_10"),
            _ => (3, "default"),
        };

        let edges = generate_scale_free_graph(num_nodes, edges_per_node);

        group.bench_with_input(
            BenchmarkId::new("pagerank", label),
            &edges,
            |b, edges| {
                b.iter(|| {
                    graph_algo::pagerank(black_box(edges), num_nodes, 0.85, 20, 1e-4)
                })
            }
        );
    }

    group.finish();
}

criterion_group!(
    benches,
    bench_pagerank,
    bench_leiden,
    bench_leiden_parallel,
    bench_scc,
    bench_betweenness,
    bench_harmonic,
    bench_density_impact,
);

criterion_main!(benches);
