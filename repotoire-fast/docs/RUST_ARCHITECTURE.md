# Rust Architecture

This document describes the architecture of the `repotoire-fast` Rust extension.

## Overview

`repotoire-fast` is a PyO3-based Python extension that provides high-performance
implementations of computationally expensive operations. It achieves 10-100x
speedup over pure Python implementations while maintaining full API compatibility.

## Module Structure

```
repotoire-fast/
├── Cargo.toml              # Dependencies and build configuration
├── src/
│   ├── lib.rs              # PyO3 module definition and bindings
│   ├── graph_algo.rs       # Graph algorithms (PageRank, Leiden, SCC, etc.)
│   ├── duplicate.rs        # Duplicate code detection (Rabin-Karp)
│   ├── complexity.rs       # Cyclomatic complexity calculation
│   ├── lcom.rs             # Lack of Cohesion of Methods
│   ├── similarity.rs       # Cosine similarity and vector ops
│   ├── hashing.rs          # File hashing (MD5)
│   ├── pylint_rules.rs     # Pylint rule implementations
│   └── errors.rs           # Error types and handling
├── benches/
│   ├── duplicate_bench.rs  # Duplicate detection benchmarks
│   └── graph_bench.rs      # Graph algorithm benchmarks
└── docs/
    ├── RUST_ARCHITECTURE.md   # This file
    └── RUST_DEVELOPMENT.md    # Development guide
```

## FFI Boundary (PyO3)

### Function Annotations

All public functions use PyO3 annotations for Python interop:

```rust
#[pyfunction]
#[pyo3(signature = (num_nodes, edges, damping=0.85, max_iterations=100))]
fn pagerank(
    num_nodes: usize,
    edges: Vec<(usize, usize)>,
    damping: f64,
    max_iterations: usize,
) -> PyResult<Vec<f64>> {
    // Implementation
}
```

Key concepts:
- `#[pyfunction]` - Exposes function to Python
- `#[pyo3(signature = ...)]` - Defines Python signature with defaults
- `PyResult<T>` - Error handling across FFI boundary
- GIL is automatically managed by PyO3

### Python Classes

For complex return types, we define Python classes:

```rust
#[pyclass]
#[derive(Clone)]
pub struct PyDuplicateBlock {
    #[pyo3(get)]
    pub file1: String,
    #[pyo3(get)]
    pub start1: usize,
    // ...
}

#[pymethods]
impl PyDuplicateBlock {
    fn __repr__(&self) -> String {
        format!("DuplicateBlock(...)")
    }
}
```

### Type Conversions

| Python Type | Rust Type |
|-------------|-----------|
| `int` | `i32`, `i64`, `usize` |
| `float` | `f32`, `f64` |
| `str` | `String`, `&str` |
| `list[int]` | `Vec<i32>` |
| `list[tuple[int, int]]` | `Vec<(i32, i32)>` |
| `dict[str, Any]` | `HashMap<String, PyObject>` |
| `numpy.ndarray` | `PyReadonlyArray` |

## Parallelism with Rayon

All parallelizable operations use Rayon for multi-core execution:

```rust
use rayon::prelude::*;

// Parallel iteration
let results: Vec<_> = data.par_iter()
    .map(|item| process(item))
    .collect();

// Parallel reduction
let sum: f64 = values.par_iter().sum();
```

### GIL Release

GIL is released during parallel operations via `py.allow_threads()`:

```rust
#[pyfunction]
fn heavy_computation(py: Python<'_>, data: Vec<i32>) -> PyResult<Vec<i32>> {
    py.allow_threads(|| {
        // GIL released here - safe for parallel Rust code
        data.par_iter().map(|x| x * 2).collect()
    })
}
```

## Algorithm Implementations

### Graph Algorithms

| Algorithm | Complexity | Parallelized | Notes |
|-----------|------------|--------------|-------|
| SCC (Tarjan) | O(V + E) | No | Sequential DFS required |
| PageRank | O(iterations * E) | Yes | Per-node updates parallel |
| Betweenness | O(V * E) | Yes | BFS from each source |
| Harmonic | O(V * (V + E)) | Yes | BFS from each source |
| Leiden | O(E) per iteration | Yes (REPO-215) | Candidate moves parallel |

### Duplicate Detection

Uses Rabin-Karp rolling hash algorithm:

