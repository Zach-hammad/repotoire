//! Benchmarks for duplicate code detection (REPO-167)
//!
//! Run with: cargo bench --bench duplicate_bench
//!
//! These benchmarks measure:
//! - Tokenization throughput
//! - Rolling hash computation
//! - Full duplicate detection across varying file counts

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};
use repotoire_fast::duplicate;

/// Generate realistic Python-like source code for benchmarking.
fn generate_python_source(lines: usize, unique_suffix: usize) -> String {
    let mut source = String::with_capacity(lines * 50);

    source.push_str(&format!("# Generated file {}\n", unique_suffix));
    source.push_str("import os\n");
    source.push_str("import sys\n");
    source.push_str("from typing import List, Dict, Optional\n\n");

    // Generate some class definitions
    source.push_str(&format!("class Example{}:\n", unique_suffix));
    source.push_str("    \"\"\"Example class with methods.\"\"\"\n\n");
    source.push_str("    def __init__(self, value: int):\n");
    source.push_str("        self.value = value\n");
    source.push_str("        self._cache = {}\n\n");

    // Generate methods
    for i in 0..lines / 10 {
        source.push_str(&format!("    def method_{}(self, arg: int) -> int:\n", i));
        source.push_str("        \"\"\"Process the argument.\"\"\"\n");
        source.push_str("        result = self.value + arg\n");
        source.push_str("        if result > 100:\n");
        source.push_str("            result = result % 100\n");
        source.push_str("        self._cache[arg] = result\n");
        source.push_str("        return result\n\n");
    }

    // Add some common code patterns (to create duplicates)
    source.push_str("def validate_input(data: Dict) -> bool:\n");
    source.push_str("    \"\"\"Validate input data.\"\"\"\n");
    source.push_str("    if not data:\n");
    source.push_str("        return False\n");
    source.push_str("    if 'id' not in data:\n");
    source.push_str("        return False\n");
    source.push_str("    if 'name' not in data:\n");
    source.push_str("        return False\n");
    source.push_str("    return True\n\n");

    source.push_str("def process_data(items: List[Dict]) -> List[Dict]:\n");
    source.push_str("    \"\"\"Process a list of data items.\"\"\"\n");
    source.push_str("    results = []\n");
    source.push_str("    for item in items:\n");
    source.push_str("        if validate_input(item):\n");
    source.push_str("            processed = {\n");
    source.push_str("                'id': item['id'],\n");
    source.push_str("                'name': item['name'].strip(),\n");
    source.push_str("                'processed': True,\n");
    source.push_str("            }\n");
    source.push_str("            results.append(processed)\n");
    source.push_str("    return results\n");

    source
}

/// Generate test files with controlled duplication.
fn generate_test_files(file_count: usize, lines_per_file: usize) -> Vec<(String, String)> {
    (0..file_count)
        .map(|i| {
            let path = format!("src/module_{}/file_{}.py", i / 10, i);
            let source = generate_python_source(lines_per_file, i);
            (path, source)
        })
        .collect()
}

/// Benchmark tokenization throughput.
fn bench_tokenization(c: &mut Criterion) {
    let mut group = c.benchmark_group("tokenization");

    for lines in [100, 500, 1000, 5000].iter() {
        let source = generate_python_source(*lines, 0);
        let bytes = source.len();

        group.throughput(Throughput::Bytes(bytes as u64));
        group.bench_with_input(
            BenchmarkId::new("tokenize", lines),
            &source,
            |b, source| {
                b.iter(|| duplicate::tokenize(black_box(source)))
            }
        );
    }

    group.finish();
}

/// Benchmark rolling hash computation.
fn bench_rolling_hash(c: &mut Criterion) {
    let mut group = c.benchmark_group("rolling_hash");

    for token_count in [100, 500, 1000, 5000].iter() {
        // Generate tokens
        let source = generate_python_source(*token_count / 5, 0);
        let tokens = duplicate::tokenize(&source);

        group.throughput(Throughput::Elements(tokens.len() as u64));
        group.bench_with_input(
            BenchmarkId::new("rolling_hash", token_count),
            &tokens,
            |b, tokens| {
                b.iter(|| duplicate::rolling_hash(black_box(tokens), 50))
            }
        );
    }

    group.finish();
}

/// Benchmark full duplicate detection with varying file counts.
fn bench_find_duplicates(c: &mut Criterion) {
    let mut group = c.benchmark_group("find_duplicates");
    group.sample_size(10); // Reduce sample size for slower benchmarks

    for file_count in [10, 50, 100, 500].iter() {
        let files = generate_test_files(*file_count, 100);
        let total_bytes: usize = files.iter().map(|(_, s)| s.len()).sum();

        group.throughput(Throughput::Bytes(total_bytes as u64));
        group.bench_with_input(
            BenchmarkId::new("files", file_count),
            &files,
            |b, files| {
                b.iter(|| {
                    duplicate::find_duplicates(
                        black_box(files.clone()),
                        50,  // min_tokens
                        5,   // min_lines
                        0.0, // min_similarity
                    )
                })
            }
        );
    }

    group.finish();
}

/// Benchmark duplicate detection with varying min_tokens thresholds.
fn bench_min_tokens_threshold(c: &mut Criterion) {
    let mut group = c.benchmark_group("min_tokens_threshold");
    group.sample_size(10);

    let files = generate_test_files(100, 100);

    for min_tokens in [20, 50, 100, 200].iter() {
        group.bench_with_input(
            BenchmarkId::new("threshold", min_tokens),
            &(*min_tokens, &files),
            |b, (threshold, files)| {
                b.iter(|| {
                    duplicate::find_duplicates(
                        black_box((*files).clone()),
                        *threshold,
                        5,
                        0.0,
                    )
                })
            }
        );
    }

    group.finish();
}

/// Benchmark with high duplication (worst case for hash collisions).
fn bench_high_duplication(c: &mut Criterion) {
    let mut group = c.benchmark_group("high_duplication");
    group.sample_size(10);

    // Generate files with identical content (maximum duplication)
    let base_source = generate_python_source(100, 0);
    let files: Vec<(String, String)> = (0..100)
        .map(|i| (format!("file_{}.py", i), base_source.clone()))
        .collect();

    group.bench_function("100_identical_files", |b| {
        b.iter(|| {
            duplicate::find_duplicates(
                black_box(files.clone()),
                50,
                5,
                0.0,
            )
        })
    });

    group.finish();
}

criterion_group!(
    benches,
    bench_tokenization,
    bench_rolling_hash,
    bench_find_duplicates,
    bench_min_tokens_threshold,
    bench_high_duplication,
);

criterion_main!(benches);
