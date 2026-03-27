//! AI-specific detectors — AI-generated code patterns, boilerplate, churn.

pub mod ai_boilerplate;
mod ai_churn;
mod ai_complexity_spike;
mod ai_duplicate_block;
mod ai_missing_tests;
mod ai_naming_pattern;

pub use ai_boilerplate::{AIBoilerplateDetector, BoilerplatePattern};
pub use ai_churn::AIChurnDetector;
pub use ai_complexity_spike::AIComplexitySpikeDetector;
pub use ai_duplicate_block::AIDuplicateBlockDetector;
pub use ai_missing_tests::AIMissingTestsDetector;
pub use ai_naming_pattern::AINamingPatternDetector;
