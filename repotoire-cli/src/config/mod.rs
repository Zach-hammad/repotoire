//! Configuration module for Repotoire
//!
//! This module handles:
//! - Project-level configuration (repotoire.toml)
//! - User-level configuration (~/.config/repotoire/config.toml)
//! - Detector threshold overrides
//! - Scoring customization
//! - CLI defaults

mod project_config;
mod project_type_scoring;
mod user_config;

pub use project_config::{
    load_project_config, normalize_detector_name, CliDefaults, DetectorConfigOverride,
    ExcludeConfig, PillarWeights, ProjectConfig, ProjectType, ScoringConfig, ThresholdValue,
};

pub use user_config::UserConfig;
