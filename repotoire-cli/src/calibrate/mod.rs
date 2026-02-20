//! Adaptive threshold calibration + predictive coding
//!
//! Scans a codebase to build a style profile — statistical distributions
//! of code metrics (complexity, nesting, function length, etc.).
//! Also builds an n-gram language model to detect "surprising" code.
//!
//! Detectors use the profile to set thresholds based on YOUR patterns,
//! not arbitrary defaults. The n-gram model flags code that looks
//! unlike anything else in the project.

mod collector;
pub mod ngram;
mod profile;
mod resolver;

pub use collector::collect_metrics;
pub use ngram::NgramModel;
pub use profile::{MetricDistribution, MetricKind, StyleProfile};
pub use resolver::{ThresholdExplanation, ThresholdResolver};
