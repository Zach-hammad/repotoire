# Telemetry & Ecosystem Benchmarks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in telemetry that sends anonymized analysis data to PostHog and displays ecosystem benchmarks (percentile rankings, graph health comparisons) inline after every analysis.

**Architecture:** CLI sends events via direct `ureq` POST to PostHog capture API (fire-and-forget background thread). Benchmarks are pre-computed by a GitHub Action cron job and served as static JSON from a CDN. A `telemetry/` module handles config, events, capture, benchmarks, caching, and display.

**Tech Stack:** Rust, ureq (existing dep), serde_json (existing dep), uuid (new dep), PostHog capture API, Cloudflare R2/S3 for CDN

**Spec:** `docs/superpowers/specs/2026-03-20-telemetry-ecosystem-benchmarks-design.md`

---

## File Structure

### New Files

All paths are relative to `repotoire-cli/`.

| File | Responsibility |
|---|---|
| `repotoire-cli/src/telemetry/mod.rs` | Public API: `init()`, `Telemetry` handle, re-exports |
| `repotoire-cli/src/telemetry/config.rs` | Opt-in state, `distinct_id` generation, `repo_id` derivation, env var checks, TTY prompt |
| `repotoire-cli/src/telemetry/events.rs` | Event structs + flags allowlist + calibration outlier selection |
| `repotoire-cli/src/telemetry/posthog.rs` | PostHog capture API wrapper via ureq, background thread dispatch (named `posthog.rs` per spec) |
| `repotoire-cli/src/telemetry/benchmarks.rs` | CDN fetch, fallback chain, percentile interpolation, schema version validation |
| `repotoire-cli/src/telemetry/cache.rs` | 24h benchmark cache with stale-cache label, per-repo telemetry state (`nth_analysis`, `score_history`) |
| `repotoire-cli/src/telemetry/display.rs` | Terminal formatting for ecosystem context and `benchmark` command output |
| `repotoire-cli/src/telemetry/repo_shape.rs` | Repo shape detection (not in spec module list — added for separation of concerns) |
| `repotoire-cli/src/cli/benchmark.rs` | `repotoire benchmark` command handler |

### Modified Files

| File | Change |
|---|---|
| `repotoire-cli/src/lib.rs` | Add `pub mod telemetry;` |
| `repotoire-cli/src/main.rs` | Call `telemetry::init()` |
| `repotoire-cli/src/cli/mod.rs` | Add `Benchmark` command, `Telemetry` config subcommand, hook events into command dispatch, update help text |
| `repotoire-cli/src/cli/analyze/mod.rs` | Send `analysis_complete` event, display ecosystem context |
| `repotoire-cli/src/cli/analyze/output.rs` | Append benchmark section to text output |
| `repotoire-cli/src/cli/diff.rs` | Send `diff_run` event |
| `repotoire-cli/src/cli/fix.rs` | Send `fix_applied` event |
| `repotoire-cli/src/cli/watch.rs` | Send `watch_session` event |
| `repotoire-cli/src/config/user_config.rs` | Add `TelemetryConfig` struct, `[telemetry]` section parsing |
| `repotoire-cli/src/cache/paths.rs` | Add `telemetry_state_path()` and `benchmark_cache_dir()` |
| `repotoire-cli/Cargo.toml` | Add `uuid`, `sha2` dependencies |

**Note on naming:** The spec calls the capture module `posthog.rs`. This plan follows the spec naming.

---

## Task 1: Telemetry Config & Opt-In

**Files:**
- Create: `repotoire-cli/src/telemetry/mod.rs`
- Create: `repotoire-cli/src/telemetry/config.rs`
- Modify: `repotoire-cli/src/lib.rs`
- Modify: `repotoire-cli/src/config/user_config.rs`
- Modify: `repotoire-cli/Cargo.toml`

- [ ] **Step 1: Write test for TelemetryConfig parsing**

```rust
// In src/config/user_config.rs, add to existing #[cfg(test)] mod tests:

#[test]
fn test_toml_parsing_telemetry_enabled() {
    let toml_str = r#"
[telemetry]
enabled = true
"#;
    let config: UserConfig = toml::from_str(toml_str).expect("parse telemetry config");
    assert_eq!(config.telemetry.enabled, Some(true));
}

#[test]
fn test_toml_parsing_no_telemetry_section() {
    let toml_str = "";
    let config: UserConfig = toml::from_str(toml_str).expect("parse empty config");
    assert_eq!(config.telemetry.enabled, None);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib config::user_config::tests::test_toml_parsing_telemetry -v`
Expected: FAIL — `UserConfig` has no field `telemetry`

- [ ] **Step 3: Add TelemetryConfig to UserConfig**

In `src/config/user_config.rs`, add the struct and field:

```rust
#[derive(Debug, Default, Deserialize, Serialize)]
pub struct TelemetryConfig {
    /// Whether telemetry is enabled (None = not yet decided)
    pub enabled: Option<bool>,
}

// Add to UserConfig struct:
#[derive(Debug, Default, Deserialize, Serialize)]
pub struct UserConfig {
    #[serde(default)]
    pub ai: AiConfig,
    #[serde(default)]
    pub telemetry: TelemetryConfig,
}
```

Update `merge()` to include telemetry:

```rust
fn merge(&mut self, other: UserConfig) {
    // ... existing ai merges ...
    if other.telemetry.enabled.is_some() {
        self.telemetry.enabled = other.telemetry.enabled;
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo test --lib config::user_config::tests -v`
Expected: All pass

- [ ] **Step 5: Add uuid and sha2 dependencies**

