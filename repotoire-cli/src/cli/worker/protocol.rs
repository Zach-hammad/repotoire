//! JSONL protocol types shared between the LSP client and worker process.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::models::{Finding, Grade};

// ── Commands (LSP → Worker) ──────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "cmd", rename_all = "snake_case")]
pub enum Command {
    Init {
        id: u64,
        path: PathBuf,
        #[serde(default)]
        config: WorkerConfig,
    },
    /// Analyze changed files. The `files` list is used for delta display
    /// (showing which files triggered the re-analysis), not to scope the
    /// analysis — the engine always re-analyzes the full project.
    Analyze {
        id: u64,
        files: Vec<PathBuf>,
    },
    AnalyzeAll {
        id: u64,
    },
    Shutdown {
        id: u64,
    },
}

impl Command {
    pub fn id(&self) -> u64 {
        match self {
            Command::Init { id, .. }
            | Command::Analyze { id, .. }
            | Command::AnalyzeAll { id, .. }
            | Command::Shutdown { id, .. } => *id,
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct WorkerConfig {
    #[serde(default)]
    pub all_detectors: bool,
    #[serde(default = "default_workers")]
    pub workers: usize,
}

fn default_workers() -> usize {
    8
}

// ── Events (Worker → LSP) ───────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum Event {
    Ready {
        id: Option<u64>,
        findings: Vec<Finding>,
        score: f64,
        grade: Grade,
        elapsed_ms: u64,
    },
    Progress {
        id: Option<u64>,
        stage: String,
        done: usize,
        total: usize,
    },
    Delta {
        id: Option<u64>,
        new_findings: Vec<Finding>,
        fixed_findings: Vec<Finding>,
        score: f64,
        grade: Grade,
        score_delta: Option<f64>,
        total_findings: usize,
        elapsed_ms: u64,
    },
    Unchanged {
        id: Option<u64>,
        score: f64,
        grade: Grade,
        total_findings: usize,
        elapsed_ms: u64,
    },
    Error {
        id: Option<u64>,
        message: String,
    },
}

impl Event {
    pub fn id(&self) -> Option<u64> {
        match self {
            Event::Ready { id, .. }
            | Event::Progress { id, .. }
            | Event::Delta { id, .. }
            | Event::Unchanged { id, .. }
            | Event::Error { id, .. } => *id,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_init_roundtrip() {
        let cmd = Command::Init {
            id: 1,
            path: PathBuf::from("/tmp/project"),
            config: WorkerConfig::default(),
        };
        let json = serde_json::to_string(&cmd).unwrap();
        let parsed: Command = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.id(), 1);
    }

    #[test]
    fn command_analyze_roundtrip() {
        let cmd = Command::Analyze {
            id: 2,
            files: vec![PathBuf::from("src/main.rs")],
        };
        let json = serde_json::to_string(&cmd).unwrap();
        assert!(json.contains("\"cmd\":\"analyze\""));
        let parsed: Command = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.id(), 2);
    }

    #[test]
    fn event_ready_roundtrip() {
        let event = Event::Ready {
            id: Some(1),
            findings: vec![],
            score: 92.3,
            grade: Grade::AMinus,
            elapsed_ms: 2050,
        };
        let json = serde_json::to_string(&event).unwrap();
        assert!(json.contains("\"event\":\"ready\""));
        let parsed: Event = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.id(), Some(1));
    }

    #[test]
    fn event_delta_roundtrip() {
        let event = Event::Delta {
            id: Some(2),
            new_findings: vec![],
            fixed_findings: vec![],
            score: 93.0,
            grade: Grade::AMinus,
            score_delta: Some(0.7),
            total_findings: 85,
            elapsed_ms: 150,
        };
        let json = serde_json::to_string(&event).unwrap();
        let parsed: Event = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.id(), Some(2));
    }

    #[test]
    fn event_unsolicited_delta() {
        let json = r#"{"event":"delta","id":null,"new_findings":[],"fixed_findings":[],"score":90.0,"grade":"A-","score_delta":null,"total_findings":10,"elapsed_ms":50}"#;
        let parsed: Event = serde_json::from_str(json).unwrap();
        assert_eq!(parsed.id(), None);
    }

    #[test]
    fn command_shutdown_roundtrip() {
        let cmd = Command::Shutdown { id: 99 };
        let json = serde_json::to_string(&cmd).unwrap();
        let parsed: Command = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.id(), 99);
    }
}
