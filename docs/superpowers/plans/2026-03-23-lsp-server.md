# LSP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `repotoire lsp` — an LSP server that brings inline diagnostics, code actions, hover, and score tracking to any editor, backed by a worker process that wraps the existing `WatchEngine`.

**Architecture:** Two-process design. `repotoire lsp` (tower-lsp + tokio) speaks LSP to the editor and JSONL to a child `repotoire __worker` process. The worker wraps `WatchEngine` for incremental analysis. The JSONL protocol uses monotonic request IDs for correlation and supports unsolicited events from the filesystem watcher.

**Tech Stack:** Rust, `tower-lsp` (LSP protocol), `tokio` (async runtime), `tokio-util` (JSONL codec), `serde`/`serde_json` (serialization), existing `WatchEngine`/`WatchDelta`/`WatchFilter`.

**Spec:** `docs/superpowers/specs/2026-03-23-lsp-server-design.md`

---

## File Structure

| File | Responsibility | Status |
|------|----------------|--------|
| `repotoire-cli/src/cli/worker/mod.rs` | `__worker` entry point: JSONL stdin/stdout event loop | Create |
| `repotoire-cli/src/cli/worker/protocol.rs` | Shared Command/Event serde types | Create |
| `repotoire-cli/src/cli/worker/handler.rs` | Command dispatch: init → WatchEngine, analyze → reanalyze | Create |
| `repotoire-cli/src/cli/lsp/mod.rs` | `lsp` entry point: spawn worker, run tokio event loop | Create |
| `repotoire-cli/src/cli/lsp/server.rs` | tower-lsp `LanguageServer` trait impl | Create |
| `repotoire-cli/src/cli/lsp/diagnostics.rs` | Finding → Diagnostic mapping, diagnostic map | Create |
| `repotoire-cli/src/cli/lsp/actions.rs` | Code actions: ignore suppression, suggested fixes | Create |
| `repotoire-cli/src/cli/lsp/hover.rs` | Hover markdown rendering | Create |
| `repotoire-cli/src/cli/lsp/worker_client.rs` | Child process management: spawn, restart, JSONL I/O | Create |
| `repotoire-cli/src/cli/mod.rs` | Add `Lsp` and `Worker` command variants | Modify |
| `repotoire-cli/Cargo.toml` | Add tower-lsp, tokio, tokio-util deps | Modify |
| `repotoire-cli/tests/worker_protocol_test.rs` | Worker integration test | Create |

---

### Task 0: Add dependencies

**Files:**
- Modify: `repotoire-cli/Cargo.toml`

- [ ] **Step 1: Add tower-lsp, tokio, and tokio-util to Cargo.toml**

Add to `[dependencies]`:
```toml
tower-lsp = "0.20"
tokio = { version = "1", features = ["full"] }
tokio-util = { version = "0.7", features = ["codec"] }
```

Check latest versions on crates.io before adding. `tower-lsp 0.20` is the latest stable as of early 2026 — verify and adjust.

- [ ] **Step 2: Verify it compiles**

Run: `cargo check --no-default-features 2>&1 | tail -5`
Expected: `Finished` (new deps downloaded, no errors)

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/Cargo.toml Cargo.lock
git commit -m "chore: add tower-lsp, tokio, tokio-util dependencies for LSP server"
```

---

### Task 1: Worker protocol types

**Files:**
- Create: `repotoire-cli/src/cli/worker/protocol.rs`
- Create: `repotoire-cli/src/cli/worker/mod.rs` (just `pub mod protocol;` for now)
- Modify: `repotoire-cli/src/cli/mod.rs` (add `pub mod worker;`)

- [ ] **Step 1: Create `protocol.rs` with Command and Event types + tests**

```rust
//! JSONL protocol types shared between the LSP client and worker process.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::models::Finding;

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
        grade: String,
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
        grade: String,
        score_delta: Option<f64>,
        total_findings: usize,
        elapsed_ms: u64,
    },
    Unchanged {
        id: Option<u64>,
        score: f64,
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
            grade: "A-".to_string(),
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
            grade: "A-".to_string(),
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
```

- [ ] **Step 2: Create `worker/mod.rs`**

```rust
pub mod protocol;
```

- [ ] **Step 3: Add `pub mod worker;` to `cli/mod.rs`**

- [ ] **Step 4: Run tests**

Run: `cargo test --no-default-features --lib cli::worker::protocol 2>&1 | tail -10`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/cli/worker/ repotoire-cli/src/cli/mod.rs
git commit -m "feat(lsp): add worker protocol types with JSONL serde"
```

---

### Task 2: Worker handler

**Files:**
- Create: `repotoire-cli/src/cli/worker/handler.rs`
- Modify: `repotoire-cli/src/cli/worker/mod.rs`

- [ ] **Step 1: Create `handler.rs`**

The handler receives `Command`s and produces `Event`s using `WatchEngine`:

```rust
use std::path::PathBuf;

use anyhow::Result;

use super::protocol::{Command, Event, WorkerConfig};
use crate::cli::watch::delta::compute_delta;
use crate::cli::watch::engine::WatchEngine;
use crate::cli::watch::engine::WatchReanalysis;
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
            Command::Shutdown { id } => {
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
```

- [ ] **Step 2: Add `pub mod handler;` to `worker/mod.rs`**

- [ ] **Step 3: Verify it compiles**

Run: `cargo check --no-default-features 2>&1 | tail -5`
Expected: `Finished`

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/worker/handler.rs repotoire-cli/src/cli/worker/mod.rs
git commit -m "feat(lsp): add worker handler dispatching commands to WatchEngine"
```

---

### Task 3: Worker entry point (JSONL loop)

**Files:**
- Modify: `repotoire-cli/src/cli/worker/mod.rs`

- [ ] **Step 1: Implement the `__worker` JSONL stdin/stdout loop**

```rust
pub mod handler;
pub mod protocol;

use std::io::{self, BufRead, Write};

use anyhow::Result;

use self::handler::WorkerHandler;
use self::protocol::{Command, Event};

/// Entry point for `repotoire __worker`.
/// Reads JSONL commands from stdin, writes JSONL events to stdout.
/// Stderr is for logging only.
pub fn run() -> Result<()> {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut handler = WorkerHandler::new();

    for line in stdin.lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }

        let cmd: Command = match serde_json::from_str(&line) {
            Ok(cmd) => cmd,
            Err(e) => {
                let err = Event::Error {
                    id: None,
                    message: format!("Invalid command: {}", e),
                };
                let mut out = stdout.lock();
                serde_json::to_writer(&mut out, &err)?;
                out.write_all(b"\n")?;
                out.flush()?;
                continue;
            }
        };

        let is_shutdown = WorkerHandler::is_shutdown(&cmd);
        let events = handler.handle(cmd);

        let mut out = stdout.lock();
        for event in events {
            serde_json::to_writer(&mut out, &event)?;
            out.write_all(b"\n")?;
        }
        out.flush()?;

        if is_shutdown {
            break;
        }
    }

    Ok(())
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cargo check --no-default-features 2>&1 | tail -5`

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/src/cli/worker/mod.rs
git commit -m "feat(lsp): add __worker JSONL stdin/stdout event loop"
```

