# Rust Development Guide

This guide covers development setup, building, testing, and contribution
guidelines for the `repotoire-fast` Rust extension.

## Prerequisites

### Required Tools

- **Rust 1.70+**: Install via [rustup](https://rustup.rs/)
  ```bash
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
  ```

- **Python 3.10+**: Required for PyO3 bindings

- **maturin**: Python package for building Rust extensions
  ```bash
  pip install maturin
  # Or with uv:
  uv pip install maturin
  ```

### Optional Tools

- **cargo-watch**: Auto-rebuild on file changes
  ```bash
  cargo install cargo-watch
  ```

- **cargo-flamegraph**: CPU profiling
  ```bash
  cargo install flamegraph
  ```

## Building

### Development Build

Fast compile, unoptimized (for development):

```bash
cd repotoire-fast
maturin develop
```

### Release Build

Slower compile, optimized (for production/benchmarks):

```bash
maturin develop --release
```

### Build Wheel

Create distributable wheel:

```bash
maturin build --release
```

Output is in `target/wheels/`.

### Using uv

With uv, the build is integrated:

```bash
# From repo root
uv run maturin develop --release -m repotoire-fast/Cargo.toml
```

## Testing

### Rust Unit Tests

Run all Rust tests:

```bash
cargo test
```

Run tests for specific module:

```bash
cargo test duplicate      # duplicate.rs tests
cargo test graph_algo     # graph_algo.rs tests
```

Run with verbose output:

```bash
cargo test -- --nocapture
```

### Rust Benchmarks

Run Criterion benchmarks:

```bash
cargo bench
```

Run specific benchmark:

```bash
cargo bench --bench duplicate_bench
cargo bench --bench graph_bench
```

### Python Integration Tests

```bash
# From repo root
pytest tests/integration/test_rust_*.py -v
```

### Full Benchmark Comparison

```bash
pytest tests/benchmarks/test_rust_vs_python.py --benchmark-only -v
```

## Adding a New Function

### Step 1: Implement in Rust

Create or edit the appropriate module in `src/`:

```rust
// src/mymodule.rs

/// Calculate something useful.
///
/// # Arguments
/// * `input` - Input data
///
/// # Returns
/// Processed result
pub fn my_function(input: Vec<i64>) -> Vec<i64> {
    input.into_iter().map(|x| x * 2).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_my_function() {
        assert_eq!(my_function(vec![1, 2, 3]), vec![2, 4, 6]);
    }
}
```

### Step 2: Add PyO3 Wrapper

In `src/lib.rs`:

```rust
// Import the module
mod mymodule;

/// Python wrapper for my_function.
///
/// Args:
///     input: List of integers to process
///
/// Returns:
///     List of processed integers
#[pyfunction]
fn my_function_py(input: Vec<i64>) -> PyResult<Vec<i64>> {
    Ok(mymodule::my_function(input))
}
```

### Step 3: Register in Module

In `src/lib.rs`, add to the `#[pymodule]`:

```rust
#[pymodule]
fn repotoire_fast(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // ... existing functions ...
    m.add_function(wrap_pyfunction!(my_function_py, m)?)?;
    Ok(())
}
```

### Step 4: Add Python Fallback

In `repotoire/utils/` or appropriate location:

```python
# repotoire/utils/rust_fallback.py

try:
    from repotoire_fast import my_function_py as my_function
except ImportError:
    def my_function(input):
        """Python fallback for my_function."""
        return [x * 2 for x in input]
```

### Step 5: Write Tests

Add tests for both Rust and fallback paths:

```python
# tests/unit/test_my_function.py

import pytest

def test_my_function_rust():
    try:
        from repotoire_fast import my_function_py
        result = my_function_py([1, 2, 3])
        assert result == [2, 4, 6]
    except ImportError:
        pytest.skip("Rust extension not available")

def test_my_function_fallback():
    from repotoire.utils.rust_fallback import my_function
    result = my_function([1, 2, 3])
    assert result == [2, 4, 6]
```

## Debugging

### Enable Backtraces

```bash
RUST_BACKTRACE=1 python -c "import repotoire_fast"
```

### Verbose Logging

```bash
RUST_LOG=debug cargo test
```

### Debug Build with Symbols

```bash
maturin develop  # Debug mode by default
```

### Profile with Flamegraph

```bash
cargo flamegraph --bench duplicate_bench
```

View `flamegraph.svg` in browser.

### Print Debug Output

```rust
// Temporary debug output (remove before commit!)
eprintln!("Debug: value = {:?}", value);
```

## Code Style

### Formatting

Format with rustfmt:

```bash
cargo fmt
```

### Linting

Check with clippy:

```bash
cargo clippy
```

Fix clippy warnings:

```bash
cargo clippy --fix
```

### Documentation

Document all public items:

```rust
/// Calculate the sum of numbers.
///
/// # Arguments
/// * `numbers` - Slice of integers to sum
///
/// # Returns
/// The sum of all numbers
///
/// # Example
/// ```
/// let sum = calculate_sum(&[1, 2, 3]);
/// assert_eq!(sum, 6);
/// ```
pub fn calculate_sum(numbers: &[i32]) -> i32 {
    numbers.iter().sum()
}
```

## Common Patterns

### Error Handling

Use `Result` and custom error types:

```rust
use thiserror::Error;

#[derive(Debug, Error)]
pub enum MyError {
    #[error("Invalid input: {0}")]
    InvalidInput(String),

    #[error("Processing failed: {0}")]
    ProcessingFailed(String),
}

pub fn process(input: &str) -> Result<String, MyError> {
    if input.is_empty() {
        return Err(MyError::InvalidInput("empty input".to_string()));
    }
    Ok(input.to_uppercase())
}
```

### Parallel Processing

Use Rayon for parallelism:

```rust
use rayon::prelude::*;

pub fn process_parallel(items: Vec<String>) -> Vec<String> {
    items.par_iter()
        .map(|s| s.to_uppercase())
        .collect()
}
```

### Optional Features

Use `cfg` for optional code:

```rust
#[cfg(feature = "simd")]
fn fast_sum(data: &[f32]) -> f32 {
    // SIMD implementation
}

#[cfg(not(feature = "simd"))]
fn fast_sum(data: &[f32]) -> f32 {
    // Fallback implementation
    data.iter().sum()
}
```

## Performance Tips

1. **Pre-allocate**: Use `Vec::with_capacity()` when size is known
2. **Avoid cloning**: Use references where possible
3. **Batch operations**: Process items in batches, not one-by-one
4. **Use iterators**: Prefer `iter()` over manual loops
5. **Parallelize carefully**: Overhead matters for small data

## Troubleshooting

### Build Errors

**"can't find crate"**
```bash
# Clean and rebuild
cargo clean
maturin develop
```

**"incompatible Python version"**
```bash
# Ensure correct Python is active
which python3
maturin develop -i python3.12
```

### Runtime Errors

**"undefined symbol"**
```bash
# Rebuild in release mode
maturin develop --release
```

**"module not found"**
```bash
# Install in development mode
pip install -e ".[dev]"
```

### Performance Issues

1. Ensure release mode: `maturin develop --release`
2. Check parallelism: `RAYON_NUM_THREADS=4`
3. Profile with flamegraph
4. Review algorithm complexity

## CI/CD Integration

### GitHub Actions

The project includes benchmark CI in `.github/workflows/benchmark.yml`:

- Runs Criterion benchmarks on every PR
- Compares against main branch baseline
- Alerts on 50%+ regression

### Local Pre-commit

```bash
# Run before committing
cargo fmt --check
cargo clippy
cargo test
```
