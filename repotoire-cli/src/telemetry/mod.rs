pub mod benchmarks;
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
    let state = TelemetryState::load()?;
    if state.is_enabled() {
        Ok(Telemetry::Active(state))
    } else {
        Ok(Telemetry::Disabled)
    }
}