---

### Task 4: CLI integration (add Lsp and Worker commands)

**Files:**
- Modify: `repotoire-cli/src/cli/mod.rs`

- [ ] **Step 1: Add `Lsp` and `Worker` command variants**

Add to the `Commands` enum (after existing variants):

```rust
    /// Start the LSP server (stdio transport, for editor integration)
    Lsp,

    /// Internal: analysis worker process (not user-facing)
    #[command(name = "__worker", hide = true)]
    Worker,
```

- [ ] **Step 2: Add dispatch arms**

In the main `match` block, add:

```rust
Some(Commands::Lsp) => {
    // TODO: implement in Task 6
    anyhow::bail!("LSP server not yet implemented")
}
Some(Commands::Worker) => {
    crate::cli::worker::run()
}
```

- [ ] **Step 3: Add `pub mod lsp;` to cli/mod.rs**

Create `repotoire-cli/src/cli/lsp/mod.rs` with placeholder:

```rust
// LSP server — implemented in subsequent tasks
```

- [ ] **Step 4: Add telemetry extraction arms**

```rust
Some(Commands::Lsp) => ("lsp".into(), None),
Some(Commands::Worker) => ("worker".into(), None),
```

- [ ] **Step 5: Verify it compiles and test the worker**

Run: `cargo check --no-default-features 2>&1 | tail -5`

Test the worker manually:
```bash
echo '{"cmd":"shutdown","id":1}' | cargo run --no-default-features -- __worker 2>&1
```
Expected: Worker starts, receives shutdown, exits cleanly.

- [ ] **Step 6: Commit**

```bash
git add repotoire-cli/src/cli/mod.rs repotoire-cli/src/cli/lsp/mod.rs
git commit -m "feat(lsp): add Lsp and Worker CLI commands"
```

---

### Task 5: Worker integration test

**Files:**
- Create: `repotoire-cli/tests/worker_protocol_test.rs`

- [ ] **Step 1: Write integration tests**

```rust
//! Integration tests for the __worker process.
//! Spawns `repotoire __worker` and communicates via JSONL stdin/stdout.

use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};
use std::time::Duration;

struct WorkerProcess {
    child: std::process::Child,
    reader: BufReader<std::process::ChildStdout>,
}

fn spawn_worker() -> WorkerProcess {
    let mut child = Command::new(env!("CARGO_BIN_EXE_repotoire"))
        .arg("__worker")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("Failed to spawn worker");
    let stdout = child.stdout.take().unwrap();
    let reader = BufReader::new(stdout);
    WorkerProcess { child, reader }
}

fn send(proc: &mut WorkerProcess, cmd: &str) {
    let stdin = proc.child.stdin.as_mut().unwrap();
    stdin.write_all(cmd.as_bytes()).unwrap();
    stdin.write_all(b"\n").unwrap();
    stdin.flush().unwrap();
}

fn recv(proc: &mut WorkerProcess) -> String {
    let mut line = String::new();
    proc.reader.read_line(&mut line).unwrap();
    line
}

#[test]
fn worker_init_and_shutdown() {
    let tmp = tempfile::TempDir::new().unwrap();
    std::fs::write(tmp.path().join("main.py"), "def hello(): pass\n").unwrap();

    let mut proc = spawn_worker();

    // Init
    let init_cmd = format!(
        r#"{{"cmd":"init","id":1,"path":"{}","config":{{}}}}"#,
        tmp.path().display()
    );
    send(&mut proc, &init_cmd);
    let response = recv(&mut proc);
    let event: serde_json::Value = serde_json::from_str(&response).unwrap();
    assert_eq!(event["event"], "ready");
    assert_eq!(event["id"], 1);
    assert!(event["score"].as_f64().unwrap() > 0.0);

    // Shutdown
    send(&mut proc, r#"{"cmd":"shutdown","id":2}"#);
    let status = proc.child.wait().unwrap();
    assert!(status.success());
}

#[test]
fn worker_analyze_after_file_change() {
    let tmp = tempfile::TempDir::new().unwrap();
    std::fs::write(tmp.path().join("main.py"), "def hello(): pass\n").unwrap();

    let mut proc = spawn_worker();

    // Init
    let init_cmd = format!(
        r#"{{"cmd":"init","id":1,"path":"{}","config":{{}}}}"#,
        tmp.path().display()
    );
    send(&mut proc, &init_cmd);
    let _ready = recv(&mut proc); // ready

    // Write a new file
    let new_file = tmp.path().join("config.py");
    std::fs::write(&new_file, r#"SECRET = "AKIAIOSFODNN7EXAMPLE""#).unwrap();

    // Analyze
    let analyze_cmd = format!(
        r#"{{"cmd":"analyze","id":2,"files":["{}"]}}"#,
        new_file.display()
    );
    send(&mut proc, &analyze_cmd);
    let response = recv(&mut proc);
    let event: serde_json::Value = serde_json::from_str(&response).unwrap();
    // Should be delta, unchanged, or error — not a crash
    assert!(
        event["event"] == "delta"
            || event["event"] == "unchanged"
            || event["event"] == "error"
    );
    assert_eq!(event["id"], 2);

    // Shutdown
    send(&mut proc, r#"{"cmd":"shutdown","id":3}"#);
    proc.child.wait().unwrap();
}

#[test]
fn worker_invalid_command() {
    let mut proc = spawn_worker();

    send(&mut proc, "this is not json");
    let response = recv(&mut proc);
    let event: serde_json::Value = serde_json::from_str(&response).unwrap();
    assert_eq!(event["event"], "error");
    assert!(event["message"].as_str().unwrap().contains("Invalid command"));

    // Worker should still be alive
    send(&mut proc, r#"{"cmd":"shutdown","id":1}"#);
    let status = proc.child.wait().unwrap();
    assert!(status.success());
}
```

