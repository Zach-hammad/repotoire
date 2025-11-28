# Repotoire-Fast Roadmap

## Overview

`repotoire-fast` is a Rust extension providing high-performance implementations
of computationally expensive operations for the Repotoire code analysis platform.
By implementing critical algorithms in Rust with PyO3 bindings, we achieve
10-100x speedup over pure Python implementations while maintaining full
compatibility with the existing Python codebase.

## Phase 1: Graph Algorithms (Completed)

Phase 1 replaced Neo4j GDS dependency with pure Rust implementations,
enabling database-agnostic analysis and eliminating network round-trip overhead.

### Completed Optimizations

| Algorithm | Python/GDS Baseline | Rust Implementation | Speedup | Status |
|-----------|--------------------|--------------------|---------|--------|
| Strongly Connected Components | 500ms (Neo4j GDS) | 5ms | 100x | Done |
| PageRank | 200ms (NetworkX) | 15ms | 13x | Done |
| Betweenness Centrality | 2s (NetworkX) | 100ms | 20x | Done |
| Leiden Community Detection | 1s (Neo4j GDS) | 50ms | 20x | Done |
| Harmonic Centrality | 500ms (NetworkX) | 25ms | 20x | Done |
| Cyclomatic Complexity | 100ms (radon) | 5ms | 20x | Done |
| LCOM Calculation | 50ms (custom) | 2ms | 25x | Done |
| File Hashing (MD5) | 200ms (hashlib) | 20ms | 10x | Done |
| Cosine Similarity (batch) | 500ms (numpy) | 50ms | 10x | Done |

### Key Features

- **Parallel execution**: All algorithms use Rayon for multi-core parallelism
- **Error handling**: Proper validation with descriptive error messages (REPO-227)
- **Zero-copy where possible**: Efficient data transfer across FFI boundary
- **Comprehensive tests**: Edge cases, known topologies, numerical precision

### Files

```
repotoire-fast/src/
├── graph_algo.rs     # SCC, PageRank, Betweenness, Leiden, Harmonic
├── complexity.rs     # Cyclomatic complexity calculation
├── lcom.rs           # Lack of Cohesion of Methods
├── hashing.rs        # File hashing (MD5)
├── similarity.rs     # Cosine similarity, top-k search
├── errors.rs         # Error types with thiserror
└── lib.rs            # PyO3 module definition
```

---

## Phase 2: Advanced Detectors (In Progress)

Phase 2 reimplements computationally expensive detectors in Rust for additional
performance gains and reduced external tool dependencies.

### Scope

| Component | Description | Expected Speedup | Priority |
|-----------|-------------|------------------|----------|
| Duplicate Detection | Rabin-Karp rolling hash | 5-10x vs jscpd | P0 |
| Pylint Rules | 11 rules not covered by Ruff | 10-50x vs pylint | P1 |
| AST-based Detectors | Complex pattern matching | 5-20x | P2 |

### REPO-166: Duplicate Code Detection

**Problem**: Current `jscpd_detector.py` takes ~10 seconds with:
- Node.js subprocess overhead (~100ms startup)
- JSON serialization/deserialization overhead
- Single-threaded execution

**Solution**: Rabin-Karp rolling hash algorithm in Rust:
- O(n) time complexity for tokenization + hashing
- Parallel file processing with Rayon
- Direct Python integration (no subprocess)
- Configurable thresholds (min_tokens, min_lines, similarity)

**Algorithm**:
```
1. Tokenize all files in parallel
2. Compute rolling hashes for each token window
3. Build hash index across all files
4. Find duplicates via hash collisions
5. Verify matches and merge overlapping blocks
```

**Target Performance**:
| Metric | jscpd | Rust | Improvement |
|--------|-------|------|-------------|
| 100 files | 2s | 200ms | 10x |
| 1000 files | 10s | 1s | 10x |
| 10000 files | 60s | 5s | 12x |

### REPO-167: Benchmark Suite

**Components**:
1. **Criterion benchmarks** (`benches/`): Micro-benchmarks for Rust functions
2. **pytest-benchmark tests**: Rust vs Python comparison
3. **CI workflow**: Automated regression detection
4. **Performance dashboard**: Track improvements over time