1. **Tokenization**: O(n) - single pass through source
2. **Rolling Hash**: O(n) - sliding window with constant-time updates
3. **Hash Index**: O(n) - build hash-to-location map
4. **Match Verification**: O(k * m) - verify k collisions of length m
5. **Merge Overlapping**: O(d log d) - sort and merge d duplicates

Total: O(n) average case, O(n * k) worst case with many collisions.

## Error Handling

All algorithms return `Result<T, GraphError>` for proper error handling:

```rust
#[derive(Debug, thiserror::Error)]
pub enum GraphError {
    #[error("Node {0} out of bounds (max: {1})")]
    NodeOutOfBounds(u32, u32),

    #[error("Invalid parameter: {0}")]
    InvalidParameter(String),
}
```

Errors are converted to Python `ValueError` via PyO3:

```rust
impl From<GraphError> for PyErr {
    fn from(err: GraphError) -> PyErr {
        PyValueError::new_err(err.to_string())
    }
}
```

## Performance Guidelines

### Avoid Allocations in Hot Loops

Pre-allocate vectors when size is known:

```rust
// Good: pre-allocate
let mut results = Vec::with_capacity(n);
for item in items {
    results.push(process(item));
}

// Bad: grow dynamically
let mut results = Vec::new();
for item in items {
    results.push(process(item));
}
```

### Use `par_iter()` for Large Collections

Parallelism overhead isn't worth it for small collections:

```rust
// Use parallel for >1000 items
if items.len() > 1000 {
    items.par_iter().map(|x| process(x)).collect()
} else {
    items.iter().map(|x| process(x)).collect()
}
```

### Batch Python Calls

Minimize FFI boundary crossings:

```rust
// Good: single call with batch data
fn process_batch(items: Vec<String>) -> Vec<Result>;

// Bad: many calls
fn process_single(item: String) -> Result;
```

### Use `black_box()` in Benchmarks

Prevent dead code elimination:

```rust
use criterion::black_box;

b.iter(|| {
    let result = heavy_computation(black_box(input.clone()));
    black_box(result)
})
```

## Memory Management

### Ownership Model

Rust's ownership model ensures memory safety:
- Data passed to Python is cloned or wrapped in `Py<T>`
- Python-owned data is accessed via references

### Large Data Handling

For large datasets, use streaming or chunked processing:

```rust
fn process_large_file(path: &str) -> PyResult<Vec<Finding>> {
    let file = BufReader::new(File::open(path)?);

    // Process line by line, not all at once
    let findings: Vec<Finding> = file
        .lines()
        .par_bridge()  // Parallel processing of stream
        .filter_map(|line| process_line(line.ok()?))
        .collect();

    Ok(findings)
}
```

## Testing

### Unit Tests

Each module has unit tests in a `tests` submodule:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_functionality() {
        let result = my_function(input);
        assert_eq!(result, expected);
    }
}
```

Run with: `cargo test`

### Benchmarks

Criterion benchmarks in `benches/`:

```rust
use criterion::{criterion_group, criterion_main, Criterion};

fn bench_function(c: &mut Criterion) {
    c.bench_function("my_function", |b| {
        b.iter(|| my_function(input))
    });
}

criterion_group!(benches, bench_function);
criterion_main!(benches);
```

Run with: `cargo bench`

## Integration with Python

### Module Registration

Functions are registered in `lib.rs`:

```rust
#[pymodule]
fn repotoire_fast(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(pagerank, m)?)?;
    m.add_function(wrap_pyfunction!(find_duplicates, m)?)?;
    m.add_class::<PyDuplicateBlock>()?;
    Ok(())
}
```

### Python Fallback Pattern

All Rust functions have Python fallbacks:

```python
# repotoire/detectors/duplicate_rust.py

try:
    from repotoire_fast import find_duplicates
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False

def find_duplicates_with_fallback(files, min_tokens, min_lines):
    if RUST_AVAILABLE:
        return find_duplicates(files, min_tokens, min_lines, 0.0)
    else:
        return python_fallback(files, min_tokens, min_lines)
```

## Dependencies

| Crate | Purpose |
|-------|---------|
| `pyo3` | Python bindings |
| `rayon` | Parallel iteration |
| `petgraph` | Graph data structures |
| `rustpython-parser` | Python AST parsing |
| `numpy` | NumPy array interop |
| `criterion` | Benchmarking (dev) |
| `thiserror` | Error derive macro |