Note: `env!("CARGO_BIN_EXE_repotoire")` resolves to the test binary path. If the binary name differs, check `Cargo.toml`'s `[[bin]]` section and adjust.

- [ ] **Step 2: Run tests**

Run: `cargo test --no-default-features --test worker_protocol_test 2>&1 | tail -15`
Expected: 3 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add repotoire-cli/tests/worker_protocol_test.rs
git commit -m "test(lsp): add worker process integration tests"
```

---

### Task 6: LSP diagnostics module

**Files:**
- Create: `repotoire-cli/src/cli/lsp/diagnostics.rs`

- [ ] **Step 1: Create `diagnostics.rs` with Finding → Diagnostic mapping + tests**

```rust
use std::collections::HashMap;
use std::path::PathBuf;

use tower_lsp::lsp_types::{
    Diagnostic, DiagnosticSeverity, NumberOrString, Position, Range, Url,
};

use crate::models::{Finding, Severity};

/// Convert a Finding severity to LSP DiagnosticSeverity.
pub fn to_lsp_severity(severity: Severity) -> DiagnosticSeverity {
    match severity {
        Severity::Critical => DiagnosticSeverity::ERROR,
        Severity::High => DiagnosticSeverity::WARNING,
        Severity::Medium => DiagnosticSeverity::WARNING,
        Severity::Low => DiagnosticSeverity::INFORMATION,
        Severity::Info => DiagnosticSeverity::HINT,
    }
}

/// Convert a Finding to an LSP Diagnostic.
pub fn finding_to_diagnostic(finding: &Finding) -> Diagnostic {
    // LSP lines are 0-indexed, Finding lines are 1-indexed
    let start_1 = finding.line_start.unwrap_or(1);
    let end_1 = finding.line_end.unwrap_or(start_1);
    let start_line = start_1.saturating_sub(1);
    let end_line = end_1; // end is exclusive in LSP, so 1-indexed end == 0-indexed exclusive end

    Diagnostic {
        range: Range {
            start: Position::new(start_line, 0),
            end: Position::new(end_line, 0),
        },
        severity: Some(to_lsp_severity(finding.severity)),
        code: Some(NumberOrString::String(finding.id.clone())),
        source: Some("repotoire".to_string()),
        message: finding.title.clone(),
        ..Default::default()
    }
}

/// Convert a file path to a URI.
pub fn path_to_uri(path: &PathBuf) -> Option<Url> {
    Url::from_file_path(path).ok()
}

/// Manages the diagnostic state: maps file URIs to their current diagnostics.
pub struct DiagnosticMap {
    map: HashMap<Url, Vec<(Finding, Diagnostic)>>,
}

impl DiagnosticMap {
    pub fn new() -> Self {
        Self {
            map: HashMap::new(),
        }
    }

    /// Set all diagnostics from a full findings list (used on `ready` event).
    pub fn set_all(&mut self, findings: &[Finding]) {
        self.map.clear();
        for finding in findings {
            if let Some(path) = finding.affected_files.first() {
                if let Some(uri) = path_to_uri(path) {
                    let diag = finding_to_diagnostic(finding);
                    self.map
                        .entry(uri)
                        .or_default()
                        .push((finding.clone(), diag));
                }
            }
        }
    }

    /// Fingerprint a finding for matching — same key as compute_delta uses.
    fn fingerprint(f: &Finding) -> (String, Option<std::path::PathBuf>, Option<u32>) {
        (
            f.detector.clone(),
            f.affected_files.first().cloned(),
            f.line_start,
        )
    }

    /// Apply a delta: remove fixed findings, add new findings.
    /// Returns the set of URIs that changed (need re-publishing).
    pub fn apply_delta(
        &mut self,
        new_findings: &[Finding],
        fixed_findings: &[Finding],
    ) -> Vec<Url> {
        let mut changed_uris = Vec::new();

        // Remove fixed findings (match by fingerprint, not id — id can be empty)
        for fixed in fixed_findings {
            if let Some(path) = fixed.affected_files.first() {
                if let Some(uri) = path_to_uri(path) {
                    let fixed_fp = Self::fingerprint(fixed);
                    if let Some(entries) = self.map.get_mut(&uri) {
                        let before = entries.len();
                        entries.retain(|(f, _)| Self::fingerprint(f) != fixed_fp);
                        if entries.len() != before {
                            changed_uris.push(uri.clone());
                        }
                    }
                    // Clean up empty entries to prevent memory leak
                    if self.map.get(&uri).map(|e| e.is_empty()).unwrap_or(false) {
                        self.map.remove(&uri);
                    }
                }
            }
        }

        // Add new findings
        for finding in new_findings {
            if let Some(path) = finding.affected_files.first() {
                if let Some(uri) = path_to_uri(path) {
                    let diag = finding_to_diagnostic(finding);
                    self.map
                        .entry(uri.clone())
                        .or_default()
                        .push((finding.clone(), diag));
                    if !changed_uris.contains(&uri) {
                        changed_uris.push(uri);
                    }
                }
            }
        }

        changed_uris
    }

    /// Get diagnostics for a specific URI.
    pub fn get_diagnostics(&self, uri: &Url) -> Vec<Diagnostic> {
        self.map
            .get(uri)
            .map(|entries| entries.iter().map(|(_, d)| d.clone()).collect())
            .unwrap_or_default()
    }

    /// Get all URIs that have diagnostics.
    pub fn all_uris(&self) -> Vec<Url> {
        self.map.keys().cloned().collect()
    }

