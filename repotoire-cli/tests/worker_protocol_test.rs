//! Integration tests for the __worker process.
//! Spawns `repotoire __worker` and communicates via JSONL stdin/stdout.

use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

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