In `repotoire-cli/Cargo.toml`, add:
```toml
uuid = { version = "1", features = ["v4"] }
sha2 = "0.10"
```

- [ ] **Step 6: Write tests for telemetry config module**

Create `src/telemetry/config.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    // NOTE: Do NOT use std::env::set_var in tests — it is unsound in parallel test
    // execution (Rust 1.66+). Instead, pass env values as parameters.

    #[test]
    fn test_is_enabled_respects_do_not_track() {
        let config = TelemetryState::resolve_with_env(None, Some("1"), None);
        assert!(!config.is_enabled());
    }

    #[test]
    fn test_is_enabled_env_override() {
        let config = TelemetryState::resolve_with_env(None, None, Some("on"));
        assert!(config.is_enabled());
    }

    #[test]
    fn test_is_enabled_defaults_to_false() {
        let config = TelemetryState::resolve_with_env(None, None, None);
        assert!(!config.is_enabled());
    }

    #[test]
    fn test_distinct_id_generation() {
        let id = generate_distinct_id();
        assert_eq!(id.len(), 36); // UUID v4 format
    }

    #[test]
    fn test_repo_id_from_root_commit() {
        // SHA-256 of a known string
        let repo_id = compute_repo_id_from_hash("abc123");
        assert_eq!(repo_id.len(), 64); // SHA-256 hex length
    }
}
```

- [ ] **Step 7: Run test to verify it fails**

Run: `cargo test --lib telemetry::config::tests -v`
Expected: FAIL — module doesn't exist yet

- [ ] **Step 8: Implement telemetry config**

Create `src/telemetry/mod.rs`:

```rust
pub mod config;

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
```

Create `src/telemetry/config.rs` (full implementation):

```rust
use anyhow::Result;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

/// Resolved telemetry state after checking all sources
pub struct TelemetryState {
    pub enabled: bool,
    pub distinct_id: Option<String>,
}

impl TelemetryState {
    /// Load telemetry state from config file + env vars
    pub fn load() -> Result<Self> {
        let user_config = crate::config::user_config::UserConfig::load()?;
        let file_enabled = user_config.telemetry.enabled;
        Ok(Self::resolve(file_enabled))
    }

    /// For testing: resolve from explicit env values (avoids unsafe env::set_var)
    pub fn resolve_with_env(
        file_enabled: Option<bool>,
        do_not_track: Option<&str>,
        repotoire_telemetry: Option<&str>,
    ) -> Self {
        // DO_NOT_TRACK=1 always wins
        if do_not_track == Some("1") {
            return Self { enabled: false, distinct_id: None };
        }

        // REPOTOIRE_TELEMETRY env var overrides config file
        if let Some(val) = repotoire_telemetry {
            let enabled = matches!(val.to_lowercase().as_str(), "on" | "true" | "1");
            return Self {
                enabled,
                distinct_id: if enabled { Self::load_or_create_distinct_id().ok() } else { None },
            };
        }

        // Config file value
        let enabled = file_enabled.unwrap_or(false);
        Self {
            enabled,
            distinct_id: if enabled { Self::load_or_create_distinct_id().ok() } else { None },
        }
    }

    fn resolve(file_enabled: Option<bool>) -> Self {
        Self::resolve_with_env(
            file_enabled,
            std::env::var("DO_NOT_TRACK").ok().as_deref(),
            std::env::var("REPOTOIRE_TELEMETRY").ok().as_deref(),
        )
    }

    pub fn is_enabled(&self) -> bool {
        self.enabled
    }

    fn load_or_create_distinct_id() -> Result<String> {
        let path = telemetry_id_path()
            .ok_or_else(|| anyhow::anyhow!("no config dir"))?;
        if path.exists() {
            Ok(std::fs::read_to_string(&path)?.trim().to_string())
        } else {
            let id = generate_distinct_id();
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(&path, &id)?;
            Ok(id)
        }
    }
}

pub fn generate_distinct_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

pub fn compute_repo_id(repo_path: &Path) -> Option<String> {
    let repo = git2::Repository::open(repo_path).ok()?;
    let mut revwalk = repo.revwalk().ok()?;
    revwalk.push_head().ok()?;
    revwalk.set_sorting(git2::Sort::TIME | git2::Sort::REVERSE).ok()?;
    let root_oid = revwalk.next()?.ok()?;
    Some(compute_repo_id_from_hash(&root_oid.to_string()))
}

pub fn compute_repo_id_from_hash(commit_hash: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(commit_hash.as_bytes());
    format!("{:x}", hasher.finalize())
}

fn telemetry_id_path() -> Option<PathBuf> {
    dirs::config_dir().map(|p| p.join("repotoire").join("telemetry_id"))
}
```

Add to `src/lib.rs`:
```rust
pub mod telemetry;
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cargo test --lib telemetry::config::tests -v`
Expected: All pass (sha2 was added in Step 5)

- [ ] **Step 10: Commit**

```bash
git add repotoire-cli/src/telemetry/ repotoire-cli/src/lib.rs repotoire-cli/src/config/user_config.rs repotoire-cli/Cargo.toml
git commit -m "feat(telemetry): add config, opt-in state, distinct_id and repo_id"
```

---

## Task 2: Event Structs & PostHog Capture

**Files:**
- Create: `repotoire-cli/src/telemetry/events.rs`
- Create: `repotoire-cli/src/telemetry/posthog.rs`
- Modify: `repotoire-cli/src/telemetry/mod.rs`

- [ ] **Step 1: Write test for event serialization**