    /// Get the finding at a specific URI and line (for hover/code actions).
    pub fn find_at(&self, uri: &Url, line: u32) -> Vec<&Finding> {
        self.map
            .get(uri)
            .map(|entries| {
                entries
                    .iter()
                    .filter(|(f, _)| {
                        let start = f.line_start.unwrap_or(0);
                        let end = f.line_end.unwrap_or(start);
                        line >= start.saturating_sub(1) && line <= end
                    })
                    .map(|(f, _)| f)
                    .collect()
            })
            .unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_finding(id: &str, detector: &str, file: &str, line: u32, severity: Severity) -> Finding {
        Finding {
            id: id.to_string(),
            detector: detector.to_string(),
            affected_files: vec![PathBuf::from(file)],
            line_start: Some(line),
            severity,
            title: format!("{} issue", detector),
            ..Default::default()
        }
    }

    #[test]
    fn severity_mapping() {
        assert_eq!(to_lsp_severity(Severity::Critical), DiagnosticSeverity::ERROR);
        assert_eq!(to_lsp_severity(Severity::High), DiagnosticSeverity::WARNING);
        assert_eq!(to_lsp_severity(Severity::Medium), DiagnosticSeverity::WARNING);
        assert_eq!(to_lsp_severity(Severity::Low), DiagnosticSeverity::INFORMATION);
        assert_eq!(to_lsp_severity(Severity::Info), DiagnosticSeverity::HINT);
    }

    #[test]
    fn finding_to_diagnostic_mapping() {
        let f = make_finding("f1", "XSS", "/tmp/a.rs", 10, Severity::High);
        let d = finding_to_diagnostic(&f);
        assert_eq!(d.range.start.line, 9); // 0-indexed
        assert_eq!(d.severity, Some(DiagnosticSeverity::WARNING));
        assert_eq!(d.source, Some("repotoire".to_string()));
        assert_eq!(d.message, "XSS issue");
    }

    #[test]
    fn finding_no_line_defaults_to_zero() {
        let mut f = make_finding("f1", "Arch", "/tmp/a.rs", 1, Severity::Medium);
        f.line_start = None;
        let d = finding_to_diagnostic(&f);
        assert_eq!(d.range.start.line, 0);
    }

    #[test]
    fn diagnostic_map_set_all() {
        let mut map = DiagnosticMap::new();
        let findings = vec![
            make_finding("f1", "XSS", "/tmp/a.rs", 10, Severity::High),
            make_finding("f2", "SQLi", "/tmp/b.rs", 20, Severity::Critical),
        ];
        map.set_all(&findings);
        assert_eq!(map.all_uris().len(), 2);
    }

    #[test]
    fn diagnostic_map_apply_delta() {
        let mut map = DiagnosticMap::new();
        let initial = vec![make_finding("f1", "XSS", "/tmp/a.rs", 10, Severity::High)];
        map.set_all(&initial);

        let new = vec![make_finding("f2", "SQLi", "/tmp/a.rs", 20, Severity::Critical)];
        let fixed = vec![make_finding("f1", "XSS", "/tmp/a.rs", 10, Severity::High)];
        let changed = map.apply_delta(&new, &fixed);

        let uri = Url::from_file_path("/tmp/a.rs").unwrap();
        assert!(changed.contains(&uri));
        let diags = map.get_diagnostics(&uri);
        assert_eq!(diags.len(), 1);
        assert_eq!(diags[0].message, "SQLi issue");
    }
}
```

- [ ] **Step 2: Add `pub mod diagnostics;` to `lsp/mod.rs`**

- [ ] **Step 3: Run tests**

Run: `cargo test --no-default-features --lib cli::lsp::diagnostics 2>&1 | tail -10`
Expected: 5 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/lsp/diagnostics.rs repotoire-cli/src/cli/lsp/mod.rs
git commit -m "feat(lsp): add diagnostics module with Finding → Diagnostic mapping"
```

---

### Task 7: Hover module

**Files:**
- Create: `repotoire-cli/src/cli/lsp/hover.rs`

- [ ] **Step 1: Create `hover.rs` with markdown rendering + tests**

```rust
use crate::models::Finding;

/// Render a rich markdown hover for a finding.
/// Returns None if the finding has no extra context beyond title/description
/// (since the diagnostic tooltip already shows the title).
pub fn render_hover(finding: &Finding) -> Option<String> {
    let mut parts: Vec<String> = Vec::new();

    if !finding.description.is_empty() {
        parts.push(finding.description.clone());
    }

    if let Some(why) = &finding.why_it_matters {
        parts.push(format!("**Why it matters:** {}", why));
    }

    if let Some(fix) = &finding.suggested_fix {
        parts.push(format!("**Suggested fix:** {}", fix));
    }

    let mut footer = Vec::new();
    if let Some(cwe) = &finding.cwe_id {
        footer.push(format!("**CWE:** {}", cwe));
    }
    if let Some(conf) = finding.confidence {
        footer.push(format!("**Confidence:** {:.2}", conf));
    }
    if let Some(effort) = &finding.estimated_effort {
        footer.push(format!("**Effort:** {}", effort));
    }
    if !footer.is_empty() {
        parts.push(footer.join(" · "));
    }

    if parts.is_empty() {
        None
    } else {
        Some(parts.join("\n\n"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Severity;
    use std::path::PathBuf;

    fn make_finding() -> Finding {
        Finding {
            id: "f1".to_string(),
            detector: "SQLi".to_string(),
            severity: Severity::Critical,
            title: "SQL Injection".to_string(),
            description: "User input flows into SQL query.".to_string(),
            affected_files: vec![PathBuf::from("/tmp/a.rs")],
            line_start: Some(10),
            why_it_matters: Some("Attacker can read/modify data.".to_string()),
            suggested_fix: Some("Use parameterized queries.".to_string()),
            cwe_id: Some("CWE-89".to_string()),
            confidence: Some(0.92),
            estimated_effort: Some("Low".to_string()),
            ..Default::default()
        }
    }

    #[test]
    fn hover_full_finding() {
        let f = make_finding();
        let md = render_hover(&f).unwrap();
        assert!(md.contains("User input flows into SQL query."));
        assert!(md.contains("**Why it matters:**"));
        assert!(md.contains("**Suggested fix:**"));
        assert!(md.contains("CWE-89"));
        assert!(md.contains("0.92"));
        assert!(md.contains("Low"));
    }

    #[test]
    fn hover_minimal_finding() {
        let f = Finding {
            title: "Something".to_string(),
            ..Default::default()
        };
        // No description, no extra fields → None
        assert!(render_hover(&f).is_none());
    }

    #[test]
    fn hover_partial_fields() {
        let f = Finding {
            description: "Some issue.".to_string(),
            suggested_fix: Some("Fix it.".to_string()),
            ..Default::default()
        };
        let md = render_hover(&f).unwrap();
        assert!(md.contains("Some issue."));
        assert!(md.contains("**Suggested fix:** Fix it."));
        assert!(!md.contains("CWE"));
        assert!(!md.contains("Confidence"));
    }
}
```

- [ ] **Step 2: Add `pub mod hover;` to `lsp/mod.rs`**

- [ ] **Step 3: Run tests**

Run: `cargo test --no-default-features --lib cli::lsp::hover 2>&1 | tail -10`
Expected: 3 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/lsp/hover.rs repotoire-cli/src/cli/lsp/mod.rs
git commit -m "feat(lsp): add hover module with markdown rendering"
```

---

### Task 8: Code actions module

**Files:**
- Create: `repotoire-cli/src/cli/lsp/actions.rs`

- [ ] **Step 1: Create `actions.rs` with ignore and fix actions + tests**

```rust
use std::collections::HashMap;

use tower_lsp::lsp_types::{
    CodeAction, CodeActionKind, Position, Range, TextEdit, Url, WorkspaceEdit,
};

use crate::models::Finding;

/// Comment prefix for a given file extension.
fn comment_prefix(uri: &Url) -> &'static str {
    let path = uri.path();
    if path.ends_with(".py") || path.ends_with(".rb") {
        "#"
    } else if path.ends_with(".rs")
        || path.ends_with(".ts")
        || path.ends_with(".tsx")
        || path.ends_with(".js")
        || path.ends_with(".jsx")
        || path.ends_with(".go")
        || path.ends_with(".java")
        || path.ends_with(".c")
        || path.ends_with(".cpp")
        || path.ends_with(".cs")
        || path.ends_with(".swift")
        || path.ends_with(".kt")
    {
        "//"
    } else if path.ends_with(".php") {
        "//"
    } else {
        "//" // default
    }
}

