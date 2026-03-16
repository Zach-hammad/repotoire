//! Pure function stages for the analysis pipeline.
//!
//! Each stage: `fn(Input) -> Result<Output>`.
//! No engine state, no I/O side effects, independently testable.

pub mod calibrate;
pub mod collect;
pub mod detect;
pub mod git_enrich;
pub mod graph;
pub mod parse;
pub mod postprocess;
pub mod score;
