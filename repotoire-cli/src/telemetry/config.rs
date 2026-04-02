//! Telemetry state resolution and distinct ID management

use anyhow::Result;
use std::path::{Path, PathBuf};

use crate::config::UserConfig;

/// Resolved telemetry state
pub struct TelemetryState {
    pub enabled: bool,
    pub distinct_id: Option<String>,
}

impl TelemetryState {
    /// Load telemetry state from user config and environment
    pub fn load() -> Result<Self> {
        let config = UserConfig::load()?;
        let file_enabled = config.telemetry.enabled;
        let state = Self::resolve(file_enabled);
        let distinct_id = if state.enabled {
            Some(load_or_create_distinct_id()?)
        } else {
            None
        };
        Ok(TelemetryState {
            enabled: state.enabled,
            distinct_id,
        })
    }

    pub fn is_enabled(&self) -> bool {
        self.enabled
    }

    /// Production resolver — reads real env vars and delegates to `resolve_with_env`
    fn resolve(file_enabled: Option<bool>) -> Self {
        let do_not_track = std::env::var("DO_NOT_TRACK").ok();
        let repotoire_telemetry = std::env::var("REPOTOIRE_TELEMETRY").ok();
        Self::resolve_with_env(
            file_enabled,
            do_not_track.as_deref(),
            repotoire_telemetry.as_deref(),
        )
    }

    /// Testable resolver that accepts env values as parameters.
    ///
    /// Priority:
    /// 1. `DO_NOT_TRACK=1` → always disabled
    /// 2. `REPOTOIRE_TELEMETRY` env var → parses as truthy/falsy
    /// 3. Config file `telemetry.enabled`
    /// 4. Default: false
    pub fn resolve_with_env(
        file_enabled: Option<bool>,
        do_not_track: Option<&str>,
        repotoire_telemetry: Option<&str>,
    ) -> Self {
        // DO_NOT_TRACK=1 wins unconditionally
        if do_not_track == Some("1") {
            return TelemetryState {
                enabled: false,
                distinct_id: None,
            };
        }

        // REPOTOIRE_TELEMETRY env var
        if let Some(val) = repotoire_telemetry {
            return TelemetryState {
                enabled: is_truthy(val),
                distinct_id: None,
            };
        }

        // Config file
        if let Some(enabled) = file_enabled {
            return TelemetryState {
                enabled,
                distinct_id: None,
            };
        }

        // Default: off
        TelemetryState {
            enabled: false,
            distinct_id: None,
        }
    }
}

fn is_truthy(val: &str) -> bool {
    matches!(
        val.to_lowercase().as_str(),
        "1" | "true" | "yes" | "on" | "enabled"
    )
}

/// Generate a random distinct ID (hex string, same format as UUID v4 without dashes)
pub fn generate_distinct_id() -> String {
    use rand::Rng;
    let bytes: [u8; 16] = rand::rng().random();
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}

/// Returns the path to the telemetry ID file
pub fn telemetry_id_path() -> Option<PathBuf> {
    dirs::config_dir().map(|p| p.join("repotoire").join("telemetry_id"))
}

/// Load the distinct ID from disk, creating it if it doesn't exist
pub fn load_or_create_distinct_id() -> Result<String> {
    let path = telemetry_id_path()
        .ok_or_else(|| anyhow::anyhow!("Could not determine config directory for telemetry_id"))?;

    if path.exists() {
        let id = std::fs::read_to_string(&path)?.trim().to_string();
        if !id.is_empty() {
            return Ok(id);
        }
    }

    // Create parent directory if needed
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let id = generate_distinct_id();
    std::fs::write(&path, &id)?;
    Ok(id)
}

/// Compute a stable repo ID from the root commit of the git repo at `path`.
/// Returns `None` if the path is not a git repo or has no commits.
pub fn compute_repo_id(path: &Path) -> Option<String> {
    let repo = crate::git::raw::RawRepo::discover(path).ok()?;
    let root_oid = repo.find_root_commit().ok()?;
    let hash = root_oid.to_hex();
    Some(compute_repo_id_from_hash(&hash))
}

/// Hash of a string, returned as lowercase hex (for telemetry repo ID — not crypto-sensitive)
pub fn compute_repo_id_from_hash(commit_hash: &str) -> String {
    let hash = xxhash_rust::xxh3::xxh3_128(commit_hash.as_bytes());
    format!("{:032x}", hash)
}