/// Generate code actions for a finding at a given URI.
pub fn actions_for_finding(finding: &Finding, uri: &Url) -> Vec<CodeAction> {
    let mut actions = Vec::new();
    let line = finding.line_start.unwrap_or(1).saturating_sub(1); // 0-indexed

    // 1. Ignore suppression
    let prefix = comment_prefix(uri);
    let ignore_text = format!(
        "{} repotoire:ignore[{}]\n",
        prefix,
        finding.detector.to_lowercase().replace("detector", "")
    );

    let ignore_edit = TextEdit {
        range: Range {
            start: Position::new(line, 0),
            end: Position::new(line, 0),
        },
        new_text: ignore_text,
    };

    actions.push(CodeAction {
        title: format!("Ignore: {} (repotoire)", finding.detector),
        kind: Some(CodeActionKind::QUICKFIX),
        edit: Some(WorkspaceEdit {
            changes: Some(HashMap::from([(uri.clone(), vec![ignore_edit])])),
            ..Default::default()
        }),
        ..Default::default()
    });

    // 2. Suggested fix (if available)
    if let Some(fix) = &finding.suggested_fix {
        actions.push(CodeAction {
            title: format!("Fix: {}", finding.title),
            kind: Some(CodeActionKind::QUICKFIX),
            // Show fix description as a comment above the line
            edit: Some(WorkspaceEdit {
                changes: Some(HashMap::from([(
                    uri.clone(),
                    vec![TextEdit {
                        range: Range {
                            start: Position::new(line, 0),
                            end: Position::new(line, 0),
                        },
                        new_text: format!("{} FIX: {}\n", comment_prefix(uri), fix),
                    }],
                )])),
                ..Default::default()
            }),
            ..Default::default()
        });
    }

    actions
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Severity;
    use std::path::PathBuf;

    #[test]
    fn ignore_action_python() {
        let f = Finding {
            detector: "SQLInjection".to_string(),
            line_start: Some(10),
            affected_files: vec![PathBuf::from("/tmp/app.py")],
            title: "SQL Injection".to_string(),
            severity: Severity::Critical,
            ..Default::default()
        };
        let uri = Url::from_file_path("/tmp/app.py").unwrap();
        let actions = actions_for_finding(&f, &uri);
        assert_eq!(actions.len(), 1); // no suggested_fix
        let edit = &actions[0].edit.as_ref().unwrap().changes.as_ref().unwrap()[&uri][0];
        assert!(edit.new_text.starts_with("# repotoire:ignore"));
    }

    #[test]
    fn ignore_action_rust() {
        let f = Finding {
            detector: "UnwrapDetector".to_string(),
            line_start: Some(5),
            affected_files: vec![PathBuf::from("/tmp/main.rs")],
            title: "Unwrap".to_string(),
            severity: Severity::Medium,
            ..Default::default()
        };
        let uri = Url::from_file_path("/tmp/main.rs").unwrap();
        let actions = actions_for_finding(&f, &uri);
        let edit = &actions[0].edit.as_ref().unwrap().changes.as_ref().unwrap()[&uri][0];
        assert!(edit.new_text.starts_with("// repotoire:ignore"));
    }

    #[test]
    fn suggested_fix_action() {
        let f = Finding {
            detector: "SQLi".to_string(),
            line_start: Some(10),
            affected_files: vec![PathBuf::from("/tmp/app.py")],
            title: "SQL Injection".to_string(),
            suggested_fix: Some("Use parameterized queries".to_string()),
            severity: Severity::Critical,
            ..Default::default()
        };
        let uri = Url::from_file_path("/tmp/app.py").unwrap();
        let actions = actions_for_finding(&f, &uri);
        assert_eq!(actions.len(), 2); // ignore + fix
        assert!(actions[1].title.contains("Fix:"));
    }
}
```

- [ ] **Step 2: Add `pub mod actions;` to `lsp/mod.rs`**

- [ ] **Step 3: Run tests**

Run: `cargo test --no-default-features --lib cli::lsp::actions 2>&1 | tail -10`
Expected: 3 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/lsp/actions.rs repotoire-cli/src/cli/lsp/mod.rs
git commit -m "feat(lsp): add code actions module (ignore suppression, suggested fixes)"
```

---

### Task 9: Worker client (child process management)

**Files:**
- Create: `repotoire-cli/src/cli/lsp/worker_client.rs`

- [ ] **Step 1: Create `worker_client.rs`**

This module spawns `repotoire __worker`, sends JSONL commands, reads JSONL events, and handles crash/restart:

