//! Graph-based constant propagation — static value oracle for detectors.
//!
//! Extracts symbolic values during parsing, propagates across function boundaries
//! via the call graph, and provides O(1) value queries to detectors.

pub mod types;
