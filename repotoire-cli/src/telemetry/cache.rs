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
    /// Load from cache dir, or return default if not found/corrupt
    pub fn load_or_default(cache_path: &Path) -> Self {
        let state_path = cache_path.join("telemetry_state.json");
        std::fs::read_to_string(&state_path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    /// Record a new analysis: increment counter, append score, cap at 100
    pub fn record_analysis(&mut self, score: f64) {
        self.nth_analysis += 1;
        self.score_history.push(ScoreEntry {
            score,
            timestamp: Utc::now(),
        });
        if self.score_history.len() > 100 {
            let drain_count = self.score_history.len() - 100;
            self.score_history.drain(..drain_count);
        }
    }

    /// Save to cache dir
    pub fn save(&self, cache_path: &Path) -> Result<()> {
        let state_path = cache_path.join("telemetry_state.json");
        std::fs::create_dir_all(cache_path)?;
        std::fs::write(&state_path, serde_json::to_string_pretty(self)?)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_increment_nth_analysis() {
        let dir = TempDir::new().expect("create temp dir");
        let mut state = TelemetryRepoState::load_or_default(dir.path());
        assert_eq!(state.nth_analysis, 0);
        state.record_analysis(72.4);
        assert_eq!(state.nth_analysis, 1);
        assert_eq!(state.score_history.len(), 1);
        state.save(dir.path()).expect("save");

        let reloaded = TelemetryRepoState::load_or_default(dir.path());
        assert_eq!(reloaded.nth_analysis, 1);
    }

    #[test]
    fn test_score_history_capped_at_100() {
        let dir = TempDir::new().expect("create temp dir");
        let mut state = TelemetryRepoState::load_or_default(dir.path());
        for i in 0..110 {
            state.record_analysis(i as f64);
        }
        assert_eq!(state.score_history.len(), 100);
        assert_eq!(state.nth_analysis, 110);
    }

    #[test]
    fn test_load_corrupt_returns_default() {
        let dir = TempDir::new().expect("create temp dir");
        std::fs::write(dir.path().join("telemetry_state.json"), "not json").expect("write");
        let state = TelemetryRepoState::load_or_default(dir.path());
        assert_eq!(state.nth_analysis, 0);
    }
}