```rust
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};

use anyhow::Result;
use tokio::sync::mpsc;

use crate::cli::worker::protocol::{self, Event, WorkerConfig};

pub struct WorkerClient {
    child: Option<Child>,
    reader: Option<BufReader<std::process::ChildStdout>>,
    repo_path: PathBuf,
    config: WorkerConfig,
    next_id: u64,
    crash_count: u32,
    last_crash: Option<std::time::Instant>,
}

impl WorkerClient {
    pub fn new(repo_path: PathBuf, config: WorkerConfig) -> Self {
        Self {
            child: None,
            reader: None,
            repo_path,
            config,
            next_id: 1,
            crash_count: 0,
            last_crash: None,
        }
    }

    /// Spawn the worker child process.
    pub fn spawn(&mut self) -> Result<()> {
        let binary = std::env::current_exe()?;
        let mut child = Command::new(binary)
            .arg("__worker")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit()) // worker logs go to parent's stderr
            .spawn()?;
        // Take stdout and wrap in BufReader — stored for the lifetime of the child
        let stdout = child.stdout.take().ok_or_else(|| anyhow::anyhow!("No stdout"))?;
        self.reader = Some(BufReader::new(stdout));
        self.child = Some(child);
        Ok(())
    }

    /// Send the init command and return the next request ID.
    pub fn send_init(&mut self) -> Result<u64> {
        let id = self.next_id();
        let cmd = protocol::Command::Init {
            id,
            path: self.repo_path.clone(),
            config: self.config.clone(),
        };
        self.send_command(&cmd)?;
        Ok(id)
    }

    /// Send an analyze command for specific files.
    pub fn send_analyze(&mut self, files: Vec<PathBuf>) -> Result<u64> {
        let id = self.next_id();
        let cmd = protocol::Command::Analyze { id, files };
        self.send_command(&cmd)?;
        Ok(id)
    }

    /// Send shutdown command.
    pub fn send_shutdown(&mut self) -> Result<()> {
        let id = self.next_id();
        let cmd = protocol::Command::Shutdown { id };
        self.send_command(&cmd)?;
        Ok(())
    }

    /// Read one event from the worker's stdout.
    /// Returns None if the worker has exited (broken pipe).
    /// Uses the stored BufReader to avoid losing buffered data between calls.
    pub fn read_event(&mut self) -> Option<Event> {
        let reader = self.reader.as_mut()?;
        let mut line = String::new();
        match reader.read_line(&mut line) {
            Ok(0) => None, // EOF — worker exited
            Ok(_) => serde_json::from_str(&line).ok(),
            Err(_) => None,
        }
    }

    /// Check if the worker should be restarted after a crash.
    /// Returns true if under the retry limit (3 crashes in 60s).
    pub fn should_restart(&mut self) -> bool {
        let now = std::time::Instant::now();
        if let Some(last) = self.last_crash {
            if now.duration_since(last).as_secs() > 60 {
                self.crash_count = 0; // reset if >60s since last crash
            }
        }
        self.crash_count += 1;
        self.last_crash = Some(now);
        self.crash_count <= 3
    }

    /// Kill the worker process if running.
    pub fn kill(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    fn next_id(&mut self) -> u64 {
        let id = self.next_id;
        self.next_id += 1;
        id
    }

    fn send_command(&mut self, cmd: &protocol::Command) -> Result<()> {
        let child = self.child.as_mut().ok_or_else(|| anyhow::anyhow!("Worker not running"))?;
        let stdin = child.stdin.as_mut().ok_or_else(|| anyhow::anyhow!("No stdin"))?;
        serde_json::to_writer(&mut *stdin, cmd)?;
        stdin.write_all(b"\n")?;
        stdin.flush()?;
        Ok(())
    }
}

impl Drop for WorkerClient {
    fn drop(&mut self) {
        self.kill();
    }
}
```

**Important: All sync I/O on WorkerClient must be wrapped in `tokio::task::spawn_blocking`.** Both `read_event()` AND `send_command()` are blocking I/O. The LSP server (Task 10) must never call these directly from async handlers — always use `spawn_blocking`. This includes `did_save`'s call to `send_analyze()`. Alternatively, the implementer can rewrite `WorkerClient` to use `tokio::process::Command` with async stdin/stdout, avoiding `spawn_blocking` entirely. Either approach works — choose based on complexity.

- [ ] **Step 2: Add `pub mod worker_client;` to `lsp/mod.rs`**

- [ ] **Step 3: Verify it compiles**

Run: `cargo check --no-default-features 2>&1 | tail -5`

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/lsp/worker_client.rs repotoire-cli/src/cli/lsp/mod.rs
git commit -m "feat(lsp): add worker client for child process management"
```

---

### Task 10: LSP server (tower-lsp integration)

**Files:**
- Create: `repotoire-cli/src/cli/lsp/server.rs`
- Modify: `repotoire-cli/src/cli/lsp/mod.rs`

This is the largest task — it wires everything together. The implementer should read the spec's "LSP Server Lifecycle" and "Event Loop Architecture" sections carefully.

- [ ] **Step 1: Create `server.rs` with `LanguageServer` trait impl**

The server holds shared state behind `Arc<Mutex<>>` (tower-lsp requires `&self` on handlers). It implements:
- `initialize` — declare capabilities
- `did_save` — add file to debounce buffer
- `hover` — lookup finding at position, render markdown
- `code_action` — lookup findings at range, generate actions
- `shutdown` — send shutdown to worker

Key patterns:
- State is `Arc<tokio::sync::Mutex<ServerState>>` where `ServerState` holds `DiagnosticMap`, `WorkerClient`, `latest_request_id`, pending files set
- `did_save` adds the file to a pending set and resets a debounce timer (200ms `tokio::time::sleep`)
- A background task reads worker events and publishes diagnostics via `client.publish_diagnostics()`
- Stale responses (id < latest_request_id) are discarded

This file will be 200-300 lines. The implementer should:
1. Start with the `initialize` handler returning capabilities
2. Wire up `did_save` → send analyze to worker
3. Wire up worker event reader → publish diagnostics
4. Add hover and code_action handlers
5. Add the `repotoire/scoreUpdate` custom notification
6. Add debounce logic
7. Add stale response filtering

Given the complexity, provide the skeleton and let the implementer fill in handlers:

```rust
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::Arc;

use tokio::sync::Mutex;
use tower_lsp::jsonrpc::Result;
use tower_lsp::lsp_types::*;
use tower_lsp::{Client, LanguageServer};

use super::actions::actions_for_finding;
use super::diagnostics::DiagnosticMap;
use super::hover::render_hover;
use super::worker_client::WorkerClient;
use crate::cli::worker::protocol::Event;