In `src/telemetry/events.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_analysis_complete_serializes() {
        let event = AnalysisComplete {
            repo_id: Some("abc123".into()),
            nth_analysis: Some(5),
            score: 72.4,
            grade: "B+".into(),
            pillar_structure: 78.0,
            pillar_quality: 65.2,
            pillar_architecture: 74.1,
            primary_language: "rust".into(),
            total_kloc: 20.0,
            total_files: 342,
            // ... remaining fields with defaults
            ..Default::default()
        };
        let json = serde_json::to_value(&event).expect("serialize");
        assert_eq!(json["score"], 72.4);
        assert_eq!(json["grade"], "B+");
        assert_eq!(json["primary_language"], "rust");
    }

    #[test]
    fn test_command_used_serializes() {
        let event = CommandUsed {
            command: "analyze".into(),
            subcommand: None,
            flags: vec!["--format".into(), "--output".into()],
            duration_ms: 120,
            exit_code: 0,
            version: env!("CARGO_PKG_VERSION").into(),
            os: std::env::consts::OS.into(),
            ci: false,
        };
        let json = serde_json::to_value(&event).expect("serialize");
        assert_eq!(json["command"], "analyze");
        assert!(json["flags"].as_array().expect("array").len() == 2);
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib telemetry::events::tests -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement event structs**

Create `src/telemetry/events.rs` with all 6 event types from the spec. Each struct derives `Serialize`, `Default`. Use `#[serde(skip_serializing_if = "Option::is_none")]` for optional fields. Include all properties from the spec's data model tables.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo test --lib telemetry::events::tests -v`
Expected: All pass

- [ ] **Step 5: Write test for PostHog capture**

In `repotoire-cli/src/telemetry/posthog.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_capture_payload() {
        let payload = build_capture_payload(
            "phc_test_key",
            "analysis_complete",
            "uuid-123",
            &serde_json::json!({"score": 72.4}),
        );
        assert_eq!(payload["api_key"], "phc_test_key");
        assert_eq!(payload["event"], "analysis_complete");
        assert_eq!(payload["distinct_id"], "uuid-123");
        assert_eq!(payload["properties"]["score"], 72.4);
    }

    #[test]
    fn test_capture_does_not_block() {
        // Verify fire-and-forget: capture returns immediately even with bad URL
        let start = std::time::Instant::now();
        let _ = send_event_background("https://invalid.test/capture", "key", "evt", "id", &serde_json::json!({}));
        assert!(start.elapsed().as_millis() < 100);
    }
}
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cargo test --lib telemetry::posthog::tests -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 7: Implement PostHog capture**

Create `repotoire-cli/src/telemetry/posthog.rs`:

```rust
use serde_json::Value;
use std::thread;

const POSTHOG_CAPTURE_URL: &str = "https://app.posthog.com/capture/";
const POSTHOG_API_KEY: &str = "phc_REPLACE_WITH_REAL_KEY";

pub fn build_capture_payload(
    api_key: &str,
    event: &str,
    distinct_id: &str,
    properties: &Value,
) -> Value {
    serde_json::json!({
        "api_key": api_key,
        "event": event,
        "distinct_id": distinct_id,
        "properties": properties,
    })
}

/// Fire-and-forget: spawns a background thread to POST the event.
/// Returns immediately. Failures are silently ignored.
pub fn send_event_background(
    url: &str,
    api_key: &str,
    event: &str,
    distinct_id: &str,
    properties: &Value,
) {
    let payload = build_capture_payload(api_key, event, distinct_id, properties);
    let url = url.to_string();
    thread::spawn(move || {
        let agent = ureq::config::Config::builder()
            .http_status_as_error(false)
            .timeout_global(Some(std::time::Duration::from_secs(10)))
            .build()
            .new_agent();
        let _ = agent.post(&url).send_json(&payload);
    });
}

/// Send an event using the default PostHog URL and API key
pub fn capture(event: &str, distinct_id: &str, properties: &Value) {
    send_event_background(POSTHOG_CAPTURE_URL, POSTHOG_API_KEY, event, distinct_id, properties);
}
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cargo test --lib telemetry::posthog::tests -v`
Expected: All pass

- [ ] **Step 9: Update mod.rs to export new modules**

In `src/telemetry/mod.rs`, add:
```rust
pub mod events;
pub mod posthog;
```

- [ ] **Step 10: Commit**

```bash
git add repotoire-cli/src/telemetry/events.rs repotoire-cli/src/telemetry/posthog.rs repotoire-cli/src/telemetry/mod.rs
git commit -m "feat(telemetry): add event structs and PostHog capture wrapper"
```

---

## Task 3: Repo Shape Detection

**Files:**
- Create: `repotoire-cli/src/telemetry/repo_shape.rs`
- Modify: `repotoire-cli/src/telemetry/mod.rs`

- [ ] **Step 1: Write tests for repo shape detection**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;
    use std::fs;

    #[test]
    fn test_single_package_default() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("Cargo.toml"), "[package]\nname = \"foo\"").unwrap();
        let shape = detect_repo_shape(dir.path());
        assert_eq!(shape.repo_shape, "single-package");
        assert!(!shape.has_workspace);
        assert_eq!(shape.buildable_roots, 1);
    }

    #[test]
    fn test_workspace_detected() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("Cargo.toml"), "[workspace]\nmembers = [\"a\", \"b\"]").unwrap();
        fs::create_dir_all(dir.path().join("a")).unwrap();
        fs::write(dir.path().join("a/Cargo.toml"), "[package]\nname = \"a\"").unwrap();
        let shape = detect_repo_shape(dir.path());
        assert_eq!(shape.repo_shape, "workspace");
        assert!(shape.has_workspace);
    }

    #[test]
    fn test_monorepo_by_buildable_roots() {
        let dir = TempDir::new().unwrap();
        for name in ["svc-a", "svc-b", "svc-c"] {
            let p = dir.path().join(name);
            fs::create_dir_all(&p).unwrap();
            fs::write(p.join("package.json"), r#"{"scripts":{"build":"tsc"}}"#).unwrap();
        }
        let shape = detect_repo_shape(dir.path());
        assert_eq!(shape.repo_shape, "monorepo");
        assert_eq!(shape.buildable_roots, 3);
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib telemetry::repo_shape::tests -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement repo shape detection**

Create `src/telemetry/repo_shape.rs` with:
- `RepoShapeInfo` struct (all fields from spec)
- `detect_repo_shape(path: &Path) -> RepoShapeInfo`
- Heuristics: scan for Cargo.toml `[workspace]`, pnpm-workspace.yaml, lerna.json, go.work; count buildable roots at distinct subtrees
- Evaluation order: monorepo → workspace → multi-package → single-package (first match wins)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo test --lib telemetry::repo_shape::tests -v`
Expected: All pass

