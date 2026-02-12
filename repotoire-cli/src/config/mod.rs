//! Configuration module for Repotoire
//!
//! This module handles:
//! - Project-level configuration (repotoire.toml)
//! - User-level configuration (~/.config/repotoire/config.toml)
//! - Detector threshold overrides
//! - Scoring customization
//! - CLI defaults

mod project_config;
mod user_config;

pub use project_config::{
    CliDefaults,
    DetectorConfigOverride,
    ExcludeConfig,
    PillarWeights,
    ProjectConfig,
    ScoringConfig,
    ThresholdValue,
    load_project_config,
    normalize_detector_name,
};

pub use user_config::UserConfig;