pub struct Backend {
    client: Client,
    /// Read-heavy state (hover, code_action read; ready/delta write).
    /// Use RwLock to avoid blocking reads during diagnostic publishing.
    diagnostics: Arc<tokio::sync::RwLock<DiagnosticMap>>,
    /// Write-heavy state (worker communication, debounce).
    /// Separate from diagnostics to avoid contention.
    worker_state: Arc<Mutex<WorkerState>>,
}

struct WorkerState {
    worker: WorkerClient,
    latest_request_id: u64,
    pending_files: HashSet<PathBuf>,
    workspace_root: Option<PathBuf>,
}

impl Backend {
    pub fn new(client: Client, worker: WorkerClient) -> Self {
        Self {
            client,
            diagnostics: Arc::new(tokio::sync::RwLock::new(DiagnosticMap::new())),
            worker_state: Arc::new(Mutex::new(WorkerState {
                worker,
                latest_request_id: 0,
                pending_files: HashSet::new(),
                workspace_root: None,
            })),
        }
    }

    /// Start the background worker event reader.
    pub fn start_worker_reader(&self) {
        let client = self.client.clone();
        let diagnostics = self.diagnostics.clone();
        let worker_state = self.worker_state.clone();
        tokio::spawn(async move {
            // Read events from worker in a blocking thread
            // and publish diagnostics / send notifications.
            //
            // Pattern: use spawn_blocking for the sync read_event() call,
            // then process the event on the async side.
            //
            // For diagnostic publishing: acquire the RwLock as write,
            // update the map, collect the URIs+diagnostics to publish,
            // DROP the lock, THEN publish (publish is async and should
            // not hold the lock).
            //
            // For stale filtering: check event.id() against
            // worker_state.lock().latest_request_id.
        });
    }

    async fn publish_all_diagnostics(&self) {
        // Collect diagnostics under read lock, then publish outside the lock
        let to_publish: Vec<(Url, Vec<Diagnostic>)> = {
            let diag_map = self.diagnostics.read().await;
            diag_map
                .all_uris()
                .into_iter()
                .map(|uri| {
                    let diags = diag_map.get_diagnostics(&uri);
                    (uri, diags)
                })
                .collect()
        };
        // Lock is dropped here — publish without holding it
        for (uri, diags) in to_publish {
            self.client.publish_diagnostics(uri, diags, None).await;
        }
    }

    async fn send_score_update(&self, score: f64, grade: &str, delta: Option<f64>, findings: usize) {
        let params = serde_json::json!({
            "score": score,
            "grade": grade,
            "delta": delta,
            "findings": findings,
        });
        self.client
            .send_notification::<ScoreUpdateNotification>(params)
            .await;
    }
}

// Custom notification type for score updates
pub enum ScoreUpdateNotification {}
impl tower_lsp::lsp_types::notification::Notification for ScoreUpdateNotification {
    type Params = serde_json::Value;
    const METHOD: &'static str = "repotoire/scoreUpdate";
}

#[tower_lsp::async_trait]
impl LanguageServer for Backend {
    async fn initialize(&self, params: InitializeParams) -> Result<InitializeResult> {
        // Store workspace root
        if let Some(root) = params.root_uri {
            if let Ok(path) = root.to_file_path() {
                self.worker_state.lock().await.workspace_root = Some(path);
            }
        }

        Ok(InitializeResult {
            capabilities: ServerCapabilities {
                text_document_sync: Some(TextDocumentSyncCapability::Options(
                    TextDocumentSyncOptions {
                        save: Some(TextDocumentSyncSaveOptions::SaveOptions(SaveOptions {
                            include_text: Some(false),
                        })),
                        ..Default::default()
                    },
                )),
                hover_provider: Some(HoverProviderCapability::Simple(true)),
                code_action_provider: Some(CodeActionProviderCapability::Simple(true)),
                ..Default::default()
            },
            ..Default::default()
        })
    }

    async fn initialized(&self, _: InitializedParams) {
        // Spawn worker and send init
        let mut state = self.worker_state.lock().await;
        if let Some(_root) = &state.workspace_root {
            let _ = state.worker.spawn();
            let _ = state.worker.send_init();
        }
        drop(state);

        // Start reading worker events
        self.start_worker_reader();
    }

    async fn shutdown(&self) -> Result<()> {
        {
            let mut state = self.worker_state.lock().await;
            let _ = state.worker.send_shutdown();
        }
        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
        {
            let mut state = self.worker_state.lock().await;
            state.worker.kill();
        }
        Ok(())
    }

    async fn did_save(&self, params: DidSaveTextDocumentParams) {
        let uri = params.text_document.uri;
        if let Ok(path) = uri.to_file_path() {
            let mut state = self.worker_state.lock().await;
            state.pending_files.insert(path);
            // TODO: debounce — flush pending_files after 200ms
            // For now, flush immediately
            let files: Vec<PathBuf> = state.pending_files.drain().collect();
            if let Ok(id) = state.worker.send_analyze(files) {
                state.latest_request_id = id;
            }
        }
    }

    async fn hover(&self, params: HoverParams) -> Result<Option<Hover>> {
        let uri = params.text_document_position_params.text_document.uri;
        let line = params.text_document_position_params.position.line + 1; // 1-indexed
        let diag_map = self.diagnostics.read().await;
        let findings = diag_map.find_at(&uri, line);

        if let Some(finding) = findings.first() {
            if let Some(md) = render_hover(finding) {
                return Ok(Some(Hover {
                    contents: HoverContents::Markup(MarkupContent {
                        kind: MarkupKind::Markdown,
                        value: md,
                    }),
                    range: None,
                }));
            }
        }
        Ok(None)
    }

    async fn code_action(&self, params: CodeActionParams) -> Result<Option<CodeActionResponse>> {
        let uri = params.text_document.uri;
        let line = params.range.start.line + 1; // 1-indexed
        let diag_map = self.diagnostics.read().await;
        let findings = diag_map.find_at(&uri, line);

        let mut actions: Vec<CodeActionOrCommand> = Vec::new();
        for finding in findings {
            for action in actions_for_finding(finding, &uri) {
                actions.push(CodeActionOrCommand::CodeAction(action));
            }
        }

        if actions.is_empty() {
            Ok(None)
        } else {
            Ok(Some(actions))
        }
    }
}
```

This is a starting skeleton. The implementer should:
- Complete `start_worker_reader` with the spawn_blocking loop
- Add debounce logic (200ms timer on pending_files)
- Add stale response filtering
- Wire up `send_score_update` calls

- [ ] **Step 2: Update `lsp/mod.rs` to be the entry point**

```rust
pub mod actions;
pub mod diagnostics;
pub mod hover;
pub mod server;
pub mod worker_client;

