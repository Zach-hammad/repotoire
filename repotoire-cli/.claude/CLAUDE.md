# Repotoire Codebase

You are fixing issues in the repotoire-cli codebase, a Rust-based code analysis tool.

## Architecture
- `src/` - Main Rust source code
- `src/detectors/` - Code smell and security detectors
- `src/parsers/` - Tree-sitter based language parsers
- `src/graph/` - Knowledge graph construction
- `src/cli/` - CLI commands and TUI

## Fixing Code Issues

When fixing a reported issue:

1. **Read the file first** - Understand the context before making changes
2. **Make minimal changes** - Fix only what's needed
3. **Follow existing patterns** - Match the code style in the file
4. **Verify it compiles** - Run `cargo check` after editing

## Common Patterns

### Rust Patterns
- Use `?` for error propagation
- Prefer `if let` over `match` for single variants
- Use iterators over explicit loops where idiomatic

### Detector Patterns
- All detectors implement `Detector` trait
- Use `DetectorContext` for graph access
- Return `Vec<Finding>` from `detect()`

## After Fixing

1. Run `cargo check` to verify compilation
2. Run `cargo test` if touching logic
3. Commit with message format: `fix: <description>`
4. Push to feature branch
5. Create PR with finding details

## Don't Touch
- `tests/fixtures/` - These are intentionally bad code for testing
- `Cargo.lock` - Unless updating dependencies
