pub mod fingerprint;

use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::path::{Path, PathBuf};

pub const BASELINE_FILENAME: &str = "repotoire-baseline.json";
const BASELINE_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Baseline {
    pub version: u32,
    pub accepted_at: String,
    pub findings: Vec<BaselineEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BaselineEntry {
    pub detector: String,
    pub fingerprint: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub qualified_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub first_line_content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub accepted_by: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

impl Baseline {
    pub fn empty() -> Self {
        Baseline {
            version: BASELINE_VERSION,
            accepted_at: chrono::Utc::now().to_rfc3339(),
            findings: Vec::new(),
        }
    }

    pub fn load(repo_root: &Path) -> anyhow::Result<Self> {
        let path = repo_root.join(BASELINE_FILENAME);
        if !path.exists() {
            return Ok(Self::empty());
        }
        let content = std::fs::read_to_string(&path)?;
        let baseline: Baseline = serde_json::from_str(&content)?;
        Ok(baseline)
    }

    pub fn save(&self, repo_root: &Path) -> anyhow::Result<PathBuf> {
        let path = repo_root.join(BASELINE_FILENAME);
        let content = serde_json::to_string_pretty(self)?;
        std::fs::write(&path, &content)?;
        Ok(path)
    }

    pub fn contains(&self, fingerprint: &str) -> bool {
        self.findings.iter().any(|e| e.fingerprint == fingerprint)
    }

    pub fn get(&self, fingerprint: &str) -> Option<&BaselineEntry> {
        self.findings.iter().find(|e| e.fingerprint == fingerprint)
    }

    pub fn add(&mut self, entry: BaselineEntry) -> bool {
        if self.contains(&entry.fingerprint) {
            return false;
        }
        self.findings.push(entry);
        true
    }

    pub fn prune(&mut self, active_fingerprints: &HashSet<String>) -> usize {
        let before = self.findings.len();
        self.findings.retain(|e| active_fingerprints.contains(&e.fingerprint));
        before - self.findings.len()
    }

    pub fn fingerprints(&self) -> HashSet<String> {
        self.findings.iter().map(|e| e.fingerprint.clone()).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_load_nonexistent_returns_empty() {
        let dir = tempdir().unwrap();
        let baseline = Baseline::load(dir.path()).unwrap();
        assert!(baseline.findings.is_empty());
        assert_eq!(baseline.version, 1);
    }

    #[test]
    fn test_save_and_load_roundtrip() {
        let dir = tempdir().unwrap();
        let mut baseline = Baseline::empty();
        baseline.add(BaselineEntry {
            detector: "god-class".into(),
            fingerprint: "abc123".into(),
            qualified_name: Some("mod::MyClass".into()),
            file: None, first_line_content: None,
            accepted_by: Some("zach".into()),
            reason: Some("facade pattern".into()),
        });
        baseline.save(dir.path()).unwrap();

        let loaded = Baseline::load(dir.path()).unwrap();
        assert_eq!(loaded.findings.len(), 1);
        assert_eq!(loaded.findings[0].detector, "god-class");
        assert_eq!(loaded.findings[0].fingerprint, "abc123");
    }

    #[test]
    fn test_add_deduplicates_by_fingerprint() {
        let mut baseline = Baseline::empty();
        let entry = BaselineEntry {
            detector: "god-class".into(), fingerprint: "abc123".into(),
            qualified_name: None, file: None, first_line_content: None,
            accepted_by: None, reason: None,
        };
        assert!(baseline.add(entry.clone()));
        assert!(!baseline.add(entry));
        assert_eq!(baseline.findings.len(), 1);
    }

    #[test]
    fn test_prune_removes_stale_entries() {
        let mut baseline = Baseline::empty();
        baseline.add(BaselineEntry {
            detector: "a".into(), fingerprint: "keep".into(),
            qualified_name: None, file: None, first_line_content: None,
            accepted_by: None, reason: None,
        });
        baseline.add(BaselineEntry {
            detector: "b".into(), fingerprint: "remove".into(),
            qualified_name: None, file: None, first_line_content: None,
            accepted_by: None, reason: None,
        });
        let active: HashSet<String> = ["keep".into()].into();
        let removed = baseline.prune(&active);
        assert_eq!(removed, 1);
        assert_eq!(baseline.findings.len(), 1);
        assert_eq!(baseline.findings[0].fingerprint, "keep");
    }
}