use anyhow::Result;
use tower_lsp::{LspService, Server};

use self::server::Backend;
use self::worker_client::WorkerClient;
use crate::cli::worker::protocol::WorkerConfig;

/// Entry point for `repotoire lsp`.
pub async fn run(path: std::path::PathBuf, workers: usize, all_detectors: bool) -> Result<()> {
    let config = WorkerConfig {
        all_detectors,
        workers,
    };
    let worker = WorkerClient::new(path, config);

    let stdin = tokio::io::stdin();
    let stdout = tokio::io::stdout();

    let (service, socket) = LspService::new(|client| Backend::new(client, worker));
    Server::new(stdin, stdout, socket).serve(service).await;
    Ok(())
}
```

- [ ] **Step 3: Update CLI dispatch for Lsp command**

In `cli/mod.rs`, replace the placeholder:
```rust
Some(Commands::Lsp) => {
    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(crate::cli::lsp::run(
        cli.path.clone(),
        cli.workers,
        false, // all_detectors default
    ))
}
```

- [ ] **Step 4: Verify it compiles**

Run: `cargo check --no-default-features 2>&1 | tail -10`

- [ ] **Step 5: Commit**

```bash
git add repotoire-cli/src/cli/lsp/ repotoire-cli/src/cli/mod.rs
git commit -m "feat(lsp): add tower-lsp server with diagnostics, hover, and code actions"
```

---

### Task 11: Debounce and stale response handling

**Files:**
- Modify: `repotoire-cli/src/cli/lsp/server.rs`

- [ ] **Step 1: Add 200ms debounce to `did_save`**

Replace the immediate flush in `did_save` with a debounce mechanism:
- On `did_save`, add file to `pending_files` and spawn a debounce task
- The debounce task sleeps 200ms, then checks if `pending_files` is non-empty
- If yes, flush all pending files as one `analyze` command
- If another `did_save` arrives during the 200ms, the previous debounce task is cancelled (use a `tokio::sync::Notify` or track a debounce generation counter)

- [ ] **Step 2: Add stale response filtering to worker event reader**

In `start_worker_reader`, when processing events:
- If `event.id().is_some() && event.id().unwrap() < state.latest_request_id`, discard the event
- If `event.id().is_none()`, always process (unsolicited filesystem watcher event)

- [ ] **Step 3: Verify it compiles**

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/lsp/server.rs
git commit -m "feat(lsp): add 200ms save debounce and stale response filtering"
```

---

### Task 12: Progress reporting and crash recovery

**Files:**
- Modify: `repotoire-cli/src/cli/lsp/server.rs`

- [ ] **Step 1: Forward progress events as `$/progress`**

When the worker emits `Event::Progress`, create or update an LSP progress token:
- Use `client.send_request::<request::WorkDoneProgressCreate>` to create the token on first progress event
- Send `WorkDoneProgress::Report` with message = stage name and percentage

- [ ] **Step 2: Implement crash recovery in worker event reader**

When `read_event()` returns `None` (worker exited):
- Log the error
- Send `window/showMessage` warning
- If `worker.should_restart()` returns true, sleep 2s, call `worker.spawn()` + `worker.send_init()`, resume reading
- If false (exceeded 3 crashes in 60s), send error notification, clear all diagnostics, stop reading

- [ ] **Step 3: Verify it compiles**

- [ ] **Step 4: Commit**

```bash
git add repotoire-cli/src/cli/lsp/server.rs
git commit -m "feat(lsp): add progress reporting and worker crash recovery"
```

---

### Task 13: Documentation (editor config snippets)

**Files:**
- Create: `docs/guides/lsp-setup.md`

- [ ] **Step 1: Write editor config snippets**

````markdown
# LSP Setup Guide

## VS Code

Add to `.vscode/settings.json`:

```json
{
  "repotoire.server.path": "repotoire",
  "repotoire.server.args": ["lsp"]
}
```

Or with a generic LSP extension (e.g., [vscode-languageclient](https://github.com/AnyLanguage/vscode-languageclient)):

```json
{
  "languageserver": {
    "repotoire": {
      "command": "repotoire",
      "args": ["lsp"],
      "filetypes": ["python", "typescript", "javascript", "rust", "go", "java", "c", "cpp", "csharp"]
    }
  }
}
```

## Neovim (nvim-lspconfig)

```lua
local lspconfig = require('lspconfig')
local configs = require('lspconfig.configs')

configs.repotoire = {
  default_config = {
    cmd = { 'repotoire', 'lsp' },
    filetypes = { 'python', 'typescript', 'javascript', 'rust', 'go', 'java', 'c', 'cpp', 'cs' },
    root_dir = lspconfig.util.root_pattern('.git', 'repotoire.toml'),
  },
}

lspconfig.repotoire.setup({})
```

## Helix

Add to `~/.config/helix/languages.toml`:

```toml
[language-server.repotoire]
command = "repotoire"
args = ["lsp"]

[[language]]
name = "python"
language-servers = ["pylsp", "repotoire"]

[[language]]
name = "rust"
language-servers = ["rust-analyzer", "repotoire"]
```

## Verifying

After configuring, open a file and save it. You should see:
- Diagnostic underlines on code issues
- Hover popups with finding details
- Code actions for suppression
````

- [ ] **Step 2: Commit**

```bash
git add docs/guides/lsp-setup.md
git commit -m "docs: add LSP setup guide for VS Code, Neovim, and Helix"
```

---

### Task 14: Final verification

- [ ] **Step 1: Run the full test suite**

Run: `cargo test --no-default-features 2>&1 | tail -10`
Expected: All tests pass (existing + new protocol/diagnostics/hover/actions tests).

- [ ] **Step 2: Test the worker manually**

```bash
echo '{"cmd":"init","id":1,"path":".","config":{}}' | cargo run --no-default-features -- __worker 2>/dev/null
```
Expected: JSON `ready` event with findings and score.

- [ ] **Step 3: Test the LSP starts**

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{}}}' | cargo run --no-default-features -- lsp 2>/dev/null | head -1
```
Expected: JSON-RPC response with capabilities.

- [ ] **Step 4: Commit any final fixes**

```bash
git add -A
git commit -m "chore(lsp): final verification and cleanup"
```
