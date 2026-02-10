# Contributing to repotoire-fast

Thank you for your interest in contributing to the Rust extension for Repotoire!
This guide will help you get started.

## Getting Started

### Prerequisites

1. **Rust toolchain** (1.70+):
   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   ```

2. **Python 3.10+** with development headers

3. **maturin** for building:
   ```bash
   pip install maturin
   ```

### Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/your-org/repotoire.git
   cd repotoire/repotoire-fast
   ```

2. Build in development mode:
   ```bash
   maturin develop
   ```

3. Run tests:
   ```bash
   cargo test
   ```

## Development Workflow

### 1. Create a Branch

```bash
git checkout -b feature/your-feature-name
```

### 2. Make Changes

- Write code following the style guidelines
- Add tests for new functionality
- Update documentation

### 3. Test Your Changes

```bash
# Rust tests
cargo test

# Rust linting
cargo clippy

# Format check
cargo fmt --check

# Python integration tests
pytest tests/integration/test_rust_*.py -v
```

### 4. Run Benchmarks

If your change affects performance:

```bash
cargo bench
```

### 5. Submit Pull Request

1. Push your branch
2. Create a PR with a clear description
3. Wait for CI to pass
4. Address review feedback

## Code Guidelines

### Rust Code Style

- Follow [Rust API Guidelines](https://rust-lang.github.io/api-guidelines/)
- Use `rustfmt` for formatting
- Address all `clippy` warnings
- Document all public items

### Example Function

```rust
/// Calculate the complexity score for a code block.
///
/// Uses McCabe cyclomatic complexity algorithm.
///
/// # Arguments
/// * `source` - Python source code to analyze
///
/// # Returns
/// Complexity score, or `None` if parsing fails
///
/// # Example
/// ```
/// let score = calculate_complexity("def foo(): pass");
/// assert_eq!(score, Some(1));
/// ```
pub fn calculate_complexity(source: &str) -> Option<u32> {
    // Implementation
}
```

### Error Handling

Use custom error types with `thiserror`:

```rust
use thiserror::Error;

#[derive(Debug, Error)]
pub enum MyError {
    #[error("Invalid input: {0}")]
    InvalidInput(String),
}
```

### Tests

Write comprehensive tests:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_input() {
        assert_eq!(my_function(""), None);
    }

    #[test]
    fn test_valid_input() {
        assert_eq!(my_function("data"), Some(result));
    }

    #[test]
    fn test_edge_cases() {
        // Test boundary conditions
    }
}
```

## Architecture

See [docs/RUST_ARCHITECTURE.md](docs/RUST_ARCHITECTURE.md) for:
- Module structure
- FFI boundary design
- Parallelism patterns
- Performance guidelines

## Adding New Features

### New Function Checklist

- [ ] Implement in appropriate Rust module
- [ ] Add unit tests
- [ ] Add PyO3 wrapper in `lib.rs`
- [ ] Register in `#[pymodule]`
- [ ] Add Python fallback
- [ ] Add Python tests
- [ ] Add benchmarks (if performance-critical)
- [ ] Update documentation

### New Module Checklist

- [ ] Create `src/mymodule.rs`
- [ ] Add `mod mymodule;` to `lib.rs`
- [ ] Export with `pub mod` if needed for tests
- [ ] Add module-level documentation
- [ ] Add to architecture docs

## Performance Contributions

### Optimization Guidelines

1. **Measure first**: Use benchmarks to identify bottlenecks
2. **Profile**: Use `cargo flamegraph` for CPU profiling
3. **Test impact**: Compare before/after with benchmarks
4. **Document**: Explain optimizations in comments

### Benchmark Requirements

New performance-critical code should include benchmarks:

```rust
// benches/my_bench.rs
use criterion::{criterion_group, criterion_main, Criterion};

fn bench_my_function(c: &mut Criterion) {
    c.bench_function("my_function", |b| {
        b.iter(|| my_function(data))
    });
}

criterion_group!(benches, bench_my_function);
criterion_main!(benches);
```

## Questions?

- Open an issue for bugs or feature requests
- Check existing documentation
- Ask in the PR if unsure about approach

## Code of Conduct

Please be respectful and constructive in all interactions.
