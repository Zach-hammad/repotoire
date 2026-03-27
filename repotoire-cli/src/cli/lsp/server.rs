use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use tower_lsp::jsonrpc::Result;
use tower_lsp::lsp_types::notification::Progress;
use tower_lsp::lsp_types::request::WorkDoneProgressCreate;
use tower_lsp::lsp_types::*;
use tower_lsp::{Client, LanguageServer};

use super::actions::actions_for_finding;
use super::diagnostics::DiagnosticMap;
use super::hover::render_hover;
use super::worker_client::WorkerClient;
use crate::cli::worker::protocol::Event;

use std::io::BufReader;

pub struct Backend {
    client: Client,
    /// Read-heavy state (hover, code_action read; ready/delta write).
    /// Use RwLock to avoid blocking reads during diagnostic publishing.
    diagnostics: Arc<tokio::sync::RwLock<DiagnosticMap>>,
    /// Write-heavy state (worker communication, debounce).
    /// Uses std::sync::Mutex because all access is via spawn_blocking.
    /// This avoids the mixed lock().await / blocking_lock() deadlock risk
    /// that tokio::sync::Mutex would introduce.
    worker_state: Arc<std::sync::Mutex<WorkerState>>,
    /// Debounce generation counter. Incremented on every save.
    /// The debounce task checks if the generation is still current after 200ms.
    debounce_generation: Arc<AtomicU64>,
    /// Reader generation counter. Incremented on worker restart.
    /// Events from old readers are discarded after a restart.
    reader_generation: Arc<AtomicU64>,
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
            diagnostics: Arc::new(tokio::sync::RwLock::new(DiagnosticMap::default())),
            worker_state: Arc::new(std::sync::Mutex::new(WorkerState {
                worker,
                latest_request_id: 0,
                pending_files: HashSet::new(),
                workspace_root: None,
            })),
            debounce_generation: Arc::new(AtomicU64::new(0)),
            reader_generation: Arc::new(AtomicU64::new(0)),
        }
    }

    /// Start the background worker event reader.
    /// Spawns a tokio task that reads worker events via spawn_blocking
    /// and publishes diagnostics / sends notifications on the async side.
    ///
    /// The reader is owned by the task — no Mutex needed for reads.
    /// Only `send_*` methods (writes) need the worker_state Mutex.
    fn start_worker_reader(&self, reader: Option<BufReader<std::process::ChildStdout>>) {
        let client = self.client.clone();
        let diagnostics = self.diagnostics.clone();
        let worker_state = self.worker_state.clone();
        let reader_generation = self.reader_generation.clone();

        tokio::spawn(async move {
            // Track whether the LSP progress token has been created yet.
            let mut progress_token_created = false;

            // Own the reader in this task — no Mutex contention with send_* methods.
            // Wrapped in Arc<std::sync::Mutex> so spawn_blocking can borrow it
            // without permanently moving it out of scope.
            let reader = match reader {
                Some(r) => Arc::new(std::sync::Mutex::new(r)),
                None => {
                    tracing::error!("No reader available for worker");
                    return;
                }
            };

            // Track current reader generation to detect stale reads after restart
            let mut current_gen = reader_generation.load(Ordering::SeqCst);

            loop {
                // Read one event from the worker in a blocking thread.
                let reader_clone = reader.clone();
                let read_gen = current_gen;
                let event = tokio::task::spawn_blocking(move || {
                    let mut r = reader_clone.lock().unwrap_or_else(|e| e.into_inner());
                    (WorkerClient::read_event_from(&mut r), read_gen)
                })
                .await;

                let (event, event_gen) = match event {
                    Ok((ev, gen)) => (ev, gen),
                    Err(_) => (None, current_gen), // spawn_blocking panicked
                };

                // Discard events from a stale reader generation (pre-restart)
                if event_gen != reader_generation.load(Ordering::SeqCst) {
                    if event.is_some() {
                        continue; // Stale event from old worker — discard
                    }
                    // EOF from old worker after restart — also discard, update gen
                    current_gen = reader_generation.load(Ordering::SeqCst);
                    continue;
                }

                let event = match event {
                    Some(ev) => ev,
                    None => {
                        // Worker exited or spawn_blocking panicked — attempt crash recovery
                        tracing::error!("repotoire worker exited unexpectedly");
                        client
                            .show_message(
                                MessageType::WARNING,
                                "repotoire worker crashed — attempting restart",
                            )
                            .await;

                        // Check restart limit and restart worker
                        let workspace_root = {
                            let state = worker_state.lock().unwrap_or_else(|e| e.into_inner());
                            state.workspace_root.clone()
                        };

                        let ws = worker_state.clone();
                        let can_restart = tokio::task::spawn_blocking(move || {
                            let mut state = ws.lock().unwrap_or_else(|e| e.into_inner());
                            state.worker.should_restart()
                        })
                        .await
                        .unwrap_or(false);

                        if can_restart {
                            // Sleep 2 seconds before respawning
                            tokio::time::sleep(std::time::Duration::from_secs(2)).await;

                            // Respawn worker and re-send init, take the new reader
                            let ws = worker_state.clone();
                            let spawn_result = tokio::task::spawn_blocking(move || {
                                let mut state = ws.lock().unwrap_or_else(|e| e.into_inner());
                                state.worker.kill();
                                state.worker.spawn().and_then(|_| {
                                    state.worker.send_init(workspace_root.as_ref()).map(|_| ())
                                })?;
                                // Take the new reader for the restarted worker
                                Ok::<_, anyhow::Error>(state.worker.take_reader())
                            })
                            .await;

                            match spawn_result {
                                Ok(Ok(Some(new_reader))) => {
                                    tracing::info!("repotoire worker restarted successfully");
                                    // Bump reader generation so in-flight reads from old pipe are discarded
                                    current_gen = reader_generation.fetch_add(1, Ordering::SeqCst) + 1;
                                    *reader.lock().unwrap_or_else(|e| e.into_inner()) = new_reader;
                                    // Reset progress token so it gets re-created on next progress event
                                    progress_token_created = false;
                                    continue; // Resume the reader loop
                                }
                                _ => {
                                    tracing::error!("repotoire worker failed to restart");
                                }
                            }
                        }

                        // Exceeded retry limit or failed to restart — give up
                        client
                            .show_message(
                                MessageType::ERROR,
                                "repotoire worker failed permanently — diagnostics cleared",
                            )
                            .await;

                        // Clear all diagnostics
                        let all_uris: Vec<Url> = {
                            let diag_map = diagnostics.read().await;
                            diag_map.all_uris()
                        };
                        for uri in all_uris {
                            client.publish_diagnostics(uri, vec![], None).await;
                        }
                        {
                            diagnostics.write().await.clear();
                        }

                        break;
                    }
                };

                // Stale response filtering: discard responses to outdated requests.
                // Events with id: None are unsolicited (filesystem watcher) — never stale.
                if let Some(event_id) = event.id() {
                    let state = worker_state.lock().unwrap_or_else(|e| e.into_inner());
                    if event_id < state.latest_request_id {
                        // Stale response — a newer analyze request has been issued since
                        // this event was generated. Discard to avoid overwriting fresher results.
                        continue;
                    }
                }

                let is_terminal = !matches!(event, Event::Progress { .. });

                match event {
                    Event::Ready {
                        findings,
                        score,
                        grade,
                        ..
                    } => {
                        // Write-lock diagnostics, set all findings
                        let removed_uris = {
                            let mut diag_map = diagnostics.write().await;
                            diag_map.set_all(&findings)
                        };

                        // Publish diagnostics (lock is dropped)
                        publish_all_diagnostics(&client, &diagnostics, removed_uris).await;

                        // Send score update notification
                        send_score_update(&client, score, &grade, None, findings.len()).await;
                    }
                    Event::Delta {
                        new_findings,
                        fixed_findings,
                        score,
                        grade,
                        score_delta,
                        total_findings,
                        ..
                    } => {
                        // Write-lock diagnostics, apply delta
                        let changed_uris = {
                            let mut diag_map = diagnostics.write().await;
                            diag_map.apply_delta(&new_findings, &fixed_findings)
                        };

                        // Publish only changed URIs
                        let to_publish: Vec<(Url, Vec<Diagnostic>)> = {
                            let diag_map = diagnostics.read().await;
                            changed_uris
                                .iter()
                                .map(|uri| {
                                    let diags = diag_map.get_diagnostics(uri);
                                    (uri.clone(), diags)
                                })
                                .collect()
                        };
                        for (uri, diags) in to_publish {
                            client.publish_diagnostics(uri, diags, None).await;
                        }

                        // Send score update notification
                        send_score_update(
                            &client,
                            score,
                            &grade,
                            score_delta,
                            total_findings,
                        )
                        .await;
                    }
                    Event::Unchanged { score, grade, total_findings, .. } => {
                        // No diagnostic changes, but still send score update
                        send_score_update(&client, score, &grade, None, total_findings).await;
                    }
                    Event::Progress {
                        stage,
                        done,
                        total,
                        ..
                    } => {
                        let token = NumberOrString::String("repotoire-analysis".to_string());

                        // Create the progress token on the first progress event
                        if !progress_token_created {
                            let _ = client
                                .send_request::<WorkDoneProgressCreate>(
                                    WorkDoneProgressCreateParams {
                                        token: token.clone(),
                                    },
                                )
                                .await;
                            // Send Begin to open the progress UI
                            client
                                .send_notification::<Progress>(ProgressParams {
                                    token: token.clone(),
                                    value: ProgressParamsValue::WorkDone(
                                        WorkDoneProgress::Begin(WorkDoneProgressBegin {
                                            title: "Repotoire analysis".to_string(),
                                            cancellable: Some(false),
                                            message: None,
                                            percentage: Some(0),
                                        }),
                                    ),
                                })
                                .await;
                            progress_token_created = true;
                        }

                        // Compute percentage; guard against division by zero
                        let percentage = if total > 0 {
                            Some(((done as f64 / total as f64) * 100.0) as u32)
                        } else {
                            None
                        };

                        client
                            .send_notification::<Progress>(ProgressParams {
                                token,
                                value: ProgressParamsValue::WorkDone(WorkDoneProgress::Report(
                                    WorkDoneProgressReport {
                                        cancellable: Some(false),
                                        message: Some(format!("{} ({}/{})", stage, done, total)),
                                        percentage,
                                    },
                                )),
                            })
                            .await;
                    }
                    Event::Error { message, .. } => {
                        client.show_message(MessageType::ERROR, &message).await;
                    }
                }

                // Send WorkDoneProgress::End after terminal events (Ready, Delta, Unchanged, Error)
                // Progress events are not terminal — they are intermediate updates.
                if progress_token_created && is_terminal {
                    let token = NumberOrString::String("repotoire-analysis".to_string());
                    client
                        .send_notification::<Progress>(ProgressParams {
                            token,
                            value: ProgressParamsValue::WorkDone(
                                WorkDoneProgress::End(WorkDoneProgressEnd {
                                    message: Some("Analysis complete".to_string()),
                                }),
                            ),
                        })
                        .await;
                    progress_token_created = false;
                }
            }
        });
    }
}