- [ ] **Step 5: Update mod.rs, commit**

```bash
git add repotoire-cli/src/telemetry/repo_shape.rs repotoire-cli/src/telemetry/mod.rs
git commit -m "feat(telemetry): add repo shape detection"
```

---

## Task 4: Telemetry State Persistence (nth_analysis, score_history)

**Files:**
- Create: `repotoire-cli/src/telemetry/cache.rs`
- Modify: `repotoire-cli/src/cache/paths.rs`
- Modify: `repotoire-cli/src/telemetry/mod.rs`

- [ ] **Step 1: Write test for telemetry state persistence**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_increment_nth_analysis() {
        let dir = TempDir::new().unwrap();
        let mut state = TelemetryRepoState::load_or_default(dir.path());
        assert_eq!(state.nth_analysis, 0);
        state.record_analysis(72.4);
        assert_eq!(state.nth_analysis, 1);
        assert_eq!(state.score_history.len(), 1);
        state.save(dir.path()).unwrap();

        let reloaded = TelemetryRepoState::load_or_default(dir.path());
        assert_eq!(reloaded.nth_analysis, 1);
    }

    #[test]
    fn test_score_history_capped_at_100() {
        let dir = TempDir::new().unwrap();
        let mut state = TelemetryRepoState::load_or_default(dir.path());
        for i in 0..110 {
            state.record_analysis(i as f64);
        }
        assert_eq!(state.score_history.len(), 100);
        assert_eq!(state.nth_analysis, 110);
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib telemetry::cache::tests -v`
Expected: FAIL

- [ ] **Step 3: Add cache path helpers**

In `src/cache/paths.rs`, add:

```rust
/// Telemetry state file for a repository
pub fn telemetry_state_path(repo_path: &Path) -> PathBuf {
    cache_dir(repo_path).join("telemetry_state.json")
}

/// Benchmark cache directory (global, not per-repo)
pub fn benchmark_cache_dir() -> PathBuf {
    dirs::cache_dir()
        .unwrap_or_else(|| PathBuf::from(".cache"))
        .join("repotoire")
        .join("benchmarks")
}
```

- [ ] **Step 4: Implement telemetry repo state**

Create `src/telemetry/cache.rs`:

```rust
use anyhow::Result;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct TelemetryRepoState {
    pub nth_analysis: u64,
    pub score_history: Vec<ScoreEntry>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ScoreEntry {
    pub score: f64,
    pub timestamp: DateTime<Utc>,
}

impl TelemetryRepoState {
    pub fn load_or_default(cache_path: &Path) -> Self {
        let state_path = cache_path.join("telemetry_state.json");
        std::fs::read_to_string(&state_path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    pub fn record_analysis(&mut self, score: f64) {
        self.nth_analysis += 1;
        self.score_history.push(ScoreEntry {
            score,
            timestamp: Utc::now(),
        });
        // Cap at 100 entries — keep most recent
        if self.score_history.len() > 100 {
            let drain_count = self.score_history.len() - 100;
            self.score_history.drain(..drain_count);
        }
    }

    pub fn save(&self, cache_path: &Path) -> Result<()> {
        let state_path = cache_path.join("telemetry_state.json");
        std::fs::create_dir_all(cache_path)?;
        std::fs::write(&state_path, serde_json::to_string_pretty(self)?)?;
        Ok(())
    }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cargo test --lib telemetry::cache::tests -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/telemetry/cache.rs repotoire-cli/src/cache/paths.rs repotoire-cli/src/telemetry/mod.rs
git commit -m "feat(telemetry): add per-repo state persistence (nth_analysis, score_history)"
```

---

## Task 5: Benchmark Fetch & Fallback Chain

**Files:**
- Create: `repotoire-cli/src/telemetry/benchmarks.rs`
- Modify: `repotoire-cli/src/telemetry/mod.rs`

- [ ] **Step 1: Write test for benchmark data parsing**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE_BENCHMARK: &str = r#"{
        "schema_version": 1,
        "segment": {"language": "rust", "kloc_bucket": "10-50k"},
        "sample_size": 1247,
        "updated_at": "2026-03-20T14:00:00Z",
        "score": {"p25": 58.2, "p50": 67.1, "p75": 76.8, "p90": 84.3},
        "pillar_structure": {"p25": 62.0, "p50": 71.3, "p75": 80.1, "p90": 87.5},
        "pillar_quality": {"p25": 55.1, "p50": 64.8, "p75": 74.2, "p90": 82.0},
        "pillar_architecture": {"p25": 60.4, "p50": 69.7, "p75": 78.5, "p90": 85.8},
        "graph_modularity": {"p25": 0.45, "p50": 0.58, "p75": 0.71, "p90": 0.82},
        "graph_avg_degree": {"p25": 3.2, "p50": 5.1, "p75": 8.4, "p90": 12.7},
        "graph_scc_count": {"pct_zero": 0.45, "p50": 2, "p75": 5, "p90": 11},
        "grade_distribution": {"A+": 0.02, "A": 0.05, "B+": 0.12, "B": 0.18},
        "top_detectors": [{"name": "dead_code", "pct_repos_with_findings": 0.78}],
        "detector_accuracy": [{"name": "sql_injection", "true_positive_rate": 0.88, "feedback_count": 234}],
        "avg_improvement_per_analysis": 0.8
    }"#;

    #[test]
    fn test_parse_benchmark_json() {
        let data: BenchmarkData = serde_json::from_str(SAMPLE_BENCHMARK).expect("parse");
        assert_eq!(data.schema_version, 1);
        assert_eq!(data.sample_size, 1247);
        assert!((data.score.p50 - 67.1).abs() < f64::EPSILON);
    }

    #[test]
    fn test_compute_percentile_interpolation() {
        let dist = PercentileDistribution { p25: 50.0, p50: 65.0, p75: 80.0, p90: 90.0 };
        // Score of 72.5 is between p50 (65) and p75 (80) → ~65th percentile
        let pct = interpolate_percentile(72.5, &dist);
        assert!(pct > 60.0 && pct < 75.0);
    }

    #[test]
    fn test_kloc_to_bucket() {
        assert_eq!(kloc_to_bucket(3.0), "0-5k");
        assert_eq!(kloc_to_bucket(5.0), "5-10k");
        assert_eq!(kloc_to_bucket(20.0), "10-50k");
        assert_eq!(kloc_to_bucket(150.0), "100k+");
    }

    #[test]
    fn test_fallback_chain_order() {
        let chain = build_fallback_urls("rust", 20.0);
        assert_eq!(chain.len(), 3);
        assert!(chain[0].contains("lang/rust/10-50k.json"));
        assert!(chain[1].contains("lang/rust.json"));
        assert!(chain[2].contains("global.json"));
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib telemetry::benchmarks::tests -v`
Expected: FAIL

- [ ] **Step 3: Implement benchmark data structures and fetch**

Create `src/telemetry/benchmarks.rs` with:
- `BenchmarkData` struct matching the spec JSON schema
- `PercentileDistribution` struct (`p25`, `p50`, `p75`, `p90`)
- `interpolate_percentile(score: f64, dist: &PercentileDistribution) -> f64` — linear interpolation between known percentiles
- `kloc_to_bucket(kloc: f64) -> &str` — inclusive-exclusive boundaries
- `build_fallback_urls(language: &str, kloc: f64) -> Vec<String>` — 3-level fallback chain
- `fetch_benchmarks(language: &str, kloc: f64) -> Option<BenchmarkData>` — try each URL in order, first success with `sample_size >= 50` and `schema_version == 1` wins. Uses 24h cache via `benchmark_cache_dir()`. Stale cache (<48h) used with "(cached Xh ago)" label if CDN fetch fails. Failures return None (silent).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo test --lib telemetry::benchmarks::tests -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/telemetry/benchmarks.rs repotoire-cli/src/telemetry/mod.rs
git commit -m "feat(telemetry): add benchmark fetch with fallback chain and percentile interpolation"
```

---

## Task 6: Benchmark Display Formatting

**Files:**
- Create: `repotoire-cli/src/telemetry/display.rs`
- Modify: `repotoire-cli/src/telemetry/mod.rs`

- [ ] **Step 1: Write test for ecosystem context formatting**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_format_ecosystem_context_basic() {
        let ctx = EcosystemContext {
            score_percentile: 68.0,
            comparison_group: "Rust projects".into(),
            sample_size: 1247,
            pillar_percentiles: Some(PillarPercentiles {
                structure: 70.0,
                quality: 45.0,
                architecture: 80.0,
            }),
            modularity_percentile: Some(85.0),
            coupling_percentile: Some(60.0),
            trend: None,
        };
        let output = format_ecosystem_context(&ctx);
        assert!(output.contains("better than 68%"));
        assert!(output.contains("Rust projects"));
        assert!(output.contains("1,247"));
    }

    #[test]
    fn test_format_ecosystem_context_insufficient_data() {
        let output = format_insufficient_data("Rust workspace");
        assert!(output.contains("Not enough data"));
        assert!(output.contains("Rust workspace"));
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib telemetry::display::tests -v`
Expected: FAIL

- [ ] **Step 3: Implement display formatting**

Create `src/telemetry/display.rs` with:
- `EcosystemContext` struct holding all computed percentiles
- `PillarPercentiles` struct
- `format_ecosystem_context(ctx: &EcosystemContext) -> String` — renders the "Ecosystem Context" box from the spec
- `format_insufficient_data(segment_name: &str) -> String`
- `format_telemetry_tip() -> String` — the opt-in suggestion shown when telemetry is off
- `format_benchmark_full(ctx: &EcosystemContext, ...) -> String` — full `repotoire benchmark` output
- Use `console` crate (already a dependency) for terminal styling

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo test --lib telemetry::display::tests -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/telemetry/display.rs repotoire-cli/src/telemetry/mod.rs
git commit -m "feat(telemetry): add benchmark display formatting"
```

---

## Task 7: CLI Scaffolding (init, commands, help text)

**Files:**
- Modify: `repotoire-cli/src/main.rs`
- Modify: `repotoire-cli/src/cli/mod.rs`
- Create: `repotoire-cli/src/cli/benchmark.rs`

- [ ] **Step 1: Write test for command_used exclusion list**

In `repotoire-cli/src/telemetry/events.rs`, add:

```rust
/// Commands excluded from command_used events
const EXCLUDED_COMMANDS: &[&str] = &["--help", "--version"];
const EXCLUDED_SUBCOMMANDS: &[(&str, &str)] = &[("config", "telemetry")];

/// Flags allowed to be sent (names only, never values)
const ALLOWED_FLAGS: &[&str] = &[
    "--format", "--output", "--severity", "--top", "--page",
    "--fail-on", "--explain-score", "--timings", "--verify",
    "--relaxed", "--no-emoji", "--json",
];

pub fn should_track_command(command: &str, subcommand: Option<&str>) -> bool {
    if EXCLUDED_COMMANDS.contains(&command) {
        return false;
    }
    if let Some(sub) = subcommand {
        if EXCLUDED_SUBCOMMANDS.contains(&(command, sub)) {
            return false;
        }
    }
    true
}

pub fn filter_flags(flags: &[String]) -> Vec<String> {
    flags.iter()
        .filter(|f| ALLOWED_FLAGS.contains(&f.as_str()))
        .cloned()
        .collect()
}

// In tests:
#[test]
fn test_command_exclusion_list() {
    assert!(!should_track_command("--help", None));
    assert!(!should_track_command("--version", None));
    assert!(!should_track_command("config", Some("telemetry")));
    assert!(should_track_command("analyze", None));
    assert!(should_track_command("config", Some("show")));
}

#[test]
fn test_flags_allowlist() {
    let flags = vec!["--format".into(), "--secret-flag".into(), "--output".into()];
    let filtered = filter_flags(&flags);
    assert_eq!(filtered, vec!["--format", "--output"]);
}
```

- [ ] **Step 2: Write test for calibration outlier selection**

In `repotoire-cli/src/telemetry/events.rs`, add:

```rust
pub fn select_calibration_outliers(
    calibrated: &std::collections::HashMap<String, f64>,
    defaults: &std::collections::HashMap<String, f64>,
) -> (usize, usize, std::collections::HashMap<String, f64>) {
    let total = calibrated.len();
    let mut deviations: Vec<(String, f64, f64)> = Vec::new();
    let mut at_default = 0;

    for (key, &cal_val) in calibrated {
        if let Some(&def_val) = defaults.get(key) {
            let deviation = if def_val.abs() > f64::EPSILON {
                ((cal_val - def_val) / def_val).abs()
            } else {
                0.0
            };
            if deviation < 0.01 {
                at_default += 1;
            }
            deviations.push((key.clone(), cal_val, deviation));
        }
    }

    deviations.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));
    let outliers: std::collections::HashMap<String, f64> = deviations
        .into_iter()
        .take(10)
        .filter(|(_, _, dev)| *dev >= 0.01)
        .map(|(k, v, _)| (k, v))
        .collect();

    (total, at_default, outliers)
}

#[test]
fn test_calibration_outlier_selection() {
    let mut calibrated = std::collections::HashMap::new();
    let mut defaults = std::collections::HashMap::new();
    calibrated.insert("complexity".into(), 14.2);
    calibrated.insert("nesting".into(), 5.0);
    calibrated.insert("method_length".into(), 30.0);
    defaults.insert("complexity".into(), 10.0);
    defaults.insert("nesting".into(), 5.0);
    defaults.insert("method_length".into(), 30.0);

    let (total, at_default, outliers) = select_calibration_outliers(&calibrated, &defaults);
    assert_eq!(total, 3);
    assert_eq!(at_default, 2); // nesting and method_length at default
    assert!(outliers.contains_key("complexity")); // 42% deviation
    assert!(!outliers.contains_key("nesting")); // 0% deviation
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cargo test --lib telemetry::events::tests -v`
Expected: FAIL

- [ ] **Step 4: Implement the exclusion list, flags allowlist, and calibration outlier selection**

Add the implementations shown above to `repotoire-cli/src/telemetry/events.rs`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cargo test --lib telemetry::events::tests -v`
Expected: All pass

- [ ] **Step 6: Update main.rs to initialize telemetry**

In `repotoire-cli/src/main.rs`, after the tracing initialization (line 26), add:

```rust
let telemetry = repotoire::telemetry::init()?;
```

Pass `telemetry` to `repotoire::cli::run(cli, telemetry)`.

Update `run()` signature in `repotoire-cli/src/cli/mod.rs` to accept `telemetry: crate::telemetry::Telemetry`.

- [ ] **Step 7: Update CLI help text**

In `repotoire-cli/src/cli/mod.rs`, update the doc comment (line 37) and `long_about` (line 46):

Change: `100% LOCAL — No account needed. No data leaves your machine.`
To: `100% LOCAL by default — No account needed. No data leaves your machine unless you opt in.`

- [ ] **Step 8: Add Benchmark command and Telemetry config subcommand**

In `repotoire-cli/src/cli/mod.rs`, add to the `Commands` enum:

```rust
/// View ecosystem benchmarks for your project
Benchmark {
    /// Output format: text, json
    #[arg(long, short = 'f', default_value = "text")]
    format: String,
},
```

Add to `ConfigAction` enum:

```rust
/// Manage telemetry settings
Telemetry {
    /// Action: on, off, status
    action: String,
},
```

- [ ] **Step 9: Implement config telemetry handler and benchmark command stub**

In the `run()` match for `ConfigAction::Telemetry { action }`:

```rust
match action.as_str() {
    "on" => { /* set enabled=true in config, create distinct_id */ }
    "off" => { /* set enabled=false in config */ }
    "status" => { /* print current state, what's collected */ }
    _ => eprintln!("Unknown action: {}. Use on, off, or status.", action),
}
```

Create `repotoire-cli/src/cli/benchmark.rs` with a handler that:
- Checks telemetry is enabled (error message if not)
- Loads last analysis from `health_cache_path()` and `findings_cache_path()`
- Extracts `primary_language` and `total_kloc` for the fallback chain
- Fetches benchmarks, formats output (text or `--json`)
- Falls back gracefully if no cached analysis exists

- [ ] **Step 10: Run cargo check**

Run: `cargo check`
Expected: Clean compilation

- [ ] **Step 11: Commit**

```bash
git add repotoire-cli/src/main.rs repotoire-cli/src/cli/
git commit -m "feat(telemetry): add CLI scaffolding, benchmark command, config telemetry subcommand"
```

---

## Task 8: Wire Events Into Analyze + Benchmark Display

**Files:**
- Modify: `repotoire-cli/src/cli/analyze/mod.rs`
- Modify: `repotoire-cli/src/cli/analyze/output.rs`

- [ ] **Step 1: Wire analysis_complete event into analyze**

In `repotoire-cli/src/cli/analyze/mod.rs`, after analysis completes and scoring is done:
1. Compute `repo_id` via `telemetry::config::compute_repo_id(&path)`
2. Detect repo shape via `telemetry::repo_shape::detect_repo_shape(&path)`
3. Load/update `TelemetryRepoState` (increment `nth_analysis`, record score)
4. Select calibration outliers via `events::select_calibration_outliers()`
5. Build `AnalysisComplete` event from the `AnalysisResult`
6. Call `telemetry::posthog::capture()` (fire-and-forget)

- [ ] **Step 2: Wire benchmark display into analyze output**

In `repotoire-cli/src/cli/analyze/output.rs`, after rendering the report:
1. If telemetry enabled: fetch benchmarks via `telemetry::benchmarks::fetch_benchmarks()`, compute percentiles, append ecosystem context via `telemetry::display::format_ecosystem_context()`
2. If telemetry enabled but no data: show `telemetry::display::format_insufficient_data()`
3. If telemetry off: show `telemetry::display::format_telemetry_tip()` (once per session)
4. If telemetry enabled: append footer `telemetry: on (repotoire config telemetry off to disable)`

- [ ] **Step 3: Run cargo check**

Run: `cargo check`
Expected: Clean compilation

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/analyze/
git commit -m "feat(telemetry): wire analysis_complete event and benchmark display into analyze"
```

---

## Task 9: Wire Events Into Remaining Commands

**Files:**
- Modify: `repotoire-cli/src/cli/diff.rs`
- Modify: `repotoire-cli/src/cli/fix.rs`
- Modify: `repotoire-cli/src/cli/watch.rs`
- Modify: `repotoire-cli/src/cli/mod.rs`

- [ ] **Step 1: Wire diff_run event**

In `repotoire-cli/src/cli/diff.rs`, after diff computation completes, build a `DiffRun` event from the `DiffResult` and call `telemetry::posthog::capture()`.

- [ ] **Step 2: Wire fix_applied event**

In `repotoire-cli/src/cli/fix.rs`, after each fix is accepted or rejected, build a `FixApplied` event and call `telemetry::posthog::capture()`.

- [ ] **Step 3: Wire watch_session event**

In `repotoire-cli/src/cli/watch.rs`, track session start time at the beginning of the watch loop. On graceful exit (the existing signal handler), build a `WatchSession` event with duration, reanalysis count, and score delta, then call `telemetry::posthog::capture()`.

- [ ] **Step 4: Wire detector_feedback event**

In the `Commands::Feedback` match arm of `repotoire-cli/src/cli/mod.rs`, after recording the TP/FP label, build a `DetectorFeedback` event and call `telemetry::posthog::capture()`.

- [ ] **Step 5: Wire command_used event**

In `repotoire-cli/src/cli/mod.rs`, at the end of `run()`, record a `CommandUsed` event with the command name, filtered flags, duration, and exit code. Use `events::should_track_command()` to check the exclusion list. Use `events::filter_flags()` to apply the allowlist.

- [ ] **Step 6: Run full test suite**

Run: `cargo test`
Expected: All existing tests still pass, no regressions

- [ ] **Step 7: Run cargo clippy**

Run: `cargo clippy -- -D warnings`
Expected: No warnings

- [ ] **Step 8: Commit**

```bash
git add repotoire-cli/src/cli/
git commit -m "feat(telemetry): wire events into diff, fix, watch, feedback, and command_used"
```

---

## Task 10: First-Run Opt-In Prompt

**Files:**
- Modify: `repotoire-cli/src/telemetry/config.rs`
- Modify: `repotoire-cli/src/cli/mod.rs`

- [ ] **Step 1: Write test for prompt logic**

```rust
#[test]
fn test_should_show_prompt_only_when_undecided_and_tty() {
    // Undecided + no env override → should prompt (if TTY)
    assert!(should_prompt(None, false)); // false = no env override
    // Already decided → no prompt
    assert!(!should_prompt(Some(true), false));
    assert!(!should_prompt(Some(false), false));
    // Env override present → no prompt
    assert!(!should_prompt(None, true));
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib telemetry::config::tests::test_should_show_prompt -v`
Expected: FAIL

- [ ] **Step 3: Implement prompt logic**

In `repotoire-cli/src/telemetry/config.rs`, add:

```rust
pub fn should_prompt(file_enabled: Option<bool>, has_env_override: bool) -> bool {
    file_enabled.is_none() && !has_env_override
}

pub fn show_opt_in_prompt() -> bool {
    // Only show if stderr is a TTY
    if !std::io::stderr().is_terminal() {
        return false;
    }

    eprintln!("────────────────────────────────────────────────────");
    eprintln!("Help improve repotoire?");
    eprintln!();
    eprintln!("Share anonymous usage data to:");
    eprintln!("  - Get ecosystem benchmarks (\"your score is top 25% for Rust projects\")");
    eprintln!("  - Help us tune detectors and reduce false positives");
    eprintln!();
    eprintln!("No repo names, file paths, or code content. Ever.");
    eprintln!("See what's collected: https://repotoire.dev/telemetry");
    eprintln!();
    eprint!("Enable? [y/N] ");

    let mut input = String::new();
    std::io::stdin().read_line(&mut input).unwrap_or(0);
    let enabled = input.trim().eq_ignore_ascii_case("y");

    // Save choice to config file
    save_telemetry_choice(enabled).ok();
    enabled
}
```

- [ ] **Step 4: Wire prompt into telemetry init**

Update `TelemetryState::load()` to call `should_prompt()` and `show_opt_in_prompt()` when telemetry state is undecided.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cargo test --lib telemetry::config::tests -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/telemetry/config.rs repotoire-cli/src/cli/mod.rs
git commit -m "feat(telemetry): add first-run opt-in prompt with TTY and env var checks"
```

---

## Task 11: Cron Job (Benchmark Generator)

**Files:**
- Create: `.github/workflows/benchmark-generator.yml`
- Create: `scripts/generate-benchmarks.py` (or `.sh`)

This task creates the GitHub Action that queries PostHog and publishes benchmark JSON to the CDN.

- [ ] **Step 1: Create GitHub Action workflow**

Create `.github/workflows/benchmark-generator.yml`:

```yaml
name: Generate Ecosystem Benchmarks

on:
  schedule:
    - cron: '0 */6 * * *'  # Every 6 hours
  workflow_dispatch: # Manual trigger

jobs:
  generate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Query PostHog and generate benchmarks
        env:
          POSTHOG_API_KEY: ${{ secrets.POSTHOG_PERSONAL_API_KEY }}
          POSTHOG_PROJECT_ID: ${{ secrets.POSTHOG_PROJECT_ID }}
          R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
          R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
          R2_BUCKET: ${{ secrets.R2_BUCKET }}
          R2_ENDPOINT: ${{ secrets.R2_ENDPOINT }}
        run: python3 scripts/generate-benchmarks.py

      - name: Upload to R2/S3
        run: |
          pip install boto3
          python3 scripts/upload-benchmarks.py
```

- [ ] **Step 2: Create benchmark generation script**

Create `scripts/generate-benchmarks.py`:
- Query PostHog HogQL API for each segment (global, per-language, per-language+size)
- Deduplicate by `repo_id` (latest event per repo)
- Compute p25/p50/p75/p90 percentiles
- Skip segments with <50 unique repos
- Write JSON files to `output/v1/` directory
- Upload to R2/S3

- [ ] **Step 3: Create upload script**

Create `scripts/upload-benchmarks.py`:
- Read files from `output/v1/`
- Upload to R2 bucket with appropriate content-type and cache headers
- Only upload files that changed (compare hashes)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/benchmark-generator.yml scripts/
git commit -m "feat(telemetry): add benchmark generator cron job and upload scripts"
```

---

## Task 12: Integration Test & Final Polish

**Files:**
- Modify: various telemetry files
- Modify: `src/config/user_config.rs` (init_user_config example)

- [ ] **Step 1: Add telemetry section to example config**

In `repotoire-cli/src/config/user_config.rs`, update `init_user_config()` to include:

```toml
[telemetry]
# enabled = true  # Enable to get ecosystem benchmarks and help improve repotoire
```

- [ ] **Step 2: Run full test suite**

Run: `cargo test`
Expected: All pass

- [ ] **Step 3: Run clippy**

Run: `cargo clippy -- -D warnings`
Expected: No warnings

- [ ] **Step 4: Manual smoke test**

```bash
cargo run -- config telemetry status
cargo run -- config telemetry on
cargo run -- analyze . --format text  # Should show telemetry footer
cargo run -- benchmark               # Should attempt CDN fetch
cargo run -- config telemetry off
cargo run -- analyze . --format text  # Should show opt-in tip
```

- [ ] **Step 5: Commit**

```bash
git add .
git commit -m "feat(telemetry): integration test, example config, final polish"
```

---

## Dependency Summary

| Crate | Version | Purpose | New? |
|---|---|---|---|
| `uuid` | 1 (features: v4) | distinct_id generation | Yes |
| `sha2` | 0.10 | repo_id hashing | Yes |
| `ureq` | 3 | PostHog capture + CDN fetch | Existing |
| `serde` / `serde_json` | 1 | Event serialization | Existing |
| `chrono` | 0.4 | Timestamps in score_history | Existing |
| `git2` | 0.20 | Root commit for repo_id | Existing |
| `dirs` | 6 | Config/cache paths | Existing |
| `console` | 0.15 | Terminal styling for benchmarks | Existing |
| `tempfile` | 3 | Tests | Existing (dev) |
