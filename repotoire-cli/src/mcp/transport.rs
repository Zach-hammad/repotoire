//! Streamable HTTP transport for MCP server
//!
//! Provides HTTP-based MCP access via axum + rmcp StreamableHttpService.

use anyhow::Result;

use super::rmcp_server::RepotoireServer;
use super::state::HandlerState;

pub async fn serve_http(
    repo_path: std::path::PathBuf,
    force_local: bool,
    port: u16,
) -> Result<()> {
    use rmcp::transport::streamable_http_server::{
        session::local::LocalSessionManager,
        tower::{StreamableHttpServerConfig, StreamableHttpService},
    };

    let ct = tokio_util::sync::CancellationToken::new();

    let rp = repo_path.clone();
    let fl = force_local;
    let service = StreamableHttpService::new(
        move || {
            let state = HandlerState::new(rp.clone(), fl);
            Ok(RepotoireServer::new(state))
        },
        LocalSessionManager::default().into(),
        StreamableHttpServerConfig {
            cancellation_token: ct.child_token(),
            ..Default::default()
        },
    );

    let router = axum::Router::new().nest_service("/mcp", service);
    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port)).await?;

    eprintln!("   Listening on http://0.0.0.0:{}/mcp", port);

    axum::serve(listener, router)
        .with_graceful_shutdown(async move {
            tokio::signal::ctrl_c().await.ok();
            ct.cancel();
        })
        .await?;

    Ok(())
}
