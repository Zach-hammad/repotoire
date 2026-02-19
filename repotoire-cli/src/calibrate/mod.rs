//! Adaptive threshold calibration
//!
//! Scans a codebase to build a style profile — statistical distributions
//! of code metrics (complexity, nesting, function length, etc.).
//! Detectors use the profile to set thresholds based on YOUR patterns,
//! not arbitrary defaults.

mod collector;
mod profile;
mod resolver;

pub use collector::collect_metrics;
pub use profile::{MetricDistribution, MetricKind, StyleProfile};
pub use resolver::ThresholdResolver;
