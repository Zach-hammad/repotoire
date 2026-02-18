//! Adaptive threshold calibration
//!
//! Scans a codebase to build a style profile — statistical distributions
//! of code metrics (complexity, nesting, function length, etc.).
//! Detectors use the profile to set thresholds based on YOUR patterns,
//! not arbitrary defaults.

mod profile;
mod collector;
mod resolver;

pub use profile::{StyleProfile, MetricDistribution, MetricKind};
pub use collector::collect_metrics;
pub use resolver::ThresholdResolver;
