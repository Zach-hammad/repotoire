use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use tokio::sync::Mutex;
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
    /// Separate from diagnostics to avoid contention.
    worker_state: Arc<Mutex<WorkerState>>,
    /// Debounce generation counter. Incremented on every save.
    /// The debounce task checks if the generation is still current after 200ms.
    debounce_generation: Arc<AtomicU64>,
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
            debounce_generation: Arc::new(AtomicU64::new(0)),
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

            loop {
                // Read one event from the worker in a blocking thread.
                let reader_clone = reader.clone();
                let event = tokio::task::spawn_blocking(move || {
                    let mut r = reader_clone.lock().expect("reader lock not poisoned");
                    WorkerClient::read_event_from(&mut r)
                })
                .await;

                let event = match event {
                    Ok(ev) => ev,
                    Err(_) => None, // spawn_blocking panicked
                };

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

                        // Check restart limit inside the blocking lock
                        let ws = worker_state.clone();
                        let workspace_root = {
                            let state = worker_state.lock().await;
                            state.workspace_root.clone()
                        };
                        let should_restart = tokio::task::spawn_blocking(move || {
                            let mut state = ws.blocking_lock();
                            state.worker.should_restart()
                        })
                        .await
                        .unwrap_or(false);

                        if should_restart {
                            // Sleep 2 seconds before respawning
                            tokio::time::sleep(std::time::Duration::from_secs(2)).await;

                            // Respawn worker and re-send init, take the new reader
                            let ws = worker_state.clone();
                            let spawn_result = tokio::task::spawn_blocking(move || {
                                let mut state = ws.blocking_lock();
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
                                    *reader.lock().expect("reader lock not poisoned") = new_reader;
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
                    let state = worker_state.lock().await;
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
        // Spawn worker and send init with the workspace root from initialize()
        let mut state = self.worker_state.lock().await;
        let root = state.workspace_root.clone();
        if let Err(e) = state.worker.spawn() {
            drop(state);
            self.client
                .show_message(
                    MessageType::ERROR,
                    format!("Failed to spawn repotoire worker: {}", e),
                )
                .await;
            return;
        }
        if let Err(e) = state.worker.send_init(root.as_ref()) {
            drop(state);
            self.client
                .show_message(
                    MessageType::ERROR,
                    format!("Failed to initialize repotoire worker: {}", e),
                )
                .await;
            return;
        }
        // Take the reader before dropping the lock — owned by the reader task
        let reader = state.worker.take_reader();
        drop(state);

        // Start reading worker events with the owned reader
        self.start_worker_reader(reader);
    }

    async fn shutdown(&self) -> Result<()> {
        // Send shutdown — drop lock before sleeping so other handlers can proceed
        {
            let mut state = self.worker_state.lock().await;
            let _ = state.worker.send_shutdown();
        }
        // Give the worker 5 seconds to exit gracefully
        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
        // Force kill if still running
        {
            let mut state = self.worker_state.lock().await;
            state.worker.kill();
        }
        Ok(())
    }

    async fn did_save(&self, params: DidSaveTextDocumentParams) {
        let uri = params.text_document.uri;
        if let Ok(path) = uri.to_file_path() {
            // Add file to pending set
            {
                let mut state = self.worker_state.lock().await;
                state.pending_files.insert(path);
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

                // Flush pending files as one analyze command via spawn_blocking
                let ws = worker_state.clone();
                let result = tokio::task::spawn_blocking(move || {
                    let mut state = ws.blocking_lock();
                    let files: Vec<PathBuf> = state.pending_files.drain().collect();
                    if files.is_empty() {
                        return None;
                    }
                    state.worker.send_analyze(files).ok()
                })
                .await;

                // Update latest_request_id with the id returned by send_analyze
                if let Ok(Some(id)) = result {
                    let mut state = worker_state.lock().await;
                    state.latest_request_id = id;
                }
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
