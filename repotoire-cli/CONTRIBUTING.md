# Contributing to Repotoire CLI

Thanks for your interest in contributing to Repotoire! We welcome contributions from the Rust community.

## Prerequisites

- **Rust 1.70+** (we use recent language features)
- **Git** for version control
- A code editor with rust-analyzer support (recommended)

```bash
# Check your Rust version
rustc --version

# Update if needed
rustup update stable
```

## Getting Started

### Clone and Build

```bash
git clone https://github.com/your-org/repotoire.git
cd repotoire/repotoire-cli

# Development build (faster compilation)
cargo build

# Release build (optimized)
cargo build --release
```

### Run Tests

```bash
# Run all tests
cargo test

# Run tests with output
cargo test -- --nocapture

# Run a specific test
cargo test test_circular_dependency
```

### Code Formatting and Linting

```bash
# Format code
cargo fmt

# Run clippy
cargo clippy -- -D warnings

# Both (do this before committing!)
cargo fmt && cargo clippy -- -D warnings
```

## Project Structure

```
src/
├── main.rs           # Entry point
├── models.rs         # Core data structures
├── cli/              # Command-line interface (clap)
├── parsers/          # Tree-sitter language parsers
├── graph/            # Code graph using petgraph
├── cache/            # Incremental analysis with sled
├── detectors/        # 81 code smell detectors ← most contributions go here
│   ├── mod.rs        # Detector trait & engine
│   ├── base.rs       # Base detector utilities
│   ├── engine.rs     # Parallel execution engine
│   └── *.rs          # Individual detectors
├── reporters/        # Output formatters (JSON, SARIF, etc.)
├── ai/               # AI-powered analysis
├── git/              # Git integration
├── mcp/              # MCP protocol support
└── pipeline/         # Analysis pipeline
```

## Adding a New Detector

This is the most common contribution. Here's how:

### 1. Create the Detector File

Create `src/detectors/my_detector.rs`:

```rust
use crate::detectors::base::{Detector, Finding, Severity};
use crate::graph::CodeGraph;

pub struct MyDetector;

impl Detector for MyDetector {
    fn name(&self) -> &'static str {
        "my-detector"
    }

    fn description(&self) -> &'static str {
        "Detects [what it detects]"
    }

    fn detect(&self, graph: &CodeGraph) -> Vec<Finding> {
        let mut findings = Vec::new();

        // Your detection logic here
        // Query the graph, analyze nodes, find patterns

        for node in graph.nodes() {
            if self.is_problematic(node) {
                findings.push(Finding {
                    detector: self.name().to_string(),
                    message: "Description of the issue".to_string(),
                    file: node.file_path.clone(),
                    line: node.line,
                    severity: Severity::Warning,
                    ..Default::default()
                });
            }
        }

        findings
    }

    fn is_dependent(&self) -> bool {
        false  // true if this detector needs results from others
    }
}

impl MyDetector {
    fn is_problematic(&self, node: &Node) -> bool {
        // Your logic here
        false
    }
}
```

### 2. Register the Detector

In `src/detectors/mod.rs`:

```rust
mod my_detector;
pub use my_detector::MyDetector;

// In the engine registration:
engine.register(Box::new(MyDetector));
```

### 3. Add Tests

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_my_detector_finds_issue() {
        let graph = create_test_graph_with_issue();
        let detector = MyDetector;
        let findings = detector.detect(&graph);
        
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].severity, Severity::Warning);
    }

    #[test]
    fn test_my_detector_clean_code() {
        let graph = create_clean_test_graph();
        let detector = MyDetector;
        let findings = detector.detect(&graph);
        
        assert!(findings.is_empty());
    }
}
```

### Detector Categories

Choose the right category for your detector:

| Category | Examples | Description |
|----------|----------|-------------|
| **Security** | `sql_injection`, `xss`, `hardcoded_secrets` | Vulnerabilities |
| **Architecture** | `circular_dependency`, `god_class` | Structural issues |
| **Quality** | `long_methods`, `magic_numbers` | Code smells |

## Code Style

We follow standard Rust conventions:

- **Formatting**: `cargo fmt` (rustfmt defaults)
- **Linting**: `cargo clippy` with warnings as errors
- **Documentation**: Document public APIs with `///` doc comments
- **Error handling**: Use `Result` and `?` operator, avoid `.unwrap()` in library code
- **Naming**: `snake_case` for functions/variables, `CamelCase` for types

### Commit Messages

```
feat(detectors): add prototype pollution detector

- Detects __proto__ and constructor.prototype access
- Includes tests for common patterns
- Severity: High (security)
```

Prefixes: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

## Pull Request Process

1. **Fork** the repository
2. **Create a branch**: `git checkout -b feat/my-detector`
3. **Make changes** with tests
4. **Ensure CI passes**:
   ```bash
   cargo fmt --check
   cargo clippy -- -D warnings
   cargo test
   ```
5. **Open a PR** with a clear description
6. **Address feedback** from reviewers

### PR Checklist

- [ ] Code compiles without warnings (`cargo build`)
- [ ] All tests pass (`cargo test`)
- [ ] Code is formatted (`cargo fmt`)
- [ ] Clippy is happy (`cargo clippy -- -D warnings`)
- [ ] New code has tests
- [ ] Documentation updated if needed

## Architecture Notes

### Key Dependencies

- **tree-sitter**: Fast incremental parsing for multiple languages
- **petgraph**: Graph data structures for code relationships
- **sled**: Embedded database for incremental caching
- **rayon**: Parallel detector execution
- **clap**: CLI argument parsing

### Performance Considerations

- Detectors run in parallel via rayon (unless `is_dependent()` returns true)
- Use the graph's indices rather than cloning data
- Leverage sled caching for expensive computations
- Prefer iterators over collecting into Vecs

## Getting Help

- **Questions?** Open a GitHub Discussion
- **Bug?** Open an Issue with reproduction steps
- **Feature idea?** Open an Issue to discuss first

## License

By contributing, you agree that your contributions will be licensed under the same license as the project.

---

Happy hacking! 🦀
