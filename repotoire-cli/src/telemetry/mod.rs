pub mod benchmarks;
pub mod display;
pub mod cache;
pub mod config;
pub mod events;
pub mod posthog;
pub mod repo_shape;

use anyhow::Result;
use config::TelemetryState;

/// Telemetry handle — either active or a no-op stub
pub enum Telemetry {
    Active(TelemetryState),
    Disabled,
}

impl Telemetry {
    pub fn is_enabled(&self) -> bool {
        matches!(self, Telemetry::Active(_))
    }
}

/// Initialize telemetry. Returns Active handle if enabled, Disabled otherwise.
pub fn init() -> Result<Telemetry> {
    let user_config = crate::config::UserConfig::load()?;
    let file_enabled = user_config.telemetry.enabled;

    // Check for env var overrides
    let has_env_override = std::env::var("DO_NOT_TRACK").is_ok()
        || std::env::var("REPOTOIRE_TELEMETRY").is_ok();

    // First-run prompt if undecided
    let effective_enabled = if config::should_prompt(file_enabled, has_env_override) {
        match config::show_opt_in_prompt() {
            Some(choice) => {
                let _ = config::save_telemetry_choice(choice);
                Some(choice)
            }
            None => Some(false), // Non-interactive: default off
        }
    } else {
        file_enabled
    };

    let state = config::TelemetryState::resolve_with_env(
        effective_enabled,
        std::env::var("DO_NOT_TRACK").ok().as_deref(),
        std::env::var("REPOTOIRE_TELEMETRY").ok().as_deref(),
    );

    if state.is_enabled() {
        // Ensure distinct_id exists
        let state = config::TelemetryState::load()?;
        Ok(Telemetry::Active(state))
    } else {
        Ok(Telemetry::Disabled)
    }
}
