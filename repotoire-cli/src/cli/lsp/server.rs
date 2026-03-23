use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
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
    fn start_worker_reader(&self) {
        let client = self.client.clone();
        let diagnostics = self.diagnostics.clone();
        let worker_state = self.worker_state.clone();

        tokio::spawn(async move {
            loop {
                // Read one event from the worker in a blocking thread
                let ws = worker_state.clone();
                let event = tokio::task::spawn_blocking(move || {
                    // We need to lock the mutex synchronously inside spawn_blocking.
                    // Use try_lock in a loop with a small sleep to avoid holding
                    // the lock while blocking on I/O — but read_event itself needs
                    // the lock for the BufReader. Simplification: block on the lock.
                    let mut state = ws.blocking_lock();
                    state.worker.read_event()
                })
                .await;

                let event = match event {
                    Ok(Some(ev)) => ev,
                    Ok(None) => break, // Worker exited
                    Err(_) => break,   // spawn_blocking panicked
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
                    Event::Unchanged { .. } => {
                        // No changes — nothing to publish
                    }
                    Event::Progress {
                        stage,
                        done,
                        total,
                        ..
                    } => {
                        // Forward progress as a log message
                        client
                            .log_message(
                                MessageType::LOG,
                                format!("repotoire: {} ({}/{})", stage, done, total),
                            )
                            .await;
                    }
                    Event::Error { message, .. } => {
                        client.show_message(MessageType::ERROR, &message).await;
                    }
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
        drop(state);

        // Start reading worker events
        self.start_worker_reader();
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