/// Check if we should show the opt-in prompt
pub fn should_prompt(file_enabled: Option<bool>, has_env_override: bool) -> bool {
    file_enabled.is_none() && !has_env_override
}

/// Show the opt-in prompt. Returns true if user accepts.
/// Only shows when stderr is a TTY.
pub fn show_opt_in_prompt() -> Option<bool> {
    use std::io::IsTerminal;

    // Only show if stderr is a TTY
    if !std::io::stderr().is_terminal() {
        return None; // Non-interactive: don't prompt, default to off
    }

    eprintln!("────────────────────────────────────────────────────");
    eprintln!("Help improve repotoire?");
    eprintln!();
    eprintln!("Share anonymous usage data to:");
    eprintln!("  - Get ecosystem benchmarks (\"your score is top 25% for Rust projects\")");
    eprintln!("  - Help us tune detectors and reduce false positives");
    eprintln!();
    eprintln!("No repo names, file paths, or code content. Ever.");
    eprintln!("See what's collected: https://repotoire.com/telemetry");
    eprintln!();
    eprint!("Enable? [y/N] ");

    let mut input = String::new();
    if std::io::stdin().read_line(&mut input).is_err() {
        return Some(false);
    }
    Some(input.trim().eq_ignore_ascii_case("y"))
}

/// Save telemetry choice to config file
pub fn save_telemetry_choice(enabled: bool) -> anyhow::Result<()> {
    let config_path = crate::config::UserConfig::user_config_path()
        .ok_or_else(|| anyhow::anyhow!("Could not determine config directory"))?;
    if let Some(parent) = config_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut content = std::fs::read_to_string(&config_path).unwrap_or_default();
    if content.contains("[telemetry]") {
        // Replace existing enabled value
        content = content
            .replace("enabled = true", &format!("enabled = {}", enabled))
            .replace("enabled = false", &format!("enabled = {}", enabled));
    } else {
        content.push_str(&format!("\n[telemetry]\nenabled = {}\n", enabled));
    }
    std::fs::write(&config_path, &content)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_enabled_respects_do_not_track() {
        // DO_NOT_TRACK=1 should always disable telemetry
        let state = TelemetryState::resolve_with_env(None, Some("1"), None);
        assert!(!state.is_enabled());
    }

    #[test]
    fn test_is_enabled_env_override() {
        // REPOTOIRE_TELEMETRY=on should enable telemetry
        let state = TelemetryState::resolve_with_env(None, None, Some("on"));
        assert!(state.is_enabled());
    }

    #[test]
    fn test_is_enabled_defaults_to_false() {
        // No env vars, no config -> disabled
        let state = TelemetryState::resolve_with_env(None, None, None);
        assert!(!state.is_enabled());
    }

    #[test]
    fn test_do_not_track_overrides_explicit_config_enabled() {
        let state = TelemetryState::resolve_with_env(Some(true), Some("1"), None);
        assert!(!state.is_enabled());
    }

    #[test]
    fn test_do_not_track_overrides_env_var() {
        let state = TelemetryState::resolve_with_env(None, Some("1"), Some("on"));
        assert!(!state.is_enabled());
    }

    #[test]
    fn test_distinct_id_generation() {
        let id = generate_distinct_id();
        // 16 random bytes as hex = 32 chars
        assert_eq!(id.len(), 32);
        // Should be valid hex
        assert!(id.chars().all(|c| c.is_ascii_hexdigit()));
        // Two generated IDs should differ
        let id2 = generate_distinct_id();
        assert_ne!(id, id2);
    }

    #[test]
    fn test_repo_id_from_root_commit() {
        let hash = "abc123def456abc123def456abc123def456abc1";
        let repo_id = compute_repo_id_from_hash(hash);
        // XXH3-128 produces 32 hex characters
        assert_eq!(repo_id.len(), 32);
        // Verify it's valid hex
        assert!(repo_id.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn test_should_prompt_only_when_undecided() {
        assert!(should_prompt(None, false)); // Undecided, no env → prompt
        assert!(!should_prompt(Some(true), false)); // Already on → no prompt
        assert!(!should_prompt(Some(false), false)); // Already off → no prompt
        assert!(!should_prompt(None, true)); // Env override → no prompt
    }
}
