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
