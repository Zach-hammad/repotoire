//! Telemetry state resolution and distinct ID management

use anyhow::Result;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use uuid::Uuid;

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
        let enabled = resolve(file_enabled);
        let distinct_id = if enabled {
            Some(load_or_create_distinct_id()?)
        } else {
            None
        };
        Ok(TelemetryState {
            enabled,
            distinct_id,
        })
    }

    pub fn is_enabled(&self) -> bool {
        self.enabled
    }
}

/// Production resolver — reads real env vars and delegates to `resolve_with_env`
pub fn resolve(file_enabled: Option<bool>) -> bool {
    let do_not_track = std::env::var("DO_NOT_TRACK").ok();
    let repotoire_telemetry = std::env::var("REPOTOIRE_TELEMETRY").ok();
    resolve_with_env(
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
) -> bool {
    // DO_NOT_TRACK=1 wins unconditionally
    if do_not_track == Some("1") {
        return false;
    }

    // REPOTOIRE_TELEMETRY env var
    if let Some(val) = repotoire_telemetry {
        return is_truthy(val);
    }

    // Config file
    if let Some(enabled) = file_enabled {
        return enabled;
    }

    // Default: off
    false
}

fn is_truthy(val: &str) -> bool {
    matches!(
        val.to_lowercase().as_str(),
        "1" | "true" | "yes" | "on" | "enabled"
    )
}

/// Generate a new UUID v4 distinct ID
pub fn generate_distinct_id() -> String {
    Uuid::new_v4().to_string()
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
    let repo = git2::Repository::discover(path).ok()?;
    let mut revwalk = repo.revwalk().ok()?;
    revwalk.push_head().ok()?;
    revwalk.simplify_first_parent().ok()?;

    // Walk to the root commit (last in first-parent chain)
    let root_oid = revwalk.last()?.ok()?;
    let hash = root_oid.to_string();
    Some(compute_repo_id_from_hash(&hash))
}

/// SHA-256 hash of a string, returned as lowercase hex
pub fn compute_repo_id_from_hash(commit_hash: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(commit_hash.as_bytes());
    let result = hasher.finalize();
    format!("{:x}", result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_enabled_respects_do_not_track() {
        // DO_NOT_TRACK=1 should always disable telemetry
        let enabled = resolve_with_env(None, Some("1"), None);
        assert!(!enabled);
    }

    #[test]
    fn test_is_enabled_env_override() {
        // REPOTOIRE_TELEMETRY=on should enable telemetry
        let enabled = resolve_with_env(None, None, Some("on"));
        assert!(enabled);
    }

    #[test]
    fn test_is_enabled_defaults_to_false() {
        // No env vars, no config -> disabled
        let enabled = resolve_with_env(None, None, None);
        assert!(!enabled);
    }

    #[test]
    fn test_distinct_id_generation() {
        let id = generate_distinct_id();
        // UUID v4: 8-4-4-4-12 hex chars separated by dashes = 36 chars total
        assert_eq!(id.len(), 36);
        // Verify it parses as a valid UUID
        let uuid = Uuid::parse_str(&id).unwrap();
        assert_eq!(uuid.get_version_num(), 4);
    }

    #[test]
    fn test_repo_id_from_root_commit() {
        let hash = "abc123def456abc123def456abc123def456abc1";
        let repo_id = compute_repo_id_from_hash(hash);
        // SHA-256 produces 64 hex characters
        assert_eq!(repo_id.len(), 64);
        // Verify it's valid hex
        assert!(repo_id.chars().all(|c| c.is_ascii_hexdigit()));
    }
}
