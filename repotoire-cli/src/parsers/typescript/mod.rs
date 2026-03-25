//! TypeScript/JavaScript parser using tree-sitter
//!
//! Extracts functions, classes, interfaces, imports, and call relationships from TypeScript/JavaScript source code.

mod extractors;
mod parser;

// Re-export the public API so external callers are unaffected
#[allow(unused_imports)]
pub use parser::{parse, parse_source, parse_source_with_tree};

#[cfg(test)]
mod tests;