/// Publish all diagnostics. `removed_uris` are files that no longer have
/// findings — we must publish empty diagnostics to clear stale underlines.
async fn publish_all_diagnostics(
    client: &Client,
    diagnostics: &Arc<tokio::sync::RwLock<DiagnosticMap>>,
    removed_uris: Vec<Url>,
) {
    // Collect diagnostics under read lock, then publish outside the lock
    let to_publish: Vec<(Url, Vec<Diagnostic>)> = {
        let diag_map = diagnostics.read().await;
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
        client.publish_diagnostics(uri, diags, None).await;
    }
    // Clear stale diagnostics for files that no longer have findings
    for uri in removed_uris {
        client.publish_diagnostics(uri, vec![], None).await;
    }
}

async fn send_score_update(
    client: &Client,
    score: f64,
    grade: &str,
    delta: Option<f64>,
    findings: usize,
) {
    let params = serde_json::json!({
        "score": score,
        "grade": grade,
        "delta": delta,
        "findings": findings,
    });
    client
        .send_notification::<ScoreUpdateNotification>(params)
        .await;
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
                self.worker_state.lock().unwrap_or_else(|e| e.into_inner()).workspace_root = Some(path);
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
        // Spawn worker and send init with the workspace root from initialize().
        // Lock is acquired and released within the block — never held across await.
        let init_result: std::result::Result<Option<BufReader<std::process::ChildStdout>>, String> = {
            let mut state = self.worker_state.lock().unwrap_or_else(|e| e.into_inner());
            let root = state.workspace_root.clone();
            if let Err(e) = state.worker.spawn() {
                Err(format!("Failed to spawn repotoire worker: {}", e))
            } else if let Err(e) = state.worker.send_init(root.as_ref()) {
                Err(format!("Failed to initialize repotoire worker: {}", e))
            } else {
                Ok(state.worker.take_reader())
            }
        };

        match init_result {
            Ok(reader) => self.start_worker_reader(reader),
            Err(msg) => {
                self.client.show_message(MessageType::ERROR, msg).await;
            }
        }
    }

    async fn shutdown(&self) -> Result<()> {
        // Send shutdown
        {
            let mut state = self.worker_state.lock().unwrap_or_else(|e| e.into_inner());
            let _ = state.worker.send_shutdown();
        }
        // Give the worker 5 seconds to exit gracefully
        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
        // Force kill if still running
        {
            let mut state = self.worker_state.lock().unwrap_or_else(|e| e.into_inner());
            state.worker.kill();
        }
        Ok(())
    }

    async fn did_save(&self, params: DidSaveTextDocumentParams) {
        let uri = params.text_document.uri;
        if let Ok(path) = uri.to_file_path() {
            // Add file to pending set
            {
                self.worker_state.lock().unwrap_or_else(|e| e.into_inner()).pending_files.insert(path);
            }

            // Increment debounce generation to cancel any in-flight debounce task
            let gen = self.debounce_generation.fetch_add(1, Ordering::SeqCst) + 1;

            // Clone Arcs for the debounce task
            let debounce_gen = self.debounce_generation.clone();
            let worker_state = self.worker_state.clone();

            // Spawn a debounce task: wait 200ms, then flush if no newer save arrived
            tokio::spawn(async move {
                tokio::time::sleep(std::time::Duration::from_millis(200)).await;

                // Check if our generation is still current (no newer save arrived)
                if debounce_gen.load(Ordering::SeqCst) != gen {
                    return; // A newer save arrived — let that task flush instead
                }

                // Flush pending files as one analyze command
                let ws = worker_state.clone();
                let result = tokio::task::spawn_blocking(move || {
                    let mut state = ws.lock().unwrap_or_else(|e| e.into_inner());
                    let files: Vec<PathBuf> = state.pending_files.drain().collect();
                    if files.is_empty() {
                        return None;
                    }
                    let id = state.worker.send_analyze(files).ok()?;
                    state.latest_request_id = id;
                    Some(())
                })
                .await;
                let _ = result;
            });
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
