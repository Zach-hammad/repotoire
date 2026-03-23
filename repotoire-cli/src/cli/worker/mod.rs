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