**Metrics tracked**:
- Throughput (operations/second)
- Latency (p50, p95, p99)
- Memory usage
- Scaling characteristics

### REPO-168: Documentation

**Deliverables**:
1. `docs/RUST_ARCHITECTURE.md`: Module structure, FFI design, parallelism
2. `docs/RUST_DEVELOPMENT.md`: Build, test, debug, add new functions
3. `docs/RUST_API.md`: Auto-generated from docstrings
4. `CONTRIBUTING.md`: Contribution guidelines

---

## Phase 3: Future Work (Planned)

### Multi-language Parsing

Extend Rust-based parsing to additional languages using tree-sitter:

| Language | Parser | Status |
|----------|--------|--------|
| Python | rustpython-parser | Done |
| TypeScript | tree-sitter-typescript | Planned |
| JavaScript | tree-sitter-javascript | Planned |
| Java | tree-sitter-java | Planned |
| Go | tree-sitter-go | Planned |
| Rust | tree-sitter-rust | Planned |

### Advanced Analysis

- **Call graph construction**: Static analysis of function calls
- **Data flow analysis**: Track variable assignments and usage
- **Type inference**: Infer types for dynamically-typed code
- **Dead code detection**: Find unreachable code paths

### Performance Optimizations

- **SIMD acceleration**: Use SIMD for string/vector operations
- **Memory-mapped files**: Reduce I/O overhead for large files
- **Incremental hashing**: Skip unchanged code regions
- **GPU acceleration**: CUDA/Metal for embedding similarity

---

## Performance Targets

### Throughput Goals

| Operation | Current | Phase 2 Target | Phase 3 Target |
|-----------|---------|----------------|----------------|
| Files/second (ingest) | 100 | 500 | 1000 |
| Duplicates/second | 10 | 100 | 500 |
| Graph ops/second | 1000 | 5000 | 10000 |

### Latency Goals (p95)

| Operation | Current | Phase 2 Target |
|-----------|---------|----------------|
| Single file analysis | 50ms | 10ms |
| Duplicate detection (100 files) | 2s | 200ms |
| Full codebase analysis (1000 files) | 5min | 30s |

### Quality Goals

- False positive rate: <10% vs original tools
- False negative rate: <5% vs original tools
- Memory usage: <2x baseline
- CPU utilization: >80% on available cores

---

## Migration Guide

### Python Fallbacks

All Rust functions have Python fallbacks that activate automatically when
the Rust extension is unavailable:

```python
# repotoire/utils/rust_fallback.py

try:
    from repotoire_fast import find_duplicates
except ImportError:
    def find_duplicates(files, min_tokens=50, min_lines=5, similarity=0.0):
        """Python fallback for duplicate detection."""
        # Uses jscpd subprocess as fallback
        from repotoire.detectors.jscpd_detector import JscpdDetector
        detector = JscpdDetector()
        return detector.detect_duplicates(files, min_tokens, min_lines)
```

### Feature Flags

Enable/disable Rust acceleration via environment variables:

```bash
# Disable Rust acceleration (use Python fallbacks)
export REPOTOIRE_DISABLE_RUST=1

# Enable verbose Rust logging
export RUST_LOG=debug

# Limit Rust parallelism
export RAYON_NUM_THREADS=4
```

### Gradual Rollout

1. **Alpha**: Rust functions available but not default
2. **Beta**: Rust functions default with Python fallback
3. **GA**: Rust functions required (Python fallback deprecated)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Development setup
- Code style guidelines
- Testing requirements
- PR process

---

## Changelog

### v0.2.0 (Phase 2) - In Progress
- [ ] Duplicate code detection (REPO-166)
- [ ] Benchmark suite (REPO-167)
- [ ] Documentation (REPO-168)

### v0.1.0 (Phase 1) - Completed
- [x] Graph algorithms (SCC, PageRank, Betweenness, Leiden, Harmonic)
- [x] Complexity calculation
- [x] LCOM calculation
- [x] File hashing
- [x] Cosine similarity
- [x] Pylint rules (11 rules)
- [x] Error handling (REPO-227)
- [x] Parallel Leiden (REPO-215)
- [x] Comprehensive tests (REPO-218)
