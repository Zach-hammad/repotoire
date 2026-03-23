use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};

use anyhow::Result;

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

    /// Send the init command. Uses the provided path (workspace root from LSP),
    /// falling back to the CLI path if not provided.
    pub fn send_init(&mut self, workspace_root: Option<&PathBuf>) -> Result<u64> {
        let id = self.next_id();
        let path = workspace_root.cloned().unwrap_or_else(|| self.repo_path.clone());
        let cmd = protocol::Command::Init {
            id,
            path,
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
