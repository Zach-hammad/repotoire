use std::path::PathBuf;

use super::protocol::{Command, Event, WorkerConfig};
use crate::cli::watch::engine::{WatchEngine, WatchReanalysis};
use crate::engine::AnalysisConfig;

pub struct WorkerHandler {
    engine: Option<WatchEngine>,
}

impl WorkerHandler {
    pub fn new() -> Self {
        Self { engine: None }
    }

    pub fn handle(&mut self, cmd: Command) -> Vec<Event> {
        match cmd {
            Command::Init { id, path, config } => self.handle_init(id, &path, config),
            Command::Analyze { id, files } => self.handle_analyze(id, &files),
            Command::AnalyzeAll { id } => self.handle_analyze_all(id),
            Command::Shutdown { id: _ } => {
                if let Some(engine) = &self.engine {
                    let _ = engine.save();
                }
                // Return empty — the caller will exit the loop
                vec![]
            }
        }
    }

    fn handle_init(&mut self, id: u64, path: &PathBuf, config: WorkerConfig) -> Vec<Event> {
        let analysis_config = AnalysisConfig {
            workers: config.workers,
            all_detectors: config.all_detectors,
            no_git: !path.join(".git").exists(),
            ..Default::default()
        };

        let mut engine = match WatchEngine::new(path, analysis_config) {
            Ok(e) => e,
            Err(e) => {
                return vec![Event::Error {
                    id: Some(id),
                    message: format!("Failed to initialize: {:#}", e),
                }];
            }
        };

        match engine.initial_analyze() {
            Ok(result) => {
                let score = result.score.overall;
                let grade = result.score.grade.clone();
                // Move findings out of result to avoid unnecessary clone
                let findings = result.findings;
                let elapsed_ms = 0; // TODO: track elapsed in initial_analyze
                self.engine = Some(engine);
                vec![Event::Ready {
                    id: Some(id),
                    findings,
                    score,
                    grade,
                    elapsed_ms,
                }]
            }
            Err(e) => {
                vec![Event::Error {
                    id: Some(id),
                    message: format!("Initial analysis failed: {:#}", e),
                }]
            }
        }
    }

    fn handle_analyze(&mut self, id: u64, files: &[PathBuf]) -> Vec<Event> {
        let Some(engine) = &mut self.engine else {
            return vec![Event::Error {
                id: Some(id),
                message: "Worker not initialized. Send init first.".to_string(),
            }];
        };

        match engine.reanalyze(files) {
            WatchReanalysis::Delta(delta) => {
                vec![Event::Delta {
                    id: Some(id),
                    new_findings: delta.new_findings,
                    fixed_findings: delta.fixed_findings,
                    score: delta.score,
                    grade: engine
                        .last_result()
                        .map(|r| r.score.grade.clone())
                        .unwrap_or_default(),
                    score_delta: delta.score_delta,
                    total_findings: delta.total_findings,
                    elapsed_ms: delta.elapsed.as_millis() as u64,
                }]
            }
            WatchReanalysis::Unchanged => {
                let last = engine.last_result();
                vec![Event::Unchanged {
                    id: Some(id),
                    score: last.map(|r| r.score.overall).unwrap_or(0.0),
                    grade: last.map(|r| r.score.grade.clone()).unwrap_or_default(),
                    total_findings: last.map(|r| r.findings.len()).unwrap_or(0),
                    elapsed_ms: 0,
                }]
            }
            WatchReanalysis::Error(msg) => {
                vec![Event::Error {
                    id: Some(id),
                    message: msg,
                }]
            }
        }
    }

    fn handle_analyze_all(&mut self, id: u64) -> Vec<Event> {
        let Some(engine) = &mut self.engine else {
            return vec![Event::Error {
                id: Some(id),
                message: "Worker not initialized. Send init first.".to_string(),
            }];
        };

        match engine.initial_analyze() {
            Ok(result) => {
                // Move findings out — engine already stored its own clone in last_result
                let score = result.score.overall;
                let grade = result.score.grade.clone();
                vec![Event::Ready {
                    id: Some(id),
                    findings: result.findings,
                    score,
                    grade,
                    elapsed_ms: 0,
                }]
            }
            Err(e) => {
                vec![Event::Error {
                    id: Some(id),
                    message: format!("Full re-analysis failed: {:#}", e),
                }]
            }
        }
    }

    pub fn is_shutdown(cmd: &Command) -> bool {
        matches!(cmd, Command::Shutdown { .. })
    }
}
